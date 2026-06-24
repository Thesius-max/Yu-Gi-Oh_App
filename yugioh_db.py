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

import contextlib
import datetime
import itertools
import json
import math
import os
import random
import re
import shutil
import sqlite3
import sys
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

API_BASE = "https://db.ygoprodeck.com/api/v7"
CARDINFO_URL = f"{API_BASE}/cardinfo.php"
DBVER_URL = f"{API_BASE}/checkDBVer.php"

APP_DIR_NAME = "YugiohSammlung"

# App-Version: eine Nummer fuer alle Plattform-Builds (Windows + macOS).
# Release-Ritual: hochzaehlen -> committen -> Tag "v<version>" pushen; die CI
# baut dann alle drei ZIPs und haengt sie an EIN gemeinsames GitHub-Release.
APP_VERSION = "0.6.0"
RELEASES_API_URL = (
    "https://api.github.com/repos/Thesius-max/Yu-Gi-Oh_App/releases/latest"
)
RELEASES_PAGE_URL = "https://github.com/Thesius-max/Yu-Gi-Oh_App/releases/latest"


def _app_data_dir() -> Path:
    """Plattformueblicher, beschreibbarer Ort fuer Nutzerdaten (nur stdlib,
    damit diese Datei Qt-frei bleibt). Wird fuer den gepackten Build genutzt."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    d = Path(base) / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


# Im gepackten Zustand (PyInstaller setzt sys.frozen) liegen DB und Bilder im
# Nutzerordner -- das Bundle selbst ist u.U. schreibgeschuetzt/fluechtig.
# Im Entwicklungsbetrieb bleibt es beim relativen Pfad (eigene Test-DB).
if getattr(sys, "frozen", False):
    _DATA_DIR = _app_data_dir()
    DEFAULT_DB = str(_DATA_DIR / "yugioh.sqlite3")
    IMAGE_DIR = str(_DATA_DIR / "card_images")
else:
    DEFAULT_DB = "yugioh.sqlite3"
    IMAGE_DIR = "card_images"


def bundled_seed_path() -> Optional[Path]:
    """Pfad zur mitgelieferten Seed-Datenbank im PyInstaller-Bundle, sonst None."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        return None
    seed = Path(base) / "seed.sqlite3"
    return seed if seed.exists() else None


def ensure_user_db(db_path: str = DEFAULT_DB) -> bool:
    """Erststart im gepackten Build: mitgelieferte Seed-DB in den Nutzerordner
    kopieren, falls dort noch keine DB liegt. Rueckgabe: True, wenn danach eine
    DB existiert."""
    if os.path.exists(db_path):
        return True
    seed = bundled_seed_path()
    if seed is not None:
        shutil.copy(seed, db_path)
        return True
    return False


# ---------------------------------------------------------------------------
# Netzwerk (der einzige Teil, der online sein muss)
# ---------------------------------------------------------------------------

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


def _parse_version(version: str) -> tuple[int, ...]:
    """'v0.2.0' -> (0, 2, 0). Robust gegen Praefixe/Anhaengsel; ohne Ziffern
    ergibt sich (0,), damit kaputte Tags nie als 'neuer' gelten."""
    nums = re.findall(r"\d+", version)
    return tuple(int(n) for n in nums) if nums else (0,)


def check_app_update() -> Optional[dict]:
    """Fragt das neueste GitHub-Release ab (ein Mini-Request). Rueckgabe:
    {'version', 'url', 'notes'} wenn es neuer als APP_VERSION ist, sonst
    None. Netzfehler schlagen als Exception durch -- der Aufrufer
    entscheidet, ob das still bleibt (Startup) oder gemeldet wird (Menue)."""
    data = _http_get_json(RELEASES_API_URL, timeout=15)
    tag = data.get("tag_name") or ""
    if _parse_version(tag) <= _parse_version(APP_VERSION):
        return None
    return {
        "version": tag.lstrip("v"),
        "url": data.get("html_url") or RELEASES_PAGE_URL,
        "notes": (data.get("body") or "").strip(),
    }


def migration_backup(db_path: str = DEFAULT_DB) -> Optional[str]:
    """Sichert die Benutzer-DB beim ersten Start einer neuen App-Version --
    VOR ensure_schema, damit eine schiefgehende Migration nie der letzte
    Stand ist. Merkt sich die Version in meta ('app_version'); es bleibt nur
    die juengste Versions-Sicherung liegen. Rueckgabe: Pfad der Sicherung
    oder None, wenn keine noetig war."""
    if not os.path.exists(db_path):
        return None
    with _conn(db_path) as conn:
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='app_version';"
            ).fetchone()
            stored = row["value"] if row else None
        except sqlite3.OperationalError:
            stored = None  # sehr alte DB ohne meta-Tabelle
        if stored == APP_VERSION:
            return None
        # Frische DB ohne Benutzerdaten (typisch: Erststart mit Seed-DB):
        # nur die Version vermerken, keine sinnlose Kopie anlegen.
        has_user_data = False
        for table in ("collection", "decks", "combos", "card_translations"):
            try:
                if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    has_user_data = True
                    break
            except sqlite3.OperationalError:
                pass  # Tabelle existiert noch nicht -> dort auch keine Daten
        backup = None
        if has_user_data:
            backup = f"{db_path}.bak-v{stored or 'alt'}"
            shutil.copy2(db_path, backup)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);"
        )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('app_version', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
            (APP_VERSION,),
        )
        conn.commit()
    if backup is None:
        return None
    # Nur die frische Sicherung behalten -- die DB ist zu gross, um je
    # Version eine Kopie anzusammeln.
    prefix = os.path.basename(db_path) + ".bak-v"
    folder = os.path.dirname(backup) or "."
    for name in os.listdir(folder):
        if name.startswith(prefix) and os.path.join(folder, name) != backup:
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass  # liegengebliebene Sicherung ist kein Starthindernis
    return backup


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
    name_de     TEXT,                  -- deutscher Name (NULL wenn keine Übersetzung)
    type        TEXT,                  -- z.B. "Effect Monster", "Spell Card"
    frame_type  TEXT,                  -- z.B. "effect", "spell", "xyz"
    description TEXT,                   -- Kartentext (englisch)
    desc_de     TEXT,                  -- Kartentext (deutsch, NULL wenn keine Übersetzung)
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

-- Volltextsuche ueber Name + Kartentext (deutsch + englisch).
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
    name, description, name_de, desc_de, content='cards', content_rowid='id'
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

-- Eigene DE-Uebersetzungen fuer Karten, bei denen die API keine (oder eine
-- falsche) liefert. Benutzerdaten: build_database wendet sie nach jedem
-- Karten-UPSERT wieder auf cards an, damit sie Updates ueberleben.
CREATE TABLE IF NOT EXISTS card_translations (
    card_id  INTEGER PRIMARY KEY REFERENCES cards(id),
    name_de  TEXT,
    desc_de  TEXT
);

