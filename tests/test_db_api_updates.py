"""
Datenschicht, Netz-Rand: build_database (UPSERT-Update ohne Datenverlust),
API-Helfer, Bild-Cache, App-Update-Check, Seed-DB-Uebernahme,
Migrations-Sicherung und die CLI. Netz ist IMMER gepatcht -- und zwar dort,
wo der Name zur Laufzeit nachgeschlagen wird (updates importiert
_http_get_json per Name, __main__ build_database/needs_update).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest import mock

import yugioh_db as ydb
import yugioh_db.__main__ as cli
from yugioh_db import api, updates

from tests._support import (
    REPO_ROOT, CardIdsTestCase, DevDbTestCase, api_payload_from_db, remove_tree
)


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@contextlib.contextmanager
def fake_api(cards, de, version="999.1"):
    with mock.patch.object(api, "fetch_all_cards", return_value=cards), \
         mock.patch.object(api, "fetch_all_cards_de", return_value=de), \
         mock.patch.object(api, "fetch_db_version", return_value=version):
        yield


class LegacyBuildTests(CardIdsTestCase):
    """Aus test_yugioh_db.DbTests uebernommen."""

    def _counts(self):
        return tuple(self.query(
            "SELECT (SELECT COUNT(*) FROM cards WHERE name_de IS NOT NULL),"
            "       (SELECT COUNT(*) FROM card_sets)")[0])

    def test_build_database_aborts_on_empty_api_response(self):
        before = self._counts()
        one_card = [{"id": self.main_id, "name": "X"}]
        cases = [
            ([], {self.main_id: {"name": "Y"}}),         # EN leer
            (one_card, {}),                               # DE leer
            (one_card, {self.main_id: {"name": "Y"}}),    # EN unplausibel klein
        ]
        for en, de in cases:
            with fake_api(en, de, "x"):
                with self.assertRaises(RuntimeError):
                    ydb.build_database(self.db)
            self.assertEqual(self._counts(), before)

    def test_build_database_unescapes_html_entities(self):
        ydb.build_database(
            self.db, cards=[{"id": self.main_id, "name": "A &amp; B"}]
        )
        self.assertEqual(
            self.scalar("SELECT name FROM cards WHERE id = ?", (self.main_id,)),
            "A & B")


class FullUpdateTests(DevDbTestCase):
    """Kompletter Daten-Update-Lauf mit einer aus der DB rekonstruierten
    API-Antwort: die Invarianten 'kein DELETE auf cards' und 'eigene
    Uebersetzungen ueberleben' muessen halten."""

    def _snapshot(self):
        return {
            "collection": self.query("SELECT * FROM collection ORDER BY entry_id"),
            "decks": self.query("SELECT * FROM decks ORDER BY deck_id"),
            "deck_cards": self.query(
                "SELECT * FROM deck_cards ORDER BY deck_id, card_id, zone"),
            "translations": self.query(
                "SELECT * FROM card_translations ORDER BY card_id"),
        }

    def test_update_keeps_user_data_and_reapplies_overrides(self):
        overrides = {r["card_id"] for r in self.query("SELECT card_id FROM card_translations")}
        piece = self.main_ids(1)[0]
        combo = self.seed_combo("Linie", {piece: "starter"}, steps=["NS A"])

        # Die 'API' liefert fuer Karten mit eigenem Override andere DE-Werte.
        def api_de(cid, name, desc):
            if cid in overrides:
                return f"API-{cid}", "API-Text"
            return name, desc

        cards, de = api_payload_from_db(self.db, api_de)
        before = self._snapshot()
        n_cards, n_sets = self.count("cards"), self.count("card_sets")
        with fake_api(cards, de):
            n = ydb.build_database(self.db)
        self.assertEqual(n, n_cards)
        after = self._snapshot()
        for key in before:
            self.assertEqual([tuple(r) for r in after[key]],
                             [tuple(r) for r in before[key]], key)
        self.assertEqual([p["card_id"] for p in ydb.combo_cards(self.db, combo)], [piece])
        self.assertEqual(ydb.combo_steps(self.db, combo)[0]["text"], "NS A")

        # Override gewinnt je Feld; ohne eigenen Wert greift der API-Wert.
        for r in self.query(
            "SELECT t.card_id, t.name_de AS t_name, t.desc_de AS t_desc, "
            "c.name_de, c.desc_de FROM card_translations t "
            "JOIN cards c ON c.id = t.card_id"
        ):
            self.assertEqual(r["name_de"], r["t_name"] or f"API-{r['card_id']}")
            self.assertEqual(r["desc_de"], r["t_desc"] or "API-Text")

        self.assertEqual(ydb.local_db_version(self.db), "999.1")
        self.assertEqual(self.count("cards"), n_cards)          # kein Loeschen
        self.assertEqual(self.count("card_sets"), n_sets)
        self.assertEqual(self.count("cards_fts"), n_cards)

    def test_update_rebuilds_fulltext_index(self):
        cid = self.translated_card()
        name_de = self.scalar("SELECT name_de FROM cards WHERE id = ?", (cid,))
        cards, de = api_payload_from_db(self.db)
        with fake_api(cards, de):
            ydb.build_database(self.db)
        conn = ydb._connect(self.db)
        try:
            hits = {r[0] for r in conn.execute(
                "SELECT rowid FROM cards_fts WHERE cards_fts MATCH ?",
                (f'"{name_de}"',))}
        finally:
            conn.close()
        self.assertIn(cid, hits)

    def test_changed_card_data_is_upserted(self):
        cid = self.main_ids(1)[0]
        cards, de = api_payload_from_db(self.db)
        for c in cards:
            if c["id"] == cid:
                c["atk"], c["card_sets"] = 4242, [{"set_name": "Neu", "set_code": "NEU-001"}]
        with fake_api(cards, de):
            ydb.build_database(self.db)
        self.assertEqual(self.scalar("SELECT atk FROM cards WHERE id = ?", (cid,)), 4242)
        self.assertEqual([tuple(r) for r in self.query(
            "SELECT set_name, set_code FROM card_sets WHERE card_id = ?", (cid,))],
            [("Neu", "NEU-001")])

    def test_link_markers_are_stored_and_survive_updates(self):
        link = next(iter(self.REAL_LINK_MARKERS))
        cards, de = api_payload_from_db(self.db)
        for c in cards:
            if c["id"] == link:
                self.assertIsNone(c["linkmarkers"])      # Dev-DB: noch leer
                c["linkmarkers"] = self.REAL_LINK_MARKERS[link].split(",")
        with fake_api(cards, de):
            ydb.build_database(self.db)
        sql = "SELECT link_markers FROM cards WHERE id = ?"
        self.assertEqual(self.scalar(sql, (link,)), self.REAL_LINK_MARKERS[link])
        # Zweiter Lauf aus der aktualisierten DB: Pfeile bleiben (UPSERT).
        cards, de = api_payload_from_db(self.db)
        with fake_api(cards, de):
            ydb.build_database(self.db)
        self.assertEqual(self.scalar(sql, (link,)), self.REAL_LINK_MARKERS[link])
        self.assertIsNone(self.scalar(sql, (self.main_ids(1)[0],)))

    def test_update_into_fresh_database(self):
        cards, _de = api_payload_from_db(self.db)
        cards = cards[:50]
        de = {c["id"]: {"id": c["id"], "name": c["name"], "desc": c["desc"]} for c in cards}
        fresh = os.path.join(self._dir, "neu.sqlite3")
        with fake_api(cards, de):
            self.assertEqual(ydb.build_database(fresh), 50)
        self.assertEqual(ydb.local_db_version(fresh), "999.1")
        ydb.ensure_schema(fresh)
        self.assertEqual(ydb.list_decks(fresh), [])


