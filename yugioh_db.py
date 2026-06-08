"""
yugioh_db.py
============
Selbststaendiges Fundament fuer eine Yu-Gi-Oh-Sammlungs- und Nachschlage-App.

Prinzip:
  - Einmalig die komplette Kartendatenbank von der YGOPRODeck-API ziehen.
  - Lokal in SQLite ablegen (inkl. Volltextsuche ueber FTS5).
  - Danach laeuft alles offline; Update nur bei neuer DB-Version.

Nur Standardbibliothek (urllib, json, sqlite3) -- keine Drittpakete.

Datenmodell (zwei klar getrennte Ebenen):
  cards / card_sets   -> Referenz: alle Karten, die es gibt (aus der API)
  collection          -> dein Bestand: was du tatsaechlich besitzt

Hinweis zu Bildern: Die API-Bilder NICHT dauerhaft hotlinken. Wer Bilder
anzeigen will, laedt sie einmal herunter und legt sie lokal ab
(siehe cache_image()), sonst droht eine IP-Sperre.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

API_BASE = "https://db.ygoprodeck.com/api/v7"
CARDINFO_URL = f"{API_BASE}/cardinfo.php"
DBVER_URL = f"{API_BASE}/checkDBVer.php"

DEFAULT_DB = "yugioh.sqlite3"
IMAGE_DIR = "card_images"


# ---------------------------------------------------------------------------
# Netzwerk (der einzige Teil, der online sein muss)
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "yugioh-tool/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all_cards() -> list[dict]:
    """Holt die komplette Kartenliste in EINEM Request (kein Rate-Limit-Problem)."""
    payload = _http_get_json(CARDINFO_URL)
    return payload.get("data", [])


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


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY,   -- passcode der Karte
    name        TEXT NOT NULL,
    type        TEXT,                  -- z.B. "Effect Monster", "Spell Card"
    frame_type  TEXT,                  -- z.B. "effect", "spell", "xyz"
    description TEXT,                   -- Kartentext
    atk         INTEGER,
    def         INTEGER,
    level       INTEGER,               -- Level / Rank
    race        TEXT,                  -- Typ-Linie bzw. Spell/Trap-Art
    attribute   TEXT,                  -- LIGHT, DARK, ...
    archetype   TEXT,
    scale       INTEGER,               -- Pendulum-Skala (sonst NULL)
    link_value  INTEGER                -- Link-Wert (sonst NULL)
);

CREATE INDEX IF NOT EXISTS idx_cards_type      ON cards(type);
CREATE INDEX IF NOT EXISTS idx_cards_attribute ON cards(attribute);
CREATE INDEX IF NOT EXISTS idx_cards_archetype ON cards(archetype);
CREATE INDEX IF NOT EXISTS idx_cards_level     ON cards(level);

-- Eine Karte erscheint in vielen Sets; daher eigene Tabelle.
CREATE TABLE IF NOT EXISTS card_sets (
    card_id   INTEGER NOT NULL REFERENCES cards(id),
    set_name  TEXT,
    set_code  TEXT,
    rarity    TEXT
);
CREATE INDEX IF NOT EXISTS idx_card_sets_card ON card_sets(card_id);

-- Volltextsuche ueber Name + Kartentext.
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
    name, description, content='cards', content_rowid='id'
);

-- DEIN Bestand: was du besitzt. Getrennt von der Referenz.
CREATE TABLE IF NOT EXISTS collection (
    entry_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id   INTEGER NOT NULL REFERENCES cards(id),
    quantity  INTEGER NOT NULL DEFAULT 1,
    set_code  TEXT,          -- welcher Druck (optional)
    edition   TEXT,          -- 1st Edition / Unlimited ...
    condition TEXT,          -- NM, LP, ...
    language  TEXT,
    notes     TEXT
);
CREATE INDEX IF NOT EXISTS idx_collection_card ON collection(card_id);

-- Decks und ihre Karten (Main/Extra/Side).
CREATE TABLE IF NOT EXISTS decks (
    deck_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deck_cards (
    deck_id   INTEGER NOT NULL REFERENCES decks(deck_id) ON DELETE CASCADE,
    card_id   INTEGER NOT NULL REFERENCES cards(id),
    zone      TEXT NOT NULL CHECK (zone IN ('main', 'extra', 'side')),
    quantity  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (deck_id, card_id, zone)
);
CREATE INDEX IF NOT EXISTS idx_deck_cards_deck ON deck_cards(deck_id);

-- Kombo-Bibliothek: eigene Guides aus Bausteinen (Karten) und Schritten.
CREATE TABLE IF NOT EXISTS combos (
    combo_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    archetype TEXT,
    notes     TEXT
);

CREATE TABLE IF NOT EXISTS combo_cards (
    combo_id  INTEGER NOT NULL REFERENCES combos(combo_id) ON DELETE CASCADE,
    card_id   INTEGER NOT NULL REFERENCES cards(id),
    quantity  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (combo_id, card_id)
);

CREATE TABLE IF NOT EXISTS combo_steps (
    combo_id  INTEGER NOT NULL REFERENCES combos(combo_id) ON DELETE CASCADE,
    step_no   INTEGER NOT NULL,
    text      TEXT NOT NULL,
    PRIMARY KEY (combo_id, step_no)
);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_schema(db_path: str = DEFAULT_DB) -> None:
    """Legt fehlende Tabellen an (idempotent). Auch fuer bestehende DBs,
    damit nachgeruestete Tabellen wie decks/deck_cards vorhanden sind."""
    conn = _connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Aufbau / Befuellung
# ---------------------------------------------------------------------------

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
    cards = list(cards)

    conn = _connect(db_path)
    try:
        conn.executescript(SCHEMA)
        # Karten per UPSERT aktualisieren statt zu loeschen -- sonst wuerden
        # Fremdschluessel aus collection/deck_cards beim Update brechen.
        conn.execute("DELETE FROM card_sets;")
        conn.execute("DELETE FROM cards_fts;")

        for c in cards:
            conn.execute(
                """INSERT INTO cards
                   (id, name, type, frame_type, description, atk, def,
                    level, race, attribute, archetype, scale, link_value)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                       link_value=excluded.link_value""",
                (
                    c.get("id"),
                    c.get("name"),
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

        # FTS-Index aus den Stammdaten neu aufbauen.
        conn.execute(
            "INSERT INTO cards_fts (rowid, name, description) "
            "SELECT id, name, description FROM cards;"
        )

        if db_version:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('db_version', ?);",
                (db_version,),
            )
        conn.commit()
        return len(cards)
    finally:
        conn.close()


def needs_update(db_path: str = DEFAULT_DB) -> bool:
    """True, wenn die API eine neuere DB-Version meldet als lokal gespeichert."""
    remote = fetch_db_version()
    if remote is None:
        return False
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='db_version';"
        ).fetchone()
        local = row["value"] if row else None
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
    return remote != local


# ---------------------------------------------------------------------------
# Suche / Filter (Nachschlagewerk)
# ---------------------------------------------------------------------------

def search_text(db_path: str, query: str, limit: int = 50) -> list[sqlite3.Row]:
    """Volltextsuche in Name und Kartentext."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            """SELECT c.* FROM cards_fts
               JOIN cards c ON c.id = cards_fts.rowid
               WHERE cards_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()
    finally:
        conn.close()


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
    conn = _connect(db_path)
    try:
        return conn.execute(
            f"SELECT * FROM cards {where} ORDER BY name LIMIT ?", params
        ).fetchall()
    finally:
        conn.close()


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
    conn = _connect(db_path)
    try:
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
    finally:
        conn.close()