-- Decks und ihre Karten (Main/Extra/Side).
-- kind='reference' markiert importierte Meta-Listen (Korpus fuer den
-- Synergie-Graphen); NULL = eigenes Deck. source = Herkunft (Turnier/URL/
-- Spieler), format_date = Stand der Liste (ISO yyyy-mm-dd, fuer die
-- Alterung der Co-Occurrence-Gewichte). Referenz-Decks binden keinen
-- Bestand und erscheinen nicht in der normalen Deck-Auswahl.
CREATE TABLE IF NOT EXISTS decks (
    deck_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT,
    source      TEXT,
    format_date TEXT
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
    combo_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    archetype    TEXT,
    notes        TEXT,
    boss_card_id INTEGER REFERENCES cards(id),  -- Zielmonster der Kombo (optional)
    -- Heimat-Deck: nur eine Verknuepfung (Filter/Komfort), KEIN Besitz --
    -- jede Kombo bleibt gegen jedes Deck abgleichbar (Bibliothek-Prinzip).
    deck_id      INTEGER REFERENCES decks(deck_id) ON DELETE SET NULL,
    -- Variante/Verzweigung: zeigt auf die Hauptlinie (Interruption-Branch).
    -- NULL = eigenstaendige Hauptlinie. Struktur bewusst zweistufig
    -- (Hauptlinie -> Varianten). Nur Hauptlinien zaehlen in Abdeckung,
    -- Vorschlaegen, Synergie und Fahrplan; Varianten sind Dokumentation.
    -- ON DELETE CASCADE: mit der Hauptlinie verschwinden ihre Branches.
    parent_combo_id INTEGER REFERENCES combos(combo_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS combo_cards (
    combo_id  INTEGER NOT NULL REFERENCES combos(combo_id) ON DELETE CASCADE,
    card_id   INTEGER NOT NULL REFERENCES cards(id),
    quantity  INTEGER NOT NULL DEFAULT 1,
    role      TEXT,   -- starter | extender | payoff | handtrap (NULL = uneingestuft)
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


@contextlib.contextmanager
def _conn(db_path: str):
    """Verbindung als Kontextmanager: schliesst zuverlaessig, auch bei
    Fehlern. Ersetzt das wiederkehrende try/finally-Geruest -- das explizite
    conn.commit() bleibt in den schreibenden Funktionen stehen (klare
    Trennung lesend/schreibend, unveraenderte Semantik: ohne commit gehen
    Aenderungen beim Schliessen verloren)."""
    conn = _connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Ruestet Spalten nach, die juenger sind als die Tabelle selbst
    (CREATE TABLE IF NOT EXISTS ergaenzt keine Spalten). Idempotent."""
    for table, col, decl in (
        ("cards", "name_de", "TEXT"),
        ("cards", "desc_de", "TEXT"),
        ("combos", "boss_card_id", "INTEGER REFERENCES cards(id)"),
        ("combos", "deck_id", "INTEGER REFERENCES decks(deck_id) ON DELETE SET NULL"),
        ("combos", "parent_combo_id",
         "INTEGER REFERENCES combos(combo_id) ON DELETE CASCADE"),
        ("combo_cards", "role", "TEXT"),
        ("decks", "kind", "TEXT"),
        ("decks", "source", "TEXT"),
        ("decks", "format_date", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass  # Spalte existiert bereits


def ensure_schema(db_path: str = DEFAULT_DB) -> None:
    """Legt fehlende Tabellen an (idempotent). Auch fuer bestehende DBs,
    damit nachgeruestete Tabellen wie decks/deck_cards vorhanden sind."""
    with _conn(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
        _migrate(conn)
        conn.commit()


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
        de_by_id = fetch_all_cards_de()
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
                    name_de, desc_de)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                       name_de=excluded.name_de,
                       desc_de=excluded.desc_de""",
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
                    de.get("name"),
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
        conn.commit()
        return len(cards)


def local_db_version(db_path: str = DEFAULT_DB) -> Optional[str]:
    """Lokal gespeicherte Datenbankversion (None, wenn unbekannt)."""
    with _conn(db_path) as conn:
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='db_version';"
            ).fetchone()
            return row["value"] if row else None
        except sqlite3.OperationalError:
            return None


def needs_update(db_path: str = DEFAULT_DB) -> bool:
    """True, wenn die API eine neuere DB-Version meldet als lokal gespeichert."""
    remote = fetch_db_version()
    if remote is None:
        return False
    return remote != local_db_version(db_path)


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
) -> list[sqlite3.Row]:
    """Bestandseintraege mit Kartenname -- ein Eintrag (Druck) pro Zeile.
    Optional gefiltert: text (Namenssuche de/en), category (Wert von
    card_category), attribute, archetype."""
    sql = """SELECT col.entry_id, col.card_id, c.name, c.name_de, c.type,
                    c.attribute, c.archetype,
                    col.quantity, col.set_code, col.edition,
                    col.condition, col.language, col.notes
             FROM collection col
             JOIN cards c ON c.id = col.card_id"""
    where, args = [], []
    if text and text.strip():
        like = f"%{text.strip()}%"
        where.append("(c.name LIKE ? OR c.name_de LIKE ?)")
        args += [like, like]
    if attribute:
        where.append("c.attribute = ?")
        args.append(attribute)
    if archetype:
        where.append("c.archetype = ?")
        args.append(archetype)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(c.name_de, c.name), col.entry_id"
    with _conn(db_path) as conn:
        rows = conn.execute(sql, args).fetchall()
    # Kartenklasse in Python filtern -- exakt dieselbe Logik wie die
    # Gruppierung (card_category), keine zweite LIKE-Naeherung in SQL.
    if category:
        rows = [r for r in rows if card_category(r["type"]) == category]
    return rows


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


def collection_overview(db_path: str) -> list[sqlite3.Row]:
    """Bestand mit Kartennamen und Gesamtmenge je Karte."""
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT c.id, c.name, c.type, SUM(col.quantity) AS total
               FROM collection col
               JOIN cards c ON c.id = col.card_id
               GROUP BY c.id
               ORDER BY c.name"""
        ).fetchall()


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


def create_deck(
    db_path: str, name: str, kind: Optional[str] = None,
    source: Optional[str] = None, format_date: Optional[str] = None,
) -> int:
    """kind='reference' legt ein Referenz-Deck (Korpus) an; Standard ist
    ein eigenes Deck (kind NULL)."""
    with _conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO decks (name, kind, source, format_date) "
            "VALUES (?,?,?,?)",
            (name, kind, source, format_date),
        )
        conn.commit()
        return cur.lastrowid


def list_decks(db_path: str) -> list[sqlite3.Row]:
    """Nur eigene Decks -- Referenz-Decks (Korpus) bleiben bewusst draussen,
    damit Deck-Auswahl, Heimat-Deck und Filter sie nie anbieten."""
    with _conn(db_path) as conn:
        return conn.execute(
            "SELECT deck_id, name FROM decks WHERE kind IS NULL ORDER BY name"
        ).fetchall()


def list_reference_decks(db_path: str) -> list[sqlite3.Row]:
    """Der Korpus: importierte Meta-Listen mit Quelle, Stand und Kartenzahl,
    neueste zuerst (Listen ohne Datum zuletzt)."""
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT d.deck_id, d.name, d.source, d.format_date,
                      COALESCE((SELECT SUM(dc.quantity) FROM deck_cards dc
                                WHERE dc.deck_id = d.deck_id), 0) AS cards
               FROM decks d WHERE d.kind = 'reference'
               ORDER BY d.format_date IS NULL, d.format_date DESC, d.name"""
        ).fetchall()


def delete_deck(db_path: str, deck_id: int) -> None:
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM decks WHERE deck_id = ?", (deck_id,))
        conn.commit()


