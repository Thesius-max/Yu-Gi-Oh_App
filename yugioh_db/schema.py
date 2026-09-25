"""Basis der Datenschicht: Ablageorte, SQLite-Verbindungen, Schema.

Unterste Schicht des Pakets -- importiert keine anderen yugioh_db-Module.
Hier leben die Pfad-Konstanten (DEFAULT_DB, IMAGE_DIR), das komplette DDL
(SCHEMA), die Verbindungs-Helfer (_connect/_conn) und die idempotente
Schema-Pflege (ensure_schema/_migrate).
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

APP_DIR_NAME = "YugiohSammlung"


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
    link_value  INTEGER,               -- Link-Wert (sonst NULL)
    link_markers TEXT                  -- Link-Pfeile, kommagetrennt wie die
                                       -- API ('Top,Bottom-Left'; sonst NULL)
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


def _casefold(value):
    """SQL-Funktion casefold(x): SQLites LIKE/lower() sind nur fuer ASCII
    case-insensitiv -- 'über' faende sonst 'Über…' nicht."""
    return value.casefold() if isinstance(value, str) else value


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.create_function("casefold", 1, _casefold, deterministic=True)
    return conn


def _like_contains(text: str) -> str:
    r"""LIKE-Muster 'enthaelt text' (casefold, %/_/\ escaped). Gehoert zu
    SQL der Form  casefold(spalte) LIKE ? ESCAPE '\'  -- sonst wirken
    '%' und '_' in der Eingabe als Platzhalter."""
    esc = (text.strip().casefold()
           .replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))
    return f"%{esc}%"


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
    (CREATE TABLE IF NOT EXISTS ergaenzt keine Spalten). Idempotent.
    Vorhandene Spalten werden per PRAGMA ermittelt statt Fehler zu schlucken
    -- ein 'database is locked' darf nicht als 'Spalte existiert' gelten."""
    existing: dict[str, set[str]] = {}
    for table, col, decl in (
        ("cards", "name_de", "TEXT"),
        ("cards", "desc_de", "TEXT"),
        ("cards", "link_markers", "TEXT"),
        ("combos", "boss_card_id", "INTEGER REFERENCES cards(id)"),
        ("combos", "deck_id", "INTEGER REFERENCES decks(deck_id) ON DELETE SET NULL"),
        ("combos", "parent_combo_id",
         "INTEGER REFERENCES combos(combo_id) ON DELETE CASCADE"),
        ("combo_cards", "role", "TEXT"),
        ("decks", "kind", "TEXT"),
        ("decks", "source", "TEXT"),
        ("decks", "format_date", "TEXT"),
    ):
        if table not in existing:
            existing[table] = {
                r[1] for r in conn.execute(f"PRAGMA table_info({table})")
            }
        if col not in existing[table]:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            existing[table].add(col)


def ensure_schema(db_path: str = DEFAULT_DB) -> None:
    """Legt fehlende Tabellen an (idempotent). Auch fuer bestehende DBs,
    damit nachgeruestete Tabellen wie decks/deck_cards vorhanden sind."""
    with _conn(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
        _migrate(conn)
        conn.commit()
        # Ausdrucks-Index fuer die Anzeige-Namens-Sortierung (Browse-Leersuche
        # sortiert ueber 14k+ Karten nach COALESCE(name_de, name)). Bewusst
        # NACH _migrate -- auf alten DBs existiert name_de erst dann.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cards_disp_name "
            "ON cards(COALESCE(name_de, name))"
        )
        conn.commit()


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
