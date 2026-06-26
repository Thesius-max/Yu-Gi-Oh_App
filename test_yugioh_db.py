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
import random
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

    def test_prob_open_all_single_matches_hypergeom(self):
        # Eine Wunschkarte -> identisch zu hypergeom_at_least(min_hits=1).
        self.assertAlmostEqual(
            ydb.prob_open_all(40, [3], 5),
            ydb.hypergeom_at_least(40, 3, 5),
            places=12,
        )

    def test_prob_open_all_two_cards_inclusion_exclusion(self):
        # P(>=1 von A UND >=1 von B), A=B=3 Kopien, 40 Karten, 5 Zuege.
        expected = (
            math.comb(40, 5) - 2 * math.comb(37, 5) + math.comb(34, 5)
        ) / math.comb(40, 5)
        self.assertAlmostEqual(ydb.prob_open_all(40, [3, 3], 5), expected, places=12)
        # AND ist nie wahrscheinlicher als 'mindestens eine davon'.
        self.assertLessEqual(
            ydb.prob_open_all(40, [3, 3], 5),
            ydb.hypergeom_at_least(40, 6, 5),
        )

    def test_prob_open_all_edge_cases(self):
        self.assertEqual(ydb.prob_open_all(40, [], 5), 1.0)    # keine Bedingung
        self.assertEqual(ydb.prob_open_all(40, [3], 0), 0.0)   # keine Zuege
        self.assertEqual(ydb.prob_open_all(0, [3], 5), 0.0)    # leeres Deck

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
            # Karten, die garantiert NICHT im Bestand liegen (fuer
            # deterministische Sammlungs-Abdeckung).
            unowned = conn.execute(
                "SELECT id FROM cards WHERE id NOT IN "
                "(SELECT card_id FROM collection) LIMIT 2"
            ).fetchall()
            self.unowned_id = unowned[0]["id"]
            self.unowned_id2 = unowned[1]["id"]
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

    def test_untranslated_filter_and_count(self):
        # Eine unuebersetzte Karte aus dem echten Bestand greifen.
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT col.card_id FROM collection col "
                "JOIN cards c ON c.id = col.card_id "
                "WHERE c.name_de IS NULL LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row, "Dev-DB sollte unuebersetzte Bestandskarten haben")
        card_id = row["card_id"]

        before = ydb.collection_untranslated_count(self.db)
        rows = ydb.list_collection(self.db, untranslated_only=True)
        # Filter liefert ausschliesslich Karten ohne deutsche Uebersetzung.
        self.assertTrue(all(r["name_de"] is None for r in rows))
        # Zaehler == verschiedene Karten in der gefilterten Ansicht.
        self.assertEqual(before, len({r["card_id"] for r in rows}))
        self.assertIn(card_id, {r["card_id"] for r in rows})

        # Uebersetzen -> faellt aus Filter und Zaehler.
        ydb.set_card_translation(self.db, card_id, name_de="Testname DE")
        self.assertEqual(ydb.collection_untranslated_count(self.db), before - 1)
        rows2 = ydb.list_collection(self.db, untranslated_only=True)
        self.assertNotIn(card_id, {r["card_id"] for r in rows2})

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

    def test_combos_for_deck_aggregates_coverage(self):
        # Frisches Deck -> Abdeckung gegen genau dieses Deck ist deterministisch.
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=1)
        combo = ydb.create_combo(self.db, "K")
        ydb.add_combo_card(self.db, combo, self.main_id, 1)
        ydb.add_combo_card(self.db, combo, self.extra_id, 1)  # nicht im Deck
        row = next(
            r for r in ydb.combos_for_deck(self.db, deck)
            if r["combo_id"] == combo
        )
        self.assertEqual(row["total"], 2)
        self.assertEqual(row["covered"], 1)
        self.assertAlmostEqual(row["coverage"], 0.5)

    def test_combo_coverage_collection(self):
        combo = ydb.create_combo(self.db, "K")
        ydb.add_combo_card(self.db, combo, self.unowned_id, 1)
        ydb.add_combo_card(self.db, combo, self.unowned_id2, 1)
        cov = ydb.combo_coverage_collection(self.db, combo)
        self.assertEqual(cov["total"], 2)
        self.assertEqual(cov["covered"], 0)         # nichts im Bestand
        ydb.add_to_collection(self.db, self.unowned_id, 1)
        cov = ydb.combo_coverage_collection(self.db, combo)
        self.assertEqual(cov["covered"], 1)         # jetzt ein Baustein da

    # -- Vorschlaege: Namen aufloesen -------------------------------------

    def test_deck_suggestions_resolves_names(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=1)
        combo = ydb.create_combo(self.db, "K")
        ydb.add_combo_card(self.db, combo, self.main_id, 1)
        ydb.add_combo_card(self.db, combo, self.main_id2, 1)  # Kandidat
        res = ydb.deck_suggestions(self.db, deck)
        sug = {s["card_id"]: s for s in res["suggestions"]}
        self.assertIn(self.main_id2, sug)
        # Name aufgeloest, nicht der Zahlen-Fallback str(id).
        self.assertFalse(sug[self.main_id2]["name"].isdigit())
        self.assertGreaterEqual(sug[self.main_id2]["direct"], 1)

    # -- Starthand-Simulator ----------------------------------------------

    def test_draw_sample_hand_is_deterministic_and_valid(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=3)
        ydb.add_card_to_deck(self.db, deck, self.main_id2, zone="main", count=2)
        pool_ids = {self.main_id, self.main_id2}
        hand = ydb.draw_sample_hand(self.db, deck, 5, rng=random.Random(0))
        self.assertEqual(len(hand), 5)                       # 5 aus 5 Kopien
        self.assertTrue(all(c["card_id"] in pool_ids for c in hand))
        # Kopiengrenzen nicht ueberschritten (max 3x main_id, 2x main_id2).
        counts = {}
        for c in hand:
            counts[c["card_id"]] = counts.get(c["card_id"], 0) + 1
        self.assertLessEqual(counts.get(self.main_id, 0), 3)
        self.assertLessEqual(counts.get(self.main_id2, 0), 2)
        # Gleicher Seed -> gleiche Hand (Reproduzierbarkeit).
        again = ydb.draw_sample_hand(self.db, deck, 5, rng=random.Random(0))
        self.assertEqual([c["card_id"] for c in hand],
                         [c["card_id"] for c in again])

    def test_draw_sample_hand_caps_to_main_size(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=2)
        hand = ydb.draw_sample_hand(self.db, deck, 5, rng=random.Random(1))
        self.assertEqual(len(hand), 2)                       # nur 2 Karten da

    def test_deck_main_cards_copies_and_roles(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=3)
        combo = ydb.create_combo(self.db, "K")
        ydb.add_combo_card(self.db, combo, self.main_id, 1)
        ydb.set_combo_card_role(self.db, combo, self.main_id, "starter")
        cards = {c["card_id"]: c for c in ydb.deck_main_cards(self.db, deck)}
        self.assertEqual(cards[self.main_id]["copies"], 3)
        self.assertIn("starter", cards[self.main_id]["roles"])

    # -- Kombo-Varianten (Branches) ---------------------------------------

    def test_variant_link_and_listing(self):
        parent = ydb.create_combo(self.db, "Hauptlinie")
        child = ydb.create_combo(self.db, "Variante")
        ydb.set_combo_parent(self.db, child, parent)
        got = ydb.get_combo(self.db, child)
        self.assertEqual(got["parent_combo_id"], parent)
        self.assertEqual(got["parent_name"], "Hauptlinie")
        # Variante haengt unter der Hauptlinie ...
        self.assertEqual(
            [v["combo_id"] for v in ydb.combo_variants(self.db, parent)], [child]
        )
        # ... und taucht nicht in der Hauptlinien-Liste auf.
        ids = [c["combo_id"] for c in ydb.list_combos(self.db)]
        self.assertIn(parent, ids)
        self.assertNotIn(child, ids)

    def test_set_combo_parent_guards(self):
        a = ydb.create_combo(self.db, "A")
        b = ydb.create_combo(self.db, "B")
        c = ydb.create_combo(self.db, "C")
        with self.assertRaises(ValueError):       # Selbst-Verknuepfung
            ydb.set_combo_parent(self.db, a, a)
        ydb.set_combo_parent(self.db, b, a)       # B wird Variante von A
        with self.assertRaises(ValueError):       # parent darf keine Variante sein
            ydb.set_combo_parent(self.db, c, b)
        with self.assertRaises(ValueError):       # A hat Varianten -> nicht selbst Variante
            ydb.set_combo_parent(self.db, a, c)

    def test_variant_excluded_from_aggregates(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=1)
        parent = ydb.create_combo(self.db, "Haupt")
        ydb.add_combo_card(self.db, parent, self.main_id, 1)
        ydb.add_combo_card(self.db, parent, self.main_id2, 1)
        variant = ydb.create_combo(self.db, "Var")
        ydb.add_combo_card(self.db, variant, self.main_id, 1)
        ydb.add_combo_card(self.db, variant, self.extra_id, 1)
        ydb.set_combo_parent(self.db, variant, parent)
        # combos_for_deck zaehlt nur die Hauptlinie.
        deck_combo_ids = [c["combo_id"] for c in ydb.combos_for_deck(self.db, deck)]
        self.assertIn(parent, deck_combo_ids)
        self.assertNotIn(variant, deck_combo_ids)
        # Synergie-Kanten der Variante (main_id<->extra_id) entstehen nicht.
        edges = ydb.synergy_edges(self.db)
        pair = tuple(sorted((self.main_id, self.extra_id)))
        self.assertNotIn(pair, edges)

    def test_delete_parent_cascades_variants(self):
        parent = ydb.create_combo(self.db, "Haupt")
        variant = ydb.create_combo(self.db, "Var")
        ydb.set_combo_parent(self.db, variant, parent)
        ydb.delete_combo(self.db, parent)
        self.assertIsNone(ydb.get_combo(self.db, variant))   # mitgeloescht

    # -- Reference-Decks binden keinen Bestand ----------------------------

    def test_reference_deck_does_not_bind_stock(self):
        text = "#main\n" + f"{self.main_id}\n" * 2 + "#extra\n!side\n"
        ydb.import_deck_ydk(self.db, "Meta", text, kind="reference")
        # Referenz-Deck taucht nicht in den eigenen Decks auf ...
        own_names = [d["name"] for d in ydb.list_decks(self.db)]
        self.assertNotIn("Meta", own_names)
        # ... und bindet keinen physischen Bestand.
        self.assertEqual(ydb.card_bound_in_decks(self.db, self.main_id), 0)

    # -- Sammlungs-/Deck-Export ------------------------------------------

    def test_export_deck_text_lists_zones(self):
        deck = ydb.create_deck(self.db, "Export-Deck")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=2)
        ydb.add_card_to_deck(self.db, deck, self.extra_id, zone="extra", count=1)
        text = ydb.export_deck_text(self.db, deck)
        self.assertIn("Deck: Export-Deck", text)
        self.assertIn("== Main Deck (2) ==", text)
        self.assertIn("== Extra Deck (1) ==", text)
        self.assertIn("2x ", text)

    def test_export_deck_text_unknown_deck_raises(self):
        with self.assertRaises(ValueError):
            ydb.export_deck_text(self.db, 999999)

    def test_export_deck_markdown_has_effects_role_and_combos(self):
        deck = ydb.create_deck(self.db, "MD-Deck")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=1)
        combo = ydb.create_combo(self.db, "K")
        ydb.add_combo_card(self.db, combo, self.main_id, 1)
        ydb.set_combo_card_role(self.db, combo, self.main_id, "starter")
        md = ydb.export_deck_markdown(self.db, deck)
        self.assertIn("# Deck: MD-Deck", md)
        self.assertIn("## Main Deck (1)", md)
        self.assertIn("- Effekt:", md)            # voller Kartentext
        self.assertIn("Starter", md)              # Rolle uebernommen
        self.assertIn("## Konsistenz & Kombo-Linien", md)

    def test_export_collection_text_respects_filter(self):
        # Eine bekannte Karte in den Bestand legen und gezielt danach filtern.
        ydb.add_to_collection(self.db, self.unowned_id, 2, set_code="TST-001")
        name = sqlite3.connect(self.db).execute(
            "SELECT COALESCE(name_de, name) FROM cards WHERE id = ?",
            (self.unowned_id,),
        ).fetchone()[0]
        full = ydb.export_collection_text(self.db)
        self.assertIn("Sammlung", full)
        self.assertIn(name, full)
        # Filter auf einen Namensteil grenzt die Ausgabe ein.
        filtered = ydb.export_collection_text(self.db, text=name)
        self.assertIn(name, filtered)
        self.assertIn("TST-001", filtered)

    def test_export_collection_markdown_aggregates_copies(self):
        # Zwei Drucke derselben Karte -> in der KI-Ansicht eine Karte, Menge 3.
        ydb.add_to_collection(self.db, self.unowned_id, 1, set_code="A")
        ydb.add_to_collection(self.db, self.unowned_id, 2, set_code="B")
        name = sqlite3.connect(self.db).execute(
            "SELECT COALESCE(name_de, name) FROM cards WHERE id = ?",
            (self.unowned_id,),
        ).fetchone()[0]
        md = ydb.export_collection_markdown(self.db)
        self.assertIn("# Yu-Gi-Oh!-Sammlung", md)
        self.assertIn(f"### 3x {name}", md)
        self.assertIn("- Effekt:", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