def deck_cards(db_path: str, deck_id: int, zone: str) -> list[sqlite3.Row]:
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT dc.card_id, c.name, c.type, c.frame_type, dc.quantity
               FROM deck_cards dc
               JOIN cards c ON c.id = dc.card_id
               WHERE dc.deck_id = ? AND dc.zone = ?
               ORDER BY c.name""",
            (deck_id, zone),
        ).fetchall()


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
    with _conn(db_path) as conn:
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
            return (0, f"Diese Karte gehört ins {target}.")

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
        msg = "" if add == count else f"Nur {add} hinzugefügt ({MAX_COPIES}-Kopien-Grenze)."
        return (add, msg)


def change_deck_quantity(
    db_path: str, deck_id: int, card_id: int, zone: str, delta: int
) -> None:
    """Erhoeht/senkt die Menge in einer Zone. Auf 0 -> Eintrag entfernt.
    Erhoehung beachtet die 3-Kopien-Regel ueber alle Zonen."""
    with _conn(db_path) as conn:
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


def remove_deck_card(db_path: str, deck_id: int, card_id: int, zone: str) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            "DELETE FROM deck_cards "
            "WHERE deck_id = ? AND card_id = ? AND zone = ?",
            (deck_id, card_id, zone),
        )
        conn.commit()


def move_deck_card(
    db_path: str, deck_id: int, card_id: int,
    from_zone: str, to_zone: str, count: int = 1,
) -> tuple[int, str]:
    """Verschiebt Kopien zwischen Zonen (z.B. Main<->Side). Prueft die Zonen-Logik.
    Die Gesamtzahl bleibt gleich, die 3-Kopien-Regel ist daher nicht betroffen."""
    with _conn(db_path) as conn:
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


def deck_availability(db_path: str, deck_id: int) -> list[dict]:
    """Sammlung<->Deck-Abgleich fuer ein Deck. Karten liegen physisch vor:
    Kopien, die in anderen Decks stecken, stehen diesem Deck nicht zur
    Verfuegung. Je Karte im Deck (alle Zonen):
      in_deck   -- Kopien in diesem Deck
      owned     -- Kopien im Bestand (alle Drucke zusammen)
      elsewhere -- Kopien, die andere Decks binden
      missing   -- fuer dieses Deck fehlende Kopien
    Nur Hinweis-Charakter: das Deckbuilding wird nicht blockiert (geplante
    Kaeufe / Proxies bleiben moeglich)."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT dc.card_id, c.name, c.name_de,
                      SUM(dc.quantity) AS in_deck,
                      COALESCE((SELECT SUM(col.quantity) FROM collection col
                                WHERE col.card_id = dc.card_id), 0) AS owned,
                      COALESCE((SELECT SUM(o.quantity) FROM deck_cards o
                                JOIN decks od ON od.deck_id = o.deck_id
                                WHERE o.card_id = dc.card_id
                                  AND o.deck_id != dc.deck_id
                                  AND od.kind IS NULL), 0) AS elsewhere
               FROM deck_cards dc JOIN cards c ON c.id = dc.card_id
               WHERE dc.deck_id = ?
               GROUP BY dc.card_id
               ORDER BY COALESCE(c.name_de, c.name)""",
            (deck_id,),
        ).fetchall()
    out = []
    for r in rows:
        available = max(0, r["owned"] - r["elsewhere"])
        out.append({
            "card_id": r["card_id"],
            "name": r["name_de"] or r["name"],
            "in_deck": r["in_deck"],
            "owned": r["owned"],
            "elsewhere": r["elsewhere"],
            "missing": max(0, r["in_deck"] - available),
        })
    return out


def card_bound_in_decks(db_path: str, card_id: int) -> int:
    """Wie viele Kopien einer Karte ueber alle EIGENEN Decks zusammen
    verplant sind (Referenz-Decks binden keinen physischen Bestand)."""
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(dc.quantity), 0) AS n FROM deck_cards dc "
            "JOIN decks d ON d.deck_id = dc.deck_id "
            "WHERE dc.card_id = ? AND d.kind IS NULL",
            (card_id,),
        ).fetchone()
        return int(row["n"])


def deck_counts(db_path: str, deck_id: int) -> dict:
    """Kartenzahl je Zone als {'main': n, 'extra': n, 'side': n}."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT zone, COALESCE(SUM(quantity), 0) AS n FROM deck_cards "
            "WHERE deck_id = ? GROUP BY zone",
            (deck_id,),
        ).fetchall()
        counts = {"main": 0, "extra": 0, "side": 0}
        for r in rows:
            counts[r["zone"]] = int(r["n"])
        return counts


def validate_deck(db_path: str, deck_id: int) -> list[tuple[bool, str]]:
    """Prueft die offiziellen Grundregeln. Rueckgabe: Liste von (ok, Text)."""
    counts = deck_counts(db_path, deck_id)
    m, e, s = counts["main"], counts["extra"], counts["side"]
    checks = [
        (MAIN_MIN <= m <= MAIN_MAX, f"Main {m} ({MAIN_MIN}-{MAIN_MAX})"),
        (e <= EXTRA_MAX, f"Extra {e} (max. {EXTRA_MAX})"),
        (s <= SIDE_MAX, f"Side {s} (max. {SIDE_MAX})"),
    ]
    with _conn(db_path) as conn:
        over = conn.execute(
            """SELECT c.name, SUM(dc.quantity) AS n
               FROM deck_cards dc JOIN cards c ON c.id = dc.card_id
               WHERE dc.deck_id = ?
               GROUP BY dc.card_id HAVING n > ?""",
            (deck_id, MAX_COPIES),
        ).fetchall()
    for r in over:
        checks.append((False, f"{r['name']}: {r['n']} Kopien"))
    return checks


# ---------------------------------------------------------------------------
# Deck-Import/-Export (.ydk)
# ---------------------------------------------------------------------------
# .ydk ist das YGOPro-Format: je Zeile ein Passcode, Abschnitte "#main",
# "#extra" und "!side"; weitere "#..."-Zeilen sind Kommentare. Die Passcodes
# sind identisch mit unseren Karten-IDs, daher braucht es kein Mapping.

def export_deck_ydk(db_path: str, deck_id: int) -> str:
    """Serialisiert ein Deck als .ydk-Text (je Kopie eine Zeile)."""
    with _conn(db_path) as conn:
        deck = conn.execute(
            "SELECT name FROM decks WHERE deck_id = ?", (deck_id,)
        ).fetchone()
        if deck is None:
            raise ValueError(f"Deck {deck_id} nicht gefunden.")
        lines = [f"#created by YugiohSammlung - {deck['name']}"]
        for zone, header in (("main", "#main"), ("extra", "#extra"), ("side", "!side")):
            lines.append(header)
            rows = conn.execute(
                """SELECT dc.card_id, dc.quantity
                   FROM deck_cards dc JOIN cards c ON c.id = dc.card_id
                   WHERE dc.deck_id = ? AND dc.zone = ?
                   ORDER BY c.name""",
                (deck_id, zone),
            ).fetchall()
            for r in rows:
                lines.extend([str(r["card_id"])] * r["quantity"])
        return "\n".join(lines) + "\n"


def parse_ydk(text: str) -> dict[str, list[int]]:
    """Zerlegt .ydk-Text in {'main': [ids...], 'extra': [...], 'side': [...]}.
    Jede Kopie steht einzeln in der Liste. Unbekannte Zeilen werden ignoriert."""
    zones: dict[str, list[int]] = {"main": [], "extra": [], "side": []}
    current = "main"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        if lower in ("#main", "#extra"):
            current = lower[1:]
        elif lower == "!side":
            current = "side"
        elif line.startswith(("#", "!")):
            continue  # Kommentar (z.B. "#created by ...")
        elif line.isdigit():
            zones[current].append(int(line))
    return zones


def import_deck_ydk(
    db_path: str, name: str, text: str, kind: Optional[str] = None,
    source: Optional[str] = None, format_date: Optional[str] = None,
) -> tuple[Optional[int], dict]:
    """Legt aus .ydk-Text ein neues Deck an. Erzwingt die 3-Kopien-Regel und
    korrigiert Main/Extra anhand des Kartentyps (Side bleibt Side).
    kind='reference' (plus source/format_date) importiert in den Korpus
    statt in die eigenen Decks.

    Rueckgabe: (deck_id, report). deck_id ist None, wenn keine einzige Karte
    importiert werden konnte (dann wird auch kein Deck angelegt). report:
      imported  -- importierte Kopien je Zone {'main': n, ...}
      unknown   -- Passcodes ohne Karte in der DB (DB veraltet/fremde Karte)
      capped    -- Kartennamen, bei denen Kopien ueber der 3er-Grenze wegfielen
      moved     -- Kartennamen, die in die zum Typ passende Zone wandern mussten
    """
    zones = parse_ydk(text)
    report: dict = {
        "imported": {"main": 0, "extra": 0, "side": 0},
        "unknown": [], "capped": [], "moved": [],
    }
    with _conn(db_path) as conn:
        # Kopien je (zone, card_id) zaehlen; Zone ggf. korrigieren.
        counts: dict[tuple[str, int], int] = {}
        cards: dict[int, sqlite3.Row] = {}
        for zone, ids in zones.items():
            for cid in ids:
                if cid not in cards:
                    row = conn.execute(
                        "SELECT name, type, frame_type FROM cards WHERE id = ?",
                        (cid,),
                    ).fetchone()
                    cards[cid] = row
                card = cards[cid]
                if card is None:
                    if cid not in report["unknown"]:
                        report["unknown"].append(cid)
                    continue
                target = zone
                if zone != "side":
                    natural = deck_zone_for(card["frame_type"], card["type"])
                    if natural != zone:
                        target = natural
                        if card["name"] not in report["moved"]:
                            report["moved"].append(card["name"])
                counts[(target, cid)] = counts.get((target, cid), 0) + 1

        # 3-Kopien-Grenze ueber alle Zonen zusammen durchsetzen.
        totals: dict[int, int] = {}
        for (zone, cid), n in sorted(counts.items()):
            have = totals.get(cid, 0)
            keep = min(n, max(0, MAX_COPIES - have))
            totals[cid] = have + keep
            if keep < n and cards[cid]["name"] not in report["capped"]:
                report["capped"].append(cards[cid]["name"])
            counts[(zone, cid)] = keep

        if not any(n > 0 for n in counts.values()):
            return (None, report)

        cur = conn.execute(
            "INSERT INTO decks (name, kind, source, format_date) "
            "VALUES (?,?,?,?)",
            (name, kind, source, format_date),
        )
        deck_id = cur.lastrowid
        for (zone, cid), n in counts.items():
            if n <= 0:
                continue
            conn.execute(
                "INSERT INTO deck_cards (deck_id, card_id, zone, quantity) "
                "VALUES (?,?,?,?)",
                (deck_id, cid, zone, n),
            )
            report["imported"][zone] += n
        conn.commit()
        return (deck_id, report)


