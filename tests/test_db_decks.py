"""
Datenschicht: Decks -- 3-Kopien-Regel, Zonen, Mengen, Verschieben,
Validierung, Bestands-Abgleich, Korpus-Decks, .ydk-Import/-Export.
Gegen eine Temp-Kopie der Dev-DB mit echten Karten.
"""

from __future__ import annotations

import unittest

import yugioh_db as ydb

from tests._support import CardIdsTestCase

UNKNOWN_ID = 999_999_999


class LegacyDeckTests(CardIdsTestCase):
    """Aus test_yugioh_db.DbTests uebernommen."""

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

    def test_deck_play_lists(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=3)
        ydb.add_card_to_deck(self.db, deck, self.main_id2, zone="main", count=2)
        ydb.add_card_to_deck(self.db, deck, self.extra_id, zone="extra", count=1)
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="side", count=1)
        play = ydb.deck_play_lists(self.db, deck)
        counts = ydb.deck_counts(self.db, deck)
        self.assertEqual(len(play["main"]), counts["main"])   # Side bleibt aussen vor
        self.assertEqual(len(play["extra"]), counts["extra"])
        self.assertEqual(play["main"].count(self.main_id), 3)
        self.assertEqual(play["extra"], [self.extra_id])
        self.assertIn(self.main_id, play["names"])
        self.assertEqual(set(play["info"]), set(play["names"]))
        info = play["info"][self.extra_id]
        self.assertEqual(info["name"], play["names"][self.extra_id])
        self.assertEqual(ydb.deck_zone_for(info["frame_type"], info["type"]), "extra")

    def test_link_markers_missing_counts_link_monsters_without_arrows(self):
        before = ydb.link_markers_missing(self.db)
        self.assertEqual(before, self.count("cards", "frame_type = 'link'"))
        self.set_real_link_markers()
        self.assertEqual(ydb.link_markers_missing(self.db), before - len(self.REAL_LINK_MARKERS))

    def test_play_info_link_markers(self):
        markers = self.set_real_link_markers()
        ids = list(markers) + [self.main_id, 999_999_999_999]
        info = ydb.card_play_info(self.db, ids)
        self.assertNotIn(999_999_999_999, info)            # unbekannt -> fehlt
        for cid, m in markers.items():
            self.assertEqual(info[cid]["link_markers"], tuple(m.split(",")))
            self.assertEqual(info[cid]["frame_type"], "link")
        self.assertIsNone(info[self.main_id]["link_markers"])
        self.assertEqual(ydb.card_play_info(self.db, []), {})
        deck = self.own_deck()
        play = ydb.deck_play_lists(self.db, deck)
        for cid in set(markers) & set(play["extra"]):
            self.assertEqual(play["info"][cid]["link_markers"], info[cid]["link_markers"])

    def test_deck_corpus_diff(self):
        # Eigenes Deck vs. Referenz-Liste, kopiengenau (Main+Extra).
        mine = ydb.create_deck(self.db, "Mein")
        ydb.add_card_to_deck(self.db, mine, self.main_id, zone="main", count=3)
        ydb.add_card_to_deck(self.db, mine, self.main_id2, zone="main", count=1)
        ydb.add_card_to_deck(self.db, mine, self.extra_id, zone="extra", count=1)
        ref = ydb.create_deck(self.db, "Ref")
        ydb.add_card_to_deck(self.db, ref, self.main_id, zone="main", count=1)
        ydb.add_card_to_deck(self.db, ref, self.extra_id, zone="extra", count=1)
        ydb.add_card_to_deck(self.db, ref, self.unowned_id, zone="main", count=2)

        d = ydb.deck_corpus_diff(self.db, mine, ref)
        self.assertEqual(d["ref_name"], "Ref")
        self.assertEqual(d["my_total"], 5)
        self.assertEqual(d["ref_total"], 4)
        self.assertEqual(d["ref_cards"], 3)
        self.assertEqual(d["shared_cards"], 2)  # main_id + extra_id in beiden
        # Referenz hat mehr: nur unowned_id (+2).
        self.assertEqual([(m["card_id"], m["diff"]) for m in d["missing"]],
                         [(self.unowned_id, 2)])
        # Du hast mehr: main_id (+2) vor main_id2 (+1), nach Differenz sortiert.
        self.assertEqual([(e["card_id"], e["diff"]) for e in d["extra"]],
                         [(self.main_id, 2), (self.main_id2, 1)])

    def test_deck_corpus_diff_unknown_ref_raises(self):
        mine = ydb.create_deck(self.db, "Mein")
        with self.assertRaises(ValueError):
            ydb.deck_corpus_diff(self.db, mine, 999999)

    # -- Reference-Decks binden keinen Bestand ----------------------------

    def test_reference_deck_does_not_bind_stock(self):
        text = "#main\n" + f"{self.main_id}\n" * 2 + "#extra\n!side\n"
        ydb.import_deck_ydk(self.db, "Meta", text, kind="reference")
        # Referenz-Deck taucht nicht in den eigenen Decks auf ...
        own_names = [d["name"] for d in ydb.list_decks(self.db)]
        self.assertNotIn("Meta", own_names)
        # ... und bindet keinen physischen Bestand.
        self.assertEqual(ydb.card_bound_in_decks(self.db, self.main_id), 0)


