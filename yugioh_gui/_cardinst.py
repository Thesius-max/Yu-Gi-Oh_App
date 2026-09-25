"""Karten-Exemplar auf dem Spielfeld: reine Datenklasse ohne Qt.

_CardInst repraesentiert eine einzelne Karte auf dem Brett oder in der Hand.
Jede Kopie ist ein eigenes Exemplar mit eigener 'uid' (stabil ueber Undo-
Snapshots hinweg). 'origin' merkt sich beim Aufnehmen aus einem Stapel die
Herkunft (ED/GY/...) bis zum Ablegen -- daraus wird 'SS <X> (<Ort>)'.

Dieses Modul hat KEINE Qt-Abhaengigkeit und ist daher unabhängig vom GUI
testbar. Die Gui-Widgets (_BoardCard, PlayTestView) importieren diese Klasse.
"""

from __future__ import annotations

import itertools


class _CardInst:
    """Eine Karte auf dem Brett/Hand: Identitaet + Zustand (offen/verdeckt,
    ATK/DEF). Jede Kopie ist ein eigenes Exemplar; 'uid' identifiziert es
    stabil ueber Undo-Snapshots hinweg (der Kombo-Recorder zaehlt darueber
    benutzte Exemplare). 'origin' merkt sich beim Aufnehmen aus einem Stapel
    die Herkunft (ED/GY/...) bis zum Ablegen -- daraus wird 'SS <X> (<Ort>)'.

    Spielfeld-Zustand: 'owner' ('me'/'opp' -- Gegner-Karten gehoeren nie in
    die eigenen Stapel), 'token' (Spielmarke: verschwindet beim Verlassen
    des Felds, card_id < 0), 'materials' (Xyz-Material: Exemplare UNTER
    dieser Karte), 'counters' (Zaehlmarken) und manuelle Werte-Aenderungen
    durch Effekte ('atk_mod'/'def_mod'/'level_mod')."""
    __slots__ = ("card_id", "name", "face_down", "defense", "uid", "origin",
                 "owner", "token", "materials", "counters",
                 "atk_mod", "def_mod", "level_mod")
    _uid_counter = itertools.count(1)

    def __init__(self, card_id: int, name: str,
                 face_down: bool = False, defense: bool = False,
                 uid: int | None = None, origin: str | None = None,
                 owner: str = "me", token: bool = False,
                 materials: list[_CardInst] | None = None, counters: int = 0,
                 atk_mod: int = 0, def_mod: int = 0, level_mod: int = 0):
        self.card_id = card_id
        self.name = name
        self.face_down = face_down
        self.defense = defense
        self.uid = next(self._uid_counter) if uid is None else uid
        self.origin = origin
        self.owner = owner
        self.token = token
        self.materials = list(materials) if materials else []
        self.counters = counters
        self.atk_mod = atk_mod
        self.def_mod = def_mod
        self.level_mod = level_mod

    def reset_state(self) -> None:
        """Zustand, der nur auf dem Feld gilt, zuruecksetzen (Karte verlaesst
        das Feld): offen, ATK-Position, keine Marken/Werte-Aenderungen.
        Xyz-Material raeumt der Aufrufer ab (es muss irgendwohin)."""
        self.face_down = self.defense = False
        self.counters = self.atk_mod = self.def_mod = self.level_mod = 0

    # -- Serialisierung (wertbasierte Snapshots fuer Undo) -------------------

    def to_tuple(self) -> tuple:
        """Packt alle Felder in ein Tupel (rekonstruierbar via from_tuple);
        Xyz-Material wird rekursiv mitserialisiert."""
        return (self.card_id, self.name, self.face_down, self.defense,
                self.uid, self.origin, self.owner, self.token,
                tuple(m.to_tuple() for m in self.materials), self.counters,
                self.atk_mod, self.def_mod, self.level_mod)

    @classmethod
    def from_tuple(cls, t: tuple | None) -> _CardInst | None:
        """Baut ein _CardInst aus einem to_tuple-Ergebnis (None -> None)."""
        if t is None:
            return None
        inst = cls(*t)
        inst.materials = [cls.from_tuple(m) for m in t[8]]
        return inst
