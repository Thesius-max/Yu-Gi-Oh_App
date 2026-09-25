"""
test_playtest.py
================
Offscreen-Tests fuer den Spielfeld-Tab (PlayTestView): Karten duerfen weder
verloren gehen noch sich verdoppeln, Extra-Deck-Monster bleiben im Extra
Deck, der Recorder protokolliert sauber.

Wie die DB-Tests (tests/_support.py): gegen eine Temp-Kopie der Dev-DB, keine Mock-Karten,
kein Netz (cache_image wird abgeschaltet). Modale Dialoge/Menues werden
durch Stubs ersetzt, die eine vorgegebene Wahl treffen.

    python -m unittest tests.test_playtest
"""

from __future__ import annotations

import collections
import random
import sqlite3
import unittest
from unittest import mock

import yugioh_db as ydb

from tests._support import (
    HAS_QT, copy_dev_db, needs_qt, query, remove_tree, write_real_link_markers
)
from tests.test_rules import (
    BONE, CALLED, CRIMSON_BLADE, IMPERM, KNIGHT, LUBELLION, MASQ, SAJI, SOUL
)

if HAS_QT:
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtWidgets import QApplication, QDialog, QMenu


# Gemeinsame Steuerung der Stubs: was "der Benutzer" im Dialog/Menue waehlt.
CTRL: dict = {}


def _pile_exec(self):
    n = self.listw.count()
    if n == 0:
        return QDialog.DialogCode.Rejected
    self.listw.setCurrentRow(CTRL["pile_row"] % n)
    self._take(CTRL["pile_action"])
    return QDialog.DialogCode.Accepted


def _top_exec(self):
    for row, act in CTRL["top_rows"]:
        if not self.cards:
            break
        self.listw.setCurrentRow(row % len(self.cards))
        self._do(act)
    return QDialog.DialogCode.Accepted


if HAS_QT:
    class _FakeMenu(QMenu):
        """QMenu, dessen exec() die Aktion mit dem Text CTRL['menu_pick']
        liefert (None, wenn es sie nicht gibt)."""

        def exec(self, *_a):
            CTRL["menu_texts"] = [
                a.text() for a in self.actions() if not a.isSeparator()
            ]
            CTRL.setdefault("menus", []).append(CTRL["menu_texts"])
            # Warteschlange (mehrere Menues in einem Ablauf) vor Einzelwahl.
            pick = CTRL["menu_picks"].pop(0) if CTRL.get("menu_picks") else CTRL["menu_pick"]
            if pick == "*":                  # Fuzz: irgendein Eintrag
                acts = [a for a in self.actions() if not a.isSeparator()]
                return CTRL["rnd"].choice(acts + [None])
            for a in self.actions():
                if a.text() == pick:
                    return a
            return None


def _token_exec(self):
    self.count.setValue(CTRL.get("token_count", 1))
    self.side.setCurrentIndex(self.side.findData(CTRL.get("token_owner", "me")))
    return QDialog.DialogCode.Accepted


def _material_exec(self):
    """_MaterialDialog: Zeilen aus CTRL['mat_rows'] ankreuzen (None =
    abbrechen); protokolliert Titel und Befund."""
    rows = CTRL["mat_rows"].pop(0) if CTRL.get("mat_rows") else None
    if CTRL.get("rnd") is not None:          # Fuzz: zufaellige Auswahl
        n = self.listw.count()
        rows = CTRL["rnd"].sample(range(n), CTRL["rnd"].randint(0, n)) if n else []
    CTRL.setdefault("mat_titles", []).append(self.windowTitle())
    if rows is None:
        return QDialog.DialogCode.Rejected
    self.set_checked(rows)
    CTRL.setdefault("verdicts", []).append(self.verdict.text())
    return QDialog.DialogCode.Accepted