# ---------------------------------------------------------------------------
# Neu
# ---------------------------------------------------------------------------

class AddCardTests(CardIdsTestCase):
    def test_messages_for_rejected_adds(self):
        deck = ydb.create_deck(self.db, "T")
        self.assertEqual(ydb.add_card_to_deck(self.db, deck, UNKNOWN_ID),
                         (0, "Karte nicht gefunden."))
        self.assertEqual(ydb.add_card_to_deck(self.db, deck, self.main_id, zone="bogus"),
                         (0, "Unbekannte Zone."))
        self.assertEqual(ydb.add_card_to_deck(self.db, deck, self.main_id, zone="extra"),
                         (0, "Diese Karte gehört ins Main Deck."))

    def test_partial_and_full_cap_messages(self):
        deck = ydb.create_deck(self.db, "T")
        self.assertEqual(
            ydb.add_card_to_deck(self.db, deck, self.main_id, count=5),
            (3, "Nur 3 hinzugefügt (3-Kopien-Grenze)."),
        )
        self.assertEqual(
            ydb.add_card_to_deck(self.db, deck, self.main_id),
            (0, "Maximal 3 Kopien je Karte erreicht."),
        )

    def test_side_accepts_main_and_extra_cards(self):
        deck = ydb.create_deck(self.db, "T")
        self.assertEqual(ydb.add_card_to_deck(self.db, deck, self.main_id, zone="side"), (1, ""))
        self.assertEqual(ydb.add_card_to_deck(self.db, deck, self.extra_id, zone="side"), (1, ""))
        self.assertEqual(ydb.deck_counts(self.db, deck), {"main": 0, "extra": 0, "side": 2})

    def test_repeated_add_accumulates_in_one_row(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id)
        ydb.add_card_to_deck(self.db, deck, self.main_id)
        rows = ydb.deck_cards(self.db, deck, "main")
        self.assertEqual([(r["card_id"], r["quantity"]) for r in rows], [(self.main_id, 2)])


