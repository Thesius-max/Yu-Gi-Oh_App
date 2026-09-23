"""Karten-Detailansichten und -Suche (von mehreren Tabs geteilt).

DetailPanel (Suche-Tab, wirkt per Callbacks auf Deck/Kombo),
CardDetailDialog (read-only Pop-up in Sammlung/Deck), CardSearchDialog
(Karte nachschlagen, z.B. '+ Baustein' im Kombo-Editor) und der
gemeinsame Uebersetzungs-Editor edit_card_translation ('DE'-Knopf).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox,
    QTextEdit, QVBoxLayout, QWidget
)

import yugioh_db as ydb

from .images import CardImageView
from .labels import ATTR_DE, RACE_DE, TYPE_DE
from .repository import CardRepository


# ---------------------------------------------------------------------------
# Karten-Suchdialog (für das Hinzufügen aus dem Deck-Tab)
# ---------------------------------------------------------------------------

class CardSearchDialog(QDialog):
    def __init__(self, repo: "CardRepository", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Karte hinzufügen")
        self.resize(420, 520)
        self.repo = repo
        self.selected_id: int | None = None

        layout = QVBoxLayout(self)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Name oder Kartentext suchen …")
        self.search_box.textChanged.connect(self._search)
        layout.addWidget(self.search_box)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list, stretch=1)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Hinzufügen")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Abbrechen")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self.search_box.setFocus()

    def _search(self, text: str) -> None:
        self.list.clear()
        if not text.strip():
            return
        for c in self.repo.query(text=text, limit=80):
            item = QListWidgetItem(c["name_de"] or c["name"])
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def chosen_card_id(self) -> int | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


# ---------------------------------------------------------------------------
# Detailansicht (rechte Spalte)
# ---------------------------------------------------------------------------

def _format_card_stats(card) -> str:
    """Karten-Stat-Zeile mit deutscher Beschriftung (Typ • Sorte • Attribut •
    Stufe • ATK/DEF • Archetyp). Geteilt von DetailPanel und CardDetailDialog."""
    parts = []
    if card["type"]:
        parts.append(TYPE_DE.get(card["type"], card["type"]))
    if card["race"]:
        parts.append(RACE_DE.get(card["race"], card["race"]))
    if card["attribute"]:
        parts.append(ATTR_DE.get(card["attribute"], card["attribute"]))
    if card["level"] is not None:
        parts.append(f"Stufe {card['level']}")
    if card["atk"] is not None or card["def"] is not None:
        atk = card["atk"] if card["atk"] is not None else "—"
        # Link-Monster haben keine DEF (def ist NULL) -> nicht "None" anzeigen.
        if card["def"] is not None:
            parts.append(f"ATK {atk} / DEF {card['def']}")
        else:
            parts.append(f"ATK {atk}")
    if card["archetype"]:
        parts.append(f"Archetyp: {card['archetype']}")
    return "  •  ".join(parts)


def edit_card_translation(repo: "CardRepository", card_id: int, parent) -> bool:
    """Eigene DE-Uebersetzung erfassen/aendern (Override; ueberlebt
    Karten-Updates). Leere Felder = kein Override fuer dieses Feld. Rueckgabe:
    True, wenn gespeichert wurde. Geteilt von DetailPanel und CardDetailDialog;
    der Aufrufer aktualisiert danach seine Anzeige."""
    card = repo.get_card(card_id)
    if card is None:
        return False
    dlg = QDialog(parent)
    dlg.setWindowTitle("Deutsche Übersetzung bearbeiten")
    dlg.resize(420, 360)
    v = QVBoxLayout(dlg)
    v.addWidget(QLabel(f"Karte: {card['name']}"))
    # Nur eigene Overrides vorbelegen; der aktuelle Wert steht grau als
    # Platzhalter -- sonst wuerde ein unveraenderter API-Text beim Speichern
    # still zum Override und kuenftige API-Korrekturen kaemen nie mehr an.
    own = ydb.get_card_translation(repo.db_path, card_id)
    form = QFormLayout()
    name_edit = QLineEdit((own["name_de"] if own else None) or "")
    name_edit.setPlaceholderText(card["name_de"] or card["name"])
    form.addRow("Name (DE)", name_edit)
    v.addLayout(form)
    v.addWidget(QLabel("Kartentext (DE):"))
    desc_edit = QTextEdit()
    desc_edit.setPlainText((own["desc_de"] if own else None) or "")
    desc_edit.setPlaceholderText(card["desc_de"] or card["description"] or "")
    v.addWidget(desc_edit, stretch=1)
    hint = QLabel(
        "Leere Felder = keine eigene Übersetzung (grau: aktueller Wert). "
        "Eine entfernte eigene Übersetzung fällt bis zum nächsten "
        "Daten-Update auf Englisch zurück."
    )
    hint.setWordWrap(True)
    hint.setObjectName("HintLabel")
    v.addWidget(hint)
    btn_row = QHBoxLayout()
    ok_btn = QPushButton("Speichern")
    ok_btn.clicked.connect(dlg.accept)
    cancel_btn = QPushButton("Abbrechen")
    cancel_btn.clicked.connect(dlg.reject)
    btn_row.addStretch()
    btn_row.addWidget(ok_btn)
    btn_row.addWidget(cancel_btn)
    v.addLayout(btn_row)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    ydb.set_card_translation(
        repo.db_path, card_id,
        name_de=name_edit.text(), desc_de=desc_edit.toPlainText(),
    )
    return True


class DetailPanel(CardImageView, QWidget):
    def __init__(self, repo: CardRepository):
        super().__init__()
        self.repo = repo
        self.current_id: int | None = None
        self._init_card_image()

        layout = QVBoxLayout(self)

        self.image = QLabel("Keine Karte ausgewählt")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(220, 320)
        self.image.setObjectName("CardImage")

        self.name = QLabel("")
        self.name.setObjectName("CardTitle")
        self.name.setWordWrap(True)
        # Eigene Uebersetzung pflegen -- fuer Karten, denen die API keinen
        # deutschen Namen/Text liefert.
        self.edit_trans_btn = QPushButton("✎ DE")
        self.edit_trans_btn.setToolTip("Deutsche Übersetzung bearbeiten")
        self.edit_trans_btn.setFixedWidth(48)
        self.edit_trans_btn.clicked.connect(self._edit_translation)

        self.stats = QLabel("")
        self.stats.setWordWrap(True)

        self.text = QTextEdit()
        self.text.setReadOnly(True)

        # Sammlung
        coll_box = QGroupBox("Sammlung")
        coll_layout = QHBoxLayout(coll_box)
        self.owned = QLabel("im Bestand: 0")
        self.qty = QSpinBox()
        self.qty.setRange(1, 99)
        self.add_btn = QPushButton("Hinzufügen")
        self.add_btn.clicked.connect(self._add_to_collection)
        coll_layout.addWidget(self.owned)
        coll_layout.addStretch()
        coll_layout.addWidget(self.qty)
        coll_layout.addWidget(self.add_btn)

        # Deck
        deck_box = QGroupBox("Deck")
        deck_layout = QHBoxLayout(deck_box)
        self.add_deck_btn = QPushButton("+ Deck")
        self.add_side_btn = QPushButton("+ Side")
        self.add_deck_btn.clicked.connect(lambda: self._add_to_deck(False))
        self.add_side_btn.clicked.connect(lambda: self._add_to_deck(True))
        deck_layout.addWidget(self.add_deck_btn)
        deck_layout.addWidget(self.add_side_btn)

        # Wird von MainWindow gesetzt: callback(card_id, to_side) -> (added, msg)
        self.add_to_deck_callback = None

        # Kombo
        combo_box = QGroupBox("Kombo")
        combo_layout = QHBoxLayout(combo_box)
        self.add_combo_btn = QPushButton("+ als Baustein zur aktiven Kombo")
        self.add_combo_btn.clicked.connect(self._add_to_combo)
        combo_layout.addWidget(self.add_combo_btn)
        # Wird von MainWindow gesetzt: callback(card_id)
        self.add_to_combo_callback = None

        layout.addWidget(self.image)
        name_row = QHBoxLayout()
        name_row.addWidget(self.name, stretch=1)
        name_row.addWidget(
            self.edit_trans_btn, alignment=Qt.AlignmentFlag.AlignTop
        )
        layout.addLayout(name_row)
        layout.addWidget(self.stats)
        layout.addWidget(self.text, stretch=1)
        layout.addWidget(coll_box)
        layout.addWidget(deck_box)
        layout.addWidget(combo_box)
        self._set_enabled(False)

    def _set_enabled(self, on: bool):
        self.qty.setEnabled(on)
        self.add_btn.setEnabled(on)
        self.add_deck_btn.setEnabled(on)
        self.add_side_btn.setEnabled(on)
        self.add_combo_btn.setEnabled(on)
        self.edit_trans_btn.setEnabled(on)

    def _edit_translation(self) -> None:
        if self.current_id is None:
            return
        if edit_card_translation(self.repo, self.current_id, self):
            updated = self.repo.get_card(self.current_id)
            if updated is not None:
                self.show_card(updated)

    def _add_to_combo(self) -> None:
        if self.current_id is None or self.add_to_combo_callback is None:
            return
        self.add_to_combo_callback(self.current_id)

    def _add_to_deck(self, to_side: bool) -> None:
        if self.current_id is None or self.add_to_deck_callback is None:
            return
        result = self.add_to_deck_callback(self.current_id, to_side)
        if result:
            _added, msg = result
            if msg:
                QMessageBox.information(self, "Deck", msg)

    def show_card(self, card) -> None:
        self.current_id = card["id"]
        self.name.setText(card["name_de"] or card["name"])
        self.stats.setText(_format_card_stats(card))
        self.text.setPlainText(card["desc_de"] or card["description"] or "")

        self._load_image(card["id"])
        self._update_owned(card["id"])
        self._set_enabled(True)

    def _update_owned(self, card_id: int) -> None:
        owned = self.repo.owned_count(card_id)
        bound = ydb.card_bound_in_decks(self.repo.db_path, card_id)
        text = f"im Bestand: {owned}"
        if bound:
            text += f"  ·  in Decks: {bound}"
            if bound > owned:
                text += "  ⚠"
        self.owned.setText(text)

    def _add_to_collection(self) -> None:
        if self.current_id is None:
            return
        ydb.add_to_collection(self.repo.db_path, self.current_id, self.qty.value())
        self._update_owned(self.current_id)


class CardDetailDialog(CardImageView, QDialog):
    """Read-only Detail-Pop-up einer Karte (Bild, deutsche Infos, Kartentext)
    -- geoeffnet per Doppelklick im Sammlung-Tab. Bietet zusaetzlich den
    „✎ DE"-Button zum Uebersetzung-Bearbeiten; nach dem Speichern meldet
    'translation_changed', damit der aufrufende Tab seine Liste aktualisiert.
    Eine Instanz wird je View wiederverwendet (load() zeigt die naechste
    Karte). Bild und Cache wie DetailPanel, nur in groesserer Darstellung."""

    translation_changed = Signal()
    IMG_W, IMG_H = 300, 438
    _CACHE_PREFIX = "detail"

    def __init__(self, repo: "CardRepository", parent=None):
        super().__init__(parent)
        self.repo = repo
        self.current_id: int | None = None
        self.resize(360, 660)
        self._init_card_image()

        layout = QVBoxLayout(self)

        self.image = QLabel("")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(self.IMG_W, self.IMG_H)
        self.image.setObjectName("CardImage")

        self.name = QLabel("")
        self.name.setObjectName("CardTitle")
        self.name.setWordWrap(True)
        self.edit_trans_btn = QPushButton("✎ DE")
        self.edit_trans_btn.setToolTip("Deutsche Übersetzung bearbeiten")
        self.edit_trans_btn.setFixedWidth(48)
        self.edit_trans_btn.clicked.connect(self._edit_translation)

        self.stats = QLabel("")
        self.stats.setWordWrap(True)

        self.text = QTextEdit()
        self.text.setReadOnly(True)

        close_btn = QPushButton("Schließen")
        close_btn.clicked.connect(self.close)

        layout.addWidget(self.image)
        name_row = QHBoxLayout()
        name_row.addWidget(self.name, stretch=1)
        name_row.addWidget(
            self.edit_trans_btn, alignment=Qt.AlignmentFlag.AlignTop
        )
        layout.addLayout(name_row)
        layout.addWidget(self.stats)
        layout.addWidget(self.text, stretch=1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def load(self, card_id: int) -> None:
        """Karte anzeigen (auch beim Wiederverwenden der Instanz)."""
        card = self.repo.get_card(card_id)
        if card is None:
            return
        self.current_id = card_id
        title = card["name_de"] or card["name"]
        self.setWindowTitle(title)
        self.name.setText(title)
        self.stats.setText(_format_card_stats(card))
        self.text.setPlainText(card["desc_de"] or card["description"] or "")
        self._load_image(card_id)

    def _edit_translation(self) -> None:
        if self.current_id is None:
            return
        if edit_card_translation(self.repo, self.current_id, self):
            self.load(self.current_id)
            self.translation_changed.emit()
