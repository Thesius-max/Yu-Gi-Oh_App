"""Spielfeld-Tab: Goldfishing mit optionalem Regelwerk und Dummy-Gegner.

PlayTestView zeigt den Spielzustand (_game.Game: beide Seiten, geteilte
Extra-Monsterzonen, Zug/Phase/LP) mit Brett-Helfern (_BoardCard/_Zone),
Stapel- und Regel-Dialogen (playtest_dialogs), wertbasiertem Undo-Stack
und Kombo-Recorder (Zuege als Notation-Entwurf, _RecordSaveDialog speichert
als Kombo mit Heimat-Deck). Die Regeln selbst (_rules) sind reine
Funktionen; die View setzt sie je Modus um: aus / warnen (Protokoll) /
erzwingen (Rueckfrage 'per Effekt erlaubt?'). Keine Persistenz -- der
Spielzustand lebt nur in der GUI.
"""

from __future__ import annotations

import collections
import itertools
import random

from PySide6.QtCore import QPoint, QPointF, QTimer, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QCursor, QImage, QKeySequence, QPainter, QPen, QPixmap,
    QPixmapCache, QPolygonF, QShortcut, QTransform
)
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QListWidget, QMenu, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget
)

import yugioh_db as ydb

from . import _rules as R
from ._cardinst import _CardInst
from ._game import PHASE_KEYS, PHASES, Game
from .carddetail import CardDetailDialog, CardSearchDialog
from .images import (
    IMAGE_URL, card_back_pixmap, image_pool, lookup_card_pixmap,
    placeholder_pixmap, scale_pixmap,
)
from .playtest_dialogs import (
    _MaterialDialog, _PileDialog, _RecordSaveDialog, _StatDialog,
    _TokenDialog, _TopDeckDialog,
)
from .repository import CardRepository
from .tasks import ImageLoader, ImageSignals
from .theme import _BG, _GOLD, _TEXT

# ---------------------------------------------------------------------------
# Spielfeld — eigener Tab
# ---------------------------------------------------------------------------

_CARD_W, _CARD_H = 74, 106     # Brettkarten-Groesse (aufrecht)
_ZONE_W = _ZONE_H = 112        # Zelle: fasst Karte aufrecht UND um 90° gedreht
_LOG_MAX = 300

_METHOD_LABELS = {
    "link": "Link-Beschwörung", "xyz": "Xyz-Beschwörung",
    "synchro": "Synchro-Beschwörung", "fusion": "Fusionsbeschwörung",
    "ritual": "Ritualbeschwörung",
}
# Richtung der Link-Pfeile als Einheitsvektor (x nach rechts, y nach unten).
_ARROW_DIRS = {
    "Top": (0, -1), "Bottom": (0, 1), "Left": (-1, 0), "Right": (1, 0),
    "Top-Left": (-1, -1), "Top-Right": (1, -1),
    "Bottom-Left": (-1, 1), "Bottom-Right": (1, 1),
}


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
            self.setObjectName("HeldCard")

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
        self.setObjectName("OppBoardZone" if zone_key.startswith("o") else "BoardZone")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedSize(w, h)
        self.setProperty("hint", False)
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

    def set_hint(self, on: bool) -> None:
        """Zone als gueltiges Ziel hervorheben (Gold-Rahmen per QSS)."""
        if self.property("hint") != on:
            self.setProperty("hint", on)
            self.style().unpolish(self)
            self.style().polish(self)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.zone_key)
        super().mousePressEvent(e)


