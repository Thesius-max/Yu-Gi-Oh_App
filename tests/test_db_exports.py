"""
Datenschicht: Text-/Markdown-Exporte fuer Sammlung, Deck und Kombo-Linien.
"""

from __future__ import annotations

import datetime
import re
import unittest

import yugioh_db as ydb

from tests._support import CardIdsTestCase

TODAY = datetime.date.today().strftime("%d.%m.%Y")


class LegacyExportTests(CardIdsTestCase):
    """Aus test_yugioh_db.DbTests uebernommen."""

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
        name = self.display_name(self.unowned_id)
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
        name = self.display_name(self.unowned_id)
        md = ydb.export_collection_markdown(self.db)
        self.assertIn("# Yu-Gi-Oh!-Sammlung", md)
        self.assertIn(f"### 3x {name}", md)
        self.assertIn("- Effekt:", md)


class CollectionExportTests(CardIdsTestCase):
    def test_full_text_export_header_and_totals(self):
        text = ydb.export_collection_text(self.db)
        entries, unique, total = ydb.collection_stats(self.db)
        lines = text.splitlines()
        self.assertEqual(lines[0], "Sammlung")
        self.assertEqual(lines[1], f"Stand: {TODAY}")
        self.assertEqual(lines[2], f"{entries} Eintrag(e) · {unique} verschiedene "
                                   f"Karten · {total} Karten gesamt")
        # Eine Zeile je Eintrag, Gruppen-Summen ergeben das Gesamt.
        self.assertEqual(sum(1 for l in lines if re.match(r"  \d+x ", l)), entries)
        group_sum = sum(int(m) for m in re.findall(r"^== \w+ \((\d+)\) ==$", text, re.M))
        self.assertEqual(group_sum, total)
        self.assertTrue(text.endswith("\n"))

    def test_groups_in_fixed_order(self):
        text = ydb.export_collection_text(self.db)
        heads = re.findall(r"^== (\w+) \(", text, re.M)
        order = ["Monster", "Zauber", "Falle", "Sonstige"]
        self.assertEqual(heads, [h for h in order if h in heads])

    def test_print_details_and_notes_in_line(self):
        ydb.add_to_collection(self.db, self.unowned_id, 2, set_code="LOB-001",
                              edition="1st", condition="NM", notes=" Binder ")
        text = ydb.export_collection_text(self.db, text=self.display_name(self.unowned_id))
        self.assertIn(f"  2x {self.display_name(self.unowned_id)}  "
                      f"[LOB-001, 1st, NM]  (Binder)", text)

    def test_untranslated_filter_in_title_and_content(self):
        text = ydb.export_collection_text(self.db, untranslated_only=True)
        self.assertTrue(text.startswith("Sammlung — nur unübersetzte\n"))
        translated = self.query(
            "SELECT DISTINCT c.name_de FROM collection col JOIN cards c "
            "ON c.id = col.card_id WHERE c.name_de IS NOT NULL LIMIT 5")
        for r in translated:
            self.assertNotIn(f"x {r[0]}\n", text)
        n = ydb.collection_untranslated_count(self.db)
        self.assertIn(f"{n} verschiedene Karten", text)

    def test_markdown_one_block_per_card(self):
        md = ydb.export_collection_markdown(self.db, category="spell")
        self.assertTrue(md.startswith("# Yu-Gi-Oh!-Sammlung — Klasse=Zauber\n"))
        spells = {r["card_id"] for r in ydb.list_collection(self.db, category="spell")}
        self.assertEqual(md.count("\n### "), len(spells))
        self.assertIn(f"## Zauber ({len(spells)})", md)
        self.assertNotIn("## Monster", md)

    def test_empty_filter_result(self):
        text = ydb.export_collection_text(self.db, text="garantiert-kein-treffer")
        self.assertIn("0 Eintrag(e) · 0 verschiedene Karten · 0 Karten gesamt", text)
        self.assertNotIn("==", text)


class DeckExportTests(CardIdsTestCase):
    def test_real_deck_text(self):
        deck = self.own_deck()
        text = ydb.export_deck_text(self.db, deck)
        counts = ydb.deck_counts(self.db, deck)
        self.assertTrue(text.startswith(f"Deck: RDA-Mitsu\nStand: {TODAY}\n"))
        self.assertIn(f"== Main Deck ({counts['main']}) ==", text)
        self.assertIn(f"== Extra Deck ({counts['extra']}) ==", text)
        if counts["side"] == 0:
            self.assertNotIn("Side Deck", text)              # leere Zone fehlt ...
        ydb.add_card_to_deck(self.db, deck, self.main_ids(1)[0], zone="side")
        self.assertIn("== Side Deck (", ydb.export_deck_text(self.db, deck))  # ... bis belegt
        for r in ydb.deck_cards(self.db, deck, "main"):
            self.assertIn(f"  {r['quantity']}x {r['name']}\n", text)

    def test_side_zone_listed(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="side", count=2)
        self.assertIn("== Side Deck (2) ==", ydb.export_deck_text(self.db, deck))

    def test_markdown_real_deck_blocks_and_fence(self):
        deck = self.own_deck()
        md = ydb.export_deck_markdown(self.db, deck)
        n_cards = sum(len(ydb.deck_cards(self.db, deck, z)) for z in ("main", "extra", "side"))
        self.assertEqual(md.count("\n### "), n_cards)
        # Kombo-Text im Codeblock: genau zwei Fence-Zeilen am Ende.
        self.assertEqual(len(re.findall(r"^```+$", md, re.M)), 2)
        self.assertIn("(Keine Kombos mit Bausteinen aus diesem Deck erfasst.)", md)

    def test_markdown_unknown_deck_raises(self):
        with self.assertRaises(ValueError):
            ydb.export_deck_markdown(self.db, 999_999)

    def test_markdown_escapes_card_names(self):
        # Echte Karte, deren (eigener) Name Markdown-Sonderzeichen traegt.
        ydb.set_card_translation(self.db, self.main_id, name_de="Maliss <P> `Test`")
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id)
        md = ydb.export_deck_markdown(self.db, deck)
        self.assertIn(r"### 1x Maliss \<P\> \`Test\`", md)


