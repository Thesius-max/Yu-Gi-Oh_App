"""
Reine Funktionen der Datenschicht -- kein DB-Zugriff, kein Qt, laufen immer
(auch ohne Dev-DB).
"""

from __future__ import annotations

import itertools
import math
import unittest

import yugioh_db as ydb
from yugioh_db.exports import (
    _card_meta_line, _export_filter_note, _md_fence, _md_inline
)
from yugioh_db.schema import _casefold, _like_contains
from yugioh_db.updates import _parse_version


class PureFunctionTests(unittest.TestCase):
    """Aus test_yugioh_db.py uebernommen."""

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

    def test_parse_ydk_bom_and_unreadable_lines(self):
        bad: list[str] = []
        text = "﻿100\r\n²\r\n０１２\r\n5 -- Name\r\n"
        zones = ydb.parse_ydk(text, bad)
        self.assertEqual(zones["main"], [100])  # BOM-Zeile gelesen
        self.assertEqual(bad, ["²", "０１２", "5 -- Name"])

    def test_like_contains_escapes_wildcards(self):
        self.assertEqual(_like_contains(r" 50%_a\ "), r"%50\%\_a\\%")
        self.assertEqual(_like_contains("ÜBER"), "%über%")

    def test_markdown_escaping(self):
        self.assertEqual(_md_inline("Maliss <P> `x`"), r"Maliss \<P\> \`x\`")
        self.assertEqual(_md_fence("ohne"), "```")
        self.assertEqual(_md_fence("a ``` b ```` c"), "`````")

    def test_lint_combo_steps(self):
        self.assertEqual(ydb.lint_combo_steps(["NS Karte (Hand)"]), [])
        warnings = ydb.lint_combo_steps(["irgendwas ohne keyword"])
        self.assertTrue(warnings)
        self.assertIn("Schritt 1", warnings[0])

    def test_lint_formula_keywords(self):
        ok = ["Xyz: A (4) + B (4) -> C (R4)", "Link: A + B -> C (L2)",
              "Fusion: A + B -> C"]
        self.assertEqual(ydb.lint_combo_steps(ok), [])
        self.assertTrue(ydb.lint_combo_steps(["Synchro Soul + Bone"]))
        self.assertTrue(ydb.lint_combo_steps(["NS X -> Xyz: A + B -> C"]))


# ---------------------------------------------------------------------------
# Neu: Randfaelle und Eigenschaften
# ---------------------------------------------------------------------------

class ClassificationTests(unittest.TestCase):
    def test_all_extra_frames_including_pendulum_variants(self):
        for ft in ydb.EXTRA_DECK_FRAMES:
            self.assertEqual(ydb.deck_zone_for(ft), "extra", ft)
        # Frame-Vergleich ist unabhaengig von Gross-/Kleinschreibung.
        self.assertEqual(ydb.deck_zone_for("XYZ"), "extra")

    def test_main_frames_stay_main(self):
        for ft in ("effect_pendulum", "normal_pendulum", "ritual",
                   "ritual_pendulum", "spell", "trap", "token", "skill"):
            self.assertEqual(ydb.deck_zone_for(ft), "main", ft)

    def test_type_fallback_variants(self):
        self.assertEqual(ydb.deck_zone_for(None, "XYZ Pendulum Effect Monster"), "extra")
        self.assertEqual(ydb.deck_zone_for(None, "Synchro Tuner Monster"), "extra")
        self.assertEqual(ydb.deck_zone_for(None, "Fusion Monster"), "extra")
        self.assertEqual(ydb.deck_zone_for(None, "Ritual Effect Monster"), "main")
        self.assertEqual(ydb.deck_zone_for(None, None), "main")

    def test_card_category_variants(self):
        self.assertEqual(ydb.card_category("Pendulum Effect Monster"), "monster")
        self.assertEqual(ydb.card_category("Token"), "other")
        self.assertEqual(ydb.card_category(""), "other")


