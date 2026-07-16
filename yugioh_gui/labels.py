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
