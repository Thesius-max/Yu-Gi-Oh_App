"""Sprung ins Regelwerk von ueberall (Blatt-Modul).

Kartendetails, Wortlaut-Lesehilfe und Spielfeld-Protokoll verweisen auf
Regelwerk-Kapitel. Damit diese Stellen keine View importieren muessen
(Regel: Views importieren einander nie), registriert MainWindow hier die
Sprungfunktion. Gebundene Methoden werden schwach referenziert -- ein
geschlossenes Fenster haelt sich so nicht am Leben und wird nie mehr
angesprungen. Ohne Registrierung ist open_rulebook ein No-op (False).
"""

from __future__ import annotations

import inspect
import weakref
from typing import Callable, Optional

_opener = None      # WeakMethod, Funktion oder None


def set_rulebook_opener(fn: Optional[Callable[[str], None]]) -> None:
    """Sprungfunktion setzen (MainWindow) bzw. mit None entfernen."""
    global _opener
    _opener = weakref.WeakMethod(fn) if inspect.ismethod(fn) else fn


def open_rulebook(key: str) -> bool:
    """Regelwerk-Kapitel 'key' zeigen; False, wenn niemand (mehr) da ist."""
    fn = _opener() if isinstance(_opener, weakref.WeakMethod) else _opener
    if fn is None or not key:
        return False
    try:
        fn(key)
    except RuntimeError:        # Qt-Objekt des Fensters bereits geloescht
        return False
    return True