def list_collection(db_path: str) -> list[sqlite3.Row]:
    """Alle Bestandseintraege mit Kartenname -- ein Eintrag (Druck) pro Zeile."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            """SELECT col.entry_id, col.card_id, c.name, c.type,
                      col.quantity, col.set_code, col.edition,
                      col.condition, col.language, col.notes
               FROM collection col
               JOIN cards c ON c.id = col.card_id
               ORDER BY c.name, col.entry_id"""
        ).fetchall()
    finally:
        conn.close()


def set_collection_quantity(db_path: str, entry_id: int, quantity: int) -> None:
    """Setzt die Menge eines Eintrags. quantity <= 0 entfernt den Eintrag."""
    conn = _connect(db_path)
    try:
        if quantity <= 0:
            conn.execute("DELETE FROM collection WHERE entry_id = ?", (entry_id,))
        else:
            conn.execute(
                "UPDATE collection SET quantity = ? WHERE entry_id = ?",
                (quantity, entry_id),
            )
        conn.commit()
    finally:
        conn.close()


def remove_collection_entry(db_path: str, entry_id: int) -> None:
    """Entfernt einen Bestandseintrag vollstaendig."""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM collection WHERE entry_id = ?", (entry_id,))
        conn.commit()
    finally:
        conn.close()


def collection_stats(db_path: str) -> tuple[int, int, int]:
    """(Anzahl Eintraege, verschiedene Karten, Karten gesamt)."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS entries,
                      COUNT(DISTINCT card_id) AS unique_cards,
                      COALESCE(SUM(quantity), 0) AS total
               FROM collection"""
        ).fetchone()
        return row["entries"], row["unique_cards"], row["total"]
    finally:
        conn.close()


def collection_overview(db_path: str) -> list[sqlite3.Row]:
    """Bestand mit Kartennamen und Gesamtmenge je Karte."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            """SELECT c.id, c.name, c.type, SUM(col.quantity) AS total
               FROM collection col
               JOIN cards c ON c.id = col.card_id
               GROUP BY c.id
               ORDER BY c.name"""
        ).fetchall()
    finally:
        conn.close()


def cache_image(card_id: int, image_url: str, image_dir: str = IMAGE_DIR) -> Path:
    """Laedt ein Kartenbild EINMAL herunter und legt es lokal ab (kein Hotlinking)."""
    Path(image_dir).mkdir(parents=True, exist_ok=True)
    dest = Path(image_dir) / f"{card_id}.jpg"
    if not dest.exists():
        req = urllib.request.Request(
            image_url, headers={"User-Agent": "yugioh-tool/0.1"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            dest.write_bytes(resp.read())
    return dest


# ---------------------------------------------------------------------------
# Deckbuilding
# ---------------------------------------------------------------------------

# Frame-Typen, die ins Extra Deck gehoeren.
EXTRA_DECK_FRAMES = {
    "fusion", "synchro", "xyz", "link",
    "synchro_pendulum", "xyz_pendulum", "fusion_pendulum",
}

MAIN_MIN, MAIN_MAX = 40, 60
EXTRA_MAX = SIDE_MAX = 15
MAX_COPIES = 3


def deck_zone_for(frame_type: Optional[str], card_type: str = "") -> str:
    """Natuerliche Zone einer Karte: 'extra' (Fusion/Synchro/Xyz/Link) sonst 'main'."""
    ft = (frame_type or "").lower()
    if ft in EXTRA_DECK_FRAMES:
        return "extra"
    if any(k in (card_type or "") for k in ("Fusion", "Synchro", "Xyz", "XYZ", "Link")):
        return "extra"
    return "main"


def create_deck(db_path: str, name: str) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute("INSERT INTO decks (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_decks(db_path: str) -> list[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT deck_id, name FROM decks ORDER BY name"
        ).fetchall()
    finally:
        conn.close()


def delete_deck(db_path: str, deck_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM decks WHERE deck_id = ?", (deck_id,))
        conn.commit()
    finally:
        conn.close()


def deck_cards(db_path: str, deck_id: int, zone: str) -> list[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        return conn.execute(
            """SELECT dc.card_id, c.name, c.type, c.frame_type, dc.quantity
               FROM deck_cards dc
               JOIN cards c ON c.id = dc.card_id
               WHERE dc.deck_id = ? AND dc.zone = ?
               ORDER BY c.name""",
            (deck_id, zone),
        ).fetchall()
    finally:
        conn.close()


def _total_copies(conn: sqlite3.Connection, deck_id: int, card_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS n FROM deck_cards "
        "WHERE deck_id = ? AND card_id = ?",
        (deck_id, card_id),
    ).fetchone()
    return int(row["n"])


def add_card_to_deck(
    db_path: str, deck_id: int, card_id: int,
    zone: Optional[str] = None, count: int = 1,
) -> tuple[int, str]:
    """Fuegt Kopien hinzu. zone=None -> automatische Zuordnung (main/extra).
    Beachtet die 3-Kopien-Regel und die Zonen-Logik.
    Rueckgabe: (tatsaechlich hinzugefuegt, Hinweistext)."""
    conn = _connect(db_path)
    try:
        card = conn.execute(
            "SELECT type, frame_type FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        if card is None:
            return (0, "Karte nicht gefunden.")
        natural = deck_zone_for(card["frame_type"], card["type"])
        if zone is None:
            zone = natural
        if zone not in ("main", "extra", "side"):
            return (0, "Unbekannte Zone.")
        if zone != "side" and zone != natural:
            target = "Extra Deck" if natural == "extra" else "Main Deck"
            return (0, f"Diese Karte gehoert ins {target}.")

        allowed = max(0, MAX_COPIES - _total_copies(conn, deck_id, card_id))
        add = min(count, allowed)
        if add <= 0:
            return (0, f"Maximal {MAX_COPIES} Kopien je Karte erreicht.")

        existing = conn.execute(
            "SELECT quantity FROM deck_cards "
            "WHERE deck_id = ? AND card_id = ? AND zone = ?",
            (deck_id, card_id, zone),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE deck_cards SET quantity = quantity + ? "
                "WHERE deck_id = ? AND card_id = ? AND zone = ?",
                (add, deck_id, card_id, zone),
            )
        else:
            conn.execute(
                "INSERT INTO deck_cards (deck_id, card_id, zone, quantity) "
                "VALUES (?,?,?,?)",
                (deck_id, card_id, zone, add),
            )
        conn.commit()
        msg = "" if add == count else f"Nur {add} hinzugefuegt ({MAX_COPIES}-Kopien-Grenze)."
        return (add, msg)
    finally:
        conn.close()


def change_deck_quantity(
    db_path: str, deck_id: int, card_id: int, zone: str, delta: int
) -> None:
    """Erhoeht/senkt die Menge in einer Zone. Auf 0 -> Eintrag entfernt.
    Erhoehung beachtet die 3-Kopien-Regel ueber alle Zonen."""
    conn = _connect(db_path)
    try:
        if delta > 0:
            allowed = MAX_COPIES - _total_copies(conn, deck_id, card_id)
            if allowed <= 0:
                return
            delta = min(delta, allowed)
        row = conn.execute(
            "SELECT quantity FROM deck_cards "
            "WHERE deck_id = ? AND card_id = ? AND zone = ?",
            (deck_id, card_id, zone),
        ).fetchone()
        if row is None:
            return
        new = row["quantity"] + delta
        if new <= 0:
            conn.execute(
                "DELETE FROM deck_cards "
                "WHERE deck_id = ? AND card_id = ? AND zone = ?",
                (deck_id, card_id, zone),
            )
        else:
            conn.execute(
                "UPDATE deck_cards SET quantity = ? "
                "WHERE deck_id = ? AND card_id = ? AND zone = ?",
                (new, deck_id, card_id, zone),
            )
        conn.commit()
    finally:
        conn.close()


def remove_deck_card(db_path: str, deck_id: int, card_id: int, zone: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "DELETE FROM deck_cards "
            "WHERE deck_id = ? AND card_id = ? AND zone = ?",
            (deck_id, card_id, zone),
        )
        conn.commit()
    finally:
        conn.close()


def move_deck_card(
    db_path: str, deck_id: int, card_id: int,
    from_zone: str, to_zone: str, count: int = 1,
) -> tuple[int, str]:
    """Verschiebt Kopien zwischen Zonen (z.B. Main<->Side). Prueft die Zonen-Logik.
    Die Gesamtzahl bleibt gleich, die 3-Kopien-Regel ist daher nicht betroffen."""
    conn = _connect(db_path)
    try:
        card = conn.execute(
            "SELECT type, frame_type FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        if card is None:
            return (0, "Karte nicht gefunden.")
        natural = deck_zone_for(card["frame_type"], card["type"])
        if to_zone != "side" and to_zone != natural:
            return (0, "Zielzone passt nicht zum Kartentyp.")
        src = conn.execute(
            "SELECT quantity FROM deck_cards "
            "WHERE deck_id = ? AND card_id = ? AND zone = ?",
            (deck_id, card_id, from_zone),
        ).fetchone()
        if src is None:
            return (0, "")
        move = min(count, src["quantity"])
        if src["quantity"] - move <= 0:
            conn.execute(
                "DELETE FROM deck_cards "
                "WHERE deck_id = ? AND card_id = ? AND zone = ?",
                (deck_id, card_id, from_zone),
            )
        else:
            conn.execute(
                "UPDATE deck_cards SET quantity = quantity - ? "
                "WHERE deck_id = ? AND card_id = ? AND zone = ?",
                (move, deck_id, card_id, from_zone),
            )
        dst = conn.execute(
            "SELECT quantity FROM deck_cards "
            "WHERE deck_id = ? AND card_id = ? AND zone = ?",
            (deck_id, card_id, to_zone),
        ).fetchone()
        if dst:
            conn.execute(
                "UPDATE deck_cards SET quantity = quantity + ? "
                "WHERE deck_id = ? AND card_id = ? AND zone = ?",
                (move, deck_id, card_id, to_zone),
            )
        else:
            conn.execute(
                "INSERT INTO deck_cards (deck_id, card_id, zone, quantity) "
                "VALUES (?,?,?,?)",
                (deck_id, card_id, to_zone, move),
            )
        conn.commit()
        return (move, "")
    finally:
        conn.close()


def deck_counts(db_path: str, deck_id: int) -> dict:
    """Kartenzahl je Zone als {'main': n, 'extra': n, 'side': n}."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT zone, COALESCE(SUM(quantity), 0) AS n FROM deck_cards "
            "WHERE deck_id = ? GROUP BY zone",
            (deck_id,),
        ).fetchall()
        counts = {"main": 0, "extra": 0, "side": 0}
        for r in rows:
            counts[r["zone"]] = int(r["n"])
        return counts
    finally:
        conn.close()


