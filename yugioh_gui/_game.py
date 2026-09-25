"""Spielzustand des Spielfeld-Tabs: reine Datenklassen ohne Qt.

Game haelt beide Seiten (Side: Deck/Extra als card_id-Listen, Hand,
Monster-/Zauber-Fallen-Zonen, Feldzone, Friedhof, Verbannt, LP), die
geteilten Extra-Monsterzonen (Besitz steckt im Exemplar: _CardInst.owner)
sowie Zug, Phase und die zugbezogenen Merker fuer das Regelwerk. Ein
einziges snapshot()/restore()-Paar sichert alles wertbasiert fuer den
Undo-Stack -- neue Felder gehoeren hier hinein, nicht in die View.

Zonen-Schluessel (auch die Widget-Keys der View): 'm0'..'m4', 's0'..'s4',
'field', 'e0'/'e1' fuer die eigene Seite; mit Praefix 'o' ('om0', 'os2',
'ofield') fuer die Gegner-Seite.
"""

from __future__ import annotations

from ._cardinst import _CardInst

START_LP = 8000
# Phasen in Zug-Reihenfolge: (Schluessel, Anzeige).
PHASES = (("DP", "Draw"), ("SP", "Standby"), ("M1", "Main 1"),
          ("BP", "Battle"), ("M2", "Main 2"), ("EP", "End"))
PHASE_KEYS = tuple(k for k, _ in PHASES)
# Zugbezogene Merker (Mengen von Exemplar-uids), geleert bei Zugwechsel.
TURN_FLAGS = ("summoned", "pos_changed", "set", "attacked")


class Side:
    """Eine Spielerseite. Deck/Extra sind card_id-Listen (oberste Karte
    zuerst), alles andere _CardInst-Exemplare."""

    def __init__(self) -> None:
        self.deck: list[int] = []
        self.extra: list[int] = []
        self.hand: list[_CardInst] = []
        self.m: list[_CardInst | None] = [None] * 5
        self.s: list[_CardInst | None] = [None] * 5
        self.field: _CardInst | None = None
        self.gy: list[_CardInst] = []
        self.banished: list[_CardInst] = []
        self.lp = START_LP

    def field_cards(self) -> list[_CardInst]:
        """Alle Karten in Monster-, Zauber/Fallen- und Feldzone."""
        return [c for c in (*self.m, *self.s, self.field) if c is not None]

    def snapshot(self) -> dict:
        si = _snap
        return {
            "deck": list(self.deck), "extra": list(self.extra),
            "hand": [si(c) for c in self.hand],
            "m": [si(c) for c in self.m], "s": [si(c) for c in self.s],
            "field": si(self.field),
            "gy": [si(c) for c in self.gy],
            "ban": [si(c) for c in self.banished],
            "lp": self.lp,
        }

    def restore(self, s: dict) -> None:
        ft = _CardInst.from_tuple
        self.deck = list(s["deck"])
        self.extra = list(s["extra"])
        self.hand = [ft(t) for t in s["hand"]]
        self.m = [ft(t) for t in s["m"]]
        self.s = [ft(t) for t in s["s"]]
        self.field = ft(s["field"])
        self.gy = [ft(t) for t in s["gy"]]
        self.banished = [ft(t) for t in s["ban"]]
        self.lp = s["lp"]


def _snap(c: _CardInst | None):
    return None if c is None else c.to_tuple()


