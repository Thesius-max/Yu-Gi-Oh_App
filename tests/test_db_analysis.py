"""
Datenschicht: Konsistenz-Mathematik je Deck, Starthand-Simulator,
Was-waere-wenn, Linien-Startbarkeit, Synergie-/Korpus-Graph, Vorschlaege.
Korpus-Tests laufen gegen die 14 echten RDA-Referenzlisten der Dev-DB.
"""

from __future__ import annotations

import datetime
import math
import random
import unittest
from types import SimpleNamespace
from unittest import mock

import yugioh_db as ydb
from yugioh_db import analysis

from tests._support import CardIdsTestCase


class LegacyAnalysisTests(CardIdsTestCase):
    """Aus test_yugioh_db.DbTests uebernommen."""

    def _fresh_main_ids(self, n: int) -> list[int]:
        """Main-taugliche Karten, die in KEINER Kombo stecken -- so bleiben
        die Rollen-Kopien des Testdecks exakt vorhersagbar."""
        return self.main_ids(n)

    def _real_ids(self, where: str, n: int) -> list[int]:
        return [r[0] for r in self.query(
            f"SELECT id FROM cards WHERE {where} LIMIT ?", (n,))]

    @staticmethod
    def _corpus(s):
        return analysis._CORPUS_WEIGHT * sum(l["weight"] for l in s["corpus"])

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

    def test_deck_what_if(self):
        starter_id, blank_id = self._fresh_main_ids(2)
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, starter_id, zone="main", count=3)
        ydb.add_card_to_deck(self.db, deck, blank_id, zone="main", count=2)
        combo = ydb.create_combo(self.db, "K")
        ydb.add_combo_card(self.db, combo, starter_id, 1)
        ydb.set_combo_card_role(self.db, combo, starter_id, "starter")

        res = ydb.deck_what_if(self.db, deck, hand_sizes=(2,))
        self.assertEqual(res["deck_size"], 5)
        # Basis: P(>=1 von 3 Startern in 2 aus 5) = 1 - C(2,2)/C(5,2) = 0.9
        self.assertAlmostEqual(res["base"][2]["starter"], 0.9)
        cards = {c["card_id"]: c for c in res["cards"]}
        # Starter am 3-Kopien-Limit: kein '+1'-Szenario.
        self.assertIsNone(cards[starter_id]["plus"])
        # -1 Starter: N=4, 2 Starter -> 1 - C(2,2)/C(4,2) = 1 - 1/6
        self.assertAlmostEqual(
            cards[starter_id]["minus"][2]["starter"], 1 - 1 / 6)
        # +1 rollenlose Karte verduennt: N=6, 3 Starter ->
        # 1 - C(3,2)/C(6,2) = 0.8
        self.assertAlmostEqual(cards[blank_id]["plus"][2]["starter"], 0.8)
        # Handtraps gibt es im Testdeck keine.
        self.assertAlmostEqual(res["base"][2]["handtrap"], 0.0)

    def test_deck_line_playability(self):
        starter_id, payoff_id = self._fresh_main_ids(2)
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, starter_id, zone="main", count=2)
        ydb.add_card_to_deck(self.db, deck, payoff_id, zone="main", count=1)

        k1 = ydb.create_combo(self.db, "K1")
        ydb.add_combo_card(self.db, k1, starter_id, 1)
        ydb.set_combo_card_role(self.db, k1, starter_id, "starter")
        ydb.add_combo_card(self.db, k1, payoff_id, 1)
        ydb.set_combo_card_role(self.db, k1, payoff_id, "payoff")
        k2 = ydb.create_combo(self.db, "K2")
        ydb.add_combo_card(self.db, k2, payoff_id, 1)
        variant = ydb.create_combo(self.db, "Var")
        ydb.set_combo_parent(self.db, variant, k1)
        ydb.add_combo_card(self.db, variant, starter_id, 1)
        ydb.set_combo_card_role(self.db, variant, starter_id, "starter")

        res = ydb.deck_line_playability(self.db, deck, hand_sizes=(1,))
        # K1: 1 Starter-Baustein, 2 Main-Kopien, P(1 aus 3 trifft) = 2/3.
        self.assertEqual(res[k1]["starters"], 1)
        self.assertEqual(res[k1]["copies"], 2)
        self.assertAlmostEqual(res[k1]["hands"][1], 2 / 3)
        # K2: kein Baustein als Starter eingestuft.
        self.assertEqual(res[k2]["starters"], 0)
        self.assertAlmostEqual(res[k2]["hands"][1], 0.0)
        # Varianten (Branches) bekommen keinen eigenen Eintrag.
        self.assertNotIn(variant, res)

    def test_gap_role_counts_extra_deck_payoffs(self):
        deck = ydb.create_deck(self.db, "T")
        starter = self.main_id
        ydb.add_card_to_deck(self.db, deck, starter, count=3)
        ydb.add_card_to_deck(self.db, deck, self.extra_id, count=2)
        combo = ydb.create_combo(self.db, "K")
        for cid, role in ((starter, "starter"), (self.extra_id, "payoff")):
            ydb.add_combo_card(self.db, combo, cid, 1)
            ydb.set_combo_card_role(self.db, combo, cid, role)
        res = ydb.deck_suggestions(self.db, deck)
        self.assertEqual(res["role_copies"]["payoff"], 2)
        self.assertNotEqual(res["gap_role"], "payoff")

    def test_gap_role_none_without_roles(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, count=1)
        self.assertIsNone(ydb.deck_suggestions(self.db, deck)["gap_role"])

    def test_combo_siblings_are_not_bridges(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, count=1)
        others = self._real_ids(
            f"frame_type = 'effect' AND id != {self.main_id} AND id NOT IN "
            "(SELECT card_id FROM combo_cards)", 4)
        combo = ydb.create_combo(self.db, "K")
        for cid in [self.main_id, *others]:
            ydb.add_combo_card(self.db, combo, cid, 1)
        scores = {
            s["card_id"]: s for s in ydb.deck_suggestions(self.db, deck, limit=500)[
                "suggestions"] if s["card_id"] in others
        }
        for cid in others:
            self.assertEqual(scores[cid]["bridges"], [])
            self.assertAlmostEqual(scores[cid]["score"] - self._corpus(scores[cid]), 1.0)

    def test_what_if_plus_respects_side_copies(self):
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="main", count=2)
        ydb.add_card_to_deck(self.db, deck, self.main_id, zone="side", count=1)
        card = next(c for c in ydb.deck_what_if(self.db, deck)["cards"]
                    if c["card_id"] == self.main_id)
        self.assertIsNone(card["plus"])


