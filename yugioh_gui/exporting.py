"""Datei-Export-Helfer: Text woertlich als .txt/.md oder als PDF setzen.

Von Sammlungs- und Deck-Tab gemeinsam genutzt; QPdfWriter gehoert zu
PySide6 (keine zusaetzliche Abhaengigkeit).
"""

from __future__ import annotations

import os

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPdfWriter, QTextDocument


# ---------------------------------------------------------------------------
# Datei-Export (von Sammlungs- und Deck-Tab gemeinsam genutzt)
# ---------------------------------------------------------------------------

def write_text_file(path: str, text: str) -> None:
    """Text woertlich als UTF-8 mit Unix-Zeilenenden schreiben (.txt/.md)."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_text_pdf(path: str, text: str) -> None:
    """Text unveraendert als PDF setzen (Monospace, A4). QPdfWriter gehoert
    zu PySide6 -- keine zusaetzliche Abhaengigkeit."""
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(15, 12, 15, 12), QPageLayout.Unit.Millimeter)
    doc = QTextDocument()
    font = QFont("Consolas")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(9)
    doc.setDefaultFont(font)
    doc.setPlainText(text)
    doc.print_(writer)


def resolve_export_path(
    path: str, selected: str, filter_to_ext: dict[str, str], default_ext: str,
) -> tuple[str, str]:
    """Bestimmt die Ziel-Endung aus dem Pfad oder -- falls keine angegeben --
    aus dem gewaehlten Dateifilter und ergaenzt sie. Rueckgabe (pfad, endung).
    'filter_to_ext' bildet ein Erkennungswort des Filterlabels auf die Endung
    ab (z.B. {'PDF': '.pdf', 'Markdown': '.md'})."""
    lower = path.lower()
    for ext in set(filter_to_ext.values()):
        if lower.endswith(ext):
            return path, ext
    ext = default_ext
    for key, e in filter_to_ext.items():
        if key in selected:
            ext = e
            break
    if "." not in os.path.basename(path):
        path += ext
    return path, ext