# ---------------------------------------------------------------------------
# Kombo-Bibliothek + Deckbuilding-Hilfe
# ---------------------------------------------------------------------------

MAX_COMBO_PIECE = 3  # mehr als 3 Kopien je Karte sind ohnehin nicht spielbar

# Rolle eines Bausteins INNERHALB einer Kombo (dieselbe Karte kann anderswo
# eine andere Rolle haben). NULL = noch nicht eingestuft.
COMBO_ROLES = ("starter", "extender", "payoff", "handtrap")

# Verbindliche Notation fuer Kombo-Schritte (siehe KOMBO-NOTATION.md):
#   <AKTION> <Karte> (<Quelle>) [Req: <Bedingung>] -> <Folge> | Lock: <Lock>
# Erlaubte Aktions-Keywords am Zeilenanfang:
COMBO_STEP_KEYWORDS = (
    "NS", "SS", "Act", "Eff", "Eff1", "Eff2", "Add", "Send", "Banish",
    "Mill", "Draw", "Discard", "Set", "Synchro:",
)


def lint_combo_steps(steps: Iterable[str]) -> list[str]:
    """Prueft Schritte gegen die Kombo-Notation und liefert Warnungen
    (leer = konform). Bewusst tolerant und nur beratend -- die App warnt,
    blockiert aber nie."""
    allowed = {k.rstrip(":").lower() for k in COMBO_STEP_KEYWORDS}
    warnings: list[str] = []
    for no, text in enumerate(steps, start=1):
        problems: list[str] = []
        tokens = text.split()
        first = tokens[0].rstrip(":").lower() if tokens else ""
        if first not in allowed:
            problems.append(
                "beginnt nicht mit einem Notation-Keyword "
                "(NS, SS, Act, Eff/Eff1/Eff2, Add, Send, Banish, Mill, "
                "Draw, Discard, Set, Synchro:)"
            )
        head, *locks = (seg.strip() for seg in text.split("|"))
        for seg in locks:
            if not seg.lower().startswith("lock:"):
                problems.append("nach '|' fehlt das 'Lock:'-Praefix")
        if text.count("[") != text.count("]"):
            problems.append("eckige Klammer nicht geschlossen")
        else:
            for inner in re.findall(r"\[([^\]]*)\]", text):
                if not inner.strip().lower().startswith("req:"):
                    problems.append(
                        "eckige Klammern sind fuer Bedingungen "
                        "reserviert: [Req: ...]"
                    )
        lowered = head.lower()
        if lowered.startswith("synchro:"):
            if "+" not in head or "->" not in head:
                problems.append(
                    "Formel unvollstaendig -- erwartet: "
                    "Synchro: Tuner (Lvl) + Non-Tuner (Lvl) -> Ziel (Lvl)"
                )
        elif "synchro:" in lowered:
            problems.append(
                "Beschwoerungsformeln ('Synchro: ...') bekommen eine "
                "eigene Zeile"
            )
        warnings.extend(f"Schritt {no}: {p}" for p in problems)
    return warnings


def create_combo(
    db_path: str, name: str, archetype: Optional[str] = None,
    deck_id: Optional[int] = None,
) -> int:
    with _conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO combos (name, archetype, deck_id) VALUES (?,?,?)",
            (name, archetype, deck_id),
        )
        conn.commit()
        return cur.lastrowid


def list_combos(db_path: str, deck_id: Optional[int] = None) -> list[sqlite3.Row]:
    """Hauptlinien (parent_combo_id IS NULL), optional nach Heimat-Deck
    gefiltert: deck_id=None -> alle, deck_id=0 -> nur ohne Heimat-Deck, sonst
    das Deck. Varianten haengen an ihrer Hauptlinie (siehe combo_variants) und
    erscheinen hier bewusst nicht direkt."""
    sql = """SELECT cb.combo_id, cb.name, cb.archetype, cb.deck_id,
                    d.name AS deck_name
             FROM combos cb LEFT JOIN decks d ON d.deck_id = cb.deck_id
             WHERE cb.parent_combo_id IS NULL"""
    args: tuple = ()
    if deck_id == 0:
        sql += " AND cb.deck_id IS NULL"
    elif deck_id is not None:
        sql += " AND cb.deck_id = ?"
        args = (deck_id,)
    sql += " ORDER BY cb.name"
    with _conn(db_path) as conn:
        return conn.execute(sql, args).fetchall()


def get_combo(db_path: str, combo_id: int) -> Optional[sqlite3.Row]:
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT cb.combo_id, cb.name, cb.archetype, cb.notes,
                      cb.boss_card_id, b.name AS boss_name,
                      cb.deck_id, d.name AS deck_name,
                      cb.parent_combo_id, p.name AS parent_name
               FROM combos cb
               LEFT JOIN cards b ON b.id = cb.boss_card_id
               LEFT JOIN decks d ON d.deck_id = cb.deck_id
               LEFT JOIN combos p ON p.combo_id = cb.parent_combo_id
               WHERE cb.combo_id = ?""",
            (combo_id,),
        ).fetchone()


def set_combo_deck(db_path: str, combo_id: int, deck_id: Optional[int]) -> None:
    """Setzt das Heimat-Deck einer Kombo; None entfernt die Verknuepfung."""
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE combos SET deck_id = ? WHERE combo_id = ?",
            (deck_id, combo_id),
        )
        conn.commit()


def set_combo_parent(
    db_path: str, combo_id: int, parent_id: Optional[int]
) -> None:
    """Macht combo_id zur Variante (Branch) von parent_id; parent_id=None
    loest die Verknuepfung (wird wieder Hauptlinie). Haelt die Struktur
    bewusst ZWEISTUFIG: die Hauptlinie muss selbst eine Hauptlinie sein, und
    eine Kombo mit eigenen Varianten kann nicht selbst Variante werden.
    Dadurch sind Selbst-/Zyklus-Verknuepfungen ausgeschlossen.
    Wirft ValueError, wenn die Regeln verletzt wuerden."""
    if parent_id is not None and parent_id == combo_id:
        raise ValueError("Eine Kombo kann keine Variante ihrer selbst sein.")
    with _conn(db_path) as conn:
        if parent_id is not None:
            prow = conn.execute(
                "SELECT parent_combo_id FROM combos WHERE combo_id = ?",
                (parent_id,),
            ).fetchone()
            if prow is None:
                raise ValueError("Hauptlinie nicht gefunden.")
            if prow["parent_combo_id"] is not None:
                raise ValueError(
                    "Die gewaehlte Kombo ist selbst eine Variante."
                )
            if conn.execute(
                "SELECT 1 FROM combos WHERE parent_combo_id = ? LIMIT 1",
                (combo_id,),
            ).fetchone():
                raise ValueError(
                    "Diese Kombo hat eigene Varianten und kann nicht selbst "
                    "Variante werden."
                )
        conn.execute(
            "UPDATE combos SET parent_combo_id = ? WHERE combo_id = ?",
            (parent_id, combo_id),
        )
        conn.commit()


def combo_variants(db_path: str, combo_id: int) -> list[sqlite3.Row]:
    """Varianten (Branches) einer Hauptlinie, nach Name."""
    with _conn(db_path) as conn:
        return conn.execute(
            "SELECT combo_id, name, archetype FROM combos "
            "WHERE parent_combo_id = ? ORDER BY name",
            (combo_id,),
        ).fetchall()


def update_combo(
    db_path: str, combo_id: int, name: str, archetype: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE combos SET name = ?, archetype = ?, notes = ?"
            " WHERE combo_id = ?",
            (name, archetype, notes, combo_id),
        )
        conn.commit()


def set_combo_boss(db_path: str, combo_id: int, card_id: Optional[int]) -> None:
    """Setzt das Zielmonster (Boss) einer Kombo; None entfernt es."""
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE combos SET boss_card_id = ? WHERE combo_id = ?",
            (card_id, combo_id),
        )
        conn.commit()


def delete_combo(db_path: str, combo_id: int) -> None:
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM combos WHERE combo_id = ?", (combo_id,))
        conn.commit()


def add_combo_card(db_path: str, combo_id: int, card_id: int, quantity: int = 1) -> None:
    with _conn(db_path) as conn:
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


def set_combo_card_quantity(
    db_path: str, combo_id: int, card_id: int, quantity: int
) -> None:
    with _conn(db_path) as conn:
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


def set_combo_card_role(
    db_path: str, combo_id: int, card_id: int, role: Optional[str]
) -> None:
    """Setzt die Rolle eines Bausteins (siehe COMBO_ROLES); None loescht sie."""
    if role is not None and role not in COMBO_ROLES:
        raise ValueError(f"Unbekannte Rolle: {role}")
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE combo_cards SET role = ? WHERE combo_id = ? AND card_id = ?",
            (role, combo_id, card_id),
        )
        conn.commit()


def remove_combo_card(db_path: str, combo_id: int, card_id: int) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            "DELETE FROM combo_cards WHERE combo_id = ? AND card_id = ?",
            (combo_id, card_id),
        )
        conn.commit()


def combo_cards(db_path: str, combo_id: int) -> list[sqlite3.Row]:
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT cc.card_id, c.name, c.type, c.frame_type,
                      cc.quantity, cc.role
               FROM combo_cards cc JOIN cards c ON c.id = cc.card_id
               WHERE cc.combo_id = ? ORDER BY c.name""",
            (combo_id,),
        ).fetchall()


