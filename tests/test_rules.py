"""
test_rules.py
=============
Regelwerk des Spielfelds (yugioh_gui._rules) und Spielzustand
(yugioh_gui._game) -- reine Funktionen, kein Qt. Kartenwerte kommen aus
einer migrierten Temp-Kopie der Dev-DB (echte Karten, keine Mock-Karten):
die Resonator-/Mitsurugi-Karten aus 'RDA-Mitsu' plus per Kriterium
gewaehlte Xyz-/Feldzauber-/Pendel-/Fusionskarten.

    python -m unittest tests.test_rules
"""

from __future__ import annotations

import unittest

import yugioh_db as ydb
from yugioh_gui import _rules as R
from yugioh_gui._cardinst import _CardInst
from yugioh_gui._game import Game

from tests._support import (
    REAL_LINK_MARKERS, copy_dev_db, needs_dev_db, query, remove_tree,
    write_real_link_markers,
)

# Karten aus 'RDA-Mitsu' (Passcodes; die Suite setzt dieses Deck voraus).
SOUL = 62991792         # Soul Resonator: Empfaenger, Stufe 3
BONE = 25784595         # Bone Archfiend: Stufe 4
SAJI = 18176525         # Mitsurugi no Mikoto, Saji: Stufe 4
CRIMSON_BLADE = 3294539  # Synchro Stufe 7
RED_RISING = 66141736   # Synchro Stufe 6
POWER_VICE = 19434243   # Stufe 5 (1 Tribut)
LUBELLION = 32731036    # Stufe 8 (2 Tribute)
HABAKIRI = 13332685     # Ritualmonster Stufe 8
IMPERM = 10045474       # Normale Falle
CALLED = 24224830       # Schnellzauber
RESO_CALL = 23008320    # Normaler Zauber
MASQ = 65741786         # Link-2, Pfeile unten links/rechts
KNIGHT = 29301450       # Link-2, Pfeile links/rechts


class LinkGeometryTests(unittest.TestCase):
    """Pfeil-Tabelle passend zum Brett (e0 ueber m1, e1 ueber m3)."""

    def test_emz_and_main_zones_point_at_each_other(self):
        opposite = {"Top": "Bottom", "Top-Left": "Bottom-Right",
                    "Top-Right": "Bottom-Left", "Left": "Right", "Right": "Left"}
        for (zone, marker), target in R.LINK_TARGETS.items():
            back = opposite.get(marker) or {v: k for k, v in opposite.items()}[marker]
            self.assertEqual(R.LINK_TARGETS[(target, back)], zone, (zone, marker))

    def test_known_cases(self):
        self.assertEqual(R.pointed_zones("e0", ("Bottom-Left", "Bottom-Right")),
                         {"m0", "m2"})
        self.assertEqual(R.pointed_zones("e1", ("Bottom",)), {"m3"})
        self.assertEqual(R.pointed_zones("m2", ("Top-Left", "Top-Right")),
                         {"e0", "e1"})
        self.assertEqual(R.pointed_zones("m0", ("Left", "Top")), set())
        self.assertEqual(R.pointed_zones("m4", None), set())


@needs_dev_db
class _RulesBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir, cls.db = copy_dev_db()
        ydb.ensure_schema(cls.db)
        write_real_link_markers(cls.db)

        def first(where):
            row = query(cls.db, f"SELECT id FROM cards WHERE {where} ORDER BY id LIMIT 1")
            assert row, where
            return row[0][0]
        cls.XYZ4 = first("frame_type = 'xyz' AND level = 4")
        cls.FIELD = first("type = 'Spell Card' AND race = 'Field'")
        cls.PEND = first("frame_type = 'effect_pendulum'")
        cls.FUSION = first("frame_type = 'fusion'")
        cls.PEND_SYNCHRO = first("frame_type = 'synchro_pendulum'")
        ids = [cls.PEND_SYNCHRO, SOUL, BONE, SAJI, CRIMSON_BLADE, RED_RISING, POWER_VICE,
               LUBELLION, HABAKIRI, IMPERM, CALLED, RESO_CALL, MASQ, KNIGHT,
               cls.XYZ4, cls.FIELD, cls.PEND, cls.FUSION]
        cls.info = ydb.card_play_info(cls.db, ids)
        missing = set(ids) - set(cls.info)
        assert not missing, f"Dev-DB fehlen Karten: {missing}"

    @classmethod
    def tearDownClass(cls):
        remove_tree(cls.tmpdir)

    def info_of(self, cid):
        return self.info[cid]

    def inst(self, cid, **kw) -> _CardInst:
        return _CardInst(cid, self.info[cid]["name"], **kw)

    @staticmethod
    def game(phase="M1", turn=1) -> Game:
        g = Game()
        g.phase, g.turn = phase, turn
        return g


