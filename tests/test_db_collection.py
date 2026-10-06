"""
Datenschicht: Sammlung -- Zusammenfuehren identischer Drucke, Mengen,
Filter (Text de/en, Klasse, Attribut, Archetyp, unuebersetzt), Statistiken.
"""

from __future__ import annotations

import unittest

import yugioh_db as ydb

from tests._support import CardIdsTestCase


class LegacyCollectionTests(CardIdsTestCase):
    """Aus test_yugioh_db.DbTests uebernommen."""

    def test_collection_merges_identical_prints(self):
        ydb.add_to_collection(self.db, self.main_id, 1, set_code="ABC-001")
        ydb.add_to_collection(self.db, self.main_id, 2, set_code="ABC-001")
        rows = self.query(
            "SELECT quantity FROM collection WHERE card_id=? AND set_code=?",
            (self.main_id, "ABC-001"),
        )
        self.assertEqual(len(rows), 1)       # ein Eintrag, nicht zwei
        self.assertEqual(rows[0][0], 3)      # Mengen summiert

    def test_untranslated_filter_and_count(self):
        # Eine unuebersetzte Karte aus dem echten Bestand greifen.
        row = self.query(
            "SELECT col.card_id FROM collection col "
            "JOIN cards c ON c.id = col.card_id "
            "WHERE c.name_de IS NULL LIMIT 1"
        )
        self.assertTrue(row, "Dev-DB sollte unuebersetzte Bestandskarten haben")
        card_id = row[0]["card_id"]

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

    def test_collection_filter_escapes_wildcards(self):
        rows = ydb.list_collection(self.db, text="%")
        self.assertTrue(all("%" in (r["name"] + (r["name_de"] or ""))
                            for r in rows))


class CollectionEntryTests(CardIdsTestCase):
    def _entries(self, card_id):
        return self.query(
            "SELECT entry_id, quantity, set_code, edition FROM collection "
            "WHERE card_id = ? ORDER BY entry_id", (card_id,))

    def test_merge_is_null_safe(self):
        card = self.unowned_id
        first = ydb.add_to_collection(self.db, card, 1)
        second = ydb.add_to_collection(self.db, card, 2)
        self.assertEqual(first, second)
        self.assertEqual([tuple(r)[1:] for r in self._entries(card)], [(3, None, None)])

    def test_different_print_details_stay_separate(self):
        card = self.unowned_id
        ydb.add_to_collection(self.db, card, 1)
        ydb.add_to_collection(self.db, card, 1, edition="1st")
        ydb.add_to_collection(self.db, card, 1, set_code="X-1", edition="1st")
        ydb.add_to_collection(self.db, card, 1, language="DE")
        self.assertEqual(len(self._entries(card)), 4)

    def test_notes_do_not_split_prints(self):
        # Notizen sind kein Druck-Merkmal: gleicher Druck -> ein Eintrag.
        card = self.unowned_id
        ydb.add_to_collection(self.db, card, 1, notes="a")
        ydb.add_to_collection(self.db, card, 1, notes="b")
        self.assertEqual([r["quantity"] for r in self._entries(card)], [2])

    def test_set_quantity_and_remove(self):
        entry = ydb.add_to_collection(self.db, self.unowned_id, 1)
        ydb.set_collection_quantity(self.db, entry, 7)
        self.assertEqual(self._entries(self.unowned_id)[0]["quantity"], 7)
        ydb.set_collection_quantity(self.db, entry, 0)          # <= 0 loescht
        self.assertEqual(self._entries(self.unowned_id), [])
        entry = ydb.add_to_collection(self.db, self.unowned_id, 1)
        ydb.remove_collection_entry(self.db, entry)
        self.assertEqual(self._entries(self.unowned_id), [])


