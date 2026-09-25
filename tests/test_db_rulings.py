"""
Datenschicht: eigene Rulings je Karte (yugioh_db.rulings) -- CRUD, Zaehler,
Ueberleben eines Daten-Updates, Links zu offiziellen Quellen und der
KI-Markdown-Export. Gegen eine Temp-Kopie der Dev-DB (echte Karten).
"""

from __future__ import annotations

import unittest
import urllib.parse

import yugioh_db as ydb

from tests._support import CardIdsTestCase, api_payload_from_db
from tests.test_db_api_updates import fake_api


class RulingTests(CardIdsTestCase):
    def test_crud_and_counts(self):
        rid = ydb.add_card_ruling(self.db, self.main_id, "  Erster Text  ", " Konami-FAQ ")
        ydb.add_card_ruling(self.db, self.main_id, "Zweiter")
        rows = ydb.list_card_rulings(self.db, self.main_id)
        self.assertEqual([(r["text"], r["source"]) for r in rows],
                         [("Erster Text", "Konami-FAQ"), ("Zweiter", None)])
        self.assertTrue(rows[0]["created"])
        ydb.update_card_ruling(self.db, rid, "Geändert", "")
        self.assertEqual(ydb.list_card_rulings(self.db, self.main_id)[0]["source"], None)
        self.assertEqual(ydb.card_ruling_counts(self.db, [self.main_id, self.main_id2]),
                         {self.main_id: 2})
        ydb.delete_card_ruling(self.db, rid)
        self.assertEqual(ydb.card_ruling_counts(self.db, [self.main_id]), {self.main_id: 1})
        self.assertEqual(ydb.card_ruling_counts(self.db, []), {})

    def test_empty_text_and_unknown_card_are_rejected(self):
        with self.assertRaises(ValueError):
            ydb.add_card_ruling(self.db, self.main_id, "   ")
        with self.assertRaises(ValueError):
            ydb.add_card_ruling(self.db, 999_999_999_999, "Text")
        rid = ydb.add_card_ruling(self.db, self.main_id, "Text")
        with self.assertRaises(ValueError):
            ydb.update_card_ruling(self.db, rid, "")

    def test_rulings_survive_a_data_update(self):
        ydb.add_card_ruling(self.db, self.main_id, "Bleibt", "Quelle")
        cards, de = api_payload_from_db(self.db)
        with fake_api(cards, de):
            ydb.build_database(self.db)
        self.assertEqual([r["text"] for r in ydb.list_card_rulings(self.db, self.main_id)],
                         ["Bleibt"])

    def test_links_are_encoded(self):
        links = ydb.ruling_links("Ash Blossom & Joyous Spring", "Aschenblüte & Freudiger Frühling")
        self.assertEqual(len(links), 3)
        (_l1, de), (_l2, en), (_l3, wiki) = links
        for url, keyword, locale in ((de, "Aschenblüte & Freudiger Frühling", "de"),
                                     (en, "Ash Blossom & Joyous Spring", "en")):
            self.assertTrue(url.startswith("https://www.db.yugioh-card.com/"))
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            self.assertEqual((query["keyword"], query["request_locale"], query["stype"]),
                             ([keyword], [locale], ["1"]))
        self.assertEqual(wiki, "https://yugipedia.com/wiki/"
                               "Card_Rulings:Ash_Blossom_%26_Joyous_Spring")
        # Ohne deutschen Namen keine deutsche Suche (sie faende nichts).
        self.assertEqual([lbl for lbl, _u in ydb.ruling_links("X", None)][0],
                         "Konami-Datenbank (englisch, mit FAQ)")
        self.assertIn("%22", ydb.ruling_links('"A" Cell')[-1][1])

    def test_markdown_export_lists_rulings(self):
        deck = self.own_deck()
        cid = ydb.deck_cards(self.db, deck, "main")[0]["card_id"]
        ydb.add_card_ruling(self.db, cid, "Zeile 1\nZeile 2", "FAQ")
        md = ydb.export_deck_markdown(self.db, deck)
        self.assertIn("- Ruling: Zeile 1\n  Zeile 2 (Quelle: FAQ)", md)


if __name__ == "__main__":
    unittest.main()
