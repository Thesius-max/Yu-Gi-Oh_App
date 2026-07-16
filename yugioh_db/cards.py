"""Karten-Nachschlagewerk: Suche, Filter, Klassifikation, Uebersetzung.

Volltext-/Attributsuche ueber die Referenztabelle cards, die natuerliche
Zonen-/Kategorien-Zuordnung (deck_zone_for, card_category) und eigene
DE-Uebersetzungen (set_card_translation, ueberleben Daten-Updates).
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from .schema import _conn


# ---------------------------------------------------------------------------
# Suche / Filter (Nachschlagewerk)
# ---------------------------------------------------------------------------

def search_text(db_path: str, query: str, limit: int = 50) -> list[sqlite3.Row]:
    """Volltextsuche in Name und Kartentext."""
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT c.* FROM cards_fts
               JOIN cards c ON c.id = cards_fts.rowid
               WHERE cards_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()


def filter_cards(
    db_path: str,
    *,
    type: Optional[str] = None,
    attribute: Optional[str] = None,
    archetype: Optional[str] = None,
    level: Optional[int] = None,
    atk_min: Optional[int] = None,
    atk_max: Optional[int] = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    """Strukturierter Filter ueber die Kartenattribute."""
    clauses, params = [], []
    if type is not None:
        clauses.append("type = ?"); params.append(type)
    if attribute is not None:
        clauses.append("attribute = ?"); params.append(attribute)
    if archetype is not None:
        clauses.append("archetype = ?"); params.append(archetype)
    if level is not None:
        clauses.append("level = ?"); params.append(level)
    if atk_min is not None:
        clauses.append("atk >= ?"); params.append(atk_min)
    if atk_max is not None:
        clauses.append("atk <= ?"); params.append(atk_max)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with _conn(db_path) as conn:
        return conn.execute(
            f"SELECT * FROM cards {where} ORDER BY name LIMIT ?", params
        ).fetchall()


def set_card_translation(
    db_path: str, card_id: int,
    name_de: Optional[str] = None, desc_de: Optional[str] = None,
) -> None:
    """Eigene DE-Uebersetzung (Override) fuer eine Karte setzen.

    Leere Werte bedeuten 'kein Override fuer dieses Feld'; sind beide leer,
    wird der Override entfernt (die Karte faellt beim naechsten Daten-Update
    auf die API-Werte zurueck). Schreibt direkt nach cards durch und haelt
    den FTS-Index aktuell, damit die Suche den Namen sofort findet."""
    name_de = (name_de or "").strip() or None
    desc_de = (desc_de or "").strip() or None
    with _conn(db_path) as conn:
        old = conn.execute(
            "SELECT name, description, name_de, desc_de FROM cards WHERE id = ?",
            (card_id,),
        ).fetchone()
        if old is None:
            raise ValueError(f"Karte {card_id} nicht gefunden.")
        if name_de is None and desc_de is None:
            conn.execute(
                "DELETE FROM card_translations WHERE card_id = ?", (card_id,)
            )
            conn.commit()
            return
        conn.execute(
            """INSERT INTO card_translations (card_id, name_de, desc_de)
               VALUES (?,?,?)
               ON CONFLICT(card_id) DO UPDATE SET
                   name_de = excluded.name_de, desc_de = excluded.desc_de""",
            (card_id, name_de, desc_de),
        )
        # FTS (external content) verlangt beim Loeschen die ALTEN Werte.
        conn.execute(
            "INSERT INTO cards_fts (cards_fts, rowid, name, description, "
            "name_de, desc_de) VALUES ('delete', ?,?,?,?,?)",
            (card_id, old["name"], old["description"],
             old["name_de"], old["desc_de"]),
        )
        conn.execute(
            "UPDATE cards SET name_de = COALESCE(?, name_de), "
            "desc_de = COALESCE(?, desc_de) WHERE id = ?",
            (name_de, desc_de, card_id),
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