class ClassificationTests(_RulesBase):
    def test_kinds_and_levels(self):
        i = self.info
        self.assertEqual(R.kind(i[CRIMSON_BLADE]), "synchro")
        self.assertEqual(R.kind(i[MASQ]), "link")
        self.assertEqual(R.kind(i[HABAKIRI]), "ritual")
        self.assertEqual(R.kind(i[IMPERM]), "trap")
        self.assertEqual(R.kind(i[CALLED]), "spell")
        self.assertEqual(R.spell_trap_kind(i[CALLED]), "Quick-Play")
        self.assertTrue(R.is_tuner(i[SOUL]))
        self.assertFalse(R.is_tuner(i[BONE]))
        self.assertTrue(R.is_pendulum(i[self.PEND]))
        self.assertIsNone(R.level_of(self.inst(MASQ), i[MASQ]))
        self.assertIsNone(R.level_of(self.inst(self.XYZ4), i[self.XYZ4]))
        self.assertEqual(R.rank_of(i[self.XYZ4]), 4)
        self.assertEqual(R.level_of(self.inst(BONE, level_mod=-1), i[BONE]), 3)
        self.assertIsNone(R.def_of(self.inst(MASQ), i[MASQ]))
        bone = self.inst(BONE, atk_mod=500)
        self.assertEqual(R.atk_of(bone, i[BONE]), (i[BONE]["atk"] or 0) + 500)
        self.assertEqual([R.tributes_needed(i[c]) for c in (BONE, POWER_VICE, LUBELLION)],
                         [0, 1, 2])
        self.assertEqual(R.summon_methods(i[CRIMSON_BLADE]), ["synchro"])
        self.assertEqual(R.summon_methods(i[BONE]), [])