def set_combo_steps(db_path: str, combo_id: int, steps: list[str]) -> None:
    """Ersetzt alle Schritte einer Kombo durch die uebergebene Liste."""
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM combo_steps WHERE combo_id = ?", (combo_id,))
        for i, text in enumerate(steps, start=1):
            conn.execute(
                "INSERT INTO combo_steps (combo_id, step_no, text) VALUES (?,?,?)",
                (combo_id, i, text),
            )
        conn.commit()


def combo_steps(db_path: str, combo_id: int) -> list[sqlite3.Row]:
    with _conn(db_path) as conn:
        return conn.execute(
            "SELECT step_no, text FROM combo_steps WHERE combo_id = ? ORDER BY step_no",
            (combo_id,),
        ).fetchall()


def combos_for_card(db_path: str, card_id: int) -> list[sqlite3.Row]:
    """Alle Kombos, die diese Karte als Baustein verwenden."""
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT cb.combo_id, cb.name FROM combos cb
               JOIN combo_cards cc ON cc.combo_id = cb.combo_id
               WHERE cc.card_id = ? ORDER BY cb.name""",
            (card_id,),
        ).fetchall()


def _coverage_result(pieces) -> dict:
    """Baut die Coverage-Rueckgabe (total/covered/pieces) aus Baustein-Zeilen
    mit den Feldern needed/have. Gemeinsame Logik von combo_coverage (gegen
    ein Deck) und combo_coverage_collection (gegen die Sammlung)."""
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


def _combo_coverage_deck(conn: sqlite3.Connection, combo_id: int, deck_id: int) -> dict:
    """Abdeckung gegen ein Deck (Main+Extra) auf einer offenen Verbindung --
    so koennen combos_for_deck/Export sie ohne N+1-Verbindungen wiederholen."""
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
    return _coverage_result(pieces)


def _combo_coverage_collection(conn: sqlite3.Connection, combo_id: int) -> dict:
    """Abdeckung gegen die Sammlung auf einer offenen Verbindung."""
    pieces = conn.execute(
        """SELECT cc.card_id, c.name, c.type, c.frame_type,
                  cc.quantity AS needed,
                  COALESCE((SELECT SUM(col.quantity) FROM collection col
                            WHERE col.card_id = cc.card_id), 0) AS have
           FROM combo_cards cc JOIN cards c ON c.id = cc.card_id
           WHERE cc.combo_id = ? ORDER BY c.name""",
        (combo_id,),
    ).fetchall()
    return _coverage_result(pieces)


def combo_coverage(db_path: str, combo_id: int, deck_id: int) -> dict:
    """Abgleich einer Kombo mit einem Deck (Main+Extra).
    Rueckgabe: {'total', 'covered', 'pieces': [{card_id, name, type,
    frame_type, needed, have, missing}]}."""
    with _conn(db_path) as conn:
        return _combo_coverage_deck(conn, combo_id, deck_id)


def combos_for_deck(db_path: str, deck_id: int) -> list[dict]:
    """Alle Kombos mit ihrer Abdeckung gegen das Deck, nach Abdeckung sortiert.
    Eine Verbindung fuer alle Kombos (kein N+1)."""
    with _conn(db_path) as conn:
        combos = conn.execute(
            "SELECT combo_id, name, archetype FROM combos "
            "WHERE parent_combo_id IS NULL ORDER BY name"
        ).fetchall()
        out = []
        for cb in combos:
            cov = _combo_coverage_deck(conn, cb["combo_id"], deck_id)
            total = cov["total"]
            out.append({
                "combo_id": cb["combo_id"], "name": cb["name"],
                "archetype": cb["archetype"], "total": total,
                "covered": cov["covered"],
                "coverage": (cov["covered"] / total) if total else 0.0,
            })
    out.sort(key=lambda x: (-x["coverage"], x["name"]))
    return out


def deck_role_summary(db_path: str, deck_id: int) -> dict[str, list[dict]]:
    """Karten je Rolle im Deck (Main + Extra, wie combo_coverage; das Side
    Deck bleibt aussen vor). Eine Karte erscheint je Rolle einmal, auch wenn
    mehrere Kombos ihr dieselbe Rolle geben; verschiedene Rollen aus
    verschiedenen Kombos sind moeglich.
    Rueckgabe: {rolle: [{'card_id', 'name', 'copies'}, ...]}."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT DISTINCT cc.role, dc.card_id,
                      COALESCE(c.name_de, c.name) AS name, dc.quantity
               FROM deck_cards dc
               JOIN combo_cards cc ON cc.card_id = dc.card_id
               JOIN cards c ON c.id = dc.card_id
               WHERE dc.deck_id = ? AND dc.zone IN ('main', 'extra')
                 AND cc.role IS NOT NULL
                 AND cc.combo_id IN (SELECT combo_id FROM combos
                                     WHERE parent_combo_id IS NULL)
               ORDER BY name""",
            (deck_id,),
        ).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["role"], []).append({
            "card_id": r["card_id"], "name": r["name"],
            "copies": int(r["quantity"]),
        })
    return out


def deck_boss_lines(db_path: str, deck_id: int) -> list[dict]:
    """Linien (Kombos) gruppiert nach Bossmonster; innerhalb der Gruppen
    bleibt die Abdeckungs-Sortierung aus combos_for_deck erhalten, ebenso
    zwischen den Gruppen (beste Linie zuerst). Kombos ohne Boss bilden die
    letzte Gruppe (boss_card_id None).
    Rueckgabe: [{'boss_card_id', 'boss_name', 'lines': [wie combos_for_deck]}]."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT cb.combo_id, cb.boss_card_id,
                      COALESCE(b.name_de, b.name) AS boss_name
               FROM combos cb LEFT JOIN cards b ON b.id = cb.boss_card_id"""
        ).fetchall()
    boss_of = {r["combo_id"]: (r["boss_card_id"], r["boss_name"]) for r in rows}
    groups: dict = {}
    for line in combos_for_deck(db_path, deck_id):
        boss_id, boss_name = boss_of.get(line["combo_id"], (None, None))
        g = groups.setdefault(
            boss_id,
            {"boss_card_id": boss_id, "boss_name": boss_name, "lines": []},
        )
        g["lines"].append(line)
    out = [g for k, g in groups.items() if k is not None]
    if None in groups:
        out.append(groups[None])
    return out


def combo_coverage_collection(db_path: str, combo_id: int) -> dict:
    """Abgleich einer Kombo mit der eigenen Sammlung (collection).
    Gleiche Struktur wie combo_coverage, aber 'have' zaehlt den Bestand --
    beantwortet: 'Welche Bausteine besitze ich schon?'"""
    with _conn(db_path) as conn:
        return _combo_coverage_collection(conn, combo_id)


def combos_for_collection(db_path: str) -> list[dict]:
    """Alle Kombos mit ihrer Abdeckung gegen die Sammlung, nach Abdeckung
    sortiert. Beantwortet: 'Welche Kombos kann ich mit meinen Karten bauen?'
    Eine Verbindung fuer alle Kombos (kein N+1)."""
    with _conn(db_path) as conn:
        combos = conn.execute(
            "SELECT combo_id, name, archetype FROM combos "
            "WHERE parent_combo_id IS NULL ORDER BY name"
        ).fetchall()
        out = []
        for cb in combos:
            cov = _combo_coverage_collection(conn, cb["combo_id"])
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
# Konsistenz-Mathematik (hypergeometrisch, exakt, reine Standardbibliothek)
# ---------------------------------------------------------------------------

def hypergeom_at_least(
    population: int, successes: int, draws: int, min_hits: int = 1
) -> float:
    """Wahrscheinlichkeit, beim Ziehen ohne Zuruecklegen mindestens
    'min_hits' Erfolge zu ziehen. Unsinnige Eingaben werden gekappt
    (successes/draws auf population), statt zu raten oder zu werfen."""
    population = max(0, population)
    successes = max(0, min(successes, population))
    draws = max(0, min(draws, population))
    if min_hits <= 0:
        return 1.0
    if successes == 0 or draws == 0:
        return 0.0
    total = math.comb(population, draws)
    misses = sum(
        math.comb(successes, k) * math.comb(population - successes, draws - k)
        for k in range(min(min_hits, draws + 1))
    )
    return 1.0 - misses / total


def prob_open_all(deck_size: int, copies: Iterable[int], hand: int) -> float:
    """Wahrscheinlichkeit, von JEDER genannten Karte mindestens eine Kopie in
    der Starthand zu haben ('alle gewuenschten Karten zusammen oeffnen').

    'copies' = Kopienzahl je verschiedener Wunschkarte (disjunkte Karten).
    Exakt ueber Inklusion-Exklusion: P(alle da) =
        Sum_S (-1)^|S| * C(N - Sum_{i in S} c_i, h) / C(N, h)
    ueber alle Teilmengen S der Wunschkarten (math.comb, reine stdlib). Fuer
    die 'mindestens eine davon'-Frage genuegt hypergeom_at_least(N, sum(c), h).
    Unsinnige Eingaben werden gekappt statt geworfen; leere Auswahl -> 1.0
    (keine Bedingung)."""
    deck_size = max(0, deck_size)
    hand = max(0, min(hand, deck_size))
    cs = [c for c in (max(0, c) for c in copies) if c > 0]
    if not cs:
        return 1.0           # keine Wunschkarte -> Bedingung trivial erfuellt
    if hand == 0:
        return 0.0
    total = math.comb(deck_size, hand)
    acc = 0.0
    for r in range(len(cs) + 1):
        for subset in itertools.combinations(cs, r):
            remaining = deck_size - sum(subset)
            # remaining < hand (auch negativ bei Unsinn) -> C() = 0, kein Term.
            term = math.comb(remaining, hand) if remaining >= hand else 0
            acc += ((-1) ** r) * term
    return acc / total


def deck_role_copies(db_path: str, deck_id: int) -> dict[str, int]:
    """Kopien je Rolle im MAIN Deck (nur daraus wird gezogen).
    Eine Karte zaehlt je Rolle einmal, auch wenn mehrere Kombos ihr dieselbe
    Rolle geben; traegt sie in verschiedenen Kombos verschiedene Rollen,
    zaehlt sie in jeder davon."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT role, SUM(quantity) AS n FROM (
                   SELECT DISTINCT dc.card_id, cc.role, dc.quantity
                   FROM deck_cards dc
                   JOIN combo_cards cc ON cc.card_id = dc.card_id
                   WHERE dc.deck_id = ? AND dc.zone = 'main'
                     AND cc.role IS NOT NULL
                     AND cc.combo_id IN (SELECT combo_id FROM combos
                                         WHERE parent_combo_id IS NULL)
               ) GROUP BY role""",
            (deck_id,),
        ).fetchall()
        return {r["role"]: int(r["n"]) for r in rows}


# Anzeigenamen der Baustein-Rollen (siehe COMBO_ROLES). Oeffentlich, weil die
# GUI dieselbe Beschriftung nutzt -- eine Quelle der Wahrheit.
ROLE_LABEL = {
    "starter": "Starter", "extender": "Extender",
    "payoff": "Payoff", "handtrap": "Handtrap",
}


def export_deck_combos_text(db_path: str, deck_id: int) -> str:
    """Kombo-Linien eines Decks als lesbarer Text -- zum Weitergeben,
    z.B. wenn ein erfahrener Spieler ueber Deck und Linien schauen soll.
    Enthaelt Notation-Legende, Konsistenz, Rollen-Uebersicht und alle
    Kombos, die mindestens einen Baustein im Deck haben oder dieses Deck
    als Heimat-Deck fuehren (Reihenfolge wie im Deck-Tab: nach Abdeckung)."""
    with _conn(db_path) as conn:
        deck = conn.execute(
            "SELECT name FROM decks WHERE deck_id = ?", (deck_id,)
        ).fetchone()
    if deck is None:
        raise ValueError(f"Deck {deck_id} existiert nicht.")

    def pct(p: float) -> str:
        return f"{100 * p:.1f}".replace(".", ",") + " %"

    lines = [
        f"Kombo-Linien — {deck['name']}",
        f"Stand: {datetime.date.today().strftime('%d.%m.%Y')}",
        "",
        "Notation:  NS/SS = Normal/Special Summon · Act = Zauber/Falle"
        " aktivieren ·",
        "Eff1/Eff2 = (ersten/zweiten) Effekt aktivieren · Add = auf die"
        " Hand (Suche) ·",
        "GY = Friedhof · ED = Extra Deck · '->' verkettet Kosten/Wirkung/"
        "Resultat ·",
        "'| Lock: …' = Einschränkung · '[Req: …]' = Bedingung",
    ]

    stats = deck_consistency(db_path, deck_id)
    if stats["deck_size"] and stats["roles"].get("starter"):
        lines += ["", "== Konsistenz =="]
        lines.append("Kopien im Main: " + " · ".join(
            f"{ROLE_LABEL[r]} {stats['roles'][r]}"
            for r in COMBO_ROLES if stats["roles"].get(r)
        ))
        for hand, p in stats["hands"].items():
            ln = f"Starthand {hand}: ≥1 Starter {pct(p['starter'])}"
            if stats["roles"].get("handtrap"):
                ln += f" · ≥1 Handtrap {pct(p['handtrap'])}"
            lines.append(ln + f" · Brick {pct(p['brick'])}")

    summary = deck_role_summary(db_path, deck_id)
    if summary:
        lines += ["", "== Rollen im Deck (Main + Extra) =="]
        for role in COMBO_ROLES:
            cards = summary.get(role)
            if not cards:
                continue
            total = sum(c["copies"] for c in cards)
            lines.append(f"{ROLE_LABEL[role]} ({total}):")
            lines += [f"  {c['copies']}x {c['name']}" for c in cards]

    lines += ["", "== Kombo-Linien (nach Abdeckung im Deck) =="]
    count = 0
    for cb in combos_for_deck(db_path, deck_id):
        combo = get_combo(db_path, cb["combo_id"])
        if cb["covered"] == 0 and combo["deck_id"] != deck_id:
            continue  # gehoert erkennbar nicht zu diesem Deck
        count += 1
        head = f"{count}) {combo['name']}"
        if combo["archetype"]:
            head += f"  [{combo['archetype']}]"
        lines += ["", head]
        info = []
        if combo["boss_name"]:
            info.append(f"Boss: {combo['boss_name']}")
        info.append(f"Abdeckung: {cb['covered']}/{cb['total']} Bausteine im Deck")
        lines.append("   " + " · ".join(info))
        if combo["notes"]:
            lines += [
                f"   {ln.strip()}"
                for ln in combo["notes"].splitlines() if ln.strip()
            ]
        cov = combo_coverage(db_path, cb["combo_id"], deck_id)
        roles = {
            p["card_id"]: p["role"] for p in combo_cards(db_path, cb["combo_id"])
        }
        if cov["pieces"]:
            lines.append("   Bausteine:")
        for p in cov["pieces"]:
            role = roles.get(p["card_id"])
            tag = f"  [{ROLE_LABEL[role]}]" if role else ""
            if p["missing"] == 0:
                gap = ""
            elif p["have"] == 0:
                gap = "  (fehlt im Deck)"
            else:
                gap = f"  (nur {p['have']}/{p['needed']} im Deck)"
            lines.append(f"     {p['needed']}x {p['name']}{tag}{gap}")
        steps = combo_steps(db_path, cb["combo_id"])
        if steps:
            lines.append("   Schritte:")
            lines += [f"     {s['step_no']}. {s['text']}" for s in steps]
        # Varianten (Interruption-Branches) unter der Hauptlinie auffuehren.
        for var in combo_variants(db_path, cb["combo_id"]):
            lines += ["", f"   ↳ Variante: {var['name']}"]
            vcombo = get_combo(db_path, var["combo_id"])
            if vcombo["notes"]:
                lines += [
                    f"     {ln.strip()}"
                    for ln in vcombo["notes"].splitlines() if ln.strip()
                ]
            vsteps = combo_steps(db_path, var["combo_id"])
            lines += [f"     {s['step_no']}. {s['text']}" for s in vsteps]
    if count == 0:
        lines.append("(Keine Kombos mit Bausteinen aus diesem Deck erfasst.)")
    return "\n".join(lines) + "\n"


def deck_consistency(
    db_path: str, deck_id: int, hand_sizes: Iterable[int] = (5, 6)
) -> dict:
    """Konsistenz-Kennzahlen der Starthand (5 = First, 6 = Second).
    'brick' ist als Hand ohne Starter definiert.
    Rueckgabe: {'deck_size', 'roles': {rolle: kopien},
    'hands': {handgroesse: {'starter', 'handtrap', 'brick'}}}."""
    size = deck_counts(db_path, deck_id)["main"]
    roles = deck_role_copies(db_path, deck_id)
    hands = {}
    for hand in hand_sizes:
        p_starter = hypergeom_at_least(size, roles.get("starter", 0), hand)
        hands[hand] = {
            "starter": p_starter,
            "handtrap": hypergeom_at_least(size, roles.get("handtrap", 0), hand),
            "brick": 1.0 - p_starter,
        }
    return {"deck_size": size, "roles": roles, "hands": hands}


def deck_main_cards(db_path: str, deck_id: int) -> list[dict]:
    """Verschiedene Main-Deck-Karten mit Kopienzahl und (ggf.) Rollen --
    Grundlage fuer den Starthand-Simulator (Karten-Picker + Zufallshand).
    Eine Karte traegt alle Rollen, die ihr in irgendeiner Kombo gegeben
    wurden. Rueckgabe: [{'card_id', 'name', 'copies', 'roles': [...]}],
    nach Anzeigename sortiert."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT dc.card_id, COALESCE(c.name_de, c.name) AS name,
                      dc.quantity AS copies
               FROM deck_cards dc JOIN cards c ON c.id = dc.card_id
               WHERE dc.deck_id = ? AND dc.zone = 'main'
               ORDER BY name""",
            (deck_id,),
        ).fetchall()
        role_rows = conn.execute(
            """SELECT DISTINCT cc.card_id, cc.role FROM combo_cards cc
               WHERE cc.role IS NOT NULL
                 AND cc.combo_id IN (SELECT combo_id FROM combos
                                     WHERE parent_combo_id IS NULL)
                 AND cc.card_id IN
                   (SELECT card_id FROM deck_cards
                    WHERE deck_id = ? AND zone = 'main')""",
            (deck_id,),
        ).fetchall()
    roles_by: dict[int, list[str]] = {}
    for r in role_rows:
        roles_by.setdefault(r["card_id"], []).append(r["role"])
    return [
        {"card_id": r["card_id"], "name": r["name"], "copies": int(r["copies"]),
         "roles": sorted(roles_by.get(r["card_id"], []))}
        for r in rows
    ]


def draw_sample_hand(
    db_path: str, deck_id: int, hand_size: int = 5, rng=None
) -> list[dict]:
    """Zieht zufaellig hand_size Karten aus dem MAIN Deck (Kopien expandiert,
    ohne Zuruecklegen). 'rng' (random.Random) ist injizierbar -- so wird das
    Ziehen deterministisch testbar. Handgroesse wird auf die Main-Deck-Groesse
    gekappt. Rueckgabe: gezogene Karten [{'card_id', 'name', 'roles'}]."""
    pool = []
    for c in deck_main_cards(db_path, deck_id):
        pool.extend([c] * c["copies"])
    rng = rng or random
    k = min(max(0, hand_size), len(pool))
    return [
        {"card_id": c["card_id"], "name": c["name"], "roles": c["roles"]}
        for c in rng.sample(pool, k)
    ]


# ---------------------------------------------------------------------------
# Synergie-Graph & Vorschlaege (Roadmap-Schritt 4)
# ---------------------------------------------------------------------------

# Abschlag fuer Zwei-Hop-Verbindungen: eine Brueckenkarte (selbst nicht im
# Deck) zaehlt deutlich weniger als eine direkte gemeinsame Kombo.
_TRANSITIVE_DISCOUNT = 0.3

# --- Korpus-Kanten (Co-Occurrence ueber Referenz-Decks) ---------------------
# Mindestens so viele Referenz-Decks muessen ein Kartenpaar enthalten, damit
# eine Kante entsteht: PMI gibt sonst gerade den seltensten Paaren (eine
# einzige schraege Liste) die hoechsten Werte.
_CORPUS_MIN_DECKS = 2
# Halbwertszeit der Alterung: eine ein Jahr alte Liste zaehlt halb so viel
# (das Format dreht sich weiter; format_date NULL wird wie heute behandelt).
_CORPUS_HALF_LIFE_DAYS = 365.0
# Daempfung der Korpus-Kanten gegenueber den praezisen Kombo-Kanten im Score.
_CORPUS_WEIGHT = 0.5
# Je Kandidat gehen nur die staerksten PMI-Verbindungen in den Score ein,
# sonst schlaegt die schiere Deckgroesse jede Kombo-Evidenz.
_CORPUS_TOP_LINKS = 5


def synergy_edges(db_path: str) -> dict[tuple[int, int], dict]:
    """Kanten des Synergie-Graphen aus der Kombo-Bibliothek: zwei Karten sind
    verbunden, wenn sie gemeinsam in einer Kombo stehen; Gewicht = Anzahl
    gemeinsamer Kombos. Der Meta-Korpus (Roadmap-Schritt 5) speist spaeter
    zusaetzliche Kanten in denselben Graphen ein.
    Rueckgabe: {(a, b): {'weight': n, 'combos': [combo_id, ...]}} mit a < b."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT cc.combo_id, cc.card_id FROM combo_cards cc "
            "JOIN combos cb ON cb.combo_id = cc.combo_id "
            "WHERE cb.parent_combo_id IS NULL"
        ).fetchall()
    members: dict[int, set[int]] = {}
    for r in rows:
        members.setdefault(r["combo_id"], set()).add(r["card_id"])
    edges: dict[tuple[int, int], dict] = {}
    for combo_id in sorted(members):
        for a, b in itertools.combinations(sorted(members[combo_id]), 2):
            e = edges.setdefault((a, b), {"weight": 0, "combos": []})
            e["weight"] += 1
            e["combos"].append(combo_id)
    return edges


