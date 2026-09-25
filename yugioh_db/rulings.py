"""Eigene Rulings je Karte (Benutzerdaten) und Links zu offiziellen Quellen.

Rulings sind Notizen, die der Benutzer selbst pflegt (Text + Quelle) --
die App laedt keine Rulings aus dem Netz (kein Scraping). ruling_links
liefert nur Adressen, die die GUI im Browser oeffnet. Die Tabelle
card_rulings gehoert zu den Benutzerdaten und ueberlebt Daten-Updates, weil
build_database Karten nur per UPSERT aktualisiert. Importiert nur .schema.
"""

from __future__ import annotations

import datetime
import sqlite3
import urllib.parse
from typing import Iterable, Optional

from .schema import _conn


def list_card_rulings(db_path: str, card_id: int) -> list[sqlite3.Row]:
    """Eigene Rulings einer Karte, aelteste zuerst."""
    with _conn(db_path) as conn:
        return conn.execute(
            "SELECT ruling_id, card_id, text, source, created FROM card_rulings "
            "WHERE card_id = ? ORDER BY ruling_id",
            (card_id,),
        ).fetchall()


def _clean(text: Optional[str]) -> Optional[str]:
    return (text or "").strip() or None


def add_card_ruling(db_path: str, card_id: int, text: str,
                    source: Optional[str] = None) -> int:
    """Ruling anlegen (leerer Text ist ein Fehler); Rueckgabe: ruling_id."""
    text = _clean(text)
    if text is None:
        raise ValueError("Ein Ruling braucht einen Text.")
    with _conn(db_path) as conn:
        if conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone() is None:
            raise ValueError(f"Karte {card_id} nicht gefunden.")
        cur = conn.execute(
            "INSERT INTO card_rulings (card_id, text, source, created) VALUES (?,?,?,?)",
            (card_id, text, _clean(source), datetime.date.today().isoformat()),
        )
        conn.commit()
        return cur.lastrowid


def update_card_ruling(db_path: str, ruling_id: int, text: str,
                       source: Optional[str] = None) -> None:
    text = _clean(text)
    if text is None:
        raise ValueError("Ein Ruling braucht einen Text.")
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE card_rulings SET text = ?, source = ? WHERE ruling_id = ?",
            (text, _clean(source), ruling_id),
        )
        conn.commit()


def delete_card_ruling(db_path: str, ruling_id: int) -> None:
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM card_rulings WHERE ruling_id = ?", (ruling_id,))
        conn.commit()


def card_ruling_counts(db_path: str, card_ids: Iterable[int]) -> dict[int, int]:
    """{card_id: Anzahl eigener Rulings} -- Karten ohne Rulings fehlen."""
    ids = sorted({int(i) for i in card_ids})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with _conn(db_path) as conn:
        rows = conn.execute(
            f"SELECT card_id, COUNT(*) AS n FROM card_rulings "
            f"WHERE card_id IN ({marks}) GROUP BY card_id",
            ids,
        ).fetchall()
    return {r["card_id"]: r["n"] for r in rows}


_KONAMI_SEARCH = "https://www.db.yugioh-card.com/yugiohdb/card_search.action?"


def _konami(keyword: str, locale: str) -> str:
    # stype=1 = Suche im Kartennamen; die Datenbank sucht Namen in der
    # Sprache der Oberflaeche -- englischer Name nur mit locale 'en'.
    return _KONAMI_SEARCH + urllib.parse.urlencode(
        {"ope": 1, "sess": 1, "stype": 1, "keyword": keyword,
         "request_locale": locale})


def ruling_links(name_en: str, name_de: Optional[str] = None) -> list[tuple[str, str]]:
    """Offizielle bzw. etablierte Ruling-Quellen einer Karte als
    (Beschriftung, URL) -- nur Adressen, nichts wird abgerufen. Die
    Konami-Suche landet mit passendem Namen direkt auf der Karte (dort
    'FAQ'); deutsch nur, wenn ein deutscher Name bekannt ist."""
    name = (name_en or "").strip()
    links = []
    if (name_de or "").strip():
        links.append(("Konami-Datenbank (deutsch)", _konami(name_de.strip(), "de")))
    links.append(("Konami-Datenbank (englisch, mit FAQ)", _konami(name, "en")))
    links.append(("Yugipedia (Rulings-Sammlung)",
                  "https://yugipedia.com/wiki/Card_Rulings:"
                  + urllib.parse.quote(name.replace(" ", "_"), safe="_-.:!,()'")))
    return links
