"""
test_yugioh_db.py
=================
Tests fuer die Datenschicht. Reine Standardbibliothek (unittest) -- passend
zum Standalone-Prinzip von yugioh_db.

Strategie (siehe CLAUDE.md): Mit der Dev-DB testen, keine Mock-Karten
erfinden. Tests, die schreiben, laufen gegen eine Temp-Kopie der Dev-DB
(yugioh.sqlite3 neben dieser Datei); die Dev-DB selbst wird nie mutiert.
Fehlt die Dev-DB (frischer Clone ohne Build), werden die DB-Tests
uebersprungen -- die Tests reiner Funktionen laufen trotzdem.

    python -m unittest test_yugioh_db        # oder: python test_yugioh_db.py
"""

from __future__ import annotations

import math
import os
import shutil
import sqlite3
import tempfile
import unittest

import yugioh_db as ydb

DEV_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yugioh.sqlite3")
HAS_DEV_DB = os.path.exists(DEV_DB)


# ---------------------------------------------------------------------------
# Reine Funktionen -- kein DB-Zugriff, laufen immer
# ---------------------------------------------------------------------------

class PureFunctionTests(unittest.TestCase):
    def test_deck_zone_for_extra_frames(self):
        for ft in ("fusion", "synchro", "xyz", "link", "xyz_pendulum"):
            self.assertEqual(ydb.deck_zone_for(ft), "extra")

    def test_deck_zone_for_main(self):
        self.assertEqual(ydb.deck_zone_for("effect"), "main")
        self.assertEqual(ydb.deck_zone_for("normal"), "main")
        self.assertEqual(ydb.deck_zone_for(None), "main")

    def test_deck_zone_for_falls_back_to_type(self):
        # Frame unbekannt, aber der Kartentyp verraet das Extra Deck.
        self.assertEqual(ydb.deck_zone_for("", "Link Monster"), "extra")
        self.assertEqual(ydb.deck_zone_for(None, "Xyz Monster"), "extra")

    def test_card_category(self):
        self.assertEqual(ydb.card_category("Spell Card"), "spell")
        self.assertEqual(ydb.card_category("Trap Card"), "trap")
        self.assertEqual(ydb.card_category("Effect Monster"), "monster")
        self.assertEqual(ydb.card_category("Skill Card"), "other")
        self.assertEqual(ydb.card_category(None), "other")

    def test_hypergeom_edge_cases(self):
        self.assertEqual(ydb.hypergeom_at_least(40, 0, 5), 0.0)   # keine Erfolge
        self.assertEqual(ydb.hypergeom_at_least(40, 3, 0), 0.0)   # keine Zuege
        self.assertEqual(ydb.hypergeom_at_least(40, 3, 5, 0), 1.0)  # min_hits 0

    def test_hypergeom_known_value(self):
        # P(>=1 aus 3 Treffern bei 5 Zuegen aus 40) = 1 - C(37,5)/C(40,5).
        expected = 1 - math.comb(37, 5) / math.comb(40, 5)
        self.assertAlmostEqual(ydb.hypergeom_at_least(40, 3, 5), expected, places=10)

    def test_hypergeom_caps_absurd_input(self):
        # successes/draws > population werden gekappt, nicht geworfen.
        self.assertEqual(ydb.hypergeom_at_least(5, 99, 99), 1.0)

    def test_parse_ydk_sections(self):
        text = "#created by x\n#main\n100\n100\n#extra\n200\n!side\n300\n"
        zones = ydb.parse_ydk(text)
        self.assertEqual(zones["main"], [100, 100])
        self.assertEqual(zones["extra"], [200])
        self.assertEqual(zones["side"], [300])

    def test_lint_combo_steps(self):
        self.assertEqual(ydb.lint_combo_steps(["NS Karte (Hand)"]), [])
        warnings = ydb.lint_combo_steps(["irgendwas ohne keyword"])
        self.assertTrue(warnings)
        self.assertIn("Schritt 1", warnings[0])