def validate_deck(db_path: str, deck_id: int) -> list[tuple[bool, str]]:
    """Prueft die offiziellen Grundregeln. Rueckgabe: Liste von (ok, Text)."""
    counts = deck_counts(db_path, deck_id)
    m, e, s = counts["main"], counts["extra"], counts["side"]
    checks = [
        (MAIN_MIN <= m <= MAIN_MAX, f"Main {m} ({MAIN_MIN}-{MAIN_MAX})"),
        (e <= EXTRA_MAX, f"Extra {e} (max. {EXTRA_MAX})"),
        (s <= SIDE_MAX, f"Side {s} (max. {SIDE_MAX})"),
    ]
    conn = _connect(db_path)
    try:
        over = conn.execute(
            """SELECT c.name, SUM(dc.quantity) AS n
               FROM deck_cards dc JOIN cards c ON c.id = dc.card_id
               WHERE dc.deck_id = ?
               GROUP BY dc.card_id HAVING n > ?""",
            (deck_id, MAX_COPIES),
        ).fetchall()
    finally:
        conn.close()
    for r in over:
        checks.append((False, f"{r['name']}: {r['n']} Kopien"))
    return checks


# ---------------------------------------------------------------------------
# Kombo-Bibliothek + Deckbuilding-Hilfe
# ---------------------------------------------------------------------------

