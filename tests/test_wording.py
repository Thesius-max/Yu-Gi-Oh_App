"""
Wortlaut-Lesehilfe (yugioh_db.wording): echte Kartentexte aus einer
Temp-Kopie der Dev-DB -- feste Karten aus 'RDA-Mitsu' plus per Kriterium
gewaehlte Karten fuer die uebrigen Satzmuster. Ausserdem: jedes
Regelwerk-Kapitel, auf das Lesehilfe oder Spielfeld verweisen, existiert.
"""

from __future__ import annotations

import unittest

from yugioh_db import wording as W

from tests._support import copy_dev_db, needs_dev_db, query, remove_tree
from tests.test_rules import BONE, CRIMSON_BLADE, IMPERM, KNIGHT, MASQ

ASH = 14558127


def _rulebook_keys() -> set[str]:
    from yugioh_gui.rulebook_text import RULEBOOK_SECTIONS
    return {k for k, _t, _md in RULEBOOK_SECTIONS}


class TopicTests(unittest.TestCase):
    def test_all_topics_exist_in_rulebook(self):
        keys = _rulebook_keys()
        for const in (W.TOPIC_SUMMON, W.TOPIC_EFFECTS, W.TOPIC_CHAIN, W.TOPIC_TEXT,
                      W.TOPIC_RULINGS, W.TOPIC_CARDS):
            self.assertIn(const, keys)
        # Sprungziele des Spielfeld-Protokolls (playtest._allowed).
        for key in ("beschwoerung", "phasen", "kartenarten", "kampf"):
            self.assertIn(key, keys)

    def test_sections_are_unique_and_filled(self):
        from yugioh_gui.rulebook_text import RULEBOOK_SECTIONS, section_title
        keys = [k for k, _t, _md in RULEBOOK_SECTIONS]
        self.assertEqual(len(keys), len(set(keys)))
        for key, title, md in RULEBOOK_SECTIONS:
            self.assertTrue(md.lstrip().startswith("# "), key)
            self.assertEqual(section_title(key), title)
        self.assertEqual(section_title("gibt-es-nicht"), "")

    def test_empty_text(self):
        self.assertEqual(W.explain_card_text(None), [])
        self.assertEqual(W.explain_card_text("  "), [])