class Game:
    """Kompletter Spielzustand beider Seiten plus Zug/Phase."""

    def __init__(self) -> None:
        self.me = Side()
        self.opp = Side()
        self.emz: list[_CardInst | None] = [None, None]
        self.going_first = True
        self.turn = 1
        self.phase = "M1"
        self.ns_used = 0            # Normalbeschwoerungen/Setzen in diesem Zug
        self.ns_extra = 0           # per Effekt gewaehrte zusaetzliche
        self.flags: dict[str, set[int]] = {f: set() for f in TURN_FLAGS}

    @property
    def my_turn(self) -> bool:
        """Eigener Zug? Wer beginnt, hat die ungeraden Zuege."""
        return (self.turn % 2 == 1) == self.going_first

    def next_turn(self) -> None:
        """Zugwechsel: Merker leeren, Draw Phase des neuen Zugs."""
        self.turn += 1
        self.phase = "DP"
        self.ns_used = self.ns_extra = 0
        for s in self.flags.values():
            s.clear()

    # -- Zonen ---------------------------------------------------------------

    def side(self, owner: str) -> Side:
        return self.opp if owner == "opp" else self.me

    def get(self, key: str) -> _CardInst | None:
        """Karte in einer Feldzone (None = leer)."""
        arr, i = self.slot(key)
        return arr[i]

    def put(self, key: str, inst: _CardInst | None) -> None:
        arr, i = self.slot(key)
        arr[i] = inst

    def slot(self, key: str) -> tuple[list, int]:
        """(Liste, Index) einer Feldzone; die Feldzone wird ueber eine
        Hilfs-Liste auf ihr Attribut umgelenkt (siehe _FieldSlot)."""
        side = self.me
        if key.startswith("o"):
            side, key = self.opp, key[1:]
        if key == "field":
            return _FieldSlot(side), 0
        if key[0] == "e":
            return self.emz, int(key[1:])
        return {"m": side.m, "s": side.s}[key[0]], int(key[1:])

    def zone_of(self, inst: _CardInst) -> str | None:
        """Zonen-Schluessel der Feldzone, in der das Exemplar liegt."""
        for prefix, side in (("", self.me), ("o", self.opp)):
            if side.field is inst:
                return prefix + "field"
            for kind, arr in (("m", side.m), ("s", side.s)):
                for i, c in enumerate(arr):
                    if c is inst:
                        return f"{prefix}{kind}{i}"
        for i, c in enumerate(self.emz):
            if c is inst:
                return f"e{i}"
        return None

    def on_field(self, inst: _CardInst) -> bool:
        return self.zone_of(inst) is not None

    def field_cards(self, owner: str | None = None) -> list[_CardInst]:
        """Feldkarten einer Seite (None = beide), inkl. Extra-Monsterzonen."""
        out: list[_CardInst] = []
        for side_owner in ("me", "opp"):
            if owner in (None, side_owner):
                out += self.side(side_owner).field_cards()
                out += [c for c in self.emz
                        if c is not None and c.owner == side_owner]
        return out

    def monsters(self, owner: str = "me") -> list[_CardInst]:
        """Karten in den Monsterzonen (inkl. EMZ) einer Seite."""
        side = self.side(owner)
        return ([c for c in side.m if c is not None]
                + [c for c in self.emz if c is not None and c.owner == owner])

    def remove(self, inst: _CardInst) -> None:
        """Entfernt ein Exemplar aus Hand/Zonen/GY/Verbannt (per Identitaet)."""
        for side in (self.me, self.opp):
            if inst in side.hand:
                side.hand.remove(inst)
                return
        key = self.zone_of(inst)
        if key is not None:
            self.put(key, None)
            return
        for side in (self.me, self.opp):
            for pile in (side.gy, side.banished):
                if inst in pile:
                    pile.remove(inst)
                    return

    # -- Snapshots -------------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "me": self.me.snapshot(), "opp": self.opp.snapshot(),
            "emz": [_snap(c) for c in self.emz],
            "going_first": self.going_first, "turn": self.turn,
            "phase": self.phase, "ns_used": self.ns_used,
            "ns_extra": self.ns_extra,
            "flags": {k: set(v) for k, v in self.flags.items()},
        }

    def restore(self, s: dict) -> None:
        self.me.restore(s["me"])
        self.opp.restore(s["opp"])
        self.emz = [_CardInst.from_tuple(t) for t in s["emz"]]
        self.going_first = s["going_first"]
        self.turn = s["turn"]
        self.phase = s["phase"]
        self.ns_used = s["ns_used"]
        self.ns_extra = s["ns_extra"]
        self.flags = {k: set(v) for k, v in s["flags"].items()}


class _FieldSlot:
    """Listen-Fassade fuer Side.field, damit slot() einheitlich (Liste,
    Index) liefern kann: fs[0] liest/schreibt side.field."""
    __slots__ = ("_side",)

    def __init__(self, side: Side) -> None:
        self._side = side

    def __getitem__(self, _i: int) -> _CardInst | None:
        return self._side.field

    def __setitem__(self, _i: int, value: _CardInst | None) -> None:
        self._side.field = value