@needs_qt
class _PlayTestBase(unittest.TestCase):
    """Gemeinsame Stubs: Menues, Stapel-/Material-/Such-Dialoge und
    Rueckfragen treffen die in CTRL vorgegebene Wahl."""
    RULES = "off"

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.tmpdir, cls.db = copy_dev_db()
        ydb.ensure_schema(cls.db)          # wie beim App-Start (Migrationen)
        write_real_link_markers(cls.db)
        from yugioh_gui import playtest as pt

        cls.pt = pt
        cls.patches = [
            mock.patch.object(ydb, "cache_image", side_effect=OSError("offline")),
            # Bilder spielen fuer die Karten-Invarianten keine Rolle: immer
            # Platzhalter, kein Dekodieren, kein ImageLoader im Thread-Pool.
            mock.patch.object(pt, "lookup_card_pixmap", return_value=None),
            mock.patch.object(pt.PlayTestView, "_ensure_image", lambda *a: None),
            mock.patch.object(pt, "QMenu", _FakeMenu),
            mock.patch.object(pt._PileDialog, "exec", _pile_exec),
            mock.patch.object(pt._TopDeckDialog, "exec", _top_exec),
            mock.patch.object(
                pt.QInputDialog, "getInt",
                staticmethod(lambda *a, **k: (CTRL["getint"], True)),
            ),
            mock.patch.object(
                pt.QMessageBox, "question",
                staticmethod(lambda *a, **k: CTRL["question"]),
            ),
            mock.patch.object(
                pt._RecordSaveDialog, "exec", lambda self: CTRL["save_result"]
            ),
            mock.patch.object(pt._MaterialDialog, "exec", _material_exec),
            mock.patch.object(pt._TokenDialog, "exec", _token_exec),
            mock.patch.object(pt._StatDialog, "exec",
                              lambda self: QDialog.DialogCode.Accepted),
            mock.patch.object(pt.CardSearchDialog, "exec",
                              lambda self: QDialog.DialogCode.Accepted),
            mock.patch.object(pt.CardSearchDialog, "chosen_card_id",
                              lambda self: CTRL["search_id"]),
        ]
        for p in cls.patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        remove_tree(cls.tmpdir)

    def setUp(self):
        CTRL.clear()
        CTRL.update({
            "pile_row": 0, "pile_action": "hold", "menu_pick": None,
            "top_rows": [], "getint": 3,
            "question": self.pt.QMessageBox.StandardButton.Yes,
            "save_result": QDialog.DialogCode.Accepted,
        })
        from yugioh_gui.repository import CardRepository

        random.seed(1)
        self.v = self.pt.PlayTestView(CardRepository(self.db))
        self.v.set_rule_mode(self.RULES)
        # Fest das Referenz-Deck waehlen statt 'erstes nach Name' -- ein neu
        # angelegtes Deck in der Dev-DB soll die Tests nicht verschieben.
        idx = self.v.deck_cb.findText("RDA-Mitsu")
        self.assertGreaterEqual(idx, 0, "Dev-DB braucht das Deck 'RDA-Mitsu'")
        self.v.deck_cb.setCurrentIndex(idx)
        random.seed(1)
        self.v.reset()
        self.assertIsNotNone(self.v._deck_id, "Dev-DB braucht ein Deck")
        self.assertTrue(self.v.game.me.extra, "Test-Deck braucht ein Extra Deck")

    def tearDown(self):
        self.v.deleteLater()

    # -- Helfer -------------------------------------------------------------

    def _insts(self):
        """Alle eigenen echten Exemplare: Hand, beide Feldseiten (eigene
        Karten koennen per Effekt beim Gegner liegen), Xyz-Material, eigene
        Stapel. Spielmarken und Gegner-Karten zaehlen nicht."""
        g = self.v.game
        board = g.field_cards()
        mats = [m for c in board for m in c.materials]
        return [c for c in (list(g.me.hand) + board + mats + g.me.gy + g.me.banished)
                if c.owner == "me" and not c.token]

    def _counts(self):
        v = self.v
        ids = list(v.game.me.deck) + list(v.game.me.extra) + [c.card_id for c in self._insts()]
        return collections.Counter(ids)

    def _menu(self, inst, text):
        """Kontextmenue-Eintrag waehlen; Folge-Menues (z. B. Angriffsziel)
        stehen vorher in CTRL['menu_picks']."""
        CTRL["menu_pick"] = text
        CTRL["menu_picks"] = [text] + CTRL.get("menu_picks", [])
        self.v._on_card_context(inst, QPoint(0, 0))


