"""
Datenschicht: eigene Uebersetzungen (Overrides, FTS-Pflege) und Schema
(ensure_schema idempotent, Migration, local_db_version).
"""

from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

import yugioh_db as ydb
from yugioh_db.schema import _migrate

from tests._support import CardIdsTestCase


def _fts_hits(db, term):
    conn = ydb._connect(db)
    try:
        return {r[0] for r in conn.execute(
            "SELECT rowid FROM cards_fts WHERE cards_fts MATCH ?", (term,))}
    finally:
        conn.close()


class LegacyTranslationTests(CardIdsTestCase):
    """Aus test_yugioh_db.DbTests uebernommen."""

    def _card(self, cid):
        return self.query(
            "SELECT name, name_de, desc_de FROM cards WHERE id = ?", (cid,))[0]

    def test_translation_writes_through_and_is_searchable(self):
        ydb.set_card_translation(self.db, self.main_id, name_de="Mein Testname")
        name_de = self._card(self.main_id)["name_de"]
        self.assertEqual(name_de, "Mein Testname")
        self.assertIn(self.main_id, _fts_hits(self.db, "Testname"))  # FTS mitgepflegt

    def test_removing_translation_override_reverts_card(self):
        cid = self.scalar("SELECT id FROM cards WHERE name_de IS NULL LIMIT 1")
        ydb.set_card_translation(self.db, cid, name_de="Falsch")
        self.assertEqual(self._card(cid)["name_de"], "Falsch")
        ydb.set_card_translation(self.db, cid, name_de="")
        self.assertIsNone(self._card(cid)["name_de"])
        self.assertIsNone(ydb.get_card_translation(self.db, cid))
        # Suche findet den falschen Namen nicht mehr (FTS gepflegt).
        self.assertNotIn(cid, _fts_hits(self.db, "Falsch"))

    def test_name_only_override_keeps_api_text(self):
        cid = self.translated_card()
        before = self._card(cid)
        ydb.set_card_translation(self.db, cid, name_de="Eigener Name")
        after = self._card(cid)
        self.assertEqual(after["name_de"], "Eigener Name")
        self.assertEqual(after["desc_de"], before["desc_de"])
        self.assertIsNone(ydb.get_card_translation(self.db, cid)["desc_de"])

    def test_migrate_does_not_swallow_lock_errors(self):
        # Spalte fehlt + Schreibsperre einer zweiten Verbindung: frueher
        # kehrte ensure_schema still zurueck, jetzt gibt es einen Fehler.
        conn = sqlite3.connect(self.db)
        conn.execute("ALTER TABLE decks DROP COLUMN format_date")
        conn.commit()
        conn.execute("BEGIN EXCLUSIVE")
        try:
            with self.assertRaises(sqlite3.OperationalError):
                with ydb._conn(self.db) as c2:
                    c2.execute("PRAGMA busy_timeout = 50")
                    _migrate(c2)
        finally:
            conn.rollback()
            conn.close()
        ydb.ensure_schema(self.db)  # ohne Sperre: Spalte wird nachgeruestet
        cols = {r[1] for r in self.query("PRAGMA table_info(decks)")}
        self.assertIn("format_date", cols)


class TranslationTests(CardIdsTestCase):
    def _card(self, cid):
        return self.query(
            "SELECT name, name_de, desc_de FROM cards WHERE id = ?", (cid,))[0]

    def test_get_translation_only_returns_own_overrides(self):
        cid = self.translated_card()
        self.assertIsNone(ydb.get_card_translation(self.db, cid))  # nur API-Wert
        ydb.set_card_translation(self.db, cid, name_de="X", desc_de="Y")
        own = ydb.get_card_translation(self.db, cid)
        self.assertEqual((own["name_de"], own["desc_de"]), ("X", "Y"))

    def test_existing_overrides_of_dev_db_are_applied(self):
        rows = self.query(
            "SELECT t.name_de AS t_name, c.name_de AS c_name FROM card_translations t "
            "JOIN cards c ON c.id = t.card_id WHERE t.name_de IS NOT NULL")
        self.assertTrue(rows, "Dev-DB braucht eigene Uebersetzungen")
        self.assertTrue(all(r["t_name"] == r["c_name"] for r in rows))

    def test_unknown_card_raises(self):
        with self.assertRaises(ValueError):
            ydb.set_card_translation(self.db, 999_999_999, name_de="X")

    def test_values_are_stripped(self):
        cid = self.untranslated_card()
        ydb.set_card_translation(self.db, cid, name_de="  Name  ", desc_de="\nText\n")
        card = self._card(cid)
        self.assertEqual((card["name_de"], card["desc_de"]), ("Name", "Text"))

    def test_desc_only_override_keeps_api_name(self):
        cid = self.translated_card()
        before = self._card(cid)
        ydb.set_card_translation(self.db, cid, desc_de="Eigener Text")
        after = self._card(cid)
        self.assertEqual(after["name_de"], before["name_de"])
        self.assertEqual(after["desc_de"], "Eigener Text")

    def test_override_can_be_replaced(self):
        cid = self.untranslated_card()
        ydb.set_card_translation(self.db, cid, name_de="Erster")
        ydb.set_card_translation(self.db, cid, name_de="Zweiter")
        self.assertEqual(self._card(cid)["name_de"], "Zweiter")
        self.assertIn(cid, _fts_hits(self.db, "Zweiter"))
        self.assertNotIn(cid, _fts_hits(self.db, "Erster"))
        self.assertEqual(self.scalar(
            "SELECT COUNT(*) FROM card_translations WHERE card_id = ?", (cid,)), 1)

    def test_english_name_stays_searchable(self):
        cid = self.untranslated_card()
        english = self._card(cid)["name"].split()[0]
        ydb.set_card_translation(self.db, cid, name_de="Deutscher Name")
        self.assertIn(cid, _fts_hits(self.db, f'"{english}"'))


