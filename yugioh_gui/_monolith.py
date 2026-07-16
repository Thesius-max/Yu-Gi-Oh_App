"""
yugioh_gui.py
=============
PySide6-Grundgeruest fuer die Yu-Gi-Oh-Sammlungs- und Nachschlage-App.

Greift auf die lokale SQLite-Datenbank aus yugioh_db.py zu. Beide Dateien
gehoeren zusammen und liegen im selben Verzeichnis.

Vorab einmalig die Datenbank anlegen:
    python yugioh_db.py build

Dann starten:
    python yugioh_gui.py

Benoetigt PySide6:
    pip install PySide6

Aufbau (drei Spalten, frei skalierbar via Splitter):
    links   -- Suche + Filter (Volltext, Typ, Attribut, Archetyp, Level, ATK)
    mitte   -- Trefferliste
    rechts  -- Detailansicht: Bild, Werte, Kartentext, "zur Sammlung"
"""

from __future__ import annotations

import datetime
import itertools
import os
import random
import shutil
import sys

from PySide6.QtCore import (
    Qt, QEvent, QMarginsF, QObject, QPoint, QPointF, QRunnable, QSettings,
    QThreadPool, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QDesktopServices, QFont, QGuiApplication, QImage,
    QKeySequence, QPageLayout, QPageSize, QPainter, QPalette, QPdfWriter,
    QPen, QPixmap, QPixmapCache, QShortcut, QTextCursor, QTextDocument,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QProgressDialog, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

import yugioh_db as ydb
from .combos import ComboView
from .deck import DeckView
from .manual import HelpView
from .carddetail import (
    CardDetailDialog, CardSearchDialog, DetailPanel, edit_card_translation,
)
from .collection import CollectionView
from .deck_dialogs import (
    ComboFromDeckDialog, DeckCorpusDiffDialog, ReferenceDeckDialog,
)

from .exporting import (
    resolve_export_path as _resolve_export_path,
    write_text_file as _write_text_file,
    write_text_pdf as _write_text_pdf,
)
from .images import (
    IMAGE_URL,
    CardImageView as _CardImageView,
    HoverCardPreview as _HoverCardPreview,
    card_back_pixmap as _card_back_pixmap,
    lookup_card_pixmap as _lookup_card_pixmap,
    placeholder_pixmap as _placeholder_pixmap,
    scale_pixmap as _scale_pixmap,
)
from .labels import (
    ATTR_DE as _ATTR_DE, CATEGORY_DE as _CATEGORY_DE,
    CATEGORY_ORDER as _CATEGORY_ORDER, COLLECTION_CARD_ID as _COLLECTION_CARD_ID,
    PIECE_ROLE_DATA as _PIECE_ROLE_DATA, RACE_DE as _RACE_DE,
    ROLE_DE as _ROLE_DE, SIM_COPIES_DATA as _SIM_COPIES_DATA,
    TYPE_DE as _TYPE_DE, ZONE_LABELS,
    import_report_lines as _import_report_lines, pct as _pct,
)
from .repository import CardRepository
from .tasks import (
    DbTask as _DbTask, DbTaskSignals as _DbTaskSignals,
    ImageLoader as _ImageLoader, ImageSignals as _ImageSignals,
)
from .theme import (
    GROUP_HEADER_BG as _GROUP_HEADER_BG, GROUP_HEADER_FG as _GROUP_HEADER_FG,
    UNTRANSLATED_FG as _UNTRANSLATED_FG, apply_theme,
)







# ---------------------------------------------------------------------------
# Spielfeld-Test (Solitaire-Goldfishing) — eigener Tab
# ---------------------------------------------------------------------------

_CARD_W, _CARD_H = 74, 106     # Brettkarten-Groesse (aufrecht)
_ZONE_W = _ZONE_H = 120        # Zelle: fasst Karte aufrecht UND um 90° gedreht




class _CardInst:
    """Eine Karte auf dem Brett/Hand: Identitaet + Zustand (offen/verdeckt,
    ATK/DEF). Jede Kopie ist ein eigenes Exemplar; 'uid' identifiziert es
    stabil ueber Undo-Snapshots hinweg (der Kombo-Recorder zaehlt darueber
    benutzte Exemplare). 'origin' merkt sich beim Aufnehmen aus einem Stapel
    die Herkunft (ED/GY/…) bis zum Ablegen — daraus wird 'SS <X> (<Ort>)'."""
    __slots__ = ("card_id", "name", "face_down", "defense", "uid", "origin")
    _uid_counter = itertools.count(1)

    def __init__(self, card_id: int, name: str,
                 face_down: bool = False, defense: bool = False,
                 uid: int | None = None, origin: str | None = None):
        self.card_id = card_id
        self.name = name
        self.face_down = face_down
        self.defense = defense
        self.uid = next(self._uid_counter) if uid is None else uid
        self.origin = origin


class _BoardCard(QLabel):
    """Anzeige einer Karte auf dem Brett/Hand. Linksklick = aufnehmen/anwaehlen,
    Rechtsklick = Kontextmenue. Das fertige Pixmap (offen/verdeckt, ggf. gedreht)
    liefert die View."""
    clicked = Signal(object)            # _CardInst
    context = Signal(object, QPoint)    # _CardInst, globale Position

    def __init__(self, inst: _CardInst, pixmap: QPixmap,
                 held: bool = False, parent=None):
        super().__init__(parent)
        self.inst = inst
        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.size())
        self.setToolTip(inst.name)
        if held:
            self.setStyleSheet("border: 2px solid #d4af37;")

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self.context.emit(self.inst, e.globalPosition().toPoint())
        elif e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.inst)


class _Zone(QFrame):
    """Eine Feldzelle (Monster/Zauber/Feld/EMZ) oder ein Stapel
    (Deck/GY/Verbannt/Extra). Linksklick auf leere Flaeche meldet zone_key."""
    clicked = Signal(str)

    def __init__(self, zone_key: str, w: int, h: int, parent=None):
        super().__init__(parent)
        self.zone_key = zone_key
        self.setObjectName("BoardZone")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedSize(w, h)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(2, 2, 2, 2)
        self._lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_content(self, widget) -> None:
        while self._lay.count():
            old = self._lay.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
        if widget is not None:
            self._lay.addWidget(widget, alignment=Qt.AlignmentFlag.AlignCenter)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.zone_key)
        super().mousePressEvent(e)


class _PileDialog(QDialog):
    """Listet die Karten eines Stapels; Auswahl (Doppelklick/Button) landet in
    self.selected (Zeilenindex) und self.action (Aktions-Key). Doppelklick
    nimmt die erste Aktion. Der Aufrufer kennt die Reihenfolge."""

    def __init__(self, title: str, labels: list[str], parent=None,
                 actions: list[tuple[str, str]] | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 480)
        self.selected = -1
        self.action: str | None = None
        self._actions = actions or [("Auf die Hand", "hand")]
        lay = QVBoxLayout(self)
        self.listw = QListWidget()
        for s in labels:
            QListWidgetItem(s, self.listw)
        self.listw.itemDoubleClicked.connect(lambda _item: self._take())
        lay.addWidget(self.listw, stretch=1)
        row = QHBoxLayout()
        row.addStretch()
        for label, key in self._actions:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, k=key: self._take(k))
            row.addWidget(btn)
        close = QPushButton("Schließen")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        lay.addLayout(row)

    def _take(self, key: str | None = None) -> None:
        if self.listw.currentRow() >= 0:
            self.selected = self.listw.currentRow()
            self.action = key or self._actions[0][1]
            self.accept()