def corpus_edges(db_path: str) -> dict[tuple[int, int], dict]:
    """Co-Occurrence-Kanten aus den Referenz-Decks: jede Liste ist ein
    Datensatz, jedes Kartenpaar (Main+Extra, Praesenz statt Kopienzahl)
    eine gemeinsame Nennung. Statistik, kein ML-Training.

    Normierung ueber PPMI (positive pointwise mutual information) mit nach
    format_date gealterten Deck-Gewichten -- Staples, die in fast jeder
    Liste stehen, fallen dadurch auf (nahe) 0, echte Engine-Partner bleiben
    uebrig. Paare in weniger als _CORPUS_MIN_DECKS Listen entfallen.
    Rueckgabe: {(a, b): {'weight': ppmi, 'decks': k, 'total': N}} mit a < b;
    'decks'/'total' sind ungewichtete Listen-Zaehler fuer die Begruendung."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT d.deck_id, d.format_date, dc.card_id
               FROM decks d JOIN deck_cards dc ON dc.deck_id = d.deck_id
               WHERE d.kind = 'reference' AND dc.zone IN ('main', 'extra')"""
        ).fetchall()

    members: dict[int, set[int]] = {}
    dates: dict[int, Optional[str]] = {}
    for r in rows:
        members.setdefault(r["deck_id"], set()).add(r["card_id"])
        dates[r["deck_id"]] = r["format_date"]
    if not members:
        return {}

    today = datetime.date.today()
    weights: dict[int, float] = {}
    for did, date_str in dates.items():
        age_days = 0.0
        if date_str:
            try:
                age_days = max(0, (today - datetime.date.fromisoformat(date_str)).days)
            except ValueError:
                pass  # unlesbares Datum -> wie heute gewichtet
        weights[did] = 0.5 ** (age_days / _CORPUS_HALF_LIFE_DAYS)

    total_w = sum(weights.values())
    n_decks = len(members)
    card_w: dict[int, float] = {}
    pair_w: dict[tuple[int, int], float] = {}
    pair_n: dict[tuple[int, int], int] = {}
    for did, cards in members.items():
        w = weights[did]
        for c in cards:
            card_w[c] = card_w.get(c, 0.0) + w
        for a, b in itertools.combinations(sorted(cards), 2):
            pair_w[(a, b)] = pair_w.get((a, b), 0.0) + w
            pair_n[(a, b)] = pair_n.get((a, b), 0) + 1

    edges: dict[tuple[int, int], dict] = {}
    for (a, b), w_ab in pair_w.items():
        if pair_n[(a, b)] < _CORPUS_MIN_DECKS:
            continue
        ppmi = max(0.0, math.log(w_ab * total_w / (card_w[a] * card_w[b])))
        if ppmi > 0:
            edges[(a, b)] = {
                "weight": ppmi, "decks": pair_n[(a, b)], "total": n_decks,
            }
    return edges


