"""App-Version, Update-Pruefung und Migrations-Sicherung.

APP_VERSION ist die eine Versionsnummer fuer alle Plattform-Builds
(Release-Ritual: hochzaehlen -> committen -> Tag "v<version>" pushen).
Dazu der stille GitHub-Release-Check (check_app_update), die Seed-DB-
Uebernahme beim gepackten Erststart (ensure_user_db) und die
Versions-Sicherung der Benutzer-DB (migration_backup).
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from .api import _http_get_json
from .schema import DEFAULT_DB, _conn

# App-Version: eine Nummer fuer alle Plattform-Builds (Windows + macOS).
# Release-Ritual: hochzaehlen -> committen -> Tag "v<version>" pushen; die CI
# baut dann alle drei ZIPs und haengt sie an EIN gemeinsames GitHub-Release.
APP_VERSION = "0.8.0"
RELEASES_API_URL = (
    "https://api.github.com/repos/Thesius-max/Yu-Gi-Oh_App/releases/latest"
)
RELEASES_PAGE_URL = "https://github.com/Thesius-max/Yu-Gi-Oh_App/releases/latest"


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