MAX_COMBO_PIECE = 3  # mehr als 3 Kopien je Karte sind ohnehin nicht spielbar


def create_combo(db_path: str, name: str, archetype: Optional[str] = None) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO combos (name, archetype) VALUES (?,?)", (name, archetype)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_combos(db_path: str) -> list[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT combo_id, name, archetype FROM combos ORDER BY name"
        ).fetchall()
    finally:
        conn.close()


def get_combo(db_path: str, combo_id: int) -> Optional[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT combo_id, name, archetype, notes FROM combos WHERE combo_id = ?",
            (combo_id,),
        ).fetchone()
    finally:
        conn.close()


def update_combo(
    db_path: str, combo_id: int, name: str, archetype: Optional[str] = None
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE combos SET name = ?, archetype = ? WHERE combo_id = ?",
            (name, archetype, combo_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_combo(db_path: str, combo_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM combos WHERE combo_id = ?", (combo_id,))
        conn.commit()
    finally:
        conn.close()


def add_combo_card(db_path: str, combo_id: int, card_id: int, quantity: int = 1) -> None:
    conn = _connect(db_path)
    try:
        ex = conn.execute(
            "SELECT quantity FROM combo_cards WHERE combo_id = ? AND card_id = ?",
            (combo_id, card_id),
        ).fetchone()
        if ex:
            new = min(MAX_COMBO_PIECE, ex["quantity"] + quantity)
            conn.execute(
                "UPDATE combo_cards SET quantity = ? WHERE combo_id = ? AND card_id = ?",
                (new, combo_id, card_id),
            )
        else:
            conn.execute(
                "INSERT INTO combo_cards (combo_id, card_id, quantity) VALUES (?,?,?)",
                (combo_id, card_id, min(MAX_COMBO_PIECE, quantity)),
            )
        conn.commit()
    finally:
        conn.close()


def set_combo_card_quantity(
    db_path: str, combo_id: int, card_id: int, quantity: int
) -> None:
    conn = _connect(db_path)
    try:
        if quantity <= 0:
            conn.execute(
                "DELETE FROM combo_cards WHERE combo_id = ? AND card_id = ?",
                (combo_id, card_id),
            )
        else:
            conn.execute(
                "UPDATE combo_cards SET quantity = ? WHERE combo_id = ? AND card_id = ?",
                (min(MAX_COMBO_PIECE, quantity), combo_id, card_id),
            )
        conn.commit()
    finally:
        conn.close()


def remove_combo_card(db_path: str, combo_id: int, card_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "DELETE FROM combo_cards WHERE combo_id = ? AND card_id = ?",
            (combo_id, card_id),
        )
        conn.commit()
    finally:
        conn.close()


def combo_cards(db_path: str, combo_id: int) -> list[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        return conn.execute(
            """SELECT cc.card_id, c.name, c.type, c.frame_type, cc.quantity
               FROM combo_cards cc JOIN cards c ON c.id = cc.card_id
               WHERE cc.combo_id = ? ORDER BY c.name""",
            (combo_id,),
        ).fetchall()
    finally:
        conn.close()


def set_combo_steps(db_path: str, combo_id: int, steps: list[str]) -> None:
    """Ersetzt alle Schritte einer Kombo durch die uebergebene Liste."""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM combo_steps WHERE combo_id = ?", (combo_id,))
        for i, text in enumerate(steps, start=1):
            conn.execute(
                "INSERT INTO combo_steps (combo_id, step_no, text) VALUES (?,?,?)",
                (combo_id, i, text),
            )
        conn.commit()
    finally:
        conn.close()


def combo_steps(db_path: str, combo_id: int) -> list[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT step_no, text FROM combo_steps WHERE combo_id = ? ORDER BY step_no",
            (combo_id,),
        ).fetchall()
    finally:
        conn.close()


def combos_for_card(db_path: str, card_id: int) -> list[sqlite3.Row]:
    """Alle Kombos, die diese Karte als Baustein verwenden."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            """SELECT cb.combo_id, cb.name FROM combos cb
               JOIN combo_cards cc ON cc.combo_id = cb.combo_id
               WHERE cc.card_id = ? ORDER BY cb.name""",
            (card_id,),
        ).fetchall()
    finally:
        conn.close()


def combo_coverage(db_path: str, combo_id: int, deck_id: int) -> dict:
    """Abgleich einer Kombo mit einem Deck (Main+Extra).
    Rueckgabe: {'total', 'covered', 'pieces': [{card_id, name, type,
    frame_type, needed, have, missing}]}."""
    conn = _connect(db_path)
    try:
        pieces = conn.execute(
            """SELECT cc.card_id, c.name, c.type, c.frame_type,
                      cc.quantity AS needed,
                      COALESCE((SELECT SUM(dc.quantity) FROM deck_cards dc
                                WHERE dc.deck_id = ? AND dc.card_id = cc.card_id
                                  AND dc.zone IN ('main','extra')), 0) AS have
               FROM combo_cards cc JOIN cards c ON c.id = cc.card_id
               WHERE cc.combo_id = ? ORDER BY c.name""",
            (deck_id, combo_id),
        ).fetchall()
    finally:
        conn.close()
    result, covered = [], 0
    for p in pieces:
        missing = max(0, p["needed"] - p["have"])
        if missing == 0:
            covered += 1
        result.append({
            "card_id": p["card_id"], "name": p["name"], "type": p["type"],
            "frame_type": p["frame_type"], "needed": p["needed"],
            "have": p["have"], "missing": missing,
        })
    return {"total": len(result), "covered": covered, "pieces": result}


def combos_for_deck(db_path: str, deck_id: int) -> list[dict]:
    """Alle Kombos mit ihrer Abdeckung gegen das Deck, nach Abdeckung sortiert."""
    out = []
    for cb in list_combos(db_path):
        cov = combo_coverage(db_path, cb["combo_id"], deck_id)
        total = cov["total"]
        out.append({
            "combo_id": cb["combo_id"], "name": cb["name"],
            "archetype": cb["archetype"], "total": total,
            "covered": cov["covered"],
            "coverage": (cov["covered"] / total) if total else 0.0,
        })
    out.sort(key=lambda x: (-x["coverage"], x["name"]))
    return out


# ---------------------------------------------------------------------------
# CLI: erstes Befuellen / Update
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    db = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DB
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"

    if cmd == "build":
        print("Lade Kartendatenbank von der API ...")
        n = build_database(db)
        print(f"Fertig: {n} Karten in {db} importiert.")
    elif cmd == "check":
        print("Update verfuegbar." if needs_update(db) else "Datenbank ist aktuell.")
    else:
        print("Verwendung: python yugioh_db.py [build|check] [db_pfad]")