@needs_dev_db
class WordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir, cls.db = copy_dev_db()

    @classmethod
    def tearDownClass(cls):
        remove_tree(cls.tmpdir)

    def card(self, cid=None, where=None):
        sql = "SELECT id, name, type, race, description FROM cards WHERE "
        rows = query(self.db, sql + ("id = ?" if cid else where + " ORDER BY id LIMIT 1"),
                     (cid,) if cid else ())
        self.assertTrue(rows, cid or where)
        return rows[0]

    def explain(self, cid=None, where=None):
        c = self.card(cid, where)
        return W.explain_card_text(c["description"], c["type"], c["race"])

    @staticmethod
    def labels(seg):
        return [t.label for t in seg.tags]

    def test_ash_blossom(self):
        eff, limit = self.explain(ASH)
        self.assertEqual(eff.kind, "quick")
        self.assertIn("Schnelleffekt", self.labels(eff))
        self.assertIn("Antwort auf eine Aktivierung", self.labels(eff))
        self.assertNotIn("„Wenn“", self.labels(eff))      # kein Timing-Verpassen
        self.assertIn("Kosten", self.labels(eff))
        self.assertEqual([p.role for p in eff.parts[:3]], ["condition", "cost", "effect"])
        self.assertIn("discard this card", eff.parts[1].text)
        self.assertTrue(eff.text.count("●") == 3)          # Aufzaehlung bleibt dran
        self.assertEqual((limit.kind, self.labels(limit)), ("limit", ["Hartes OPT"]))

    def test_infinite_impermanence(self):
        act, rule = self.explain(IMPERM)
        self.assertEqual(act.kind, "activation")
        self.assertIn("Ziel bei Aktivierung", self.labels(act))
        self.assertNotIn("Kosten", self.labels(act))
        self.assertIn("„dann“", self.labels(act))
        self.assertEqual(rule.kind, "rule")

    def test_bone_archfiend(self):
        first, second, limit = self.explain(BONE)
        self.assertEqual(first.kind, "ignition")
        self.assertIn("„Falls“ (Zustand)", self.labels(first))
        self.assertIn("Einschränkung (Lock)", self.labels(first))
        self.assertIn("„und falls du dies tust“", self.labels(second))
        self.assertEqual(self.labels(limit), ["Hartes OPT (je Effekt)"])

    def test_extra_deck_materials_and_triggers(self):
        segs = self.explain(KNIGHT)
        self.assertEqual(segs[0].kind, "materials")
        self.assertEqual(segs[1].kind, "trigger")
        self.assertIn("„Falls“ (Ereignis)", self.labels(segs[1]))
        masq = self.explain(MASQ)
        quick = next(s for s in masq if s.kind == "quick")
        self.assertNotIn("Pflichteffekt", self.labels(quick))   # „you can“ in der Bedingung
        self.assertEqual(masq[-1].kind, "continuous")
        blade = self.explain(CRIMSON_BLADE)
        self.assertEqual(blade[1].kind, "note")                  # always treated as
        self.assertNotIn("summon_condition", [s.kind for s in blade])

    def test_other_patterns_by_criteria(self):
        when = self.explain(where="type = 'Effect Monster' AND description LIKE 'When%: You can%'")
        self.assertIn("„Wenn“", self.labels(when[0]))
        flip = self.explain(where="description LIKE 'FLIP:%'")
        self.assertEqual(flip[0].kind, "flip")
        nomi = self.explain(where="description LIKE 'Cannot be Normal Summoned/Set. "
                                  "Must first be%'")
        self.assertEqual([s.kind for s in nomi[:2]], ["summon_condition"] * 2)
        self.assertIn("Semi-Nomi", self.labels(nomi[1]))
        soft = self.explain(where="description LIKE 'Once per turn: You can%'")
        self.assertIn("Weiches OPT", self.labels(soft[0]))
        normal = self.explain(where="type = 'Normal Monster'")
        self.assertEqual([s.kind for s in normal], ["flavor"])
        pend = self.explain(where="frame_type = 'effect_pendulum' AND "
                                  "description LIKE '%[ Monster Effect ]%'")
        self.assertEqual({s.scope for s in pend} - {""}, {"Pendeleffekt", "Monstereffekt"})

    def test_card_flags(self):
        def flags(cid):
            c = self.card(cid)
            return W.card_flags(c["description"], c["type"], c["race"])
        ash = flags(ASH)
        self.assertTrue({"handtrap", "quick", "hard_opt", "negate"} <= ash)
        self.assertNotIn("no_opt", ash)
        self.assertIn("handtrap", flags(IMPERM))                  # Falle aus der Hand
        self.assertTrue({"ignition", "lock", "targets"} <= flags(BONE))
        self.assertNotIn("handtrap", flags(BONE))
        self.assertIn("continuous", flags(MASQ))
        self.assertEqual(W.flags_to_text({"b", "a"}), ",a,b,")
        self.assertEqual(W.flags_to_text(set()), ",")
        self.assertLessEqual(set().union(*(flags(c) for c in (ASH, IMPERM, BONE, MASQ))),
                             {k for k, _l in W.WORDING_FILTERS})

    def test_every_card_is_analyzed_without_errors(self):
        rows = query(self.db, "SELECT type, race, description FROM cards")
        kinds = set()
        for r in rows:
            for seg in W.explain_card_text(r["description"], r["type"], r["race"]):
                kinds.add(seg.kind)
                self.assertTrue(seg.parts, seg.text)
                self.assertTrue(all(t.topic in _rulebook_keys() for t in seg.tags))
        self.assertLessEqual(kinds, set(W.KIND_LABELS))
        self.assertGreaterEqual(len(kinds), 10)


if __name__ == "__main__":
    unittest.main()
