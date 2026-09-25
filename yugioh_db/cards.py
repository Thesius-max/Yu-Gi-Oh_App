"""Karten-Nachschlagewerk: Klassifikation und eigene Uebersetzungen.

Die natuerliche Zonen-/Kategorien-Zuordnung (deck_zone_for, card_category)
und eigene DE-Uebersetzungen (get/set_card_translation, ueberleben
Daten-Updates). Die Kartensuche selbst lebt in der GUI (CardRepository).
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from .schema import _conn


def get_card_translation(db_path: str, card_id: int) -> Optional[sqlite3.Row]:
    """Eigener DE-Override einer Karte (name_de, desc_de) oder None --
    nur die Benutzerdaten, nicht die API-Werte aus cards."""
    with _conn(db_path) as conn:
        return conn.execute(
            "SELECT name_de, desc_de FROM card_translations WHERE card_id = ?",
            (card_id,),
        ).fetchone()


def set_card_translation(
    db_path: str, card_id: int,
    name_de: Optional[str] = None, desc_de: Optional[str] = None,
) -> None:
    """Eigene DE-Uebersetzung (Override) fuer eine Karte setzen.

    Leere Werte bedeuten 'kein Override fuer dieses Feld'; sind beide leer,
    wird der Override entfernt. Schreibt direkt nach cards durch und haelt
    den FTS-Index aktuell, damit die Suche den Namen sofort findet. Wird ein
    bestehender Override geleert, faellt das Feld in cards auf NULL zurueck
    (der API-Wert ist lokal nicht mehr vorhanden -- er kommt beim naechsten
    Daten-Update zurueck); so bleibt ein falscher Name nicht haengen."""
    new = {
        "name_de": (name_de or "").strip() or None,
        "desc_de": (desc_de or "").strip() or None,
    }
    with _conn(db_path) as conn:
        old = conn.execute(
            "SELECT name, description, name_de, desc_de FROM cards WHERE id = ?",
            (card_id,),
        ).fetchone()
        if old is None:
            raise ValueError(f"Karte {card_id} nicht gefunden.")
        prev = conn.execute(
            "SELECT name_de, desc_de FROM card_translations WHERE card_id = ?",
            (card_id,),
        ).fetchone()
        if new["name_de"] is None and new["desc_de"] is None:
            conn.execute(
                "DELETE FROM card_translations WHERE card_id = ?", (card_id,)
            )
        else:
            conn.execute(
                """INSERT INTO card_translations (card_id, name_de, desc_de)
                   VALUES (?,?,?)
                   ON CONFLICT(card_id) DO UPDATE SET
                       name_de = excluded.name_de, desc_de = excluded.desc_de""",
                (card_id, new["name_de"], new["desc_de"]),
            )
        values = {}
        for field, value in new.items():
            if value is not None:
                values[field] = value
            elif prev is not None and prev[field] is not None                     and old[field] == prev[field]:
                values[field] = None      # Override entfernt -> kein DE-Wert
            else:
                values[field] = old[field]  # API-Wert bleibt unangetastet
        # FTS (external content) verlangt beim Loeschen die ALTEN Werte.
        conn.execute(
            "INSERT INTO cards_fts (cards_fts, rowid, name, description, "
            "name_de, desc_de) VALUES ('delete', ?,?,?,?,?)",
            (card_id, old["name"], old["description"],
             old["name_de"], old["desc_de"]),
        )
        conn.execute(
            "UPDATE cards SET name_de = ?, desc_de = ? WHERE id = ?",
            (values["name_de"], values["desc_de"], card_id),
        )
        conn.execute(
            "INSERT INTO cards_fts (rowid, name, description, name_de, desc_de) "
            "SELECT id, name, description, name_de, desc_de FROM cards "
            "WHERE id = ?",
            (card_id,),
        )
        conn.commit()


# Frame-Typen, die ins Extra Deck gehoeren.
EXTRA_DECK_FRAMES = {
    "fusion", "synchro", "xyz", "link",
    "synchro_pendulum", "xyz_pendulum", "fusion_pendulum",
}


def deck_zone_for(frame_type: Optional[str], card_type: str = "") -> str:
    """Natuerliche Zone einer Karte: 'extra' (Fusion/Synchro/Xyz/Link) sonst 'main'."""
    ft = (frame_type or "").lower()
    if ft in EXTRA_DECK_FRAMES:
        return "extra"
    if any(k in (card_type or "") for k in ("Fusion", "Synchro", "Xyz", "XYZ", "Link")):
        return "extra"
    return "main"


def card_category(card_type: Optional[str]) -> str:
    """Grobe Kartenklasse fuer die Anzeige/Gruppierung:
    'monster' | 'spell' | 'trap' | 'other' (z.B. Skill/Token)."""
    t = card_type or ""
    if "Spell" in t:
        return "spell"
    if "Trap" in t:
        return "trap"
    if "Monster" in t:
        return "monster"
    return "other"


# Spalten fuer die Spielfeld-Regeln (Beschwoerungen, Material, Zonen, Kampf).
PLAY_INFO_COLUMNS = (
    "c.id, COALESCE(c.name_de, c.name) AS name, c.type, c.frame_type, "
    "c.level, c.atk, c.def, c.race, c.attribute, c.scale, c.link_value, "
    "c.link_markers"
)


def play_info_from_row(row) -> dict:
    """Regel-relevante Kartenwerte als dict (Zeile mit PLAY_INFO_COLUMNS).
    'link_markers' ist ein Tupel der API-Pfeilnamen ('Top', 'Bottom-Left',
    ...); None bei Link-Monstern heisst *unbekannt* (Kartendaten aelter als
    die Spalte), bei allen anderen Karten 'keine Pfeile'."""
    markers = row["link_markers"]
    return {
        "name": row["name"], "type": row["type"] or "",
        "frame_type": row["frame_type"] or "",
        "level": row["level"], "atk": row["atk"], "def": row["def"],
        "race": row["race"], "attribute": row["attribute"],
        "scale": row["scale"], "link_value": row["link_value"],
        "link_markers": tuple(markers.split(",")) if markers else None,
    }


def card_play_info(db_path: str, card_ids) -> dict[int, dict]:
    """{card_id: play_info} fuer beliebige Karten (z. B. Gegner-Karten auf
    dem Spielfeld). Unbekannte IDs fehlen im Ergebnis."""
    ids = sorted({int(i) for i in card_ids})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with _conn(db_path) as conn:
        rows = conn.execute(
            f"SELECT {PLAY_INFO_COLUMNS} FROM cards c WHERE c.id IN ({marks})",
            ids,
        ).fetchall()
    return {r["id"]: play_info_from_row(r) for r in rows}