# ---------------------------------------------------------------------------
# DB-Tests gegen eine Temp-Kopie der Dev-DB
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_DEV_DB, "Dev-DB (yugioh.sqlite3) nicht vorhanden")
class DbTests(unittest.TestCase):
    def setUp(self):
        # Dev-DB in einen Temp-Ordner kopieren; nur die Kopie wird mutiert.
        self._dir = tempfile.mkdtemp(prefix="ygo_test_")
        self.db = os.path.join(self._dir, "test.sqlite3")
        shutil.copy(DEV_DB, self.db)
        ydb.ensure_schema(self.db)
        # Reale Karten-IDs aus der DB ziehen (keine erfundenen Karten).
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            self.main_id = conn.execute(
                "SELECT id FROM cards WHERE frame_type IN ('effect','normal') "
                "LIMIT 1"
            ).fetchone()["id"]
            self.main_id2 = conn.execute(
                "SELECT id FROM cards WHERE frame_type IN ('effect','normal') "
                "AND id != ? LIMIT 1", (self.main_id,)
            ).fetchone()["id"]
            self.extra_id = conn.execute(
                "SELECT id FROM cards WHERE frame_type IN "
                "('fusion','synchro','xyz','link') LIMIT 1"
            ).fetchone()["id"]
        finally:
            conn.close()

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    # -- 3-Kopien-Regel ---------------------------------------------------

    def test_three_copy_rule_on_add(self):
        deck = ydb.create_deck(self.db, "T")
        added, _msg = ydb.add_card_to_deck(self.db, deck, self.main_id, count=5)
        self.assertEqual(added, 3)  # auf 3 gekappt
        self.assertEqual(ydb.deck_counts(self.db, deck)["main"], 3)

    def test_three_copy_rule_across_zones(self):
        # Main + Side derselben Karte duerfen zusammen 3 nicht ueberschreiten.
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=2)
        added, _ = ydb.add_card_to_deck(self.db, deck, self.main_id, zone="side", count=2)
        self.assertEqual(added, 1)  # nur noch eine Kopie erlaubt

    # -- Zonen-Zuordnung --------------------------------------------------

    def test_extra_card_rejected_from_main(self):
        deck = ydb.create_deck(self.db, "T")
        added, msg = ydb.add_card_to_deck(self.db, deck, self.extra_id, zone="main")
        self.assertEqual(added, 0)
        self.assertIn("Extra Deck", msg)

    def test_auto_zone_assignment(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.extra_id)  # zone=None -> auto
        self.assertEqual(ydb.deck_counts(self.db, deck)["extra"], 1)

    # -- Sammlung: identische Drucke zusammenfuehren ----------------------

    def test_collection_merges_identical_prints(self):
        ydb.add_to_collection(self.db, self.main_id, 1, set_code="ABC-001")
        ydb.add_to_collection(self.db, self.main_id, 2, set_code="ABC-001")
        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute(
                "SELECT quantity FROM collection WHERE card_id=? AND set_code=?",
                (self.main_id, "ABC-001"),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)       # ein Eintrag, nicht zwei
        self.assertEqual(rows[0][0], 3)      # Mengen summiert

    # -- Eigene Uebersetzungen ueberleben / schreiben durch ---------------

    def test_translation_writes_through_and_is_searchable(self):
        ydb.set_card_translation(self.db, self.main_id, name_de="Mein Testname")
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            name_de = conn.execute(
                "SELECT name_de FROM cards WHERE id=?", (self.main_id,)
            ).fetchone()["name_de"]
            hit = conn.execute(
                "SELECT rowid FROM cards_fts WHERE cards_fts MATCH ?",
                ("Testname",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(name_de, "Mein Testname")
        self.assertIsNotNone(hit)           # FTS-Index mitgepflegt

    # -- YDK-Roundtrip ----------------------------------------------------

    def test_ydk_export_import_roundtrip(self):
        deck = ydb.create_deck(self.db, "Quelle")
        ydb.add_card_to_deck(self.db, deck, self.main_id, count=2)
        ydb.add_card_to_deck(self.db, deck, self.extra_id, count=1)
        text = ydb.export_deck_ydk(self.db, deck)
        new_id, report = ydb.import_deck_ydk(self.db, "Ziel", text)
        self.assertIsNotNone(new_id)
        self.assertEqual(report["imported"]["main"], 2)
        self.assertEqual(report["imported"]["extra"], 1)

    def test_ydk_import_caps_and_reports(self):
        # Mehr als 3 Kopien im .ydk -> auf 3 gekappt, im Report vermerkt.
        text = "#main\n" + f"{self.main_id}\n" * 5 + "#extra\n!side\n"
        new_id, report = ydb.import_deck_ydk(self.db, "Cap", text)
        self.assertEqual(report["imported"]["main"], 3)
        self.assertTrue(report["capped"])

    # -- Kombo-Abdeckung: nur Main + Extra --------------------------------

    def test_combo_coverage_ignores_side(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="side", count=1)
        combo = ydb.create_combo(self.db, "K")
        ydb.add_combo_card(self.db, combo, self.main_id, 1)
        cov = ydb.combo_coverage(self.db, combo, deck)
        # Karte liegt nur im Side -> zaehlt nicht als abgedeckt.
        self.assertEqual(cov["total"], 1)
        self.assertEqual(cov["covered"], 0)

    def test_combo_coverage_counts_main(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=1)
        combo = ydb.create_combo(self.db, "K")
        ydb.add_combo_card(self.db, combo, self.main_id, 1)
        cov = ydb.combo_coverage(self.db, combo, deck)
        self.assertEqual(cov["covered"], 1)

    # -- Reference-Decks binden keinen Bestand ----------------------------

    def test_reference_deck_does_not_bind_stock(self):
        text = "#main\n" + f"{self.main_id}\n" * 2 + "#extra\n!side\n"
        ydb.import_deck_ydk(self.db, "Meta", text, kind="reference")
        # Referenz-Deck taucht nicht in den eigenen Decks auf ...
        own_names = [d["name"] for d in ydb.list_decks(self.db)]
        self.assertNotIn("Meta", own_names)
        # ... und bindet keinen physischen Bestand.
        self.assertEqual(ydb.card_bound_in_decks(self.db, self.main_id), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