class CollectionStatsTests(CardIdsTestCase):
    def test_stats_of_dev_db(self):
        entries, unique, total = ydb.collection_stats(self.db)
        self.assertGreater(entries, 0, "Dev-DB braucht eine Sammlung")
        self.assertEqual(entries, self.count("collection"))
        self.assertEqual(unique, self.scalar("SELECT COUNT(DISTINCT card_id) FROM collection"))
        self.assertEqual(total, self.scalar("SELECT SUM(quantity) FROM collection"))

    def test_stats_follow_additions(self):
        before = ydb.collection_stats(self.db)
        ydb.add_to_collection(self.db, self.unowned_id, 3)
        ydb.add_to_collection(self.db, self.unowned_id, 1, set_code="Z")
        after = ydb.collection_stats(self.db)
        self.assertEqual(after, (before[0] + 2, before[1] + 1, before[2] + 4))

    def test_summary_stats_combine_both_queries(self):
        self.assertEqual(
            ydb.collection_summary_stats(self.db),
            (*ydb.collection_stats(self.db), ydb.collection_untranslated_count(self.db)),
        )

    def test_overview_aggregates_prints(self):
        ydb.add_to_collection(self.db, self.unowned_id, 1, set_code="A")
        ydb.add_to_collection(self.db, self.unowned_id, 2, set_code="B")
        rows = ydb.collection_overview(self.db)
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id[self.unowned_id]["total"], 3)
        self.assertEqual(by_id[self.unowned_id]["name"], self.display_name(self.unowned_id))
        self.assertEqual(len(rows), ydb.collection_stats(self.db)[1])
        names = [r["name"] for r in rows]
        self.assertEqual(names, sorted(names))


class CollectionFilterTests(CardIdsTestCase):
    def test_distinct_whitelist(self):
        with self.assertRaises(ValueError):
            ydb.collection_distinct(self.db, "name; DROP TABLE cards")
        for column in ("attribute", "archetype", "race", "type"):
            values = ydb.collection_distinct(self.db, column)
            expected = sorted(r[0] for r in self.query(
                f"SELECT DISTINCT c.{column} FROM collection col "
                f"JOIN cards c ON c.id = col.card_id WHERE c.{column} IS NOT NULL"))
            self.assertEqual(values, expected, column)

    def test_category_filter(self):
        for cat, word in (("spell", "Spell"), ("trap", "Trap"), ("monster", "Monster")):
            rows = ydb.list_collection(self.db, category=cat)
            self.assertTrue(rows, cat)
            self.assertTrue(all(ydb.card_category(r["type"]) == cat for r in rows), cat)
            self.assertTrue(all(word in r["type"] for r in rows), cat)
        total = sum(len(ydb.list_collection(self.db, category=c))
                    for c in ("monster", "spell", "trap", "other"))
        self.assertEqual(total, self.count("collection"))   # Klassen zerlegen den Bestand

    def test_attribute_and_archetype_filters(self):
        attr = ydb.collection_distinct(self.db, "attribute")[0]
        rows = ydb.list_collection(self.db, attribute=attr)
        self.assertTrue(rows and all(r["attribute"] == attr for r in rows))
        arch = ydb.collection_distinct(self.db, "archetype")[0]
        rows = ydb.list_collection(self.db, archetype=arch)
        self.assertTrue(rows and all(r["archetype"] == arch for r in rows))
        both = ydb.list_collection(self.db, attribute=attr, archetype=arch)
        self.assertTrue(all(r["attribute"] == attr and r["archetype"] == arch
                            for r in both))

    def test_text_filter_matches_german_and_english(self):
        row = self.query(
            "SELECT c.id, c.name, c.name_de FROM collection col "
            "JOIN cards c ON c.id = col.card_id "
            "WHERE c.name_de IS NOT NULL AND c.name_de != c.name "
            "AND length(c.name) > 8 AND length(c.name_de) > 8 LIMIT 1")[0]
        for needle in (row["name"][2:8], row["name_de"][2:8].upper()):
            ids = {r["card_id"] for r in ydb.list_collection(self.db, text=needle)}
            self.assertIn(row["id"], ids, needle)

    def test_blank_text_means_no_filter(self):
        self.assertEqual(len(ydb.list_collection(self.db, text="   ")), self.count("collection"))

    def test_sorted_by_display_name(self):
        rows = ydb.list_collection(self.db)
        names = [r["name_de"] or r["name"] for r in rows]
        self.assertEqual(names, sorted(names))


