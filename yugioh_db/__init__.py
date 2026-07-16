"""
yugioh_db
=========
Datenschicht der Yu-Gi-Oh-App als Paket -- nur Standardbibliothek
(sqlite3, urllib, json), keine Drittpakete (Standalone-Prinzip).

Fassade: re-exportiert die komplette oeffentliche API, damit Aufrufer
unveraendert `import yugioh_db as ydb` nutzen (GUI, Tests). Waehrend der
schrittweisen Aufteilung liegt die Implementierung in _monolith; die
Fachmodule (schema, api, decks, combos, ...) loesen ihn Stueck fuer
Stueck ab, ohne dass sich fuer Aufrufer ein Name aendert.
"""

from .schema import *  # noqa: F401,F403
from .api import *  # noqa: F401,F403
from .updates import *  # noqa: F401,F403
from ._monolith import *  # noqa: F401,F403 -- restliche API (schrumpft)
# Von der GUI genutzte Verbindungs-Helfer (Unterstrich-Namen deckt der
# Stern-Import nicht ab).
from .schema import _conn, _connect  # noqa: F401