class PlayTestViewTests(_PlayTestBase):
    """Der freie Sandkasten (Regeln aus): Karten gehen weder verloren noch
    verdoppeln sie sich, der Heuristik-Recorder bleibt wie gehabt."""

    # -- Tests --------------------------------------------------------------

    def test_held_card_sent_away_cannot_be_placed_again(self):
        v = self.v
        base = self._counts()
        for target in ("→ Deck (oben)", "→ Friedhof", "→ Verbannt"):
            h = v.game.me.hand[0]
            v._on_card_clicked(h)            # aufnehmen
            self._menu(h, target)            # wegschicken
            self.assertIsNone(v._held)
            v._on_zone_clicked("m0")         # darf nichts ablegen
            self.assertIsNone(v.game.me.m[0])
            self.assertEqual(self._counts(), base)

    def test_extra_deck_monster_returns_to_extra_deck(self):
        v = self.v
        n_deck, n_extra = len(v.game.me.deck), len(v.game.me.extra)
        v._open_pile("extra")                # 'hold' -> Hand + aufgenommen
        ed = v._held
        v._on_zone_clicked("e0")
        self.assertIs(v.game.emz[0], ed)
        self._menu(ed, "→ Extra-Deck")
        self.assertNotIn("→ Hand", CTRL["menu_texts"])
        self.assertNotIn("→ Deck (oben)", CTRL["menu_texts"])
        self.assertEqual(len(v.game.me.extra), n_extra)
        self.assertEqual(len(v.game.me.deck), n_deck)
        self.assertNotIn(ed.card_id, v.game.me.deck)

    def test_face_down_and_defense_reset_when_leaving_field(self):
        v = self.v
        h = v.game.me.hand[0]
        v._on_card_clicked(h)
        v._on_zone_clicked("m0")
        self._menu(h, "Verdeckt")
        self._menu(h, "DEF")
        self.assertTrue(h.face_down and h.defense)
        self._menu(h, "→ Hand")
        self.assertFalse(h.face_down or h.defense)

    # -- Recorder -------------------------------------------------------------

    def _combo_count(self):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute("SELECT COUNT(*) FROM combos").fetchone()[0]
        finally:
            conn.close()

    def _place(self, inst, zone):
        self.v._on_card_clicked(inst)
        self.v._on_zone_clicked(zone)

    def test_recorder_logs_set_when_turned_face_down(self):
        v = self.v
        v._toggle_recording()
        h = v.game.me.hand[0]
        self._place(h, "s0")
        self._menu(h, "Verdeckt")
        m = v.game.me.hand[0]
        self._place(m, "m0")
        self._menu(m, "Verdeckt")
        self.assertEqual(v._rec_log, [f"Set {h.name}", f"Set {m.name}"])

    def test_undo_after_save_does_not_resume_recording(self):
        v = self.v
        before = self._combo_count()
        v._toggle_recording()
        self._place(v.game.me.hand[0], "m0")
        v._finish_recording()                  # speichert
        self.assertEqual(self._combo_count(), before + 1)
        v.undo()
        self.assertFalse(v._recording)
        v._finish_recording()                  # kein zweites Speichern
        self.assertEqual(self._combo_count(), before + 1)

    def test_undo_after_empty_recording_stays_off(self):
        v = self.v
        v._toggle_recording()
        v.shuffle()                            # Snapshot mit recording=True
        v._finish_recording()                  # nichts protokolliert -> aus
        v.undo()
        self.assertFalse(v._recording)

    def test_saved_pieces_capped_at_deck_copies(self):
        v = self.v
        v._toggle_recording()
        h = v.game.me.hand[0]
        cid = h.card_id
        for _ in range(4):                     # dasselbe Exemplar im Kreis
            inst = next(c for c in v.game.me.hand if c.card_id == cid)
            self._place(inst, "m0")
            self._menu(inst, "→ Deck (oben)")
            v.draw()
        combo_id = v._save_recording("Kreis", None, False)
        qty = {p["card_id"]: p["needed"]
               for p in ydb.combo_coverage_collection(self.db, combo_id)["pieces"]}
        self.assertEqual(qty[cid], v._deck_copies[cid])

    def test_deck_switch_keeps_recording_when_declined(self):
        v = self.v
        if v.deck_cb.count() < 2:
            self.skipTest("braucht zwei Decks")
        v._toggle_recording()
        self._place(v.game.me.hand[0], "m0")
        deck = v._deck_id
        CTRL["question"] = self.pt.QMessageBox.StandardButton.No
        other = next(i for i in range(v.deck_cb.count())
                     if v.deck_cb.itemData(i) != deck)
        v.deck_cb.setCurrentIndex(other)
        self.assertEqual(v._deck_id, deck)
        self.assertEqual(v.deck_cb.currentData(), deck)
        self.assertTrue(v._recording and v._rec_log)

    def test_random_actions_keep_card_count(self):
        """Kurzer Fuzz: beliebige Aktionen, Kartenmenge bleibt konstant,
        uids eindeutig, kein Extra-Deck-Monster im Main Deck."""
        v = self.v
        choices = ["Offen", "Verdeckt", "ATK", "DEF", "→ Hand", "→ Friedhof",
                   "→ Verbannt", "→ Deck (oben)", "→ Extra-Deck"]
        for seed in range(4):
            rnd = random.Random(seed)
            random.seed(seed)
            v.reset()
            base = self._counts()
            ed_ids = set(v.game.me.extra)
            for step in range(300):
                r = rnd.random()
                board = (list(v.game.me.hand)
                         + [c for c in (*v.game.me.m, *v.game.me.s, *v.game.emz, v.game.me.field) if c])
                if r < 0.1:
                    v.draw()
                elif r < 0.3 and board:
                    v._on_card_clicked(rnd.choice(board))
                elif r < 0.5:
                    v._on_zone_clicked(rnd.choice(list(v._zones)))
                elif r < 0.65 and board:
                    self._menu(rnd.choice(board), rnd.choice(choices))
                elif r < 0.75:
                    CTRL["pile_row"] = rnd.randrange(50)
                    CTRL["pile_action"] = rnd.choice(["hand", "hold", "gy", "banish"])
                    v._open_pile(rnd.choice(["extra", "gy", "banished"]))
                elif r < 0.8:
                    CTRL["getint"] = max(1, min(rnd.randint(1, 5), len(v.game.me.deck)))
                    CTRL["top_rows"] = [
                        (rnd.randrange(5), rnd.choice(["hand", "gy", "banish", "bottom"]))
                        for _ in range(rnd.randint(0, 3))
                    ]
                    if v.game.me.deck:
                        v._peek_top()
                else:
                    v.undo()
                # Ohne Event-Loop wuerden deleteLater()-Widgets nie abgeraeumt.
                QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                msg = f"seed {seed}, step {step}"
                # Kein assertEqual auf Countern: dessen Diff ist bei ~60
                # Karten quälend langsam.
                diff = (self._counts() - base) + (base - self._counts())
                self.assertFalse(diff, f"{msg}: {dict(diff)}")
                uids = [i.uid for i in self._insts()]
                self.assertEqual(len(uids), len(set(uids)), msg)
                self.assertFalse(ed_ids & set(v.game.me.deck), msg)