# ---------------------------------------------------------------------------
# Neu
# ---------------------------------------------------------------------------

class RoleCopiesTests(CardIdsTestCase):
    def _deck(self):
        a, b, blank = self.main_ids(3)
        e = self.extra_ids(1)[0]
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, a, count=3)
        ydb.add_card_to_deck(self.db, deck, b, count=2)
        ydb.add_card_to_deck(self.db, deck, blank, count=3)
        ydb.add_card_to_deck(self.db, deck, e, count=1)
        self.seed_combo("K1", {a: "starter", b: "starter", e: "payoff"})
        self.seed_combo("K2", {a: "starter", b: "extender"})
        var = self.seed_combo("Var", {a: "handtrap"})
        ydb.set_combo_parent(self.db, var, self.seed_combo("Haupt", {}))
        return deck, (a, b, blank, e)

    def test_role_copies_main_only_once_per_role(self):
        deck, _ = self._deck()
        self.assertEqual(ydb.deck_role_copies(self.db, deck),
                         {"starter": 5, "extender": 2})

    def test_consistency_matches_hypergeometry(self):
        deck, _ = self._deck()
        stats = ydb.deck_consistency(self.db, deck, hand_sizes=(5, 6))
        self.assertEqual(stats["deck_size"], 8)
        for hand in (5, 6):
            p = stats["hands"][hand]
            expected = ydb.hypergeom_at_least(8, 5, hand)
            self.assertAlmostEqual(p["starter"], expected)
            self.assertAlmostEqual(p["brick"], 1 - expected)
            self.assertEqual(p["handtrap"], 0.0)

    def test_consistency_of_real_deck_without_roles(self):
        deck = self.own_deck()
        stats = ydb.deck_consistency(self.db, deck)
        self.assertEqual(stats["deck_size"], ydb.deck_counts(self.db, deck)["main"])
        self.assertEqual(stats["roles"], {})
        self.assertEqual(stats["hands"][5]["brick"], 1.0)

    def test_consistency_of_real_deck_with_seeded_roles(self):
        deck = self.own_deck()
        rows = ydb.deck_cards(self.db, deck, "main")
        starters = {r["card_id"]: "starter" for r in rows[:3]}
        traps = {r["card_id"]: "handtrap" for r in rows[3:5]}
        self.seed_combo("Linie", {**starters, **traps})
        copies = lambda ids: sum(r["quantity"] for r in rows if r["card_id"] in ids)
        stats = ydb.deck_consistency(self.db, deck)
        self.assertEqual(stats["roles"], {"starter": copies(starters),
                                          "handtrap": copies(traps)})
        self.assertAlmostEqual(stats["hands"][6]["handtrap"],
                               ydb.hypergeom_at_least(stats["deck_size"], copies(traps), 6))

    def test_main_cards_sorted_and_roles_sorted(self):
        deck, (a, b, *_rest) = self._deck()
        cards = ydb.deck_main_cards(self.db, deck)
        names = [c["name"] for c in cards]
        self.assertEqual(names, sorted(names))
        roles = {c["card_id"]: c["roles"] for c in cards}
        self.assertEqual(roles[a], ["starter"])
        self.assertEqual(roles[b], ["extender", "starter"])
        self.assertEqual(sum(c["copies"] for c in cards), 8)   # kein Extra

    def test_line_playability_matches_hypergeometry(self):
        deck, (a, b, *_rest) = self._deck()
        res = ydb.deck_line_playability(self.db, deck)
        k1 = self.scalar("SELECT combo_id FROM combos WHERE name = 'K1'")
        self.assertEqual((res[k1]["starters"], res[k1]["copies"]), (2, 5))
        for hand in (5, 6):
            self.assertAlmostEqual(res[k1]["hands"][hand],
                                   ydb.hypergeom_at_least(8, 5, hand))

    def test_what_if_minus_and_plus_directions(self):
        deck, (a, b, blank, _e) = self._deck()
        # Hand 2: bei 8 Karten mit nur 3 Nieten waere Hand 5 immer 100 %.
        res = ydb.deck_what_if(self.db, deck, hand_sizes=(2,))
        cards = {c["card_id"]: c for c in res["cards"]}
        base = res["base"][2]["starter"]
        self.assertAlmostEqual(base, 1 - 3 / 28)
        self.assertLess(cards[b]["minus"][2]["starter"], base)   # Starter weg
        self.assertGreater(cards[b]["plus"][2]["starter"], base)  # Starter dazu
        self.assertGreater(cards[blank]["minus"][2]["starter"], base)  # verdichtet
        self.assertIsNone(cards[a]["plus"])                      # 3/3


