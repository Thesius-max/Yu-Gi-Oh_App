"""
Datenschicht: Kombo-Bibliothek -- Bausteine, Rollen, Schritte, Varianten
(Branch-Modell), Abdeckung gegen Deck/Sammlung, Fahrplan-Aggregate.
Die Dev-DB hat keine Kombos; jeder Test saet seine eigenen.
"""

from __future__ import annotations

import unittest

import yugioh_db as ydb

from tests._support import CardIdsTestCase


class LegacyComboTests(CardIdsTestCase):
    """Aus test_yugioh_db.DbTests uebernommen."""

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
        self.assertEqual(row["present"], 1)
        self.assertAlmostEqual(row["coverage"], 0.5)
        # Teilweise vorhanden (1 von 2 Kopien): zaehlt fuer 'present', nicht 'covered'.
        ydb.add_combo_card(self.db, combo, self.main_id, 1)          # jetzt 2 benoetigt
        row = next(r for r in ydb.combos_for_deck(self.db, deck) if r["combo_id"] == combo)
        self.assertEqual((row["covered"], row["present"]), (0, 1))

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

    def test_list_combos_text_filter(self):
        # Filter nach Name, Archetyp oder Baustein-Kartenname.
        c_name = ydb.create_combo(self.db, "Resonator-Turbo")
        c_arch = ydb.create_combo(self.db, "Andere Linie", archetype="Resonator")
        c_piece = ydb.create_combo(self.db, "Dritte Linie")
        ydb.add_combo_card(self.db, c_piece, self.main_id, 1)
        # Anzeigename des Bausteins fuer die Piece-Suche ermitteln.
        piece_name = ydb.combo_cards(self.db, c_piece)[0]["name"]

        def ids(text):
            return {c["combo_id"] for c in ydb.list_combos(self.db, text=text)}

        self.assertIn(c_name, ids("resonator"))     # Name-Treffer
        self.assertIn(c_arch, ids("resonator"))      # Archetyp-Treffer
        self.assertNotIn(c_piece, ids("resonator"))  # kein Bezug
        self.assertIn(c_piece, ids(piece_name[:4]))  # Baustein-Name-Treffer
        self.assertEqual(ids("garantiert-kein-treffer-xyz"), set())

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

    def test_deck_boss_lines_variants(self):
        parent = ydb.create_combo(self.db, "Haupt")
        child = ydb.create_combo(self.db, "Var")
        ydb.set_combo_parent(self.db, child, parent)
        solo = ydb.create_combo(self.db, "Solo")
        deck = ydb.create_deck(self.db, "T")

        lines = {
            line["combo_id"]: line
            for g in ydb.deck_boss_lines(self.db, deck)
            for line in g["lines"]
        }
        self.assertEqual(lines[parent]["variants"], ["Var"])
        self.assertEqual(lines[solo]["variants"], [])
        self.assertNotIn(child, lines)  # Branch ist keine eigene Linie

    def test_list_combos_text_search_en_de_variant_escape(self):
        cid = self.translated_card()
        card = self.query("SELECT name, name_de FROM cards WHERE id = ?", (cid,))[0]
        main = ydb.create_combo(self.db, "Hauptlinie")
        ydb.add_combo_card(self.db, main, cid, 1)
        other = ydb.create_combo(self.db, "Übermut 100%")
        variant = ydb.create_combo(self.db, "Gegen Nibiru")
        ydb.set_combo_parent(self.db, variant, other)

        def ids(text):
            return {r["combo_id"] for r in ydb.list_combos(self.db, text=text)}

        self.assertEqual(ids(card["name"][:6]), {main})      # englisch
        self.assertEqual(ids(card["name_de"][:6]), {main})   # deutsch
        self.assertEqual(ids("übermut"), {other})            # Umlaut-Casefold
        self.assertEqual(ids("nibiru"), {other})             # via Variante
        self.assertEqual(ids("0%"), {other})                 # % wortwoertlich
        self.assertEqual(ids("_"), set())                    # _ kein Joker

    def test_get_combo_boss_name_is_german(self):
        cid = self.translated_card()
        combo = ydb.create_combo(self.db, "K")
        ydb.add_combo_card(self.db, combo, cid, 1)
        ydb.set_combo_boss(self.db, combo, cid)
        self.assertEqual(ydb.get_combo(self.db, combo)["boss_name"],
                         self.display_name(cid))

    def test_combo_variants_by_parent(self):
        a = ydb.create_combo(self.db, "A")
        v2 = ydb.create_combo(self.db, "Z-Var")
        v1 = ydb.create_combo(self.db, "B-Var")
        for v in (v2, v1):
            ydb.set_combo_parent(self.db, v, a)
        grouped = ydb.combo_variants_by_parent(self.db)
        self.assertEqual([r["combo_id"] for r in grouped[a]], [v1, v2])


# ---------------------------------------------------------------------------
# Neu
# ---------------------------------------------------------------------------