class PrintTests(CardIdsTestCase):
    def test_localize_set_code(self):
        loc = ydb.localize_set_code
        self.assertEqual(loc("RA01-EN008", "DE"), "RA01-DE008")
        self.assertEqual(loc("RA01-DE008", "EN"), "RA01-EN008")
        self.assertEqual(loc("L5DD-ENC09", "DE"), "L5DD-DEC09")
        self.assertEqual(loc("PSV-E088", "DE"), "PSV-G088")         # alter EU-Code
        self.assertEqual(loc("PSV-G088", "EN"), "PSV-E088")
        self.assertEqual(loc("PSV-088", "DE"), "PSV-088")           # ohne Region
        self.assertEqual(loc("RA01-EN008", None), "RA01-EN008")
        self.assertIsNone(loc(None, "DE"))

    def test_card_set_choices(self):
        choices = ydb.card_set_choices(self.db, 14558127)
        codes = [c["code"] for c in choices]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(len(codes), self.scalar(
            "SELECT COUNT(DISTINCT set_code) FROM card_sets WHERE card_id = ?", (14558127,)))
        self.assertIn("RA01-EN008", codes)
        names = [c["name"].casefold() for c in choices]
        self.assertEqual(names, sorted(names))
        self.assertFalse(any("&" in c["name"] and ";" in c["name"] for c in choices))
        tricky = self.scalar("SELECT card_id FROM card_sets WHERE set_name LIKE '%&apos;%' LIMIT 1")
        if tricky is not None:                                       # alte Kartendaten
            self.assertFalse(any("&apos;" in c["name"]
                                 for c in ydb.card_set_choices(self.db, tricky)))
        self.assertEqual(ydb.card_set_choices(self.db, 999_999_999), [])

    def test_update_print_and_merge(self):
        cid = self.unowned_id
        a = ydb.add_to_collection(self.db, cid, 2, notes="Binder")
        b = ydb.add_to_collection(self.db, cid, 1, set_code="RA01-DE008", language="DE",
                                  notes="Deck-Box")
        self.assertEqual(ydb.update_collection_print(self.db, a, "LOB-DE001", "DE"), a)
        row = self.query("SELECT set_code, language, quantity FROM collection "
                         "WHERE entry_id = ?", (a,))[0]
        self.assertEqual(tuple(row), ("LOB-DE001", "DE", 2))
        kept = ydb.update_collection_print(self.db, a, " RA01-DE008 ", "DE")
        self.assertEqual(kept, b)                                    # zusammengefuehrt
        self.assertIsNone(self.scalar("SELECT 1 FROM collection WHERE entry_id = ?", (a,)))
        row = self.query("SELECT quantity, notes FROM collection WHERE entry_id = ?", (b,))[0]
        self.assertEqual(tuple(row), (3, "Deck-Box; Binder"))
        self.assertEqual(ydb.update_collection_print(self.db, b, "", ""), b)
        self.assertEqual(tuple(self.query("SELECT set_code, language FROM collection "
                                          "WHERE entry_id = ?", (b,))[0]), (None, None))
        with self.assertRaises(ValueError):
            ydb.update_collection_print(self.db, 999_999, "X", None)


class PerCardTests(CardIdsTestCase):
    def test_list_collection_cards_groups_prints(self):
        cid = self.unowned_id
        ydb.add_to_collection(self.db, cid, 2, set_code="RA01-DE008", language="DE")
        ydb.add_to_collection(self.db, cid, 1, set_code="MP22-EN257", language="EN")
        cards = ydb.list_collection_cards(self.db)
        self.assertEqual(len(cards), self.scalar(
            "SELECT COUNT(DISTINCT card_id) FROM collection"))
        card = next(c for c in cards if c["card_id"] == cid)
        self.assertEqual((card["quantity"], len(card["prints"])), (3, 2))
        self.assertEqual(ydb.prints_summary(card["prints"]),
                         "2× RA01-DE008 (DE) · 1× MP22-EN257 (EN)")
        names = [(c["name_de"] or c["name"]).casefold() for c in cards]
        self.assertEqual(sum(card["quantity"] for card in cards),
                         ydb.collection_stats(self.db)[2])
        filtered = ydb.list_collection_cards(self.db, text=self.display_name(cid))
        self.assertIn(cid, [c["card_id"] for c in filtered])
        self.assertTrue(names)

    def test_prints_summary_and_label(self):
        single = ydb.list_collection(self.db)[:1]
        self.assertEqual(ydb.prints_summary(single), "")          # nur Menge, kein Set
        cid = self.unowned_id
        e = ydb.add_to_collection(self.db, cid, 1, set_code="LOB-001", edition="1st",
                                  condition="NM", language="EN")
        row = [r for r in ydb.list_collection(self.db) if r["entry_id"] == e]
        self.assertEqual(ydb.print_label(row[0]), "LOB-001 (EN, 1st, NM)")
        self.assertEqual(ydb.prints_summary(row), "1× LOB-001 (EN, 1st, NM)")

    def test_remove_collection_card(self):
        cid = self.unowned_id
        ydb.add_to_collection(self.db, cid, 1, set_code="A")
        ydb.add_to_collection(self.db, cid, 1, set_code="B")
        other = self.count("collection") - 2
        ydb.remove_collection_card(self.db, cid)
        self.assertEqual(self.count("collection", "card_id = ?", (cid,)), 0)
        self.assertEqual(self.count("collection"), other)


if __name__ == "__main__":
    unittest.main(verbosity=2)