class SampleHandTests(CardIdsTestCase):
    def test_degenerate_hand_sizes(self):
        deck = self.own_deck()
        self.assertEqual(ydb.draw_sample_hand(self.db, deck, 0, rng=random.Random(1)), [])
        self.assertEqual(ydb.draw_sample_hand(self.db, deck, -3, rng=random.Random(1)), [])
        empty = ydb.create_deck(self.db, "Leer")
        self.assertEqual(ydb.draw_sample_hand(self.db, empty, 5), [])

    def test_hand_from_real_deck_respects_copies(self):
        deck = self.own_deck()
        copies = {c["card_id"]: c["copies"] for c in ydb.deck_main_cards(self.db, deck)}
        for seed in range(20):
            hand = ydb.draw_sample_hand(self.db, deck, 6, rng=random.Random(seed))
            self.assertEqual(len(hand), 6)
            for cid in {c["card_id"] for c in hand}:
                self.assertIn(cid, copies)
                self.assertLessEqual(sum(c["card_id"] == cid for c in hand), copies[cid])


class SynergyEdgeTests(CardIdsTestCase):
    def test_weights_count_shared_main_lines(self):
        a, b, c = self.main_ids(3)
        k1 = self.seed_combo("K1", {a: None, b: None, c: None})
        k2 = self.seed_combo("K2", {a: None, b: None})
        edges = ydb.synergy_edges(self.db)
        ab = tuple(sorted((a, b)))
        self.assertEqual(edges[ab], {"weight": 2, "combos": [k1, k2]})
        self.assertEqual(edges[tuple(sorted((a, c)))]["weight"], 1)
        self.assertEqual(len(edges), 3)
        self.assertTrue(all(x < y for x, y in edges))

    def test_empty_without_combos(self):
        self.assertEqual(ydb.synergy_edges(self.db), {})