class SummonTests(_RulesBase):
    def test_normal_summon_once_per_turn(self):
        g = self.game()
        bone = self.inst(BONE)
        self.assertTrue(R.check_normal_summon(g, bone, self.info[BONE], "m0", [], self.info_of))
        g.ns_used = 1
        v = R.check_normal_summon(g, bone, self.info[BONE], "m0", [], self.info_of)
        self.assertIn("schon verbraucht", v.violations[0])
        g.ns_extra = 1                      # Effekt: zusaetzliche NS
        self.assertTrue(R.check_normal_summon(g, bone, self.info[BONE], "m0", [], self.info_of))

    def test_normal_summon_phase_turn_and_zone(self):
        bone = self.inst(BONE)
        for g, frag in ((self.game(phase="BP", turn=3), "Main Phase"),
                        (self.game(turn=2), "eigenen Zug")):
            v = R.check_normal_summon(g, bone, self.info[BONE], "m0", [], self.info_of)
            self.assertTrue(any(frag in x for x in v.violations), v.violations)
        v = R.check_normal_summon(self.game(), bone, self.info[BONE], "e0", [], self.info_of)
        self.assertTrue(any("Extra-Monsterzone" in x for x in v.violations))

    def test_tributes(self):
        g = self.game()
        lub = self.inst(LUBELLION)
        one = [self.inst(BONE)]
        v = R.check_normal_summon(g, lub, self.info[LUBELLION], "m0", one, self.info_of)
        self.assertIn("2 Tribute", v.violations[0])
        two = one + [self.inst(SAJI)]
        self.assertTrue(R.check_normal_summon(g, lub, self.info[LUBELLION], "m0", two,
                                              self.info_of))
        pv = self.inst(POWER_VICE)
        self.assertTrue(R.check_normal_summon(g, pv, self.info[POWER_VICE], "m0", one,
                                              self.info_of, set_=True))

    def test_ritual_and_extra_monsters_cannot_be_normal_summoned(self):
        g = self.game()
        for cid in (HABAKIRI, CRIMSON_BLADE):
            v = R.check_normal_summon(g, self.inst(cid), self.info[cid], "m0",
                                      [self.inst(BONE), self.inst(SAJI)], self.info_of)
            self.assertTrue(any("nicht als Normalbeschwörung" in x for x in v.violations))
        v = R.check_normal_summon(g, self.inst(IMPERM), self.info[IMPERM], "m0", [],
                                  self.info_of)
        self.assertIn("keine Monsterkarte", v.violations[0])

    def test_extra_monster_zones(self):
        g = self.game()
        blade = self.inst(CRIMSON_BLADE)
        info = self.info[CRIMSON_BLADE]
        self.assertTrue(R.check_special_summon(g, blade, info, "e0", True, self.info_of))
        self.assertTrue(R.check_special_summon(g, blade, info, "m2", True, self.info_of))
        g.emz[1] = self.inst(RED_RISING)
        v = R.check_special_summon(g, blade, info, "e0", True, self.info_of)
        self.assertIn("nur eine Extra-Monsterzone", v.violations[0])
        g.emz[1].owner = "opp"              # Gegner-EMZ blockiert die eigene nicht
        self.assertTrue(R.check_special_summon(g, blade, info, "e0", True, self.info_of))
        # Main-Deck-Monster (auch aus dem GY) nie in die EMZ.
        v = R.check_special_summon(g, self.inst(BONE), self.info[BONE], "e0", False,
                                   self.info_of)
        self.assertFalse(v)

    def test_link_monsters_need_linked_zone(self):
        g = self.game()
        knight, kinfo = self.inst(KNIGHT), self.info[KNIGHT]
        v = R.check_special_summon(g, knight, kinfo, "m0", True, self.info_of)
        self.assertIn("Link-Pfeil", v.violations[0])
        g.emz[0] = self.inst(MASQ)          # zeigt auf m0 und m2
        self.assertEqual(R.linked_zones(g, self.info_of), ({"m0", "m2"}, []))
        self.assertTrue(R.check_special_summon(g, knight, kinfo, "m0", True, self.info_of))
        self.assertFalse(R.check_special_summon(g, knight, kinfo, "m1", True, self.info_of))
        # Aus dem GY wiederbelebt: keine Link-Pfeil-Pflicht.
        self.assertTrue(R.check_special_summon(g, knight, kinfo, "m1", False, self.info_of))
        # Synchro aus dem ED darf in jede Hauptmonsterzone.
        self.assertTrue(R.check_special_summon(g, self.inst(CRIMSON_BLADE),
                                               self.info[CRIMSON_BLADE], "m1", True,
                                               self.info_of))

    def test_pendulum_extra_monsters_from_face_down_ed_are_free(self):
        """Nur offene Pendel im ED unterliegen der Link-Zonen-Regel -- ein
        Synchro-Pendel aus dem (verdeckten) ED darf in jede Zone."""
        g = self.game()
        info = self.info[self.PEND_SYNCHRO]
        self.assertTrue(R.is_pendulum(info))
        v = R.check_special_summon(g, self.inst(self.PEND_SYNCHRO), info, "m1",
                                   True, self.info_of)
        self.assertEqual((v.violations, v.notes), ([], []))

    def test_unknown_link_markers_are_only_a_note(self):
        g = self.game()
        info = dict(self.info)
        info[MASQ] = dict(info[MASQ], link_markers=None)
        g.emz[0] = self.inst(MASQ)
        v = R.check_special_summon(g, self.inst(KNIGHT), info[KNIGHT], "m4", True, info.get)
        self.assertTrue(v)                  # blockiert nie
        self.assertIn("Link-Pfeile unbekannt", v.notes[0])