class QuantityAndMoveTests(CardIdsTestCase):
    def test_change_quantity_up_down_and_cap(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id)
        ydb.change_deck_quantity(self.db, deck, self.main_id, "main", +1)
        self.assertEqual(ydb.deck_counts(self.db, deck)["main"], 2)
        ydb.change_deck_quantity(self.db, deck, self.main_id, "main", +5)
        self.assertEqual(ydb.deck_counts(self.db, deck)["main"], 3)   # gekappt
        ydb.change_deck_quantity(self.db, deck, self.main_id, "main", -1)
        self.assertEqual(ydb.deck_counts(self.db, deck)["main"], 2)
        ydb.change_deck_quantity(self.db, deck, self.main_id, "main", -5)
        self.assertEqual(ydb.deck_cards(self.db, deck, "main"), [])  # Zeile weg

    def test_change_quantity_counts_other_zones(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=2)
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="side", count=1)
        ydb.change_deck_quantity(self.db, deck, self.main_id, "main", +1)
        self.assertEqual(ydb.deck_counts(self.db, deck), {"main": 2, "extra": 0, "side": 1})

    def test_change_quantity_without_row_is_noop(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id)
        ydb.change_deck_quantity(self.db, deck, self.main_id, "side", +1)
        self.assertEqual(ydb.deck_counts(self.db, deck), {"main": 1, "extra": 0, "side": 0})

    def test_move_between_main_and_side(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, count=3)
        self.assertEqual(ydb.move_deck_card(self.db, deck, self.main_id, "main", "side", 2), (2, ""))
        self.assertEqual(ydb.deck_counts(self.db, deck), {"main": 1, "extra": 0, "side": 2})
        # Mehr verschieben als da ist -> nur der Rest; Quellzeile verschwindet.
        self.assertEqual(ydb.move_deck_card(self.db, deck, self.main_id, "main", "side", 5), (1, ""))
        self.assertEqual(ydb.deck_cards(self.db, deck, "main"), [])
        self.assertEqual(ydb.move_deck_card(self.db, deck, self.main_id, "side", "main", 3), (3, ""))
        self.assertEqual(ydb.deck_counts(self.db, deck), {"main": 3, "extra": 0, "side": 0})

    def test_move_respects_card_type(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.extra_id)
        self.assertEqual(
            ydb.move_deck_card(self.db, deck, self.extra_id, "extra", "main"),
            (0, "Zielzone passt nicht zum Kartentyp."),
        )
        self.assertEqual(ydb.move_deck_card(self.db, deck, self.extra_id, "extra", "side"), (1, ""))
        self.assertEqual(ydb.move_deck_card(self.db, deck, self.extra_id, "side", "extra"), (1, ""))
        self.assertEqual(ydb.deck_counts(self.db, deck)["extra"], 1)

    def test_move_unknown_card_or_empty_source(self):
        deck = ydb.create_deck(self.db, "T")
        self.assertEqual(ydb.move_deck_card(self.db, deck, UNKNOWN_ID, "main", "side"),
                         (0, "Karte nicht gefunden."))
        self.assertEqual(ydb.move_deck_card(self.db, deck, self.main_id, "main", "side"), (0, ""))

    def test_remove_deck_card_only_touches_one_zone(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=2)
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="side", count=1)
        ydb.remove_deck_card(self.db, deck, self.main_id, "main")
        self.assertEqual(ydb.deck_counts(self.db, deck), {"main": 0, "extra": 0, "side": 1})


