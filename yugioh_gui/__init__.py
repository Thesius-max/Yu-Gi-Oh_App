"""
yugioh_gui
==========
PySide6-Oberflaeche der Yu-Gi-Oh-App als Paket (einzige externe
Abhaengigkeit: PySide6; die Datenschicht yugioh_db bleibt stdlib-only).

Fassade: re-exportiert die Einstiegspunkte (main, MainWindow,
apply_theme). App-Start: start_app.py im Repo-Root.

Module (Abhaengigkeiten fliessen einbahnig zu den Blaettern):

    theme / labels / tasks / images / exporting / repository -- Blaetter
    carddetail  -- DetailPanel, CardDetailDialog, CardSearchDialog (geteilt)
    collection / deck / deck_dialogs / combos / playtest / manual -- Tabs
    mainwindow  -- Kompositionswurzel (verdrahtet Views per Callbacks)
    app         -- main(): QApplication, QSettings-Identitaet, Theme

Views importieren einander nie -- Querbezuege laufen ueber Callbacks,
die MainWindow setzt.
"""

from .app import main  # noqa: F401
from .mainwindow import MainWindow  # noqa: F401
from .theme import apply_theme  # noqa: F401