class HypergeomTests(unittest.TestCase):
    def test_min_hits_two_known_value(self):
        # P(>=2 aus 3 Treffern bei 5 aus 40) = 1 - P(0) - P(1).
        n = math.comb(40, 5)
        p0 = math.comb(37, 5) / n
        p1 = 3 * math.comb(37, 4) / n
        self.assertAlmostEqual(ydb.hypergeom_at_least(40, 3, 5, 2), 1 - p0 - p1, places=12)

    def test_min_hits_above_successes_is_zero(self):
        self.assertAlmostEqual(ydb.hypergeom_at_least(40, 3, 5, 4), 0.0, places=12)

    def test_distribution_sums_to_one(self):
        # P(>=k) - P(>=k+1) = P(=k); die Summe ueber k ist 1.
        total = sum(
            ydb.hypergeom_at_least(40, 9, 6, k) - ydb.hypergeom_at_least(40, 9, 6, k + 1)
            for k in range(0, 7)
        )
        self.assertAlmostEqual(total, 1.0, places=12)

    def test_monotone_in_successes_and_draws(self):
        by_succ = [ydb.hypergeom_at_least(40, s, 5) for s in range(0, 16)]
        self.assertEqual(by_succ, sorted(by_succ))
        by_draw = [ydb.hypergeom_at_least(40, 6, d) for d in range(0, 16)]
        self.assertEqual(by_draw, sorted(by_draw))

    def test_all_successes_is_certain(self):
        self.assertEqual(ydb.hypergeom_at_least(40, 40, 1), 1.0)
        # Mehr Zuege als Nieten -> Treffer garantiert.
        self.assertAlmostEqual(ydb.hypergeom_at_least(10, 6, 5), 1.0, places=12)

    def test_negative_inputs_are_clamped(self):
        self.assertEqual(ydb.hypergeom_at_least(-5, 3, 5), 0.0)
        self.assertEqual(ydb.hypergeom_at_least(40, -3, 5), 0.0)
        self.assertEqual(ydb.hypergeom_at_least(40, 3, -5), 0.0)


class ProbOpenAllTests(unittest.TestCase):
    def test_brute_force_matches_small_deck(self):
        # Deck: A A B C x x x x (8 Karten), Hand 3: P(A und B) exakt zaehlen.
        deck = ["A", "A", "B", "C", "x", "x", "x", "x"]
        hands = list(itertools.combinations(range(len(deck)), 3))
        hits = sum(
            1 for h in hands
            if {"A", "B"} <= {deck[i] for i in h}
        )
        self.assertAlmostEqual(
            ydb.prob_open_all(8, [2, 1], 3), hits / len(hands), places=12
        )

    def test_at_most_single_card_probabilities(self):
        both = ydb.prob_open_all(40, [3, 2], 5)
        self.assertLessEqual(both, ydb.hypergeom_at_least(40, 3, 5))
        self.assertLessEqual(both, ydb.hypergeom_at_least(40, 2, 5))

    def test_monotone_in_copies(self):
        vals = [ydb.prob_open_all(40, [c, 3], 5) for c in range(1, 4)]
        self.assertEqual(vals, sorted(vals))

    def test_more_cards_than_hand_is_impossible(self):
        self.assertAlmostEqual(ydb.prob_open_all(40, [1] * 6, 5), 0.0, places=12)

    def test_zero_and_negative_copies_are_ignored(self):
        self.assertAlmostEqual(
            ydb.prob_open_all(40, [3, 0, -2], 5), ydb.prob_open_all(40, [3], 5)
        )

    def test_hand_larger_than_deck_is_capped(self):
        self.assertAlmostEqual(ydb.prob_open_all(5, [1, 1], 9), 1.0)


class ParseYdkTests(unittest.TestCase):
    def test_headers_case_insensitive_and_blank_lines(self):
        text = "#MAIN\n\n  1  \n#Extra\n2\n!SIDE\n3\n"
        self.assertEqual(ydb.parse_ydk(text), {"main": [1], "extra": [2], "side": [3]})

    def test_ids_before_any_header_count_as_main(self):
        self.assertEqual(ydb.parse_ydk("7\n7\n")["main"], [7, 7])

    def test_comments_are_ignored_not_unreadable(self):
        bad: list[str] = []
        ydb.parse_ydk("#created by X\n!irgendwas\n#main\n1\n", bad)
        self.assertEqual(bad, [])

    def test_unreadable_without_list_does_not_raise(self):
        self.assertEqual(ydb.parse_ydk("abc\n1\n")["main"], [1])

    def test_empty_text(self):
        self.assertEqual(ydb.parse_ydk(""), {"main": [], "extra": [], "side": []})