def _card_names(conn: sqlite3.Connection, ids) -> dict[int, str]:
    """{id -> Anzeigename (DE bevorzugt)} fuer eine Menge Karten-IDs auf einer
    offenen Verbindung. Leere Menge -> leeres dict (kein Query)."""
    ids = tuple(ids)
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    return {
        r["id"]: r["name"]
        for r in conn.execute(
            f"SELECT id, COALESCE(name_de, name) AS name FROM cards "
            f"WHERE id IN ({ph})",
            ids,
        )
    }


def deck_suggestions(db_path: str, deck_id: int, limit: int = 15) -> dict:
    """Kartenvorschlaege fuer ein Deck aus dem Synergie-Graphen.

    Direkt: jede Kombo, die den Kandidaten enthaelt, traegt einen Punkt je
    Baustein, der schon im Deck (Main+Extra, wie combo_coverage) liegt --
    daraus entsteht zugleich die Begruendung ('zusammen mit X, Y in Kombo Z').
    Transitiv: Brueckenkarten (weder im Deck noch der Kandidat), die sowohl
    mit dem Kandidaten als auch mit >=1 Deck-Karte verbunden sind, gehen mit
    _TRANSITIVE_DISCOUNT ein. Korpus: die staerksten PPMI-Verbindungen des
    Kandidaten zu Deck-Karten (corpus_edges, max. _CORPUS_TOP_LINKS) gehen
    mit _CORPUS_WEIGHT ein -- Kombo-Kanten bleiben die praezise, hoeher
    gewichtete Quelle. Kandidaten sind nur Karten, die in KEINER Zone des
    Decks liegen. Die Luecken-Rolle (gap_role = Rolle mit den wenigsten
    Kopien im Main Deck) manipuliert keine Scores, sie steuert nur die
    Gruppierung in der Anzeige.
    Rueckgabe: {'gap_role', 'role_copies', 'suggestions': [{'card_id',
    'name', 'score', 'direct', 'bridges', 'roles', 'reasons',
    'corpus': [{'name', 'decks', 'weight'}, ...], 'corpus_total'}, ...]},
    Score-sortiert, auf 'limit' gekappt."""
    with _conn(db_path) as conn:
        deck_rows = conn.execute(
            "SELECT card_id, zone FROM deck_cards WHERE deck_id = ?",
            (deck_id,),
        ).fetchall()
        combo_rows = conn.execute(
            """SELECT cc.combo_id, cb.name AS combo_name, cc.card_id, cc.role
               FROM combo_cards cc JOIN combos cb ON cb.combo_id = cc.combo_id
               WHERE cb.parent_combo_id IS NULL"""
        ).fetchall()
        card_ids = {r["card_id"] for r in combo_rows}
        names = _card_names(conn, card_ids)

    in_deck_any = {r["card_id"] for r in deck_rows}
    deck_set = {r["card_id"] for r in deck_rows if r["zone"] in ("main", "extra")}

    # Kombo-Mitgliedschaften, Rollen und Direkt-Score samt Begruendung.
    combo_members: dict[int, set[int]] = {}
    combo_names: dict[int, str] = {}
    roles: dict[int, set[str]] = {}
    for r in combo_rows:
        combo_members.setdefault(r["combo_id"], set()).add(r["card_id"])
        combo_names[r["combo_id"]] = r["combo_name"]
        if r["role"]:
            roles.setdefault(r["card_id"], set()).add(r["role"])

    direct: dict[int, int] = {}
    reasons: dict[int, list[dict]] = {}
    for combo_id in sorted(combo_members):
        members = combo_members[combo_id]
        overlap = members & deck_set
        if not overlap:
            continue
        for c in members - in_deck_any:
            direct[c] = direct.get(c, 0) + len(overlap)
            reasons.setdefault(c, []).append({
                "combo_id": combo_id,
                "combo_name": combo_names[combo_id],
                "with": sorted(names.get(d, str(d)) for d in overlap),
            })

    # Adjazenz fuer den Zwei-Hop-Anteil (Brueckenkarten).
    adj: dict[int, set[int]] = {}
    for (a, b), _e in synergy_edges(db_path).items():
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    # Korpus-Kanten (Co-Occurrence der Referenz-Decks) in den Graphen mischen.
    corpus_adj: dict[int, dict[int, dict]] = {}
    corpus_total = 0
    for (a, b), e in corpus_edges(db_path).items():
        corpus_adj.setdefault(a, {})[b] = e
        corpus_adj.setdefault(b, {})[a] = e
        corpus_total = e["total"]

    candidates = (set(adj) | set(corpus_adj)) - in_deck_any
    bridges: dict[int, list[int]] = {}
    for c in candidates:
        bridges[c] = sorted(
            m for m in adj.get(c, ())
            if m not in in_deck_any and adj.get(m, set()) & deck_set
        )

    # Namen fuer Karten nachladen, die nur im Korpus vorkommen.
    missing = set(corpus_adj) - set(names)
    if missing:
        with _conn(db_path) as conn:
            names.update(_card_names(conn, missing))

    suggestions = []
    for c in candidates:
        links = sorted(
            (
                {"card_id": d, "name": names.get(d, str(d)),
                 "weight": e["weight"], "decks": e["decks"]}
                for d, e in corpus_adj.get(c, {}).items() if d in deck_set
            ),
            key=lambda l: -l["weight"],
        )[:_CORPUS_TOP_LINKS]
        corpus_score = _CORPUS_WEIGHT * sum(l["weight"] for l in links)
        n_bridges = len(bridges.get(c, []))
        score = direct.get(c, 0) + _TRANSITIVE_DISCOUNT * n_bridges + corpus_score
        if score <= 0:
            continue
        suggestions.append({
            "card_id": c,
            "name": names.get(c, str(c)),
            "score": score,
            "direct": direct.get(c, 0),
            "bridges": [names.get(m, str(m)) for m in bridges.get(c, [])],
            "roles": sorted(roles.get(c, ())),
            "reasons": reasons.get(c, []),
            "corpus": links,
            "corpus_total": corpus_total,
        })
    suggestions.sort(key=lambda s: (-s["score"], s["name"]))

    role_copies = deck_role_copies(db_path, deck_id)
    gap_role = min(COMBO_ROLES, key=lambda r: role_copies.get(r, 0))
    return {
        "gap_role": gap_role,
        "role_copies": role_copies,
        "suggestions": suggestions[:limit],
    }


# ---------------------------------------------------------------------------
# CLI: erstes Befuellen / Update
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    db = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DB
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"

    if cmd == "build":
        print("Lade Kartendatenbank von der API (englisch + deutsch) ...")
        n = build_database(db)
        print(f"Fertig: {n} Karten in {db} importiert.")
    elif cmd == "check":
        print("Update verfügbar." if needs_update(db) else "Datenbank ist aktuell.")
    else:
        print("Verwendung: python yugioh_db.py [build|check] [db_pfad]")
