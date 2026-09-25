"""
tests/_support.py
=================
Gemeinsame Test-Infrastruktur -- vorher in jeder Testdatei kopiert.

Strategie (CLAUDE.md): mit der Dev-DB testen, keine Mock-Karten erfinden.
Jeder schreibende Test bekommt eine eigene Temp-Kopie von yugioh.sqlite3;
die Dev-DB selbst wird nie mutiert. Fehlt die Dev-DB, werden DB-/GUI-Tests
uebersprungen (reine Funktionen laufen trotzdem). YGO_DEV_DB ueberschreibt
den Pfad (z.B. um das Skip-Verhalten zu pruefen).

Bausteine:
  DevDbTestCase   -- Temp-Kopie je Test + Kartenwahl aus echten Daten
  QtTestCase      -- dazu QApplication (offscreen), kein Netz, keine
                     lokalen Bilder, gestubbte modale Dialoge (UiStubs)
  UiStubs         -- QMessageBox/QInputDialog/QFileDialog/QDialog.exec
                     ersetzt; Antworten steuerbar, Meldungen protokolliert
  pump()          -- Hintergrund-Tasks (DbTask) abwarten + Signale zustellen
  api_payload_from_db() -- API-Antwort aus einer DB rekonstruieren (fuer
                     build_database ohne Netz und ohne erfundene Karten)
"""

from __future__ import annotations

import contextlib
import gc
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import yugioh_db as ydb

REPO_ROOT = Path(__file__).resolve().parent.parent
DEV_DB = os.environ.get("YGO_DEV_DB") or str(REPO_ROOT / "yugioh.sqlite3")
HAS_DEV_DB = os.path.exists(DEV_DB)

try:
    from PySide6.QtCore import QEvent, QThreadPool
    from PySide6.QtGui import QPixmapCache
    from PySide6.QtWidgets import (
        QApplication, QDialog, QFileDialog, QInputDialog, QMessageBox
    )

    HAS_QT = True
except ImportError:  # pragma: no cover
    HAS_QT = False

needs_dev_db = unittest.skipUnless(
    HAS_DEV_DB, "Dev-DB (yugioh.sqlite3) nicht vorhanden"
)
needs_qt = unittest.skipUnless(HAS_DEV_DB and HAS_QT, "Dev-DB oder PySide6 fehlt")

TEMP_PREFIX = "ygo_test_"


# ---------------------------------------------------------------------------
# Temp-Kopien der Dev-DB
# ---------------------------------------------------------------------------

def copy_dev_db() -> tuple[str, str]:
    """(temp_ordner, db_pfad) einer frischen Kopie der Dev-DB."""
    folder = tempfile.mkdtemp(prefix=TEMP_PREFIX)
    db = os.path.join(folder, "test.sqlite3")
    shutil.copy(DEV_DB, db)
    return folder, db


# Echte Link-Pfeile (YGOPRODeck, geprueft 2026-09-25) fuer die Link-Monster
# im Extra Deck von 'RDA-Mitsu' -- die Dev-DB ist aelter als die Spalte
# link_markers und hat dort NULL.
REAL_LINK_MARKERS = {
    29301450: "Left,Right",                 # S:P Little Knight
    65741786: "Bottom-Left,Bottom-Right",   # I:P Masquerena
}


def write_real_link_markers(db: str) -> dict[int, str]:
    """Echte Link-Pfeile in eine (migrierte) Kopie schreiben."""
    conn = ydb._connect(db)
    try:
        conn.executemany("UPDATE cards SET link_markers = ? WHERE id = ?",
                         [(m, cid) for cid, m in REAL_LINK_MARKERS.items()])
        conn.commit()
    finally:
        conn.close()
    return dict(REAL_LINK_MARKERS)


def remove_tree(folder: str) -> None:
    """Temp-Ordner loeschen und PRUEFEN, dass er weg ist. Unter Windows
    haelt eine offene SQLite-Verbindung die Datei fest -- rmtree mit
    ignore_errors liesse dann still 26-MB-Leichen liegen."""
    for attempt in range(5):
        gc.collect()  # vergessene Connection-Objekte schliessen
        shutil.rmtree(folder, ignore_errors=True)
        if not os.path.exists(folder):
            return
        time.sleep(0.05 * (attempt + 1))
    left = [str(p) for p in Path(folder).rglob("*")]
    raise AssertionError(
        f"Temp-Ordner {folder} nicht loeschbar (offene Datei?): {left}"
    )


def query(db: str, sql: str, args=()) -> list[sqlite3.Row]:
    """Nur-lesende Hilfsabfrage mit eigener, sofort geschlossener Verbindung."""
    conn = ydb._connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def scalar(db: str, sql: str, args=()):
    rows = query(db, sql, args)
    return rows[0][0] if rows else None