class MaterialTests(_RulesBase):
    def pairs(self, *cids, **kw):
        return [(self.inst(c, **kw), self.info[c]) for c in cids]

    def test_synchro(self):
        info = self.info[CRIMSON_BLADE]                     # Stufe 7 = 3 + 4
        self.assertTrue(R.check_materials("synchro", info, self.pairs(SOUL, BONE)))
        v = R.check_materials("synchro", info, self.pairs(SOUL, SAJI, BONE))
        self.assertTrue(any("Stufensumme" in x for x in v.violations))
        v = R.check_materials("synchro", info, self.pairs(BONE, SAJI))
        self.assertTrue(any("Empfänger" in x for x in v.violations))
        v = R.check_materials("synchro", info, self.pairs(SOUL))
        self.assertTrue(any("Nicht-Empfänger" in x for x in v.violations))
        # Stufen-Aenderung per Effekt zaehlt: Bone auf 3 -> Summe 6 = Red Rising.
        bone3 = (self.inst(BONE, level_mod=-1), self.info[BONE])
        self.assertTrue(R.check_materials("synchro", self.info[RED_RISING],
                                          [self.pairs(SOUL)[0], bone3]))

    def test_xyz(self):
        info = self.info[self.XYZ4]
        self.assertTrue(R.check_materials("xyz", info, self.pairs(BONE, SAJI)))
        v = R.check_materials("xyz", info, self.pairs(BONE))
        self.assertIn("mindestens 2", v.violations[0])
        v = R.check_materials("xyz", info, self.pairs(BONE, SOUL))
        self.assertTrue(any("Stufe 3" in x for x in v.violations))
        v = R.check_materials("xyz", info, self.pairs(BONE, MASQ))
        self.assertTrue(any("keine Stufe" in x for x in v.violations))
        token = (_CardInst(-1, "Spielmarke", token=True),
                 {"name": "Spielmarke", "type": "Token", "frame_type": "token", "level": 4})
        v = R.check_materials("xyz", info, self.pairs(BONE) + [token])
        self.assertTrue(any("Spielmarken" in x for x in v.violations))

    def test_link_counts_link_materials_flexibly(self):
        info = self.info[KNIGHT]                             # Link-2
        self.assertTrue(R.check_materials("link", info, self.pairs(BONE, SAJI)))
        self.assertTrue(R.check_materials("link", info, self.pairs(MASQ)))   # 1x Link-2
        self.assertFalse(R.check_materials("link", info, self.pairs(BONE)))
        self.assertFalse(R.check_materials("link", info, self.pairs(BONE, SAJI, SOUL)))
        self.assertFalse(R.check_materials("link", info, []))
        v = R.check_materials("link", info, self.pairs(BONE, SAJI, face_down=True))
        self.assertTrue(any("verdeckt" in x for x in v.violations))

    def test_fusion_and_ritual(self):
        self.assertFalse(R.check_materials("fusion", self.info[self.FUSION],
                                           self.pairs(BONE)))
        v = R.check_materials("fusion", self.info[self.FUSION], self.pairs(BONE, SAJI))
        self.assertTrue(v)
        self.assertTrue(v.notes)                              # Text ungeprueft
        hab = self.info[HABAKIRI]                             # Stufe 8
        self.assertTrue(R.check_materials("ritual", hab, self.pairs(BONE, SAJI)))
        self.assertFalse(R.check_materials("ritual", hab, self.pairs(BONE, SOUL)))

    def test_same_material_twice(self):
        bone = self.inst(BONE)
        v = R.check_materials("link", self.info[KNIGHT],
                              [(bone, self.info[BONE]), (bone, self.info[BONE])])
        self.assertTrue(any("doppelt" in x for x in v.violations))

    def test_candidates(self):
        g = self.game()
        bone, saji, soul = self.inst(BONE), self.inst(SAJI), self.inst(SOUL)
        g.me.m[0], g.me.hand = bone, [saji, soul]
        g.emz[0] = self.inst(MASQ, owner="opp")
        self.assertEqual(R.material_candidates(g, "synchro"), [bone])
        self.assertEqual(R.material_candidates(g, "fusion", saji), [bone, soul])


