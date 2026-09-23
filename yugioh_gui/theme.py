"""Theme: dunkles Violett/Gold ("Yu-Gi-Oh").

Fusion-Style als neutrale Basis, darueber eine dunkle Palette (faerbt
auch vom Style gemalte Teile wie Menue-Pfeile/Checkboxen) und ein
durchgaengiges Stylesheet. Eine Stelle fuer die komplette Optik --
Views selbst bleiben stylefrei und importieren abgeleitete Farb-Objekte
(GROUP_HEADER_*, UNTRANSLATED_FG) von hier.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


# Farb-Tokens (eine Quelle der Wahrheit; im QSS unten als Literale wiederholt,
# da Qt-Stylesheets keine Variablen kennen).
_BG        = "#1a1426"   # Fenster-Hintergrund (dunkelviolett)
_PANEL     = "#241a33"   # Panels / Buttons / Header
_BASE      = "#1f1830"   # Eingabefelder / Listen / Tabellen
_HOVER     = "#2c2140"   # Hover-Flaeche
_BORDER    = "#3a2f4d"   # Rahmen
_BORDER_HI = "#4a3d5e"   # hellerer Rahmen (Buttons)
_TEXT      = "#efe9f5"   # Haupttext
_TEXT_DIM  = "#9b90b5"   # gedaempfter Text
_DISABLED  = "#6b6280"   # deaktiviert
_GOLD      = "#d4af37"   # Akzent
_GOLD_HI   = "#e6c34d"   # Akzent hell (Hover)


def _build_palette() -> QPalette:
    pal = QPalette()
    C = QColor
    pal.setColor(QPalette.ColorRole.Window, C(_BG))
    pal.setColor(QPalette.ColorRole.WindowText, C(_TEXT))
    pal.setColor(QPalette.ColorRole.Base, C(_BASE))
    pal.setColor(QPalette.ColorRole.AlternateBase, C(_PANEL))
    pal.setColor(QPalette.ColorRole.ToolTipBase, C(_PANEL))
    pal.setColor(QPalette.ColorRole.ToolTipText, C(_TEXT))
    pal.setColor(QPalette.ColorRole.Text, C(_TEXT))
    pal.setColor(QPalette.ColorRole.Button, C(_PANEL))
    pal.setColor(QPalette.ColorRole.ButtonText, C(_TEXT))
    pal.setColor(QPalette.ColorRole.BrightText, C(_GOLD_HI))
    pal.setColor(QPalette.ColorRole.Link, C(_GOLD))
    pal.setColor(QPalette.ColorRole.Highlight, C(_GOLD))
    pal.setColor(QPalette.ColorRole.HighlightedText, C(_BG))
    pal.setColor(QPalette.ColorRole.PlaceholderText, C("#8a7fa6"))
    dis = C(_DISABLED)
    for role in (
        QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        pal.setColor(QPalette.ColorGroup.Disabled, role, dis)
    return pal


_THEME_QSS = """
QWidget { font-size: 10pt; }

