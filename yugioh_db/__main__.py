"""CLI der Datenschicht: erstes Befuellen / Update der Kartendatenbank.

Verwendung:
    python -m yugioh_db [build|check] [db_pfad]
"""

import sys

from . import DEFAULT_DB, build_database, needs_update


def main() -> None:
    db = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DB
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"

    if cmd == "build":
        print("Lade Kartendatenbank von der API (englisch + deutsch) ...")
        n = build_database(db)
        print(f"Fertig: {n} Karten in {db} importiert.")
    elif cmd == "check":
        print("Update verfügbar." if needs_update(db) else "Datenbank ist aktuell.")
    else:
        print("Verwendung: python -m yugioh_db [build|check] [db_pfad]")


if __name__ == "__main__":
    main()
