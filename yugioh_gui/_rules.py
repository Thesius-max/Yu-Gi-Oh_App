"""Regelwerk des Spielfelds: allgemeine Spielmechanik, reine Funktionen ohne Qt.

Stufe "Mechanik": Zug/Phasen, Normal-/Tribut-/Ritual-/Extra-Deck-
Beschwoerungen mit Materialpruefung, Zonenregeln (Master Rule 2020:
Extra-Monsterzone, Link-Pfeile), Positionswechsel, Aktivieren gesetzter
Karten, Handlimit und Kampf. **Karteneffekte werden nicht ausgewertet** --
ob ein Effekt eine Ausnahme erlaubt, weiss nur der Benutzer (Leitprinzip:
nichts aus Kartentext herleiten). Darum liefern die Pruefungen nur
Befunde; die View entscheidet je Regel-Modus (aus / warnen / erzwingen mit
Rueckfrage 'per Effekt erlaubt?').

Karten-Werte kommen als play_info-dicts (yugioh_db.card_play_info bzw.
deck_play_lists['info']); 'info_of' ist eine Funktion card_id -> info.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._cardinst import _CardInst
from ._game import PHASE_KEYS, Game

MODES = (("off", "Regeln aus"), ("warn", "Regeln: warnen"),
         ("enforce", "Regeln: erzwingen"))
HAND_LIMIT = 6
MAIN_PHASES = ("M1", "M2")


@dataclass
class Verdict:
    """Ergebnis einer Pruefung: 'violations' sind Regelverstoesse (je nach
    Modus Warnung oder Rueckfrage), 'notes' reine Hinweise, die nie
    blockieren (z. B. unbekannte Link-Pfeile, ungepruefte Fusions-Texte)."""
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:            # True = regelkonform
        return not self.violations


# ---------------------------------------------------------------------------
# Kartenklassifikation
# ---------------------------------------------------------------------------

_EXTRA_KINDS = ("link", "xyz", "synchro", "fusion")


def kind(info: dict) -> str:
    """'link' | 'xyz' | 'synchro' | 'fusion' | 'ritual' | 'token' |
    'monster' | 'spell' | 'trap' | 'other'."""
    ft = (info.get("frame_type") or "").lower()
    t = info.get("type") or ""
    for k in _EXTRA_KINDS + ("ritual", "token"):
        if ft.startswith(k):
            return k
    if "Spell" in t:
        return "spell"
    if "Trap" in t:
        return "trap"
    if t == "Token":
        return "token"
    if "Monster" in t:
        return "monster"
    return "other"


def is_monster(info: dict) -> bool:
    return kind(info) not in ("spell", "trap", "other")


def is_extra(info: dict) -> bool:
    return kind(info) in _EXTRA_KINDS


def is_tuner(info: dict) -> bool:
    return "Tuner" in (info.get("type") or "")


def is_pendulum(info: dict) -> bool:
    return "pendulum" in (info.get("frame_type") or "").lower()


def spell_trap_kind(info: dict) -> str:
    """Unterart eines Zaubers/einer Falle ('Normal', 'Quick-Play', 'Field',
    'Continuous', 'Equip', 'Ritual', 'Counter'); die API legt sie in race ab."""
    return info.get("race") or "Normal"


def level_of(inst: _CardInst, info: dict) -> int | None:
    """Aktuelle Stufe (inkl. Effekt-Aenderung); Link- und Xyz-Monster haben
    keine Stufe (Xyz: Rang, siehe rank_of)."""
    if kind(info) in ("link", "xyz") or not info.get("level"):
        return None
    return max(1, info["level"] + inst.level_mod)


def rank_of(info: dict) -> int | None:
    return info.get("level") if kind(info) == "xyz" else None


def atk_of(inst: _CardInst, info: dict) -> int:
    return max(0, (info.get("atk") or 0) + inst.atk_mod)


def def_of(inst: _CardInst, info: dict) -> int | None:
    if kind(info) == "link":
        return None
    return max(0, (info.get("def") or 0) + inst.def_mod)


def tributes_needed(info: dict) -> int:
    """Tribute fuer eine Normalbeschwoerung/ein Setzen: Stufe 5-6 -> 1,
    ab Stufe 7 -> 2."""
    lvl = info.get("level") or 0
    return 2 if lvl >= 7 else 1 if lvl >= 5 else 0


def summon_methods(info: dict) -> list[str]:
    """Regulaere Beschwoerungsarten einer Karte ('link', 'xyz', 'synchro',
    'fusion', 'ritual'); leer fuer Karten ohne Beschwoerungsformel."""
    k = kind(info)
    return [k] if k in _EXTRA_KINDS + ("ritual",) else []


# ---------------------------------------------------------------------------
# Zonen: Extra-Monsterzonen und Link-Pfeile
# ---------------------------------------------------------------------------

# Brett-Geometrie der View: e0 liegt ueber m1, e1 ueber m3. Pfeile, die auf
# Zauber-/Fallenzonen oder die Gegnerseite zeigen, spielen fuer
# Beschwoerungen keine Rolle und fehlen hier.
LINK_TARGETS: dict[tuple[str, str], str] = {}
for _i in range(5):
    if _i > 0:
        LINK_TARGETS[(f"m{_i}", "Left")] = f"m{_i - 1}"
    if _i < 4:
        LINK_TARGETS[(f"m{_i}", "Right")] = f"m{_i + 1}"
for _e, _col in (("e0", 1), ("e1", 3)):
    LINK_TARGETS[(f"m{_col}", "Top")] = _e
    LINK_TARGETS[(f"m{_col - 1}", "Top-Right")] = _e
    LINK_TARGETS[(f"m{_col + 1}", "Top-Left")] = _e
    LINK_TARGETS[(_e, "Bottom-Left")] = f"m{_col - 1}"
    LINK_TARGETS[(_e, "Bottom")] = f"m{_col}"
    LINK_TARGETS[(_e, "Bottom-Right")] = f"m{_col + 1}"
del _i, _e, _col


def pointed_zones(zone: str, markers) -> set[str]:
    """Zonen, auf die ein Link-Monster in 'zone' mit diesen Pfeilen zeigt."""
    return {LINK_TARGETS[(zone, m)] for m in markers or ()
            if (zone, m) in LINK_TARGETS}


def linked_zones(game: Game, info_of) -> tuple[set[str], list[str]]:
    """(eigene Zonen, auf die ein offenes eigenes Link-Monster zeigt;
    Namen der Link-Monster mit unbekannten Pfeilen)."""
    zones: set[str] = set()
    unknown: list[str] = []
    for inst in game.monsters("me"):
        info = info_of(inst.card_id)
        if kind(info) != "link" or inst.face_down:
            continue
        if info.get("link_markers") is None:
            unknown.append(inst.name)
            continue
        zones |= pointed_zones(game.zone_of(inst), info["link_markers"])
    return zones, unknown


def _check_monster_zone(game: Game, inst: _CardInst, info: dict, zone: str,
                        from_extra: bool, info_of, v: Verdict) -> None:
    """Zonenregeln fuer Monster (Master Rule 2020)."""
    if zone.startswith("e"):
        if not from_extra:
            v.violations.append(
                "Die Extra-Monsterzone ist Monstern vorbehalten, die aus dem "
                "Extra Deck beschworen werden")
            return
        other = game.emz[1 - int(zone[1:])]
        if other is not None and other.owner == "me" and other is not inst:
            v.violations.append("Du darfst nur eine Extra-Monsterzone benutzen")
        return
    # Master Rule 2020 beschraenkt nur Link-Monster und Pendelmonster aus dem
    # *offenen* Extra Deck; das (verdeckte) ED hier enthaelt keine offenen
    # Pendel -- Synchro-/Xyz-/Fusions-Pendel duerfen also frei in jede Zone.
    if from_extra and kind(info) == "link":
        zones, unknown = linked_zones(game, info_of)
        if unknown:
            v.notes.append(
                "Link-Pfeile unbekannt (" + ", ".join(unknown) + ") — "
                "Kartendaten aktualisieren (Menü „Daten“)")
        elif zone not in zones:
            v.violations.append(
                f"{inst.name} muss aus dem Extra Deck in eine Extra-"
                "Monsterzone oder in eine Zone, auf die ein Link-Pfeil zeigt")


# ---------------------------------------------------------------------------
# Beschwoerungen
# ---------------------------------------------------------------------------

def _own_main_phase(game: Game, what: str, v: Verdict) -> None:
    if not game.my_turn:
        v.violations.append(f"{what} nur im eigenen Zug")
    elif game.phase not in MAIN_PHASES:
        v.violations.append(f"{what} nur in der Main Phase")


def check_normal_summon(game: Game, inst: _CardInst, info: dict, zone: str,
                        tributes: list[_CardInst], info_of,
                        set_: bool = False) -> Verdict:
    """Normalbeschwoerung bzw. Setzen aus der Hand (inkl. Tribute)."""
    v = Verdict()
    what = "Setzen" if set_ else "Normalbeschwörung"
    if not is_monster(info) or kind(info) == "token":
        v.violations.append(f"{inst.name} ist keine Monsterkarte")
        return v
    if is_extra(info) or kind(info) == "ritual":
        v.violations.append(
            f"{inst.name} kann nicht als Normalbeschwörung gerufen werden")
    _own_main_phase(game, what, v)
    if game.ns_used >= 1 + game.ns_extra:
        v.violations.append(
            "Normalbeschwörung/Setzen ist in diesem Zug schon verbraucht")
    need = tributes_needed(info)
    if len(tributes) != need:
        v.violations.append(
            f"Stufe {info.get('level')} braucht {need} Tribut"
            f"{'e' if need != 1 else ''}, gewählt: {len(tributes)}")
    if kind(info) == "link" and set_:
        v.violations.append("Link-Monster können nicht gesetzt werden")
    _check_monster_zone(game, inst, info, zone, False, info_of, v)
    return v


def check_special_summon(game: Game, inst: _CardInst, info: dict, zone: str,
                         from_extra: bool, info_of) -> Verdict:
    """Spezialbeschwoerung (Effekt oder Formel): nur Zonenregeln."""
    v = Verdict()
    if not is_monster(info):
        v.violations.append(f"{inst.name} ist keine Monsterkarte")
        return v
    _check_monster_zone(game, inst, info, zone, from_extra, info_of, v)
    return v


def _link_sums(ratings: list[set[int]]) -> set[int]:
    sums = {0}
    for options in ratings:
        sums = {s + o for s in sums for o in options}
    return sums


def check_materials(method: str, target: dict,
                    materials: list[tuple[_CardInst, dict]]) -> Verdict:
    """Materialpruefung einer Beschwoerungsformel. 'target' ist die
    play_info des Zielmonsters, 'materials' (Exemplar, info)-Paare.
    Geprueft wird die allgemeine Formel (Anzahl/Stufen/Link-Wert/Empfaenger)
    -- spezielle Anforderungen aus dem Kartentext (Typ, Attribut, Namen)
    nicht."""
    v = Verdict()
    names = [m.name for m, _ in materials]
    for m, info in materials:
        if m.face_down and method != "ritual":
            v.violations.append(f"{m.name} ist verdeckt")
    if method == "link":
        need = target.get("link_value") or 0
        if not materials:
            v.violations.append("Kein Link-Material gewählt")
        ratings = [{1, i.get("link_value") or 1} if kind(i) == "link" else {1}
                   for _, i in materials]
        if materials and need not in _link_sums(ratings):
            v.violations.append(
                f"{len(materials)} Material(ien) ergeben nicht Link-{need}")
        v.notes.append("Material-Anforderungen aus dem Kartentext werden nicht geprüft")
    elif method == "xyz":
        rank = target.get("level")
        if len(materials) < 2:
            v.violations.append("Xyz braucht mindestens 2 Materialien")
        for m, info in materials:
            if kind(info) == "token":
                v.violations.append(f"{m.name}: Spielmarken sind kein Xyz-Material")
                continue
            lvl = level_of(m, info)
            if lvl is None:
                v.violations.append(f"{m.name} hat keine Stufe")
            elif lvl != rank:
                v.violations.append(f"{m.name} hat Stufe {lvl}, nötig: {rank}")
        v.notes.append("Material-Anforderungen aus dem Kartentext werden nicht geprüft")
    elif method == "synchro":
        goal = target.get("level") or 0
        tuners = [m for m, i in materials if is_tuner(i)]
        others = [m for m, i in materials if not is_tuner(i)]
        if len(tuners) != 1:
            v.violations.append(
                f"Synchro braucht genau 1 Empfänger (Tuner), gewählt: {len(tuners)}")
        if not others:
            v.violations.append("Synchro braucht mindestens 1 Nicht-Empfänger")
        levels = [level_of(m, i) for m, i in materials]
        if any(lvl is None for lvl in levels):
            v.violations.append("Material ohne Stufe (Link/Xyz) gewählt")
        elif sum(levels) != goal:
            v.violations.append(f"Stufensumme {sum(levels)} ≠ Stufe {goal}")
        v.notes.append("Material-Anforderungen aus dem Kartentext werden nicht geprüft")
    elif method == "fusion":
        if len(materials) < 2:
            v.violations.append("Fusion braucht mindestens 2 Materialien")
        v.notes.append("Fusions-Materialien laut Kartentext werden nicht geprüft")
    elif method == "ritual":
        goal = target.get("level") or 0
        levels = [level_of(m, i) or 0 for m, i in materials]
        if not materials:
            v.violations.append("Kein Ritual-Tribut gewählt")
        elif sum(levels) < goal:
            v.violations.append(f"Tribut-Stufen {sum(levels)} < Stufe {goal}")
        v.notes.append("Nötig ist auch ein Ritualzauber (nicht geprüft)")
    if len(set(id(m) for m, _ in materials)) != len(names):
        v.violations.append("Ein Material ist doppelt gewählt")
    return v


def material_candidates(game: Game, method: str,
                        target: _CardInst | None = None) -> list[_CardInst]:
    """Karten, die als Material angeboten werden: eigene Monster auf dem
    Feld; bei Fusion und Ritual zusaetzlich Monster aus der Hand."""
    cands = [m for m in game.monsters("me") if m is not target]
    if method in ("fusion", "ritual"):
        cands += [c for c in game.me.hand if c is not target]
    return cands


# ---------------------------------------------------------------------------
# Zauber/Fallen, Positionen, Aktivieren
# ---------------------------------------------------------------------------

def check_spell_trap_from_hand(game: Game, inst: _CardInst, info: dict,
                               zone: str, activate: bool) -> Verdict:
    """Karte aus der Hand in eine Zauber/Fallen- oder die Feldzone."""
    v = Verdict()
    k = kind(info)
    sub = spell_trap_kind(info)
    zone = zone.removeprefix("o")
    if k not in ("spell", "trap"):
        if is_pendulum(info):
            if zone not in ("s0", "s4"):
                v.violations.append(
                    "Pendelmonster als Skala nur in die äußeren Zauber-/"
                    "Fallenzonen (Pendelzonen)")
            _own_main_phase(game, "Pendelskala aktivieren", v)
        else:
            v.violations.append(f"{inst.name} ist kein Zauber/keine Falle")
        return v
    if zone == "field" and sub != "Field":
        v.violations.append("In die Feldzone gehören nur Feldzauber")
    elif zone != "field" and sub == "Field":
        v.violations.append("Feldzauber gehören in die Feldzone")
    if not activate:
        _own_main_phase(game, "Setzen", v)
    elif k == "trap":
        v.violations.append(
            "Fallen müssen erst gesetzt werden (aktivierbar ab dem nächsten Zug)")
    elif sub == "Quick-Play":
        if not game.my_turn:
            v.violations.append("Schnellzauber aus der Hand nur im eigenen Zug")
    else:
        _own_main_phase(game, "Zauber aktivieren", v)
    return v


def check_activate_set(game: Game, inst: _CardInst, info: dict) -> Verdict:
    """Gesetzten Zauber/gesetzte Falle aufdecken = aktivieren."""
    v = Verdict()
    k = kind(info)
    sub = spell_trap_kind(info)
    if k == "trap" or (k == "spell" and sub == "Quick-Play"):
        if inst.uid in game.flags["set"]:
            v.violations.append(
                f"{inst.name} wurde in diesem Zug gesetzt — erst ab dem "
                "nächsten Zug aktivierbar")
    elif k == "spell":
        _own_main_phase(game, "Zauber aktivieren", v)
    return v


def check_position_change(game: Game, inst: _CardInst, info: dict) -> Verdict:
    """Manueller Wechsel Angriff <-> Verteidigung (offen)."""
    v = Verdict()
    if kind(info) == "link":
        v.violations.append("Link-Monster können nicht in Verteidigungsposition")
    _own_main_phase(game, "Positionswechsel", v)
    if inst.uid in game.flags["summoned"]:
        v.violations.append(f"{inst.name} wurde in diesem Zug beschworen")
    if inst.uid in game.flags["pos_changed"]:
        v.violations.append(f"Position von {inst.name} wurde in diesem Zug schon geändert")
    return v


def check_flip_summon(game: Game, inst: _CardInst, info: dict) -> Verdict:
    v = Verdict()
    _own_main_phase(game, "Flippbeschwörung", v)
    if inst.uid in game.flags["set"] or inst.uid in game.flags["summoned"]:
        v.violations.append(f"{inst.name} wurde in diesem Zug gesetzt")
    if inst.uid in game.flags["pos_changed"]:
        v.violations.append(f"Position von {inst.name} wurde in diesem Zug schon geändert")
    return v


def check_turn_face_down(inst: _CardInst, info: dict, zone: str) -> Verdict:
    """Offene Karte auf dem Feld verdecken -- regulaer nur per Effekt."""
    v = Verdict()
    if kind(info) == "link":
        v.violations.append("Link-Monster können nicht verdeckt sein")
    elif zone.removeprefix("o")[0] in "me":
        v.violations.append("Offene Monster werden nur durch Effekte verdeckt")
    else:
        v.violations.append("Offene Zauber/Fallen werden nur durch Effekte verdeckt")
    return v


# ---------------------------------------------------------------------------
# Zug & Phasen
# ---------------------------------------------------------------------------

def check_phase_change(game: Game, target: str) -> Verdict:
    v = Verdict()
    if PHASE_KEYS.index(target) < PHASE_KEYS.index(game.phase):
        v.violations.append("Phasen laufen nur vorwärts")
    if target in ("BP", "M2") and game.turn == 1:
        v.violations.append(
            "Im ersten Zug des Duells gibt es keine Battle Phase")
    return v


def check_end_turn(game: Game) -> Verdict:
    v = Verdict()
    n = len(game.me.hand)
    if game.my_turn and n > HAND_LIMIT:
        v.violations.append(
            f"Handlimit: {n} Karten — in der End Phase auf {HAND_LIMIT} abwerfen")
    return v


# ---------------------------------------------------------------------------
# Kampf
# ---------------------------------------------------------------------------

def check_attack(game: Game, attacker: _CardInst,
                 target: _CardInst | None) -> Verdict:
    v = Verdict()
    if not game.my_turn or game.phase != "BP":
        v.violations.append("Angriffe nur in der eigenen Battle Phase")
    if game.turn == 1:
        v.violations.append("Im ersten Zug des Duells darf nicht angegriffen werden")
    if attacker.face_down or attacker.defense:
        v.violations.append(f"{attacker.name} ist nicht in offener Angriffsposition")
    if attacker.uid in game.flags["attacked"]:
        v.violations.append(f"{attacker.name} hat in diesem Zug schon angegriffen")
    if target is None and game.monsters("opp"):
        v.violations.append("Direkter Angriff nur, wenn der Gegner keine Monster hat")
    return v


@dataclass
class BattleResult:
    attacker_destroyed: bool = False
    target_destroyed: bool = False
    damage_me: int = 0          # Kampfschaden an dich
    damage_opp: int = 0         # Kampfschaden an den Gegner


def damage_calc(atk: int, target: tuple[int, int | None, bool] | None) -> BattleResult:
    """Schadensberechnung. 'target' = (ATK, DEF, in_Verteidigung) oder None
    fuer einen direkten Angriff."""
    r = BattleResult()
    if target is None:
        r.damage_opp = atk
        return r
    t_atk, t_def, defense = target
    if defense:
        t_def = t_def or 0
        if atk > t_def:
            r.target_destroyed = True
        elif atk < t_def:
            r.damage_me = t_def - atk
        return r
    if atk > t_atk:
        r.target_destroyed = True
        r.damage_opp = atk - t_atk
    elif atk < t_atk:
        r.attacker_destroyed = True
        r.damage_me = t_atk - atk
    elif atk > 0:                     # gleich stark (und nicht 0/0)
        r.attacker_destroyed = r.target_destroyed = True
    return r