def api_payload_from_db(
    db: str, de_override=None
) -> tuple[list[dict], dict[int, dict]]:
    """Rekonstruiert eine YGOPRODeck-Antwort (EN-Liste + DE-dict) aus einer
    DB -- echte Karten statt erfundener. de_override(card_id, name_de,
    desc_de) -> (name, desc) erlaubt, die 'API'-Werte einzelner Karten
    gezielt abweichen zu lassen."""
    sets: dict[int, list[dict]] = {}
    for r in query(db, "SELECT card_id, set_name, set_code, rarity FROM card_sets"):
        sets.setdefault(r["card_id"], []).append({
            "set_name": r["set_name"], "set_code": r["set_code"],
            "set_rarity": r["rarity"],
        })
    cards, de = [], {}
    for r in query(db, "SELECT * FROM cards"):
        cards.append({
            "id": r["id"], "name": r["name"], "type": r["type"],
            "frameType": r["frame_type"], "desc": r["description"],
            "atk": r["atk"], "def": r["def"], "level": r["level"],
            "race": r["race"], "attribute": r["attribute"],
            "archetype": r["archetype"], "scale": r["scale"],
            "linkval": r["link_value"], "card_sets": sets.get(r["id"], []),
            "linkmarkers": (r["link_markers"] or "").split(",")
                           if r["link_markers"] else None,
        })
        name_de, desc_de = r["name_de"], r["desc_de"]
        if de_override is not None:
            name_de, desc_de = de_override(r["id"], name_de, desc_de)
        if name_de is not None:
            de[r["id"]] = {"id": r["id"], "name": name_de, "desc": desc_de}
    return cards, de


# ---------------------------------------------------------------------------
# DB-Testbasis
# ---------------------------------------------------------------------------

@needs_dev_db
class DevDbTestCase(unittest.TestCase):
    """Eigene Temp-Kopie der Dev-DB je Test (self.db); ensure_schema ist
    gelaufen. Helfer waehlen echte Karten aus der Kopie."""

    def setUp(self):
        self._dir, self.db = copy_dev_db()
        self.addCleanup(remove_tree, self._dir)
        ydb.ensure_schema(self.db)
        # Kombos saet jeder Test selbst: falls die Dev-DB (z.B. nach einem
        # App-Start aus dem Repo-Root) doch welche hat, in der KOPIE leeren.
        conn = ydb._connect(self.db)
        try:
            conn.execute("DELETE FROM combos")        # Kaskade: Bausteine/Schritte
            conn.commit()
        finally:
            conn.close()

    # -- Abfragen -----------------------------------------------------------
    # Zahlen immer aus der Kopie ableiten statt sie festzuschreiben: die
    # Dev-DB veraendert sich durch normale Nutzung (App-Start aus dem
    # Repo-Root oeffnet sie direkt).

    def query(self, sql, args=()):
        return query(self.db, sql, args)

    def scalar(self, sql, args=()):
        return scalar(self.db, sql, args)

    def count(self, table: str, where: str = "1", args=()) -> int:
        return self.scalar(f"SELECT COUNT(*) FROM {table} WHERE {where}", args)

    def n_refs(self) -> int:
        """Anzahl der Referenz-Decks (Korpus) in der Kopie."""
        return len(ydb.list_reference_decks(self.db))

    def set_meta(self, key: str, value: str) -> None:
        """meta-Eintrag in der Kopie setzen (z.B. einen definierten
        Ausgangsstand fuer app_version/db_version)."""
        conn = ydb._connect(self.db)
        try:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                         (key, value))
            conn.commit()
        finally:
            conn.close()

    def pick_ids(self, where: str, n: int = 1, args=()) -> list[int]:
        """n echte Karten-IDs, die 'where' erfuellen (feste Reihenfolge)."""
        rows = self.query(
            f"SELECT id FROM cards WHERE {where} ORDER BY id LIMIT ?",
            (*args, n),
        )
        self.assertEqual(len(rows), n, f"Dev-DB hat keine {n} Karten fuer: {where}")
        return [r[0] for r in rows]

    REAL_LINK_MARKERS = REAL_LINK_MARKERS

    def set_real_link_markers(self) -> dict[int, str]:
        """Traegt die echten Link-Pfeile in die Kopie ein (Rueckgabe wie
        REAL_LINK_MARKERS)."""
        return write_real_link_markers(self.db)

    # Kombos werden in Tests selbst gesaet; 'frisch' = in keiner Kombo, damit
    # Rollen-Kopien vorhersagbar bleiben.
    _FRESH = "id NOT IN (SELECT card_id FROM combo_cards)"

    def main_ids(self, n: int = 1) -> list[int]:
        return self.pick_ids(
            f"frame_type IN ('effect','normal') AND {self._FRESH}", n
        )

    def spell_ids(self, n: int = 1) -> list[int]:
        return self.pick_ids(f"frame_type = 'spell' AND {self._FRESH}", n)

    def extra_ids(self, n: int = 1) -> list[int]:
        return self.pick_ids(
            f"frame_type IN ('fusion','synchro','xyz','link') AND {self._FRESH}", n
        )

    def unowned_ids(self, n: int = 1) -> list[int]:
        return self.pick_ids(
            "frame_type IN ('effect','normal') AND id NOT IN "
            "(SELECT card_id FROM collection) AND id NOT IN "
            "(SELECT card_id FROM deck_cards)", n
        )

    def translated_card(self) -> int:
        """Karte mit API-Uebersetzung (DE != EN), ohne eigenen Override."""
        return self.pick_ids(
            "name_de IS NOT NULL AND name_de != name AND id NOT IN "
            "(SELECT card_id FROM card_translations)"
        )[0]

    def untranslated_card(self) -> int:
        return self.pick_ids("name_de IS NULL AND frame_type = 'effect'")[0]

    def own_deck(self, name: str = "RDA-Mitsu") -> int:
        deck = self.scalar(
            "SELECT deck_id FROM decks WHERE name = ? AND kind IS NULL", (name,)
        )
        self.assertIsNotNone(deck, f"Dev-DB braucht das Deck {name!r}")
        return deck

    def display_name(self, card_id: int) -> str:
        return self.scalar(
            "SELECT COALESCE(name_de, name) FROM cards WHERE id = ?", (card_id,)
        )

    def seed_combo(self, name: str, pieces: dict[int, str | None],
                   deck_id=None, steps=None) -> int:
        """Kombo mit Bausteinen {card_id: rolle|None} in die Kopie saeen."""
        combo = ydb.create_combo(self.db, name, deck_id=deck_id)
        for cid, role in pieces.items():
            ydb.add_combo_card(self.db, combo, cid, 1)
            if role:
                ydb.set_combo_card_role(self.db, combo, cid, role)
        if steps:
            ydb.set_combo_steps(self.db, combo, steps)
        return combo


