"""Netzwerk & Befuellung: der einzige Teil der Datenschicht, der online muss.

YGOPRODeck-Requests (Kartenliste englisch + deutsch, DB-Version), das
einmalige lokale Ablegen von Kartenbildern (kein Hotlinking!) und der
Aufbau/Refresh der Datenbank (build_database, needs_update). Importiert
nur .schema (Basis-Schicht).
"""

from __future__ import annotations

import html
import json
import os
import sqlite3
import threading
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

from .schema import DEFAULT_DB, IMAGE_DIR, SCHEMA, _conn, _migrate, local_db_version

API_BASE = "https://db.ygoprodeck.com/api/v7"
CARDINFO_URL = f"{API_BASE}/cardinfo.php"
DBVER_URL = f"{API_BASE}/checkDBVer.php"


def _http_get_json(url: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "yugioh-tool/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all_cards() -> list[dict]:
    """Holt die komplette Kartenliste in EINEM Request (kein Rate-Limit-Problem)."""
    payload = _http_get_json(CARDINFO_URL, timeout=120)
    return payload.get("data", [])


def fetch_all_cards_de() -> dict[int, dict]:
    """Holt die deutschen Kartendaten; Rueckgabe: dict {id -> Karten-dict}.
    Nicht alle Karten haben eine Uebersetzung -- fehlende bleiben einfach leer."""
    payload = _http_get_json(CARDINFO_URL + "?language=de", timeout=120)
    return {c["id"]: c for c in payload.get("data", [])}


def fetch_db_version() -> Optional[str]:
    """Aktuelle Datenbankversion der API -- billig, um auf Updates zu pruefen."""
    try:
        payload = _http_get_json(DBVER_URL)
        # Antwortform: [{"database_version": "...", "last_update": "..."}]
        if isinstance(payload, list) and payload:
            return str(payload[0].get("database_version"))
    except Exception:
        return None
    return None


def cache_image(card_id: int, image_url: str, image_dir: str = IMAGE_DIR) -> Path:
    """Laedt ein Kartenbild EINMAL herunter und legt es lokal ab (kein Hotlinking)."""
    Path(image_dir).mkdir(parents=True, exist_ok=True)
    dest = Path(image_dir) / f"{card_id}.jpg"
    if not dest.exists():
        req = urllib.request.Request(
            image_url, headers={"User-Agent": "yugioh-tool/0.1"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        # Atomar: erst .part, dann umbenennen -- ein Abbruch hinterlaesst
        # nie eine halbe Datei, die exists() dauerhaft fuer gueltig haelt.
        tmp = dest.with_name(
            f"{dest.name}.{os.getpid()}.{threading.get_ident()}.part"
        )
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    return dest


# ---------------------------------------------------------------------------
# Aufbau / Befuellung
# ---------------------------------------------------------------------------

# Unter diesem Anteil des lokalen Bestands gilt eine API-Antwort als kaputt
# (Wartung, Teilantwort) -- lieber abbrechen als Daten ueberschreiben.
_MIN_FETCH_RATIO = 0.5


def _check_fetch_plausible(db_path: str, cards: list, de_by_id: dict) -> None:
    """Bricht VOR jeder Schreib-Transaktion ab, wenn die API leer oder
    unplausibel klein antwortet. Sonst setzt der UPSERT alle deutschen
    Namen auf NULL bzw. DELETE FROM card_sets leert die Sets -- und das
    wuerde committet und als Erfolg gemeldet."""
    local = 0
    try:
        with _conn(db_path) as conn:
            local = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    except sqlite3.Error:
        pass  # frische DB ohne Tabelle
    if not cards or len(cards) < local * _MIN_FETCH_RATIO:
        raise RuntimeError(
            f"API lieferte nur {len(cards)} Karten (lokal {local}) -- "
            "Update abgebrochen, lokale Daten unveraendert."
        )
    if not de_by_id or len(de_by_id) < len(cards) * _MIN_FETCH_RATIO:
        raise RuntimeError(
            f"API lieferte nur {len(de_by_id)} deutsche Kartendaten -- "
            "Update abgebrochen, lokale Daten unveraendert."
        )


def _unescape(text: Optional[str]) -> Optional[str]:
    """Die API liefert vereinzelt HTML-Entities im Namen ('&amp;')."""
    return html.unescape(text) if text else text


def build_database(
    db_path: str = DEFAULT_DB,
    cards: Optional[Iterable[dict]] = None,
    db_version: Optional[str] = None,
) -> int:
    """
    Legt das Schema an und befuellt es.

    cards=None  -> Daten werden live von der API geholt.
    cards=[...] -> vorgegebene Daten (praktisch zum Testen / Offline-Beilegen).

    Rueckgabe: Anzahl importierter Karten.
    """
    if cards is None:
        cards = fetch_all_cards()
        if db_version is None:
            db_version = fetch_db_version()
        de_by_id = fetch_all_cards_de()
        _check_fetch_plausible(db_path, cards, de_by_id)
    else:
        de_by_id: dict[int, dict] = {}
    cards = list(cards)

    with _conn(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        # Karten per UPSERT aktualisieren statt zu loeschen -- sonst wuerden
        # Fremdschluessel aus collection/deck_cards beim Update brechen.
        conn.execute("DELETE FROM card_sets;")
        # FTS droppen und mit aktualisiertem Schema (inkl. DE-Spalten) neu anlegen.
        conn.execute("DROP TABLE IF EXISTS cards_fts;")
        conn.execute(
            "CREATE VIRTUAL TABLE cards_fts USING fts5("
            "name, description, name_de, desc_de, content='cards', content_rowid='id')"
        )

        for c in cards:
            de = de_by_id.get(c.get("id"), {})
            conn.execute(
                """INSERT INTO cards
                   (id, name, type, frame_type, description, atk, def,
                    level, race, attribute, archetype, scale, link_value,
                    link_markers, name_de, desc_de)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       name=excluded.name,
                       type=excluded.type,
                       frame_type=excluded.frame_type,
                       description=excluded.description,
                       atk=excluded.atk,
                       def=excluded.def,
                       level=excluded.level,
                       race=excluded.race,
                       attribute=excluded.attribute,
                       archetype=excluded.archetype,
                       scale=excluded.scale,
                       link_value=excluded.link_value,
                       link_markers=excluded.link_markers,
                       name_de=excluded.name_de,
                       desc_de=excluded.desc_de""",
                (
                    c.get("id"),
                    _unescape(c.get("name")),
                    c.get("type"),
                    c.get("frameType"),
                    c.get("desc"),
                    c.get("atk"),
                    c.get("def"),
                    c.get("level"),
                    c.get("race"),
                    c.get("attribute"),
                    c.get("archetype"),
                    c.get("scale"),
                    c.get("linkval"),
                    ",".join(c.get("linkmarkers") or []) or None,
                    _unescape(de.get("name")),
                    de.get("desc"),
                ),
            )
            for s in c.get("card_sets", []) or []:
                conn.execute(
                    """INSERT INTO card_sets (card_id, set_name, set_code, rarity)
                       VALUES (?,?,?,?)""",
                    (
                        c.get("id"),
                        s.get("set_name"),
                        s.get("set_code"),
                        s.get("set_rarity"),
                    ),
                )

        # Eigene Uebersetzungen wieder anwenden -- der UPSERT oben hat
        # name_de/desc_de mit den API-Werten ueberschrieben.
        conn.execute(
            """UPDATE cards SET
                 name_de = COALESCE((SELECT t.name_de FROM card_translations t
                                     WHERE t.card_id = cards.id), name_de),
                 desc_de = COALESCE((SELECT t.desc_de FROM card_translations t
                                     WHERE t.card_id = cards.id), desc_de)
               WHERE id IN (SELECT card_id FROM card_translations)"""
        )

        # FTS-Index aus den Stammdaten neu aufbauen (deutsch + englisch).
        conn.execute(
            "INSERT INTO cards_fts (rowid, name, description, name_de, desc_de) "
            "SELECT id, name, description, name_de, desc_de FROM cards;"
        )

        if db_version:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('db_version', ?);",
                (db_version,),
            )
        # Kartentexte koennen sich geaendert haben: Wortlaut-Merkmale beim
        # naechsten Bedarf neu berechnen (cards.ensure_wording_flags).
        conn.execute("DELETE FROM meta WHERE key = 'wording_version';")
        conn.commit()
        return len(cards)


def needs_update(db_path: str = DEFAULT_DB) -> bool:
    """True, wenn die API eine neuere DB-Version meldet als lokal gespeichert."""
    remote = fetch_db_version()
    if remote is None:
        return False
    return remote != local_db_version(db_path)