class _FixedDate(datetime.date):
    """'Heute' fest -- die Alterungs-Gewichte haengen sonst vom Kalender ab
    (und damit auch Float-Rundungen im PPMI)."""

    @classmethod
    def today(cls):
        return cls(2026, 9, 24)


class CorpusEdgeTests(CardIdsTestCase):
    """Gegen die echten RDA-Referenzlisten der Dev-DB. Der Stand wird in der
    Kopie vereinheitlicht und 'heute' festgehalten -- dann kuerzen sich die
    Alterungs-Gewichte und PPMI ist aus reinen Zaehlungen nachrechenbar."""

    def setUp(self):
        super().setUp()
        self.assertGreaterEqual(self.n_refs(), 3, "Dev-DB braucht Referenz-Decks")
        self._set_ref_dates("2026-06-11")
        patch = mock.patch.object(analysis, "datetime", SimpleNamespace(date=_FixedDate))
        patch.start()
        self.addCleanup(patch.stop)

    def _set_ref_dates(self, date):
        conn = ydb._connect(self.db)
        try:
            conn.execute("UPDATE decks SET format_date = ? WHERE kind = 'reference'", (date,))
            conn.commit()
        finally:
            conn.close()

    def _presence(self):
        rows = self.query(
            "SELECT DISTINCT dc.deck_id, dc.card_id FROM deck_cards dc "
            "JOIN decks d ON d.deck_id = dc.deck_id "
            "WHERE d.kind = 'reference' AND dc.zone IN ('main','extra')")
        per_card: dict[int, set[int]] = {}
        for r in rows:
            per_card.setdefault(r["card_id"], set()).add(r["deck_id"])
        return per_card

    def test_edges_have_support_and_sorted_keys(self):
        edges = ydb.corpus_edges(self.db)
        self.assertTrue(edges)
        for (a, b), e in edges.items():
            self.assertLess(a, b)
            self.assertGreaterEqual(e["decks"], analysis._CORPUS_MIN_DECKS)
            self.assertEqual(e["total"], self.n_refs())
            self.assertGreater(e["weight"], 0)

    def test_staples_in_every_list_have_no_edges(self):
        # Eine echte Karte in jede Liste legen -> garantiert ein Staple.
        staple = self.unowned_ids(1)[0]
        for r in ydb.list_reference_decks(self.db):
            ydb.add_card_to_deck(self.db, r["deck_id"], staple)
        per_card = self._presence()
        n = self.n_refs()
        staples = {c for c, decks in per_card.items() if len(decks) == n}
        self.assertIn(staple, staples)
        edges = ydb.corpus_edges(self.db)
        self.assertTrue(edges)
        self.assertFalse({c for pair in edges for c in pair} & staples,
                         f"Staples mit Kante bei {n} Listen -- vermutlich der Float-"
                         "Befund, siehe test_staple_ppmi_is_exactly_zero_for_15_lists")

    def test_ppmi_matches_counts_for_equal_weights(self):
        # Alle Listen haben denselben Stand -> Gewichte kuerzen sich heraus.
        per_card = self._presence()
        n = self.n_refs()
        for (a, b), e in list(ydb.corpus_edges(self.db).items())[:25]:
            n_ab = len(per_card[a] & per_card[b])
            self.assertEqual(e["decks"], n_ab)
            expected = math.log(n_ab * n / (len(per_card[a]) * len(per_card[b])))
            self.assertAlmostEqual(e["weight"], expected, places=9)

    @unittest.expectedFailure
    def test_staple_ppmi_is_exactly_zero_for_15_lists(self):
        """BEFUND (2026-09-24): Bei 15 gleich alten Listen weichen
        sum(weights) und die Aufsummierung je Karte im letzten Bit ab.
        Staples bekommen dann PPMI ~2.2e-16 statt 0 -- es entstehen
        Schein-Kanten, und der Staple taucht als Vorschlag mit 'Score 0,0'
        auf. Moegliche Korrektur: 'if ppmi > 1e-9' statt 'if ppmi > 0' in
        corpus_edges (bzw. total_w aus derselben Summation bilden)."""
        for r in ydb.list_reference_decks(self.db):
            ydb.delete_deck(self.db, r["deck_id"])
        staple, *others = self.main_ids(6)
        for i in range(15):                          # 15 echte Listen, gleicher Stand
            deck = ydb.create_deck(self.db, f"Liste {i}", kind="reference",
                                   format_date="2026-06-11")
            for cid in [staple] + others[: 2 + i % 4]:
                ydb.add_card_to_deck(self.db, deck, cid)
        edges = ydb.corpus_edges(self.db)
        self.assertTrue(edges)                       # die anderen Karten verbinden sich
        self.assertEqual([k for k in edges if staple in k], [])

    def test_no_reference_decks_no_edges(self):
        for r in ydb.list_reference_decks(self.db):
            ydb.delete_deck(self.db, r["deck_id"])
        self.assertEqual(ydb.corpus_edges(self.db), {})

    def test_unreadable_date_is_treated_as_today(self):
        conn = ydb._connect(self.db)
        try:
            conn.execute("UPDATE decks SET format_date = 'kaputt' WHERE kind = 'reference'")
            conn.commit()
        finally:
            conn.close()
        self.assertTrue(ydb.corpus_edges(self.db))

    def test_own_decks_do_not_feed_the_corpus(self):
        before = ydb.corpus_edges(self.db)
        ydb.import_deck_ydk(self.db, "Eigene Kopie",
                            ydb.export_deck_ydk(self.db, self.own_deck()))
        self.assertEqual(ydb.corpus_edges(self.db), before)


