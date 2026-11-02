"""Dokument-Ansicht mit Kapitelliste und Suche (Blatt-Modul).

Geteilt von Handbuch- und Regelwerk-Tab: links das Suchfeld und die
Kapitelliste, rechts das gewaehlte Kapitel als gerendertes Markdown. Die
Suche filtert Kapitel nach Titel und Text (casefold, also auch 'Ü'/'ü')
und springt im gezeigten Kapitel zur ersten Fundstelle. Links: 'card:<id>'
oeffnet die Kartendetails (navigation.open_card), 'rulebook:<key>' ein
Kapitel dieser Ansicht, alles andere der Browser. Lesbarkeit: Zeilen
werden auf ~95 Zeichen begrenzt, Zitatbloecke ('> Hinweis') als dezente
Kaesten statt weit eingerueckt dargestellt.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QSplitter, QTextBrowser,
    QVBoxLayout, QWidget
)

from . import navigation
from .theme import _PANEL

_MAX_LINE_CHARS = 95     # laengere Zeilen liest man schlecht


class _ReadableBrowser(QTextBrowser):
    """QTextBrowser mit begrenzter Zeilenbreite: bei breitem Fenster bricht
    der Text nach ~_MAX_LINE_CHARS Zeichen um, statt ueber die volle Breite
    zu laufen; schmaler als das bleibt er an die Fensterbreite gebunden."""

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        limit = self.fontMetrics().averageCharWidth() * _MAX_LINE_CHARS
        width = min(self.viewport().width() - 4, limit)
        if width != self.lineWrapColumnOrWidth():
            self.setLineWrapMode(QTextBrowser.LineWrapMode.FixedPixelWidth)
            self.setLineWrapColumnOrWidth(max(width, 100))

    def set_markdown(self, md: str) -> None:
        """Markdown setzen und Zitatbloecke als Hinweis-Kaesten gestalten
        (Qt rueckt sie sonst links und rechts um 40 px ein)."""
        self.setMarkdown(md)
        doc = self.document()
        block = doc.begin()
        while block.isValid():
            fmt = block.blockFormat()
            if fmt.intProperty(QTextFormat.Property.BlockQuoteLevel):
                fmt.setLeftMargin(8)
                fmt.setRightMargin(0)
                fmt.setBackground(QColor(_PANEL))
                cursor = QTextCursor(block)
                cursor.setBlockFormat(fmt)
            block = block.next()


class DocView(QWidget):
    """Statische Kapitel (Schluessel, Titel, Markdown) mit Suche. Kein
    Repo-Zugriff, daher kein refresh()."""

    def __init__(self, sections: list[tuple[str, str, str]],
                 placeholder: str = "Suchen …") -> None:
        super().__init__()
        self._sections = list(sections)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText(placeholder)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        lv.addWidget(self.search)
        self.index = QListWidget()
        for key, title, _md in self._sections:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.index.addItem(item)
        self.index.currentRowChanged.connect(self._show_row)
        lv.addWidget(self.index, stretch=1)
        self.hits = QLabel("")
        self.hits.setObjectName("HintLabel")
        lv.addWidget(self.hits)
        splitter.addWidget(left)

        self.content = _ReadableBrowser()
        self.content.setOpenLinks(False)
        self.content.anchorClicked.connect(self._on_link)
        splitter.addWidget(self.content)
        # Kapitelliste schmal halten; zusaetzliche Breite geht an den Text.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([210, 790])

        outer = QVBoxLayout(self)
        outer.addWidget(splitter)
        self.index.setCurrentRow(0)

    # -- Anzeige ------------------------------------------------------------

    def _show_row(self, row: int) -> None:
        if not 0 <= row < len(self._sections):
            return
        self.content.set_markdown(self._sections[row][2])
        self.content.verticalScrollBar().setValue(0)
        term = self.search.text().strip()
        if term:
            self.content.find(term)       # erste Fundstelle markieren

    def _on_link(self, url: QUrl) -> None:
        scheme = url.scheme()
        if scheme == "card" and url.path().isdigit():
            navigation.open_card(int(url.path()))
        elif scheme == "rulebook":
            self.show_section(url.path())
        else:
            QDesktopServices.openUrl(url)

    def current_key(self) -> str | None:
        item = self.index.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def show_section(self, key: str) -> bool:
        """Kapitel anzeigen (Suche wird dafuer geleert). False = unbekannt."""
        for row, (k, _title, _md) in enumerate(self._sections):
            if k == key:
                if self.search.text():
                    self.search.clear()
                self.index.setCurrentRow(row)
                self._show_row(row)
                return True
        return False

    # -- Suche --------------------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        term = text.strip().casefold()
        first_visible = -1
        shown = 0
        for row, (_k, title, md) in enumerate(self._sections):
            match = not term or term in title.casefold() or term in md.casefold()
            self.index.item(row).setHidden(not match)
            if match:
                shown += 1
                if first_visible < 0:
                    first_visible = row
        self.hits.setText(f"{shown} Kapitel" if term else "")
        current = self.index.currentRow()
        if term and (current < 0 or self.index.item(current).isHidden()):
            if first_visible >= 0:
                self.index.setCurrentRow(first_visible)
            else:
                self.content.clear()
        elif current >= 0:
            self._show_row(current)