class ApiHelperTests(unittest.TestCase):
    def test_http_get_json_sets_user_agent_and_decodes(self):
        seen = {}

        def fake_urlopen(req, timeout):
            seen["ua"] = req.get_header("User-agent")
            seen["timeout"] = timeout
            return _FakeResponse(json.dumps({"ok": "ä"}).encode("utf-8"))

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            self.assertEqual(api._http_get_json("https://x", timeout=7), {"ok": "ä"})
        self.assertEqual(seen, {"ua": "yugioh-tool/0.1", "timeout": 7})

    def test_fetch_all_cards_variants(self):
        with mock.patch.object(api, "_http_get_json", return_value={"data": [{"id": 1}]}):
            self.assertEqual(api.fetch_all_cards(), [{"id": 1}])
        with mock.patch.object(api, "_http_get_json", return_value={}):
            self.assertEqual(api.fetch_all_cards(), [])
        with mock.patch.object(api, "_http_get_json",
                               return_value={"data": [{"id": 5, "name": "X"}]}) as m:
            self.assertEqual(api.fetch_all_cards_de(), {5: {"id": 5, "name": "X"}})
            self.assertTrue(m.call_args[0][0].endswith("?language=de"))

    def test_fetch_db_version_variants(self):
        cases = [
            ([{"database_version": 146.5}], "146.5"),
            ([], None),
            ({"database_version": "1"}, None),
        ]
        for payload, expected in cases:
            with mock.patch.object(api, "_http_get_json", return_value=payload):
                self.assertEqual(api.fetch_db_version(), expected)
        with mock.patch.object(api, "_http_get_json", side_effect=OSError("offline")):
            self.assertIsNone(api.fetch_db_version())