class ComboCrudTests(CardIdsTestCase):
    def test_create_and_update(self):
        combo = ydb.create_combo(self.db, "Alt", archetype="A")
        ydb.update_combo(self.db, combo, "Neu", "Branded", "Start: X\nEnd: Y")
        got = ydb.get_combo(self.db, combo)
        self.assertEqual((got["name"], got["archetype"], got["notes"]),
                         ("Neu", "Branded", "Start: X\nEnd: Y"))
        self.assertIsNone(got["boss_card_id"])
        self.assertIsNone(got["parent_combo_id"])

    def test_get_unknown_combo_is_none(self):
        self.assertIsNone(ydb.get_combo(self.db, 999_999))

    def test_home_deck_filter(self):
        deck = self.own_deck()
        home = ydb.create_combo(self.db, "B mit Deck", deck_id=deck)
        loose = ydb.create_combo(self.db, "A ohne Deck")
        ids = lambda **kw: [c["combo_id"] for c in ydb.list_combos(self.db, **kw)]
        self.assertEqual(ids(), [loose, home])             # nach Name sortiert
        self.assertEqual(ids(deck_id=deck), [home])
        self.assertEqual(ids(deck_id=0), [loose])
        self.assertEqual(ydb.list_combos(self.db, deck_id=deck)[0]["deck_name"], "RDA-Mitsu")
        ydb.set_combo_deck(self.db, home, None)
        self.assertEqual(ids(deck_id=deck), [])

    def test_piece_quantities_are_capped(self):
        combo = ydb.create_combo(self.db, "K")
        ydb.add_combo_card(self.db, combo, self.main_id, 2)
        ydb.add_combo_card(self.db, combo, self.main_id, 2)
        ydb.add_combo_card(self.db, combo, self.main_id2, 9)
        qty = {p["card_id"]: p["quantity"] for p in ydb.combo_cards(self.db, combo)}
        self.assertEqual(qty, {self.main_id: 3, self.main_id2: 3})
        ydb.set_combo_card_quantity(self.db, combo, self.main_id, 7)
        ydb.set_combo_card_quantity(self.db, combo, self.main_id2, 0)   # entfernt
        qty = {p["card_id"]: p["quantity"] for p in ydb.combo_cards(self.db, combo)}
        self.assertEqual(qty, {self.main_id: 3})

    def test_roles(self):
        combo = ydb.create_combo(self.db, "K")
        ydb.add_combo_card(self.db, combo, self.main_id, 1)
        with self.assertRaises(ValueError):
            ydb.set_combo_card_role(self.db, combo, self.main_id, "boss")
        ydb.set_combo_card_role(self.db, combo, self.main_id, "handtrap")
        self.assertEqual(ydb.combo_cards(self.db, combo)[0]["role"], "handtrap")
        ydb.set_combo_card_role(self.db, combo, self.main_id, None)
        self.assertIsNone(ydb.combo_cards(self.db, combo)[0]["role"])

    def test_remove_piece(self):
        combo = self.seed_combo("K", {self.main_id: None, self.main_id2: None})
        ydb.remove_combo_card(self.db, combo, self.main_id)
        self.assertEqual([p["card_id"] for p in ydb.combo_cards(self.db, combo)],
                         [self.main_id2])

    def test_combo_cards_sorted_with_german_names(self):
        ids = [self.translated_card(), self.main_id, self.extra_id]
        combo = self.seed_combo("K", {cid: None for cid in ids})
        pieces = ydb.combo_cards(self.db, combo)
        self.assertEqual({p["card_id"]: p["name"] for p in pieces},
                         {cid: self.display_name(cid) for cid in ids})
        names = [p["name"] for p in pieces]
        self.assertEqual(names, sorted(names))

    def test_steps_replace_and_keep_order(self):
        combo = ydb.create_combo(self.db, "K")
        ydb.set_combo_steps(self.db, combo, ["NS A", "SS B (GY)", "Link: A + B -> C"])
        self.assertEqual([(s["step_no"], s["text"]) for s in ydb.combo_steps(self.db, combo)],
                         [(1, "NS A"), (2, "SS B (GY)"), (3, "Link: A + B -> C")])
        ydb.set_combo_steps(self.db, combo, ["Draw 1"])
        self.assertEqual([s["text"] for s in ydb.combo_steps(self.db, combo)], ["Draw 1"])
        ydb.set_combo_steps(self.db, combo, [])
        self.assertEqual(ydb.combo_steps(self.db, combo), [])

    def test_delete_cascades_pieces_and_steps(self):
        combo = self.seed_combo("K", {self.main_id: "starter"}, steps=["NS A"])
        ydb.delete_combo(self.db, combo)
        for table in ("combo_cards", "combo_steps"):
            self.assertEqual(self.scalar(
                f"SELECT COUNT(*) FROM {table} WHERE combo_id = ?", (combo,)), 0)

    def test_parent_can_be_detached_and_must_exist(self):
        parent = ydb.create_combo(self.db, "Haupt")
        child = ydb.create_combo(self.db, "Var")
        ydb.set_combo_parent(self.db, child, parent)
        ydb.set_combo_parent(self.db, child, None)
        self.assertIn(child, [c["combo_id"] for c in ydb.list_combos(self.db)])
        self.assertEqual(ydb.combo_variants(self.db, parent), [])
        with self.assertRaisesRegex(ValueError, "nicht gefunden"):
            ydb.set_combo_parent(self.db, child, 999_999)


