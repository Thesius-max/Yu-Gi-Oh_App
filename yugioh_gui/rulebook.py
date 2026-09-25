"""Regelwerk-Tab: wie man Yu-Gi-Oh spielt.

Zeigt die Kapitel aus rulebook_text (Phasen, Kartenarten, Beschwoerungen,
Effekte, Ketten, Kartentext lesen, Glossar, Rulings) in der geteilten
DocView mit Suche. Rein statisch; Spruenge von anderen Stellen laufen ueber
navigation.open_rulebook -> MainWindow -> show_section.
"""

from __future__ import annotations

from .docview import DocView
from .rulebook_text import RULEBOOK_SECTIONS


class RulebookView(DocView):
    def __init__(self) -> None:
        super().__init__(RULEBOOK_SECTIONS,
                         placeholder="Regeln durchsuchen (z. B. Kette, Tribut, Link) …")