class PlayTestView(QWidget):
    """Spielfeld: ein eigenes Deck laden, Starthand ziehen und Karten
    auslegen (Klick-Aufnehmen + Klick-Ablegen, Rechtsklick fuer Zustaende
    und Aktionen). Zug/Phasen/Lebenspunkte laufen immer mit; das
    **Regelwerk** (_rules, Modus aus/warnen/erzwingen) prueft Beschwoerungen
    samt Material, Zonen (EMZ, Link-Pfeile), Positionen, Aktivierungen,
    Handlimit und Angriffe -- Karteneffekte bleiben Sache des Benutzers.
    Ein **Dummy-Gegner** (Karten per Suche, Handtraps, eigene LP) dient als
    Ziel fuer Angriffe und zum Durchspielen von Unterbrechungen.

    Stapel-Dialoge (Deck/Extra/GY/Verbannt) koennen Karten auch direkt
    aufnehmen ('hold': in die Hand + angewaehlt, naechster Zonen-Klick legt
    ab); 'Top ansehen…' blaettert die obersten Deck-Karten. Jede
    Zustandsaenderung ist per Undo-Stack (Game-Snapshots, Strg+Z)
    rueckgaengig machbar. Der **Kombo-Recorder** protokolliert Zuege als
    Notation-Entwurf (mit Regeln: explizite NS/Set/Formeln; ohne: NS/SS-
    Heuristik) und speichert sie als Kombo mit Heimat-Deck = Spielfeld-Deck.
    Spielmarken und Gegner-Karten werden nie zu Bausteinen."""

    _PLACEMENT_LABELS = {"field": "Feld", "e": "EMZ", "m": "Mon", "s": "Z/F",
                         "f": "Feld"}
    _UNDO_MAX = 80

    def __init__(self, repo: CardRepository):
        super().__init__()
        self.repo = repo
        self._deck_id: int | None = None
        self._names: dict[int, str] = {}
        self._info: dict[int, dict] = {}    # card_id -> play_info (inkl. Gegner/Marken)
        self.game = Game()
        self._extra_ids: set[int] = set()  # card_ids, die ins Extra Deck gehoeren
        self._deck_copies: dict[int, int] = {}  # Kopien je Karte (Main+Extra)
        self._held: _CardInst | None = None
        self._undo: list[dict] = []
        self._detail_dialog: CardDetailDialog | None = None
        self._token_ids = itertools.count(-1, -1)  # Spielmarken: negative ids
        self.rule_mode = "warn"
        self.open_combo_callback = None   # setzt MainWindow

        # Kombo-Recorder (alles wandert mit in die Undo-Snapshots).
        self._recording = False
        self._rec_log: list[str] = []          # Schritte (Notation-Entwurf)
        self._rec_used: dict[int, int] = {}    # Exemplar-uid -> card_id
        self._rec_ns_used = False               # NS-Heuristik: 1x je Aufnahme
        self._rec_last_ed: int | None = None    # letzter ED-SS = Boss-Vorschlag
        self._rec_start: list[str] = []         # Starthand bei Aufnahmebeginn
        self._rec_last_uid: int | None = None   # Exemplar des letzten Schritts

        # Nachladen fehlender Bilder (meist sind sie lokal gecacht).
        self._loading_imgs: set[int] = set()
        self._img_signals = ImageSignals()
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

        # -- Zug- und Phasenleiste -------------------------------------------
        turn_bar = QHBoxLayout()
        self.turn_label = QLabel()
        self.turn_label.setObjectName("TurnLabel")
        turn_bar.addWidget(self.turn_label)
        self.phase_btns: dict[str, QPushButton] = {}
        for key, label in PHASES:
            b = QPushButton(label)
            b.setObjectName("PhaseButton")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self._set_phase(k))
            self.phase_btns[key] = b
            turn_bar.addWidget(b)
        self.next_phase_btn = QPushButton("Nächste Phase ▸")
        self.next_phase_btn.clicked.connect(self.next_phase)
        turn_bar.addWidget(self.next_phase_btn)
        self.end_turn_btn = QPushButton("Zug beenden")
        self.end_turn_btn.setObjectName("primary")
        self.end_turn_btn.clicked.connect(self.end_turn)
        turn_bar.addWidget(self.end_turn_btn)
        turn_bar.addStretch()
        self.rules_cb = QComboBox()
        for key, label in R.MODES:
            self.rules_cb.addItem(label, key)
        self.rules_cb.setToolTip(
            "Regelwerk (Spielmechanik, keine Karteneffekte): aus, nur "
            "warnen (Protokoll) oder erzwingen (Rückfrage bei Verstößen)"
        )
        self.rules_cb.setCurrentIndex(self.rules_cb.findData(self.rule_mode))
        self.rules_cb.currentIndexChanged.connect(
            lambda _i: self.set_rule_mode(self.rules_cb.currentData())
        )
        turn_bar.addWidget(self.rules_cb)
        outer.addLayout(turn_bar)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        # -- Feld-Grid -------------------------------------------------------
        body = QHBoxLayout()
        left = QVBoxLayout()
        self._zones: dict[str, _Zone] = {}
        grid = QGridLayout()
        grid.setSpacing(4)

        def zone(key, r, c):
            z = _Zone(key, _ZONE_W, _ZONE_H)
            z.clicked.connect(self._on_zone_clicked)
            self._zones[key] = z
            grid.addWidget(z, r, c)

        # Gegnerseite oben (Zeilen 0-1), geteilte EMZ-Zeile (2), eigene Seite
        # unten (3-4). Links: Feldzauber + Extra-Deck; rechts: Verbannt/GY/Deck.
        zone("obanished", 0, 0)
        zone("ogy", 1, 0)
        for i in range(5):
            zone(f"os{i}", 0, 1 + i)
            zone(f"om{i}", 1, 1 + i)
        zone("ofield", 1, 6)
        zone("field", 2, 0)
        zone("e0", 2, 2)
        zone("e1", 2, 4)
        zone("banished", 2, 6)
        for i in range(5):
            zone(f"m{i}", 3, 1 + i)
            zone(f"s{i}", 4, 1 + i)
        zone("gy", 3, 6)
        zone("extra", 4, 0)
        zone("deck", 4, 6)
        board = QWidget()
        board.setLayout(grid)
        left.addWidget(board, alignment=Qt.AlignmentFlag.AlignHCenter)

        # -- Hand ------------------------------------------------------------
        self.hand_label = QLabel("Hand")
        self.hand_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        left.addWidget(self.hand_label)
        self._hand_host = QWidget()
        self._hand_row = QHBoxLayout(self._hand_host)
        self._hand_row.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(_CARD_H + 24)
        scroll.setWidget(self._hand_host)
        left.addWidget(scroll)
        left.addStretch()
        # Brett + Hand scrollen, statt die Mindesthoehe des Hauptfensters
        # (fuenf Zonen-Reihen) auf kleine Bildschirme durchzudruecken.
        left_host = QWidget()
        left_host.setLayout(left)
        board_scroll = QScrollArea()
        board_scroll.setWidgetResizable(True)
        board_scroll.setFrameShape(QFrame.Shape.NoFrame)
        board_scroll.setWidget(left_host)
        body.addWidget(board_scroll, stretch=1)

        # -- Seitenleiste: LP, Gegner, Marken, Protokoll ------------------------
        side = QVBoxLayout()
        self.lp_labels: dict[str, QLabel] = {}
        for owner, title in (("opp", "Gegner"), ("me", "Du")):
            row = QHBoxLayout()
            lbl = QLabel()
            lbl.setObjectName("LifePoints")
            self.lp_labels[owner] = lbl
            row.addWidget(QLabel(f"{title}:"))
            row.addWidget(lbl, stretch=1)
            b = QPushButton("± LP")
            b.setToolTip("Lebenspunkte ändern (negativ = Schaden)")
            b.clicked.connect(lambda _=False, o=owner: self._change_lp(o))
            row.addWidget(b)
            side.addLayout(row)
        self.opp_check = QCheckBox("Gegnerseite zeigen")
        self.opp_check.setChecked(True)
        self.opp_check.toggled.connect(self._toggle_opp_side)
        side.addWidget(self.opp_check)
        for text, slot, tip in (
            ("Gegner-Karte…", self._add_opp_card,
             "Karte des Gegners aufs Feld legen oder als Handtrap aktivieren"),
            ("Spielmarke…", self._create_token, "Spielmarken (Tokens) erzeugen"),
            ("+1 Normalbeschwörung", self._grant_extra_ns,
             "Ein Effekt erlaubt eine zusätzliche Normalbeschwörung in diesem Zug"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            side.addWidget(b)
        side.addWidget(QLabel("Protokoll"))
        self.log_list = QListWidget()
        self.log_list.setObjectName("RuleLog")
        self.log_list.setWordWrap(True)
        self.log_list.setMinimumWidth(240)
        side.addWidget(self.log_list, stretch=1)
        body.addLayout(side)
        outer.addLayout(body, stretch=1)

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
        if not self._confirm_discard_recording():
            # Auswahl auf das bisherige Deck zuruecksetzen.
            self.deck_cb.blockSignals(True)
            self.deck_cb.setCurrentIndex(max(self.deck_cb.findData(self._deck_id), 0))
            self.deck_cb.blockSignals(False)
            return
        self._deck_id = self.deck_cb.currentData()
        self.reset()

    def _hand_size(self) -> int:
        return self.hand_size_cb.currentData() or 5

    def _confirm_discard_recording(self) -> bool:
        """True, wenn keine Aufzeichnung laeuft oder der Benutzer sie
        verwerfen will (neue Starthand, Deckwechsel)."""
        if not (self._recording and self._rec_log):
            return True
        answer = QMessageBox.question(
            self, "Aufzeichnung verwerfen?",
            "Eine Kombo-Aufzeichnung läuft. Das verwirft die "
            f"{len(self._rec_log)} protokollierten Schritte — fortfahren?",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _reset_clicked(self) -> None:
        """Button-Variante von reset(): warnt, wenn eine Aufzeichnung läuft."""
        if self._confirm_discard_recording():
            self.reset()

    def reset(self) -> None:
        """Neues Spiel: Feld/Hand leeren, Deck+Extra neu laden, mischen,
        Starthand ziehen. 5 Karten = du beginnst (Zug 1, keine Battle
        Phase), 6 = du bist Zweiter (Zug 2). Beendet eine laufende
        Aufzeichnung (der Spielzustand, auf den sich das Protokoll bezieht,
        ist danach weg)."""
        self._stop_recording()
        self._held = None
        self._undo = []
        self.game = g = Game()
        g.going_first = self._hand_size() == 5
        g.turn = 1 if g.going_first else 2
        if self._deck_id is not None and self.repo.exists():
            data = ydb.deck_play_lists(self.repo.db_path, self._deck_id)
            self._names = dict(data["names"])
            self._info = dict(data["info"])
            g.me.deck = list(data["main"])
            g.me.extra = list(data["extra"])
            self._extra_ids = set(data["extra"])
            self._deck_copies = dict(
                collections.Counter(data["main"]) + collections.Counter(data["extra"])
            )
            random.shuffle(g.me.deck)
            for _ in range(self._hand_size()):
                if g.me.deck:
                    g.me.hand.append(self._mk_inst(g.me.deck.pop(0)))
        else:
            self._names, self._info = {}, {}
            self._extra_ids = set()
            self._deck_copies = {}
        self.log_list.clear()
        self._log("Neues Spiel — " + ("du beginnst" if g.going_first
                                      else "du bist Zweiter"))
        self._render()

    def _mk_inst(self, card_id: int, owner: str = "me") -> _CardInst:
        return _CardInst(card_id, self._names.get(card_id, str(card_id)),
                         owner=owner)

    def _info_of(self, card_id: int) -> dict:
        """Regel-Werte einer Karte (Deck, Gegner-Karten und Spielmarken sind
        vorgeladen; alles andere wird einmal nachgeschlagen)."""
        info = self._info.get(card_id)
        if info is None:
            found = (ydb.card_play_info(self.repo.db_path, [card_id])
                     if card_id > 0 and self.repo.exists() else {})
            info = found.get(card_id) or {"name": str(card_id), "type": "",
                                          "frame_type": ""}
            self._info[card_id] = info
        return info

    # -- Regel-Modus und Protokoll --------------------------------------------

    def set_rule_mode(self, mode: str) -> None:
        """'off' | 'warn' | 'enforce' (auch von MainWindow/Tests gesetzt)."""
        self.rule_mode = mode
        idx = self.rules_cb.findData(mode)
        if self.rules_cb.currentIndex() != idx:
            self.rules_cb.blockSignals(True)
            self.rules_cb.setCurrentIndex(idx)
            self.rules_cb.blockSignals(False)
        self._render()

    @property
    def _rules_on(self) -> bool:
        return self.rule_mode != "off"

    def _log(self, text: str) -> None:
        self.log_list.addItem(text)
        while self.log_list.count() > _LOG_MAX:
            self.log_list.takeItem(0)
        self.log_list.scrollToBottom()

    def _allowed(self, verdict: R.Verdict, action: str) -> bool:
        """Regel-Befund je Modus umsetzen: aus -> immer erlaubt; warnen ->
        protokollieren und erlauben; erzwingen -> bei Verstoessen nachfragen
        (ein Karteneffekt kann die Ausnahme erlauben -- das weiss nur der
        Benutzer). Hinweise werden nur protokolliert."""
        if not self._rules_on:
            return True
        for note in verdict.notes:
            self._log(f"ℹ {note}")
        if not verdict.violations:
            return True
        text = "; ".join(verdict.violations)
        if self.rule_mode == "warn":
            self._log(f"⚠ {action}: {text}")
            return True
        answer = QMessageBox.question(
            self, "Regelverstoß",
            f"{action}:\n• " + "\n• ".join(verdict.violations)
            + "\n\nTrotzdem ausführen (z. B. weil ein Karteneffekt es erlaubt)?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._log(f"⚠ {action} (per Effekt erlaubt): {text}")
            return True
        self._log(f"✋ {action} abgelehnt: {text}")
        return False

    def _choose(self, options: list[tuple[str, str]]) -> str | None:
        """Kleines Auswahlmenue an der Mausposition; Rueckgabe: Schluessel."""
        menu = QMenu(self)
        keys = {}
        for key, text in options:
            keys[menu.addAction(text)] = key
        chosen = menu.exec(QCursor.pos())
        menu.deleteLater()
        return keys.get(chosen)

    # -- Undo (Snapshot-Stack) ----------------------------------------------

    def _push_undo(self) -> None:
        """Kompletten Spielzustand als wertbasierten Snapshot sichern (die
        _CardInst-Exemplare werden beim Undo neu gebaut, behalten aber ihre
        uid). Das Recorder-Protokoll wandert mit — Undo nimmt also auch
        protokollierte Schritte zurück."""
        self._undo.append({
            "game": self.game.snapshot(),
            "rec": (self._recording, list(self._rec_log),
                    dict(self._rec_used), self._rec_ns_used,
                    self._rec_last_ed, list(self._rec_start),
                    self._rec_last_uid),
        })
        del self._undo[:-self._UNDO_MAX]

    def undo(self) -> None:
        if not self._undo:
            return
        s = self._undo.pop()
        self.game.restore(s["game"])
        (self._recording, self._rec_log, self._rec_used,
         self._rec_ns_used, self._rec_last_ed, self._rec_start,
         self._rec_last_uid) = s["rec"]
        self._held = None
        self._log("↶ Rückgängig")
        self._render()

    # -- Kombo-Recorder -------------------------------------------------------

    def _rec(self, text: str, inst: _CardInst | None = None) -> None:
        """Protokolliert einen Schritt (nur während der Aufzeichnung) und
        merkt das benutzte Exemplar für die Bausteinliste -- Spielmarken und
        Gegner-Karten werden nie zu Bausteinen."""
        if not self._recording:
            return
        self._rec_log.append(text)
        self._rec_last_uid = inst.uid if inst is not None else None
        if inst is not None and inst.owner == "me" and not inst.token:
            self._rec_used[inst.uid] = inst.card_id

    def _rec_own(self, inst: _CardInst):
        """_rec fuer eigene echte Karten, stumm fuer Marken/Gegner-Karten."""
        if inst.owner == "me" and not inst.token:
            return self._rec
        return lambda *_a, **_k: None

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
        """Protokolliert das Ablegen ohne Regelwerk: Act/Set für Zauber/
        Fallen-Zonen, 'SS (<Ort>)' für aufgenommene Stapel-Karten, sonst die
        NS/SS-Heuristik (die erste Handbeschwörung der Aufzeichnung ist NS —
        eine pro Zug —, alle weiteren SS)."""
        # Herkunft immer beim Ablegen verbrauchen -- auch ohne laufende
        # Aufzeichnung, sonst klebt sie am Exemplar und eine spaetere
        # Aufzeichnung protokolliert faelschlich 'SS <X> (GY/ED/...)'.
        src, inst.origin = inst.origin, None
        if not self._recording or inst.owner != "me":
            return
        if zone_key.removeprefix("o") == "field" or zone_key.removeprefix("o")[0] == "s":
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
        self._rec_start = [c.name for c in self.game.me.hand]
        self._rec_last_uid = None
        self._render()

    def _stop_recording(self) -> None:
        self._recording = False
        self._rec_log = []
        self._rec_used = {}
        self._rec_ns_used = False
        self._rec_last_ed = None
        self._rec_start = []
        self._rec_last_uid = None

    def _end_recording(self) -> None:
        """Aufzeichnung beenden (gespeichert/verworfen) -- auch in allen
        Undo-Snapshots, sonst schaltet Strg+Z sie wieder ein und ein
        zweites Speichern legt die Kombo doppelt an."""
        self._stop_recording()
        neutral = (False, [], {}, False, None, [], None)
        for snap in self._undo:
            snap["rec"] = neutral

    def _end_board_text(self) -> str:
        """Offene eigene Feldkarten als 'End:'-Zeile (verdeckte nur gezählt)."""
        cards = self.game.field_cards("me")
        names = [c.name for c in cards if not c.face_down]
        sets = sum(1 for c in cards if c.face_down)
        if sets:
            names.append(f"{sets} Set")
        return " + ".join(names) if names else "—"

    def _finish_recording(self) -> None:
        """'■ Speichern…': Dialog zeigen; speichern, weiter aufzeichnen
        oder verwerfen."""
        if not self._rec_log:
            self._end_recording()
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
        dlg.deleteLater()
        if result == QDialog.DialogCode.Accepted:
            name, boss_id, with_notes = dlg.values()
            combo_id = self._save_recording(name, boss_id, with_notes)
            if self.open_combo_callback is not None:
                self.open_combo_callback(combo_id)
        elif result == _RecordSaveDialog.DISCARD:
            self._end_recording()
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
            # Ein Exemplar, das ins Deck zurueck und neu gezogen wurde, hat
            # eine neue uid -- mehr Kopien als im Deck gibt es aber nicht.
            qty = min(qty, self._deck_copies.get(cid, qty))
            ydb.add_combo_card(db, combo_id, cid, qty)
        ydb.set_combo_steps(db, combo_id, list(self._rec_log))
        if boss_id is not None:
            ydb.set_combo_boss(db, combo_id, boss_id)
        if with_notes:
            start = ", ".join(self._rec_start) if self._rec_start else "—"
            notes = f"Start: {start}\nEnd: {self._end_board_text()}"
            ydb.update_combo(db, combo_id, name, None, notes)
        self._end_recording()
        self._render()
        return combo_id

    # -- Zug & Phasen ---------------------------------------------------------

    @staticmethod
    def _phase_name(key: str) -> str:
        return dict(PHASES)[key]

    def _set_phase(self, target: str) -> None:
        """Phase direkt anwaehlen (Phasenleiste)."""
        g = self.game
        if target == g.phase or not g.my_turn:
            self._render()
            return
        if not self._allowed(R.check_phase_change(g, target),
                             f"Wechsel in die {self._phase_name(target)} Phase"):
            self._render()
            return
        self._push_undo()
        g.phase = target
        self._log(f"Zug {g.turn} · {self._phase_name(target)} Phase")
        self._render()

    def next_phase(self) -> None:
        g = self.game
        if not g.my_turn or g.phase == "EP":
            self.end_turn()
            return
        target = PHASE_KEYS[PHASE_KEYS.index(g.phase) + 1]
        if self._rules_on and g.turn == 1 and target == "BP":
            target = "EP"          # im ersten Duell-Zug keine Battle Phase
        self._set_phase(target)

    def end_turn(self) -> None:
        """Eigenen Zug beenden (mit Regeln: Handlimit) bzw. den Gegnerzug;
        danach Draw Phase des eigenen Zugs mit automatischem Ziehen."""
        g = self.game
        if g.my_turn:
            if self._rules_on and len(g.me.hand) > R.HAND_LIMIT:
                if not self._discard_to_limit():
                    return
            self._push_undo()
            self._held = None
            g.next_turn()
            self._log(f"Zug {g.turn} · Gegnerzug")
        else:
            self._push_undo()
            self._held = None
            g.next_turn()
            self._log(f"Zug {g.turn} · dein Zug — Draw Phase")
            if g.me.deck:
                self._draw_one()
            else:
                self._log("✖ Deck leer — du kannst nicht ziehen (Niederlage)")
        self._render()

    def _discard_to_limit(self) -> bool:
        """End Phase mit zu voller Hand: Abwurf-Auswahl. False = Zugende
        abbrechen (nur wenn der Regel-Modus es verweigert)."""
        g = self.game
        excess = len(g.me.hand) - R.HAND_LIMIT
        hand = list(g.me.hand)

        def check(chosen):
            v = R.Verdict()
            if len(chosen) != excess:
                v.violations.append(
                    f"Handlimit: genau {excess} Karte(n) abwerfen, gewählt: {len(chosen)}")
            return v

        chosen = self._pick_cards(f"Handlimit: {excess} abwerfen", hand,
                                  [c.name for c in hand], check, "Abwerfen") or []
        if not self._allowed(check(chosen), "Zug beenden"):
            return False
        if chosen:
            self._push_undo()
            for c in chosen:
                self._move(c, "gy")
                self._rec(f"Discard {c.name}", c)
        return True

    def _grant_extra_ns(self) -> None:
        self._push_undo()
        self.game.ns_extra += 1
        self._log(f"+1 Normalbeschwörung in Zug {self.game.turn} (Effekt)")
        self._render()

    def _change_lp(self, owner: str) -> None:
        side = self.game.side(owner)
        delta, ok = QInputDialog.getInt(
            self, "Lebenspunkte", "Änderung (negativ = Schaden):",
            -1000, -99_999, 99_999, 100,
        )
        if not ok or delta == 0:
            return
        self._push_undo()
        side.lp = max(0, side.lp + delta)
        who = "Gegner" if owner == "opp" else "Du"
        self._log(f"{who}: LP {delta:+d} → {side.lp}")
        self._check_lp()
        self._render()

    def _check_lp(self) -> None:
        for owner, text in (("me", "Deine LP sind 0 — Niederlage"),
                            ("opp", "LP des Gegners sind 0 — Sieg")):
            if self.game.side(owner).lp <= 0:
                self._log(f"✖ {text}")

    # -- Aktionen -----------------------------------------------------------

    def _draw_one(self) -> None:
        """Oberste Karte ziehen (ohne Undo-Schritt); der Recorder fasst
        Mehrfach-Ziehen zu 'Draw n' zusammen."""
        g = self.game
        g.me.hand.append(self._mk_inst(g.me.deck.pop(0)))
        if self._recording:
            if self._rec_log and self._rec_log[-1].startswith("Draw "):
                n = int(self._rec_log[-1].split()[1])
                self._rec_log[-1] = f"Draw {n + 1}"
            else:
                self._rec_log.append("Draw 1")

    def draw(self) -> None:
        if self.game.me.deck:
            self._push_undo()
            self._draw_one()
            self._render()

    def shuffle(self) -> None:
        if self.game.me.deck:
            self._push_undo()
            random.shuffle(self.game.me.deck)
            self._render()

    def _dispose(self, inst: _CardInst, action: str,
                 source: str | None = None) -> None:
        """Bringt eine bereits entnommene Karte ans Aktions-Ziel. 'hold' legt
        sie in die Hand UND nimmt sie auf — der naechste Zonen-Klick legt sie
        ab; so kann kein Exemplar verloren gehen. 'source' merkt sich dabei
        die Herkunft fuer den Recorder ('SS <X> (<Ort>)')."""
        me = self.game.me
        if action == "gy":
            me.gy.append(inst)
        elif action == "banish":
            me.banished.append(inst)
        else:  # "hand" oder "hold"
            me.hand.append(inst)
            if action == "hold":
                self._held = inst
                inst.origin = source

    def _move(self, inst: _CardInst, dest: str) -> None:
        """Karte an ein Ziel bringen ('hand', 'gy', 'banished', 'deck',
        'extra'; 'gone' entfernt sie aus dem Spiel, 'attach' nimmt sie nur
        vom Brett -- sie wird Xyz-Material). Ziel ist immer die Seite ihres
        Besitzers. Verlaesst sie das Feld, fallen ihr Zustand und ihr
        Xyz-Material ab (Material in den GY); Spielmarken verschwinden."""
        g = self.game
        if g.on_field(inst):
            for m in inst.materials:
                m.reset_state()
                if not m.token:
                    g.side(m.owner).gy.append(m)
            inst.materials = []
        g.remove(inst)
        if self._held is inst:
            self._held = None
        inst.reset_state()
        if inst.token:
            return
        side = g.side(inst.owner)
        if dest == "hand":
            side.hand.append(inst)
        elif dest == "gy":
            side.gy.append(inst)
        elif dest == "banished":
            side.banished.append(inst)
        elif dest == "deck":
            side.deck.insert(0, inst.card_id)
        elif dest == "extra":
            side.extra.append(inst.card_id)

    def _search_deck(self) -> None:
        me = self.game.me
        if not me.deck:
            return
        pairs = sorted(
            ((self._names.get(c, str(c)), c) for c in me.deck),
            key=lambda x: x[0],
        )
        dlg = _PileDialog(
            "Deck durchsuchen", [p[0] for p in pairs], self,
            actions=[("Auf die Hand", "hand"), ("Aufnehmen", "hold"),
                     ("→ Friedhof", "gy"), ("→ Verbannt", "banish")],
        )
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        dlg.deleteLater()
        if accepted and dlg.selected >= 0:
            self._push_undo()
            cid = pairs[dlg.selected][1]
            me.deck.remove(cid)
            inst = self._mk_inst(cid)
            self._dispose(inst, dlg.action, source="Deck")
            self._rec_pile_take(inst, dlg.action, "Deck")
            random.shuffle(me.deck)
            self._render()

    def _open_pile(self, kind: str) -> None:
        if kind in ("ogy", "obanished"):
            self._open_opp_pile(kind)
            return
        me = self.game.me
        if kind == "extra":
            labels = [self._names.get(c, str(c)) for c in me.extra]
            # Extra-Deck-Monster wollen aufs Feld: Aufnehmen ist Default.
            actions = [("Aufnehmen", "hold"), ("Auf die Hand", "hand"),
                       ("→ Friedhof", "gy"), ("→ Verbannt", "banish")]
        else:
            pile = me.gy if kind == "gy" else me.banished
            labels = [c.name for c in pile]
            other = ("→ Verbannt", "banish") if kind == "gy" else ("→ Friedhof", "gy")
            actions = [("Auf die Hand", "hand"), ("Aufnehmen", "hold"), other]
        if not labels:
            return
        titles = {"extra": "Extra-Deck", "gy": "Friedhof", "banished": "Verbannt"}
        dlg = _PileDialog(titles[kind], labels, self, actions=actions)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        dlg.deleteLater()
        if accepted and dlg.selected >= 0:
            self._push_undo()
            i = dlg.selected
            if kind == "extra":
                inst = self._mk_inst(me.extra.pop(i))
            else:
                pile = me.gy if kind == "gy" else me.banished
                inst = pile.pop(i)
            src = {"extra": "ED", "gy": "GY", "banished": "Banished"}[kind]
            self._dispose(inst, dlg.action, source=src)
            self._rec_pile_take(inst, dlg.action, src)
            self._render()

    def _peek_top(self) -> None:
        """Die obersten N Deck-Karten ansehen (Excavate/Mill): Karten einzeln
        auf Hand/GY/Verbannt/nach unten verteilen, der Rest geht in gleicher
        Reihenfolge zurueck nach oben."""
        me = self.game.me
        if not me.deck:
            return
        n, ok = QInputDialog.getInt(
            self, "Deck-Top ansehen", "Wie viele Karten ansehen?",
            3, 1, min(10, len(me.deck)),
        )
        if not ok:
            return
        self._push_undo()
        top = me.deck[:n]
        del me.deck[:n]
        dlg = _TopDeckDialog(top, self._names, self)
        dlg.exec()
        dlg.deleteLater()
        for cid, action in dlg.moves:
            if action == "bottom":
                me.deck.append(cid)
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
        me.deck[0:0] = dlg.cards   # Rest zurueck, Reihenfolge erhalten
        if not dlg.moves:
            self._undo.pop()          # nichts passiert -> kein Undo-Schritt
        self._render()

    # -- Gegner & Spielmarken -------------------------------------------------

    def _free_zone(self, owner: str, kind: str) -> str | None:
        """Erste freie Monster- ('m') bzw. Zauber/Fallen-Zone ('s')."""
        prefix = "o" if owner == "opp" else ""
        for i in range(5):
            key = f"{prefix}{kind}{i}"
            if self.game.get(key) is None:
                return key
        return None

    def _add_opp_card(self) -> None:
        """Gegner-Karte per Suche: aufs Feld (erste freie passende Zone,
        danach frei verschiebbar) oder als Handtrap direkt in den Gegner-GY."""
        dlg = CardSearchDialog(self.repo, self)
        dlg.setWindowTitle("Karte des Gegners")
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        cid = dlg.chosen_card_id() if accepted else None
        dlg.deleteLater()
        if cid is None:
            return
        info = self._info_of(cid)
        self._names.setdefault(cid, info["name"])
        if R.is_monster(info):
            options = [("atk", "Monsterzone (offen, Angriff)"),
                       ("setm", "Monsterzone (verdeckt, Verteidigung)")]
        elif R.spell_trap_kind(info) == "Field":
            options = [("field", "Feldzone")]
        else:
            options = [("sets", "Zauber/Fallenzone (verdeckt)"),
                       ("st", "Zauber/Fallenzone (offen)")]
        options.append(("trap", "Aus der Hand aktivieren (Handtrap → Friedhof)"))
        choice = self._choose(options)
        if choice is None:
            return
        inst = self._mk_inst(cid, owner="opp")
        if choice == "trap":
            self._push_undo()
            self.game.opp.gy.append(inst)
            self._log(f"Gegner aktiviert {inst.name} aus der Hand")
            self._rec(f"Eff {inst.name} (opp)")
            self._render()
            return
        key = ("ofield" if choice == "field"
               else self._free_zone("opp", "m" if choice in ("atk", "setm") else "s"))
        if key is None or self.game.get(key) is not None:
            self._log("Keine freie Zone auf der Gegnerseite")
            return
        self._push_undo()
        inst.face_down = choice in ("setm", "sets")
        inst.defense = choice == "setm"
        self.game.put(key, inst)
        self._log("Gegner: " + ("verdeckte Karte" if inst.face_down else inst.name)
                  + " aufs Feld")
        self._render()

    def _open_opp_pile(self, kind: str) -> None:
        opp = self.game.opp
        pile = opp.gy if kind == "ogy" else opp.banished
        if not pile:
            return
        other = ("→ Verbannt", "banish") if kind == "ogy" else ("→ Friedhof", "gy")
        dlg = _PileDialog("Gegner-" + ("Friedhof" if kind == "ogy" else "Verbannt"),
                          [c.name for c in pile], self,
                          actions=[("Aufs Feld", "field"), other])
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        dlg.deleteLater()
        if not (accepted and dlg.selected >= 0):
            return
        inst = pile[dlg.selected]
        if dlg.action == "field":
            info = self._info_of(inst.card_id)
            key = self._free_zone("opp", "m" if R.is_monster(info) else "s")
            if key is None:
                self._log("Keine freie Zone auf der Gegnerseite")
                return
            self._push_undo()
            pile.remove(inst)
            self.game.put(key, inst)
        else:
            self._push_undo()
            pile.remove(inst)
            (opp.banished if dlg.action == "banish" else opp.gy).append(inst)
        self._render()

    def _create_token(self) -> None:
        dlg = _TokenDialog(self)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        vals = dlg.values()
        dlg.deleteLater()
        if not accepted:
            return
        self._push_undo()
        made = 0
        for _ in range(vals["count"]):
            key = self._free_zone(vals["owner"], "m")
            if key is None:
                break
            cid = next(self._token_ids)
            self._info[cid] = {
                "name": vals["name"], "type": "Token", "frame_type": "token",
                "level": vals["level"], "atk": vals["atk"], "def": vals["def"],
                "race": None, "attribute": None, "scale": None,
                "link_value": None, "link_markers": None,
            }
            inst = _CardInst(cid, vals["name"], owner=vals["owner"], token=True,
                             defense=vals["defense"])
            self.game.put(key, inst)
            self.game.flags["summoned"].add(inst.uid)
            made += 1
            if vals["owner"] == "me":
                self._rec(f"SS {inst.name}")
        if not made:
            self._undo.pop()
            self._log("Keine freie Monsterzone für Spielmarken")
        else:
            self._log(f"{made}× {vals['name']} erzeugt")
        self._render()

    # -- Bewegen (Klick-Aufnehmen + Ablegen) -------------------------------

    def _on_card_clicked(self, inst: _CardInst) -> None:
        if self._held is inst:
            self._held = None
        elif inst in self.game.me.hand or self.game.on_field(inst):
            self._held = inst
        self._render()

    def _on_zone_clicked(self, zone_key: str) -> None:
        if zone_key == "deck":
            self.draw()
            return
        if zone_key in ("gy", "banished", "extra", "ogy", "obanished"):
            self._open_pile(zone_key)
            return
        held = self._held
        if held is None:
            return
        g = self.game
        from_board = g.on_field(held)
        # Mit Regeln: ein neuer Feldzauber aus der Hand ersetzt den alten.
        replace_field = (self._rules_on and not from_board and zone_key == "field"
                         and held.origin is None
                         and R.spell_trap_kind(self._info_of(held.card_id)) == "Field"
                         and R.kind(self._info_of(held.card_id)) == "spell")
        if g.get(zone_key) is not None and not replace_field:
            return
        if from_board or not self._rules_on or zone_key.startswith("o"):
            # Ohne Regeln / Umstellen / Karte auf die Gegnerseite: frei legen.
            self._push_undo()
            g.remove(held)
            g.put(zone_key, held)
            if not from_board:   # Umstellen auf dem Feld ist kein Schritt
                self._rec_placement(held, zone_key)
            self._held = None
            self._render()
            return
        info = self._info_of(held.card_id)
        if zone_key[0] in "me":
            self._summon_flow(held, info, zone_key)
        else:
            self._spell_flow(held, info, zone_key)
        self._render()

    def _place(self, inst: _CardInst, zone_key: str, *flags: str) -> None:
        """Exemplar in eine Zone legen (Undo-Schritt macht der Aufrufer)."""
        self.game.remove(inst)
        self.game.put(zone_key, inst)
        inst.origin = None
        for f in flags:
            self.game.flags[f].add(inst.uid)
        if self._held is inst:
            self._held = None

    def _mat_label(self, inst: _CardInst) -> str:
        info = self._info_of(inst.card_id)
        where = self.game.zone_of(inst)
        where = "Hand" if where is None else ("EMZ" if where[0] == "e" else "Feld")
        k = R.kind(info)
        if k == "link":
            stat = f"Link-{info.get('link_value')}"
        elif k == "xyz":
            stat = f"Rang {info.get('level')}"
        else:
            lvl = R.level_of(inst, info)
            stat = f"Stufe {lvl}" if lvl else "—"
        tuner = " · Empfänger" if R.is_tuner(info) else ""
        return f"{inst.name}  ({where} · {stat}{tuner})"

    def _pick_cards(self, title: str, cands: list[_CardInst], labels: list[str],
                    check, ok_text: str) -> list[_CardInst] | None:
        """Mehrfachauswahl mit Live-Pruefung; None = abgebrochen."""
        dlg = _MaterialDialog(title, cands, labels, check, ok_text, self)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        chosen = list(dlg.chosen)
        dlg.deleteLater()
        return chosen if accepted else None

    def _pick_materials(self, title: str, cands: list[_CardInst], check,
                        ok_text: str) -> list[_CardInst] | None:
        return self._pick_cards(title, cands, [self._mat_label(c) for c in cands],
                                check, ok_text)

    def _pairs(self, insts: list[_CardInst]) -> list[tuple[_CardInst, dict]]:
        return [(c, self._info_of(c.card_id)) for c in insts]

    def _summon_flow(self, held: _CardInst, info: dict, zone: str) -> None:
        """Monster aus Hand/Stapel in eine eigene Monsterzone (mit Regeln)."""
        g = self.game
        rec = self._rec_own(held)
        if held.origin == "ED":
            self._extra_summon_flow(held, info, zone)
            return
        if held.origin:                     # aus GY/Verbannt/Deck: per Effekt
            src = held.origin
            v = R.check_special_summon(g, held, info, zone, False, self._info_of)
            if not self._allowed(v, f"Spezialbeschwörung {held.name}"):
                return
            self._push_undo()
            self._place(held, zone, "summoned")
            rec(f"SS {held.name} ({src})", held)
            return
        options = [("ns", "Normalbeschwörung"), ("set", "Setzen"),
                   ("ss", "Spezialbeschwörung (Effekt)")]
        if R.kind(info) == "ritual":
            options.insert(0, ("ritual", _METHOD_LABELS["ritual"]))
        choice = self._choose(options)
        if choice is None:
            return
        if choice in ("ns", "set"):
            self._normal_summon(held, info, zone, set_=choice == "set")
        elif choice == "ritual":
            self._ritual_summon(held, info, zone)
        else:
            v = R.check_special_summon(g, held, info, zone, False, self._info_of)
            if not self._allowed(v, f"Spezialbeschwörung {held.name}"):
                return
            self._push_undo()
            self._place(held, zone, "summoned")
            rec(f"SS {held.name} (Hand)", held)

    def _normal_summon(self, held: _CardInst, info: dict, zone: str,
                       set_: bool) -> None:
        """Normalbeschwoerung/Setzen, ab Stufe 5 mit Tribut-Auswahl."""
        g = self.game
        tributes: list[_CardInst] = []
        if R.is_monster(info) and R.tributes_needed(info):
            picked = self._pick_materials(
                f"Tribute für {held.name}", g.monsters("me"),
                lambda ch: R.check_normal_summon(g, held, info, zone, ch,
                                                 self._info_of, set_),
                "Tributieren")
            if picked is None:
                return
            tributes = picked
        v = R.check_normal_summon(g, held, info, zone, tributes,
                                  self._info_of, set_)
        what = "Setzen" if set_ else "Normalbeschwörung"
        if not self._allowed(v, f"{what} {held.name}"):
            return
        self._push_undo()
        for t in tributes:
            self._rec_own(t)(f"Send {t.name} (Field -> GY)", t)
            self._move(t, "gy")
        held.face_down = held.defense = set_
        self._place(held, zone, "set" if set_ else "summoned")
        g.ns_used += 1
        trib = (f" (Tribute: {' + '.join(t.name for t in tributes)})"
                if tributes else "")
        self._rec_own(held)(f"{'Set' if set_ else 'NS'} {held.name}{trib}", held)
        self._rec_ns_used = True

    def _ritual_summon(self, held: _CardInst, info: dict, zone: str) -> None:
        g = self.game
        zone_v = R.check_special_summon(g, held, info, zone, False, self._info_of)

        def check(ch):
            v = R.check_materials("ritual", info, self._pairs(ch))
            v.violations += zone_v.violations
            return v
        picked = self._pick_materials(f"Ritualbeschwörung: {held.name}",
                                      R.material_candidates(g, "ritual", held),
                                      check, "Beschwören")
        if picked is None:
            return
        v = check(picked)
        v.notes = zone_v.notes
        if not self._allowed(v, f"Ritualbeschwörung {held.name}"):
            return
        self._push_undo()
        for m in picked:
            self._rec_own(m)(f"Send {m.name} (-> GY)", m)
            self._move(m, "gy")
        self._place(held, zone, "summoned")
        self._rec_own(held)(
            f"SS {held.name} (Ritual: {' + '.join(m.name for m in picked)})", held)

    def _formula(self, method: str, target: _CardInst, info: dict,
                 mats: list[_CardInst]) -> str:
        """Beschwoerungsformel in Kombo-Notation (Synchro: Empfaenger zuerst)."""
        pairs = self._pairs(mats)
        if method == "synchro":
            pairs.sort(key=lambda p: not R.is_tuner(p[1]))

        def part(m, i):
            level = R.level_of(m, i)
            shown = f" ({level})" if level and method in ("synchro", "xyz") else ""
            return m.name + shown
        suffix = {"synchro": f" ({info.get('level')})",
                  "xyz": f" (R{info.get('level')})",
                  "link": f" (L{info.get('link_value')})"}.get(method, "")
        kw = {"synchro": "Synchro", "xyz": "Xyz", "link": "Link",
              "fusion": "Fusion"}[method]
        return (f"{kw}: {' + '.join(part(m, i) for m, i in pairs)} "
                f"-> {target.name}{suffix}")

    def _extra_summon_flow(self, held: _CardInst, info: dict, zone: str) -> None:
        """Monster aus dem Extra Deck: Beschwoerungsformel mit Material-
        Auswahl oder Spezialbeschwoerung per Effekt (ohne Material)."""
        g = self.game
        options = [(m, _METHOD_LABELS[m]) for m in R.summon_methods(info)]
        options.append(("ss", "Spezialbeschwörung (Effekt)"))
        choice = self._choose(options)
        if choice is None:
            return
        zone_v = R.check_special_summon(g, held, info, zone, True, self._info_of)
        if choice == "ss":
            if not self._allowed(zone_v, f"Spezialbeschwörung {held.name}"):
                return
            self._push_undo()
            self._place(held, zone, "summoned")
            self._rec(f"SS {held.name} (ED)", held)
            self._rec_last_ed = held.card_id
            return
        label = _METHOD_LABELS[choice]

        def check(ch):
            v = R.check_materials(choice, info, self._pairs(ch))
            v.violations += zone_v.violations
            return v
        picked = self._pick_materials(f"{label}: {held.name}",
                                      R.material_candidates(g, choice, held),
                                      check, "Beschwören")
        if picked is None:
            return
        v = check(picked)
        v.notes = zone_v.notes
        if not self._allowed(v, f"{label} {held.name}"):
            return
        self._push_undo()
        formula = self._formula(choice, held, info, picked)
        for m in picked:
            if self._recording and m.owner == "me" and not m.token:
                self._rec_used[m.uid] = m.card_id
            if choice == "xyz":
                self._move(m, "attach")
                if not m.token:             # Marken sind kein Material
                    held.materials.append(m)
            else:
                self._move(m, "gy")
        self._place(held, zone, "summoned")
        self._rec(formula, held)
        self._rec_last_ed = held.card_id

    def _spell_flow(self, held: _CardInst, info: dict, zone: str) -> None:
        """Karte aus der Hand in Zauber/Fallen- oder Feldzone (mit Regeln)."""
        g = self.game
        rec = self._rec_own(held)
        if held.origin:                     # per Effekt aus einem Stapel
            if g.get(zone) is not None:
                return
            self._push_undo()
            src = held.origin
            self._place(held, zone)
            rec(f"{'Set' if held.face_down else 'Act'} {held.name} ({src})", held)
            return
        if R.is_monster(info):
            options = [("act", "Als Pendelskala aktivieren" if R.is_pendulum(info)
                        else "Ablegen (Effekt)")]
        else:
            options = [("act", "Aktivieren"), ("set", "Setzen")]
        choice = self._choose(options)
        if choice is None:
            return
        activate = choice == "act"
        v = R.check_spell_trap_from_hand(g, held, info, zone, activate)
        if not self._allowed(v, f"{'Aktivieren' if activate else 'Setzen'} {held.name}"):
            return
        self._push_undo()
        old = g.get(zone)
        if old is not None:                 # neuer Feldzauber ersetzt den alten
            self._rec_own(old)(f"Send {old.name} (Field -> GY)", old)
            self._move(old, "gy")
        held.face_down = not activate
        self._place(held, zone, *(() if activate else ("set",)))
        rec(f"{'Act' if activate else 'Set'} {held.name}", held)

    # -- Kontextmenue -----------------------------------------------------------

    def _on_card_context(self, inst: _CardInst, pos: QPoint) -> None:
        g = self.game
        menu = QMenu(self)
        acts: dict = {}

        def add(key, text):
            acts[menu.addAction(text)] = key

        info = self._info_of(inst.card_id)
        zone = g.zone_of(inst)
        in_hand = zone is None
        own = inst.owner == "me"
        mon_zone = zone is not None and zone.removeprefix("o")[0] in "me"
        # Extra-Deck-Monster kehren ins Extra Deck zurueck, nie in Hand/Deck.
        is_ed = own and inst.card_id in self._extra_ids
        if not in_hand:
            if self._rules_on and inst.face_down and mon_zone:
                add("flip", "Flippbeschwörung")
                add("face", "Aufdecken (Effekt)")
            elif self._rules_on and inst.face_down:
                add("face", "Aktivieren")
            else:
                add("face", "Offen" if inst.face_down else "Verdeckt")
            add("pos", "ATK" if inst.defense else "DEF")
            if own and mon_zone and not inst.face_down:
                add("attack", "Angreifen…")
            if inst.materials:
                add("detach", "Xyz-Material abhängen…")
            add("cnt+", "Zählmarke +1")
            if inst.counters:
                add("cnt-", "Zählmarke −1")
            if mon_zone:
                add("stats", "Werte ändern…")
            if own and not is_ed and not inst.token:
                add("hand", "→ Hand")
        if inst.token:
            add("gone", "Spielmarke entfernen")
        else:
            add("gy", "→ Friedhof")
            add("ban", "→ Verbannt")
            if own:
                add("deck", "→ Extra-Deck" if is_ed else "→ Deck (oben)")
            else:
                add("gone", "Zurück zum Gegner (entfernen)")
        menu.addSeparator()
        add("detail", "Details…")
        chosen = acts.get(menu.exec(pos))
        menu.deleteLater()
        if chosen is None:
            return
        if chosen == "detail":
            self._open_detail(inst.card_id)
            return
        if chosen == "attack":
            self._attack(inst)
            return
        if chosen == "stats":
            self._change_stats(inst, info)
            return
        if chosen == "detach":
            self._detach(inst)
            return
        if chosen in ("face", "pos", "flip") and self._rules_on:
            if not self._check_state_change(chosen, inst, info, zone):
                return
        self._push_undo()
        rec = self._rec_own(inst)
        here = "Hand" if in_hand else "Field"
        if chosen == "face":
            was_set = inst.face_down
            inst.face_down = not inst.face_down
            # Direkt nach dem Ablegen verdeckt gelegt = gesetzt: den eben
            # protokollierten 'Act X'/'NS X' dieses Exemplars zu 'Set X'.
            if (inst.face_down and self._recording and self._rec_log
                    and self._rec_last_uid == inst.uid):
                kw, _, rest = self._rec_log[-1].partition(" ")
                if kw in ("Act", "NS"):
                    self._rec_log[-1] = f"Set {rest}"
            # Aufdecken in der Zauber/Fallen- oder Feldzone = Aktivierung.
            if was_set and not inst.face_down and not mon_zone:
                rec(f"Act {inst.name}", inst)
        elif chosen == "flip":
            inst.face_down = inst.defense = False
            g.flags["pos_changed"].add(inst.uid)
            rec(f"Flip {inst.name}", inst)
        elif chosen == "pos":
            inst.defense = not inst.defense
            g.flags["pos_changed"].add(inst.uid)
        elif chosen in ("cnt+", "cnt-"):
            inst.counters = max(0, inst.counters + (1 if chosen == "cnt+" else -1))
        elif chosen == "hand":
            self._move(inst, "hand")
            rec(f"Send {inst.name} (Field -> Hand)", inst)
        elif chosen == "gy":
            self._move(inst, "gy")
            rec(f"Discard {inst.name}" if in_hand
                else f"Send {inst.name} (Field -> GY)", inst)
        elif chosen == "ban":
            self._move(inst, "banished")
            rec(f"Banish {inst.name} ({here})", inst)
        elif chosen == "deck" and is_ed:
            self._move(inst, "extra")
            rec(f"Send {inst.name} ({here} -> ED)", inst)
        elif chosen == "deck":
            self._move(inst, "deck")
            rec(f"Send {inst.name} ({here} -> Deck)", inst)
        elif chosen == "gone":
            self._move(inst, "gone")
        self._render()

    def _check_state_change(self, action: str, inst: _CardInst, info: dict,
                            zone: str) -> bool:
        """Regelpruefung fuer Aufdecken/Verdecken, Flipp und Positionswechsel
        eigener Karten (Gegner-Karten steuert der Benutzer frei)."""
        if inst.owner != "me":
            return True
        g = self.game
        mon_zone = zone[0] in "me"
        if action == "flip":
            return self._allowed(R.check_flip_summon(g, inst, info),
                                 f"Flippbeschwörung {inst.name}")
        if action == "pos":
            return self._allowed(R.check_position_change(g, inst, info),
                                 f"Positionswechsel {inst.name}")
        if not inst.face_down:
            return self._allowed(R.check_turn_face_down(inst, info, zone),
                                 f"{inst.name} verdecken")
        if not mon_zone:
            return self._allowed(R.check_activate_set(g, inst, info),
                                 f"{inst.name} aktivieren")
        return True                         # 'Aufdecken (Effekt)'

    def _change_stats(self, inst: _CardInst, info: dict) -> None:
        base = (info.get("atk"),
                None if R.kind(info) == "link" else info.get("def"),
                info.get("level"))
        dlg = _StatDialog(inst.name, base,
                          (inst.atk_mod, inst.def_mod, inst.level_mod), self)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        vals = dlg.values()
        dlg.deleteLater()
        if accepted and vals != (inst.atk_mod, inst.def_mod, inst.level_mod):
            self._push_undo()
            inst.atk_mod, inst.def_mod, inst.level_mod = vals
            self._render()

    def _detach(self, inst: _CardInst) -> None:
        """Ein Xyz-Material abhaengen (in den Friedhof seines Besitzers)."""
        mats = inst.materials
        if len(mats) == 1:
            i = 0
        else:
            dlg = _PileDialog(f"Material von {inst.name}", [m.name for m in mats],
                              self, actions=[("Abhängen (→ Friedhof)", "gy")])
            accepted = dlg.exec() == QDialog.DialogCode.Accepted
            dlg.deleteLater()
            if not (accepted and dlg.selected >= 0):
                return
            i = dlg.selected
        self._push_undo()
        m = mats.pop(i)
        self.game.side(m.owner).gy.append(m)
        self._rec_own(m)(f"Send {m.name} (Xyz-Material -> GY)", m)
        self._render()

    def _attack(self, attacker: _CardInst) -> None:
        """Angriff auf ein Gegner-Monster oder direkt, mit Schadensberechnung
        (zerstoerte Monster in den GY, Kampfschaden auf die LP)."""
        g = self.game
        targets = g.monsters("opp")
        options = [(str(i), ("verdeckte Karte" if t.face_down else t.name)
                    + (" (Verteidigung)" if t.defense else ""))
                   for i, t in enumerate(targets)]
        options.append(("direct", "Direkter Angriff"))
        choice = self._choose(options)
        if choice is None:
            return
        target = None if choice == "direct" else targets[int(choice)]
        if not self._allowed(R.check_attack(g, attacker, target),
                             f"Angriff mit {attacker.name}"):
            return
        self._push_undo()
        g.flags["attacked"].add(attacker.uid)
        atk = R.atk_of(attacker, self._info_of(attacker.card_id))
        if target is None:
            res = R.damage_calc(atk, None)
            desc = f"{attacker.name} ({atk}) greift direkt an"
        else:
            if target.face_down:
                target.face_down = False
                self._log(f"{target.name} wird aufgedeckt")
            t_info = self._info_of(target.card_id)
            t_atk, t_def = R.atk_of(target, t_info), R.def_of(target, t_info)
            res = R.damage_calc(atk, (t_atk, t_def, target.defense))
            shown = f"DEF {t_def}" if target.defense else f"ATK {t_atk}"
            desc = f"{attacker.name} ({atk}) → {target.name} ({shown})"
        g.me.lp = max(0, g.me.lp - res.damage_me)
        g.opp.lp = max(0, g.opp.lp - res.damage_opp)
        outcome = []
        if res.target_destroyed:
            outcome.append(f"{target.name} zerstört")
            self._move(target, "gy")
        if res.attacker_destroyed:
            outcome.append(f"{attacker.name} zerstört")
            self._move(attacker, "gy")
        if res.damage_opp:
            outcome.append(f"Gegner −{res.damage_opp} LP")
        if res.damage_me:
            outcome.append(f"du −{res.damage_me} LP")
        self._log(f"⚔ {desc}: " + (", ".join(outcome) or "kein Effekt"))
        self._check_lp()
        self._render()

    def _open_detail(self, card_id: int) -> None:
        if card_id < 0:                     # Spielmarke: keine Kartendaten
            return
        if self._detail_dialog is None:
            self._detail_dialog = CardDetailDialog(self.repo, self)
        self._detail_dialog.load(card_id)
        self._detail_dialog.show()
        self._detail_dialog.raise_()
        self._detail_dialog.activateWindow()

    # -- Bilder -------------------------------------------------------------

    def _pixmap_for(self, inst: _CardInst, board: bool = False) -> QPixmap:
        if inst.face_down:
            pm = card_back_pixmap(_CARD_W, _CARD_H)
        else:
            pm = (lookup_card_pixmap(inst.card_id, _CARD_W, _CARD_H, "board")
                  if inst.card_id > 0 else None)
            if pm is None:
                if inst.card_id > 0:
                    self._ensure_image(inst.card_id)
                pm = placeholder_pixmap(inst.name, _CARD_W, _CARD_H)
        if board:
            pm = self._decorate(pm, inst)
        if inst.defense:
            pm = pm.transformed(
                QTransform().rotate(90), Qt.TransformationMode.SmoothTransformation
            )
        return pm

    def _decorate(self, pm: QPixmap, inst: _CardInst) -> QPixmap:
        """Brett-Anzeige: Link-Pfeile, Werte-Leiste (ATK/DEF inkl.
        Effekt-Aenderungen, gold = veraendert), Xyz-Material- und
        Zaehlmarken-Anzahl."""
        info = self._info_of(inst.card_id)
        zone = (self.game.zone_of(inst) or "").removeprefix("o")
        show_stats = (zone[:1] in ("m", "e") and not inst.face_down
                      and R.is_monster(info))
        if not (inst.materials or inst.counters or show_stats):
            return pm
        pm = QPixmap(pm)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = pm.width(), pm.height()
        gold, text, bg = QColor(_GOLD), QColor(_TEXT), QColor(_BG)
        f = p.font()
        f.setPointSize(6)
        f.setBold(True)
        p.setFont(f)
        if show_stats:
            d = R.def_of(inst, info)
            stat = f"{R.atk_of(inst, info)}" + ("" if d is None else f" / {d}")
            modded = inst.atk_mod or inst.def_mod or inst.level_mod
            p.fillRect(0, h - 13, w, 13, bg)
            p.setPen(gold if modded else text)
            p.drawText(0, h - 13, w, 13, Qt.AlignmentFlag.AlignCenter, stat)
            # Link-Pfeile ueber der Werte-Leiste; Gegner-Karten stehen aus
            # deiner Sicht auf dem Kopf (Pfeile um 180° gedreht).
            p.setPen(QPen(bg, 1))
            p.setBrush(QBrush(gold))
            flip = -1 if inst.owner == "opp" else 1
            cx, cy, s = w / 2, h / 2, 5
            for m in info.get("link_markers") or ():
                dx, dy = (flip * c for c in _ARROW_DIRS.get(m, (0, 0)))
                tip = QPointF(cx + dx * (w / 2 - 2), cy + dy * (h / 2 - 2))
                base = QPointF(tip.x() - dx * s * 1.4, tip.y() - dy * s * 1.4)
                nx, ny = -dy, dx          # Senkrechte fuer die Dreiecksbasis
                p.drawPolygon(QPolygonF([
                    tip, QPointF(base.x() + nx * s, base.y() + ny * s),
                    QPointF(base.x() - nx * s, base.y() - ny * s),
                ]))
        badges = []
        if inst.materials:
            badges.append(f"◆{len(inst.materials)}")
        if inst.counters:
            badges.append(f"●{inst.counters}")
        if badges:
            label = " ".join(badges)
            p.fillRect(2, 2, 8 + 6 * len(label), 12, bg)
            p.setPen(gold)
            p.drawText(4, 2, w, 12, Qt.AlignmentFlag.AlignLeft, label)
        p.end()
        return pm

    def _ensure_image(self, card_id: int) -> None:
        if card_id in self._loading_imgs:
            return
        self._loading_imgs.add(card_id)
        image_pool().start(
            ImageLoader(card_id, IMAGE_URL.format(card_id), self._img_signals)
        )

    def _on_img_loaded(self, card_id: int, img: QImage) -> None:
        QPixmapCache.insert(
            f"board:{card_id}", scale_pixmap(QPixmap.fromImage(img), _CARD_W, _CARD_H)
        )
        self._loading_imgs.discard(card_id)
        self._render_timer.start()   # gebuendelt neu zeichnen

    # -- Rendern ------------------------------------------------------------

    def _tooltip(self, inst: _CardInst) -> str:
        if inst.face_down and inst.owner == "opp":
            return "Verdeckte Karte des Gegners"
        info = self._info_of(inst.card_id)
        lines = [inst.name + (" (Spielmarke)" if inst.token else "")]
        if R.is_monster(info):
            d = R.def_of(inst, info)
            lvl = R.level_of(inst, info)
            head = (f"Link-{info.get('link_value')}" if R.kind(info) == "link"
                    else f"Rang {info.get('level')}" if R.kind(info) == "xyz"
                    else f"Stufe {lvl}" if lvl else "")
            lines.append(f"{head}  ATK {R.atk_of(inst, info)}"
                         + ("" if d is None else f" / DEF {d}"))
        if inst.materials:
            lines.append("Material: " + ", ".join(m.name for m in inst.materials))
        if inst.counters:
            lines.append(f"Zählmarken: {inst.counters}")
        return "\n".join(lines)

    def _make_card(self, inst: _CardInst, board: bool = False) -> _BoardCard:
        card = _BoardCard(inst, self._pixmap_for(inst, board),
                          held=(inst is self._held))
        card.setToolTip(self._tooltip(inst))
        card.clicked.connect(self._on_card_clicked)
        card.context.connect(self._on_card_context)
        return card

    def _zone_placeholder(self, key: str) -> QLabel:
        key = key.removeprefix("o")
        lbl = QLabel(self._PLACEMENT_LABELS.get(key, self._PLACEMENT_LABELS.get(key[0], "")))
        lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lbl.setObjectName("ZonePlaceholder")
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
            img.setPixmap(card_back_pixmap(_CARD_W, _CARD_H))
        v.addWidget(img, alignment=Qt.AlignmentFlag.AlignCenter)
        cap = QLabel(f"{name} {count}")
        cap.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        cap.setObjectName("PileCaption")
        v.addWidget(cap, alignment=Qt.AlignmentFlag.AlignCenter)
        return host

    def _toggle_opp_side(self, show: bool) -> None:
        for key, z in self._zones.items():
            if key.startswith("o"):
                z.setVisible(show)

    def _hint_zones(self) -> set[str]:
        """Gueltige Zielzonen fuer ein aufgenommenes Extra-Deck-Monster."""
        held = self._held
        if not (self._rules_on and held is not None and held.origin == "ED"):
            return set()
        g = self.game
        info = self._info_of(held.card_id)
        return {key for key in [f"m{i}" for i in range(5)] + ["e0", "e1"]
                if g.get(key) is None and not R.check_special_summon(
                    g, held, info, key, True, self._info_of).violations}

    def _render(self) -> None:
        g = self.game
        slots = {"field": g.me.field, "ofield": g.opp.field,
                 "e0": g.emz[0], "e1": g.emz[1]}
        for i in range(5):
            for prefix, side in (("", g.me), ("o", g.opp)):
                slots[f"{prefix}m{i}"] = side.m[i]
                slots[f"{prefix}s{i}"] = side.s[i]
        hints = self._hint_zones()
        for key, inst in slots.items():
            self._zones[key].set_content(
                self._make_card(inst, board=True) if inst is not None
                else self._zone_placeholder(key)
            )
            self._zones[key].set_hint(key in hints)
        me, opp = g.me, g.opp
        self._zones["deck"].set_content(
            self._pile_widget("Deck", len(me.deck), back=True))
        self._zones["extra"].set_content(
            self._pile_widget("Extra", len(me.extra), back=True))
        for key, name, pile in (("gy", "GY", me.gy), ("banished", "Bann", me.banished),
                                ("ogy", "GY", opp.gy), ("obanished", "Bann", opp.banished)):
            self._zones[key].set_content(
                self._pile_widget(name, len(pile), top=pile[-1] if pile else None))
        # Hand
        while self._hand_row.count():
            old = self._hand_row.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
        for inst in me.hand:
            self._hand_row.addWidget(self._make_card(inst))
        self.hand_label.setText(f"Hand ({len(me.hand)})")

        # Zug, Phase, LP
        self.turn_label.setText(
            f"Zug {g.turn} · " + ("Du" if g.my_turn else "Gegner"))
        for key, b in self.phase_btns.items():
            b.setChecked(g.my_turn and g.phase == key)
            b.setEnabled(g.my_turn)
        self.next_phase_btn.setEnabled(g.my_turn)
        self.end_turn_btn.setText("Zug beenden" if g.my_turn else "Gegnerzug beenden")
        for owner, lbl in self.lp_labels.items():
            lbl.setText(f"{g.side(owner).lp} LP")

        held = (
            f"  ·  aufgenommen: {self._held.name} — Zone anklicken"
            if self._held else ""
        )
        if (self._held is not None and self._held.origin == "ED"
                and self._rules_on and not hints):
            held += " (keine regelkonforme Zone frei)"
        rec = f"  ·  ● Aufzeichnung ({len(self._rec_log)})" if self._recording else ""
        ns = f"  ·  NS {g.ns_used}/{1 + g.ns_extra}" if g.my_turn else ""
        self.status.setText(
            f"Deck {len(me.deck)}  ·  Extra {len(me.extra)}  ·  "
            f"GY {len(me.gy)}  ·  Verbannt {len(me.banished)}{ns}{rec}{held}"
        )
        self.undo_btn.setEnabled(bool(self._undo))
        self.rec_btn.setText(
            f"■ Speichern… ({len(self._rec_log)})" if self._recording
            else "● Aufzeichnen"
        )
        self.rec_btn.setEnabled(self._recording or self._deck_id is not None)
