"""Kleine, von mehreren Tabs geteilte Widgets (Blatt-Modul, keine Views).

EmptyHintList: QListWidget, das im leeren Zustand einen grauen Hinweis in
die Flaeche schreibt ("Noch keine Kombos -- ..."), statt nur einen leeren
Kasten zu zeigen. Der Hinweis ist kein Eintrag: count(), Auswahl und Tests
bleiben unberuehrt.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QListWidget

from .theme import _TEXT_DIM


class EmptyHintList(QListWidget):
    def __init__(self, hint: str = "", parent=None):
        super().__init__(parent)
        self._hint = hint

    def hint(self) -> str:
        return self._hint

    def set_hint(self, text: str) -> None:
        if text != self._hint:
            self._hint = text
            self.viewport().update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.count() or not self._hint:
            return
        painter = QPainter(self.viewport())
        painter.setPen(QColor(_TEXT_DIM))
        rect = self.viewport().rect().adjusted(12, 12, -12, -12)
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            self._hint,
        )
        painter.end()
