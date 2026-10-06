"""Suche-Tab: Filter nach dem Aufbau der Karte, Trefferliste, Details.

Oben waehlt ein Umschalter die Kartenart (Alle/Monster/Zauber/Falle); es
erscheinen nur die Filter, die fuer diese Art etwas bedeuten -- bei Monstern
Monsterart, Eigenschaft, Typ, Merkmale und die Werte (Stufe/Rang, Link,
Pendelskala, ATK/DEF), bei Zaubern/Fallen das Symbol. Innerhalb einer
Chip-Gruppe gilt ODER, zwischen den Filtern UND; ausgeblendete Gruppen
wirken nicht. Die Treffer stehen als schlichte Liste (Name + ATK/DEF) in
der Mitte; die Auswahl zeigt die Karte im DetailPanel rechts.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QCompleter, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSplitter, QVBoxLayout, QWidget
)

import yugioh_db as ydb

from .carddetail import DetailPanel
from .labels import ATTR_DE, RACE_DE, SPELL_KIND_DE, TRAP_KIND_DE
from .repository import CardRepository


KINDS = (("all", "Alle"), ("monster", "Monster"), ("spell", "Zauber"),
         ("trap", "Falle"))
FRAMES = (("normal", "Normal"), ("effect", "Effekt"), ("ritual", "Ritual"),
          ("fusion", "Fusion"), ("synchro", "Synchro"), ("xyz", "Xyz"),
          ("pendulum", "Pendel"), ("link", "Link"))
ATTRIBUTES = ("LIGHT", "DARK", "EARTH", "WATER", "FIRE", "WIND", "DIVINE")
TRAITS = (("tuner", "Empfänger"), ("flip", "Flipp"), ("gemini", "Zwilling"),
          ("spirit", "Spirit"), ("union", "Union"), ("toon", "Toon"))

RESULT_LIMIT = 300


def result_text(card) -> str:
    """Zeile der Trefferliste: Name, bei Monstern [ATK / DEF] -- Link-Monster
    ohne DEF (die ist NULL; nie "None" anzeigen)."""
    name = card["name_de"] or card["name"]
    if card["atk"] is None:
        return name
    if card["def"] is None:
        return f"{name}  [ATK {card['atk']}]"
    return f"{name}  [ATK {card['atk']} / DEF {card['def']}]"


def _range_spin(maximum: int, step: int = 1, egal: int = -1) -> QSpinBox:
    """Zahlenfeld, dessen kleinster Wert 'egal' bedeutet (kein Filter)."""
    sp = QSpinBox()
    sp.setRange(egal, maximum)
    sp.setSingleStep(step)
    sp.setSpecialValueText("egal")
    sp.setValue(egal)
    sp.setMinimumWidth(64)
    # Alle Bereichsfelder gleich breit, egal wie gross der Hoechstwert ist.
    sp.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    return sp


def _spin_value(sp: QSpinBox):
    return None if sp.value() == sp.minimum() else sp.value()


class SearchView(QSplitter):
    """Suche-Tab: [Filter | Treffer | DetailPanel] als Splitter (der
    Sitzungsstatus sichert dessen Aufteilung)."""

    def __init__(self, repo: CardRepository, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.repo = repo
        self._loading = True        # unterdrueckt Suchen waehrend des Aufbaus
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.search)

        self.addWidget(self._build_filter_panel())
        self.addWidget(self._build_results())
        self.detail = DetailPanel(repo)
        self.addWidget(self.detail)
        self.setSizes([290, 380, 440])
        self.setStretchFactor(1, 1)
        self._update_visibility()
        if repo.exists():
            self.populate_filters()
        self._loading = False

    # -- Aufbau ---------------------------------------------------------------

    def _chip(self, label: str, key) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("Chip")
        btn.setCheckable(True)
        btn.setProperty("key", key)
        btn.toggled.connect(self._changed)
        return btn

    def _chip_grid(self, items, columns: int) -> tuple[QWidget, list]:
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        chips = []
        for i, (key, label) in enumerate(items):
            chip = self._chip(label, key)
            grid.addWidget(chip, i // columns, i % columns)
            chips.append(chip)
        return host, chips

    @staticmethod
    def _caption(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("FilterCaption")
        return lbl

    def _range_row(self, lo: QSpinBox, hi: QSpinBox) -> QWidget:
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(lo)
        h.addWidget(QLabel("–"))
        h.addWidget(hi)
        for sp in (lo, hi):
            sp.valueChanged.connect(self._changed)
        return host

    def _build_filter_panel(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Name, Text, Passcode oder Set-Nr. …")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setToolTip(
            "Name oder Kartentext (Wortanfang genügt), die Kartennummer "
            "(Passcode, z. B. 14558127) oder eine Set-Nummer (z. B. RA01-DE008)")
        self.search_box.textChanged.connect(self._changed)
        self.search_box.returnPressed.connect(self.search)
        v.addWidget(self.search_box)

        # Kartenart: exklusiver Umschalter
        seg = QHBoxLayout()
        seg.setSpacing(0)
        self.kind_group = QButtonGroup(self)
        self.kind_buttons: dict[str, QPushButton] = {}
        for key, label in KINDS:
            btn = QPushButton(label)
            btn.setObjectName("Segment")
            btn.setCheckable(True)
            self.kind_group.addButton(btn)
            self.kind_buttons[key] = btn
            seg.addWidget(btn)
        self.kind_buttons["all"].setChecked(True)
        self.kind_group.buttonToggled.connect(self._on_kind_toggled)
        v.addLayout(seg)

        # Monster
        self.monster_box = QGroupBox("Monster")
        mv = QVBoxLayout(self.monster_box)
        mv.addWidget(self._caption("Monsterart"))
        host, self.frame_chips = self._chip_grid(FRAMES, 4)
        mv.addWidget(host)
        mv.addWidget(self._caption("Eigenschaft"))
        host, self.attr_chips = self._chip_grid(
            [(a, ATTR_DE[a]) for a in ATTRIBUTES], 3)
        mv.addWidget(host)
        mv.addWidget(self._caption("Merkmal (Typzeile)"))
        host, self.trait_chips = self._chip_grid(TRAITS, 3)
        mv.addWidget(host)
        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        self.race_cb = QComboBox()
        self.race_cb.addItem("(alle)", None)
        self.race_cb.currentIndexChanged.connect(self._changed)
        form.addRow("Typ", self.race_cb)
        self.level_min, self.level_max = _range_spin(13), _range_spin(13)
        form.addRow("Stufe/Rang", self._range_row(self.level_min, self.level_max))
        self.link_min, self.link_max = _range_spin(6, egal=0), _range_spin(6, egal=0)
        form.addRow("Link", self._range_row(self.link_min, self.link_max))
        self.scale_min, self.scale_max = _range_spin(13), _range_spin(13)
        form.addRow("Pendelskala", self._range_row(self.scale_min, self.scale_max))
        self.atk_min = _range_spin(5000, 100, egal=-100)
        self.atk_max = _range_spin(5000, 100, egal=-100)
        form.addRow("ATK", self._range_row(self.atk_min, self.atk_max))
        self.def_min = _range_spin(5000, 100, egal=-100)
        self.def_max = _range_spin(5000, 100, egal=-100)
        form.addRow("DEF", self._range_row(self.def_min, self.def_max))
        mv.addLayout(form)
        v.addWidget(self.monster_box)

        # Zauber / Fallen: Symbol
        self.spell_box = QGroupBox("Zauber-Symbol")
        sl = QVBoxLayout(self.spell_box)
        host, self.spell_chips = self._chip_grid(list(SPELL_KIND_DE.items()), 3)
        sl.addWidget(host)
        v.addWidget(self.spell_box)
        self.trap_box = QGroupBox("Fallen-Symbol")
        tl = QVBoxLayout(self.trap_box)
        host, self.trap_chips = self._chip_grid(list(TRAP_KIND_DE.items()), 3)
        tl.addWidget(host)
        v.addWidget(self.trap_box)

        # Allgemein
        general = QGroupBox("Allgemein")
        gf = QFormLayout(general)
        self.arch_cb = QComboBox()
        self.arch_cb.setEditable(True)
        self.arch_cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.arch_cb.addItem("(alle)", None)
        completer = self.arch_cb.completer()
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.arch_cb.currentIndexChanged.connect(self._changed)
        self.arch_cb.lineEdit().editingFinished.connect(self._changed)
        gf.addRow("Archetyp", self.arch_cb)
        self.wording_cb = QComboBox()
        self.wording_cb.addItem("(alle)", None)
        for key, label in ydb.WORDING_FILTERS:
            self.wording_cb.addItem(label, key)
        self.wording_cb.setToolTip(
            "Nach dem Wortlaut des Kartentexts filtern (Lesehilfe, heuristisch) "
            "— z. B. Handtraps, Schnelleffekte, hartes OPT")
        self.wording_cb.currentIndexChanged.connect(self._changed)
        gf.addRow("Wortlaut", self.wording_cb)
        self.only_coll = QCheckBox("Nur meine Sammlung")
        self.only_coll.toggled.connect(self._changed)
        gf.addRow(self.only_coll)
        v.addWidget(general)

        reset = QPushButton("Filter zurücksetzen")
        reset.clicked.connect(self.reset_filters)
        v.addWidget(reset)
        v.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(250)
        return scroll

    def _build_results(self) -> QWidget:
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        self.results = QListWidget()
        self.results.currentItemChanged.connect(self._on_select)
        v.addWidget(self.results, stretch=1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("HintLabel")
        v.addWidget(self.count_label)
        return host

    # -- Filterzustand ----------------------------------------------------------

    def populate_filters(self) -> None:
        """Typ- und Archetyp-Auswahl aus der Datenbank befuellen (auch nach
        einem Daten-Update); die aktuelle Auswahl bleibt erhalten."""
        race, arch = self.race_cb.currentData(), self.archetype()
        for cb in (self.race_cb, self.arch_cb):
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("(alle)", None)
        races = sorted(self.repo.monster_races(),
                       key=lambda r: RACE_DE.get(r, r).casefold())
        for r in races:
            self.race_cb.addItem(RACE_DE.get(r, r), r)
        for a in self.repo.distinct("archetype"):
            self.arch_cb.addItem(a, a)
        self.race_cb.setCurrentIndex(max(self.race_cb.findData(race), 0))
        self.arch_cb.setCurrentIndex(max(self.arch_cb.findData(arch), 0))
        for cb in (self.race_cb, self.arch_cb):
            cb.blockSignals(False)

    def kind(self) -> str:
        for key, btn in self.kind_buttons.items():
            if btn.isChecked():
                return key
        return "all"

    def set_kind(self, key: str) -> None:
        self.kind_buttons[key].setChecked(True)

    def archetype(self) -> str | None:
        """Archetyp aus dem (editierbaren) Feld; unbekannter Text = kein
        Filter, Gross-/Kleinschreibung egal."""
        text = self.arch_cb.currentText().strip()
        if not text or text == "(alle)":
            return None
        idx = self.arch_cb.findText(text, Qt.MatchFlag.MatchFixedString)
        return self.arch_cb.itemData(idx) if idx > 0 else None

    @staticmethod
    def _checked(chips) -> list:
        return [c.property("key") for c in chips if c.isChecked()]

    def filters(self) -> dict:
        """Parameter fuer CardRepository.query -- nur die Gruppen der
        gewaehlten Kartenart wirken."""
        kind = self.kind()
        f = {
            "text": self.search_box.text(),
            "kind": None if kind == "all" else kind,
            "archetype": self.archetype(),
            "wording": self.wording_cb.currentData(),
            "only_collection": self.only_coll.isChecked(),
        }
        if kind == "monster":
            f.update(
                frames=self._checked(self.frame_chips),
                attributes=self._checked(self.attr_chips),
                traits=self._checked(self.trait_chips),
                race=self.race_cb.currentData(),
                level_min=_spin_value(self.level_min),
                level_max=_spin_value(self.level_max),
                link_min=_spin_value(self.link_min),
                link_max=_spin_value(self.link_max),
                scale_min=_spin_value(self.scale_min),
                scale_max=_spin_value(self.scale_max),
                atk_min=_spin_value(self.atk_min),
                atk_max=_spin_value(self.atk_max),
                def_min=_spin_value(self.def_min),
                def_max=_spin_value(self.def_max),
            )
        elif kind == "spell":
            f["st_kinds"] = self._checked(self.spell_chips)
        elif kind == "trap":
            f["st_kinds"] = self._checked(self.trap_chips)
        return f

    def reset_filters(self) -> None:
        """Alle Filter (ausser dem Suchtext) auf 'egal' -- eine Suche."""
        self._loading = True
        try:
            self.set_kind("all")
            for chip in (self.frame_chips + self.attr_chips + self.trait_chips
                         + self.spell_chips + self.trap_chips):
                chip.setChecked(False)
            for sp in (self.level_min, self.level_max, self.link_min,
                       self.link_max, self.scale_min, self.scale_max,
                       self.atk_min, self.atk_max, self.def_min, self.def_max):
                sp.setValue(sp.minimum())
            for cb in (self.race_cb, self.arch_cb, self.wording_cb):
                cb.setCurrentIndex(0)
            self.only_coll.setChecked(False)
        finally:
            self._loading = False
        self.search()

    def _update_visibility(self) -> None:
        kind = self.kind()
        self.monster_box.setVisible(kind == "monster")
        self.spell_box.setVisible(kind == "spell")
        self.trap_box.setVisible(kind == "trap")

    def _on_kind_toggled(self, _btn, checked: bool) -> None:
        if checked:
            self._update_visibility()
            self._changed()

    def _changed(self, *_) -> None:
        if not self._loading:
            self._timer.start()

    # -- Suche + Treffer ----------------------------------------------------------

    def search(self) -> None:
        self._timer.stop()
        if not self.repo.exists():
            return
        f = self.filters()
        if f["wording"]:
            # Merkmale einmalig vorberechnen (~1 s, danach sofort).
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                ydb.ensure_wording_flags(self.repo.db_path)
            finally:
                QApplication.restoreOverrideCursor()
        cards = self.repo.query(**f, limit=RESULT_LIMIT + 1)
        more = len(cards) > RESULT_LIMIT
        cards = cards[:RESULT_LIMIT]
        current = self.current_card_id()
        self.results.blockSignals(True)
        self.results.clear()
        for c in cards:
            item = QListWidgetItem(result_text(c))
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            item.setToolTip(c["name"])                  # englischer Name
            self.results.addItem(item)
        self.results.blockSignals(False)
        if current is not None:
            self.select_card(current, show=False)
        self.count_label.setText(
            f"{RESULT_LIMIT}+ Treffer – Suche eingrenzen" if more
            else f"{len(cards)} Treffer")

    def refresh(self) -> None:
        """Treffer neu laden ('Nur meine Sammlung' kann sich in anderen Tabs
        geaendert haben); die Auswahl bleibt."""
        if self.repo.exists():
            self.search()

    def result_ids(self) -> list[int]:
        """card_ids der Treffer in Anzeigereihenfolge."""
        return [self.results.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.results.count())]

    def current_card_id(self) -> int | None:
        item = self.results.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def select_card(self, card_id: int, show: bool = True) -> bool:
        """Eintrag der Karte waehlen (falls unter den Treffern)."""
        for i in range(self.results.count()):
            if self.results.item(i).data(Qt.ItemDataRole.UserRole) == card_id:
                self.results.blockSignals(True)
                self.results.setCurrentRow(i)
                self.results.blockSignals(False)
                if show:
                    self._on_select()
                return True
        return False

    def _on_select(self, *_) -> None:
        card_id = self.current_card_id()
        if card_id is None:
            return
        card = self.repo.get_card(card_id)
        if card:
            self.detail.show_card(card)

    def show_card(self, card_id: int) -> bool:
        """Karte im DetailPanel zeigen (z. B. aus dem Deck-Tab)."""
        card = self.repo.get_card(card_id)
        if card is None:
            return False
        self.select_card(card_id, show=False)
        self.detail.show_card(card)
        return True