class DeckLifecycleTests(CardIdsTestCase):
    def test_delete_deck_cascades_cards_and_unlinks_combos(self):
        deck = ydb.create_deck(self.db, "Weg")
        ydb.add_card_to_deck(self.db, deck, self.main_id, count=2)
        combo = ydb.create_combo(self.db, "K", deck_id=deck)
        ydb.delete_deck(self.db, deck)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM deck_cards WHERE deck_id = ?", (deck,)), 0)
        self.assertNotIn(deck, [d["deck_id"] for d in ydb.list_decks(self.db)])
        # Heimat-Deck ist nur eine Verknuepfung: Kombo bleibt, Link wird NULL.
        self.assertIsNone(ydb.get_combo(self.db, combo)["deck_id"])

    def test_list_decks_only_own_and_sorted(self):
        ydb.create_deck(self.db, "AAA eigenes")
        ydb.create_deck(self.db, "AAA Referenz", kind="reference")
        names = [d["name"] for d in ydb.list_decks(self.db)]
        self.assertEqual(names, sorted(names))
        self.assertIn("AAA eigenes", names)
        self.assertNotIn("AAA Referenz", names)
        self.assertIn("RDA-Mitsu", names)

    def test_deck_cards_uses_german_name_and_sorts(self):
        deck = ydb.create_deck(self.db, "T")
        ids = [self.translated_card(), self.main_id, self.extra_id]
        for cid in ids:
            ydb.add_card_to_deck(self.db, deck, cid, zone="side")
        rows = ydb.deck_cards(self.db, deck, "side")
        self.assertEqual({r["card_id"]: r["name"] for r in rows},
                         {cid: self.display_name(cid) for cid in ids})
        names = [r["name"] for r in rows]
        self.assertEqual(names, sorted(names))

    def test_deck_counts_of_real_deck(self):
        deck = self.own_deck()
        expected = {"main": 0, "extra": 0, "side": 0}
        for r in self.query("SELECT zone, SUM(quantity) FROM deck_cards "
                            "WHERE deck_id = ? GROUP BY zone", (deck,)):
            expected[r[0]] = r[1]
        self.assertEqual(ydb.deck_counts(self.db, deck), expected)
        self.assertGreater(expected["main"], 0, "Dev-Deck braucht ein Main Deck")


class ValidateDeckTests(CardIdsTestCase):
    def test_empty_deck(self):
        deck = ydb.create_deck(self.db, "T")
        self.assertEqual(ydb.validate_deck(self.db, deck), [
            (False, "Main 0 (40-60)"),
            (True, "Extra 0 (max. 15)"),
            (True, "Side 0 (max. 15)"),
        ])

    def test_real_deck_checks_match_counts(self):
        deck = self.own_deck()
        c = ydb.deck_counts(self.db, deck)
        self.assertEqual(ydb.validate_deck(self.db, deck), [
            (40 <= c["main"] <= 60, f"Main {c['main']} (40-60)"),
            (c["extra"] <= 15, f"Extra {c['extra']} (max. 15)"),
            (c["side"] <= 15, f"Side {c['side']} (max. 15)"),
        ])                    # ueber die Datenschicht gebaut: nie > 3 Kopien

    def test_over_copies_and_zone_limits(self):
        # Die Datenschicht laesst so etwas nie zu -- aber alte/fremde Daten
        # koennen es enthalten; validate_deck muss es melden.
        deck = ydb.create_deck(self.db, "T")
        conn = ydb._connect(self.db)
        try:
            conn.execute("INSERT INTO deck_cards VALUES (?,?,?,?)",
                         (deck, self.main_id, "main", 4))
            conn.execute("INSERT INTO deck_cards VALUES (?,?,?,?)",
                         (deck, self.extra_id, "extra", 16))
            conn.commit()
        finally:
            conn.close()
        checks = ydb.validate_deck(self.db, deck)
        self.assertIn((False, "Extra 16 (max. 15)"), checks)
        self.assertIn((False, f"{self.display_name(self.main_id)}: 4 Kopien"), checks)
        self.assertIn((False, f"{self.display_name(self.extra_id)}: 16 Kopien"), checks)


