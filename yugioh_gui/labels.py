"""Uebersetzungstabellen (EN -> DE) und geteilte Anzeige-Konstanten.

Beschriftungen fuer Attribute, Kartentypen, Typ-Linien, Kartenklassen
und Deck-Zonen plus die Qt-ItemData-Slots, die mehrere Views teilen.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

import yugioh_db as ydb


# ---------------------------------------------------------------------------
# Übersetzungstabellen (Englisch → Deutsch)
# ---------------------------------------------------------------------------

ATTR_DE: dict[str, str] = {
    "DARK":   "FINSTERNIS",
    "DIVINE": "GÖTTLICH",
    "EARTH":  "ERDE",
    "FIRE":   "FEUER",
    "LIGHT":  "LICHT",
    "WATER":  "WASSER",
    "WIND":   "WIND",
}

TYPE_DE: dict[str, str] = {
    "Effect Monster":                  "Effekt-Monster",
    "Flip Effect Monster":             "Flipp-Effekt-Monster",
    "Flip Tuner Effect Monster":       "Flipp-Tuner-Effekt-Monster",
    "Fusion Monster":                  "Fusionsmonster",
    "Gemini Monster":                  "Gemini-Monster",
    "Link Monster":                    "Linkmonster",
    "Normal Monster":                  "Normalmonster",
    "Normal Tuner Monster":            "Tuner (Normal)",
    "Pendulum Effect Fusion Monster":  "Pendel-Effekt-Fusionsmonster",
    "Pendulum Effect Monster":         "Pendel-Effekt-Monster",
    "Pendulum Effect Ritual Monster":  "Pendel-Effekt-Ritualmonster",
    "Pendulum Flip Effect Monster":    "Pendel-Flipp-Effekt-Monster",
    "Pendulum Normal Monster":         "Pendel-Normalmonster",
    "Pendulum Tuner Effect Monster":   "Pendel-Tuner-Effekt-Monster",
    "Ritual Effect Monster":           "Ritual-Effekt-Monster",
    "Ritual Monster":                  "Ritualmonster",
    "Skill Card":                      "Skill-Karte",
    "Spell Card":                      "Zauberkarte",
    "Spirit Monster":                  "Geist-Monster",
    "Synchro Monster":                 "Synchromonster",
    "Synchro Pendulum Effect Monster": "Synchro-Pendel-Effekt-Monster",
    "Synchro Tuner Monster":           "Synchro-Tuner",
    "Token":                           "Spielmarke",
    "Toon Monster":                    "Toon-Monster",
    "Trap Card":                       "Fallenkarte",
    "Tuner Monster":                   "Tuner",
    "Union Effect Monster":            "Union-Effekt-Monster",
    "XYZ Monster":                     "Xyz-Monster",
    "XYZ Pendulum Effect Monster":     "Xyz-Pendel-Effekt-Monster",
}

# Kartenklassen für die Gruppierung (Sammlung, Main-Deck).
CATEGORY_DE: dict[str, str] = {
    "monster": "Monster",
    "spell":   "Zauber",
    "trap":    "Falle",
    "other":   "Sonstige",
}
CATEGORY_ORDER = ("monster", "spell", "trap", "other")


# Anzeigenamen der Baustein-Rollen (die Begriffe sind im deutschen
# Yu-Gi-Oh-Sprachgebrauch etabliert, daher unübersetzt). Eine Quelle der
# Wahrheit liegt bei den Rollen selbst in der Datenschicht.
ROLE_DE = ydb.ROLE_LABEL
# Zweiter Daten-Slot an Bausteine-Listeneinträgen: die Rolle (UserRole = card_id).
PIECE_ROLE_DATA = Qt.ItemDataRole.UserRole + 1
# Zweiter Daten-Slot im Starthand-Picker: Kopienzahl der Karte (UserRole = card_id).
SIM_COPIES_DATA = Qt.ItemDataRole.UserRole + 1
# Zweiter Daten-Slot der Sammlungs-Namenszelle: card_id (UserRole = entry_id) --
# fuer die Hover-Bildvorschau, die das Kartenbild und nicht den Eintrag braucht.
COLLECTION_CARD_ID = Qt.ItemDataRole.UserRole + 1


def pct(p: float) -> str:
    """Wahrscheinlichkeit als deutschen Prozentwert formatieren (84,2 %)."""
    return f"{100 * p:.1f}".replace(".", ",") + " %"


def import_report_lines(report: dict) -> list[str]:
    """Meldungszeilen aus einem import_deck_ydk-Report (Deck- und
    Korpus-Import zeigen denselben Bericht)."""
    imp = report["imported"]
    lines = [
        f"Importiert: Main {imp['main']}, Extra {imp['extra']}, "
        f"Side {imp['side']}."
    ]
    if report["unknown"]:
        ids = ", ".join(str(i) for i in report["unknown"])
        lines.append(f"Nicht in der Datenbank (übersprungen): {ids}")
    if report["capped"]:
        lines.append(
            "Über der 3-Kopien-Grenze gekürzt: " + ", ".join(report["capped"])
        )
    if report["moved"]:
        lines.append(
            "In die passende Zone verschoben: " + ", ".join(report["moved"])
        )
    if report.get("unreadable"):
        shown = report["unreadable"][:5]
        more = len(report["unreadable"]) - len(shown)
        lines.append(
            "Nicht lesbare Zeilen (übersprungen): " + ", ".join(shown)
            + (f" … und {more} weitere" if more > 0 else "")
        )
    return lines


RACE_DE: dict[str, str] = {
    # Monster-Typen
    "Aqua":         "Aqua",
    "Beast":        "Ungeheuer",
    "Beast-Warrior":"Ungeheuer-Krieger",
    "Creator God":  "Creator God",
    "Cyberse":      "Cyberse",
    "Dinosaur":     "Dinosaurier",
    "Divine-Beast": "Göttliches Ungeheuer",
    "Dragon":       "Drache",
    "Fairy":        "Fee",
    "Fiend":        "Unterweltler",
    "Fish":         "Fisch",
    "Illusion":     "Illusion",
    "Insect":       "Insekt",
    "Machine":      "Maschine",
    "Plant":        "Pflanze",
    "Psychic":      "Psi",
    "Pyro":         "Pyro",
    "Reptile":      "Reptil",
    "Rock":         "Fels",
    "Sea Serpent":  "Seeschlange",
    "Spellcaster":  "Hexer",
    "Thunder":      "Donner",
    "Warrior":      "Krieger",
    "Winged Beast": "Geflügeltes Ungeheuer",
    "Wyrm":         "Wyrm",
    "Zombie":       "Zombie",
    # Zauber/Fallen-Untertypen
    "Continuous":   "Permanent",
    "Counter":      "Konter",
    "Equip":        "Ausrüstung",
    "Field":        "Feld",
    "Normal":       "Normal",
    "Quick-Play":   "Schnelleffekt",
    "Ritual":       "Ritual",
}


ZONE_LABELS = {"main": "Main Deck", "extra": "Extra Deck", "side": "Side Deck"}


# ---------------------------------------------------------------------------
# Kartenaufbau: Typzeile, Stufe/Rang/Link, Symbole (Suche + Detailansicht)
# ---------------------------------------------------------------------------

# Begriffe der Typzeile ([Drache/Synchro/Effekt]) ausser dem Typ selbst.
TYPELINE_DE: dict[str, str] = {
    "Normal": "Normal", "Effect": "Effekt", "Tuner": "Empfänger",
    "Flip": "Flipp", "Gemini": "Zwilling", "Spirit": "Spirit",
    "Union": "Union", "Toon": "Toon", "Ritual": "Ritual", "Fusion": "Fusion",
    "Synchro": "Synchro", "XYZ": "Xyz", "Xyz": "Xyz", "Link": "Link",
    "Pendulum": "Pendel",
}
# Symbol der Zauber-/Fallenkarte (API: 'race').
SPELL_KIND_DE: dict[str, str] = {
    "Normal": "Normal", "Quick-Play": "Schnell", "Continuous": "Permanent",
    "Equip": "Ausrüstung", "Field": "Feld", "Ritual": "Ritual",
}
TRAP_KIND_DE: dict[str, str] = {
    "Normal": "Normal", "Continuous": "Permanent", "Counter": "Konter",
}
# Link-Pfeile im Uhrzeigersinn ab oben links.
LINK_ARROWS: dict[str, str] = {
    "Top-Left": "↖", "Top": "↑", "Top-Right": "↗", "Right": "→",
    "Bottom-Right": "↘", "Bottom": "↓", "Bottom-Left": "↙", "Left": "←",
}
# Reihenfolge der Typzeilen-Begriffe auf der Karte (Rueckfall ohne 'typeline').
_TYPELINE_ORDER = ("Ritual", "Fusion", "Synchro", "XYZ", "Link", "Pendulum",
                   "Flip", "Gemini", "Spirit", "Toon", "Union", "Tuner")


def _col(card, key):
    """Spalte einer Kartenzeile oder None (aeltere Zeilen ohne die Spalte)."""
    try:
        return card[key]
    except (IndexError, KeyError):
        return None


def is_monster(card) -> bool:
    return "Monster" in (card["type"] or "")


def typeline_parts(card) -> list[str]:
    """Typzeile wie auf der Karte, deutsch: ['Drache', 'Synchro', 'Effekt'].
    Quelle ist die API-Typzeile; fehlt sie (Kartendaten vor dem Update),
    wird sie aus 'type' hergeleitet -- 'Effekt' dann nur, wenn er sicher ist
    (Extra-Deck-Monster nennen ihn in 'type' nicht immer)."""
    if not is_monster(card):
        return []
    raw = _col(card, "typeline")
    if raw:
        tokens = raw.split(",")
        race, rest = tokens[0], tokens[1:]
        return [RACE_DE.get(race, race)] + [TYPELINE_DE.get(t, t) for t in rest]
    words = (card["type"] or "").replace("Monster", "").split()
    parts = [RACE_DE.get(card["race"], card["race"])] if card["race"] else []
    parts += [TYPELINE_DE[w] for w in _TYPELINE_ORDER if w in words]
    frame = card["frame_type"] or ""
    if "Normal" in words or frame in ("normal", "normal_pendulum"):
        parts.append("Normal")
    elif "Effect" in words or frame in ("effect", "effect_pendulum"):
        parts.append("Effekt")
    return parts


def typeline_text(card) -> str:
    parts = typeline_parts(card)
    return "[" + "/".join(parts) + "]" if parts else ""


def level_text(card, short: bool = False) -> str:
    """'★ Stufe 4', 'Rang 4' (Xyz) oder 'LINK-2'; short: '★4', 'R4', 'L2'."""
    if card["frame_type"] == "link":
        lv = card["link_value"]
        return "" if lv is None else (f"L{lv}" if short else f"LINK-{lv}")
    if card["level"] is None:
        return ""
    if (card["frame_type"] or "").startswith("xyz"):
        return f"R{card['level']}" if short else f"Rang {card['level']}"
    return f"★{card['level']}" if short else f"★ Stufe {card['level']}"


def link_arrows_text(card) -> str:
    """Link-Pfeile als Symbole im Uhrzeigersinn ('↑ → ↙'); leer ohne Daten."""
    markers = set((_col(card, "link_markers") or "").split(","))
    return " ".join(sym for key, sym in LINK_ARROWS.items() if key in markers)


def card_kind_text(card) -> str:
    """Kurzbeschreibung fuer Trefferlisten: 'FINSTERNIS · Synchro',
    'Zauber · Schnell', 'Falle · Konter'."""
    if is_monster(card):
        attr = ATTR_DE.get(card["attribute"], card["attribute"] or "")
        frame = (card["frame_type"] or "").replace("_pendulum", "")
        kind = {"normal": "Normal", "effect": "Effekt", "ritual": "Ritual",
                "fusion": "Fusion", "synchro": "Synchro", "xyz": "Xyz",
                "link": "Link"}.get(frame, "")
        if (card["frame_type"] or "").endswith("_pendulum"):
            kind = f"{kind}-Pendel" if kind else "Pendel"
        return " · ".join(p for p in (attr, kind) if p)
    if card["type"] == "Spell Card":
        return "Zauber · " + SPELL_KIND_DE.get(card["race"], card["race"] or "?")
    if card["type"] == "Trap Card":
        return "Falle · " + TRAP_KIND_DE.get(card["race"], card["race"] or "?")
    return TYPE_DE.get(card["type"], card["type"] or "")