class LintTests(unittest.TestCase):
    def test_lock_and_req_accepted(self):
        steps = ["SS A (GY) [Req: Drache im GY] -> Add B | Lock: nur Drachen"]
        self.assertEqual(ydb.lint_combo_steps(steps), [])

    def test_pipe_without_lock_prefix(self):
        warnings = ydb.lint_combo_steps(["NS A | nur Drachen"])
        self.assertTrue(any("Lock:" in w for w in warnings))

    def test_square_brackets_reserved_for_req(self):
        warnings = ydb.lint_combo_steps(["NS A [Hand]"])
        self.assertTrue(any("[Req:" in w for w in warnings))

    def test_unbalanced_bracket(self):
        warnings = ydb.lint_combo_steps(["NS A [Req: x"])
        self.assertTrue(any("nicht geschlossen" in w for w in warnings))

    def test_formula_without_colon(self):
        warnings = ydb.lint_combo_steps(["Link A + B -> C"])
        self.assertTrue(any("Doppelpunkt" in w for w in warnings))

    def test_incomplete_formula(self):
        warnings = ydb.lint_combo_steps(["Synchro: A -> C"])
        self.assertTrue(any("unvollstaendig" in w for w in warnings))

    def test_empty_step_warns_and_numbering(self):
        warnings = ydb.lint_combo_steps(["NS A", ""])
        self.assertTrue(warnings)
        self.assertTrue(all(w.startswith("Schritt 2:") for w in warnings))

    def test_every_keyword_is_accepted(self):
        for kw in ydb.COMBO_STEP_KEYWORDS:
            if kw.endswith(":"):
                step = f"{kw} A + B -> C"
            else:
                step = f"{kw} Karte"
            self.assertEqual(ydb.lint_combo_steps([step]), [], step)

    def test_keywords_are_case_insensitive(self):
        self.assertEqual(ydb.lint_combo_steps(["ns Karte", "ADD Karte"]), [])


class HelperTests(unittest.TestCase):
    def test_casefold_passthrough(self):
        self.assertEqual(_casefold("Straße"), "strasse")
        self.assertIsNone(_casefold(None))
        self.assertEqual(_casefold(5), 5)

    def test_like_contains_plain(self):
        self.assertEqual(_like_contains("Dragon"), "%dragon%")

    def test_md_inline_escapes_backslash_first(self):
        self.assertEqual(_md_inline("a\\<b"), "a\\\\\\<b")

    def test_parse_version(self):
        self.assertEqual(_parse_version("v0.10.2"), (0, 10, 2))
        self.assertEqual(_parse_version("1.2.3-beta4"), (1, 2, 3, 4))
        self.assertEqual(_parse_version("kaputt"), (0,))
        # Numerisch, nicht lexikografisch verglichen.
        self.assertGreater(_parse_version("0.10.0"), _parse_version("0.9.9"))

    def test_app_version_is_parseable(self):
        self.assertGreater(len(_parse_version(ydb.APP_VERSION)), 1)

    def test_export_filter_note(self):
        self.assertEqual(_export_filter_note(None, None, None, None), "")
        note = _export_filter_note(" Drache ", "spell", "DARK", "Albaz", True)
        self.assertEqual(
            note,
            "Name~„Drache“ · Klasse=Zauber · Attribut=DARK · Archetyp=Albaz"
            " · nur unübersetzte",
        )

    def test_card_meta_line(self):
        base = dict(type="Effect Monster", attribute="DARK", race="Dragon",
                    link_value=None, level=4, atk=1800, def_=None)
        row = {**base, "def": 1200}
        self.assertEqual(
            _card_meta_line(row),
            "Effect Monster · DARK · Dragon · Stufe/Rang 4 · ATK 1800 / DEF 1200",
        )
        link = {**base, "type": "Link Monster", "link_value": 2, "level": None,
                "atk": None, "def": None}
        self.assertEqual(
            _card_meta_line(link), "Link Monster · DARK · Dragon · LINK-2 · ATK ?"
        )
        spell = {"type": "Spell Card", "race": "Quick-Play", "attribute": None,
                 "link_value": None, "level": None, "atk": None, "def": None}
        self.assertEqual(_card_meta_line(spell), "Spell Card · Quick-Play")

    def test_role_labels_cover_all_roles(self):
        self.assertEqual(set(ydb.ROLE_LABEL), set(ydb.COMBO_ROLES))


if __name__ == "__main__":
    unittest.main(verbosity=2)