class AvailabilityTests(CardIdsTestCase):
    def _avail(self, deck):
        return {a["card_id"]: a for a in ydb.deck_availability(self.db, deck)}

    def test_other_own_decks_bind_stock_reference_decks_do_not(self):
        card = self.unowned_id
        ydb.add_to_collection(self.db, card, 2)
        a = ydb.create_deck(self.db, "A")
        ydb.add_card_to_deck(self.db, a, card, count=3)
        self.assertEqual(self._avail(a)[card]["missing"], 1)   # 2 da, 3 gebraucht

        b = ydb.create_deck(self.db, "B")
        ydb.add_card_to_deck(self.db, b, card, count=2)
        info = self._avail(a)[card]
        self.assertEqual((info["owned"], info["elsewhere"], info["missing"]), (2, 2, 3))

        ref = ydb.create_deck(self.db, "Meta", kind="reference")
        ydb.add_card_to_deck(self.db, ref, card, count=3)
        self.assertEqual(self._avail(a)[card]["elsewhere"], 2)  # Referenz zaehlt nicht
        self.assertEqual(ydb.card_bound_in_decks(self.db, card), 5)

    def test_owned_card_is_not_missing(self):
        card = self.unowned_id
        ydb.add_to_collection(self.db, card, 3, set_code="A")
        ydb.add_to_collection(self.db, card, 1, set_code="B")   # Drucke summiert
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, card, count=3)
        info = self._avail(deck)[card]
        self.assertEqual((info["in_deck"], info["owned"], info["missing"]), (3, 4, 0))

    def test_availability_of_real_deck_is_consistent(self):
        for a in ydb.deck_availability(self.db, self.own_deck()):
            self.assertEqual(
                a["missing"],
                max(0, a["in_deck"] - max(0, a["owned"] - a["elsewhere"])),
            )
            self.assertEqual(a["name"], self.display_name(a["card_id"]))


class ReferenceDeckTests(CardIdsTestCase):
    def test_list_reference_decks_order_and_counts(self):
        newest = ydb.create_deck(self.db, "Neu", kind="reference",
                                 source="Regional", format_date="2099-01-01")
        ydb.add_card_to_deck(self.db, newest, self.main_id, count=3)
        before = self.n_refs()
        undated = ydb.create_deck(self.db, "Ohne Datum", kind="reference")
        refs = ydb.list_reference_decks(self.db)
        ids = [r["deck_id"] for r in refs]
        self.assertEqual(len(refs), before + 1)
        self.assertEqual(ids[0], newest)
        self.assertEqual((refs[0]["cards"], refs[0]["source"]), (3, "Regional"))
        # Neueste zuerst, Listen ohne Datum geschlossen am Ende.
        dates = [r["format_date"] for r in refs]
        dated = [d for d in dates if d]
        self.assertEqual(dates[:len(dated)], sorted(dated, reverse=True))
        self.assertTrue(all(d is None for d in dates[len(dated):]))
        self.assertIn(undated, ids[len(dated):])
        self.assertEqual(next(r for r in refs if r["deck_id"] == undated)["cards"], 0)
        self.assertNotIn(self.own_deck(), ids)

    def test_real_reference_lists_are_complete(self):
        refs = ydb.list_reference_decks(self.db)
        self.assertTrue(refs, "Dev-DB braucht Referenz-Decks")
        self.assertTrue(all(r["cards"] >= 40 for r in refs))
        for r in refs:
            self.assertEqual(r["cards"], self.scalar(
                "SELECT SUM(quantity) FROM deck_cards WHERE deck_id = ?", (r["deck_id"],)))