class CardIdsTestCase(DevDbTestCase):
    """DevDbTestCase mit den festen Karten-IDs der urspruenglichen
    DbTests (main_id, main_id2, extra_id, unowned_id, unowned_id2) --
    so bleiben die umgezogenen Tests unveraendert."""

    def setUp(self):
        super().setUp()
        one = "SELECT id FROM cards WHERE frame_type IN ('effect','normal') "
        self.main_id = self.scalar(one + "LIMIT 1")
        self.main_id2 = self.scalar(one + "AND id != ? LIMIT 1", (self.main_id,))
        self.extra_id = self.scalar(
            "SELECT id FROM cards WHERE frame_type IN "
            "('fusion','synchro','xyz','link') LIMIT 1"
        )
        # Karten, die garantiert NICHT im Bestand liegen (fuer
        # deterministische Sammlungs-Abdeckung).
        unowned = self.query(
            "SELECT id FROM cards WHERE id NOT IN "
            "(SELECT card_id FROM collection) LIMIT 2"
        )
        self.unowned_id, self.unowned_id2 = unowned[0]["id"], unowned[1]["id"]


# ---------------------------------------------------------------------------
# Qt: modale Dialoge ersetzen, Hintergrund-Tasks abwarten
# ---------------------------------------------------------------------------

