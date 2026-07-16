"""Sammlungs-Tab: Bestandstabelle mit Filtern, Export und Inline-Menge.

Die Mengenspalte nutzt einen Spinbox-Delegate statt eines Widgets je
Zeile (Performance); Kopfzeilen gruppieren nach Kartenklasse.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget
)

import yugioh_db as ydb

from .carddetail import CardDetailDialog
from .exporting import resolve_export_path, write_text_file, write_text_pdf
from .images import HoverCardPreview
from .labels import ATTR_DE, CATEGORY_DE, CATEGORY_ORDER, COLLECTION_CARD_ID
from .repository import CardRepository
from .theme import GROUP_HEADER_BG, GROUP_HEADER_FG, UNTRANSLATED_FG


# ---------------------------------------------------------------------------
# Sammlungsverwaltung (eigener Tab)
# ---------------------------------------------------------------------------

class _QuantityDelegate(QStyledItemDelegate):
    """Spinbox-Editor *nach Bedarf* fuer die Mengen-Spalte der Sammlung --
    statt eines persistenten QSpinBox je Zeile, das bei mehreren hundert
    Eintraegen den Tabellen-Neuaufbau spuerbar bremst (~65 ms fuer 700 Zeilen).
    Der Editor entsteht erst beim Bearbeiten einer Zelle."""

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


class CollectionView(QWidget):
    COLUMNS = ["Name", "Menge", "Set", "Edition", "Zustand", "Sprache", "Notiz"]

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
        self.remove_btn = QPushButton("Ausgewählten Eintrag entfernen")
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
        # Nur die Mengen-Spalte ist editierbar (per Doppelklick/F2, Spinbox-
        # Editor on demand); alle anderen Zellen bleiben schreibgeschuetzt.
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setItemDelegateForColumn(1, _QuantityDelegate(self.table))
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

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
        """card_id der Tabellenzeile unter 'pos' (Viewport-Koord.) oder None
        bei Gruppen-Kopfzeilen/Leerraum -- speist die Hover-Bildvorschau."""
        row = self.table.rowAt(pos.y())
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(COLLECTION_CARD_ID) if item else None

    def _open_detail(self, row: int, col: int) -> None:
        """Detail-Pop-up der Karte in 'row' oeffnen; Gruppen-Kopfzeilen
        (ohne card_id) ignorieren. Doppelklick auf die Mengen-Spalte oeffnet
        den Spinbox-Editor statt des Pop-ups."""
        if col == 1:
            return
        item = self.table.item(row, 0)
        card_id = item.data(COLLECTION_CARD_ID) if item else None
        if card_id is None:
            return
        if self._detail_dialog is None:
            self._detail_dialog = CardDetailDialog(self.repo, self)
            self._detail_dialog.translation_changed.connect(self.refresh)
        self._detail_dialog.load(card_id)
        self._detail_dialog.show()
        self._detail_dialog.raise_()
        self._detail_dialog.activateWindow()

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
        rows = ydb.list_collection(
            self.repo.db_path,
            text=self.filter_text.text(),
            category=self.filter_cat.currentData(),
            attribute=self.filter_attr.currentData(),
            archetype=self.filter_arch.currentData(),
            untranslated_only=self.filter_untranslated.isChecked(),
        )
        # Nach Kartenklasse bucketn; Reihenfolge je Gruppe bleibt (Name).
        buckets: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
        for row in rows:
            buckets[ydb.card_category(row["type"])].append(row)
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
                    CATEGORY_DE[cat], sum(row["quantity"] for row in group)
                )
                for row in group:
                    self._add_data_row(row)
        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)
        self._update_summary(rows)

    def _add_header_row(self, label: str, count: int) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        item = QTableWidgetItem(f"{label}  ({count})")
        # Kopfzeile: nur aktiviert (nicht auswählbar/editierbar), kein entry_id.
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        font = item.font(); font.setBold(True); item.setFont(font)
        item.setBackground(GROUP_HEADER_BG)
        item.setForeground(GROUP_HEADER_FG)
        self.table.setItem(r, 0, item)
        self.table.setSpan(r, 0, 1, len(self.COLUMNS))

    def _add_data_row(self, row) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        name_item = QTableWidgetItem(row["name_de"] or row["name"])
        # entry_id an der Namenszelle ablegen -- identifiziert die Zeile.
        name_item.setData(Qt.ItemDataRole.UserRole, row["entry_id"])
        # card_id zusaetzlich fuer die Hover-Bildvorschau.
        name_item.setData(COLLECTION_CARD_ID, row["card_id"])
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        # Karten ohne deutsche Uebersetzung sichtbar markieren -- der Name oben
        # ist dann der englische Fallback.
        if not row["name_de"]:
            name_item.setForeground(UNTRANSLATED_FG)
            name_item.setToolTip(
                "Noch keine deutsche Übersetzung — angezeigt wird der "
                "englische Name als Fallback. Übersetzung über den "
                "DE-Button im Detailbereich des Suche-Tabs ergänzbar."
            )
        self.table.setItem(r, 0, name_item)

        # Menge als editierbares Zahl-Item (EditRole == DisplayRole bei
        # QTableWidgetItem); der _QuantityDelegate oeffnet beim Bearbeiten ein
        # Spinbox. Aenderungen laufen ueber itemChanged -> _on_item_changed.
        qty_item = QTableWidgetItem()
        qty_item.setData(Qt.ItemDataRole.EditRole, int(row["quantity"]))
        qty_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(r, 1, qty_item)

        for col, key in enumerate(
            ("set_code", "edition", "condition", "language", "notes"), start=2
        ):
            cell = QTableWidgetItem(row[key] or "")
            cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(r, col, cell)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Mengenaenderung (Spalte 1) in die DB schreiben. Andere Spalten sind
        schreibgeschuetzt; waehrend des Tabellenaufbaus sind Signale geblockt."""
        if item.column() != 1:
            return
        name_item = self.table.item(item.row(), 0)
        entry_id = name_item.data(Qt.ItemDataRole.UserRole) if name_item else None
        if entry_id is None:
            return
        try:
            value = int(item.data(Qt.ItemDataRole.EditRole))
        except (TypeError, ValueError):
            return
        if value < 1:
            return
        ydb.set_collection_quantity(self.repo.db_path, entry_id, value)
        self._update_summary()

    def _remove_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        entry_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if entry_id is None:
            return  # Gruppen-Kopfzeile, kein echter Eintrag
        reply = QMessageBox.question(
            self, "Eintrag entfernen",
            f"'{item.text()}' aus der Sammlung entfernen?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            ydb.remove_collection_entry(self.repo.db_path, entry_id)
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
        """Sammlung exportieren -- lesbar (.txt/.pdf) oder als KI-tauglicher
        Markdown-Block (.md). Beruecksichtigt die aktiven Filter; ohne Filter
        ist es die gesamte Sammlung."""
        if not self.repo.exists():
            QMessageBox.warning(
                self, "Export nicht möglich", "Keine Datenbank vorhanden."
            )
            return
        suggested = "sammlung-gefiltert" if self._filters_active() else "sammlung"
        path, selected = QFileDialog.getSaveFileName(
            self, "Sammlung exportieren", suggested + ".txt",
            "Textdatei (*.txt);;PDF-Datei (*.pdf);;Markdown für KI (*.md)",
        )
        if not path:
            return
        path, ext = resolve_export_path(
            path, selected,
            {"Text": ".txt", "PDF": ".pdf", "Markdown": ".md"}, default_ext=".txt",
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

    def _update_summary(self, filtered_rows=None) -> None:
        entries, unique, total, untranslated = ydb.collection_summary_stats(
            self.repo.db_path
        )
        text = (
            f"{entries} Einträge  ·  {unique} verschiedene Karten  ·  "
            f"{total} Karten gesamt"
        )
        if untranslated:
            text += f"  ·  {untranslated} ohne deutsche Übersetzung"
        if filtered_rows is not None and self._filters_active():
            shown = sum(r["quantity"] for r in filtered_rows)
            text += f"  ·  Filter: {len(filtered_rows)} Einträge ({shown} Karten)"
        self.summary.setText(text)