class ComboLinesExportTests(CardIdsTestCase):
    def _deck_with_lines(self):
        a, b, far = self.main_ids(3)
        boss = self.extra_ids(1)[0]
        deck = ydb.create_deck(self.db, "Linien-Deck")
        ydb.add_card_to_deck(self.db, deck, a, count=3)
        ydb.add_card_to_deck(self.db, deck, boss)
        main = self.seed_combo("Hauptlinie", {a: "starter", b: "extender", boss: "payoff"},
                               steps=["NS A", "Link: A + B -> Boss"])
        ydb.set_combo_boss(self.db, main, boss)
        ydb.update_combo(self.db, main, "Hauptlinie", "Testtyp", "Start: A\nEnd: Boss")
        var = self.seed_combo("Gegen Ash", {a: None}, steps=["SS B (GY)"])
        ydb.set_combo_parent(self.db, var, main)
        home = ydb.create_combo(self.db, "Heimat ohne Bausteine", deck_id=deck)
        foreign = self.seed_combo("Fremde Linie", {far: "starter"})
        return deck, dict(a=a, b=b, boss=boss, main=main, home=home, foreign=foreign)

    def test_lines_filter_and_content(self):
        deck, ids = self._deck_with_lines()
        text = ydb.export_deck_combos_text(self.db, deck)
        self.assertTrue(text.startswith("Kombo-Linien — Linien-Deck\n"))
        self.assertIn("1) Hauptlinie  [Testtyp]", text)
        self.assertIn(f"Boss: {self.display_name(ids['boss'])} · "
                      "Abdeckung: 2/3 Bausteine im Deck", text)
        self.assertIn("   Start: A\n   End: Boss\n", text)
        self.assertIn(f"1x {self.display_name(ids['b'])}  [Extender]  (fehlt im Deck)", text)
        self.assertIn("     1. NS A\n     2. Link: A + B -> Boss\n", text)
        self.assertIn("   ↳ Variante: Gegen Ash\n     1. SS B (GY)\n", text)
        self.assertIn("Heimat ohne Bausteine", text)      # Heimat-Deck zaehlt
        self.assertNotIn("Fremde Linie", text)            # kein Bezug zum Deck

    def test_consistency_and_roles_section(self):
        deck, ids = self._deck_with_lines()
        text = ydb.export_deck_combos_text(self.db, deck)
        self.assertIn("== Konsistenz ==\nKopien im Main: Starter 3\n", text)
        p = ydb.hypergeom_at_least(3, 3, 5)
        self.assertIn(f"Starthand 5: ≥1 Starter {100 * p:.1f}".replace(".", ","), text)
        self.assertIn("== Rollen im Deck (Main + Extra) ==\nStarter (3):\n"
                      f"  3x {self.display_name(ids['a'])}\n", text)
        self.assertIn(f"Payoff (1):\n  1x {self.display_name(ids['boss'])}\n", text)

    def test_partial_copies_marker(self):
        a = self.main_ids(1)[0]
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, a)
        combo = ydb.create_combo(self.db, "K", deck_id=deck)
        ydb.add_combo_card(self.db, combo, a, 3)
        text = ydb.export_deck_combos_text(self.db, deck)
        self.assertIn(f"3x {self.display_name(a)}  (nur 1/3 im Deck)", text)

    def test_combo_with_partially_present_piece_is_listed(self):
        """Regression (Befund 2026-09-24): 'Kombos mit >=1 Baustein im Deck'
        schliesst Bausteine ein, die nur teilweise (1 von 3 Kopien) im Deck
        liegen -- vorher filterte der Export auf voll abgedeckte Bausteine."""
        a = self.main_ids(1)[0]
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, a)
        combo = ydb.create_combo(self.db, "Teilweise")
        ydb.add_combo_card(self.db, combo, a, 3)
        self.assertIn("Teilweise", ydb.export_deck_combos_text(self.db, deck))

    def test_no_lines_and_unknown_deck(self):
        deck = ydb.create_deck(self.db, "Leer")
        text = ydb.export_deck_combos_text(self.db, deck)
        self.assertIn("(Keine Kombos mit Bausteinen aus diesem Deck erfasst.)", text)
        self.assertNotIn("== Konsistenz ==", text)
        with self.assertRaises(ValueError):
            ydb.export_deck_combos_text(self.db, 999_999)

    def test_markdown_fence_survives_backticks_in_steps(self):
        deck, ids = self._deck_with_lines()
        ydb.set_combo_steps(self.db, ids["main"], ["NS A ```x```"])
        md = ydb.export_deck_markdown(self.db, deck)
        fences = re.findall(r"^(`+)$", md, re.M)
        self.assertEqual(fences, ["````", "````"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
