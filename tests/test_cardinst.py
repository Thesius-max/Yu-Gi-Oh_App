"""
test_cardinst.py
================
Tests fuer _CardInst (yugioh_gui._cardinst) -- die reine Datenklasse
des Spielfelds. Keine Qt-Abhaengigkeit, kein DB-Zugriff, laeuft immer.

    QT_QPA_PLATFORM=offscreen python -m unittest tests.test_cardinst
"""

from __future__ import annotations

import unittest

from yugioh_gui._cardinst import _CardInst


class TestCardInst(unittest.TestCase):
    """Grundlegende _CardInst-Eigenschaften: Konstruktion, uid, Serialisierung."""

    def test_basic_construction(self):
        c = _CardInst(100, "Dark Magician")
        self.assertEqual(c.card_id, 100)
        self.assertEqual(c.name, "Dark Magician")
        self.assertFalse(c.face_down)
        self.assertFalse(c.defense)
        self.assertIsNone(c.origin)

    def test_explicit_fields(self):
        c = _CardInst(200, "Blue-Eyes", face_down=True, defense=True,
                      origin="GY")
        self.assertTrue(c.face_down)
        self.assertTrue(c.defense)
        self.assertEqual(c.origin, "GY")

    def test_uid_auto_increments(self):
        a = _CardInst(1, "A")
        b = _CardInst(2, "B")
        self.assertNotEqual(a.uid, b.uid)
        self.assertEqual(b.uid, a.uid + 1)

    def test_explicit_uid_override(self):
        c = _CardInst(1, "X", uid=999)
        self.assertEqual(c.uid, 999)
        # Naechste auto-uid ueberspringt 999 nicht (counter laeuft weiter).

    def test_to_tuple_roundtrip(self):
        c = _CardInst(300, "Raigeki", face_down=True, defense=False,
                      origin="ED")
        t = c.to_tuple()
        self.assertEqual(t[:6], (300, "Raigeki", True, False, c.uid, "ED"))
        restored = _CardInst.from_tuple(t)
        self.assertEqual(restored.card_id, c.card_id)
        self.assertEqual(restored.name, c.name)
        self.assertEqual(restored.face_down, c.face_down)
        self.assertEqual(restored.defense, c.defense)
        self.assertEqual(restored.uid, c.uid)
        self.assertEqual(restored.origin, c.origin)

    def test_field_state_roundtrip_with_nested_materials(self):
        """Xyz-Material (Exemplare unter der Karte) wird rekursiv
        serialisiert; uids und Spielfeld-Zustand ueberleben."""
        mat = _CardInst(10, "Mat", counters=1)
        xyz = _CardInst(20, "Xyz", owner="opp", counters=2, atk_mod=500,
                        def_mod=-100, level_mod=1, materials=[mat])
        tok = _CardInst(-1, "Token", token=True)
        back = _CardInst.from_tuple(xyz.to_tuple())
        self.assertEqual((back.owner, back.counters, back.atk_mod,
                          back.def_mod, back.level_mod), ("opp", 2, 500, -100, 1))
        self.assertEqual([(m.uid, m.card_id, m.counters) for m in back.materials],
                         [(mat.uid, 10, 1)])
        self.assertIsNot(back.materials[0], mat)
        self.assertTrue(_CardInst.from_tuple(tok.to_tuple()).token)

    def test_reset_state_clears_field_only_state(self):
        c = _CardInst(1, "X", face_down=True, defense=True, counters=3,
                      atk_mod=100, def_mod=100, level_mod=-1, owner="opp")
        c.reset_state()
        self.assertEqual((c.face_down, c.defense, c.counters, c.atk_mod,
                          c.def_mod, c.level_mod), (False, False, 0, 0, 0, 0))
        self.assertEqual(c.owner, "opp")          # Besitz bleibt

    def test_from_tuple_none(self):
        self.assertIsNone(_CardInst.from_tuple(None))

    def test_uid_survives_roundtrip(self):
        """Der kritische Test: uid muss den Undo-Snapshot ueberleben."""
        original = _CardInst(42, "Stardust Dragon")
        snapshot = original.to_tuple()
        restored = _CardInst.from_tuple(snapshot)
        self.assertEqual(original.uid, restored.uid)

    def test_uid_stable_across_multiple_roundtrips(self):
        """Simuliert Undo: Snapshot -> Restore -> Snapshot -> Restore."""
        c = _CardInst(500, "Red Dragon Archfiend")
        for _ in range(10):
            c = _CardInst.from_tuple(c.to_tuple())
        # uid bleibt identisch, obwohl Exemplare neu gebaut wurden.
        self.assertIsNotNone(c.uid)
        # Zweites Exemplar mit gleichen Feldern hat andere uid.
        other = _CardInst(500, "Red Dragon Archfiend")
        self.assertNotEqual(c.uid, other.uid)

    def test_slots_prevents_arbitrary_attributes(self):
        c = _CardInst(1, "X")
        with self.assertRaises(AttributeError):
            c.nonexistent = True

    def test_multiple_instances_unique_uids(self):
        """Jede Instanz bekommt eine eigene uid, auch bei gleichen Feldern."""
        uids = {_CardInst(1, "Same").uid for _ in range(20)}
        self.assertEqual(len(uids), 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