class YdkTests(CardIdsTestCase):
    def test_import_moves_wrong_zones_and_reports(self):
        self.assertIsNone(self.scalar("SELECT id FROM cards WHERE id = ?", (UNKNOWN_ID,)))
        extra2 = self.extra_ids(2)[1]
        text = (f"#main\n{self.extra_id}\n{UNKNOWN_ID}\n#extra\n{self.main_id}\n"
                f"!side\n{extra2}\nkaputt\n")
        deck, report = ydb.import_deck_ydk(self.db, "Mix", text)
        self.assertIsNotNone(deck)
        self.assertEqual(report["imported"], {"main": 1, "extra": 1, "side": 1})
        self.assertEqual(report["unknown"], [UNKNOWN_ID])
        self.assertEqual(sorted(report["moved"]),
                         sorted([self.display_name(self.extra_id),
                                 self.display_name(self.main_id)]))
        self.assertEqual(report["unreadable"], ["kaputt"])
        self.assertEqual(report["capped"], [])
        self.assertEqual(ydb.deck_cards(self.db, deck, "extra")[0]["card_id"], self.extra_id)

    def test_import_without_known_cards_creates_nothing(self):
        before = self.scalar("SELECT COUNT(*) FROM decks")
        deck, report = ydb.import_deck_ydk(self.db, "Leer", f"#main\n{UNKNOWN_ID}\n")
        self.assertIsNone(deck)
        self.assertEqual(report["unknown"], [UNKNOWN_ID])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM decks"), before)

    def test_import_caps_across_zones(self):
        text = f"#main\n{self.main_id}\n{self.main_id}\n!side\n{self.main_id}\n{self.main_id}\n"
        deck, report = ydb.import_deck_ydk(self.db, "Cap", text)
        self.assertEqual(ydb.deck_counts(self.db, deck), {"main": 2, "extra": 0, "side": 1})
        self.assertEqual(report["capped"], [self.display_name(self.main_id)])

    def test_import_reference_with_metadata(self):
        text = "#main\n" + f"{self.main_id}\n" * 3
        deck, _ = ydb.import_deck_ydk(self.db, "Meta", text, kind="reference",
                                      source="YCS", format_date="2099-02-03")
        ref = next(r for r in ydb.list_reference_decks(self.db) if r["deck_id"] == deck)
        self.assertEqual((ref["source"], ref["format_date"], ref["cards"]),
                         ("YCS", "2099-02-03", 3))
        self.assertNotIn(deck, [d["deck_id"] for d in ydb.list_decks(self.db)])

    def test_export_format_and_order(self):
        deck = self.own_deck()
        text = ydb.export_deck_ydk(self.db, deck)
        self.assertTrue(text.startswith("#created by YugiohSammlung - RDA-Mitsu\n#main\n"))
        self.assertTrue(text.endswith("\n"))
        zones = ydb.parse_ydk(text)
        expected_main = [r["card_id"] for r in ydb.deck_cards(self.db, deck, "main")
                         for _ in range(r["quantity"])]
        self.assertEqual(zones["main"], expected_main)  # nach DE-Namen sortiert
        counts = ydb.deck_counts(self.db, deck)
        self.assertEqual((len(zones["extra"]), len(zones["side"])),
                         (counts["extra"], counts["side"]))

    def test_export_unknown_deck_raises(self):
        with self.assertRaises(ValueError):
            ydb.export_deck_ydk(self.db, 999_999)

    def test_roundtrip_of_real_deck_is_exact(self):
        src = self.own_deck()
        new, report = ydb.import_deck_ydk(self.db, "Kopie", ydb.export_deck_ydk(self.db, src))
        self.assertEqual((report["unknown"], report["capped"], report["moved"]), ([], [], []))

        def content(deck):
            return sorted(tuple(r) for r in self.query(
                "SELECT card_id, zone, quantity FROM deck_cards WHERE deck_id = ?", (deck,)))
        self.assertEqual(content(new), content(src))


class CorpusDiffRealDataTests(CardIdsTestCase):
    def test_diff_against_real_reference_list_is_consistent(self):
        mine = self.own_deck()
        ref = self.scalar("SELECT deck_id FROM decks WHERE kind = 'reference' "
                          "ORDER BY deck_id LIMIT 1")
        d = ydb.deck_corpus_diff(self.db, mine, ref)
        self.assertEqual(
            sum(m["diff"] for m in d["missing"]) - sum(e["diff"] for e in d["extra"]),
            d["ref_total"] - d["my_total"],
        )
        counts = ydb.deck_counts(self.db, mine)
        self.assertEqual(d["my_total"], counts["main"] + counts["extra"])   # ohne Side
        diffs = [m["diff"] for m in d["missing"]]
        self.assertEqual(diffs, sorted(diffs, reverse=True))
        self.assertLessEqual(d["shared_cards"], d["ref_cards"])

    def test_diff_with_itself_is_empty(self):
        mine = self.own_deck()
        d = ydb.deck_corpus_diff(self.db, mine, mine)
        self.assertEqual((d["missing"], d["extra"]), ([], []))
        self.assertEqual(d["shared_cards"], d["ref_cards"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
