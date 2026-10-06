"""Bestand (Sammlung): Eintraege, Filter, Statistiken, Set-Auswahl.

Gespeichert wird **je Druck** (Karte + Set/Edition/Zustand/Sprache);
identische Drucke werden zusammengefuehrt statt dupliziert
(add_to_collection und update_collection_print, NULL-sicherer Abgleich per
IS). Angezeigt und exportiert wird **je Karte** (list_collection_cards):
eine Karte ist eine Zeile, die Drucke schluesseln nur die Menge auf
(prints_summary). Set-Codes kennt die API nur englisch; localize_set_code
schreibt sie fuer deutsche Drucke um (RA01-EN008 -> RA01-DE008).
"""

from __future__ import annotations

import html
import re
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


# Sprachen eines Drucks (collection.language): Schluessel -> Anzeige.
PRINT_LANGUAGES = (("DE", "Deutsch"), ("EN", "Englisch"))
_REGION = {"DE": ("DE", "G"), "EN": ("EN", "E")}   # neuer / alter EU-Code
_CODE_RE = re.compile(r"^([A-Z0-9]+)-(EN|DE|E|G)(?=\d|[A-Z]\d)", re.I)


def localize_set_code(code: Optional[str], language: Optional[str]) -> Optional[str]:
    """Set-Code in die Region einer Sprache umschreiben: 'DE' macht aus
    RA01-EN008 -> RA01-DE008 und aus dem alten EU-Code PSV-E088 -> PSV-G088,
    'EN' umgekehrt. Codes ohne Regionskennung (PSV-088) und unbekannte
    Sprachen bleiben unveraendert."""
    if not code or language not in _REGION:
        return code
    m = _CODE_RE.match(code)
    if not m:
        return code
    region = m.group(2).upper()
    new = _REGION[language][0] if region in ("EN", "DE") else _REGION[language][1]
    return f"{m.group(1)}-{new}{code[m.end():]}"


def card_set_choices(db_path: str, card_id: int) -> list[dict]:
    """Bekannte Sets einer Karte als [{'code', 'name'}] -- je Set-Code
    einmal (Seltenheiten zusammengefasst), nach Set-Name sortiert. Namen
    werden entschaerft (aeltere Kartendaten enthalten noch '&apos;')."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT set_code, MIN(set_name) AS set_name FROM card_sets "
            "WHERE card_id = ? AND set_code IS NOT NULL AND set_code != '' "
            "GROUP BY set_code",
            (card_id,),
        ).fetchall()
    out = [{"code": r["set_code"], "name": html.unescape(r["set_name"] or "")}
           for r in rows]
    out.sort(key=lambda s: (s["name"].casefold(), s["code"]))
    return out


def update_collection_print(
    db_path: str, entry_id: int, set_code: Optional[str],
    language: Optional[str],
) -> int:
    """Set und Sprache eines Sammlungseintrags aendern. Entsteht dadurch ein
    Druck, den es schon gibt (Karte, Set, Edition, Zustand, Sprache gleich),
    werden die Eintraege zusammengefuehrt: Mengen addiert, Notizen
    verbunden, der geaenderte Eintrag geloescht. Rueckgabe: die entry_id des
    verbleibenden Eintrags."""
    set_code = (set_code or "").strip() or None
    language = (language or "").strip() or None
    with _conn(db_path) as conn:
        cur = conn.execute(
            "SELECT * FROM collection WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        if cur is None:
            raise ValueError(f"Sammlungseintrag {entry_id} nicht gefunden.")
        twin = conn.execute(
            """SELECT entry_id, quantity, notes FROM collection
               WHERE card_id = ? AND set_code IS ? AND edition IS ?
                 AND condition IS ? AND language IS ? AND entry_id != ?""",
            (cur["card_id"], set_code, cur["edition"], cur["condition"],
             language, entry_id),
        ).fetchone()
        if twin is None:
            conn.execute(
                "UPDATE collection SET set_code = ?, language = ? WHERE entry_id = ?",
                (set_code, language, entry_id),
            )
            result = entry_id
        else:
            notes = "; ".join(n for n in (twin["notes"], cur["notes"])
                              if n and n.strip()) or None
            conn.execute(
                "UPDATE collection SET quantity = ?, notes = ? WHERE entry_id = ?",
                (twin["quantity"] + cur["quantity"], notes, twin["entry_id"]),
            )
            conn.execute("DELETE FROM collection WHERE entry_id = ?", (entry_id,))
            result = twin["entry_id"]
        conn.commit()
        return result


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


def list_collection_cards(
    db_path: str,
    text: Optional[str] = None,
    category: Optional[str] = None,
    attribute: Optional[str] = None,
    archetype: Optional[str] = None,
    untranslated_only: bool = False,
) -> list[dict]:
    """Bestand **je Karte** (gleiche Filter wie list_collection): eine
    Karte ist ein Eintrag mit Gesamtmenge; 'prints' enthaelt die einzelnen
    Drucke (Bestandseintraege). Reihenfolge wie list_collection (Name)."""
    cards: dict[int, dict] = {}
    for r in list_collection(db_path, text, category, attribute, archetype,
                             untranslated_only=untranslated_only):
        card = cards.get(r["card_id"])
        if card is None:
            card = cards[r["card_id"]] = {
                "card_id": r["card_id"], "name": r["name"],
                "name_de": r["name_de"], "type": r["type"],
                "attribute": r["attribute"], "archetype": r["archetype"],
                "quantity": 0, "prints": [],
            }
        card["quantity"] += r["quantity"]
        card["prints"].append(r)
    return list(cards.values())


def print_label(entry) -> str:
    """Kurzbezeichnung eines Drucks: 'RA01-DE008 (DE, 1st, NM)' bzw.
    'ohne Set'."""
    details = [entry[k] for k in ("language", "edition", "condition") if entry[k]]
    label = entry["set_code"] or "ohne Set"
    return label + (f" ({', '.join(details)})" if details else "")


def prints_summary(prints) -> str:
    """Aufschluesselung der Menge nach Drucken, z. B.
    '2× RA01-DE008 (DE) · 3× ohne Set'. Leer, wenn es nur einen Druck ohne
    jede Angabe gibt (dann sagt die Menge schon alles)."""
    if len(prints) == 1 and print_label(prints[0]) == "ohne Set":
        return ""
    return " · ".join(f"{p['quantity']}× {print_label(p)}" for p in prints)


def remove_collection_card(db_path: str, card_id: int) -> None:
    """Entfernt alle Drucke einer Karte aus dem Bestand."""
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM collection WHERE card_id = ?", (card_id,))
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