class CoverageTests(CardIdsTestCase):
    def test_partial_copies_are_missing(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, count=1)
        combo = ydb.create_combo(self.db, "K")
        ydb.add_combo_card(self.db, combo, self.main_id, 2)
        piece = ydb.combo_coverage(self.db, combo, deck)["pieces"][0]
        self.assertEqual((piece["needed"], piece["have"], piece["missing"]), (2, 1, 1))
        self.assertEqual(ydb.combo_coverage(self.db, combo, deck)["covered"], 0)

    def test_extra_deck_counts(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.extra_id)
        combo = self.seed_combo("K", {self.extra_id: "payoff"})
        self.assertEqual(ydb.combo_coverage(self.db, combo, deck)["covered"], 1)

    def test_combos_for_deck_sorting(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id)
        full = self.seed_combo("Z voll", {self.main_id: None})
        half = self.seed_combo("A halb", {self.main_id: None, self.main_id2: None})
        empty = ydb.create_combo(self.db, "B leer")
        order = [c["combo_id"] for c in ydb.combos_for_deck(self.db, deck)]
        self.assertEqual(order, [full, half, empty])
        row = next(c for c in ydb.combos_for_deck(self.db, deck) if c["combo_id"] == empty)
        self.assertEqual((row["total"], row["coverage"]), (0, 0.0))

    def test_combos_for_collection_sorting(self):
        owned = self.scalar("SELECT card_id FROM collection ORDER BY entry_id LIMIT 1")
        full = self.seed_combo("Z voll", {owned: None})
        half = self.seed_combo("A halb", {owned: None, self.unowned_id: None})
        none = self.seed_combo("M nichts", {self.unowned_id: None})
        rows = ydb.combos_for_collection(self.db)
        self.assertEqual([r["combo_id"] for r in rows], [full, half, none])
        self.assertEqual([r["coverage"] for r in rows], [1.0, 0.5, 0.0])

    def test_real_deck_coverage_of_its_own_cards(self):
        deck = self.own_deck()
        cards = [r["card_id"] for r in ydb.deck_cards(self.db, deck, "main")[:4]]
        combo = self.seed_combo("Deck-Linie", {cid: None for cid in cards}, deck_id=deck)
        cov = ydb.combo_coverage(self.db, combo, deck)
        self.assertEqual((cov["total"], cov["covered"]), (4, 4))


class FahrplanTests(CardIdsTestCase):
    def test_role_summary_main_extra_no_side_no_variants(self):
        a, b, s = self.main_ids(3)
        e = self.extra_ids(1)[0]
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, a, count=2)
        ydb.add_card_to_deck(self.db, deck, b, count=1)
        ydb.add_card_to_deck(self.db, deck, e, count=1)
        ydb.add_card_to_deck(self.db, deck, s, zone="side", count=1)
        self.seed_combo("K1", {a: "starter", e: "payoff", s: "starter"})
        self.seed_combo("K2", {a: "starter", b: "extender"})
        var = self.seed_combo("Var", {b: "handtrap"})
        ydb.set_combo_parent(self.db, var, self.seed_combo("Haupt", {}))

        summary = ydb.deck_role_summary(self.db, deck)
        flat = {r: [(c["card_id"], c["copies"]) for c in cards] for r, cards in summary.items()}
        self.assertEqual(flat, {
            "starter": [(a, 2)],                  # einmal, obwohl in zwei Kombos
            "payoff": [(e, 1)],                   # Extra Deck zaehlt
            "extender": [(b, 1)],
        })                                        # Side + Varianten-Rolle fehlen

    def test_boss_lines_grouping_and_order(self):
        deck = ydb.create_deck(self.db, "T")
        a, b = self.main_ids(2)
        boss = self.extra_ids(1)[0]
        ydb.add_card_to_deck(self.db, deck, a)
        k_full = self.seed_combo("Z voll", {a: None})
        k_half = self.seed_combo("A halb", {a: None, b: None})
        k_none = self.seed_combo("Ohne Boss", {a: None})
        for k in (k_full, k_half):
            ydb.set_combo_boss(self.db, k, boss)
        groups = ydb.deck_boss_lines(self.db, deck)
        self.assertEqual([g["boss_card_id"] for g in groups], [boss, None])
        self.assertEqual(groups[0]["boss_name"], self.display_name(boss))
        self.assertEqual([l["combo_id"] for l in groups[0]["lines"]], [k_full, k_half])
        self.assertEqual([l["combo_id"] for l in groups[1]["lines"]], [k_none])

    def test_boss_lines_empty_without_combos(self):
        self.assertEqual(ydb.deck_boss_lines(self.db, self.own_deck()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
