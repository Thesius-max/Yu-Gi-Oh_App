"""Bestand (Sammlung): Eintraege, Filter, Statistiken.

Identische Drucke werden zusammengefuehrt statt dupliziert
(add_to_collection, NULL-sicherer Abgleich per IS).
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from .schema import _conn, _like_contains


# ---------------------------------------------------------------------------
# Bestand (Sammlung)
# ---------------------------------------------------------------------------

def add_to_collection(
    db_path: str,
    card_id: int,
    quantity: int = 1,
    *,
    set_code: Optional[str] = None,
    edition: Optional[str] = None,
    condition: Optional[str] = None,
    language: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    with _conn(db_path) as conn:
        # Identischen Druck zusammenfuehren (IS vergleicht NULL-sicher),
        # statt einen zweiten Eintrag anzulegen.
        existing = conn.execute(
            """SELECT entry_id FROM collection
               WHERE card_id = ? AND set_code IS ? AND edition IS ?
                 AND condition IS ? AND language IS ?""",
            (card_id, set_code, edition, condition, language),
        ).fetchone()
        if existing:
            entry_id = existing["entry_id"]
            conn.execute(
                "UPDATE collection SET quantity = quantity + ? WHERE entry_id = ?",
                (quantity, entry_id),
            )
        else:
            cur = conn.execute(
                """INSERT INTO collection
                   (card_id, quantity, set_code, edition, condition, language, notes)
                   VALUES (?,?,?,?,?,?,?)""",
                (card_id, quantity, set_code, edition, condition, language, notes),
            )
            entry_id = cur.lastrowid
        conn.commit()
        return entry_id


def list_collection(
    db_path: str,
    text: Optional[str] = None,
    category: Optional[str] = None,
    attribute: Optional[str] = None,
    archetype: Optional[str] = None,
    untranslated_only: bool = False,
) -> list[sqlite3.Row]:
    """Bestandseintraege mit Kartenname -- ein Eintrag (Druck) pro Zeile.
    Optional gefiltert: text (Namenssuche de/en), category (Wert von
    card_category), attribute, archetype, untranslated_only (nur Karten
    ohne eigene/API-Uebersetzung, also name_de IS NULL)."""
    sql = """SELECT col.entry_id, col.card_id, c.name, c.name_de, c.type,
                    c.attribute, c.archetype,
                    col.quantity, col.set_code, col.edition,
                    col.condition, col.language, col.notes
             FROM collection col
             JOIN cards c ON c.id = col.card_id"""
    where, args = [], []
    if text and text.strip():
        like = _like_contains(text)
        where.append(
            "(casefold(c.name) LIKE ? ESCAPE '\\' "
            "OR casefold(c.name_de) LIKE ? ESCAPE '\\')"
        )
        args += [like, like]
    if untranslated_only:
        where.append("c.name_de IS NULL")
    if attribute:
        where.append("c.attribute = ?")
        args.append(attribute)
    if archetype:
        where.append("c.archetype = ?")
        args.append(archetype)
    if category:
        # card_category()-Logik direkt in SQL: Spell/Trap vor Monster pruefen,
        # da z.B. "Spell Card" kein "Monster" enthaelt.
        cat_expr = (
            "CASE WHEN c.type LIKE '%Spell%' THEN 'spell' "
            "WHEN c.type LIKE '%Trap%' THEN 'trap' "
            "WHEN c.type LIKE '%Monster%' THEN 'monster' "
            "ELSE 'other' END"
        )
        where.append(f"{cat_expr} = ?")
        args.append(category)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(c.name_de, c.name), col.entry_id"
    with _conn(db_path) as conn:
        return conn.execute(sql, args).fetchall()


def collection_distinct(db_path: str, column: str) -> list[str]:
    """Vorhandene Werte einer Kartenspalte im eigenen Bestand (fuer Filter)."""
    if column not in ("attribute", "archetype", "race", "type"):
        raise ValueError(f"Unerwartete Spalte: {column}")
    with _conn(db_path) as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT c.{column} AS v
                FROM collection col JOIN cards c ON c.id = col.card_id
                WHERE c.{column} IS NOT NULL ORDER BY v"""
        ).fetchall()
        return [r["v"] for r in rows]


def set_collection_quantity(db_path: str, entry_id: int, quantity: int) -> None:
    """Setzt die Menge eines Eintrags. quantity <= 0 entfernt den Eintrag."""
    with _conn(db_path) as conn:
        if quantity <= 0:
            conn.execute("DELETE FROM collection WHERE entry_id = ?", (entry_id,))
        else:
            conn.execute(
                "UPDATE collection SET quantity = ? WHERE entry_id = ?",
                (quantity, entry_id),
            )
        conn.commit()


def remove_collection_entry(db_path: str, entry_id: int) -> None:
    """Entfernt einen Bestandseintrag vollstaendig."""
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM collection WHERE entry_id = ?", (entry_id,))
        conn.commit()


def collection_stats(db_path: str) -> tuple[int, int, int]:
    """(Anzahl Eintraege, verschiedene Karten, Karten gesamt)."""
    with _conn(db_path) as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS entries,
                      COUNT(DISTINCT card_id) AS unique_cards,
                      COALESCE(SUM(quantity), 0) AS total
               FROM collection"""
        ).fetchone()
        return row["entries"], row["unique_cards"], row["total"]


def collection_untranslated_count(db_path: str) -> int:
    """Anzahl verschiedener Karten im Bestand ohne deutsche Uebersetzung
    (name_de IS NULL). Zaehlt Karten, nicht Bestandseintraege/Drucke."""
    with _conn(db_path) as conn:
        row = conn.execute(
            """SELECT COUNT(DISTINCT col.card_id) AS n
               FROM collection col JOIN cards c ON c.id = col.card_id
               WHERE c.name_de IS NULL"""
        ).fetchone()
        return int(row["n"])


def collection_summary_stats(
    db_path: str,
) -> tuple[int, int, int, int]:
    """(Eintraege, verschiedene Karten, Karten gesamt, ohne DE-Uebersetzung).
    Fuehrt collection_stats + collection_untranslated_count in einem DB-Trip zusammen."""
    with _conn(db_path) as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS entries,
                      COUNT(DISTINCT col.card_id) AS unique_cards,
                      COALESCE(SUM(col.quantity), 0) AS total,
                      COUNT(DISTINCT CASE WHEN c.name_de IS NULL
                                         THEN col.card_id END) AS untranslated
               FROM collection col
               JOIN cards c ON c.id = col.card_id"""
        ).fetchone()
        return (
            row["entries"], row["unique_cards"],
            row["total"], row["untranslated"],
        )


def collection_overview(db_path: str) -> list[sqlite3.Row]:
    """Bestand mit Kartennamen und Gesamtmenge je Karte."""
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT c.id, COALESCE(c.name_de, c.name) AS name, c.type,
                      SUM(col.quantity) AS total
               FROM collection col
               JOIN cards c ON c.id = col.card_id
               GROUP BY c.id
               ORDER BY COALESCE(c.name_de, c.name)"""
        ).fetchall()