class SchemaTests(CardIdsTestCase):
    def _schema(self, db):
        conn = sqlite3.connect(db)
        try:
            return sorted(conn.execute(
                "SELECT type, name, sql FROM sqlite_master").fetchall(),
                key=lambda r: (r[0], r[1]))
        finally:
            conn.close()

    def test_ensure_schema_is_idempotent(self):
        before, entries = self._schema(self.db), self.count("collection")
        ydb.ensure_schema(self.db)
        ydb.ensure_schema(self.db)
        self.assertEqual(self._schema(self.db), before)
        self.assertEqual(self.count("collection"), entries)

    def test_fresh_database_gets_all_tables_and_index(self):
        fresh = os.path.join(self._dir, "fresh.sqlite3")
        ydb.ensure_schema(fresh)
        names = {r[1] for r in self._schema(fresh)}
        for table in ("meta", "cards", "card_sets", "cards_fts", "collection",
                      "card_translations", "decks", "deck_cards", "combos",
                      "combo_cards", "combo_steps", "idx_cards_disp_name",
                      "card_rulings"):
            self.assertIn(table, names)
        self.assertIsNone(ydb.local_db_version(fresh))

    def test_migration_adds_columns_to_old_tables(self):
        old = os.path.join(self._dir, "old.sqlite3")
        conn = sqlite3.connect(old)
        conn.executescript(
            # Stand vor den DE-Spalten (SCHEMA indexiert type/attribute/...).
            "CREATE TABLE cards (id INTEGER PRIMARY KEY, name TEXT NOT NULL,"
            " type TEXT, frame_type TEXT, description TEXT, atk INTEGER,"
            " def INTEGER, level INTEGER, race TEXT, attribute TEXT,"
            " archetype TEXT, scale INTEGER, link_value INTEGER);"
            "CREATE TABLE decks (deck_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);"
            "CREATE TABLE combos (combo_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);"
            "CREATE TABLE combo_cards (combo_id INTEGER, card_id INTEGER,"
            " quantity INTEGER, PRIMARY KEY (combo_id, card_id));"
            "INSERT INTO decks (name) VALUES ('Altes Deck');"
        )
        conn.commit()
        conn.close()
        ydb.ensure_schema(old)
        cols = lambda t: {r[1] for r in self.query_other(old, f"PRAGMA table_info({t})")}
        self.assertTrue({"name_de", "desc_de"} <= cols("cards"))
        self.assertTrue({"kind", "source", "format_date"} <= cols("decks"))
        self.assertTrue({"boss_card_id", "deck_id", "parent_combo_id"} <= cols("combos"))
        self.assertIn("role", cols("combo_cards"))
        # Bestandsdaten bleiben, neue Spalten sind NULL (= eigenes Deck).
        self.assertEqual([d["name"] for d in ydb.list_decks(old)], ["Altes Deck"])

    @staticmethod
    def query_other(db, sql):
        conn = sqlite3.connect(db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_app_data_dir_per_platform(self):
        from yugioh_db import schema
        base = os.path.join(self._dir, "appdata")
        cases = (
            ("win32", {"LOCALAPPDATA": base}, base),
            ("linux", {"XDG_DATA_HOME": base}, base),
        )
        for platform, env, root in cases:
            with mock.patch.object(schema.sys, "platform", platform), \
                 mock.patch.dict(os.environ, env):
                got = schema._app_data_dir()
            self.assertEqual(str(got), os.path.join(root, "YugiohSammlung"), platform)
            self.assertTrue(got.is_dir())
        # macOS: ~/Library/Application Support (expanduser auf Temp umgebogen).
        with mock.patch.object(schema.sys, "platform", "darwin"), \
             mock.patch.object(schema.os.path, "expanduser",
                               lambda p: p.replace("~", base)):
            got = schema._app_data_dir()
        self.assertEqual(got, Path(base) / "Library" / "Application Support" / "YugiohSammlung")

    def test_local_db_version(self):
        self.set_meta("db_version", "123.4")
        self.assertEqual(ydb.local_db_version(self.db), "123.4")
        bare = os.path.join(self._dir, "bare.sqlite3")
        sqlite3.connect(bare).close()                 # DB ohne meta-Tabelle
        self.assertIsNone(ydb.local_db_version(bare))

    def test_connections_enforce_foreign_keys_and_casefold(self):
        with ydb._conn(self.db) as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT casefold('ÄÖÜ')").fetchone()[0], "äöü")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO deck_cards VALUES (999999, ?, 'main', 1)",
                             (self.main_id,))

    def test_deck_zone_check_constraint(self):
        deck = ydb.create_deck(self.db, "T")
        with ydb._conn(self.db) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO deck_cards VALUES (?, ?, 'hand', 1)",
                             (deck, self.main_id))


if __name__ == "__main__":
    unittest.main(verbosity=2)