class NeedsUpdateTests(DevDbTestCase):
    def test_needs_update(self):
        self.set_meta("db_version", "145.88")
        for remote, expected in (("145.88", False), ("146.0", True), (None, False)):
            with mock.patch.object(api, "fetch_db_version", return_value=remote):
                self.assertIs(ydb.needs_update(self.db), expected, remote)


class CacheImageTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="ygo_test_img_")
        self.addCleanup(remove_tree, self.dir)

    def test_downloads_once_and_writes_atomically(self):
        calls = []

        def fake_urlopen(req, timeout):
            calls.append(req.full_url)
            return _FakeResponse(b"\xff\xd8JPEG")

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            path = api.cache_image(123, "https://img/123.jpg", self.dir)
            again = api.cache_image(123, "https://img/123.jpg", self.dir)
        self.assertEqual(path, again)
        self.assertEqual(path.read_bytes(), b"\xff\xd8JPEG")
        self.assertEqual(calls, ["https://img/123.jpg"])         # kein zweiter Download
        self.assertEqual(os.listdir(self.dir), ["123.jpg"])      # keine .part-Reste

    def test_failed_download_leaves_nothing(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                api.cache_image(7, "https://img/7.jpg", self.dir)
        self.assertEqual(os.listdir(self.dir), [])

    def test_creates_missing_folder(self):
        target = os.path.join(self.dir, "a", "b")
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(b"x")):
            api.cache_image(1, "https://img/1.jpg", target)
        self.assertTrue(os.path.exists(os.path.join(target, "1.jpg")))


class AppUpdateCheckTests(unittest.TestCase):
    def _check(self, payload):
        with mock.patch.object(updates, "_http_get_json", return_value=payload):
            return ydb.check_app_update()

    def test_newer_release(self):
        info = self._check({"tag_name": "v99.0.0", "html_url": "https://rel",
                            "body": "  Neu!  "})
        self.assertEqual(info, {"version": "99.0.0", "url": "https://rel", "notes": "Neu!"})

    def test_same_or_older_release(self):
        self.assertIsNone(self._check({"tag_name": f"v{ydb.APP_VERSION}"}))
        self.assertIsNone(self._check({"tag_name": "v0.0.1"}))
        self.assertIsNone(self._check({}))                  # kaputter Tag nie 'neuer'

    def test_missing_fields_fall_back(self):
        info = self._check({"tag_name": "v99.1"})
        self.assertEqual(info, {"version": "99.1", "url": updates.RELEASES_PAGE_URL,
                                "notes": ""})

    def test_network_errors_propagate(self):
        with mock.patch.object(updates, "_http_get_json", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                ydb.check_app_update()


class SeedDbTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="ygo_test_seed_")
        self.addCleanup(remove_tree, self.dir)
        self.target = os.path.join(self.dir, "user", "yugioh.sqlite3")
        os.makedirs(os.path.dirname(self.target))

    def test_existing_db_is_kept(self):
        with open(self.target, "wb") as fh:
            fh.write(b"vorhanden")
        self.assertTrue(ydb.ensure_user_db(self.target))
        with open(self.target, "rb") as fh:
            self.assertEqual(fh.read(), b"vorhanden")

    def test_no_bundle_no_db(self):
        self.assertIsNone(ydb.bundled_seed_path())           # kein PyInstaller
        self.assertFalse(ydb.ensure_user_db(self.target))
        self.assertFalse(os.path.exists(self.target))

    def test_seed_is_copied_from_bundle(self):
        bundle = os.path.join(self.dir, "bundle")
        os.makedirs(bundle)
        ydb.ensure_schema(os.path.join(bundle, "seed.sqlite3"))
        with mock.patch.object(sys, "_MEIPASS", bundle, create=True):
            self.assertEqual(str(ydb.bundled_seed_path()),
                             os.path.join(bundle, "seed.sqlite3"))
            self.assertTrue(ydb.ensure_user_db(self.target))
        self.assertTrue(os.path.exists(self.target))
        self.assertFalse(os.path.exists(self.target + ".part"))
        self.assertIsNone(ydb.local_db_version(self.target))

    def test_bundle_without_seed(self):
        with mock.patch.object(sys, "_MEIPASS", self.dir, create=True):
            self.assertIsNone(ydb.bundled_seed_path())
            self.assertFalse(ydb.ensure_user_db(self.target))


