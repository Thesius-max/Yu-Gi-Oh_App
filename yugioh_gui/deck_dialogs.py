"""Dialoge des Deck-Tabs: Kombo-aus-Deck, Referenz-Korpus, Listen-Diff.

ComboFromDeckDialog (Bausteine aus Deck-Karten ankreuzen),
ReferenceDeckDialog (Meta-Listen als .ydk in den Korpus importieren;
binden keinen Bestand) und DeckCorpusDiffDialog (kopiengenauer
Vergleich Main+Extra gegen eine Referenz-Liste).
"""

from __future__ import annotations

import datetime
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QTextBrowser, QVBoxLayout
)

import yugioh_db as ydb

from .labels import ZONE_LABELS, import_report_lines
from .repository import CardRepository


class ComboFromDeckDialog(QDialog):
    """Neue Kombo direkt aus den Karten des aktiven Decks: Name vergeben,
    Bausteine ankreuzen (Main + Extra — das Side Deck zählt bei der
    Kombo-Abdeckung ohnehin nicht mit)."""

    def __init__(self, repo: CardRepository, deck_id: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kombo aus Deck anlegen")
        self.resize(420, 520)
        v = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("z.B. 'Karte X → Endboard Y'")
        form.addRow("Name", self.name_edit)
        v.addLayout(form)
        v.addWidget(QLabel("Bausteine ankreuzen:"))
        self.list = QListWidget()
        for zone in ("main", "extra"):
            rows = ydb.deck_cards(repo.db_path, deck_id, zone)
            if not rows:
                continue
            header = QListWidgetItem(f"— {ZONE_LABELS[zone]} —")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            font = header.font(); font.setBold(True); header.setFont(font)
            self.list.addItem(header)
            for r in rows:
                item = QListWidgetItem(r["name"])
                item.setData(Qt.ItemDataRole.UserRole, r["card_id"])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.list.addItem(item)
        v.addWidget(self.list, stretch=1)
        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Anlegen")
        ok_btn.clicked.connect(self._accept)
        cancel_btn = QPushButton("Abbrechen")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        v.addLayout(btn_row)

    def _accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.information(self, "Kombo", "Bitte einen Namen vergeben.")
            return
        self.accept()

    def combo_name(self) -> str:
        return self.name_edit.text().strip()

    def selected_card_ids(self) -> list[int]:
        return [
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.CheckState.Checked
        ]


class _ReferenceMetaDialog(QDialog):
    """Kleines Formular fuer die Korpus-Tags eines Referenz-Decks:
    Name, Quelle (Turnier/URL/Spieler) und Stand der Liste."""

    def __init__(self, default_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Referenz-Deck importieren")
        form = QFormLayout(self)
        self.name_edit = QLineEdit(default_name)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Turnier, URL oder Spieler")
        self.date_edit = QLineEdit(datetime.date.today().isoformat())
        self.date_edit.setPlaceholderText("JJJJ-MM-TT (leer = unbekannt)")
        form.addRow("Name:", self.name_edit)
        form.addRow("Quelle:", self.source_edit)
        form.addRow("Stand:", self.date_edit)
        btns = QHBoxLayout()
        ok_btn = QPushButton("Importieren")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._accept_checked)
        cancel_btn = QPushButton("Abbrechen")
        cancel_btn.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        form.addRow(btns)

    def _accept_checked(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Eingabe fehlt", "Name darf nicht leer sein.")
            return
        date = self.date_edit.text().strip()
        if date:
            try:
                datetime.date.fromisoformat(date)
            except ValueError:
                QMessageBox.warning(
                    self, "Ungültiges Datum",
                    "Stand bitte als JJJJ-MM-TT angeben (oder leer lassen).",
                )
                return
        self.accept()

    def values(self) -> tuple[str, str | None, str | None]:
        return (
            self.name_edit.text().strip(),
            self.source_edit.text().strip() or None,
            self.date_edit.text().strip() or None,
        )


class ReferenceDeckDialog(QDialog):
    """Korpus-Verwaltung: importierte Meta-Decklisten (Referenz-Decks).
    Sie liefern Co-Occurrence-Daten fuer die Kartenvorschlaege, binden
    keinen Bestand und tauchen in keiner Deck-Auswahl auf."""

    def __init__(self, repo: CardRepository, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Referenz-Decks (Korpus)")
        self.resize(560, 420)
        self.repo = repo

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Meta-Decklisten als .ydk importieren (z.B. Turnier-Listen). "
            "Sie dienen nur als Datenbasis für Kartenvorschläge — neuere "
            "Listen zählen später mehr."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.listing = QListWidget()
        layout.addWidget(self.listing, stretch=1)

        btns = QHBoxLayout()
        import_btn = QPushButton("Importieren… (.ydk)")
        import_btn.clicked.connect(self._import)
        del_btn = QPushButton("Löschen")
        del_btn.clicked.connect(self._delete)
        close_btn = QPushButton("Schließen")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(import_btn)
        btns.addWidget(del_btn)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        layout.addLayout(btns)
        self._reload()

    def _reload(self) -> None:
        self.listing.clear()
        for d in ydb.list_reference_decks(self.repo.db_path):
            parts = [d["name"]]
            if d["format_date"]:
                parts.append(d["format_date"])
            if d["source"]:
                parts.append(d["source"])
            item = QListWidgetItem(" — ".join(parts) + f"  ({d['cards']} Karten)")
            item.setData(Qt.ItemDataRole.UserRole, d["deck_id"])
            self.listing.addItem(item)
        if self.listing.count() == 0:
            item = QListWidgetItem(
                "Noch keine Referenz-Decks — .ydk-Listen von Turnier-Seiten "
                "importieren."
            )
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.listing.addItem(item)

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Referenz-Deck importieren", "",
            "YGOPro-Deck (*.ydk);;Alle Dateien (*)",
        )
        if not path:
            return
        try:
            text = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError as exc:
            QMessageBox.warning(self, "Import fehlgeschlagen", str(exc))
            return
        meta = _ReferenceMetaDialog(
            os.path.splitext(os.path.basename(path))[0], self
        )
        if meta.exec() != QDialog.DialogCode.Accepted:
            return
        name, source, format_date = meta.values()
        deck_id, report = ydb.import_deck_ydk(
            self.repo.db_path, name, text,
            kind="reference", source=source, format_date=format_date,
        )
        if deck_id is None:
            QMessageBox.warning(
                self, "Import fehlgeschlagen",
                "Keine der Karten wurde in der Datenbank gefunden. "
                "Eventuell hilft ein Daten-Update (Menü 'Daten').",
            )
            return
        lines = import_report_lines(report)
        if len(lines) > 1:
            QMessageBox.information(
                self, "Referenz-Deck importiert", "\n\n".join(lines)
            )
        self._reload()

    def _delete(self) -> None:
        item = self.listing.currentItem()
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        reply = QMessageBox.question(
            self, "Referenz-Deck löschen",
            f"'{item.text()}' aus dem Korpus löschen?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            ydb.delete_deck(self.repo.db_path, item.data(Qt.ItemDataRole.UserRole))
            self._reload()


def _format_corpus_diff(d: dict) -> str:
    """Korpus-Vergleich als lesbarer Text (gemeinsam von Dialog/Tests nutzbar)."""
    lines = [
        f"Referenz: {d['ref_name']}",
        f"Übereinstimmung: {d['shared_cards']}/{d['ref_cards']} Karten der "
        f"Referenzliste (Main+Extra)",
        f"Deine Karten: {d['my_total']}  ·  Referenz: {d['ref_total']}",
        "",
    ]
    if d["missing"]:
        lines.append(f"Fehlt dir ({len(d['missing'])}):")
        lines += [
            f"  +{m['diff']}  {m['name']}   "
            f"(du {m['mine']} / Referenz {m['theirs']})"
            for m in d["missing"]
        ]
    else:
        lines.append("Fehlt dir: nichts — alle Referenzkarten sind abgedeckt.")
    lines.append("")
    if d["extra"]:
        lines.append(f"Du hast extra ({len(d['extra'])}):")
        lines += [
            f"  +{e['diff']}  {e['name']}   "
            f"(du {e['mine']} / Referenz {e['theirs']})"
            for e in d["extra"]
        ]
    else:
        lines.append("Du hast extra: nichts.")
    return "\n".join(lines)


class DeckCorpusDiffDialog(QDialog):
    """Vergleicht das aktuelle Deck (Main+Extra, kopiengenau) mit einer
    waehlbaren Korpus-/Referenz-Liste: was fehlt dir, was hast du extra.
    Reine Anzeige; aendert nichts am Deck."""

    def __init__(self, repo: "CardRepository", deck_id: int, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.deck_id = deck_id
        self.setWindowTitle("Deck-Vergleich mit Korpus-Liste")
        self.resize(560, 620)

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Referenz-Liste:"))
        self.ref_cb = QComboBox()
        for d in ydb.list_reference_decks(repo.db_path):
            label = d["name"]
            if d["format_date"]:
                label += f" ({d['format_date']})"
            self.ref_cb.addItem(label, d["deck_id"])
        self.ref_cb.currentIndexChanged.connect(self._update)
        row.addWidget(self.ref_cb, stretch=1)
        layout.addLayout(row)

        self.report = QTextBrowser()
        layout.addWidget(self.report, stretch=1)

        btns = QHBoxLayout()
        btns.addStretch()
        close_btn = QPushButton("Schließen")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        self._update()

    def _update(self) -> None:
        ref_id = self.ref_cb.currentData()
        if ref_id is None:
            self.report.setPlainText("")
            return
        try:
            d = ydb.deck_corpus_diff(self.repo.db_path, self.deck_id, ref_id)
        except ValueError as exc:
            self.report.setPlainText(str(exc))
            return
        self.report.setPlainText(_format_corpus_diff(d))
