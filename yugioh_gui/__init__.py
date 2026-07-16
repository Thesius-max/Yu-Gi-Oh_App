"""
yugioh_gui
==========
PySide6-Oberflaeche der Yu-Gi-Oh-App als Paket (einzige externe
Abhaengigkeit: PySide6; die Datenschicht yugioh_db bleibt stdlib-only).

Fassade: re-exportiert die Einstiegspunkte (main, MainWindow,
apply_theme). Waehrend der schrittweisen Aufteilung liegt die
Implementierung in _monolith; die Modul-Schnitte (theme, views, ...)
loesen ihn Stueck fuer Stueck ab. App-Start: start_app.py im Root.
"""

from ._monolith import MainWindow, apply_theme, main  # noqa: F401
