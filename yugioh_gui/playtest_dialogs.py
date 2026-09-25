"""Dialoge des Spielfeld-Tabs (Blatt-Modul, importiert keine Views).

Stapel-Auswahl (_PileDialog), Deck-Top ansehen (_TopDeckDialog),
Recorder-Speichern (_RecordSaveDialog) und die Regelwerk-Dialoge:
Material-/Tribut-/Abwurf-Auswahl mit Live-Pruefung (_MaterialDialog),
Werte-Aenderung (_StatDialog) und Spielmarken (_TokenDialog). Alle sind
QDialog-Unterklassen mit exec() -- Tests ersetzen exec() auf Klassenebene.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSpinBox, QVBoxLayout
)


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


class _MaterialDialog(QDialog):
    """Auswahl mehrerer Karten (Material, Tribut, Abwurf) mit Live-Pruefung:
    'check(gewaehlte Exemplare) -> Verdict' fuellt die Befund-Zeile. Bestaetigen
    geht immer -- was ein Verstoss bedeutet, entscheidet der Regel-Modus des
    Aufrufers. Ergebnis: self.chosen (Exemplare in Listen-Reihenfolge)."""

    def __init__(self, title: str, candidates: list, labels: list[str],
                 check, ok_text: str = "Beschwören", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 440)
        self._cands = list(candidates)
        self._check = check
        self.chosen: list = []
        lay = QVBoxLayout(self)
        self.listw = QListWidget()
        for text in labels:
            item = QListWidgetItem(text, self.listw)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
        self.listw.itemChanged.connect(self._update)
        lay.addWidget(self.listw, stretch=1)
        self.verdict = QLabel()
        self.verdict.setWordWrap(True)
        lay.addWidget(self.verdict)
        row = QHBoxLayout()
        row.addStretch()
        ok = QPushButton(ok_text)
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        cancel = QPushButton("Abbrechen")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)
        self._update()

    def set_checked(self, rows) -> None:
        """Zeilen ankreuzen (fuer Vorbelegung und Tests)."""
        for r in rows:
            self.listw.item(r).setCheckState(Qt.CheckState.Checked)

    def _update(self, *_a) -> None:
        self.chosen = [
            self._cands[i] for i in range(self.listw.count())
            if self.listw.item(i).checkState() == Qt.CheckState.Checked
        ]
        v = self._check(self.chosen)
        if v.violations:
            self.verdict.setObjectName("RuleWarn")
            self.verdict.setText("⚠ " + "\n⚠ ".join(v.violations))
        else:
            self.verdict.setObjectName("RuleOk")
            self.verdict.setText("✓ regelkonform"
                                 + "".join(f"\nℹ {n}" for n in v.notes))
        self.verdict.style().unpolish(self.verdict)
        self.verdict.style().polish(self.verdict)


class _StatDialog(QDialog):
    """Werte einer Feldkarte durch Effekte aendern: ATK-/DEF-/Stufen-
    Modifikator relativ zum Kartenwert (0 = unveraendert)."""

    def __init__(self, name: str, base: tuple, mods: tuple, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Werte ändern — {name}")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.spins: list[QSpinBox] = []
        for label, b, m, step in zip(("ATK", "DEF", "Stufe"), base, mods,
                                     (100, 100, 1)):
            sp = QSpinBox()
            sp.setRange(-20000 if step == 100 else -12, 20000 if step == 100 else 12)
            sp.setSingleStep(step)
            sp.setValue(m)
            sp.setPrefix("+" if m >= 0 else "")
            sp.valueChanged.connect(lambda val, s=sp: s.setPrefix("+" if val >= 0 else ""))
            form.addRow(f"{label} (Karte: {'—' if b is None else b}):", sp)
            self.spins.append(sp)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addStretch()
        ok = QPushButton("Übernehmen")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        cancel = QPushButton("Abbrechen")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)

    def values(self) -> tuple[int, int, int]:
        return tuple(sp.value() for sp in self.spins)


class _TokenDialog(QDialog):
    """Spielmarke erzeugen: Name, Stufe, ATK/DEF, Anzahl und Seite. Die
    Werte stammen aus dem ausloesenden Karteneffekt -- der Benutzer traegt
    sie ein."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Spielmarke erzeugen")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit("Spielmarke")
        form.addRow("Name:", self.name_edit)
        self.level = QSpinBox(); self.level.setRange(1, 12); self.level.setValue(1)
        form.addRow("Stufe:", self.level)
        self.atk = QSpinBox(); self.atk.setRange(0, 10000); self.atk.setSingleStep(100)
        form.addRow("ATK:", self.atk)
        self.def_ = QSpinBox(); self.def_.setRange(0, 10000); self.def_.setSingleStep(100)
        form.addRow("DEF:", self.def_)
        self.count = QSpinBox(); self.count.setRange(1, 5)
        form.addRow("Anzahl:", self.count)
        self.side = QComboBox()
        self.side.addItem("Deine Seite", "me")
        self.side.addItem("Gegnerseite", "opp")
        form.addRow("Seite:", self.side)
        self.defense = QCheckBox("in Verteidigungsposition")
        form.addRow("", self.defense)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addStretch()
        ok = QPushButton("Erzeugen")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        cancel = QPushButton("Abbrechen")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)

    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip() or "Spielmarke",
            "level": self.level.value(), "atk": self.atk.value(),
            "def": self.def_.value(), "count": self.count.value(),
            "owner": self.side.currentData(),
            "defense": self.defense.isChecked(),
        }