class UiStubs:
    """Ersetzt alle modalen Stellen der GUI auf Klassenebene (jede View
    importiert dieselben Klassenobjekte -- ein Patch deckt alles ab).

    - messages: protokollierte (art, titel, text) aller Meldungen
    - question: Antwort auf QMessageBox.question (Standard: Yes)
    - texts/ints/save_paths/open_paths: Antwort-Warteschlangen fuer
      QInputDialog.getText/getInt bzw. QFileDialog; leer = Abbruch
    - dialogs: {Klassenname: handler(dlg) -> DialogCode} fuer QDialog.exec
      eigener Dialoge; unbekannte Dialoge landen in 'unexpected' und
      gelten als abgebrochen (nie blockieren)."""

    def __init__(self):
        self.messages: list[tuple[str, str, str]] = []
        self.question = QMessageBox.StandardButton.Yes
        self.texts: list[tuple[str, bool]] = []
        self.ints: list[tuple[int, bool]] = []
        self.save_paths: list[tuple[str, str]] = []
        self.open_paths: list[tuple[str, str]] = []
        self.dialogs: dict = {}
        self.unexpected: list[str] = []
        self._patches: list = []

    # -- Protokoll-Helfer ---------------------------------------------------

    def titles(self, kind: str | None = None) -> list[str]:
        return [t for k, t, _ in self.messages if kind in (None, k)]

    def last_text(self) -> str:
        return self.messages[-1][2] if self.messages else ""

    # -- Stubs --------------------------------------------------------------

    def _msg(self, kind):
        def fn(*args, **_kw):
            title = args[1] if len(args) > 1 else ""
            text = args[2] if len(args) > 2 else ""
            self.messages.append((kind, title, text))
            if kind == "question":
                return self.question
            return QMessageBox.StandardButton.Ok
        return staticmethod(fn)

    @staticmethod
    def _queue(q, empty):
        def fn(*_a, **_kw):
            return q.pop(0) if q else empty
        return staticmethod(fn)

    def _box_exec(self):
        stubs = self

        def fn(box, *_a):
            stubs.messages.append(("box", box.windowTitle(), box.text()))
            return 0
        return fn

    def _dialog_exec(self):
        stubs = self

        def fn(dlg, *_a):
            name = type(dlg).__name__
            handler = stubs.dialogs.get(name)
            if handler is None:
                stubs.unexpected.append(name)
                return QDialog.DialogCode.Rejected
            return handler(dlg)
        return fn

    def start(self) -> None:
        targets = [
            (QMessageBox, "information", self._msg("information")),
            (QMessageBox, "warning", self._msg("warning")),
            (QMessageBox, "critical", self._msg("critical")),
            (QMessageBox, "question", self._msg("question")),
            (QMessageBox, "exec", self._box_exec()),
            (QInputDialog, "getText", self._queue(self.texts, ("", False))),
            (QInputDialog, "getInt", self._queue(self.ints, (0, False))),
            (QFileDialog, "getSaveFileName", self._queue(self.save_paths, ("", ""))),
            (QFileDialog, "getOpenFileName", self._queue(self.open_paths, ("", ""))),
            (QDialog, "exec", self._dialog_exec()),
        ]
        for cls, name, repl in targets:
            p = mock.patch.object(cls, name, repl)
            p.start()
            self._patches.append(p)

    def stop(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._patches.clear()


def pump(rounds: int = 3) -> None:
    """Hintergrund-Tasks (DbTask im globalen Pool) abwarten und ihre
    Queued-Signale im UI-Thread zustellen."""
    QThreadPool.globalInstance().waitForDone(10_000)
    from yugioh_gui.images import image_pool
    image_pool().waitForDone(10_000)
    for _ in range(rounds):
        QApplication.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@needs_qt
class QtTestCase(DevDbTestCase):
    """DevDbTestCase + offscreen-QApplication. Kein Netz (cache_image
    wirft), keine lokalen Bilder (IMAGE_DIR = leerer Temp-Ordner), keine
    Spielfeld-Nachlade-Threads; modale Dialoge ueber self.ui (UiStubs).
    Mit self.track(widget) angelegte Widgets werden am Testende abgebaut."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = QApplication.instance() or QApplication([])
        cls._img_dir = tempfile.mkdtemp(prefix=TEMP_PREFIX + "img_")
        from yugioh_gui import playtest
        cls._class_patches = [
            mock.patch.object(ydb, "cache_image", side_effect=OSError("offline")),
            mock.patch.object(ydb, "IMAGE_DIR", cls._img_dir),
            mock.patch.object(playtest.PlayTestView, "_ensure_image", lambda *a: None),
        ]
        for p in cls._class_patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in reversed(cls._class_patches):
            p.stop()
        remove_tree(cls._img_dir)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        QPixmapCache.clear()
        self.ui = UiStubs()
        self.ui.start()
        self.addCleanup(self._check_no_unexpected_dialogs)
        self.addCleanup(self.ui.stop)
        self.addCleanup(pump)

    def _check_no_unexpected_dialogs(self):
        self.assertEqual(self.ui.unexpected, [], "unerwarteter modaler Dialog")

    @contextlib.contextmanager
    def assert_no_resource_warnings(self):
        """Offen gelassene Dateien/Handles im Block melden. ResourceWarnings
        entstehen im Destruktor und koennen dort nicht werfen -- darum
        mitschneiden und nach gc.collect() pruefen."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            yield
            gc.collect()
        leaks = [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)]
        self.assertEqual(leaks, [], "offen gelassene Ressourcen")

    def track(self, widget):
        """Widget am Testende schliessen und abraeumen (vor dem Loeschen der
        Temp-DB -- Cleanups laufen in umgekehrter Reihenfolge)."""
        def dispose():
            widget.close()
            widget.deleteLater()
        self.addCleanup(dispose)
        return widget