/* Tabs: gold unterstrichener aktiver Reiter, dezent sonst */
QTabWidget::pane { border-top: 1px solid #3a2f4d; }
QTabBar::tab {
    background: transparent; color: #9b90b5;
    padding: 7px 16px; border: none; border-bottom: 2px solid transparent;
}
QTabBar::tab:hover { color: #efe9f5; }
QTabBar::tab:selected { color: #d4af37; border-bottom: 2px solid #d4af37; }

/* Buttons: violette Pillen, Gold-Akzent bei Hover */
QPushButton {
    background: #2c2140; color: #efe9f5;
    border: 1px solid #4a3d5e; border-radius: 5px; padding: 5px 12px;
}
QPushButton:hover { border-color: #d4af37; color: #f5e8b8; }
QPushButton:pressed { background: #241a33; }
QPushButton:disabled { color: #6b6280; border-color: #332a45; }
QPushButton#primary {
    background: #d4af37; color: #1a1426; border: none; font-weight: bold;
}
QPushButton#primary:hover { background: #e6c34d; }
QPushButton#primary:disabled { background: #5a4f33; color: #3a3326; }

/* Eingaben & Listen */
QLineEdit, QComboBox, QSpinBox, QTextEdit, QTextBrowser,
QListWidget, QTableWidget {
    background: #1f1830; border: 1px solid #3a2f4d; border-radius: 4px;
    selection-background-color: #d4af37; selection-color: #1a1426;
}
QLineEdit, QComboBox, QSpinBox { padding: 4px 6px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QTextEdit:focus, QTextBrowser:focus, QListWidget:focus, QTableWidget:focus {
    border-color: #d4af37;
}
QComboBox QAbstractItemView {
    background: #241a33; border: 1px solid #3a2f4d;
    selection-background-color: #d4af37; selection-color: #1a1426;
}
QListWidget::item, QTableWidget::item { padding: 3px 4px; }
QListWidget::item:hover, QTableWidget::item:hover { background: #2c2140; }
QListWidget::item:selected, QTableWidget::item:selected {
    background: #d4af37; color: #1a1426;
}

/* Checkbox-Indikatoren (ankreuzbare Listen + QCheckBox) -- ohne dies zeichnet
   Fusion sie dunkel auf dunkel. Angekreuzt = gold gefuellt (Theme-Akzent). */
QCheckBox::indicator, QListWidget::indicator {
    width: 14px; height: 14px;
    border: 1px solid #4a3d5e; border-radius: 3px; background: #1f1830;
}
QCheckBox::indicator:hover, QListWidget::indicator:hover {
    border-color: #d4af37;
}
QCheckBox::indicator:checked, QListWidget::indicator:checked {
    background: #d4af37; border-color: #d4af37;
}

/* Tabellen-Kopf */
QHeaderView::section {
    background: #241a33; color: #cbb6e0; padding: 4px 6px; border: none;
    border-right: 1px solid #3a2f4d; border-bottom: 1px solid #3a2f4d;
}
QTableWidget { gridline-color: #3a2f4d; }
QTableCornerButton::section { background: #241a33; border: none; }

/* Gruppenrahmen mit goldenem Titel */
QGroupBox {
    border: 1px solid #3a2f4d; border-radius: 6px;
    margin-top: 10px; padding-top: 6px; font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 10px; padding: 0 4px; color: #d4af37;
}

/* Menue */
QMenuBar { background: #1a1426; color: #efe9f5; }
QMenuBar::item { padding: 4px 10px; background: transparent; }
QMenuBar::item:selected { background: #2c2140; color: #d4af37; }
QMenu { background: #241a33; border: 1px solid #3a2f4d; }
QMenu::item { padding: 5px 22px; }
QMenu::item:selected { background: #d4af37; color: #1a1426; }
QMenu::separator { height: 1px; background: #3a2f4d; margin: 4px 8px; }

/* Splitter, Scrollbalken, Fortschritt, Tooltip */
QSplitter::handle { background: #3a2f4d; }
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical { height: 2px; }
QScrollBar:vertical { background: #1a1426; width: 12px; margin: 0; }
QScrollBar:horizontal { background: #1a1426; height: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #4a3d5e; border-radius: 6px; min-height: 24px; }
QScrollBar::handle:horizontal { background: #4a3d5e; border-radius: 6px; min-width: 24px; }
QScrollBar::handle:hover { background: #d4af37; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QProgressBar {
    border: 1px solid #3a2f4d; border-radius: 4px;
    text-align: center; background: #1f1830; color: #efe9f5;
}
QProgressBar::chunk { background: #d4af37; border-radius: 3px; }
QToolTip {
    background: #241a33; color: #efe9f5;
    border: 1px solid #d4af37; padding: 3px;
}
#HoverCardPreview {
    background: #241a33; color: #9b90b5;
    border: 1px solid #d4af37; padding: 2px;
}
#BoardZone {
    background: #1f1830; border: 1px solid #3a2f4d; border-radius: 4px;
}

/* Benannte Widgets statt Inline-Styles in den Views */
#HintLabel { color: #9b90b5; }
#LintLabel { color: #e6a23c; }
#CardImage { border: 1px solid #3a2f4d; color: #9b90b5; }
#CardTitle { font-size: 16px; font-weight: bold; }
#TokenChip { padding: 1px 6px; }
#HeldCard { border: 2px solid #d4af37; }
#ZonePlaceholder { color: #5b5273; }
#PileCaption { color: #cfc6e0; font-size: 9px; }
"""


def apply_theme(app: QApplication) -> None:
    """Dunkles Violett/Gold-Theme auf die App legen (Style + Palette + QSS)."""
    app.setStyle("Fusion")
    app.setPalette(_build_palette())
    app.setStyleSheet(_THEME_QSS)

# Abgeleitete Farb-Objekte fuer Views (eine Quelle der Wahrheit: die Tokens
# oben). Kopfzeilen der Sammlungstabelle in Panel/Gold; Karten ohne deutsche
# Uebersetzung warm-orange hervorheben -- abgesetzt vom Gold.
GROUP_HEADER_BG = QColor(_PANEL)
GROUP_HEADER_FG = QColor(_GOLD)
UNTRANSLATED_FG = QColor("#e08a3c")

