"""
build_app.py
============
Erzeugt ein verteilbares Bundle der App mit PyInstaller (One-Folder).

Voraussetzungen:
    pip install pyinstaller
    python yugioh_db.py build seed.sqlite3   # einmalig die Seed-DB erzeugen

Aufruf:
    python build_app.py

Ergebnis:
    dist/YugiohSammlung/   -- kompletter Ordner zum Zippen und Weitergeben.

Hinweis: PyInstaller kann NICHT cross-kompilieren. Ein Windows-Build muss auf
Windows, ein macOS-Build (.app) auf einem Mac erzeugt werden. Dieses Skript
funktioniert auf beiden Plattformen identisch (der Pfad-Trenner fuer --add-data
wird passend gesetzt).
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEED = ROOT / "seed.sqlite3"
README = ROOT / "TESTER_LIESMICH.txt"
APP_NAME = "YugiohSammlung"


def main() -> None:
    if not SEED.exists():
        sys.exit(
            "seed.sqlite3 fehlt.\n"
            "Bitte einmalig erzeugen:  python yugioh_db.py build seed.sqlite3"
        )
    # --add-data nutzt ';' auf Windows, ':' auf macOS/Linux.
    sep = ";" if os.name == "nt" else ":"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--windowed",                       # kein Konsolenfenster (Win) / .app (mac)
        "--name", APP_NAME,
        "--add-data", f"{SEED}{sep}.",      # Seed-DB ins Bundle legen
        str(ROOT / "yugioh_gui.py"),
    ]
    print("PyInstaller:", " ".join(args))
    subprocess.check_call(args)
    out = ROOT / "dist" / APP_NAME
    # Tester-Anleitung mit ins Bundle legen.
    if README.exists():
        shutil.copy(README, out / README.name)
    print(f"\nFertig. Bundle: {out}")
    print("Diesen Ordner zippen und an die Tester weitergeben.")


if __name__ == "__main__":
    main()
