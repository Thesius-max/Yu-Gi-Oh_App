"""App-Start: QApplication, QSettings-Identitaet, Theme, Hauptfenster.

Aufruf ueber start_app.py im Repo-Root (auch PyInstaller-Einstieg).
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

import yugioh_db as ydb

from .mainwindow import MainWindow
from .theme import apply_theme


def main() -> None:
    app = QApplication(sys.argv)
    # Identitaet fuer QSettings (Sitzungs-/Fensterstatus).
    app.setOrganizationName(ydb.APP_DIR_NAME)
    app.setApplicationName(ydb.APP_DIR_NAME)
    apply_theme(app)
    win = MainWindow(restore_session=True)
    win.show()
    sys.exit(app.exec())