class MigrationBackupTests(DevDbTestCase):
    def setUp(self):
        super().setUp()
        # Definierter Ausgangsstand: Kopie stammt aus einer aelteren Version
        # (die Dev-DB selbst wird beim App-Start aus dem Repo-Root hochgezogen).
        self.set_meta("app_version", "0.8.0")

    def _meta_version(self, db=None):
        return self.query_on(db or self.db,
                             "SELECT value FROM meta WHERE key = 'app_version'")

    @staticmethod
    def query_on(db, sql):
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(sql).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def test_backup_once_per_version(self):
        backup = ydb.migration_backup(self.db)
        self.assertEqual(backup, self.db + ".bak-v0.8.0")
        self.assertTrue(os.path.exists(backup))
        self.assertEqual(self._meta_version(), ydb.APP_VERSION)
        # Die Sicherung ist der Stand VOR der Versionsmarke.
        self.assertEqual(self.query_on(backup, "SELECT value FROM meta "
                                               "WHERE key = 'app_version'"), "0.8.0")
        self.assertIsNone(ydb.migration_backup(self.db))       # schon gesichert

    def test_only_newest_backup_is_kept(self):
        first = ydb.migration_backup(self.db)
        self.assertIsNotNone(first)
        self.set_meta("app_version", "0.8.5")
        second = ydb.migration_backup(self.db)
        self.assertEqual(second, self.db + ".bak-v0.8.5")
        self.assertFalse(os.path.exists(first))
        self.assertTrue(os.path.exists(second))

    def test_missing_db(self):
        self.assertIsNone(ydb.migration_backup(os.path.join(self._dir, "gibtsnicht")))

    def test_current_version_needs_no_backup(self):
        self.set_meta("app_version", ydb.APP_VERSION)
        self.assertIsNone(ydb.migration_backup(self.db))
        self.assertEqual([f for f in os.listdir(self._dir) if ".bak-v" in f], [])

    def test_fresh_db_without_user_data_is_only_marked(self):
        fresh = os.path.join(self._dir, "seed.sqlite3")
        ydb.ensure_schema(fresh)
        self.assertIsNone(ydb.migration_backup(fresh))
        self.assertEqual(self._meta_version(fresh), ydb.APP_VERSION)
        self.assertEqual([f for f in os.listdir(self._dir) if ".bak-v" in f], [])

    def test_very_old_db_without_meta(self):
        old = os.path.join(self._dir, "alt.sqlite3")
        conn = sqlite3.connect(old)
        conn.execute("CREATE TABLE collection (card_id INTEGER, quantity INTEGER)")
        conn.execute("INSERT INTO collection VALUES (1, 1)")
        conn.commit()
        conn.close()
        backup = ydb.migration_backup(old)
        self.assertEqual(backup, old + ".bak-valt")
        self.assertEqual(self._meta_version(old), ydb.APP_VERSION)


class CliTests(unittest.TestCase):
    def _run(self, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["yugioh_db", *argv]), \
             contextlib.redirect_stdout(out):
            cli.main()
        return out.getvalue()

    def test_build(self):
        with mock.patch.object(cli, "build_database", return_value=5) as build:
            out = self._run("build", "x.sqlite3")
        build.assert_called_once_with("x.sqlite3")
        self.assertIn("Fertig: 5 Karten in x.sqlite3 importiert.", out)

    def test_build_is_default_with_default_db(self):
        with mock.patch.object(cli, "build_database", return_value=1) as build:
            self._run()
        build.assert_called_once_with(ydb.DEFAULT_DB)

    def test_check(self):
        for flag, text in ((True, "Update verfügbar."), (False, "Datenbank ist aktuell.")):
            with mock.patch.object(cli, "needs_update", return_value=flag) as check:
                self.assertIn(text, self._run("check", "y.sqlite3"))
            check.assert_called_once_with("y.sqlite3")

    def test_unknown_command_prints_usage(self):
        self.assertIn("Verwendung: python -m yugioh_db", self._run("hilfe"))

    def test_real_process_usage(self):
        # Echter Prozess: Paket startbar, unbekannter Befehl geht nie ins Netz.
        proc = subprocess.run(
            [sys.executable, "-m", "yugioh_db", "hilfe"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Verwendung", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