class _TopDeckDialog(QDialog):
    """Zeigt die obersten Deck-Karten (oberste zuerst). Jede Aktion nimmt die
    gewaehlte Karte aus der Liste und landet in self.moves (card_id, Aktion) —
    in Klick-Reihenfolge. Was beim Schliessen in self.cards uebrig ist, legt
    der Aufrufer unveraendert zurueck nach oben."""

    _ACTIONS = [("Auf die Hand", "hand"), ("→ Friedhof", "gy"),
                ("→ Verbannt", "banish"), ("Nach unten", "bottom")]

    def __init__(self, cards: list[int], names: dict[int, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Deck-Top ansehen ({len(cards)})")
        self.resize(420, 400)
        self.cards = list(cards)
        self._names = names
        self.moves: list[tuple[int, str]] = []
        lay = QVBoxLayout(self)
        hint = QLabel("Oberste Karte zuerst. Der Rest geht in dieser "
                      "Reihenfolge zurück auf das Deck.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.listw = QListWidget()
        for cid in self.cards:
            QListWidgetItem(names.get(cid, str(cid)), self.listw)
        self.listw.itemDoubleClicked.connect(lambda _item: self._do("hand"))
        lay.addWidget(self.listw, stretch=1)
        row = QHBoxLayout()
        row.addStretch()
        for label, key in self._ACTIONS:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, k=key: self._do(k))
            row.addWidget(btn)
        close = QPushButton("Fertig")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def _do(self, action: str) -> None:
        row = self.listw.currentRow()
        if row < 0:
            return
        self.moves.append((self.cards.pop(row), action))
        self.listw.takeItem(row)
        if not self.cards:
            self.accept()


class _RecordSaveDialog(QDialog):
    """Speichern-Dialog des Kombo-Recorders: Name, Boss-Vorschlag und die
    protokollierten Schritte als Vorschau. Ergebnis: Accepted = speichern,
    Rejected = weiter aufzeichnen, DISCARD = Aufzeichnung verwerfen."""

    DISCARD = 2

    def __init__(self, deck_name: str, steps: list[str],
                 boss_choices: list[tuple[int, str]], default_boss: int | None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Aufzeichnung als Kombo speichern")
        self.resize(480, 520)
        lay = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit(f"{deck_name} — neue Linie")
        form.addRow("Name:", self.name_edit)
        self.boss_cb = QComboBox()
        self.boss_cb.addItem("(kein Boss)", None)
        for cid, name in boss_choices:
            self.boss_cb.addItem(name, cid)
        if default_boss is not None:
            idx = self.boss_cb.findData(default_boss)
            self.boss_cb.setCurrentIndex(max(idx, 0))
        form.addRow("Boss:", self.boss_cb)
        form.addRow("Heimat-Deck:", QLabel(deck_name))
        lay.addLayout(form)

        self.notes_check = QCheckBox("Starthand/Endboard in die Notizen "
                                     "übernehmen (Start:/End:)")
        self.notes_check.setChecked(True)
        lay.addWidget(self.notes_check)

        lay.addWidget(QLabel(f"Protokollierte Schritte ({len(steps)}) — "
                             "Entwurf, im Kombos-Tab nachschärfen:"))
        preview = QListWidget()
        for i, s in enumerate(steps, start=1):
            QListWidgetItem(f"{i}  {s}", preview)
        preview.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        lay.addWidget(preview, stretch=1)

        row = QHBoxLayout()
        discard = QPushButton("Verwerfen")
        discard.clicked.connect(lambda: self.done(self.DISCARD))
        row.addWidget(discard)
        row.addStretch()
        cont = QPushButton("Weiter aufzeichnen")
        cont.clicked.connect(self.reject)
        row.addWidget(cont)
        save = QPushButton("Speichern")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        row.addWidget(save)
        lay.addLayout(row)

    def values(self) -> tuple[str, int | None, bool]:
        name = self.name_edit.text().strip() or "Aufgezeichnete Linie"
        return name, self.boss_cb.currentData(), self.notes_check.isChecked()


class PlayTestView(QWidget):
    """Solitaire-Spielfeld: ein eigenes Deck laden, Starthand ziehen und Karten
    frei auslegen (Klick-Aufnehmen + Klick-Ablegen, Rechtsklick fuer Zustaende).
    Stapel-Dialoge (Deck/Extra/GY/Verbannt) koennen Karten auch direkt
    aufnehmen ('hold': in die Hand + angewaehlt, naechster Zonen-Klick legt ab)
    oder in GY/Verbannt schicken; 'Top ansehen…' blaettert die obersten
    Deck-Karten (Excavate/Mill). Jede Zustandsaenderung ist per Undo-Stack
    (Snapshots, Ctrl+Z) rueckgaengig machbar. Der **Kombo-Recorder**
    ('● Aufzeichnen') protokolliert Zuege als Notation-Entwurf (NS/SS-
    Heuristik: erste Handbeschwoerung = NS; Herkunft aus _CardInst.origin)
    und speichert sie als Kombo mit Heimat-Deck = Spielfeld-Deck, Bausteinen
    (Exemplare via uid gezaehlt) und Start:/End:-Notizen — Nachschaerfen im
    Kombos-Tab (open_combo_callback). Reiner Sandkasten — keine Regeln, kein
    Gegner, keine Persistenz; der Spielzustand lebt nur in der GUI."""

    _PLACEMENT_LABELS = {"field": "Feld", "e": "EMZ", "m": "Mon", "s": "Z/F"}
    _UNDO_MAX = 80

    def __init__(self, repo: CardRepository):
        super().__init__()
        self.repo = repo
        self._deck_id: int | None = None
        self._names: dict[int, str] = {}
        self._deck: list[int] = []
        self._extra: list[int] = []
        self._hand: list[_CardInst] = []
        self._mzones: list = [None] * 5
        self._szones: list = [None] * 5
        self._emz: list = [None, None]
        self._field = None
        self._gy: list[_CardInst] = []
        self._banished: list[_CardInst] = []
        self._held: _CardInst | None = None
        self._undo: list[dict] = []
        self._detail_dialog: CardDetailDialog | None = None
        self.open_combo_callback = None   # setzt MainWindow

        # Kombo-Recorder (alles wandert mit in die Undo-Snapshots).
        self._recording = False
        self._rec_log: list[str] = []          # Schritte (Notation-Entwurf)
        self._rec_used: dict[int, int] = {}    # Exemplar-uid -> card_id
        self._rec_ns_used = False               # NS-Heuristik: 1x je Aufnahme
        self._rec_last_ed: int | None = None    # letzter ED-SS = Boss-Vorschlag
        self._rec_start: list[str] = []         # Starthand bei Aufnahmebeginn

        # Nachladen fehlender Bilder (meist sind sie lokal gecacht).
        self._loading_imgs: set[int] = set()
        self._img_signals = _ImageSignals()
        self._img_signals.loaded.connect(self._on_img_loaded)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(40)
        self._render_timer.timeout.connect(self._render)

        outer = QVBoxLayout(self)

        # -- Werkzeugleiste --------------------------------------------------
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Deck:"))
        self.deck_cb = QComboBox()
        self.deck_cb.currentIndexChanged.connect(self._on_deck_selected)
        bar.addWidget(self.deck_cb, stretch=1)
        self.hand_size_cb = QComboBox()
        self.hand_size_cb.addItem("5 (First)", 5)
        self.hand_size_cb.addItem("6 (Second)", 6)
        bar.addWidget(self.hand_size_cb)
        for text, slot in (
            ("Neue Starthand", self._reset_clicked),
            ("Ziehen", self.draw),
            ("Mischen", self.shuffle),
            ("Deck durchsuchen…", self._search_deck),
            ("Top ansehen…", self._peek_top),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        self.rec_btn = QPushButton("● Aufzeichnen")
        self.rec_btn.setToolTip(
            "Züge als Kombo-Schritte protokollieren und als Kombo mit "
            "diesem Deck als Heimat-Deck speichern"
        )
        self.rec_btn.clicked.connect(self._toggle_recording)
        bar.addWidget(self.rec_btn)
        self.undo_btn = QPushButton("↶ Rückgängig")
        self.undo_btn.setToolTip("Letzte Aktion zurücknehmen (Strg+Z)")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.undo)
        bar.addWidget(self.undo_btn)
        QShortcut(
            QKeySequence.StandardKey.Undo, self, self.undo,
            context=Qt.ShortcutContext.WidgetWithChildrenShortcut,
        )
        outer.addLayout(bar)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        # -- Feld-Grid -------------------------------------------------------
        self._zones: dict[str, _Zone] = {}
        grid = QGridLayout()
        grid.setSpacing(4)

        def zone(key, r, c, rowspan=1, colspan=1):
            z = _Zone(key, _ZONE_W, _ZONE_H)
            z.clicked.connect(self._on_zone_clicked)
            self._zones[key] = z
            grid.addWidget(z, r, c, rowspan, colspan)

        # Links: Feldzauber + Extra-Deck. Mitte (Spalten 1-5): EMZ / Monster /
        # Zauber-Fallen. Rechts: Deck / Friedhof / Verbannt.
        zone("field", 0, 0)
        zone("extra", 2, 0)
        zone("e0", 0, 2)
        zone("e1", 0, 4)
        for i in range(5):
            zone(f"m{i}", 1, 1 + i)
        for i in range(5):
            zone(f"s{i}", 2, 1 + i)
        # Rechte Spalte von unten nach oben: Deck, Friedhof, Verbannt.
        zone("banished", 0, 6)
        zone("gy", 1, 6)
        zone("deck", 2, 6)
        board = QWidget()
        board.setLayout(grid)
        outer.addWidget(board, alignment=Qt.AlignmentFlag.AlignHCenter)

        # -- Hand ------------------------------------------------------------
        self.hand_label = QLabel("Hand")
        self.hand_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(self.hand_label)
        self._hand_host = QWidget()
        self._hand_row = QHBoxLayout(self._hand_host)
        self._hand_row.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(_CARD_H + 24)
        scroll.setWidget(self._hand_host)
        outer.addWidget(scroll)
        outer.addStretch()

        self.refresh()

    # -- Deck-Auswahl / Laden ----------------------------------------------

    def refresh(self) -> None:
        """Deck-Liste aktuell halten (z. B. nach Anlegen/Loeschen von Decks)."""
        keep = self.deck_cb.currentData()
        self.deck_cb.blockSignals(True)
        self.deck_cb.clear()
        decks = ydb.list_decks(self.repo.db_path) if self.repo.exists() else []
        for d in decks:
            self.deck_cb.addItem(d["name"], d["deck_id"])
        idx = self.deck_cb.findData(keep)
        if idx < 0 and self.deck_cb.count():
            idx = 0
        self.deck_cb.setCurrentIndex(max(idx, 0))
        self.deck_cb.blockSignals(False)
        new_id = self.deck_cb.currentData()
        if new_id != self._deck_id:
            self._deck_id = new_id
            self.reset()
        else:
            self._render()

    def _on_deck_selected(self, _index: int) -> None:
        self._deck_id = self.deck_cb.currentData()
        self.reset()

    def _hand_size(self) -> int:
        return self.hand_size_cb.currentData() or 5

    def _reset_clicked(self) -> None:
        """Button-Variante von reset(): warnt, wenn eine Aufzeichnung läuft."""
        if self._recording and self._rec_log:
            answer = QMessageBox.question(
                self, "Aufzeichnung verwerfen?",
                "Eine Kombo-Aufzeichnung läuft. Eine neue Starthand "
                f"verwirft die {len(self._rec_log)} protokollierten "
                "Schritte — fortfahren?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.reset()

    def reset(self) -> None:
        """Feld/Hand leeren, Deck+Extra neu laden, mischen, Starthand ziehen.
        Beendet eine laufende Aufzeichnung (der Spielzustand, auf den sich
        das Protokoll bezieht, ist danach weg)."""
        self._stop_recording()
        self._held = None
        self._undo = []
        self._hand = []
        self._mzones = [None] * 5
        self._szones = [None] * 5
        self._emz = [None, None]
        self._field = None
        self._gy = []
        self._banished = []
        if self._deck_id is not None and self.repo.exists():
            data = ydb.deck_play_lists(self.repo.db_path, self._deck_id)
            self._names = data["names"]
            self._deck = list(data["main"])
            self._extra = list(data["extra"])
            random.shuffle(self._deck)
            for _ in range(self._hand_size()):
                if self._deck:
                    self._hand.append(self._mk_inst(self._deck.pop(0)))
        else:
            self._names, self._deck, self._extra = {}, [], []
        self._render()

    def _mk_inst(self, card_id: int) -> _CardInst:
        return _CardInst(card_id, self._names.get(card_id, str(card_id)))

    # -- Undo (Snapshot-Stack) ----------------------------------------------

    @staticmethod
    def _snap_inst(c: _CardInst | None):
        return None if c is None else (
            c.card_id, c.name, c.face_down, c.defense, c.uid, c.origin
        )

    def _push_undo(self) -> None:
        """Kompletten Spielzustand als wertbasierten Snapshot sichern (die
        _CardInst-Exemplare werden beim Undo neu gebaut, behalten aber ihre
        uid). Das Recorder-Protokoll wandert mit — Undo nimmt also auch
        protokollierte Schritte zurück."""
        si = self._snap_inst
        self._undo.append({
            "deck": list(self._deck), "extra": list(self._extra),
            "hand": [si(c) for c in self._hand],
            "m": [si(c) for c in self._mzones],
            "s": [si(c) for c in self._szones],
            "e": [si(c) for c in self._emz],
            "field": si(self._field),
            "gy": [si(c) for c in self._gy],
            "ban": [si(c) for c in self._banished],
            "rec": (self._recording, list(self._rec_log),
                    dict(self._rec_used), self._rec_ns_used,
                    self._rec_last_ed, list(self._rec_start)),
        })
        del self._undo[:-self._UNDO_MAX]

    def undo(self) -> None:
        if not self._undo:
            return
        s = self._undo.pop()

        def mk(t):
            return None if t is None else _CardInst(*t)

        self._deck = list(s["deck"])
        self._extra = list(s["extra"])
        self._hand = [mk(t) for t in s["hand"]]
        self._mzones = [mk(t) for t in s["m"]]
        self._szones = [mk(t) for t in s["s"]]
        self._emz = [mk(t) for t in s["e"]]
        self._field = mk(s["field"])
        self._gy = [mk(t) for t in s["gy"]]
        self._banished = [mk(t) for t in s["ban"]]
        (self._recording, self._rec_log, self._rec_used,
         self._rec_ns_used, self._rec_last_ed, self._rec_start) = s["rec"]
        self._held = None
        self._render()

    # -- Kombo-Recorder -------------------------------------------------------

    def _rec(self, text: str, inst: _CardInst | None = None) -> None:
        """Protokolliert einen Schritt (nur während der Aufzeichnung) und
        merkt das benutzte Exemplar für die Bausteinliste."""
        if not self._recording:
            return
        self._rec_log.append(text)
        if inst is not None:
            self._rec_used[inst.uid] = inst.card_id

    def _rec_pile_take(self, inst: _CardInst, action: str, source: str) -> None:
        """Protokolliert eine Stapel-Entnahme. 'hold' erzeugt hier noch
        keinen Schritt — die Herkunft steckt in inst.origin und wird erst
        beim Ablegen zu 'SS <X> (<Ort>)'."""
        if action == "hand":
            self._rec(f"Add {inst.name} ({source})", inst)
        elif action == "gy":
            self._rec(f"Send {inst.name} ({source} -> GY)", inst)
        elif action == "banish":
            self._rec(f"Banish {inst.name} ({source})", inst)

    def _rec_placement(self, inst: _CardInst, zone_key: str) -> None:
        """Protokolliert das Ablegen: Act/Set für Zauber/Fallen-Zonen,
        'SS (<Ort>)' für aufgenommene Stapel-Karten, sonst die NS/SS-
        Heuristik (die erste Handbeschwörung der Aufzeichnung ist NS —
        eine pro Zug —, alle weiteren SS)."""
        if not self._recording:
            return
        src, inst.origin = inst.origin, None
        if zone_key == "field" or zone_key.startswith("s"):
            kw = "Set" if inst.face_down else "Act"
            where = f" ({src})" if src else ""
            self._rec(f"{kw} {inst.name}{where}", inst)
        elif src:
            self._rec(f"SS {inst.name} ({src})", inst)
            if src == "ED":
                self._rec_last_ed = inst.card_id
        elif not self._rec_ns_used:
            self._rec(f"NS {inst.name}", inst)
            self._rec_ns_used = True
        else:
            self._rec(f"SS {inst.name} (Hand)", inst)

    def _toggle_recording(self) -> None:
        if self._recording:
            self._finish_recording()
            return
        if self._deck_id is None:
            return
        self._recording = True
        self._rec_log = []
        self._rec_used = {}
        self._rec_ns_used = False
        self._rec_last_ed = None
        self._rec_start = [c.name for c in self._hand]
        self._render()

    def _stop_recording(self) -> None:
        self._recording = False
        self._rec_log = []
        self._rec_used = {}
        self._rec_ns_used = False
        self._rec_last_ed = None
        self._rec_start = []

    def _end_board_text(self) -> str:
        """Offene Feldkarten als 'End:'-Zeile (verdeckte nur gezählt)."""
        cards = [c for c in (*self._mzones, *self._emz, self._field,
                             *self._szones) if c is not None]
        names = [c.name for c in cards if not c.face_down]
        sets = sum(1 for c in cards if c.face_down)
        if sets:
            names.append(f"{sets} Set")
        return " + ".join(names) if names else "—"

    def _finish_recording(self) -> None:
        """'■ Speichern…': Dialog zeigen; speichern, weiter aufzeichnen
        oder verwerfen."""
        if not self._rec_log:
            self._stop_recording()
            self._render()
            return
        boss_ids: list[int] = []
        for cid in self._rec_used.values():
            if cid not in boss_ids:
                boss_ids.append(cid)
        choices = [(cid, self._names.get(cid, str(cid))) for cid in boss_ids]
        dlg = _RecordSaveDialog(
            self.deck_cb.currentText(), list(self._rec_log), choices,
            self._rec_last_ed, self,
        )
        result = dlg.exec()
        if result == QDialog.DialogCode.Accepted:
            name, boss_id, with_notes = dlg.values()
            combo_id = self._save_recording(name, boss_id, with_notes)
            if self.open_combo_callback is not None:
                self.open_combo_callback(combo_id)
        elif result == _RecordSaveDialog.DISCARD:
            self._stop_recording()
            self._render()
        # Rejected ('Weiter aufzeichnen'): Zustand unverändert lassen.

    def _save_recording(self, name: str, boss_id: int | None,
                        with_notes: bool) -> int:
        """Schreibt die Aufzeichnung als Kombo mit Heimat-Deck =
        Spielfeld-Deck: Bausteine (benutzte Exemplare je Karte, via uid
        gezählt), Schritte als Notation-Entwurf, optional Start:/End:-
        Notizen. Danach ist der Recorder wieder aus."""
        db = self.repo.db_path
        combo_id = ydb.create_combo(db, name, deck_id=self._deck_id)
        counts: dict[int, int] = {}
        for cid in self._rec_used.values():
            counts[cid] = counts.get(cid, 0) + 1
        for cid, qty in counts.items():
            ydb.add_combo_card(db, combo_id, cid, qty)
        ydb.set_combo_steps(db, combo_id, list(self._rec_log))
        if boss_id is not None:
            ydb.set_combo_boss(db, combo_id, boss_id)
        if with_notes:
            start = ", ".join(self._rec_start) if self._rec_start else "—"
            notes = f"Start: {start}\nEnd: {self._end_board_text()}"
            ydb.update_combo(db, combo_id, name, None, notes)
        self._stop_recording()
        self._render()
        return combo_id

    # -- Aktionen -----------------------------------------------------------

    def draw(self) -> None:
        if self._deck:
            self._push_undo()
            self._hand.append(self._mk_inst(self._deck.pop(0)))
            if self._recording:
                # Mehrfach-Ziehen zu 'Draw n' zusammenfassen.
                if self._rec_log and self._rec_log[-1].startswith("Draw "):
                    n = int(self._rec_log[-1].split()[1])
                    self._rec_log[-1] = f"Draw {n + 1}"
                else:
                    self._rec_log.append("Draw 1")
            self._render()

    def shuffle(self) -> None:
        if self._deck:
            self._push_undo()
            random.shuffle(self._deck)
            self._render()

    def _dispose(self, inst: _CardInst, action: str,
                 source: str | None = None) -> None:
        """Bringt eine bereits entnommene Karte ans Aktions-Ziel. 'hold' legt
        sie in die Hand UND nimmt sie auf — der naechste Zonen-Klick legt sie
        ab; so kann kein Exemplar verloren gehen. 'source' merkt sich dabei
        die Herkunft fuer den Recorder ('SS <X> (<Ort>)')."""
        if action == "gy":
            self._gy.append(inst)
        elif action == "banish":
            self._banished.append(inst)
        else:  # "hand" oder "hold"
            self._hand.append(inst)
            if action == "hold":
                self._held = inst
                inst.origin = source

    def _search_deck(self) -> None:
        if not self._deck:
            return
        pairs = sorted(
            ((self._names.get(c, str(c)), c) for c in self._deck),
            key=lambda x: x[0],
        )
        dlg = _PileDialog(
            "Deck durchsuchen", [p[0] for p in pairs], self,
            actions=[("Auf die Hand", "hand"), ("Aufnehmen", "hold"),
                     ("→ Friedhof", "gy"), ("→ Verbannt", "banish")],
        )
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected >= 0:
            self._push_undo()
            cid = pairs[dlg.selected][1]
            self._deck.remove(cid)
            inst = self._mk_inst(cid)
            self._dispose(inst, dlg.action, source="Deck")
            self._rec_pile_take(inst, dlg.action, "Deck")
            random.shuffle(self._deck)
            self._render()

    def _open_pile(self, kind: str) -> None:
        if kind == "extra":
            labels = [self._names.get(c, str(c)) for c in self._extra]
            # Extra-Deck-Monster wollen aufs Feld: Aufnehmen ist Default.
            actions = [("Aufnehmen", "hold"), ("Auf die Hand", "hand"),
                       ("→ Friedhof", "gy"), ("→ Verbannt", "banish")]
        else:
            pile = self._gy if kind == "gy" else self._banished
            labels = [c.name for c in pile]
            other = ("→ Verbannt", "banish") if kind == "gy" else ("→ Friedhof", "gy")
            actions = [("Auf die Hand", "hand"), ("Aufnehmen", "hold"), other]
        if not labels:
            return
        titles = {"extra": "Extra-Deck", "gy": "Friedhof", "banished": "Verbannt"}
        dlg = _PileDialog(titles[kind], labels, self, actions=actions)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected >= 0:
            self._push_undo()
            i = dlg.selected
            if kind == "extra":
                inst = self._mk_inst(self._extra.pop(i))
            else:
                pile = self._gy if kind == "gy" else self._banished
                inst = pile.pop(i)
            src = {"extra": "ED", "gy": "GY", "banished": "Banished"}[kind]
            self._dispose(inst, dlg.action, source=src)
            self._rec_pile_take(inst, dlg.action, src)
            self._render()

    def _peek_top(self) -> None:
        """Die obersten N Deck-Karten ansehen (Excavate/Mill): Karten einzeln
        auf Hand/GY/Verbannt/nach unten verteilen, der Rest geht in gleicher
        Reihenfolge zurueck nach oben."""
        if not self._deck:
            return
        n, ok = QInputDialog.getInt(
            self, "Deck-Top ansehen", "Wie viele Karten ansehen?",
            3, 1, min(10, len(self._deck)),
        )
        if not ok:
            return
        self._push_undo()
        top = self._deck[:n]
        del self._deck[:n]
        dlg = _TopDeckDialog(top, self._names, self)
        dlg.exec()
        for cid, action in dlg.moves:
            if action == "bottom":
                self._deck.append(cid)
                self._rec(f"Send {self._names.get(cid, str(cid))} "
                          "(Deck -> Bottom)")
            else:
                inst = self._mk_inst(cid)
                self._dispose(inst, action)
                if action == "gy":
                    self._rec(f"Mill {inst.name}", inst)
                elif action == "banish":
                    self._rec(f"Banish {inst.name} (Deck)", inst)
                else:
                    self._rec(f"Add {inst.name} (Deck)", inst)
        self._deck[0:0] = dlg.cards   # Rest zurueck, Reihenfolge erhalten
        if not dlg.moves:
            self._undo.pop()          # nichts passiert -> kein Undo-Schritt
        self._render()

    # -- Bewegen (Klick-Aufnehmen + Ablegen) -------------------------------

    def _on_card_clicked(self, inst: _CardInst) -> None:
        if self._held is inst:
            self._held = None
        elif inst in self._hand or self._on_field(inst):
            self._held = inst
        self._render()

    def _on_zone_clicked(self, zone_key: str) -> None:
        if zone_key == "deck":
            self.draw()
            return
        if zone_key in ("gy", "banished", "extra"):
            self._open_pile(zone_key)
            return
        if self._held is None:
            return
        if zone_key == "field":
            if self._field is None:
                self._push_undo()
                from_board = self._on_field(self._held)
                self._remove_inst(self._held)
                self._field = self._held
                if not from_board:   # Umstellen auf dem Feld ist kein Schritt
                    self._rec_placement(self._field, zone_key)
                self._held = None
        else:
            arr, i = self._slot_ref(zone_key)
            if arr[i] is None:
                self._push_undo()
                from_board = self._on_field(self._held)
                self._remove_inst(self._held)
                arr[i] = self._held
                if not from_board:
                    self._rec_placement(arr[i], zone_key)
                self._held = None
        self._render()

    def _slot_ref(self, zone_key: str):
        arr = {"m": self._mzones, "s": self._szones, "e": self._emz}[zone_key[0]]
        return arr, int(zone_key[1:])

    def _on_field(self, inst: _CardInst) -> bool:
        if inst is self._field:
            return True
        return any(inst is c for c in (*self._mzones, *self._szones, *self._emz))

    def _remove_inst(self, inst: _CardInst) -> None:
        """Entfernt ein Exemplar aus Hand/Zonen/GY/Verbannt (per Identitaet)."""
        if inst in self._hand:
            self._hand.remove(inst)
            return
        for arr in (self._mzones, self._szones, self._emz):
            for i, c in enumerate(arr):
                if c is inst:
                    arr[i] = None
                    return
        if inst is self._field:
            self._field = None
            return
        for pile in (self._gy, self._banished):
            if inst in pile:
                pile.remove(inst)
                return

    def _on_card_context(self, inst: _CardInst, pos: QPoint) -> None:
        menu = QMenu(self)
        in_hand = inst in self._hand
        a_face = a_pos = a_hand = None
        if not in_hand:
            a_face = menu.addAction("Offen" if inst.face_down else "Verdeckt")
            a_pos = menu.addAction("ATK" if inst.defense else "DEF")
            a_hand = menu.addAction("→ Hand")
        a_gy = menu.addAction("→ Friedhof")
        a_ban = menu.addAction("→ Verbannt")
        a_deck = menu.addAction("→ Deck (oben)")
        menu.addSeparator()
        a_det = menu.addAction("Details…")
        chosen = menu.exec(pos)
        if chosen is None:
            return
        if chosen is a_det:
            self._open_detail(inst.card_id)
            return
        self._push_undo()
        here = "Hand" if in_hand else "Field"
        if chosen is a_face:
            was_set = inst.face_down
            inst.face_down = not inst.face_down
            # Aufdecken in der Zauber/Fallen- oder Feldzone = Aktivierung.
            if was_set and not inst.face_down and (
                inst is self._field
                or any(c is inst for c in self._szones)
            ):
                self._rec(f"Act {inst.name}", inst)
        elif chosen is a_pos:
            inst.defense = not inst.defense
        elif chosen is a_hand:
            self._remove_inst(inst); self._hand.append(inst)
            self._rec(f"Send {inst.name} (Field -> Hand)", inst)
        elif chosen is a_gy:
            self._remove_inst(inst); self._gy.append(inst)
            self._rec(f"Discard {inst.name}" if in_hand
                      else f"Send {inst.name} (Field -> GY)", inst)
        elif chosen is a_ban:
            self._remove_inst(inst); self._banished.append(inst)
            self._rec(f"Banish {inst.name} ({here})", inst)
        elif chosen is a_deck:
            self._remove_inst(inst); self._deck.insert(0, inst.card_id)
            self._rec(f"Send {inst.name} ({here} -> Deck)", inst)
        self._render()

    def _open_detail(self, card_id: int) -> None:
        if self._detail_dialog is None:
            self._detail_dialog = CardDetailDialog(self.repo, self)
        self._detail_dialog.load(card_id)
        self._detail_dialog.show()
        self._detail_dialog.raise_()
        self._detail_dialog.activateWindow()

    # -- Bilder -------------------------------------------------------------

    def _pixmap_for(self, inst: _CardInst) -> QPixmap:
        if inst.face_down:
            pm = _card_back_pixmap(_CARD_W, _CARD_H)
        else:
            pm = _lookup_card_pixmap(inst.card_id, _CARD_W, _CARD_H, "board")
            if pm is None:
                self._ensure_image(inst.card_id)
                pm = _placeholder_pixmap(inst.name, _CARD_W, _CARD_H)
        if inst.defense:
            pm = pm.transformed(
                QTransform().rotate(90), Qt.TransformationMode.SmoothTransformation
            )
        return pm

    def _ensure_image(self, card_id: int) -> None:
        if card_id in self._loading_imgs:
            return
        self._loading_imgs.add(card_id)
        QThreadPool.globalInstance().start(
            _ImageLoader(card_id, IMAGE_URL.format(card_id), self._img_signals)
        )

    def _on_img_loaded(self, card_id: int, img: QImage) -> None:
        QPixmapCache.insert(
            f"board:{card_id}", _scale_pixmap(QPixmap.fromImage(img), _CARD_W, _CARD_H)
        )
        self._loading_imgs.discard(card_id)
        self._render_timer.start()   # gebuendelt neu zeichnen

    # -- Rendern ------------------------------------------------------------

    def _make_card(self, inst: _CardInst) -> _BoardCard:
        card = _BoardCard(inst, self._pixmap_for(inst), held=(inst is self._held))
        card.clicked.connect(self._on_card_clicked)
        card.context.connect(self._on_card_context)
        return card

    def _zone_placeholder(self, key: str) -> QLabel:
        lbl = QLabel(self._PLACEMENT_LABELS.get(key[0], ""))
        lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lbl.setStyleSheet("color: #5b5273;")
        return lbl

    def _pile_widget(self, name: str, count: int,
                     back: bool = False, top: _CardInst = None) -> QWidget:
        host = QWidget()
        host.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img = QLabel()
        img.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        if top is not None:
            img.setPixmap(self._pixmap_for(top))
        elif back and count:
            img.setPixmap(_card_back_pixmap(_CARD_W, _CARD_H))
        v.addWidget(img, alignment=Qt.AlignmentFlag.AlignCenter)
        cap = QLabel(f"{name} {count}")
        cap.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        cap.setStyleSheet("color: #cfc6e0; font-size: 9px;")
        v.addWidget(cap, alignment=Qt.AlignmentFlag.AlignCenter)
        return host

    def _render(self) -> None:
        slots = {"field": self._field, "e0": self._emz[0], "e1": self._emz[1]}
        for i in range(5):
            slots[f"m{i}"] = self._mzones[i]
            slots[f"s{i}"] = self._szones[i]
        for key, inst in slots.items():
            self._zones[key].set_content(
                self._make_card(inst) if inst is not None
                else self._zone_placeholder(key)
            )
        self._zones["deck"].set_content(
            self._pile_widget("Deck", len(self._deck), back=True)
        )
        self._zones["extra"].set_content(
            self._pile_widget("Extra", len(self._extra), back=True)
        )
        self._zones["gy"].set_content(
            self._pile_widget("GY", len(self._gy),
                              top=self._gy[-1] if self._gy else None)
        )
        self._zones["banished"].set_content(
            self._pile_widget("Bann", len(self._banished),
                              top=self._banished[-1] if self._banished else None)
        )
        # Hand
        while self._hand_row.count():
            old = self._hand_row.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
        for inst in self._hand:
            self._hand_row.addWidget(self._make_card(inst))
        self.hand_label.setText(f"Hand ({len(self._hand)})")
        held = (
            f"  ·  aufgenommen: {self._held.name} — Zone anklicken"
            if self._held else ""
        )
        rec = f"  ·  ● Aufzeichnung ({len(self._rec_log)})" if self._recording else ""
        self.status.setText(
            f"Deck {len(self._deck)}  ·  Extra {len(self._extra)}  ·  "
            f"GY {len(self._gy)}  ·  Verbannt {len(self._banished)}{rec}{held}"
        )
        self.undo_btn.setEnabled(bool(self._undo))
        self.rec_btn.setText(
            f"■ Speichern… ({len(self._rec_log)})" if self._recording
            else "● Aufzeichnen"
        )
        self.rec_btn.setEnabled(self._recording or self._deck_id is not None)



# ---------------------------------------------------------------------------
# Hauptfenster
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, db_path: str = ydb.DEFAULT_DB, restore_session: bool = False):
        super().__init__()
        # Sitzungs-/Fensterstatus nur im echten App-Lauf (main()) wiederher-
        # stellen/speichern -- in Tests/Smokes bleibt der Zustand neutral.
        self._restore_session = restore_session
        self.setWindowTitle(
            f"Yu-Gi-Oh -- Sammlung & Suche  ·  v{ydb.APP_VERSION}"
        )
        self.resize(1100, 700)
        self.repo = CardRepository(db_path)
        # Gepackter Erststart: mitgelieferte Seed-DB in den Nutzerordner kopieren.
        ydb.ensure_user_db(db_path)
        if self.repo.exists():
            # Erster Start einer neuen App-Version: Benutzerdaten sichern,
            # BEVOR ensure_schema die DB migriert.
            try:
                ydb.migration_backup(db_path)
            except OSError as exc:
                QMessageBox.warning(
                    self, "Sicherung fehlgeschlagen",
                    f"Versions-Sicherung der Datenbank fehlgeschlagen ({exc}).\n"
                    "Die App startet trotzdem.",
                )
            ydb.ensure_schema(db_path)  # ggf. fehlende Tabellen nachruesten
        self._loading = True  # unterdrueckt Suche waehrend Initialisierung
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self.search)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_filter_panel())
        self.results = QListWidget()
        self.results.currentItemChanged.connect(self._on_select)
        splitter.addWidget(self.results)
        self.detail = DetailPanel(self.repo)
        splitter.addWidget(self.detail)
        splitter.setSizes([280, 380, 440])
        self.splitter = splitter

        self.collection_view = CollectionView(self.repo)
        self.deck_view = DeckView(self.repo)
        self.combo_view = ComboView(self.repo)
        self.playtest_view = PlayTestView(self.repo)
        # Detailansicht -> aktives Deck bzw. aktive Kombo
        self.detail.add_to_deck_callback = self.deck_view.add_card
        self.detail.add_to_combo_callback = self.combo_view.add_piece
        # Deck-Tab -> neue Kombo im Kombos-Tab oeffnen
        self.deck_view.open_combo_callback = self._open_combo
        self.playtest_view.open_combo_callback = self._open_combo
        # Deck-Tab (Vorschlaege) -> Karte im DetailPanel des Suche-Tabs
        self.deck_view.open_card_callback = self._show_card

        self.tabs = QTabWidget()
        self.tabs.addTab(splitter, "Suche")
        self.tabs.addTab(self.collection_view, "Sammlung")
        self.tabs.addTab(self.deck_view, "Deck")
        self.tabs.addTab(self.playtest_view, "Spielfeld")
        self.tabs.addTab(self.combo_view, "Kombos")
        self.tabs.addTab(HelpView(), "Handbuch")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)
        self._build_data_menu()

        if self.repo.exists():
            self._populate_filters()
        elif getattr(sys, "frozen", False):
            QMessageBox.warning(
                self, "Datenbank fehlt",
                "Die mitgelieferte Kartendatenbank konnte nicht angelegt werden.\n\n"
                "Mit Internetverbindung lässt sie sich über das Menü\n"
                "'Daten → Kartendaten aktualisieren' herunterladen.",
            )
        else:
            QMessageBox.information(
                self, "Datenbank fehlt",
                "Keine Datenbank gefunden.\n\n"
                "Anlegen über das Menü 'Daten → Kartendaten aktualisieren'\n"
                "oder per Kommandozeile:  python yugioh_db.py build",
            )
        self._loading = False
        self.search()
        if self._restore_session:
            self._restore_session_state()
        # Stiller Hinweis auf neue App-Versionen, kurz nach dem Start (ein
        # Mini-Request; offline/Fehler bleibt einfach unsichtbar).
        QTimer.singleShot(2000, lambda: self._check_app_version(manual=False))

    def _build_filter_panel(self) -> QWidget:
        panel = QWidget()
        form = QVBoxLayout(panel)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Name oder Kartentext ...")
        self.search_box.returnPressed.connect(self.search)

        box = QGroupBox("Filter")
        fl = QFormLayout(box)
        self.type_cb = QComboBox()
        self.attr_cb = QComboBox()
        self.arch_cb = QComboBox()
        self.level_cb = QComboBox()
        self.level_cb.addItem("(alle)", None)
        for lvl in range(1, 13):
            self.level_cb.addItem(str(lvl), lvl)

        self.atk_min = QSpinBox(); self.atk_min.setRange(0, 5000)
        self.atk_min.setSingleStep(100); self.atk_min.setSpecialValueText("egal")
        self.atk_max = QSpinBox(); self.atk_max.setRange(0, 5000)
        self.atk_max.setSingleStep(100); self.atk_max.setSpecialValueText("egal")

        for cb in (self.type_cb, self.attr_cb, self.arch_cb):
            cb.addItem("(alle)", None)

        fl.addRow("Typ", self.type_cb)
        fl.addRow("Attribut", self.attr_cb)
        fl.addRow("Archetyp", self.arch_cb)
        fl.addRow("Level/Rank", self.level_cb)
        fl.addRow("ATK ab", self.atk_min)
        fl.addRow("ATK bis", self.atk_max)

        self.only_coll = QCheckBox("Nur meine Sammlung")
        search_btn = QPushButton("Suchen")
        search_btn.setObjectName("primary")  # goldener Primaer-Button (Theme)
        search_btn.clicked.connect(self.search)

        # Aenderungen an Filtern loesen direkt eine neue Suche aus.
        for cb in (self.type_cb, self.attr_cb, self.arch_cb, self.level_cb):
            cb.currentIndexChanged.connect(self._on_filter_changed)
        self.atk_min.valueChanged.connect(self._on_filter_changed)
        self.atk_max.valueChanged.connect(self._on_filter_changed)
        self.only_coll.stateChanged.connect(self._on_filter_changed)

        form.addWidget(self.search_box)
        form.addWidget(box)
        form.addWidget(self.only_coll)
        form.addWidget(search_btn)
        form.addStretch()
        self.count_label = QLabel("")
        form.addWidget(self.count_label)
        return panel

    def _populate_filters(self) -> None:
        _col_trans = {"type": _TYPE_DE, "attribute": _ATTR_DE}
        for cb, col in (
            (self.type_cb, "type"),
            (self.attr_cb, "attribute"),
            (self.arch_cb, "archetype"),
        ):
            trans = _col_trans.get(col, {})
            cb.blockSignals(True)
            for val in self.repo.distinct(col):
                cb.addItem(trans.get(val, val), val)
            cb.blockSignals(False)

    def _on_filter_changed(self, *_):
        if not self._loading:
            self._search_timer.start()

    def search(self) -> None:
        if not self.repo.exists():
            return
        cards = self.repo.query(
            text=self.search_box.text(),
            type=self.type_cb.currentData(),
            attribute=self.attr_cb.currentData(),
            archetype=self.arch_cb.currentData(),
            level=self.level_cb.currentData(),
            atk_min=self.atk_min.value(),
            atk_max=self.atk_max.value(),
            only_collection=self.only_coll.isChecked(),
        )
        self.results.clear()
        for c in cards:
            stat = ""
            if c["atk"] is not None:
                # Link-Monster haben keine DEF (def ist NULL) -> nicht "None"
                # anzeigen, wie auch DetailPanel.show_card.
                if c["def"] is not None:
                    stat = f"  [ATK {c['atk']} / DEF {c['def']}]"
                else:
                    stat = f"  [ATK {c['atk']}]"
            item = QListWidgetItem(f"{c['name_de'] or c['name']}{stat}")
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            self.results.addItem(item)
        self.count_label.setText(f"{len(cards)} Treffer")

    def _on_select(self, current: QListWidgetItem, _previous=None) -> None:
        if current is None:
            return
        card_id = current.data(Qt.ItemDataRole.UserRole)
        card = self.repo.get_card(card_id)
        if card:
            self.detail.show_card(card)

    def _on_tab_changed(self, index: int) -> None:
        # Sammlung beim Wechsel auf den Tab aktualisieren, damit gerade
        # hinzugefuegte Karten sofort erscheinen.
        widget = self.tabs.widget(index)
        if widget is self.collection_view:
            self.collection_view.refresh()
        elif widget is self.deck_view:
            self.deck_view.refresh()
        elif widget is self.playtest_view:
            self.playtest_view.refresh()
        elif widget is self.combo_view:
            self.combo_view.refresh()

    def _open_combo(self, combo_id: int) -> None:
        """Aus dem Deck-Tab: in den Kombos-Tab wechseln und die Kombo zeigen."""
        self.tabs.setCurrentWidget(self.combo_view)
        self.combo_view.focus_combo(combo_id)

    def _show_card(self, card_id: int) -> None:
        """Aus dem Deck-Tab: Karte im DetailPanel zeigen (lebt im Suche-Tab);
        von dort fuegt '+ Deck' sie direkt dem aktiven Deck hinzu."""
        card = self.repo.get_card(card_id)
        if card is None:
            return
        self.tabs.setCurrentIndex(0)  # Suche-Tab haelt das DetailPanel
        self.detail.show_card(card)

    def closeEvent(self, event) -> None:
        # Noch nicht gespeicherte Kombo-Eingaben sichern (Auto-Save-Timer
        # koennte sonst verfallen).
        self.combo_view.flush_pending()
        if self._restore_session:
            self._save_session_state()
        super().closeEvent(event)

    # -- Sitzungs-/Fensterstatus (QSettings) ----------------------------------

    def _save_session_state(self) -> None:
        """Fenster-, Tab-, Deck- und Sammlungsfilter-Zustand sichern."""
        s = QSettings()
        s.setValue("win/geometry", self.saveGeometry())
        s.setValue("win/splitter", self.splitter.saveState())
        s.setValue("ui/tab", self.tabs.currentIndex())
        did = self.deck_view.deck_id
        s.setValue("ui/deck_id", -1 if did is None else int(did))
        cv = self.collection_view
        s.setValue("coll/text", cv.filter_text.text())
        s.setValue("coll/cat", cv.filter_cat.currentData() or "")
        s.setValue("coll/attr", cv.filter_attr.currentData() or "")
        s.setValue("coll/arch", cv.filter_arch.currentData() or "")
        s.setValue("coll/untranslated", cv.filter_untranslated.isChecked())

    def _restore_session_state(self) -> None:
        """Gesicherten Zustand wiederherstellen (nach dem Aufbau der Views)."""
        s = QSettings()
        geo = s.value("win/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        spl = s.value("win/splitter")
        if spl is not None:
            self.splitter.restoreState(spl)
        # Sammlungsfilter ohne Zwischen-Refreshes setzen, danach einmal anwenden.
        cv = self.collection_view
        widgets = (
            cv.filter_text, cv.filter_cat, cv.filter_attr,
            cv.filter_arch, cv.filter_untranslated,
        )
        for w in widgets:
            w.blockSignals(True)
        cv.filter_text.setText(s.value("coll/text", "") or "")
        for combo, key in (
            (cv.filter_cat, "coll/cat"), (cv.filter_attr, "coll/attr"),
            (cv.filter_arch, "coll/arch"),
        ):
            idx = combo.findData(s.value(key, "") or None)
            combo.setCurrentIndex(max(idx, 0))
        cv.filter_untranslated.setChecked(
            s.value("coll/untranslated", False, type=bool)
        )
        for w in widgets:
            w.blockSignals(False)
        if self.repo.exists():
            cv._apply_filters()
        # Gewaehltes Deck wiederherstellen.
        did = s.value("ui/deck_id", -1, type=int)
        if did is not None and did >= 0:
            self.deck_view.select_deck(did)
        # Zuletzt aktives Tab.
        tab = s.value("ui/tab", 0, type=int)
        if tab is not None and 0 <= tab < self.tabs.count():
            self.tabs.setCurrentIndex(tab)

    # -- Kartendaten-Update (Menü 'Daten') ------------------------------------

    def _build_data_menu(self) -> None:
        menu = self.menuBar().addMenu("Daten")
        self._check_action = menu.addAction("Auf Updates prüfen")
        self._check_action.triggered.connect(self._check_for_update)
        self._update_action = menu.addAction("Kartendaten aktualisieren…")
        self._update_action.triggered.connect(self._start_update)
        menu.addSeparator()
        self._app_check_action = menu.addAction("Auf neue App-Version prüfen")
        self._app_check_action.triggered.connect(
            lambda: self._check_app_version(manual=True)
        )

    # -- App-Update-Hinweis (GitHub-Releases) ---------------------------------

    def _check_app_version(self, manual: bool) -> None:
        """Neueste Version bei GitHub abfragen (im Hintergrund). manual=True
        meldet auch 'aktuell' und Fehler; der Start-Check bleibt still."""
        self._app_check_action.setEnabled(False)
        self._app_check_signals = _DbTaskSignals()
        self._app_check_signals.done.connect(
            lambda info: self._on_app_check_done(info, manual)
        )
        self._app_check_signals.failed.connect(
            lambda msg: self._on_app_check_failed(msg, manual)
        )
        QThreadPool.globalInstance().start(
            _DbTask(ydb.check_app_update, self._app_check_signals)
        )

    def _on_app_check_failed(self, msg: str, manual: bool) -> None:
        self._app_check_action.setEnabled(True)
        if manual:
            QMessageBox.warning(
                self, "Auf neue App-Version prüfen",
                f"Keine Antwort von GitHub ({msg}).\n"
                "Besteht eine Internetverbindung?",
            )

    def _on_app_check_done(self, info, manual: bool) -> None:
        self._app_check_action.setEnabled(True)
        if info is None:
            if manual:
                QMessageBox.information(
                    self, "Auf neue App-Version prüfen",
                    f"Du nutzt die aktuelle Version (v{ydb.APP_VERSION}).",
                )
            return
        notes = info["notes"]
        if len(notes) > 600:
            notes = notes[:600] + "…"
        text = (
            f"Version {info['version']} ist verfügbar "
            f"(installiert: {ydb.APP_VERSION}).\n\n"
            "Deine Daten (Sammlung, Decks, Kombos) bleiben beim Update "
            "erhalten — einfach den alten App-Ordner durch den neuen "
            "ersetzen."
        )
        if notes:
            text += f"\n\nÄnderungen:\n{notes}"
        box = QMessageBox(
            QMessageBox.Icon.Information, "Neue App-Version", text,
            parent=self,
        )
        open_btn = box.addButton(
            "Download-Seite öffnen", QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton("Später", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl(info["url"]))

    def _set_data_actions_enabled(self, on: bool) -> None:
        self._check_action.setEnabled(on)
        self._update_action.setEnabled(on)

    def _check_for_update(self) -> None:
        """Billige Versionsabfrage (ein Mini-Request), im Hintergrund, damit
        ein totes Netz die Oberfläche nicht blockiert."""
        self._set_data_actions_enabled(False)
        self._check_signals = _DbTaskSignals()
        self._check_signals.done.connect(self._on_check_done)
        self._check_signals.failed.connect(lambda _msg: self._on_check_done(None))
        QThreadPool.globalInstance().start(
            _DbTask(ydb.fetch_db_version, self._check_signals)
        )

    def _on_check_done(self, remote) -> None:
        self._set_data_actions_enabled(True)
        if not remote:
            QMessageBox.warning(
                self, "Auf Updates prüfen",
                "Keine Antwort von der YGOPRODeck-API.\n"
                "Besteht eine Internetverbindung?",
            )
            return
        local = (
            ydb.local_db_version(self.repo.db_path)
            if self.repo.exists() else None
        )
        if local == remote:
            QMessageBox.information(
                self, "Auf Updates prüfen",
                f"Die Kartendaten sind aktuell (Version {remote}).",
            )
            return
        reply = QMessageBox.question(
            self, "Auf Updates prüfen",
            f"Neue Kartendaten verfügbar (lokal: {local or 'unbekannt'}, "
            f"online: {remote}).\n\nJetzt aktualisieren?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_update(confirmed=True)

    def _start_update(self, confirmed: bool = False) -> None:
        """Laedt alle Kartendaten neu. Benutzerdaten (Sammlung, Decks, Kombos,
        eigene Übersetzungen) bleiben unberührt: build_database arbeitet per
        UPSERT ohne DELETE auf cards und wendet Übersetzungs-Overrides nach
        dem Update wieder an. Vorher wird eine .bak-Sicherung angelegt."""
        if not confirmed:
            reply = QMessageBox.question(
                self, "Kartendaten aktualisieren",
                "Alle Kartendaten von der YGOPRODeck-API herunterladen?\n\n"
                "Eigene Daten (Sammlung, Decks, Kombos, eigene Übersetzungen) "
                "bleiben erhalten.",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        if os.path.exists(self.repo.db_path):
            try:
                shutil.copy(self.repo.db_path, self.repo.db_path + ".bak")
            except OSError as exc:
                QMessageBox.warning(
                    self, "Kartendaten aktualisieren",
                    f"Sicherungskopie fehlgeschlagen ({exc}).\n"
                    "Update abgebrochen.",
                )
                return
        self._set_data_actions_enabled(False)
        self._progress = QProgressDialog(
            "Lade Kartendaten von der YGOPRODeck-API …", "", 0, 0, self
        )
        self._progress.setCancelButton(None)  # mitten im Schreiben kein Abbruch
        self._progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._progress.setWindowTitle("Aktualisierung")
        self._progress.setMinimumDuration(0)
        self._progress.show()
        self._update_signals = _DbTaskSignals()
        self._update_signals.done.connect(self._on_update_done)
        self._update_signals.failed.connect(self._on_update_failed)
        db_path = self.repo.db_path
        QThreadPool.globalInstance().start(
            _DbTask(lambda: ydb.build_database(db_path), self._update_signals)
        )

    def _on_update_done(self, count) -> None:
        self._progress.close()
        self._set_data_actions_enabled(True)
        self._refresh_all()
        version = ydb.local_db_version(self.repo.db_path) or "unbekannt"
        QMessageBox.information(
            self, "Aktualisierung",
            f"{count} Karten aktualisiert (Datenbank-Version {version}).",
        )

    def _on_update_failed(self, msg: str) -> None:
        self._progress.close()
        self._set_data_actions_enabled(True)
        QMessageBox.warning(
            self, "Aktualisierung fehlgeschlagen",
            f"{msg}\n\nDie Datenbank wurde nicht verändert; zur Not liegt "
            f"eine Sicherung neben ihr ({os.path.basename(self.repo.db_path)}"
            ".bak).",
        )

    def _refresh_all(self) -> None:
        """Nach einem Daten-Update: Such-Filter neu befüllen (neue Archetypen
        usw.) und alle Views neu laden."""
        self._loading = True
        for cb in (self.type_cb, self.attr_cb, self.arch_cb):
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("(alle)", None)
            cb.blockSignals(False)
        if self.repo.exists():
            self._populate_filters()
        self._loading = False
        self.search()
        self.collection_view.refresh()
        self.deck_view.refresh()
        self.combo_view.refresh()



def main() -> None:
    app = QApplication(sys.argv)
    # Identitaet fuer QSettings (Sitzungs-/Fensterstatus).
    app.setOrganizationName(ydb.APP_DIR_NAME)
    app.setApplicationName(ydb.APP_DIR_NAME)
    apply_theme(app)
    win = MainWindow(restore_session=True)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