class SpellTrapPositionTests(_RulesBase):
    def check_hand(self, g, cid, zone, activate):
        return R.check_spell_trap_from_hand(g, self.inst(cid), self.info[cid], zone, activate)

    def test_spells_and_traps_from_hand(self):
        g = self.game()
        self.assertTrue(self.check_hand(g, RESO_CALL, "s0", True))
        self.assertTrue(self.check_hand(g, IMPERM, "s0", False))
        self.assertIn("erst gesetzt", self.check_hand(g, IMPERM, "s0", True).violations[0])
        self.assertFalse(self.check_hand(self.game(phase="BP", turn=3), RESO_CALL, "s0", True))
        self.assertTrue(self.check_hand(self.game(phase="BP", turn=3), CALLED, "s0", True))
        self.assertFalse(self.check_hand(self.game(turn=2), CALLED, "s0", True))
        self.assertFalse(self.check_hand(g, self.FIELD, "s1", True))
        self.assertTrue(self.check_hand(g, self.FIELD, "field", True))
        self.assertFalse(self.check_hand(g, RESO_CALL, "field", True))
        self.assertTrue(self.check_hand(g, self.PEND, "s4", True))
        self.assertFalse(self.check_hand(g, self.PEND, "s2", True))
        self.assertFalse(self.check_hand(g, BONE, "s2", True))

    def test_set_cards_wait_a_turn(self):
        g = self.game()
        trap = self.inst(IMPERM)
        g.flags["set"].add(trap.uid)
        self.assertIn("in diesem Zug gesetzt",
                      R.check_activate_set(g, trap, self.info[IMPERM]).violations[0])
        g.next_turn()
        g.next_turn()
        self.assertTrue(R.check_activate_set(g, trap, self.info[IMPERM]))
        spell = self.inst(RESO_CALL)
        g.phase = "BP"
        self.assertFalse(R.check_activate_set(g, spell, self.info[RESO_CALL]))

    def test_position_changes(self):
        g = self.game()
        bone = self.inst(BONE)
        self.assertTrue(R.check_position_change(g, bone, self.info[BONE]))
        g.flags["summoned"].add(bone.uid)
        self.assertFalse(R.check_position_change(g, bone, self.info[BONE]))
        g.next_turn(); g.next_turn(); g.phase = "M1"
        self.assertTrue(R.check_position_change(g, bone, self.info[BONE]))
        g.flags["pos_changed"].add(bone.uid)
        self.assertFalse(R.check_position_change(g, bone, self.info[BONE]))
        v = R.check_position_change(g, self.inst(MASQ), self.info[MASQ])
        self.assertTrue(any("Link-Monster" in x for x in v.violations))
        # Flippbeschwoerung nicht im Zug des Setzens.
        set_mon = self.inst(BONE, face_down=True, defense=True)
        g.flags["set"].add(set_mon.uid)
        self.assertFalse(R.check_flip_summon(g, set_mon, self.info[BONE]))
        self.assertFalse(R.check_turn_face_down(bone, self.info[BONE], "m0"))
        self.assertFalse(R.check_turn_face_down(self.inst(CALLED), self.info[CALLED], "s0"))


