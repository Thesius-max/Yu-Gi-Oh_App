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
    die Herkunft (ED/GY/...) bis zum Ablegen -- daraus wird 'SS <X> (<Ort>)'."""
    __slots__ = ("card_id", "name", "face_down", "defense", "uid", "origin")
    _uid_counter = itertools.count(1)

    def __init__(self, card_id: int, name: str,
                 face_down: bool = False, defense: bool = False,
                 uid: int | None = None, origin: str | None = None):
        self.card_id = card_id
        self.name = name
        self.face_down = face_down
        self.defense = defense
        self.uid = next(self._uid_counter) if uid is None else uid
        self.origin = origin

    # -- Serialisierung (wertbasierte Snapshots fuer Undo) -------------------

    def to_tuple(self) -> tuple:
        """Packt alle Felder in ein tupel (rekonstruierbar via _CardInst(*t))."""
        return (self.card_id, self.name, self.face_down,
                self.defense, self.uid, self.origin)

    @classmethod
    def from_tuple(cls, t: tuple | None) -> _CardInst | None:
        """Baut ein _CardInst aus einem to_tuple-Ergebnis (None -> None)."""
        return None if t is None else cls(*t)