class RulesViewTests(_PlayTestBase):
    """Spielfeld mit Regelwerk: Beschwoerungs-Menues, Material-Dialoge,
    Warnen vs. Erzwingen, Zugwechsel, Kampf, Gegner und Spielmarken --
    immer mit echten Karten aus 'RDA-Mitsu'."""
    RULES = "warn"

    # -- Helfer ---------------------------------------------------------------

    def take(self, cid):
        """Ein Exemplar der Karte auf die Hand holen (aus Deck/Hand, sonst neu)."""
        g = self.v.game
        for c in g.me.hand:
            if c.card_id == cid:
                return c
        if cid in g.me.deck:
            g.me.deck.remove(cid)
        inst = self.v._mk_inst(cid)
        if cid not in self.v._names:
            inst.name = self.v._info_of(cid)["name"]
        g.me.hand.append(inst)
        return inst

    def board(self, cid, zone):
        """Karte direkt in eine Zone legen (Ausgangsstellung eines Tests)."""
        inst = self.take(cid)
        self.v.game.remove(inst)
        self.v.game.put(zone, inst)
        return inst

    def hold_from_extra(self, cid):
        CTRL["pile_row"] = self.v.game.me.extra.index(cid)
        CTRL["pile_action"] = "hold"
        self.v._open_pile("extra")
        self.assertEqual(self.v._held.card_id, cid)
        return self.v._held

    def play(self, inst, zone, *picks):
        CTRL["menu_picks"] = list(picks)
        if self.v._held is not inst:           # erneuter Klick legt sie ab
            self.v._on_card_clicked(inst)
        self.v._on_zone_clicked(zone)

    def log(self):
        return [self.v.log_list.item(i).text() for i in range(self.v.log_list.count())]

    # -- Beschwoerungen -------------------------------------------------------

    def test_normal_summon_then_second_one_is_warned(self):
        v = self.v
        v._toggle_recording()
        bone, saji = self.take(BONE), self.take(SAJI)
        self.play(bone, "m0", "Normalbeschwörung")
        self.assertIs(v.game.me.m[0], bone)
        self.assertEqual(v.game.ns_used, 1)
        self.assertIn(bone.uid, v.game.flags["summoned"])
        self.assertEqual(CTRL["menus"][-1][:3],
                         ["Normalbeschwörung", "Setzen", "Spezialbeschwörung (Effekt)"])
        self.play(saji, "m1", "Setzen")        # zweite NS/Set: nur Warnung
        self.assertIs(v.game.me.m[1], saji)
        self.assertTrue(saji.face_down and saji.defense)
        self.assertTrue(any(line.startswith("⚠ Setzen") for line in self.log()))
        self.assertEqual(v._rec_log, [f"NS {bone.name}", f"Set {saji.name}"])

    def test_enforce_asks_and_respects_the_answer(self):
        v = self.v
        v.set_rule_mode("enforce")
        v.game.ns_used = 1
        bone = self.take(BONE)
        CTRL["question"] = self.pt.QMessageBox.StandardButton.No
        self.play(bone, "m0", "Normalbeschwörung")
        self.assertIsNone(v.game.me.m[0])
        self.assertIn(bone, v.game.me.hand)
        self.assertTrue(self.log()[-1].startswith("✋"))
        CTRL["question"] = self.pt.QMessageBox.StandardButton.Yes
        self.play(bone, "m0", "Normalbeschwörung")
        self.assertIs(v.game.me.m[0], bone)
        self.assertIn("per Effekt erlaubt", self.log()[-1])

    def test_rules_off_keeps_sandbox_behaviour(self):
        v = self.v
        v.set_rule_mode("off")
        v._toggle_recording()
        bone = self.take(BONE)
        self.play(bone, "e0")                  # kein Menue, keine Pruefung
        self.assertIs(v.game.emz[0], bone)
        self.assertNotIn("menus", CTRL)
        self.assertEqual(v._rec_log, [f"NS {bone.name}"])

    def test_tribute_summon_sends_tributes_to_gy(self):
        v = self.v
        v._toggle_recording()
        a, b = self.board(BONE, "m0"), self.board(SAJI, "m1")
        lub = self.take(LUBELLION)
        CTRL["mat_rows"] = [[0, 1]]
        self.play(lub, "m2", "Normalbeschwörung")
        self.assertIs(v.game.me.m[2], lub)
        self.assertEqual(v.game.me.gy, [a, b])
        self.assertTrue(CTRL["verdicts"][-1].startswith("✓"))
        self.assertEqual(v._rec_log[-1], f"NS {lub.name} (Tribute: {a.name} + {b.name})")

    def test_synchro_summon_uses_materials_and_records_formula(self):
        v = self.v
        v._toggle_recording()
        soul, bone = self.board(SOUL, "m0"), self.board(BONE, "m1")
        blade = self.hold_from_extra(CRIMSON_BLADE)
        self.assertEqual(v._hint_zones(), {"m2", "m3", "m4", "e0", "e1"})
        CTRL["mat_rows"] = [[1, 0]]
        self.play(blade, "e0", "Synchro-Beschwörung")
        self.assertIs(v.game.emz[0], blade)
        self.assertEqual({c.uid for c in v.game.me.gy}, {soul.uid, bone.uid})
        formula = f"Synchro: {soul.name} (3) + {bone.name} (4) -> {blade.name} (7)"
        self.assertEqual(v._rec_log, [formula])
        self.assertEqual(ydb.lint_combo_steps(v._rec_log), [])
        self.assertEqual(set(v._rec_used.values()), {SOUL, BONE, CRIMSON_BLADE})
        self.assertEqual(v._rec_last_ed, CRIMSON_BLADE)
        # Falsche Stufensumme: warnen, aber beschwoeren.
        v.undo()
        self.board(SAJI, "m2")
        blade = self.hold_from_extra(CRIMSON_BLADE)
        CTRL["mat_rows"] = [[0, 1, 2]]
        self.play(blade, "m3", "Synchro-Beschwörung")
        self.assertIn("Stufensumme", CTRL["verdicts"][-1])
        self.assertIs(v.game.me.m[3], blade)
        self.assertTrue(any("Stufensumme" in line for line in self.log()))

    def test_link_summon_needs_emz_or_arrow(self):
        v = self.v
        v.set_rule_mode("enforce")
        masq = self.hold_from_extra(MASQ)
        self.play(masq, "e0", "Spezialbeschwörung (Effekt)")
        self.assertIs(v.game.emz[0], masq)
        a, b = self.board(BONE, "m3"), self.board(SAJI, "m4")
        knight = self.hold_from_extra(KNIGHT)
        self.assertEqual(v._hint_zones(), {"m0", "m2"})   # Masquerenas Pfeile
        CTRL["question"] = self.pt.QMessageBox.StandardButton.No
        CTRL["mat_rows"] = [[0, 1]]
        self.play(knight, "m1", "Link-Beschwörung")
        self.assertIsNone(v.game.me.m[1])
        self.assertIn(knight, v.game.me.hand)
        CTRL["mat_rows"] = [[0, 1]]
        self.play(knight, "m0", "Link-Beschwörung")
        self.assertIs(v.game.me.m[0], knight)
        self.assertEqual(v.game.me.gy, [a, b])

    def test_xyz_materials_attach_detach_and_follow_the_monster(self):
        v = self.v
        xyz = query(self.db, "SELECT id FROM cards WHERE frame_type = 'xyz' "
                             "AND level = 4 ORDER BY id LIMIT 1")[0][0]
        v.game.me.extra.append(xyz)
        v._extra_ids.add(xyz)
        v._names[xyz] = v._info_of(xyz)["name"]
        v._toggle_recording()
        a, b = self.board(BONE, "m0"), self.board(SAJI, "m1")
        held = self.hold_from_extra(xyz)
        CTRL["mat_rows"] = [[0, 1]]
        self.play(held, "m2", "Xyz-Beschwörung")
        self.assertEqual(held.materials, [a, b])
        self.assertEqual(v.game.me.gy, [])
        self.assertTrue(v._rec_log[-1].startswith("Xyz: "))
        self.assertTrue(v._rec_log[-1].endswith("(R4)"))
        CTRL["pile_row"], CTRL["pile_action"] = 1, "gy"
        self._menu(held, "Xyz-Material abhängen…")
        self.assertEqual((held.materials, v.game.me.gy), ([a], [b]))
        v.undo()                                           # Material ist Undo-fest
        restored = v.game.me.m[2]
        self.assertEqual([m.uid for m in restored.materials], [a.uid, b.uid])
        self._menu(restored, "→ Extra-Deck")
        self.assertEqual({m.uid for m in v.game.me.gy}, {a.uid, b.uid})
        self.assertIn(xyz, v.game.me.extra)

    # -- Zauber/Fallen, Positionen --------------------------------------------

    def test_trap_must_be_set_and_wait_a_turn(self):
        v = self.v
        trap = self.take(IMPERM)
        self.play(trap, "s0", "Setzen")
        self.assertTrue(trap.face_down)
        self.assertIn(trap.uid, v.game.flags["set"])
        self._menu(trap, "Aktivieren")
        self.assertFalse(trap.face_down)                   # warnen: passiert trotzdem
        self.assertTrue(any("in diesem Zug gesetzt" in line for line in self.log()))
        warnings = len([line for line in self.log() if line.startswith("⚠")])
        quick = self.take(CALLED)
        self.play(quick, "s1", "Aktivieren")               # Schnellzauber: erlaubt
        self.assertIs(v.game.me.s[1], quick)
        self.assertEqual(len([line for line in self.log() if line.startswith("⚠")]),
                         warnings)

    def test_only_a_field_spell_replaces_the_field_spell(self):
        v = self.v
        fields = query(self.db, "SELECT id FROM cards WHERE type = 'Spell Card' "
                                "AND race = 'Field' ORDER BY id LIMIT 2")
        old, new = (self.take(r[0]) for r in fields)
        self.play(old, "field", "Aktivieren")
        self.assertIs(v.game.me.field, old)
        bone = self.take(BONE)
        self.play(bone, "field", "Aktivieren")             # Monster: Zone belegt
        self.assertIs(v.game.me.field, old)
        self.assertIn(bone, v.game.me.hand)
        v._held = None
        self.play(new, "field", "Aktivieren")
        self.assertIs(v.game.me.field, new)
        self.assertEqual(v.game.me.gy, [old])

    def test_position_change_not_in_summon_turn(self):
        bone = self.take(BONE)
        self.play(bone, "m0", "Normalbeschwörung")
        self._menu(bone, "DEF")
        self.assertTrue(bone.defense)
        self.assertTrue(any("in diesem Zug beschworen" in line for line in self.log()))

    # -- Zug, Kampf, Gegner -----------------------------------------------------

    def test_turn_cycle_draws_and_skips_battle_on_turn_one(self):
        v = self.v
        v.next_phase()
        self.assertEqual(v.game.phase, "EP")               # Zug 1: keine BP
        v._toggle_recording()
        hand = len(v.game.me.hand)
        v.end_turn()
        self.assertEqual((v.game.turn, v.game.my_turn), (2, False))
        self.assertEqual(v.end_turn_btn.text(), "Gegnerzug beenden")
        v.end_turn()
        self.assertEqual((v.game.turn, v.game.phase, v.game.my_turn), (3, "DP", True))
        self.assertEqual(len(v.game.me.hand), hand + 1)
        self.assertEqual(v._rec_log, ["Draw 1"])
        v.undo()
        self.assertEqual((v.game.turn, len(v.game.me.hand)), (2, hand))

    def test_hand_limit_discard(self):
        v = self.v
        v._toggle_recording()
        for _ in range(3):
            v.draw()
        self.assertEqual(len(v.game.me.hand), 8)
        CTRL["mat_rows"] = [[0, 1]]
        first_two = v.game.me.hand[:2]
        v.end_turn()
        self.assertEqual(len(v.game.me.hand), 6)
        self.assertEqual(v.game.me.gy, first_two)
        self.assertEqual(v._rec_log[-2:], [f"Discard {c.name}" for c in first_two])
        self.assertFalse(v.game.my_turn)

    def _to_battle_phase(self):
        v = self.v
        v.end_turn()
        v.end_turn()
        v._set_phase("BP")
        self.assertEqual((v.game.turn, v.game.phase), (3, "BP"))

    def test_battle_against_opponent_monster_and_direct(self):
        v = self.v
        bone = self.board(BONE, "m0")
        CTRL["search_id"] = SAJI
        CTRL["menu_picks"] = ["Monsterzone (offen, Angriff)"]
        v._add_opp_card()
        target = v.game.opp.m[0]
        self.assertEqual((target.card_id, target.owner), (SAJI, "opp"))
        self._to_battle_phase()
        diff = (v._info_of(BONE)["atk"] or 0) - (v._info_of(SAJI)["atk"] or 0)
        self.assertGreater(diff, 0, "Bone Archfiend muss staerker sein als Saji")
        CTRL["menu_picks"] = [target.name]
        self._menu(bone, "Angreifen…")
        self.assertEqual(v.game.opp.lp, 8000 - diff)
        self.assertEqual(v.game.opp.gy, [target])          # Gegner-GY, nicht deiner
        self.assertNotIn(target, v.game.me.gy)
        CTRL["menu_picks"] = ["Direkter Angriff"]
        self._menu(bone, "Angreifen…")
        self.assertTrue(any("schon angegriffen" in line for line in self.log()))

    def test_opponent_handtrap_is_recorded_but_no_piece(self):
        v = self.v
        v._toggle_recording()
        CTRL["search_id"] = 14558127                      # Ash Blossom
        CTRL["menu_picks"] = ["Aus der Hand aktivieren (Handtrap → Friedhof)"]
        v._add_opp_card()
        ash = v.game.opp.gy[-1]
        self.assertEqual(ash.owner, "opp")
        self.assertEqual(v._rec_log, [f"Eff {ash.name} (opp)"])
        self.assertEqual(v._rec_used, {})
        self.assertEqual(ydb.lint_combo_steps(v._rec_log), [])

    def test_tokens_vanish_and_are_no_pieces(self):
        v = self.v
        v._toggle_recording()
        CTRL["token_count"] = 2
        v._create_token()
        tokens = [c for c in v.game.me.m if c is not None]
        self.assertEqual(len(tokens), 2)
        self.assertTrue(all(t.token and t.card_id < 0 for t in tokens))
        knight = self.hold_from_extra(KNIGHT)
        CTRL["mat_rows"] = [[0, 1]]
        self.play(knight, "e0", "Link-Beschwörung")
        self.assertIs(v.game.emz[0], knight)
        self.assertEqual(v.game.me.gy, [])                 # Marken verschwinden
        self.assertEqual(set(v._rec_used.values()), {KNIGHT})

    def test_stat_changes_count_in_battle(self):
        v = self.v
        bone = self.board(BONE, "m0")

        def set_mods(dlg):
            dlg.spins[0].setValue(1000)
            return QDialog.DialogCode.Accepted
        with mock.patch.object(self.pt._StatDialog, "exec", set_mods):
            self._menu(bone, "Werte ändern…")
        self.assertEqual(bone.atk_mod, 1000)
        self._to_battle_phase()
        CTRL["menu_picks"] = ["Direkter Angriff"]
        self._menu(v.game.me.m[0], "Angreifen…")
        self.assertEqual(v.game.opp.lp, 8000 - ((v._info_of(BONE)["atk"] or 0) + 1000))

    # -- Fuzz ---------------------------------------------------------------------

    def test_random_play_with_rules_keeps_card_count(self):
        """Fuzz mit Regeln (warnen/erzwingen): beliebige Menues, Material-
        Auswahl, Zugwechsel, Kampf, Marken und Gegner-Karten -- eigene
        Karten gehen nie verloren, uids bleiben eindeutig, nichts Fremdes
        landet in den eigenen Stapeln."""
        v = self.v
        Q = self.pt.QMessageBox.StandardButton
        for seed in range(3):
            rnd = random.Random(seed)
            CTRL["rnd"] = rnd
            CTRL["menu_pick"] = "*"
            random.seed(seed)
            v.set_rule_mode(["warn", "enforce", "warn"][seed])
            v.reset()
            base = self._counts()
            ed_ids = set(v.game.me.extra)
            for step in range(250):
                CTRL["question"] = rnd.choice([Q.Yes, Q.No])
                r = rnd.random()
                g = v.game
                cards = list(g.me.hand) + g.field_cards()
                if r < 0.06:
                    v.draw()
                elif r < 0.25 and cards:
                    v._on_card_clicked(rnd.choice(cards))
                elif r < 0.45:
                    v._on_zone_clicked(rnd.choice(list(v._zones)))
                elif r < 0.6 and cards:
                    v._on_card_context(rnd.choice(cards), QPoint(0, 0))
                elif r < 0.68:
                    CTRL["pile_row"] = rnd.randrange(50)
                    CTRL["pile_action"] = rnd.choice(["hand", "hold", "gy", "banish",
                                                      "field"])
                    v._open_pile(rnd.choice(["extra", "gy", "banished", "ogy"]))
                elif r < 0.74:
                    rnd.choice([v.next_phase, v.end_turn])()
                elif r < 0.78:
                    CTRL["token_count"] = rnd.randint(1, 3)
                    CTRL["token_owner"] = rnd.choice(["me", "opp"])
                    v._create_token()
                elif r < 0.82:
                    CTRL["search_id"] = rnd.choice([SAJI, BONE, IMPERM, MASQ])
                    v._add_opp_card()
                else:
                    v.undo()
                QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                msg = f"seed {seed}, step {step}"
                diff = (self._counts() - base) + (base - self._counts())
                self.assertFalse(diff, f"{msg}: {dict(diff)}")
                uids = [i.uid for i in self._insts()]
                self.assertEqual(len(uids), len(set(uids)), msg)
                self.assertFalse(ed_ids & set(v.game.me.deck), msg)
                foreign = [c for c in g.me.gy + g.me.banished + g.me.hand
                           if c.owner != "me" or c.token]
                self.assertFalse(foreign, msg)


if __name__ == "__main__":
    unittest.main()
