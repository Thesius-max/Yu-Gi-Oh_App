"""
test_playtest.py
================
Offscreen-Tests fuer den Spielfeld-Tab (PlayTestView): Karten duerfen weder
verloren gehen noch sich verdoppeln, Extra-Deck-Monster bleiben im Extra
Deck, der Recorder protokolliert sauber.

Wie test_yugioh_db: gegen eine Temp-Kopie der Dev-DB, keine Mock-Karten,
kein Netz (cache_image wird abgeschaltet). Modale Dialoge/Menues werden
durch Stubs ersetzt, die eine vorgegebene Wahl treffen.

    python -m unittest test_playtest
"""

from __future__ import annotations

import collections
import os
import random
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import yugioh_db as ydb

DEV_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yugioh.sqlite3")
HAS_DEV_DB = os.path.exists(DEV_DB)

try:
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtWidgets import QApplication, QDialog, QMenu

    HAS_QT = True
except ImportError:  # pragma: no cover
    HAS_QT = False


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
            for a in self.actions():
                if a.text() == CTRL["menu_pick"]:
                    return a
            return None


@unittest.skipUnless(HAS_DEV_DB and HAS_QT, "Dev-DB oder PySide6 fehlt")
class PlayTestViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.tmpdir = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmpdir, "dev.sqlite3")
        shutil.copy(DEV_DB, cls.db)
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
        ]
        for p in cls.patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

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
        self.assertIsNotNone(self.v._deck_id, "Dev-DB braucht ein Deck")
        self.assertTrue(self.v._extra, "Test-Deck braucht ein Extra Deck")

    def tearDown(self):
        self.v.deleteLater()

    # -- Helfer -------------------------------------------------------------

    def _insts(self):
        v = self.v
        return (list(v._hand)
                + [c for c in (*v._mzones, *v._szones, *v._emz, v._field) if c]
                + v._gy + v._banished)

    def _counts(self):
        v = self.v
        ids = list(v._deck) + list(v._extra) + [c.card_id for c in self._insts()]
        return collections.Counter(ids)

    def _menu(self, inst, text):
        CTRL["menu_pick"] = text
        self.v._on_card_context(inst, QPoint(0, 0))

    # -- Tests --------------------------------------------------------------

    def test_held_card_sent_away_cannot_be_placed_again(self):
        v = self.v
        base = self._counts()
        for target in ("→ Deck (oben)", "→ Friedhof", "→ Verbannt"):
            h = v._hand[0]
            v._on_card_clicked(h)            # aufnehmen
            self._menu(h, target)            # wegschicken
            self.assertIsNone(v._held)
            v._on_zone_clicked("m0")         # darf nichts ablegen
            self.assertIsNone(v._mzones[0])
            self.assertEqual(self._counts(), base)

    def test_extra_deck_monster_returns_to_extra_deck(self):
        v = self.v
        n_deck, n_extra = len(v._deck), len(v._extra)
        v._open_pile("extra")                # 'hold' -> Hand + aufgenommen
        ed = v._held
        v._on_zone_clicked("e0")
        self.assertIs(v._emz[0], ed)
        self._menu(ed, "→ Extra-Deck")
        self.assertNotIn("→ Hand", CTRL["menu_texts"])
        self.assertNotIn("→ Deck (oben)", CTRL["menu_texts"])
        self.assertEqual(len(v._extra), n_extra)
        self.assertEqual(len(v._deck), n_deck)
        self.assertNotIn(ed.card_id, v._deck)

    def test_face_down_and_defense_reset_when_leaving_field(self):
        v = self.v
        h = v._hand[0]
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
        h = v._hand[0]
        self._place(h, "s0")
        self._menu(h, "Verdeckt")
        m = v._hand[0]
        self._place(m, "m0")
        self._menu(m, "Verdeckt")
        self.assertEqual(v._rec_log, [f"Set {h.name}", f"Set {m.name}"])

    def test_undo_after_save_does_not_resume_recording(self):
        v = self.v
        before = self._combo_count()
        v._toggle_recording()
        self._place(v._hand[0], "m0")
        v._finish_recording()                  # speichert
        self.assertEqual(self._combo_count(), before + 1)
        v.undo()
        self.assertFalse(v._recording)
        v._finish_recording()                  # kein zweites Speichern
        self.assertEqual(self._combo_count(), before + 1)

    def test_saved_pieces_capped_at_deck_copies(self):
        v = self.v
        v._toggle_recording()
        h = v._hand[0]
        cid = h.card_id
        for _ in range(4):                     # dasselbe Exemplar im Kreis
            inst = next(c for c in v._hand if c.card_id == cid)
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
        self._place(v._hand[0], "m0")
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
            ed_ids = set(v._extra)
            for step in range(300):
                r = rnd.random()
                board = (list(v._hand)
                         + [c for c in (*v._mzones, *v._szones, *v._emz, v._field) if c])
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
                    CTRL["getint"] = max(1, min(rnd.randint(1, 5), len(v._deck)))
                    CTRL["top_rows"] = [
                        (rnd.randrange(5), rnd.choice(["hand", "gy", "banish", "bottom"]))
                        for _ in range(rnd.randint(0, 3))
                    ]
                    if v._deck:
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
                self.assertFalse(ed_ids & set(v._deck), msg)


if __name__ == "__main__":
    unittest.main()
