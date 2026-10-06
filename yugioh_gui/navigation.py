"""Spruenge quer durch die App von ueberall (Blatt-Modul).

Kartendetails, Wortlaut-Lesehilfe und Spielfeld-Protokoll verweisen auf
Regelwerk-Kapitel; Regelwerk und Einkaufsliste oeffnen Karten-Details.
Damit diese Stellen keine View importieren muessen (Regel: Views
importieren einander nie), registriert MainWindow hier die Sprungziele.
Gebundene Methoden werden schwach referenziert -- ein geschlossenes Fenster
haelt sich so nicht am Leben und wird nie mehr angesprungen. Ohne
Registrierung sind die Aufrufe No-ops (False).
"""

from __future__ import annotations

import inspect
import weakref
from typing import Any, Callable, Optional

_targets: dict[str, Any] = {}     # Art -> WeakMethod oder Funktion


def _register(kind: str, fn: Optional[Callable]) -> None:
    if fn is None:
        _targets.pop(kind, None)
    else:
        _targets[kind] = weakref.WeakMethod(fn) if inspect.ismethod(fn) else fn


def _call(kind: str, arg) -> bool:
    target = _targets.get(kind)
    fn = target() if isinstance(target, weakref.WeakMethod) else target
    if fn is None:
        return False
    try:
        fn(arg)
    except RuntimeError:        # Qt-Objekt des Fensters bereits geloescht
        return False
    return True


def set_rulebook_opener(fn: Optional[Callable[[str], None]]) -> None:
    """Sprungfunktion ins Regelwerk setzen (MainWindow) bzw. mit None entfernen."""
    _register("rulebook", fn)


def open_rulebook(key: str) -> bool:
    """Regelwerk-Kapitel 'key' zeigen; False, wenn niemand (mehr) da ist."""
    return bool(key) and _call("rulebook", key)


def set_card_opener(fn: Optional[Callable[[int], None]]) -> None:
    """Funktion setzen, die die Details einer Karte zeigt (MainWindow)."""
    _register("card", fn)


def open_card(card_id: int) -> bool:
    """Details der Karte 'card_id' zeigen; False, wenn niemand da ist."""
    return card_id is not None and _call("card", int(card_id))