class CorpusSuggestionTests(CardIdsTestCase):
    def test_real_deck_gets_explained_corpus_suggestions(self):
        deck = self.own_deck()
        in_deck = {r["card_id"] for r in self.query(
            "SELECT card_id FROM deck_cards WHERE deck_id = ?", (deck,))}
        res = ydb.deck_suggestions(self.db, deck, limit=10)
        sugs = res["suggestions"]
        self.assertTrue(sugs, "Korpus sollte Vorschlaege fuer RDA-Mitsu liefern")
        self.assertLessEqual(len(sugs), 10)
        self.assertIsNone(res["gap_role"])              # keine Rollen erfasst
        scores = [s["score"] for s in sugs]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for s in sugs:
            self.assertNotIn(s["card_id"], in_deck)
            self.assertEqual((s["direct"], s["bridges"], s["reasons"]), (0, [], []))
            self.assertTrue(0 < len(s["corpus"]) <= analysis._CORPUS_TOP_LINKS)
            self.assertTrue(all(l["card_id"] in in_deck for l in s["corpus"]))
            self.assertAlmostEqual(
                s["score"],
                analysis._CORPUS_WEIGHT * sum(l["weight"] for l in s["corpus"]))
            self.assertEqual(s["corpus_total"], self.n_refs())
            self.assertEqual(s["name"], self.display_name(s["card_id"]))

    def test_combo_evidence_outranks_corpus(self):
        deck = self.own_deck()
        member = ydb.deck_cards(self.db, deck, "main")[0]["card_id"]
        candidate = self.unowned_ids(1)[0]
        self.seed_combo("Linie", {member: "starter", candidate: "extender"})
        res = ydb.deck_suggestions(self.db, deck, limit=500)
        top = {s["card_id"]: s for s in res["suggestions"]}[candidate]
        self.assertEqual(top["direct"], 1)
        self.assertEqual(top["roles"], ["extender"])
        self.assertEqual(top["reasons"][0]["combo_name"], "Linie")
        self.assertEqual(top["reasons"][0]["with"], [self.display_name(member)])
        # Starter vorhanden; erste Rolle mit 0 Kopien (COMBO_ROLES-Reihenfolge).
        self.assertEqual(res["gap_role"], "extender")


if __name__ == "__main__":
    unittest.main(verbosity=2)
