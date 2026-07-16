"""
yugioh_db
=========
Datenschicht der Yu-Gi-Oh-App als Paket -- nur Standardbibliothek
(sqlite3, urllib, json), keine Drittpakete (Standalone-Prinzip).

Fassade: re-exportiert die komplette oeffentliche API, damit Aufrufer
unveraendert `import yugioh_db as ydb` nutzen (GUI, Tests). Schichtung
der Module (Importe strikt einbahnig, keine Zyklen):

    schema  -- Ablageorte, Verbindungen, DDL (Basis, importiert nichts)
    api     -- YGOPRODeck-Requests, Bild-Cache, build_database
    updates -- APP_VERSION, Update-Check, Migrations-Backup
    cards   -- Suche/Filter, Klassifikation, DE-Uebersetzungen
    collection / decks -- Benutzerdaten (Bestand, Decks, .ydk)
    combos  -- Kombo-Bibliothek (Bausteine, Rollen, Abdeckung)
    analysis -- Konsistenz-Mathematik, Synergie-Graph, Vorschlaege
    exports -- Text-/Markdown-Exporte
"""

from .schema import *  # noqa: F401,F403
from .api import *  # noqa: F401,F403
from .updates import *  # noqa: F401,F403
from .cards import *  # noqa: F401,F403
from .collection import *  # noqa: F401,F403
from .decks import *  # noqa: F401,F403
from .combos import *  # noqa: F401,F403
from .analysis import *  # noqa: F401,F403
from .exports import *  # noqa: F401,F403
# Von der GUI genutzte Verbindungs-Helfer (Unterstrich-Namen deckt der
# Stern-Import nicht ab).
from .schema import _conn, _connect  # noqa: F401