class TurnAndBattleTests(_RulesBase):
    def test_phases(self):
        g = self.game(phase="M2", turn=3)
        self.assertFalse(R.check_phase_change(g, "M1"))
        self.assertTrue(R.check_phase_change(g, "EP"))
        self.assertFalse(R.check_phase_change(self.game(turn=1), "BP"))

    def test_hand_limit(self):
        g = self.game()
        g.me.hand = [self.inst(BONE) for _ in range(7)]
        self.assertIn("Handlimit", R.check_end_turn(g).violations[0])
        g.me.hand.pop()
        self.assertTrue(R.check_end_turn(g))

    def test_attack_checks(self):
        g = self.game(phase="BP", turn=3)
        bone = self.inst(BONE)
        self.assertTrue(R.check_attack(g, bone, None))
        g.opp.m[0] = self.inst(SAJI, owner="opp")
        v = R.check_attack(g, bone, None)
        self.assertIn("Direkter Angriff", v.violations[0])
        self.assertTrue(R.check_attack(g, bone, g.opp.m[0]))
        g.flags["attacked"].add(bone.uid)
        self.assertFalse(R.check_attack(g, bone, g.opp.m[0]))
        self.assertFalse(R.check_attack(self.game(phase="BP", turn=1), self.inst(BONE), None))
        self.assertFalse(R.check_attack(g, self.inst(BONE, defense=True), g.opp.m[0]))
        self.assertFalse(R.check_attack(self.game(phase="M1", turn=3), self.inst(BONE), None))

    def test_damage_calc(self):
        dc = R.damage_calc
        self.assertEqual(dc(1800, None), R.BattleResult(damage_opp=1800))
        self.assertEqual(dc(2000, (1500, 0, False)),
                         R.BattleResult(target_destroyed=True, damage_opp=500))
        self.assertEqual(dc(1500, (2000, 0, False)),
                         R.BattleResult(attacker_destroyed=True, damage_me=500))
        self.assertEqual(dc(1500, (1500, 0, False)),
                         R.BattleResult(attacker_destroyed=True, target_destroyed=True))
        self.assertEqual(dc(0, (0, 0, False)), R.BattleResult())
        self.assertEqual(dc(2000, (0, 1500, True)), R.BattleResult(target_destroyed=True))
        self.assertEqual(dc(1000, (0, 1500, True)), R.BattleResult(damage_me=500))
        self.assertEqual(dc(1500, (0, 1500, True)), R.BattleResult())


class GameStateTests(_RulesBase):
    def test_turn_order(self):
        g = Game()
        self.assertTrue(g.my_turn)
        g.next_turn()
        self.assertEqual((g.turn, g.phase, g.my_turn), (2, "DP", False))
        g.going_first = False
        self.assertTrue(g.my_turn)

    def test_snapshot_restores_everything(self):
        g = self.game(phase="BP", turn=3)
        xyz = self.inst(self.XYZ4, materials=[self.inst(BONE), self.inst(SAJI)])
        g.emz[0] = xyz
        g.me.m[1] = self.inst(SOUL, counters=2, atk_mod=300)
        g.opp.s[2] = self.inst(IMPERM, owner="opp", face_down=True)
        g.opp.lp, g.me.lp = 4000, 7500
        g.ns_used, g.ns_extra = 1, 1
        g.flags["attacked"].add(xyz.uid)
        snap = g.snapshot()
        before = (repr(snap),)
        g2 = Game()
        g2.restore(snap)
        self.assertEqual((repr(g2.snapshot()),), before)
        self.assertEqual([m.name for m in g2.emz[0].materials],
                         [self.info[BONE]["name"], self.info[SAJI]["name"]])
        self.assertIsNot(g2.emz[0], xyz)
        # Snapshot ist wertbasiert: spaetere Aenderungen schlagen nicht durch.
        g.flags["attacked"].clear()
        g.me.m[1].counters = 9
        g2.restore(snap)
        self.assertIn(xyz.uid, g2.flags["attacked"])
        self.assertEqual(g2.me.m[1].counters, 2)

    def test_zone_helpers(self):
        g = self.game()
        bone, masq = self.inst(BONE), self.inst(MASQ)
        opp_card = self.inst(SAJI, owner="opp")
        g.put("m3", bone)
        g.put("e1", masq)
        g.put("ofield", opp_card)
        self.assertEqual([g.zone_of(c) for c in (bone, masq, opp_card)],
                         ["m3", "e1", "ofield"])
        self.assertEqual(g.monsters("me"), [bone, masq])
        self.assertEqual(g.field_cards("opp"), [opp_card])
        g.remove(opp_card)
        self.assertIsNone(g.opp.field)
        self.assertEqual(set(REAL_LINK_MARKERS), {MASQ, KNIGHT})


if __name__ == "__main__":
    unittest.main()
