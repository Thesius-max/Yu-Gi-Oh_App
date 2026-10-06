"""Sammlungs-Tab: Bestand je Karte mit Filtern, Export und Inline-Menge.

Die Datenbank speichert je Druck (Set/Sprache/Edition/Zustand); die Tabelle
zeigt **je Karte eine Zeile** mit Gesamtmenge und der Aufschluesselung nach
Drucken. Die Menge ist direkt editierbar, solange es nur einen Druck gibt;
sonst -- und ueber die Spalte 'Drucke' -- pflegt CollectionPrintsDialog die
einzelnen Drucke (Set/Sprache aendern fuehrt gleiche Drucke zusammen).
Editoren entstehen erst beim Bearbeiten (Delegates statt Widgets je Zeile
-- Performance); Kopfzeilen gruppieren nach Kartenklasse.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget
)

import yugioh_db as ydb

from .carddetail import CardDetailDialog, PrintPicker
from .exporting import (
    resolve_export_path, write_csv_file, write_text_file, write_text_pdf
)
from .images import HoverCardPreview, pos_over_item_text
from .labels import ATTR_DE, CATEGORY_DE, CATEGORY_ORDER, COLLECTION_CARD_ID
from .repository import CardRepository
from .theme import GROUP_HEADER_BG, GROUP_HEADER_FG, UNTRANSLATED_FG


# ---------------------------------------------------------------------------
# Sammlungsverwaltung (eigener Tab)
# ---------------------------------------------------------------------------

class _QuantityDelegate(QStyledItemDelegate):
    """Spinbox-Editor *nach Bedarf* fuer Mengen-Spalten -- statt eines
    persistenten QSpinBox je Zeile, das bei mehreren hundert Eintraegen den
    Tabellen-Neuaufbau spuerbar bremst (~65 ms fuer 700 Zeilen). Der Editor
    entsteht erst beim Bearbeiten einer Zelle."""

    def createEditor(self, parent, option, index):
        sb = QSpinBox(parent)
        sb.setRange(1, 9999)
        sb.setFrame(False)
        return sb

    def setEditorData(self, editor, index):
        try:
            editor.setValue(int(index.data(Qt.ItemDataRole.EditRole)))
        except (TypeError, ValueError):
            editor.setValue(1)

    def setModelData(self, editor, model, index):
        editor.interpretText()
        model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)


_SET_SEP = " · "            # Editor-Eintrag "Code · Set-Name"


class _SetDelegate(QStyledItemDelegate):
    """Editierbare Set-Auswahl: die bekannten Sets der Karte, Codes in der
    Sprache der Zeile (Deutsch, wenn keine gesetzt ist); eigener Text bleibt
    moeglich. Gespeichert wird nur der Code. 'context(row)' liefert
    (card_id, sprache) der Zeile."""

    def __init__(self, parent, db_path: str, context):
        super().__init__(parent)
        self.db_path = db_path
        self.context = context

    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.setEditable(True)
        cb.setFrame(False)
        card_id, lang = self.context(index.row())
        cb.addItem("")
        for s in ydb.card_set_choices(self.db_path, card_id):
            cb.addItem(ydb.localize_set_code(s["code"], lang or "DE") + _SET_SEP + s["name"])
        return cb

    def setEditorData(self, editor, index):
        code = index.data(Qt.ItemDataRole.EditRole) or ""
        for i in range(editor.count()):
            if editor.itemText(i).split(_SET_SEP)[0] == code:
                editor.setCurrentIndex(i)
                return
        editor.setEditText(code)

    def setModelData(self, editor, model, index):
        code = editor.currentText().split(_SET_SEP)[0].strip()
        model.setData(index, code, Qt.ItemDataRole.EditRole)


class _LanguageDelegate(QStyledItemDelegate):
    """Sprache des Drucks: leer, DE oder EN."""

    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.setFrame(False)
        cb.addItem("—", "")
        for key, label in ydb.PRINT_LANGUAGES:
            cb.addItem(f"{key} ({label})", key)
        return cb

    def setEditorData(self, editor, index):
        idx = editor.findData(index.data(Qt.ItemDataRole.EditRole) or "")
        editor.setCurrentIndex(max(idx, 0))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData() or "", Qt.ItemDataRole.EditRole)


class CollectionPrintsDialog(QDialog):
    """Die Drucke einer Karte pflegen: Set, Sprache und Menge je Druck
    (Edition/Zustand/Notiz nur Anzeige), Druck hinzufuegen oder entfernen.
    Schreibt sofort; aendert ein Set-/Sprachwechsel einen Druck in einen
    vorhandenen, werden beide zusammengefuehrt. 'changed' meldet dem
    Aufrufer, dass die Sammlung neu zu laden ist."""

    COLUMNS = ["Set", "Sprache", "Menge", "Edition", "Zustand", "Notiz"]
    COL_SET, COL_LANG, COL_QTY = 0, 1, 2

    def __init__(self, repo: CardRepository, card_id: int, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.card_id = card_id
        self.changed = False
        card = repo.get_card(card_id)
        name = (card["name_de"] or card["name"]) if card else str(card_id)
        self.setWindowTitle(f"Drucke — {name}")
        self.resize(640, 380)
        lay = QVBoxLayout(self)
        self.total = QLabel()
        lay.addWidget(self.total)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.setItemDelegateForColumn(
            self.COL_SET, _SetDelegate(self.table, repo.db_path, self._row_context))
        self.table.setItemDelegateForColumn(self.COL_LANG, _LanguageDelegate(self.table))
        self.table.setItemDelegateForColumn(self.COL_QTY, _QuantityDelegate(self.table))
        self.table.horizontalHeader().setSectionResizeMode(
            self.COL_SET, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.table, stretch=1)

        add_row = QHBoxLayout()
        self.picker = PrintPicker(repo)
        self.picker.load(card_id)
        self.add_qty = QSpinBox()
        self.add_qty.setRange(1, 99)
        add_btn = QPushButton("+ Druck")
        add_btn.clicked.connect(self._add_print)
        add_row.addWidget(self.picker, stretch=1)
        add_row.addWidget(self.add_qty)
        add_row.addWidget(add_btn)
        lay.addLayout(add_row)

        row = QHBoxLayout()
        hint = QLabel("Doppelklick auf Set, Sprache oder Menge zum Ändern.")
        hint.setObjectName("HintLabel")
        row.addWidget(hint, stretch=1)
        self.remove_btn = QPushButton("Druck entfernen")
        self.remove_btn.clicked.connect(self._remove_print)
        row.addWidget(self.remove_btn)
        close = QPushButton("Schließen")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.reload()

    def _row_context(self, row: int):
        return self.card_id, self.table.item(row, self.COL_LANG).text() or None

    def _entry_at(self, row: int):
        item = self.table.item(row, self.COL_SET) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def reload(self, select: int | None = None) -> None:
        prints = [r for r in ydb.list_collection(self.repo.db_path)
                  if r["card_id"] == self.card_id]
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        try:
            for p in prints:
                r = self.table.rowCount()
                self.table.insertRow(r)
                cells = [p["set_code"] or "", p["language"] or "", None,
                         p["edition"] or "", p["condition"] or "", p["notes"] or ""]
                for col, value in enumerate(cells):
                    item = QTableWidgetItem()
                    if col == self.COL_QTY:
                        item.setData(Qt.ItemDataRole.EditRole, int(p["quantity"]))
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    else:
                        item.setText(value)
                    if col > self.COL_QTY:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(r, col, item)
                self.table.item(r, self.COL_SET).setData(
                    Qt.ItemDataRole.UserRole, p["entry_id"])
                if p["entry_id"] == select:
                    self.table.setCurrentCell(r, self.COL_SET)
        finally:
            self.table.blockSignals(False)
        total = sum(p["quantity"] for p in prints)
        self.total.setText(f"{total} Exemplar(e) in {len(prints)} Druck(en)")
        self.remove_btn.setEnabled(bool(prints))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        entry_id = self._entry_at(row)
        if entry_id is None:
            return
        if item.column() == self.COL_QTY:
            try:
                value = int(item.data(Qt.ItemDataRole.EditRole))
            except (TypeError, ValueError):
                return
            if value >= 1:
                ydb.set_collection_quantity(self.repo.db_path, entry_id, value)
                self.changed = True
                QTimer.singleShot(0, lambda: self.reload(entry_id))
            return
        if item.column() not in (self.COL_SET, self.COL_LANG):
            return
        code = self.table.item(row, self.COL_SET).text().strip() or None
        lang = self.table.item(row, self.COL_LANG).text().strip() or None
        if item.column() == self.COL_LANG:
            code = ydb.localize_set_code(code, lang)
        elif code and not lang and ydb.localize_set_code(code, "EN") != code:
            lang = "DE"                       # deutscher Code ohne Sprache
        kept = ydb.update_collection_print(self.repo.db_path, entry_id, code, lang)
        self.changed = True
        # Nicht im itemChanged-Handler die Tabelle umbauen -- erst danach.
        QTimer.singleShot(0, lambda: self.reload(kept))

    def _add_print(self) -> None:
        code, lang = self.picker.values()
        entry = ydb.add_to_collection(self.repo.db_path, self.card_id,
                                      self.add_qty.value(), set_code=code,
                                      language=lang)
        self.changed = True
        self.reload(entry)

    def _remove_print(self) -> None:
        entry_id = self._entry_at(self.table.currentRow())
        if entry_id is None:
            return
        reply = QMessageBox.question(self, "Druck entfernen",
                                     "Diesen Druck aus der Sammlung entfernen?")
        if reply == QMessageBox.StandardButton.Yes:
            ydb.remove_collection_entry(self.repo.db_path, entry_id)
            self.changed = True
            self.reload()


class CollectionView(QWidget):
    COLUMNS = ["Name", "Menge", "Drucke"]
    COL_QTY, COL_PRINTS = 1, 2

    def __init__(self, repo: CardRepository):
        super().__init__()
        self.repo = repo

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.summary = QLabel("")
        refresh_btn = QPushButton("Aktualisieren")
        refresh_btn.clicked.connect(self.refresh)
        export_btn = QPushButton("Exportieren…")
        export_btn.clicked.connect(self._export)
        self.remove_btn = QPushButton("Ausgewählte Karte entfernen")
        self.remove_btn.clicked.connect(self._remove_selected)
        toolbar.addWidget(self.summary)
        toolbar.addStretch()
        toolbar.addWidget(export_btn)
        toolbar.addWidget(refresh_btn)
        toolbar.addWidget(self.remove_btn)

        # Filterleiste: Namenssuche + Dropdowns. Die Dropdowns zeigen nur
        # Werte, die im Bestand tatsaechlich vorkommen.
        filters = QHBoxLayout()
        self.filter_text = QLineEdit()
        self.filter_text.setPlaceholderText("Name filtern …")
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(250)
        self._filter_timer.timeout.connect(self._apply_filters)
        self.filter_text.textChanged.connect(self._filter_timer.start)
        self.filter_cat = QComboBox()
        self.filter_attr = QComboBox()
        self.filter_arch = QComboBox()
        for cb in (self.filter_cat, self.filter_attr, self.filter_arch):
            cb.addItem("(alle)", None)
            cb.currentIndexChanged.connect(self._apply_filters)
        for cat in CATEGORY_ORDER:
            self.filter_cat.addItem(CATEGORY_DE[cat], cat)
        filters.addWidget(self.filter_text, stretch=1)
        filters.addWidget(QLabel("Klasse:"))
        filters.addWidget(self.filter_cat)
        filters.addWidget(QLabel("Attribut:"))
        filters.addWidget(self.filter_attr)
        filters.addWidget(QLabel("Archetyp:"))
        filters.addWidget(self.filter_arch)
        self.filter_untranslated = QCheckBox("Nur unübersetzte")
        self.filter_untranslated.setToolTip(
            "Nur Karten ohne deutsche Übersetzung zeigen (Anzeige fällt dort "
            "auf den englischen Namen zurück)."
        )
        self.filter_untranslated.toggled.connect(self._apply_filters)
        filters.addWidget(self.filter_untranslated)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        # Nur die Menge ist editierbar (per Doppelklick/F2, Editor on demand)
        # -- und nur bei Karten mit genau einem Druck; sonst und ueber die
        # Spalte 'Drucke' oeffnet ein Doppelklick den Drucke-Dialog.
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setItemDelegateForColumn(self.COL_QTY, _QuantityDelegate(self.table))
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_PRINTS, QHeaderView.ResizeMode.Stretch)

        layout.addLayout(toolbar)
        layout.addLayout(filters)
        layout.addWidget(self.table)
        # Schwebende Kartenbild-Vorschau beim Ueberfahren einer Zeile.
        self._hover = HoverCardPreview(self.table, self._hover_card_id)
        # Doppelklick auf eine Karte -> read-only Detail-Pop-up (lazy angelegt,
        # je View wiederverwendet).
        self._detail_dialog: CardDetailDialog | None = None
        self.table.cellDoubleClicked.connect(self._open_detail)
        self.refresh()

    def _hover_card_id(self, pos):
        """card_id, wenn 'pos' (Viewport-Koord.) auf dem Namenstext einer
        Datenzeile liegt; None bei Gruppen-Kopfzeilen, anderen Spalten und
        Leerraum -- speist die Hover-Bildvorschau."""
        row = self.table.rowAt(pos.y())
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None or not pos_over_item_text(self.table, item, pos):
            return None
        return item.data(COLLECTION_CARD_ID)

    def _open_detail(self, row: int, col: int) -> None:
        """Doppelklick: Name -> Karten-Pop-up; Drucke (oder Menge bei
        mehreren Drucken) -> Drucke-Dialog. Bei genau einem Druck oeffnet die
        Mengen-Zelle stattdessen den Spinbox-Editor. Kopfzeilen ignorieren."""
        card_id = self._card_id_at(row)
        if card_id is None:
            return
        if col == self.COL_PRINTS or (col == self.COL_QTY and self._single_entry(row) is None):
            self.open_prints(card_id)
            return
        if col == self.COL_QTY:
            return
        if self._detail_dialog is None:
            self._detail_dialog = CardDetailDialog(self.repo, self)
            self._detail_dialog.translation_changed.connect(self.refresh)
        self._detail_dialog.load(card_id)
        self._detail_dialog.show()
        self._detail_dialog.raise_()
        self._detail_dialog.activateWindow()

    def open_prints(self, card_id: int) -> None:
        """Drucke-Dialog einer Karte; danach Sammlung neu laden."""
        dlg = CollectionPrintsDialog(self.repo, card_id, self)
        dlg.exec()
        changed = dlg.changed
        dlg.deleteLater()
        if changed:
            self._reload_selecting(card_id)

    def _reload_filter_values(self) -> None:
        """Attribut-/Archetyp-Dropdowns aus dem Bestand neu befuellen;
        die aktuelle Auswahl bleibt erhalten."""
        for cb, column, trans in (
            (self.filter_attr, "attribute", ATTR_DE),
            (self.filter_arch, "archetype", {}),
        ):
            keep = cb.currentData()
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("(alle)", None)
            for val in ydb.collection_distinct(self.repo.db_path, column):
                cb.addItem(trans.get(val, val), val)
            idx = cb.findData(keep)
            cb.setCurrentIndex(max(idx, 0))
            cb.blockSignals(False)

    def refresh(self) -> None:
        """Voller Refresh: Filter-Dropdowns neu laden + Daten aktualisieren.
        Wird nach Sammlung-Mutationen (Hinzufuegen/Entfernen) aufgerufen."""
        if not self.repo.exists():
            self.table.clearSpans()
            self.table.setRowCount(0)
            self.summary.setText("Keine Datenbank vorhanden.")
            return
        self._reload_filter_values()
        self._apply_filters()

    def _apply_filters(self) -> None:
        """Nur Daten neu laden und Tabelle befuellen -- ohne Filter-Dropdowns
        neu abzufragen. Wird bei jeder Filter-Aenderung aufgerufen."""
        if not self.repo.exists():
            return
        cards = ydb.list_collection_cards(
            self.repo.db_path,
            text=self.filter_text.text(),
            category=self.filter_cat.currentData(),
            attribute=self.filter_attr.currentData(),
            archetype=self.filter_arch.currentData(),
            untranslated_only=self.filter_untranslated.isChecked(),
        )
        # Auswahl + Scrollposition ueber den Neuaufbau retten (z.B. nach
        # einer DE-Uebersetzung im Detail-Pop-up).
        keep_card = self._card_id_at(self.table.currentRow())
        keep_scroll = self.table.verticalScrollBar().value()
        # Nach Kartenklasse bucketn; Reihenfolge je Gruppe bleibt (Name).
        buckets: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
        for card in cards:
            buckets[ydb.card_category(card["type"])].append(card)
        self.table.setUpdatesEnabled(False)
        # Befuellen ohne itemChanged-Signale -- sonst feuert jede Mengen-Zelle
        # beim Aufbau und schriebe in die DB zurueck.
        self.table.blockSignals(True)
        self.table.clearSpans()
        self.table.setRowCount(0)
        try:
            for cat in CATEGORY_ORDER:
                group = buckets[cat]
                if not group:
                    continue
                self._add_header_row(
                    CATEGORY_DE[cat], sum(card["quantity"] for card in group)
                )
                for card in group:
                    self._add_data_row(card)
        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)
        if keep_card is not None:
            self._select_card(keep_card)
        self.table.verticalScrollBar().setValue(keep_scroll)
        self._update_summary()

    def _card_id_at(self, row: int):
        """card_id der Tabellenzeile oder None (Kopfzeile/keine Zeile)."""
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _single_entry(self, row: int):
        """entry_id, wenn die Karte genau einen Druck hat (Menge direkt
        editierbar), sonst None."""
        item = self.table.item(row, self.COL_QTY) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _select_card(self, card_id: int) -> None:
        for r in range(self.table.rowCount()):
            if self._card_id_at(r) == card_id:
                self.table.setCurrentCell(r, 0)
                break

    def _reload_selecting(self, card_id: int) -> None:
        self._apply_filters()
        self._select_card(card_id)

    def _refresh_group_headers(self) -> None:
        """Kopfzeilen-Zaehler aus den Mengen-Zellen neu summieren (nach
        einer Mengenaenderung, ohne die Tabelle neu aufzubauen)."""
        header, count = None, 0

        def flush():
            if header is not None:
                label = header.text().rsplit("  (", 1)[0]
                header.setText(f"{label}  ({count})")

        self.table.blockSignals(True)
        try:
            for r in range(self.table.rowCount()):
                if self._card_id_at(r) is None:
                    flush()
                    header, count = self.table.item(r, 0), 0
                else:
                    qty = self.table.item(r, self.COL_QTY)
                    count += int(qty.data(Qt.ItemDataRole.EditRole) or 0)
            flush()
        finally:
            self.table.blockSignals(False)

    def _add_header_row(self, label: str, count: int) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        item = QTableWidgetItem(f"{label}  ({count})")
        # Kopfzeile: nur aktiviert (nicht auswählbar/editierbar), keine card_id.
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        font = item.font(); font.setBold(True); item.setFont(font)
        item.setBackground(GROUP_HEADER_BG)
        item.setForeground(GROUP_HEADER_FG)
        self.table.setItem(r, 0, item)
        self.table.setSpan(r, 0, 1, len(self.COLUMNS))

    def _add_data_row(self, card: dict) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        name_item = QTableWidgetItem(card["name_de"] or card["name"])
        # card_id an der Namenszelle -- identifiziert die Zeile (eine Karte =
        # eine Zeile) und speist die Hover-Bildvorschau.
        name_item.setData(Qt.ItemDataRole.UserRole, card["card_id"])
        name_item.setData(COLLECTION_CARD_ID, card["card_id"])
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        # Karten ohne deutsche Uebersetzung sichtbar markieren -- der Name oben
        # ist dann der englische Fallback.
        if not card["name_de"]:
            name_item.setForeground(UNTRANSLATED_FG)
            name_item.setToolTip(
                "Noch keine deutsche Übersetzung — angezeigt wird der "
                "englische Name als Fallback. Übersetzung über den "
                "DE-Button im Detailbereich des Suche-Tabs ergänzbar."
            )
        self.table.setItem(r, 0, name_item)

        # Gesamtmenge; bei genau einem Druck direkt editierbar (Spinbox-
        # Delegate, entry_id in UserRole), sonst schreibgeschuetzt.
        prints = card["prints"]
        qty_item = QTableWidgetItem()
        qty_item.setData(Qt.ItemDataRole.EditRole, int(card["quantity"]))
        qty_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if len(prints) == 1:
            qty_item.setData(Qt.ItemDataRole.UserRole, prints[0]["entry_id"])
        else:
            qty_item.setFlags(qty_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            qty_item.setToolTip("Mehrere Drucke — Doppelklick öffnet die Drucke")
        self.table.setItem(r, self.COL_QTY, qty_item)

        prints_item = QTableWidgetItem(ydb.prints_summary(prints))
        prints_item.setFlags(prints_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        prints_item.setToolTip("Doppelklick: Drucke (Set, Sprache, Menge) bearbeiten")
        self.table.setItem(r, self.COL_PRINTS, prints_item)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Mengenaenderung einer Karte mit genau einem Druck in die DB
        schreiben. Waehrend des Tabellenaufbaus sind Signale geblockt."""
        if item.column() != self.COL_QTY:
            return
        entry_id = self._single_entry(item.row())
        card_id = self._card_id_at(item.row())
        if entry_id is None or card_id is None:
            return
        try:
            value = int(item.data(Qt.ItemDataRole.EditRole))
        except (TypeError, ValueError):
            return
        if value < 1:
            return
        ydb.set_collection_quantity(self.repo.db_path, entry_id, value)
        # Aufschluesselung ('2× RA01-DE008') nennt die Menge mit.
        entry = [r for r in ydb.list_collection(self.repo.db_path)
                 if r["entry_id"] == entry_id]
        self.table.blockSignals(True)
        self.table.item(item.row(), self.COL_PRINTS).setText(
            ydb.prints_summary(entry))
        self.table.blockSignals(False)
        self._refresh_group_headers()
        self._update_summary()

    def _remove_selected(self) -> None:
        row = self.table.currentRow()
        card_id = self._card_id_at(row)
        if card_id is None:
            return  # Gruppen-Kopfzeile, keine Karte
        name = self.table.item(row, 0).text()
        qty = self.table.item(row, self.COL_QTY).data(Qt.ItemDataRole.EditRole)
        reply = QMessageBox.question(
            self, "Karte entfernen",
            f"'{name}' mit allen Drucken ({qty} Exemplar(e)) aus der Sammlung "
            "entfernen?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            ydb.remove_collection_card(self.repo.db_path, card_id)
            self.refresh()

    def _filters_active(self) -> bool:
        return bool(
            self.filter_text.text().strip()
            or self.filter_cat.currentData()
            or self.filter_attr.currentData()
            or self.filter_arch.currentData()
            or self.filter_untranslated.isChecked()
        )

    def _export(self) -> None:
        """Sammlung exportieren -- als .txt/.pdf, Markdown (.md) oder CSV fuer
        Excel (.csv), immer ohne Effekttexte (die gibt es nur im Deck-Export).
        Beruecksichtigt die aktiven Filter; ohne Filter ist es die gesamte
        Sammlung."""
        if not self.repo.exists():
            QMessageBox.warning(
                self, "Export nicht möglich", "Keine Datenbank vorhanden."
            )
            return
        suggested = "sammlung-gefiltert" if self._filters_active() else "sammlung"
        path, selected = QFileDialog.getSaveFileName(
            self, "Sammlung exportieren", suggested + ".txt",
            "Textdatei (*.txt);;PDF-Datei (*.pdf);;Markdown (*.md);;"
            "CSV für Excel (*.csv)",
        )
        if not path:
            return
        path, ext = resolve_export_path(
            path, selected,
            {"Text": ".txt", "PDF": ".pdf", "Markdown": ".md", "CSV": ".csv"},
            default_ext=".txt",
        )
        kwargs = dict(
            text=self.filter_text.text(),
            category=self.filter_cat.currentData(),
            attribute=self.filter_attr.currentData(),
            archetype=self.filter_arch.currentData(),
            untranslated_only=self.filter_untranslated.isChecked(),
        )
        try:
            if ext == ".md":
                write_text_file(
                    path, ydb.export_collection_markdown(self.repo.db_path, **kwargs)
                )
            elif ext == ".csv":
                write_csv_file(
                    path, ydb.export_collection_csv(self.repo.db_path, **kwargs)
                )
            else:
                text = ydb.export_collection_text(self.repo.db_path, **kwargs)
                if ext == ".pdf":
                    write_text_pdf(path, text)
                else:
                    write_text_file(path, text)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Export fehlgeschlagen", str(exc))
            return
        self.summary.setText(f"Sammlung exportiert nach {path}")

    def _update_summary(self) -> None:
        entries, unique, total, untranslated = ydb.collection_summary_stats(
            self.repo.db_path
        )
        text = (
            f"{unique} verschiedene Karten  ·  {total} Karten gesamt  ·  "
            f"{entries} Drucke"
        )
        if untranslated:
            text += f"  ·  {untranslated} ohne deutsche Übersetzung"
        if self._filters_active():
            # Aus der Tabelle gezaehlt -- bleibt nach Mengenaenderungen aktuell.
            cards_shown, shown = 0, 0
            for r in range(self.table.rowCount()):
                if self._card_id_at(r) is not None:
                    cards_shown += 1
                    qty = self.table.item(r, self.COL_QTY)
                    shown += int(qty.data(Qt.ItemDataRole.EditRole) or 0)
            text += f"  ·  Filter: {cards_shown} Karten ({shown} Exemplare)"
        self.summary.setText(text)
