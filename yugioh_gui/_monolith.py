"""
yugioh_gui.py
=============
PySide6-Grundgeruest fuer die Yu-Gi-Oh-Sammlungs- und Nachschlage-App.

Greift auf die lokale SQLite-Datenbank aus yugioh_db.py zu. Beide Dateien
gehoeren zusammen und liegen im selben Verzeichnis.

Vorab einmalig die Datenbank anlegen:
    python yugioh_db.py build

Dann starten:
    python yugioh_gui.py

Benoetigt PySide6:
    pip install PySide6

Aufbau (drei Spalten, frei skalierbar via Splitter):
    links   -- Suche + Filter (Volltext, Typ, Attribut, Archetyp, Level, ATK)
    mitte   -- Trefferliste
    rechts  -- Detailansicht: Bild, Werte, Kartentext, "zur Sammlung"
"""

from __future__ import annotations

import datetime
import itertools
import os
import random
import shutil
import sys

from PySide6.QtCore import (
    Qt, QEvent, QMarginsF, QObject, QPoint, QPointF, QRunnable, QSettings,
    QThreadPool, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QDesktopServices, QFont, QGuiApplication, QImage,
    QKeySequence, QPageLayout, QPageSize, QPainter, QPalette, QPdfWriter,
    QPen, QPixmap, QPixmapCache, QShortcut, QTextCursor, QTextDocument,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QProgressDialog, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

import yugioh_db as ydb
from .carddetail import (
    CardDetailDialog, CardSearchDialog, DetailPanel, edit_card_translation,
)
from .collection import CollectionView
from .deck_dialogs import (
    ComboFromDeckDialog, DeckCorpusDiffDialog, ReferenceDeckDialog,
)

from .exporting import (
    resolve_export_path as _resolve_export_path,
    write_text_file as _write_text_file,
    write_text_pdf as _write_text_pdf,
)
from .images import (
    IMAGE_URL,
    CardImageView as _CardImageView,
    HoverCardPreview as _HoverCardPreview,
    card_back_pixmap as _card_back_pixmap,
    lookup_card_pixmap as _lookup_card_pixmap,
    placeholder_pixmap as _placeholder_pixmap,
    scale_pixmap as _scale_pixmap,
)
from .labels import (
    ATTR_DE as _ATTR_DE, CATEGORY_DE as _CATEGORY_DE,
    CATEGORY_ORDER as _CATEGORY_ORDER, COLLECTION_CARD_ID as _COLLECTION_CARD_ID,
    PIECE_ROLE_DATA as _PIECE_ROLE_DATA, RACE_DE as _RACE_DE,
    ROLE_DE as _ROLE_DE, SIM_COPIES_DATA as _SIM_COPIES_DATA,
    TYPE_DE as _TYPE_DE, ZONE_LABELS,
    import_report_lines as _import_report_lines, pct as _pct,
)
from .repository import CardRepository
from .tasks import (
    DbTask as _DbTask, DbTaskSignals as _DbTaskSignals,
    ImageLoader as _ImageLoader, ImageSignals as _ImageSignals,
)
from .theme import (
    GROUP_HEADER_BG as _GROUP_HEADER_BG, GROUP_HEADER_FG as _GROUP_HEADER_FG,
    UNTRANSLATED_FG as _UNTRANSLATED_FG, apply_theme,
)




# ---------------------------------------------------------------------------
# Deckbuilding (eigener Tab)
# ---------------------------------------------------------------------------



class ZonePanel(QGroupBox):
    """Eine Deck-Zone: Liste der Karten plus Steuerleiste."""

    def __init__(self, zone: str, owner: "DeckView"):
        super().__init__(ZONE_LABELS[zone])
        self.zone = zone
        self.owner = owner
        self._shortages: dict[int, int] = {}

        v = QVBoxLayout(self)
        self.list = QListWidget()
        v.addWidget(self.list)
        # Schwebende Kartenbild-Vorschau beim Ueberfahren einer Karte.
        self._hover = _HoverCardPreview(self.list, self._hover_card_id)
        # Doppelklick auf eine Karte -> read-only Detail-Pop-up (geteilt ueber
        # die DeckView, analog zum Sammlung-Tab).
        self.list.itemDoubleClicked.connect(self._on_double_click)

        add_btn = QPushButton("+ Karte hinzuf\u00fcgen")
        add_btn.clicked.connect(lambda: owner.add_card_dialog(self.zone))
        v.addWidget(add_btn)

        bar = QHBoxLayout()
        minus = QPushButton("\u22121")
        plus = QPushButton("+1")
        remove = QPushButton("Entfernen")
        minus.clicked.connect(lambda: owner.adjust(self.zone, -1))
        plus.clicked.connect(lambda: owner.adjust(self.zone, +1))
        remove.clicked.connect(lambda: owner.remove(self.zone))
        bar.addWidget(minus)
        bar.addWidget(plus)
        bar.addWidget(remove)
        if zone == "side":
            move = QPushButton("\u2192 Deck")
            move.clicked.connect(owner.move_from_side)
        else:
            move = QPushButton("\u2192 Side")
            move.clicked.connect(lambda: owner.move(self.zone, "side"))
        bar.addWidget(move)
        v.addLayout(bar)

    def selected_card_id(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _hover_card_id(self, pos):
        """card_id der Karte unter 'pos' (Viewport-Koord.) oder None bei
        Gruppen-Kopfzeilen/Leerraum -- speist die Hover-Bildvorschau."""
        item = self.list.itemAt(pos)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_double_click(self, item) -> None:
        """Detail-Pop-up der Karte oeffnen; Gruppen-Kopfzeilen (ohne card_id)
        ignorieren."""
        card_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if card_id is not None:
            self.owner.open_card_detail(card_id)

    def populate(self, rows, shortages: dict[int, int] | None = None) -> int:
        self.list.clear()
        self._shortages = shortages or {}
        total = sum(r["quantity"] for r in rows)
        # Nur das Main-Deck nach Monster/Zauber/Falle gruppieren; Extra ist
        # ohnehin reines Extra-Monster, Side bleibt eine einfache Liste.
        if self.zone == "main":
            buckets: dict[str, list] = {c: [] for c in _CATEGORY_ORDER}
            for r in rows:
                buckets[ydb.card_category(r["type"])].append(r)
            for cat in _CATEGORY_ORDER:
                group = buckets[cat]
                if not group:
                    continue
                self._add_group_header(
                    _CATEGORY_DE[cat], sum(r["quantity"] for r in group)
                )
                for r in group:
                    self._add_card_item(r)
        else:
            for r in rows:
                self._add_card_item(r)
        return total

    def _add_card_item(self, r) -> None:
        text = f"{r['quantity']}x  {r['name']}"
        # Fehlbestand gilt je Karte ueber alle Zonen dieses Decks zusammen.
        missing = self._shortages.get(r["card_id"], 0)
        if missing:
            text += f"   ⚠ fehlt {missing}"
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, r["card_id"])
        if missing:
            item.setToolTip(
                f"Bestand deckt dieses Deck nicht: {missing} Kopie(n) fehlen "
                "(Kopien in anderen Decks zählen als gebunden)."
            )
        self.list.addItem(item)

    def _add_group_header(self, label: str, count: int) -> None:
        item = QListWidgetItem(f"— {label} ({count}) —")
        item.setFlags(Qt.ItemFlag.NoItemFlags)  # nicht auswählbar (kein card_id)
        font = item.font(); font.setBold(True); item.setFont(font)
        self.list.addItem(item)



class DeckView(QWidget):
    def __init__(self, repo: CardRepository):
        super().__init__()
        self.repo = repo
        self.deck_id: int | None = None
        # Wird von MainWindow gesetzt: oeffnet eine Kombo im Kombos-Tab.
        self.open_combo_callback = None
        # Wird von MainWindow gesetzt: callback(card_id) -- Karte im
        # DetailPanel des Suche-Tabs zeigen (fuer Vorschlaege).
        self.open_card_callback = None
        # Read-only Detail-Pop-up (Doppelklick in einer Zone), lazy angelegt
        # und ueber alle drei Zonen wiederverwendet.
        self._detail_dialog: CardDetailDialog | None = None

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.deck_cb = QComboBox()
        self.deck_cb.currentIndexChanged.connect(self._on_deck_selected)
        new_btn = QPushButton("Neues Deck")
        new_btn.clicked.connect(self._new_deck)
        del_btn = QPushButton("Deck löschen")
        del_btn.clicked.connect(self._delete_deck)
        import_btn = QPushButton("Importieren…")
        import_btn.clicked.connect(self._import_deck)
        export_btn = QPushButton("Exportieren…")
        export_btn.clicked.connect(self._export_deck)
        corpus_btn = QPushButton("Korpus…")
        corpus_btn.setToolTip(
            "Referenz-Decks (Meta-Listen) als Datenbasis für "
            "Kartenvorschläge verwalten"
        )
        corpus_btn.clicked.connect(self._open_corpus)
        diff_btn = QPushButton("Vergleich…")
        diff_btn.setToolTip(
            "Aktuelles Deck mit einer Korpus-/Meta-Liste vergleichen "
            "(was fehlt dir, was hast du extra)"
        )
        diff_btn.clicked.connect(self._open_corpus_diff)
        top.addWidget(QLabel("Deck:"))
        top.addWidget(self.deck_cb, stretch=1)
        top.addWidget(new_btn)
        top.addWidget(del_btn)
        top.addWidget(import_btn)
        top.addWidget(export_btn)
        top.addWidget(corpus_btn)
        top.addWidget(diff_btn)
        layout.addLayout(top)

        zones_widget = QWidget()
        zones = QHBoxLayout(zones_widget)
        zones.setContentsMargins(0, 0, 0, 0)
        self.panels = {
            "main": ZonePanel("main", self),
            "extra": ZonePanel("extra", self),
            "side": ZonePanel("side", self),
        }
        zones.addWidget(self.panels["main"], stretch=3)
        zones.addWidget(self.panels["extra"], stretch=2)
        zones.addWidget(self.panels["side"], stretch=2)

        combo_box = QGroupBox("Kombo-Hilfe")
        cv = QVBoxLayout(combo_box)
        self.helper_tabs = QTabWidget()
        cv.addWidget(self.helper_tabs)

        kombos_tab = QWidget()
        kv = QVBoxLayout(kombos_tab)
        kv.addWidget(QLabel("Kombos nach Abdeckung:"))
        self.combo_list = QListWidget()
        self.combo_list.currentItemChanged.connect(self._on_combo_selected)
        kv.addWidget(self.combo_list, stretch=1)
        kv.addWidget(QLabel("Bausteine (vorhanden / benötigt):"))
        self.combo_pieces = QListWidget()
        kv.addWidget(self.combo_pieces, stretch=1)
        kv.addWidget(QLabel("Schritte:"))
        self.combo_steps = QListWidget()
        self.combo_steps.setWordWrap(True)
        kv.addWidget(self.combo_steps, stretch=1)
        self.add_missing_btn = QPushButton("Fehlende Bausteine ins Deck")
        self.add_missing_btn.clicked.connect(self._add_missing)
        kv.addWidget(self.add_missing_btn)
        self.new_combo_btn = QPushButton("Neue Kombo aus diesem Deck…")
        self.new_combo_btn.clicked.connect(self._new_combo_from_deck)
        kv.addWidget(self.new_combo_btn)
        self.helper_tabs.addTab(kombos_tab, "Kombos")

        plan_tab = QWidget()
        pv = QVBoxLayout(plan_tab)
        self.consistency = QLabel("")
        self.consistency.setWordWrap(True)
        pv.addWidget(self.consistency)
        pv.addWidget(QLabel("Rollen im Deck (Main + Extra):"))
        self.role_summary = QListWidget()
        pv.addWidget(self.role_summary, stretch=1)
        pv.addWidget(QLabel("Linien zum Boss (Doppelklick öffnet die Kombo):"))
        self.boss_lines = QListWidget()
        self.boss_lines.itemDoubleClicked.connect(self._open_line)
        pv.addWidget(self.boss_lines, stretch=1)
        self.helper_tabs.addTab(plan_tab, "Fahrplan")

        sug_tab = QWidget()
        sv = QVBoxLayout(sug_tab)
        self.gap_label = QLabel("")
        self.gap_label.setWordWrap(True)
        sv.addWidget(self.gap_label)
        sv.addWidget(QLabel(
            "Karten aus den Kombos, die im Deck fehlen "
            "(Doppelklick zeigt die Karte):"
        ))
        self.suggestion_list = QListWidget()
        self.suggestion_list.itemDoubleClicked.connect(self._open_suggestion)
        sv.addWidget(self.suggestion_list, stretch=1)
        self.helper_tabs.addTab(sug_tab, "Vorschläge")

        sim_tab = QWidget()
        siv = QVBoxLayout(sim_tab)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Rechenart:"))
        self.sim_mode_cb = QComboBox()
        self.sim_mode_cb.addItem("alle zusammen", True)
        self.sim_mode_cb.addItem("mindestens eine", False)
        self.sim_mode_cb.currentIndexChanged.connect(self._recompute_prob)
        mode_row.addWidget(self.sim_mode_cb)
        mode_row.addStretch()
        siv.addLayout(mode_row)
        siv.addWidget(QLabel(
            "Karten ankreuzen — Wahrscheinlichkeit in der Starthand:"
        ))
        self.sim_cards = QListWidget()
        self.sim_cards.itemChanged.connect(self._recompute_prob)
        siv.addWidget(self.sim_cards, stretch=1)
        self.sim_fill_btn = QPushButton("Aus gewählter Kombo füllen")
        self.sim_fill_btn.clicked.connect(self._fill_from_combo)
        siv.addWidget(self.sim_fill_btn)
        self.sim_prob = QLabel("")
        self.sim_prob.setWordWrap(True)
        siv.addWidget(self.sim_prob)
        draw_row = QHBoxLayout()
        self.sim_draw_btn = QPushButton("Hand ziehen")
        self.sim_draw_btn.clicked.connect(self._draw_hand)
        draw_row.addWidget(self.sim_draw_btn)
        draw_row.addWidget(QLabel("Größe:"))
        self.sim_hand_cb = QComboBox()
        self.sim_hand_cb.addItem("5 (First)", 5)
        self.sim_hand_cb.addItem("6 (Second)", 6)
        draw_row.addWidget(self.sim_hand_cb)
        draw_row.addStretch()
        siv.addLayout(draw_row)
        self.sim_hand = QListWidget()
        siv.addWidget(self.sim_hand, stretch=1)
        self.sim_verdict = QLabel("")
        self.sim_verdict.setWordWrap(True)
        siv.addWidget(self.sim_verdict)
        self.helper_tabs.addTab(sim_tab, "Starthand")

        # Kombo-Linien als Datei weitergeben (z.B. an erfahrene Spieler).
        export_combos_btn = QPushButton("Kombo-Linien exportieren…")
        export_combos_btn.clicked.connect(self._export_combos)
        cv.addWidget(export_combos_btn)

        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.addWidget(zones_widget)
        main_split.addWidget(combo_box)
        main_split.setSizes([720, 320])
        layout.addWidget(main_split, stretch=1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._reload_decks()

    # -- Deck-Auswahl / Verwaltung ------------------------------------------

    def _reload_decks(self) -> None:
        self.deck_cb.blockSignals(True)
        self.deck_cb.clear()
        decks = ydb.list_decks(self.repo.db_path) if self.repo.exists() else []
        for d in decks:
            self.deck_cb.addItem(d["name"], d["deck_id"])
        self.deck_cb.blockSignals(False)
        self.deck_id = decks[0]["deck_id"] if decks else None
        if decks:
            self.deck_cb.setCurrentIndex(0)
        self.refresh()

    def _on_deck_selected(self, _index: int) -> None:
        self.deck_id = self.deck_cb.currentData()
        self.refresh()

    def select_deck(self, deck_id: int) -> None:
        """Deck per ID auswaehlen, falls vorhanden (Sitzungswiederherstellung)."""
        idx = self.deck_cb.findData(deck_id)
        if idx >= 0:
            self.deck_cb.setCurrentIndex(idx)

    def _new_deck(self) -> None:
        if not self.repo.exists():
            return
        name, ok = QInputDialog.getText(self, "Neues Deck", "Name des Decks:")
        if ok and name.strip():
            deck_id = ydb.create_deck(self.repo.db_path, name.strip())
            self._reload_decks()
            idx = self.deck_cb.findData(deck_id)
            if idx >= 0:
                self.deck_cb.setCurrentIndex(idx)

    def _open_corpus(self) -> None:
        """Referenz-Decks (Korpus) verwalten -- eigener Dialog, damit sie
        die normale Deck-Auswahl nie verstopfen."""
        if not self.repo.exists():
            return
        ReferenceDeckDialog(self.repo, self).exec()

    def _open_corpus_diff(self) -> None:
        """Aktuelles Deck gegen eine Korpus-/Referenz-Liste vergleichen."""
        if not self.repo.exists() or self.deck_id is None:
            QMessageBox.information(self, "Vergleich", "Kein Deck ausgewählt.")
            return
        if not ydb.list_reference_decks(self.repo.db_path):
            QMessageBox.information(
                self, "Vergleich",
                "Keine Korpus-/Referenz-Listen vorhanden. Importiere welche "
                "über den „Korpus…“-Knopf.",
            )
            return
        DeckCorpusDiffDialog(self.repo, self.deck_id, self).exec()

    def _delete_deck(self) -> None:
        if self.deck_id is None:
            return
        reply = QMessageBox.question(
            self, "Deck löschen",
            f"Deck '{self.deck_cb.currentText()}' löschen?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            ydb.delete_deck(self.repo.db_path, self.deck_id)
            self._reload_decks()

    # -- Import / Export (.ydk) ----------------------------------------------

    def _import_deck(self) -> None:
        if not self.repo.exists():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Deck importieren", "", "YGOPro-Deck (*.ydk);;Alle Dateien (*)"
        )
        if not path:
            return
        try:
            text = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError as exc:
            QMessageBox.warning(self, "Import fehlgeschlagen", str(exc))
            return
        default = os.path.splitext(os.path.basename(path))[0]
        name, ok = QInputDialog.getText(
            self, "Deck importieren", "Name des Decks:", text=default
        )
        if not ok or not name.strip():
            return
        deck_id, report = ydb.import_deck_ydk(self.repo.db_path, name.strip(), text)
        if deck_id is None:
            QMessageBox.warning(
                self, "Import fehlgeschlagen",
                "Keine der Karten wurde in der Datenbank gefunden. "
                "Eventuell hilft ein Daten-Update (yugioh_db.py build).",
            )
            return
        lines = _import_report_lines(report)
        self._reload_decks()
        idx = self.deck_cb.findData(deck_id)
        if idx >= 0:
            self.deck_cb.setCurrentIndex(idx)
        if len(lines) > 1:
            QMessageBox.information(self, "Deck importiert", "\n\n".join(lines))
        else:
            self.status.setText(lines[0] + "  " + self.status.text())

    def _export_deck(self) -> None:
        """Deck als .ydk (Passcodes), lesbare Liste (.txt/.pdf) oder als
        KI-tauglicher Markdown-Block (.md) exportieren."""
        if self.deck_id is None:
            return
        suggested = self.deck_cb.currentText().strip() or "deck"
        path, selected = QFileDialog.getSaveFileName(
            self, "Deck exportieren", suggested + ".ydk",
            "YGOPro-Deck (*.ydk);;Textdatei (*.txt);;PDF-Datei (*.pdf);;"
            "Markdown für KI (*.md)",
        )
        if not path:
            return
        path, ext = _resolve_export_path(
            path, selected,
            {"YGOPro": ".ydk", "Text": ".txt", "PDF": ".pdf", "Markdown": ".md"},
            default_ext=".ydk",
        )
        db, did = self.repo.db_path, self.deck_id
        try:
            if ext == ".ydk":
                _write_text_file(path, ydb.export_deck_ydk(db, did))
            elif ext == ".md":
                _write_text_file(path, ydb.export_deck_markdown(db, did))
            elif ext == ".pdf":
                _write_text_pdf(path, ydb.export_deck_text(db, did))
            else:
                _write_text_file(path, ydb.export_deck_text(db, did))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Export fehlgeschlagen", str(exc))
            return
        self.status.setText(f"Deck exportiert nach {path}")

    # -- Anzeige ------------------------------------------------------------

    def open_card_detail(self, card_id: int) -> None:
        """Read-only Detail-Pop-up einer Karte (Doppelklick in einer Zone);
        eine je DeckView wiederverwendete Instanz, refresht nach DE-Bearbeitung
        die Zonenlisten."""
        if self._detail_dialog is None:
            self._detail_dialog = CardDetailDialog(self.repo, self)
            self._detail_dialog.translation_changed.connect(self.refresh)
        self._detail_dialog.load(card_id)
        self._detail_dialog.show()
        self._detail_dialog.raise_()
        self._detail_dialog.activateWindow()

    def refresh(self) -> None:
        if self.deck_id is None:
            for p in self.panels.values():
                p.list.clear()
            self.status.setText(
                "Kein Deck ausgewählt. Lege über 'Neues Deck' eines an."
            )
            self._refresh_consistency()
            self._refresh_plan()
            self._refresh_combos()
            self._refresh_simulator()
            return
        # Sammlung<->Deck-Abgleich: fehlende Kopien je Karte (andere Decks
        # binden Bestand). Nur Warnung, blockiert nichts.
        availability = ydb.deck_availability(self.repo.db_path, self.deck_id)
        shortages = {
            a["card_id"]: a["missing"] for a in availability if a["missing"]
        }
        for zone, panel in self.panels.items():
            rows = ydb.deck_cards(self.repo.db_path, self.deck_id, zone)
            total = panel.populate(rows, shortages)
            panel.setTitle(f"{ZONE_LABELS[zone]}  ({total})")
        checks = ydb.validate_deck(self.repo.db_path, self.deck_id)
        if shortages:
            copies = sum(shortages.values())
            checks.append(
                (False, f"Bestand: {copies} Kopie(n) fehlen "
                        f"({len(shortages)} Karten)")
            )
        else:
            checks.append((True, "Bestand gedeckt"))
        parts = [("\u2713 " if ok else "\u2717 ") + txt for ok, txt in checks]
        self.status.setText("     ".join(parts))
        self._refresh_consistency()
        self._refresh_plan()
        self._refresh_combos()
        self._refresh_suggestions()
        self._refresh_simulator()

    def _export_combos(self) -> None:
        """Kombo-Linien des Decks als .txt oder .pdf speichern."""
        if self.deck_id is None:
            return
        suggested = (self.deck_cb.currentText().strip() or "deck") + "-kombos"
        path, selected = QFileDialog.getSaveFileName(
            self, "Kombo-Linien exportieren", suggested + ".txt",
            "Textdatei (*.txt);;PDF-Datei (*.pdf)",
        )
        if not path:
            return
        path, ext = _resolve_export_path(
            path, selected, {"PDF": ".pdf", "Text": ".txt"}, default_ext=".txt"
        )
        try:
            text = ydb.export_deck_combos_text(self.repo.db_path, self.deck_id)
            if ext == ".pdf":
                _write_text_pdf(path, text)
            else:
                _write_text_file(path, text)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Export fehlgeschlagen", str(exc))
            return
        self.status.setText(f"Kombo-Linien exportiert nach {path}")

    # -- Kombo-Hilfe --------------------------------------------------------

    def _refresh_consistency(self) -> None:
        """Zieh-Wahrscheinlichkeiten der Starthand (aus den Kombo-Rollen)."""
        if self.deck_id is None or not self.repo.exists():
            self.consistency.setText("")
            return
        stats = ydb.deck_consistency(self.repo.db_path, self.deck_id)
        if stats["deck_size"] == 0:
            self.consistency.setText("")
            return
        roles = stats["roles"]
        if not roles.get("starter"):
            self.consistency.setText(
                "Konsistenz: keine Starter im Main Deck — Rollen der "
                "Kombo-Bausteine im Tab 'Kombos' vergeben."
            )
            return
        lines = [
            "Kopien im Main: " + "  ·  ".join(
                f"{_ROLE_DE[r]} {roles[r]}" for r in ydb.COMBO_ROLES if roles.get(r)
            )
        ]
        for hand, p in stats["hands"].items():
            line = f"Starthand {hand}: ≥1 Starter {_pct(p['starter'])}"
            if roles.get("handtrap"):
                line += f"  ·  ≥1 Handtrap {_pct(p['handtrap'])}"
            line += f"  ·  Brick {_pct(p['brick'])}"
            lines.append(line)
        self.consistency.setText("\n".join(lines))

    @staticmethod
    def _add_plan_header(lst: QListWidget, text: str) -> None:
        item = QListWidgetItem(f"— {text} —")
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        font = item.font(); font.setBold(True); item.setFont(font)
        lst.addItem(item)

    @staticmethod
    def _add_plan_note(lst: QListWidget, text: str) -> None:
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        lst.addItem(item)

    def _refresh_plan(self) -> None:
        """Fahrplan: Rollen-Bestand des Decks und Linien je Bossmonster."""
        self.role_summary.clear()
        self.boss_lines.clear()
        if self.deck_id is None or not self.repo.exists():
            return
        summary = ydb.deck_role_summary(self.repo.db_path, self.deck_id)
        if not summary:
            self._add_plan_note(
                self.role_summary,
                "Keine Rollen im Deck — Bausteine im Tab 'Kombos' einstufen.",
            )
        for role in ydb.COMBO_ROLES:
            cards = summary.get(role)
            if not cards:
                continue
            total = sum(c["copies"] for c in cards)
            self._add_plan_header(self.role_summary, f"{_ROLE_DE[role]} ({total})")
            for c in cards:
                self.role_summary.addItem(f"{c['copies']}x  {c['name']}")
        groups = ydb.deck_boss_lines(self.repo.db_path, self.deck_id)
        if not groups:
            self._add_plan_note(self.boss_lines, "Noch keine Kombos angelegt.")
        for g in groups:
            self._add_plan_header(self.boss_lines, g["boss_name"] or "Ohne Boss")
            for line in g["lines"]:
                cov = (f"{line['covered']}/{line['total']}"
                       if line["total"] else "leer")
                item = QListWidgetItem(f"{cov}  {line['name']}")
                item.setData(Qt.ItemDataRole.UserRole, line["combo_id"])
                self.boss_lines.addItem(item)

    def _open_line(self, item: QListWidgetItem) -> None:
        """Doppelklick auf eine Linie: zur Kombo im Kombos-Reiter springen."""
        combo_id = item.data(Qt.ItemDataRole.UserRole)
        if combo_id is None:
            return
        self.helper_tabs.setCurrentIndex(0)
        self._select_combo(combo_id)

    def _refresh_suggestions(self) -> None:
        """Vorschlaege aus dem Synergie-Graphen; die Luecken-Rolle (wenigste
        Kopien im Main Deck) steuert nur die Gruppierung, nie den Score."""
        self.gap_label.setText("")
        self.suggestion_list.clear()
        if self.deck_id is None or not self.repo.exists():
            return
        res = ydb.deck_suggestions(self.repo.db_path, self.deck_id)
        gap = res["gap_role"]
        copies = res["role_copies"].get(gap, 0)
        self.gap_label.setText(
            f"Engpass: {_ROLE_DE[gap]} ({copies} Kopien im Main Deck)"
        )
        if not res["suggestions"]:
            self._add_plan_note(
                self.suggestion_list,
                "Keine Vorschläge — alle bekannten Karten stecken schon im "
                "Deck, oder es fehlen Kombos mit mehreren Bausteinen bzw. "
                "Referenz-Decks (Korpus…).",
            )
            return
        fills_gap = [s for s in res["suggestions"] if gap in s["roles"]]
        others = [s for s in res["suggestions"] if gap not in s["roles"]]
        for header, group in (
            (f"Füllt die Lücke: {_ROLE_DE[gap]}", fills_gap),
            ("Weitere Vorschläge", others),
        ):
            if not group:
                continue
            self._add_plan_header(self.suggestion_list, header)
            for s in group:
                roles = ", ".join(_ROLE_DE[r] for r in s["roles"])
                score = f"{s['score']:.1f}".replace(".", ",")
                item = QListWidgetItem(
                    f"{s['name']}  ({roles or 'uneingestuft'}) — Score {score}"
                )
                tip = [
                    f"in '{r['combo_name']}' zusammen mit {', '.join(r['with'])}"
                    for r in s["reasons"]
                ]
                if s["bridges"]:
                    tip.append("indirekt über " + ", ".join(s["bridges"]))
                if s["corpus"]:
                    partners = ", ".join(
                        f"{l['name']} ({l['decks']})" for l in s["corpus"]
                    )
                    tip.append(
                        f"Korpus ({s['corpus_total']} Referenz-Listen): "
                        f"stärkste Partner im Deck: {partners} "
                        "— (n) = gemeinsame Listen"
                    )
                item.setToolTip("\n".join(tip))
                item.setData(Qt.ItemDataRole.UserRole, s["card_id"])
                self.suggestion_list.addItem(item)

    def _open_suggestion(self, item: QListWidgetItem) -> None:
        """Doppelklick auf einen Vorschlag: Karte im DetailPanel zeigen."""
        card_id = item.data(Qt.ItemDataRole.UserRole)
        if card_id is None or self.open_card_callback is None:
            return
        self.open_card_callback(card_id)

    # -- Starthand-Simulator ------------------------------------------------

    def _checked_sim_copies(self) -> list[int]:
        """Kopienzahlen der angekreuzten Karten im Starthand-Picker."""
        out = []
        for i in range(self.sim_cards.count()):
            it = self.sim_cards.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(_SIM_COPIES_DATA))
        return out

    def _recompute_prob(self, *_a) -> None:
        """Live-Wahrscheinlichkeit für die angekreuzten Karten (5er/6er)."""
        if self.deck_id is None or not self.repo.exists():
            self.sim_prob.setText("")
            return
        copies = self._checked_sim_copies()
        if not copies:
            self.sim_prob.setText("Karten ankreuzen für die Wahrscheinlichkeit.")
            return
        size = ydb.deck_counts(self.repo.db_path, self.deck_id)["main"]
        if self.sim_mode_cb.currentData():           # AND
            p5 = ydb.prob_open_all(size, copies, 5)
            p6 = ydb.prob_open_all(size, copies, 6)
            label = "alle zusammen"
        else:                                        # OR
            total = sum(copies)
            p5 = ydb.hypergeom_at_least(size, total, 5)
            p6 = ydb.hypergeom_at_least(size, total, 6)
            label = "mindestens eine"
        noun = "Karte" if len(copies) == 1 else "Karten"
        self.sim_prob.setText(
            f"P({len(copies)} {noun}, {label}):  "
            f"5er {_pct(p5)}  ·  6er {_pct(p6)}"
        )

    def _fill_from_combo(self) -> None:
        """Bausteine der im Reiter 'Kombos' gewählten Kombo im Picker ankreuzen
        (nur die, die auch im Main Deck liegen)."""
        item = self.combo_list.currentItem()
        if item is None:
            self.sim_prob.setText("Erst im Reiter 'Kombos' eine Kombo wählen.")
            return
        combo_id = item.data(Qt.ItemDataRole.UserRole)
        piece_ids = {
            p["card_id"] for p in ydb.combo_cards(self.repo.db_path, combo_id)
        }
        self.sim_cards.blockSignals(True)
        for i in range(self.sim_cards.count()):
            it = self.sim_cards.item(i)
            in_combo = it.data(Qt.ItemDataRole.UserRole) in piece_ids
            it.setCheckState(
                Qt.CheckState.Checked if in_combo else Qt.CheckState.Unchecked
            )
        self.sim_cards.blockSignals(False)
        self._recompute_prob()

    def _draw_hand(self) -> None:
        """Eine zufällige Beispielhand aus dem Main Deck ziehen + Verdikt."""
        if self.deck_id is None or not self.repo.exists():
            return
        drawn = ydb.draw_sample_hand(
            self.repo.db_path, self.deck_id, self.sim_hand_cb.currentData()
        )
        self.sim_hand.clear()
        has_starter = False
        for c in drawn:
            tag = ""
            if c["roles"]:
                tag = "  [" + ", ".join(_ROLE_DE[r] for r in c["roles"]) + "]"
                if "starter" in c["roles"]:
                    has_starter = True
            self.sim_hand.addItem(f"{c['name']}{tag}")
        if not drawn:
            self.sim_verdict.setText("Main Deck ist leer.")
        elif has_starter:
            self.sim_verdict.setText("✓ Hand enthält einen Starter.")
        else:
            self.sim_verdict.setText("✗ Brick: kein Starter in dieser Hand.")

    def _refresh_simulator(self) -> None:
        """Picker aus dem Main Deck neu befüllen; Auswahl/Hand zurücksetzen."""
        self.sim_cards.blockSignals(True)
        self.sim_cards.clear()
        self.sim_hand.clear()
        self.sim_verdict.setText("")
        if self.deck_id is None or not self.repo.exists():
            self.sim_cards.blockSignals(False)
            self.sim_prob.setText("")
            self.sim_draw_btn.setEnabled(False)
            return
        cards = ydb.deck_main_cards(self.repo.db_path, self.deck_id)
        for c in cards:
            label = f"{c['name']}  ({c['copies']}x)"
            if c["roles"]:
                label += "  [" + ", ".join(_ROLE_DE[r] for r in c["roles"]) + "]"
            it = QListWidgetItem(label)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, c["card_id"])
            it.setData(_SIM_COPIES_DATA, c["copies"])
            self.sim_cards.addItem(it)
        self.sim_cards.blockSignals(False)
        self.sim_draw_btn.setEnabled(bool(cards))
        self._recompute_prob()

    def _refresh_combos(self) -> None:
        self.combo_list.clear()
        self.combo_pieces.clear()
        self.combo_steps.clear()
        if self.deck_id is None or not self.repo.exists():
            return
        for c in ydb.combos_for_deck(self.repo.db_path, self.deck_id):
            if c["total"] == 0:
                label = f"{c['name']}  (keine Bausteine)"
            else:
                label = f"{c['name']}  —  {c['covered']}/{c['total']}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, c["combo_id"])
            self.combo_list.addItem(item)

    def _on_combo_selected(self, current: QListWidgetItem, _previous=None) -> None:
        self.combo_pieces.clear()
        self.combo_steps.clear()
        if current is None or self.deck_id is None:
            return
        combo_id = current.data(Qt.ItemDataRole.UserRole)
        cov = ydb.combo_coverage(self.repo.db_path, combo_id, self.deck_id)
        for p in cov["pieces"]:
            mark = "\u2713" if p["missing"] == 0 else "\u2717"
            self.combo_pieces.addItem(
                f"{mark}  {p['name']}   {p['have']}/{p['needed']}"
            )
        steps = ydb.combo_steps(self.repo.db_path, combo_id)
        if steps:
            for s in steps:
                self.combo_steps.addItem(f"{s['step_no']}.  {s['text']}")
        else:
            item = QListWidgetItem("(keine Schritte erfasst)")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.combo_steps.addItem(item)

    def _select_combo(self, combo_id: int) -> bool:
        for i in range(self.combo_list.count()):
            if self.combo_list.item(i).data(Qt.ItemDataRole.UserRole) == combo_id:
                self.combo_list.setCurrentRow(i)
                return True
        return False

    def _add_missing(self) -> None:
        item = self.combo_list.currentItem()
        if item is None or self.deck_id is None:
            return
        combo_id = item.data(Qt.ItemDataRole.UserRole)
        cov = ydb.combo_coverage(self.repo.db_path, combo_id, self.deck_id)
        for p in cov["pieces"]:
            if p["missing"] > 0:
                ydb.add_card_to_deck(
                    self.repo.db_path, self.deck_id, p["card_id"], count=p["missing"]
                )
        self.refresh()
        self._select_combo(combo_id)

    def _new_combo_from_deck(self) -> None:
        """Kombo mit diesem Deck als Heimat-Deck anlegen; Bausteine kommen
        direkt aus den Deck-Karten statt über die Suche."""
        if self.deck_id is None:
            return
        dlg = ComboFromDeckDialog(self.repo, self.deck_id, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        combo_id = ydb.create_combo(
            self.repo.db_path, dlg.combo_name(), deck_id=self.deck_id
        )
        for cid in dlg.selected_card_ids():
            ydb.add_combo_card(self.repo.db_path, combo_id, cid, 1)
        self.refresh()
        if self.open_combo_callback is not None:
            self.open_combo_callback(combo_id)

    # -- Aktionen aus den Zonen ---------------------------------------------

    def adjust(self, zone: str, delta: int) -> None:
        cid = self.panels[zone].selected_card_id()
        if cid is None or self.deck_id is None:
            return
        ydb.change_deck_quantity(self.repo.db_path, self.deck_id, cid, zone, delta)
        self.refresh()

    def remove(self, zone: str) -> None:
        cid = self.panels[zone].selected_card_id()
        if cid is None or self.deck_id is None:
            return
        ydb.remove_deck_card(self.repo.db_path, self.deck_id, cid, zone)
        self.refresh()

    def move(self, from_zone: str, to_zone: str) -> None:
        cid = self.panels[from_zone].selected_card_id()
        if cid is None or self.deck_id is None:
            return
        ydb.move_deck_card(self.repo.db_path, self.deck_id, cid, from_zone, to_zone)
        self.refresh()

    def move_from_side(self) -> None:
        cid = self.panels["side"].selected_card_id()
        if cid is None or self.deck_id is None:
            return
        conn = ydb._connect(self.repo.db_path)
        try:
            card = conn.execute(
                "SELECT type, frame_type FROM cards WHERE id = ?", (cid,)
            ).fetchone()
        finally:
            conn.close()
        if card is None:
            return
        natural = ydb.deck_zone_for(card["frame_type"], card["type"])
        ydb.move_deck_card(self.repo.db_path, self.deck_id, cid, "side", natural)
        self.refresh()

    def add_card_dialog(self, zone: str) -> None:
        if self.deck_id is None:
            return
        dlg = CardSearchDialog(self.repo, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        card_id = dlg.chosen_card_id()
        if card_id is None:
            return
        _added, msg = ydb.add_card_to_deck(
            self.repo.db_path, self.deck_id, card_id, zone
        )
        if msg:
            QMessageBox.information(self, "Deck", msg)
        self.refresh()

    # -- von aussen (Detailansicht der Suche) -------------------------------

    def add_card(self, card_id: int, to_side: bool = False):
        if self.deck_id is None:
            QMessageBox.information(
                self, "Kein Deck",
                "Bitte zuerst im Tab 'Deck' ein Deck anlegen oder auswählen.",
            )
            return (0, "")
        zone = "side" if to_side else None
        added, msg = ydb.add_card_to_deck(
            self.repo.db_path, self.deck_id, card_id, zone
        )
        self.refresh()
        return added, msg


# ---------------------------------------------------------------------------
# Kombo-Bibliothek (eigener Tab zum Anlegen/Bearbeiten)
# ---------------------------------------------------------------------------

# In-App-Kurzreferenz der Kombo-Notation (Langform: KOMBO-NOTATION.md).
# Eingebettet statt aus der Datei gelesen, damit sie auch im gepackten
# Build ohne Repo-Dateien verfuegbar ist.
_NOTATION_MD = """\
### Schritt-Syntax

```
<AKTION> <Karte> (<Quelle>) [Req: <Bedingung>] -> <Folge> -> <Folge> | Lock: <Einschränkung>
```

Nur `<AKTION> <Karte>` ist Pflicht. **Ein Schritt = eine Aktion** — eine
Beschwörung *oder* eine Effekt-Aktivierung samt direkter Auflösung.
Beschwörungsformeln bekommen immer eine eigene Zeile (Tuner zuerst):

```
Synchro: Soul (2) + Bone (4) -> Red Rising (6)
```

- `->` verkettet Kosten → Wirkung → Resultat
- `| Lock: …` für dauerhafte Einschränkungen, die der Schritt auslöst
- `[Req: …]` für Bedingungen, damit der Schritt legal ist
- `(Ort)` = woher die Karte kommt, `(A -> B)` = Bewegung; offensichtliche
  Ziele entfallen (SS → Feld, Add → Hand)
- Doppelpunkt für die konkrete Wahl: `Add 1 Resonator (Deck): Darkness Resonator`
- Kurznamen sind okay, sobald eindeutig — die Bausteinliste ist die Legende

### Keywords

| Kürzel | Bedeutung |
|---|---|
| NS / SS | Normal / Special Summon |
| Act | Zauber/Falle aktivieren |
| Eff, Eff1, Eff2 | (ersten/zweiten) Effekt aktivieren |
| Add | auf die Hand nehmen (Suche) |
| Send / Banish / Mill | verschieben / verbannen / Deck → GY |
| Draw / Discard / Set | ziehen / abwerfen / setzen |
| GY / ED | Graveyard / Extra Deck |
| Lvl | Level, z. B. `Lvl ≤4` |

### Notizen der Kombo

```
Start: benötigte Hand-/Feldkarten
End: Endboard / was die Kombo erreicht
```
"""

# Tokens fuer die dauerhafte Chip-Leiste ueber dem Schritte-Editor:
# (Beschriftung, einzufuegender Text, Cursor-Schritte zurueck, Tooltip).
# Klick fuegt das Token an der Cursor-Position ein; nur Kartennamen werden
# noch von Hand getippt. Die Langform bleibt im "Notation..."-Dialog
# (_NOTATION_MD) -- hier nur die haeufigsten Bausteine kompakt in einer Reihe.
_STEP_TOKENS = [
    ("NS", "NS ", 0, "Normal Summon"),
    ("SS", "SS ", 0, "Special Summon"),
    ("Act", "Act ", 0, "Zauber/Falle aktivieren"),
    ("Eff1", "Eff1 ", 0, "Effekt aktivieren (Eff/Eff1/Eff2)"),
    ("Add", "Add ", 0, "auf die Hand nehmen (Suche)"),
    ("Send", "Send ", 0, "auf den Friedhof legen"),
    ("Banish", "Banish ", 0, "verbannen"),
    ("→", " -> ", 0, "Kette: Kosten → Wirkung → Resultat"),
    ("[Req:]", "[Req: ]", 1, "Bedingung, damit der Schritt legal ist"),
    ("| Lock:", " | Lock: ", 0, "dauerhafte Einschränkung, die der Schritt auslöst"),
]


class ComboPlaybackDialog(QDialog):
    """Spielt die Schritte einer Kombo Schritt fuer Schritt durch (Review/
    Lernen): der aktuelle Schritt ist hervorgehoben (Gold, fett), erledigte
    normal, kommende abgeblendet. Reine Anzeige; Pfeiltasten oder Buttons
    navigieren."""

    _DONE = QColor("#efe9f5")
    _CURRENT = QColor("#d4af37")
    _UPCOMING = QColor("#9b90b5")

    def __init__(self, steps: list[str], title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Durchspielen — {title}")
        self.resize(560, 480)
        self._steps = steps
        self._idx = 0

        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for s in steps:
            QListWidgetItem(s, self.list)
        layout.addWidget(self.list, stretch=1)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Zurück")
        self.next_btn = QPushButton("Weiter ▶")
        self.prev_btn.clicked.connect(self._prev)
        self.next_btn.clicked.connect(self._next)
        self.pos_label = QLabel("")
        self.pos_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.pos_label, stretch=1)
        nav.addWidget(self.next_btn)
        layout.addLayout(nav)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Schließen")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        self._render()

    def _prev(self) -> None:
        if self._idx > 0:
            self._idx -= 1
            self._render()

    def _next(self) -> None:
        if self._idx < len(self._steps) - 1:
            self._idx += 1
            self._render()

    def _render(self) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            font = it.font()
            font.setBold(i == self._idx)
            it.setFont(font)
            it.setForeground(
                self._CURRENT if i == self._idx
                else self._DONE if i < self._idx
                else self._UPCOMING
            )
        self.list.scrollToItem(self.list.item(self._idx))
        self.pos_label.setText(f"Schritt {self._idx + 1}/{len(self._steps)}")
        self.prev_btn.setEnabled(self._idx > 0)
        self.next_btn.setEnabled(self._idx < len(self._steps) - 1)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Right:
            self._next()
        elif event.key() == Qt.Key.Key_Left:
            self._prev()
        else:
            super().keyPressEvent(event)


class ComboView(QWidget):
    def __init__(self, repo: CardRepository):
        super().__init__()
        self.repo = repo
        self.combo_id: int | None = None

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Links: Deck-Filter + Liste + Neu/Loeschen
        left = QWidget()
        lv = QVBoxLayout(left)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Deck:"))
        self.deck_filter_cb = QComboBox()
        self.deck_filter_cb.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.deck_filter_cb, stretch=1)
        lv.addLayout(filter_row)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Suche: Name, Archetyp, Baustein …")
        self.search_edit.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh)
        self.search_edit.textChanged.connect(self._search_timer.start)
        lv.addWidget(self.search_edit)
        lv.addWidget(QLabel("Meine Kombos:"))
        self.combo_list = QListWidget()
        self.combo_list.currentItemChanged.connect(self._on_select)
        lv.addWidget(self.combo_list, stretch=1)
        btn_row = QHBoxLayout()
        new_btn = QPushButton("Neue Kombo")
        new_btn.clicked.connect(self._new_combo)
        var_btn = QPushButton("Neue Variante…")
        var_btn.clicked.connect(self._new_variant)
        del_btn = QPushButton("Löschen")
        del_btn.clicked.connect(self._delete_combo)
        btn_row.addWidget(new_btn)
        btn_row.addWidget(var_btn)
        btn_row.addWidget(del_btn)
        lv.addLayout(btn_row)

        # Rechts: Editor
        self.editor = QWidget()
        rv = QVBoxLayout(self.editor)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.arch_edit = QLineEdit()
        # Boss = Zielmonster der Kombo, gew\u00e4hlt aus den Bausteinen.
        # Wird (wie die Rollen) sofort gespeichert, nicht erst \u00fcber 'Speichern'.
        self.boss_cb = QComboBox()
        self.boss_cb.currentIndexChanged.connect(self._on_boss_changed)
        # Heimat-Deck = optionale Verknuepfung (Filter/Komfort), kein Besitz.
        # Speichert sofort, wie Rollen und Boss.
        self.home_cb = QComboBox()
        self.home_cb.currentIndexChanged.connect(self._on_home_changed)
        # Variante-von: macht diese Kombo zum Branch einer Hauptlinie
        # (Interruption-Verzweigung). Speichert sofort, wie Boss/Heimat-Deck.
        self.parent_cb = QComboBox()
        self.parent_cb.currentIndexChanged.connect(self._on_parent_changed)
        # Notizen tragen die Rahmendaten der Notation (Start/End), die nicht
        # in die Schritte gehoeren.
        self.notes_edit = QTextEdit()
        self.notes_edit.setAcceptRichText(False)
        self.notes_edit.setFixedHeight(56)
        self.notes_edit.setPlaceholderText(
            "Start: benötigte Hand-/Feldkarten\nEnd: Endboard / Ziel"
        )
        form.addRow("Name", self.name_edit)
        form.addRow("Archetyp", self.arch_edit)
        form.addRow("Heimat-Deck", self.home_cb)
        form.addRow("Boss", self.boss_cb)
        form.addRow("Variante von", self.parent_cb)
        form.addRow("Notizen", self.notes_edit)
        rv.addLayout(form)

        rv.addWidget(QLabel("Bausteine:"))
        self.pieces = QListWidget()
        self.pieces.currentItemChanged.connect(self._on_piece_selected)
        rv.addWidget(self.pieces, stretch=1)
        piece_row = QHBoxLayout()
        add_piece_btn = QPushButton("+ Baustein\u2026")
        add_piece_btn.clicked.connect(self._add_piece_dialog)
        minus = QPushButton("\u22121")
        plus = QPushButton("+1")
        rem = QPushButton("Entfernen")
        minus.clicked.connect(lambda: self._adjust_piece(-1))
        plus.clicked.connect(lambda: self._adjust_piece(+1))
        rem.clicked.connect(self._remove_piece)
        piece_row.addWidget(add_piece_btn)
        piece_row.addWidget(minus)
        piece_row.addWidget(plus)
        piece_row.addWidget(rem)
        piece_row.addWidget(QLabel("Rolle:"))
        self.role_cb = QComboBox()
        self.role_cb.addItem("(keine)", None)
        for r in ydb.COMBO_ROLES:
            self.role_cb.addItem(_ROLE_DE[r], r)
        self.role_cb.currentIndexChanged.connect(self._on_role_changed)
        piece_row.addWidget(self.role_cb)
        piece_row.addStretch()
        rv.addLayout(piece_row)

        # Abgleich der Bausteine mit der eigenen Sammlung.
        coll_box = QGroupBox("Mit deiner Sammlung")
        cb_l = QVBoxLayout(coll_box)
        self.coll_status = QLabel("")
        self.coll_status.setWordWrap(True)
        cb_l.addWidget(self.coll_status)
        self.coll_missing = QListWidget()
        cb_l.addWidget(self.coll_missing)
        rv.addWidget(coll_box, stretch=1)

        steps_head = QHBoxLayout()
        steps_head.addWidget(QLabel(
            "Schritte (eine Zeile pro Schritt — speichert automatisch):"
        ))
        steps_head.addStretch()
        play_btn = QPushButton("Durchspielen…")
        play_btn.setToolTip("Die Schritte Schritt für Schritt durchgehen")
        play_btn.clicked.connect(self._play_steps)
        steps_head.addWidget(play_btn)
        notation_btn = QPushButton("Notation…")
        notation_btn.clicked.connect(self._show_notation_help)
        steps_head.addWidget(notation_btn)
        rv.addLayout(steps_head)
        rv.addLayout(self._build_token_bar())
        self.steps_edit = QTextEdit()
        self.steps_edit.setAcceptRichText(False)
        self.steps_edit.setPlaceholderText(
            "<AKTION> <Karte> (<Quelle>) [Req: …] -> <Folge> | Lock: …\n"
            "z. B.  NS Soul -> Eff1: Add 1 Archfiend Lvl ≤4 (Deck): Bone"
        )
        rv.addWidget(self.steps_edit, stretch=1)
        # Notation-Pruefung: nur Hinweis, nie blockierend.
        self.lint_label = QLabel("")
        self.lint_label.setWordWrap(True)
        self.lint_label.setStyleSheet("color: #e6a23c;")
        self.lint_label.setVisible(False)
        rv.addWidget(self.lint_label)

        splitter.addWidget(left)
        splitter.addWidget(self.editor)
        splitter.setSizes([260, 640])
        outer = QVBoxLayout(self)
        outer.addWidget(splitter)

        # Auto-Speichern fuer Name/Archetyp/Schritte: Eingaben markieren die
        # Kombo als "dirty"; gespeichert wird kurz danach (Timer) und immer
        # bevor eine andere Kombo geladen oder die Liste neu aufgebaut wird.
        self._loading = False        # unterdrueckt textChanged beim Befuellen
        self._dirty_combo_id: int | None = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.flush_pending)
        self.name_edit.textChanged.connect(self._on_editor_changed)
        self.arch_edit.textChanged.connect(self._on_editor_changed)
        self.notes_edit.textChanged.connect(self._on_editor_changed)
        self.steps_edit.textChanged.connect(self._on_editor_changed)
        self.steps_edit.textChanged.connect(self._update_lint)

        self.editor.setEnabled(False)
        self.refresh()

    # -- Liste / Auswahl ----------------------------------------------------

    def refresh(self) -> None:
        self.flush_pending()  # offene Eingaben sichern, bevor neu geladen wird
        keep = self.combo_id
        self._reload_deck_filter()
        self.combo_list.blockSignals(True)
        self.combo_list.clear()
        combos = (
            ydb.list_combos(
                self.repo.db_path, self.deck_filter_cb.currentData(),
                text=self.search_edit.text(),
            )
            if self.repo.exists() else []
        )
        for c in combos:
            item = QListWidgetItem(self._combo_label(c))
            item.setData(Qt.ItemDataRole.UserRole, c["combo_id"])
            self.combo_list.addItem(item)
            # Varianten (Branches) eingerückt direkt unter der Hauptlinie.
            for v in ydb.combo_variants(self.repo.db_path, c["combo_id"]):
                vitem = QListWidgetItem(self._variant_label(v))
                vitem.setData(Qt.ItemDataRole.UserRole, v["combo_id"])
                self.combo_list.addItem(vitem)
        self.combo_list.blockSignals(False)
        if keep is not None and self._select_combo(keep):
            return
        # Aktive Kombo fiel aus dem Filter (oder es gibt keine): Editor leeren,
        # damit Eingaben nicht versehentlich eine unsichtbare Kombo treffen.
        self.combo_id = None
        self._clear_editor()
        self.editor.setEnabled(False)

    def _reload_deck_filter(self) -> None:
        """Deck-Filter neu aufbauen (Decks koennen sich geaendert haben);
        die aktuelle Auswahl bleibt erhalten."""
        keep = self.deck_filter_cb.currentData()
        self.deck_filter_cb.blockSignals(True)
        self.deck_filter_cb.clear()
        self.deck_filter_cb.addItem("(alle)", None)
        self.deck_filter_cb.addItem("(ohne Heimat-Deck)", 0)
        decks = ydb.list_decks(self.repo.db_path) if self.repo.exists() else []
        for d in decks:
            self.deck_filter_cb.addItem(d["name"], d["deck_id"])
        idx = self.deck_filter_cb.findData(keep)
        self.deck_filter_cb.setCurrentIndex(max(idx, 0))
        self.deck_filter_cb.blockSignals(False)

    def _on_filter_changed(self, _index: int) -> None:
        self.refresh()

    def focus_combo(self, combo_id: int) -> None:
        """Von aussen (Deck-Tab): Kombo anzeigen und auswaehlen; steht sie
        nicht im aktuellen Filter, wird er auf '(alle)' zurueckgesetzt."""
        self.refresh()
        if not self._select_combo(combo_id):
            self.deck_filter_cb.blockSignals(True)
            self.deck_filter_cb.setCurrentIndex(0)  # "(alle)"
            self.deck_filter_cb.blockSignals(False)
            self.refresh()
            self._select_combo(combo_id)

    def _combo_label(self, combo) -> str:
        """Listentext einer Kombo inkl. Heimat-Deck und Baubarkeit aus der
        Sammlung (✓ = vollständig baubar, sonst 'vorhanden/gesamt')."""
        arch = f"   [{combo['archetype']}]" if combo["archetype"] else ""
        deck = f"   · {combo['deck_name']}" if combo["deck_name"] else ""
        cov = ydb.combo_coverage_collection(self.repo.db_path, combo["combo_id"])
        if cov["total"] == 0:
            mark = ""
        elif cov["covered"] == cov["total"]:
            mark = "   ✓"
        else:
            mark = f"   ({cov['covered']}/{cov['total']})"
        return f"{combo['name']}{arch}{deck}{mark}"

    @staticmethod
    def _variant_label(combo) -> str:
        """Eingerückter Listentext einer Variante (Name + Archetyp)."""
        arch = f"   [{combo['archetype']}]" if combo["archetype"] else ""
        return f"    ↳ {combo['name']}{arch}"

    def _select_combo(self, combo_id: int) -> bool:
        for i in range(self.combo_list.count()):
            if self.combo_list.item(i).data(Qt.ItemDataRole.UserRole) == combo_id:
                self.combo_list.setCurrentRow(i)
                return True
        return False

    def _update_current_label(self) -> None:
        """Aktualisiert nur den Listeneintrag der aktiven Kombo (ohne die
        Liste neu aufzubauen, damit ungespeicherte Editor-Eingaben bleiben)."""
        item = self.combo_list.currentItem()
        if item is None or self.combo_id is None:
            return
        combo = ydb.get_combo(self.repo.db_path, self.combo_id)
        if combo is None:
            return
        if combo["parent_combo_id"] is not None:
            item.setText(self._variant_label(combo))
        else:
            item.setText(self._combo_label(combo))

    def _refresh_collection_coverage(self) -> None:
        """Zeigt, welche Bausteine der aktiven Kombo schon im Bestand sind."""
        self.coll_missing.clear()
        if self.combo_id is None:
            self.coll_status.setText("")
            return
        cov = ydb.combo_coverage_collection(self.repo.db_path, self.combo_id)
        if cov["total"] == 0:
            self.coll_status.setText("Noch keine Bausteine festgelegt.")
            return
        if cov["covered"] == cov["total"]:
            self.coll_status.setText(
                f"✓ Vollständig baubar – alle {cov['total']} Bausteine im Bestand."
            )
        else:
            self.coll_status.setText(
                f"{cov['covered']} von {cov['total']} Bausteinen im Bestand."
            )
        for p in cov["pieces"]:
            if p["missing"] > 0:
                self.coll_missing.addItem(
                    f"{p['missing']}× fehlt – {p['name']}  ({p['have']}/{p['needed']})"
                )

    def _on_select(self, current: QListWidgetItem, _previous=None) -> None:
        # Erst offene Eingaben der vorigen Kombo sichern -- die Felder zeigen
        # an dieser Stelle noch deren Inhalt.
        self.flush_pending()
        if current is None:
            self.combo_id = None
            self._clear_editor()
            self.editor.setEnabled(False)
            return
        self.combo_id = current.data(Qt.ItemDataRole.UserRole)
        combo = ydb.get_combo(self.repo.db_path, self.combo_id)
        self._loading = True
        self.name_edit.setText(combo["name"] or "")
        self.arch_edit.setText(combo["archetype"] or "")
        self.notes_edit.setPlainText(combo["notes"] or "")
        steps = ydb.combo_steps(self.repo.db_path, self.combo_id)
        self.steps_edit.setPlainText("\n".join(s["text"] for s in steps))
        self._loading = False
        self._update_lint()
        self._populate_home_deck(combo)
        self._populate_parent(combo)
        self._load_pieces()
        self._refresh_collection_coverage()
        self.editor.setEnabled(True)

    def _clear_editor(self) -> None:
        self._loading = True
        self.name_edit.clear()
        self.arch_edit.clear()
        self.boss_cb.blockSignals(True)
        self.boss_cb.clear()
        self.boss_cb.blockSignals(False)
        self.home_cb.blockSignals(True)
        self.home_cb.clear()
        self.home_cb.blockSignals(False)
        self.parent_cb.blockSignals(True)
        self.parent_cb.clear()
        self.parent_cb.blockSignals(False)
        self.pieces.clear()
        self.notes_edit.clear()
        self.steps_edit.clear()
        self.lint_label.clear()
        self.lint_label.setVisible(False)
        self.coll_status.clear()
        self.coll_missing.clear()
        self._loading = False

    def _after_piece_change(self) -> None:
        """Nach Änderung der Bausteine: Liste, Sammlungs-Abgleich und den
        Baubarkeit-Marker der aktiven Kombo aktualisieren."""
        self._load_pieces()
        self._refresh_collection_coverage()
        self._update_current_label()

    @staticmethod
    def _piece_label(p) -> str:
        role = f"   [{_ROLE_DE[p['role']]}]" if p["role"] else ""
        return f"{p['quantity']}x  {p['name']}{role}"

    def _load_pieces(self) -> None:
        self.pieces.clear()
        if self.combo_id is None:
            return
        pieces = ydb.combo_cards(self.repo.db_path, self.combo_id)
        for p in pieces:
            item = QListWidgetItem(self._piece_label(p))
            item.setData(Qt.ItemDataRole.UserRole, p["card_id"])
            item.setData(_PIECE_ROLE_DATA, p["role"])
            self.pieces.addItem(item)
        self._populate_boss(pieces)

    def _populate_boss(self, pieces) -> None:
        """Boss-Auswahl aus den Bausteinen neu aufbauen; gespeicherten Boss
        auch dann anzeigen, wenn er (nicht mehr) unter den Bausteinen ist."""
        combo = ydb.get_combo(self.repo.db_path, self.combo_id)
        boss_id = combo["boss_card_id"] if combo else None
        self.boss_cb.blockSignals(True)
        self.boss_cb.clear()
        self.boss_cb.addItem("(kein Boss)", None)
        for p in pieces:
            self.boss_cb.addItem(p["name"], p["card_id"])
        if boss_id is not None:
            idx = self.boss_cb.findData(boss_id)
            if idx < 0:
                self.boss_cb.addItem(combo["boss_name"] or str(boss_id), boss_id)
                idx = self.boss_cb.count() - 1
            self.boss_cb.setCurrentIndex(idx)
        self.boss_cb.blockSignals(False)

    def _on_boss_changed(self, _index: int) -> None:
        if self.combo_id is None:
            return
        ydb.set_combo_boss(
            self.repo.db_path, self.combo_id, self.boss_cb.currentData()
        )

    def _populate_home_deck(self, combo) -> None:
        """Heimat-Deck-Auswahl mit allen Decks fuellen und auf den
        gespeicherten Wert stellen, ohne ein Speichern auszuloesen."""
        self.home_cb.blockSignals(True)
        self.home_cb.clear()
        self.home_cb.addItem("(keines)", None)
        for d in ydb.list_decks(self.repo.db_path):
            self.home_cb.addItem(d["name"], d["deck_id"])
        if combo is not None and combo["deck_id"] is not None:
            idx = self.home_cb.findData(combo["deck_id"])
            self.home_cb.setCurrentIndex(max(idx, 0))
        self.home_cb.blockSignals(False)

    def _on_home_changed(self, _index: int) -> None:
        if self.combo_id is None:
            return
        ydb.set_combo_deck(
            self.repo.db_path, self.combo_id, self.home_cb.currentData()
        )
        # Listentext sofort nachziehen; faellt die Kombo damit aus dem
        # aktiven Filter, raeumt erst der naechste refresh() auf.
        self._update_current_label()

    def _populate_parent(self, combo) -> None:
        """'Variante von'-Auswahl füllen: alle Hauptlinien außer dieser. Hat
        die Kombo selbst Varianten, kann sie keine Variante werden -> Feld
        gesperrt auf '(keine)'."""
        self.parent_cb.blockSignals(True)
        self.parent_cb.clear()
        self.parent_cb.addItem("(keine — Hauptlinie)", None)
        has_children = bool(ydb.combo_variants(self.repo.db_path, self.combo_id))
        if has_children:
            self.parent_cb.setEnabled(False)
            self.parent_cb.setCurrentIndex(0)
            self.parent_cb.blockSignals(False)
            return
        self.parent_cb.setEnabled(True)
        for c in ydb.list_combos(self.repo.db_path):  # nur Hauptlinien
            if c["combo_id"] != self.combo_id:
                self.parent_cb.addItem(c["name"], c["combo_id"])
        if combo is not None and combo["parent_combo_id"] is not None:
            idx = self.parent_cb.findData(combo["parent_combo_id"])
            if idx < 0:  # Heimat-Deck-Filter o.ä. -- Parent trotzdem zeigen
                self.parent_cb.addItem(
                    combo["parent_name"] or "?", combo["parent_combo_id"]
                )
                idx = self.parent_cb.count() - 1
            self.parent_cb.setCurrentIndex(idx)
        self.parent_cb.blockSignals(False)

    def _on_parent_changed(self, _index: int) -> None:
        if self.combo_id is None:
            return
        try:
            ydb.set_combo_parent(
                self.repo.db_path, self.combo_id, self.parent_cb.currentData()
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Variante", str(exc))
        # Nesting in der Liste neu aufbauen, Kombo wieder auswählen.
        cid = self.combo_id
        self.refresh()
        self._select_combo(cid)

    def _on_piece_selected(self, current: QListWidgetItem, _previous=None) -> None:
        # Rollen-Dropdown auf den gewählten Baustein stellen, ohne dabei
        # ein Speichern auszulösen.
        self.role_cb.blockSignals(True)
        if current is None:
            self.role_cb.setCurrentIndex(0)
        else:
            idx = self.role_cb.findData(current.data(_PIECE_ROLE_DATA))
            self.role_cb.setCurrentIndex(max(idx, 0))
        self.role_cb.blockSignals(False)

    def _on_role_changed(self, _index: int) -> None:
        item = self.pieces.currentItem()
        if item is None or self.combo_id is None:
            return
        card_id = item.data(Qt.ItemDataRole.UserRole)
        role = self.role_cb.currentData()
        ydb.set_combo_card_role(self.repo.db_path, self.combo_id, card_id, role)
        # Nur den betroffenen Eintrag aktualisieren, damit die Auswahl bleibt.
        item.setData(_PIECE_ROLE_DATA, role)
        p = next(
            (p for p in ydb.combo_cards(self.repo.db_path, self.combo_id)
             if p["card_id"] == card_id),
            None,
        )
        if p is not None:
            item.setText(self._piece_label(p))

    # -- Aktionen -----------------------------------------------------------

    def _new_combo(self) -> None:
        if not self.repo.exists():
            return
        name, ok = QInputDialog.getText(self, "Neue Kombo", "Name der Kombo:")
        if ok and name.strip():
            # Ist der Filter auf ein Deck gestellt, wird es direkt Heimat-Deck
            # (sonst waere die neue Kombo im Filter unsichtbar).
            deck_id = self.deck_filter_cb.currentData() or None
            combo_id = ydb.create_combo(
                self.repo.db_path, name.strip(), deck_id=deck_id
            )
            self.refresh()
            self._select_combo(combo_id)

    def _new_variant(self) -> None:
        """Variante (Branch) der aktuell gewählten Hauptlinie anlegen."""
        if self.combo_id is None or not self.repo.exists():
            return
        parent = ydb.get_combo(self.repo.db_path, self.combo_id)
        if parent["parent_combo_id"] is not None:
            QMessageBox.information(
                self, "Neue Variante",
                "Varianten hängen an einer Hauptlinie. Wähle zuerst die "
                "Hauptlinie aus (nicht eine ihrer Varianten).",
            )
            return
        name, ok = QInputDialog.getText(
            self, "Neue Variante", "Name der Variante:",
            text=f"{parent['name']} – Variante",
        )
        if not (ok and name.strip()):
            return
        parent_id = self.combo_id
        # Variante erbt das Heimat-Deck der Hauptlinie (bleibt im Filter sichtbar).
        child = ydb.create_combo(
            self.repo.db_path, name.strip(), deck_id=parent["deck_id"]
        )
        ydb.set_combo_parent(self.repo.db_path, child, parent_id)
        self.refresh()
        self._select_combo(child)

    def _delete_combo(self) -> None:
        if self.combo_id is None:
            return
        variants = ydb.combo_variants(self.repo.db_path, self.combo_id)
        msg = f"Kombo '{self.name_edit.text()}' löschen?"
        if variants:
            msg += (
                f"\n\nAchtung: {len(variants)} Variante(n) hängen daran und "
                "werden mitgelöscht."
            )
        reply = QMessageBox.question(self, "Kombo löschen", msg)
        if reply == QMessageBox.StandardButton.Yes:
            # Offene Eingaben verwerfen -- sie gehoeren zur geloeschten Kombo.
            self._save_timer.stop()
            self._dirty_combo_id = None
            ydb.delete_combo(self.repo.db_path, self.combo_id)
            self.combo_id = None
            self.refresh()

    # -- Auto-Speichern (Name/Archetyp/Schritte) ------------------------------

    def _on_editor_changed(self, *_args) -> None:
        if self._loading or self.combo_id is None:
            return
        self._dirty_combo_id = self.combo_id
        self._save_timer.start()

    def flush_pending(self) -> None:
        """Sichert offene Editor-Eingaben. Wird vom Timer, vor jedem Laden
        einer anderen Kombo und beim Schliessen des Fensters aufgerufen."""
        self._save_timer.stop()
        if self._dirty_combo_id is None:
            return
        combo_id, self._dirty_combo_id = self._dirty_combo_id, None
        ydb.update_combo(
            self.repo.db_path, combo_id,
            name=self.name_edit.text().strip() or "Unbenannt",
            archetype=self.arch_edit.text().strip() or None,
            notes=self.notes_edit.toPlainText().strip() or None,
        )
        lines = [
            ln.strip() for ln in self.steps_edit.toPlainText().splitlines()
            if ln.strip()
        ]
        ydb.set_combo_steps(self.repo.db_path, combo_id, lines)
        # Listentext nachziehen (Name/Archetyp koennen sich geaendert haben);
        # nicht ueber currentItem, denn die Auswahl kann schon weiter sein.
        for i in range(self.combo_list.count()):
            item = self.combo_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == combo_id:
                combo = ydb.get_combo(self.repo.db_path, combo_id)
                if combo is not None:
                    item.setText(self._combo_label(combo))
                break

    # -- Notation (Hilfe + beratende Pruefung) --------------------------------

    def _update_lint(self) -> None:
        """Prueft die Schritte gegen die Kombo-Notation. Nur ein Hinweis
        unter dem Editor -- gespeichert wird immer."""
        lines = [
            ln.strip() for ln in self.steps_edit.toPlainText().splitlines()
            if ln.strip()
        ]
        warnings = ydb.lint_combo_steps(lines)
        if warnings:
            shown = warnings[:4]
            if len(warnings) > len(shown):
                shown.append(f"… und {len(warnings) - len(shown)} weitere")
            self.lint_label.setText("⚠ " + "\n⚠ ".join(shown))
        self.lint_label.setVisible(bool(warnings))

    def _build_token_bar(self) -> QHBoxLayout:
        """Kompakte, dauerhaft sichtbare Leiste mit Notations-Tokens. Klick
        fuegt das Token an der Cursor-Position im Schritte-Editor ein -- die
        Leiste ist die Legende, der 'Notation...'-Dialog die Langform."""
        bar = QHBoxLayout()
        bar.setSpacing(4)
        lbl = QLabel("Notation:")
        lbl.setStyleSheet("color: #9b90b5;")
        bar.addWidget(lbl)
        for label, text, back, tip in _STEP_TOKENS:
            chip = QPushButton(label)
            chip.setToolTip(tip)
            # Fokus nicht stehlen, damit der Cursor im Editor sichtbar bleibt.
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            chip.setStyleSheet("padding: 1px 6px;")
            chip.clicked.connect(
                lambda _=False, t=text, b=back: self._insert_token(t, b)
            )
            bar.addWidget(chip)
        bar.addStretch()
        return bar

    def _insert_token(self, text: str, back: int = 0) -> None:
        """Token an der aktuellen Cursor-Position im Schritte-Editor einfuegen;
        'back' setzt den Cursor n Zeichen nach links (z.B. in '[Req: ]')."""
        cursor = self.steps_edit.textCursor()
        cursor.insertText(text)
        if back:
            cursor.movePosition(
                QTextCursor.MoveOperation.Left,
                QTextCursor.MoveMode.MoveAnchor, back,
            )
            self.steps_edit.setTextCursor(cursor)
        self.steps_edit.setFocus()

    def _play_steps(self) -> None:
        """Die Schritte der aktuellen Kombo Schritt fuer Schritt durchspielen
        (aus dem Editor, inkl. ungespeicherter Zeilen)."""
        steps = [
            line.strip()
            for line in self.steps_edit.toPlainText().splitlines()
            if line.strip()
        ]
        if not steps:
            QMessageBox.information(
                self, "Durchspielen", "Diese Kombo hat noch keine Schritte."
            )
            return
        title = self.name_edit.text().strip() or "Kombo"
        ComboPlaybackDialog(steps, title, self).exec()

    def _show_notation_help(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Kombo-Notation")
        layout = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setMarkdown(_NOTATION_MD)
        layout.addWidget(text)
        close_btn = QPushButton("Schließen")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        dlg.resize(560, 520)
        dlg.exec()

    def _adjust_piece(self, delta: int) -> None:
        item = self.pieces.currentItem()
        if item is None or self.combo_id is None:
            return
        card_id = item.data(Qt.ItemDataRole.UserRole)
        current = next(
            (p["quantity"] for p in ydb.combo_cards(self.repo.db_path, self.combo_id)
             if p["card_id"] == card_id),
            None,
        )
        if current is None:
            return
        ydb.set_combo_card_quantity(
            self.repo.db_path, self.combo_id, card_id, current + delta
        )
        self._after_piece_change()

    def _remove_piece(self) -> None:
        item = self.pieces.currentItem()
        if item is None or self.combo_id is None:
            return
        card_id = item.data(Qt.ItemDataRole.UserRole)
        ydb.remove_combo_card(self.repo.db_path, self.combo_id, card_id)
        self._after_piece_change()

    def _add_piece_dialog(self) -> None:
        """Baustein direkt hier suchen und hinzufügen (ohne Tab-Wechsel)."""
        if self.combo_id is None:
            return
        dlg = CardSearchDialog(self.repo, self)
        dlg.setWindowTitle("Baustein hinzufügen")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        card_id = dlg.chosen_card_id()
        if card_id is None:
            return
        ydb.add_combo_card(self.repo.db_path, self.combo_id, card_id, 1)
        self._after_piece_change()

    # -- von aussen (Detailansicht der Suche) -------------------------------

    def add_piece(self, card_id: int) -> None:
        if self.combo_id is None:
            QMessageBox.information(
                self, "Keine Kombo",
                "Bitte zuerst im Tab 'Kombos' eine Kombo anlegen oder auswählen.",
            )
            return
        ydb.add_combo_card(self.repo.db_path, self.combo_id, card_id, 1)
        self._after_piece_change()


# ---------------------------------------------------------------------------
# Benutzerhandbuch (eigener Tab)
# ---------------------------------------------------------------------------

# Das Handbuch ist eingebettet (nicht aus einer Datei gelesen), damit es auch
# im gepackten Build ohne Repo-Dateien vollstaendig vorliegt -- gleiche
# Begruendung wie bei _NOTATION_MD. Jeder Eintrag: (Titel, Markdown). Die
# Reihenfolge ist die Lese-Reihenfolge; sie folgt den Tabs der App.
_MANUAL_SECTIONS: list[tuple[str, str]] = [
    ("Überblick & erste Schritte", """\
# Überblick

Diese App verwaltet deine **Yu-Gi-Oh!-Sammlung**, hilft beim **Deckbuilding**
und pflegt eine eigene **Kombo-Bibliothek**. Sie läuft eigenständig auf deinem
Rechner; die Kartendaten werden einmalig aus dem Internet geladen und danach
lokal gehalten — **danach arbeitet die App offline**.

## Die fünf Tabs

| Tab | Wofür |
|---|---|
| **Suche** | Karten finden, Details ansehen, in Sammlung/Deck/Kombo übernehmen |
| **Sammlung** | dein physischer Kartenbestand |
| **Deck** | Decks bauen, prüfen, importieren/exportieren, Kombo-Hilfe |
| **Kombos** | Kombo-Linien dokumentieren (Bausteine, Rollen, Schritte) |
| **Handbuch** | diese Hilfe |

## Beim allerersten Start

Ist noch keine Kartendatenbank vorhanden, lädst du sie über das Menü
**Daten → Kartendaten aktualisieren…** herunter (braucht einmalig Internet).
Danach füllen sich Such-Filter und Listen automatisch.

> **Deine Daten sind getrennt von den Kartendaten.** Sammlung, Decks,
> Übersetzungen und Kombos bleiben bei jedem Karten- oder App-Update erhalten.
"""),

    ("Suche-Tab", """\
# Suche

Der Suche-Tab hat drei Spalten (frei per Trennbalken verschiebbar):
**Filter** links, **Trefferliste** in der Mitte, **Detailansicht** rechts.

## Suchen & filtern

- **Textfeld oben:** durchsucht **Name und Kartentext** (Volltext).
- **Filter:** Typ, Attribut, Archetyp, Level/Rank, ATK von–bis.
  Jede Änderung löst sofort eine neue Suche aus.
- **„Nur meine Sammlung":** beschränkt die Treffer auf Karten, die du besitzt.
- Die Trefferzahl steht unten links.

## Detailansicht (rechts)

Zeigt Bild, Werte und Kartentext der gewählten Karte. Das Bild wird beim
ersten Aufruf einmal lokal zwischengespeichert.

- **✎ DE** — eigene **deutsche Übersetzung** für Name und/oder Kartentext
  hinterlegen. Leere Felder lassen die Originaldaten unangetastet. Deine
  Übersetzungen überleben Kartendaten-Updates.
- **Sammlung → Hinzufügen** — legt die Karte in deinen Bestand (gleiche Drucke
  werden zusammengeführt, nicht dupliziert).
- **Deck → + Deck / + Side** — fügt die Karte dem **aktiven Deck** in die
  passende Zone bzw. ins Side Deck hinzu.
- **Kombo → + als Baustein zur aktiven Kombo** — übernimmt die Karte als
  Baustein der gerade im Kombos-Tab geöffneten Kombo.
"""),

    ("Sammlung-Tab", """\
# Sammlung

Hier steht dein **physischer Kartenbestand** — was du tatsächlich besitzt.

- **Filter:** nach Name, Attribut und Archetyp eingrenzen.
- **Exportieren…** — speichert die Sammlung als lesbare Liste (`.txt`/`.pdf`)
  oder als **Markdown für KI** (`.md`, jede Karte mit vollem Effekttext und
  Menge). Sind Filter aktiv, wird nur die gefilterte Ansicht exportiert,
  sonst die gesamte Sammlung.
- **Aktualisieren** — Liste neu laden.
- **Ausgewählten Eintrag entfernen** — nimmt den markierten Bestand heraus.

Identische Drucke einer Karte werden **zusammengeführt** statt mehrfach
gelistet. Neue Karten kommen am einfachsten über den **Suche-Tab**
(„Hinzufügen") in die Sammlung.

> Die Sammlung ist die Grundlage für die Baubarkeits-Anzeigen in Deck- und
> Kombo-Tab („besitzt du schon / müsstest du holen").
"""),

    ("Deck-Tab", """\
# Deck

Links die **Deckliste**, rechts der Deck-Inhalt mit den drei Zonen
**Main / Extra / Side** und der **Kombo-Hilfe**.

## Decks verwalten

- **Neues Deck / Deck löschen** — Decks anlegen und entfernen.
- **Karten hinzufügen:** im Suche-Tab über **+ Deck / + Side**, oder per
  **+ Karte hinzufügen** im Deck-Tab.
- **−1 / +1 / Entfernen** — Kopienzahl der markierten Karte ändern.
- **→ Deck / → Side** — Karte zwischen Main/Extra und Side verschieben.

**Automatik & Regeln:**

- Die **Zone** (Main oder Extra) wird automatisch aus dem Kartentyp bestimmt
  (Fusion/Synchro/Xyz/Link → Extra Deck).
- Die **3-Kopien-Regel** gilt über alle Zonen zusammen und wird erzwungen.
- **⚠ fehlt n** markiert Karten, für die dein Sammlungsbestand nicht reicht
  (Kopien in anderen Decks binden Bestand). Das ist nur ein **Hinweis** —
  Deckbuilding über den Bestand hinaus bleibt für geplante Käufe erlaubt.

## Import & Export

- **Importieren…** liest `.ydk`-Dateien (das Standardformat; die Karten-ID
  ist der Passcode). Der Import erzwingt die 3-Kopien-Regel, sortiert
  Main/Extra korrekt und meldet unbekannte Passcodes im Bericht, statt
  abzubrechen.
- **Exportieren…** schreibt das Deck wahlweise als `.ydk` (Passcodes), als
  lesbare Liste (`.txt`/`.pdf`) oder als **Markdown für KI** (`.md`) — Letzteres
  enthält jede Karte mit vollem Effekttext, Rolle, Konsistenz und Kombo-Linien,
  damit ein KI-System das Deck ohne Nachschlagen beurteilen kann.

## Korpus… (Referenz-Decks)

Über **Korpus…** importierst du fremde Meta-Decklisten als Referenz. Sie
**binden keinen Bestand** und tauchen nicht in deiner Deck-Auswahl auf — sie
speisen nur die Vorschläge (siehe Kombo-Hilfe → Vorschläge). Tagge sie mit
**Quelle** und **Stand (Datum)**; neuere Listen werden höher gewichtet.

## Kombo-Hilfe (rechte Box)

Drei Reiter:

- **Kombos** — Kombos nach Abdeckung im Deck, mit Bausteinen und Schritten.
  **Fehlende Bausteine ins Deck** ergänzt fehlende Karten; **Neue Kombo aus
  diesem Deck…** legt eine Kombo aus angekreuzten Deck-Karten an.
- **Fahrplan** — Karten je **Rolle** und **Linien zum Boss**; Doppelklick auf
  eine Linie öffnet die Kombo. Oben steht die **Konsistenz** (Wahrscheinlichkeit
  für Starter/Handtrap in der Starthand, Brick-Quote).
- **Vorschläge** — Kartenempfehlungen aus dem Synergie-Graphen samt
  **Begründung** (Tooltip). Doppelklick zeigt die Karte im Suche-Tab.

**Kombo-Linien exportieren…** schreibt die Linien des Decks als `.txt` oder
`.pdf` — z. B. um sie einem erfahrenen Spieler zum Drüberschauen zu geben.
"""),

    ("Spielfeld-Tab", """\
# Spielfeld (Goldfishing)

Ein **Solitaire-Sandkasten**: Deck wählen, Starthand ziehen und Linien frei
durchspielen — **ohne Regeln, ohne Gegner**. Nichts wird gespeichert;
**Neue Starthand** beginnt von vorn.

## Karten bewegen

- **Linksklick** auf eine Karte (Hand oder Feld) nimmt sie auf (Gold-Rahmen),
  ein Klick auf eine freie Zone legt sie dort ab. Erneuter Klick auf die
  Karte legt sie zurück.
- **Rechtsklick** öffnet das Kontextmenü: offen/verdeckt, ATK/DEF,
  → Hand / Friedhof / Verbannt / Deck (oben), Details.
- **Klick auf das Deck** zieht eine Karte.

## Stapel (Deck / Extra / Friedhof / Verbannt)

Ein Klick auf einen Stapel öffnet seine Liste. Jede Karte kann direkt ans
Ziel: **Auf die Hand**, **Aufnehmen** (in die Hand + angewählt — der nächste
Zonen-Klick legt sie ab, z. B. für Spezialbeschwörungen aus dem Extra-Deck),
**→ Friedhof** oder **→ Verbannt**. Nach **Deck durchsuchen…** wird
automatisch gemischt.

## Deck-Top ansehen

**Top ansehen…** blättert die obersten N Karten (oberste zuerst) — für
Excavate- und Mill-Effekte. Jede Karte einzeln **auf die Hand**, in den
**Friedhof**, ins **Verbannt** oder **nach unten** legen; der Rest geht in
unveränderter Reihenfolge zurück auf das Deck.

## Rückgängig

**↶ Rückgängig** (oder **Strg+Z**) nimmt die letzte Aktion zurück — Ziehen,
Ablegen, Stapel-Aktionen, Zustandswechsel, auch das Mischen. **Neue
Starthand** leert den Verlauf.

## Kombo aufzeichnen

**● Aufzeichnen** protokolliert ab jetzt jeden Zug als Kombo-Schritt in
Notation (`NS`/`SS`/`Act`/`Set`/`Add`/`Send`/`Mill`/…). Dabei gilt: die
**erste** Beschwörung aus der Hand wird `NS`, alle weiteren `SS`; Karten,
die du aus einem Stapel **aufnimmst** und ablegst, werden `SS <X> (ED/GY/…)`;
das Aufdecken einer gesetzten Zauber/Falle wird `Act`. **Rückgängig** nimmt
auch protokollierte Schritte zurück.

**■ Speichern…** legt die Aufzeichnung als **Kombo** an: mit den benutzten
Karten als Bausteinen, dem Spielfeld-Deck als **Heimat-Deck** (dadurch
erscheint sie sofort in der Kombo-Hilfe des Deck-Tabs), einem
**Boss-Vorschlag** (letzte Extra-Deck-Beschwörung) und `Start:`/`End:` in
den Notizen. Danach springt die App in den Kombos-Tab.

> Das Protokoll ist ein **Entwurf**: Es hält fest, *was sich bewegt hat* —
> welcher **Effekt** eine Suche oder Beschwörung ausgelöst hat, weißt nur
> du. Ergänze im Kombos-Tab die `Eff`-Ursachen und Synchro-Formeln; die
> beratende Prüfung zeigt, wo noch etwas fehlt.
"""),

    ("Kombos-Tab", """\
# Kombos

Die **Kombo-Bibliothek** ist das Herz der App: Hier dokumentierst du Spiel-
Linien. Sie liefern Guides, den Deck-Fahrplan **und** die Kartenvorschläge.

Links die Kombo-Liste (mit **Deck-Filter**), rechts der Editor.

## Kopfdaten

- **Name / Archetyp** — frei.
- **Heimat-Deck** — optionale Verknüpfung/Filter, **kein Besitz**: jede Kombo
  bleibt gegen jedes Deck abgleichbar.
- **Boss** — das Zielmonster der Linie, gewählt aus den Bausteinen.
- **Notizen** — Rahmendaten der Linie: `Start:` (benötigte Hand-/Feldkarten)
  und `End:` (Endboard). Diese gehören in die Notizen, **nicht** in die Schritte.

## Bausteine

Die Karten der Kombo. **+ Baustein…** sucht und fügt direkt hinzu (kein
Tab-Wechsel); **−1 / +1 / Entfernen** ändern die Anzahl. Über **Rolle** stufst
du den markierten Baustein ein: *Starter / Extender / Payoff / Handtrap*. Die
Rolle gilt **je Kombo**, nicht global für die Karte.

Die Box **„Mit deiner Sammlung"** zeigt, welche Bausteine du schon besitzt und
welche fehlen.

## Schritte & Notation

Im Schritte-Editor steht **eine Zeile pro Schritt**. Darüber liegt die
dauerhafte **Notations-Leiste**: ein Klick auf ein Token (`NS`, `SS`, `Act`,
`Eff1`, `Add`, `Send`, `Banish`, `→`, `[Req:]`, `| Lock:`) fügt es an der
Cursor-Position ein — nur die **Kartennamen** tippst du selbst. Der Button
**Notation…** öffnet die ausführliche Syntax-Referenz.

Eine beratende **Prüfung** warnt unter dem Editor, wenn ein Schritt von der
Notation abweicht — sie **blockiert nie**.

> **Auto-Speichern:** Name, Archetyp, Notizen und Schritte werden kurz nach der
> Eingabe automatisch gesichert (und immer vor einem Wechsel). Rollen, Boss und
> Heimat-Deck speichern sofort.

Die genaue Syntax findest du im Abschnitt **Kombo-Notation**.
"""),

    ("Kombo-Notation", _NOTATION_MD),

    ("Daten & Updates", """\
# Daten & Updates

Alles über das Menü **„Daten"**.

## Kartendaten

- **Auf Updates prüfen** — günstige Abfrage, ob eine neuere Kartendatenbank
  vorliegt.
- **Kartendaten aktualisieren…** — lädt die Daten neu (braucht Internet). Läuft
  im Hintergrund, legt vorher eine `.bak`-Sicherung an und aktualisiert Karten
  per UPSERT — **deine Sammlung, Decks und Kombos bleiben unangetastet**.

## App-Version

- **Auf neue App-Version prüfen** — fragt bei GitHub nach einer neueren App.
  Es gibt **keinen Auto-Updater**: Bei einer neuen Version erscheint ein Hinweis
  mit Link zur Download-Seite. Zum Aktualisieren den alten App-Ordner durch den
  neuen ersetzen — deine Daten bleiben erhalten (beim ersten Start einer neuen
  Version wird die Datenbank zusätzlich gesichert).

Die App prüft beim Start still auf neue Versionen; ohne Internet bleibt das
einfach unsichtbar.
"""),

    ("Konzepte & Prinzipien", """\
# Konzepte & Prinzipien

Hintergrund, warum die App sich so verhält:

- **Offline & eigenständig.** Nach dem einmaligen Laden der Kartendaten braucht
  die App kein Internet (außer für Updates).
- **Referenzdaten vs. Benutzerdaten.** Karten sind Referenz; Sammlung, Decks,
  Übersetzungen und Kombos sind deine Daten. Diese Trennung erlaubt gefahrlose
  Karten-Updates.
- **Hinweisen statt blockieren.** Bestands-Warnungen im Deck, die Notations-
  Prüfung in Kombos und die Sammlungs-Abgleiche **warnen nur** — du behältst
  die Kontrolle (z. B. für geplante Käufe).
- **Erklärbar statt opak.** Kartenvorschläge tragen immer ihre Begründung. Die
  App liefert Struktur und Mathematik rund um deine Kombos — sie **löst nicht
  das Spiel** und leitet keine Kombos aus dem Kartentext her.
- **Du lieferst die Daten.** Kombos und Referenz-Decks pflegst du selbst; die
  App scrapt nichts.
"""),
]


# ---------------------------------------------------------------------------
# Spielfeld-Test (Solitaire-Goldfishing) — eigener Tab
# ---------------------------------------------------------------------------

_CARD_W, _CARD_H = 74, 106     # Brettkarten-Groesse (aufrecht)
_ZONE_W = _ZONE_H = 120        # Zelle: fasst Karte aufrecht UND um 90° gedreht




class _CardInst:
    """Eine Karte auf dem Brett/Hand: Identitaet + Zustand (offen/verdeckt,
    ATK/DEF). Jede Kopie ist ein eigenes Exemplar; 'uid' identifiziert es
    stabil ueber Undo-Snapshots hinweg (der Kombo-Recorder zaehlt darueber
    benutzte Exemplare). 'origin' merkt sich beim Aufnehmen aus einem Stapel
    die Herkunft (ED/GY/…) bis zum Ablegen — daraus wird 'SS <X> (<Ort>)'."""
    __slots__ = ("card_id", "name", "face_down", "defense", "uid", "origin")
    _uid_counter = itertools.count(1)

    def __init__(self, card_id: int, name: str,
                 face_down: bool = False, defense: bool = False,
                 uid: int | None = None, origin: str | None = None):
        self.card_id = card_id
        self.name = name
        self.face_down = face_down
        self.defense = defense
        self.uid = next(self._uid_counter) if uid is None else uid
        self.origin = origin


class _BoardCard(QLabel):
    """Anzeige einer Karte auf dem Brett/Hand. Linksklick = aufnehmen/anwaehlen,
    Rechtsklick = Kontextmenue. Das fertige Pixmap (offen/verdeckt, ggf. gedreht)
    liefert die View."""
    clicked = Signal(object)            # _CardInst
    context = Signal(object, QPoint)    # _CardInst, globale Position

    def __init__(self, inst: _CardInst, pixmap: QPixmap,
                 held: bool = False, parent=None):
        super().__init__(parent)
        self.inst = inst
        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.size())
        self.setToolTip(inst.name)
        if held:
            self.setStyleSheet("border: 2px solid #d4af37;")

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self.context.emit(self.inst, e.globalPosition().toPoint())
        elif e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.inst)


class _Zone(QFrame):
    """Eine Feldzelle (Monster/Zauber/Feld/EMZ) oder ein Stapel
    (Deck/GY/Verbannt/Extra). Linksklick auf leere Flaeche meldet zone_key."""
    clicked = Signal(str)

    def __init__(self, zone_key: str, w: int, h: int, parent=None):
        super().__init__(parent)
        self.zone_key = zone_key
        self.setObjectName("BoardZone")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedSize(w, h)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(2, 2, 2, 2)
        self._lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_content(self, widget) -> None:
        while self._lay.count():
            old = self._lay.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
        if widget is not None:
            self._lay.addWidget(widget, alignment=Qt.AlignmentFlag.AlignCenter)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.zone_key)
        super().mousePressEvent(e)


class _PileDialog(QDialog):
    """Listet die Karten eines Stapels; Auswahl (Doppelklick/Button) landet in
    self.selected (Zeilenindex) und self.action (Aktions-Key). Doppelklick
    nimmt die erste Aktion. Der Aufrufer kennt die Reihenfolge."""

    def __init__(self, title: str, labels: list[str], parent=None,
                 actions: list[tuple[str, str]] | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 480)
        self.selected = -1
        self.action: str | None = None
        self._actions = actions or [("Auf die Hand", "hand")]
        lay = QVBoxLayout(self)
        self.listw = QListWidget()
        for s in labels:
            QListWidgetItem(s, self.listw)
        self.listw.itemDoubleClicked.connect(lambda _item: self._take())
        lay.addWidget(self.listw, stretch=1)
        row = QHBoxLayout()
        row.addStretch()
        for label, key in self._actions:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, k=key: self._take(k))
            row.addWidget(btn)
        close = QPushButton("Schließen")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        lay.addLayout(row)

    def _take(self, key: str | None = None) -> None:
        if self.listw.currentRow() >= 0:
            self.selected = self.listw.currentRow()
            self.action = key or self._actions[0][1]
            self.accept()


class _TopDeckDialog(QDialog):
    """Zeigt die obersten Deck-Karten (oberste zuerst). Jede Aktion nimmt die
    gewaehlte Karte aus der Liste und landet in self.moves (card_id, Aktion) —
    in Klick-Reihenfolge. Was beim Schliessen in self.cards uebrig ist, legt
    der Aufrufer unveraendert zurueck nach oben."""

    _ACTIONS = [("Auf die Hand", "hand"), ("→ Friedhof", "gy"),
                ("→ Verbannt", "banish"), ("Nach unten", "bottom")]

    def __init__(self, cards: list[int], names: dict[int, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Deck-Top ansehen ({len(cards)})")
        self.resize(420, 400)
        self.cards = list(cards)
        self._names = names
        self.moves: list[tuple[int, str]] = []
        lay = QVBoxLayout(self)
        hint = QLabel("Oberste Karte zuerst. Der Rest geht in dieser "
                      "Reihenfolge zurück auf das Deck.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.listw = QListWidget()
        for cid in self.cards:
            QListWidgetItem(names.get(cid, str(cid)), self.listw)
        self.listw.itemDoubleClicked.connect(lambda _item: self._do("hand"))
        lay.addWidget(self.listw, stretch=1)
        row = QHBoxLayout()
        row.addStretch()
        for label, key in self._ACTIONS:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, k=key: self._do(k))
            row.addWidget(btn)
        close = QPushButton("Fertig")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def _do(self, action: str) -> None:
        row = self.listw.currentRow()
        if row < 0:
            return
        self.moves.append((self.cards.pop(row), action))
        self.listw.takeItem(row)
        if not self.cards:
            self.accept()


class _RecordSaveDialog(QDialog):
    """Speichern-Dialog des Kombo-Recorders: Name, Boss-Vorschlag und die
    protokollierten Schritte als Vorschau. Ergebnis: Accepted = speichern,
    Rejected = weiter aufzeichnen, DISCARD = Aufzeichnung verwerfen."""

    DISCARD = 2

    def __init__(self, deck_name: str, steps: list[str],
                 boss_choices: list[tuple[int, str]], default_boss: int | None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Aufzeichnung als Kombo speichern")
        self.resize(480, 520)
        lay = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit(f"{deck_name} — neue Linie")
        form.addRow("Name:", self.name_edit)
        self.boss_cb = QComboBox()
        self.boss_cb.addItem("(kein Boss)", None)
        for cid, name in boss_choices:
            self.boss_cb.addItem(name, cid)
        if default_boss is not None:
            idx = self.boss_cb.findData(default_boss)
            self.boss_cb.setCurrentIndex(max(idx, 0))
        form.addRow("Boss:", self.boss_cb)
        form.addRow("Heimat-Deck:", QLabel(deck_name))
        lay.addLayout(form)

        self.notes_check = QCheckBox("Starthand/Endboard in die Notizen "
                                     "übernehmen (Start:/End:)")
        self.notes_check.setChecked(True)
        lay.addWidget(self.notes_check)

        lay.addWidget(QLabel(f"Protokollierte Schritte ({len(steps)}) — "
                             "Entwurf, im Kombos-Tab nachschärfen:"))
        preview = QListWidget()
        for i, s in enumerate(steps, start=1):
            QListWidgetItem(f"{i}  {s}", preview)
        preview.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        lay.addWidget(preview, stretch=1)

        row = QHBoxLayout()
        discard = QPushButton("Verwerfen")
        discard.clicked.connect(lambda: self.done(self.DISCARD))
        row.addWidget(discard)
        row.addStretch()
        cont = QPushButton("Weiter aufzeichnen")
        cont.clicked.connect(self.reject)
        row.addWidget(cont)
        save = QPushButton("Speichern")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        row.addWidget(save)
        lay.addLayout(row)

    def values(self) -> tuple[str, int | None, bool]:
        name = self.name_edit.text().strip() or "Aufgezeichnete Linie"
        return name, self.boss_cb.currentData(), self.notes_check.isChecked()


class PlayTestView(QWidget):
    """Solitaire-Spielfeld: ein eigenes Deck laden, Starthand ziehen und Karten
    frei auslegen (Klick-Aufnehmen + Klick-Ablegen, Rechtsklick fuer Zustaende).
    Stapel-Dialoge (Deck/Extra/GY/Verbannt) koennen Karten auch direkt
    aufnehmen ('hold': in die Hand + angewaehlt, naechster Zonen-Klick legt ab)
    oder in GY/Verbannt schicken; 'Top ansehen…' blaettert die obersten
    Deck-Karten (Excavate/Mill). Jede Zustandsaenderung ist per Undo-Stack
    (Snapshots, Ctrl+Z) rueckgaengig machbar. Der **Kombo-Recorder**
    ('● Aufzeichnen') protokolliert Zuege als Notation-Entwurf (NS/SS-
    Heuristik: erste Handbeschwoerung = NS; Herkunft aus _CardInst.origin)
    und speichert sie als Kombo mit Heimat-Deck = Spielfeld-Deck, Bausteinen
    (Exemplare via uid gezaehlt) und Start:/End:-Notizen — Nachschaerfen im
    Kombos-Tab (open_combo_callback). Reiner Sandkasten — keine Regeln, kein
    Gegner, keine Persistenz; der Spielzustand lebt nur in der GUI."""

    _PLACEMENT_LABELS = {"field": "Feld", "e": "EMZ", "m": "Mon", "s": "Z/F"}
    _UNDO_MAX = 80

    def __init__(self, repo: CardRepository):
        super().__init__()
        self.repo = repo
        self._deck_id: int | None = None
        self._names: dict[int, str] = {}
        self._deck: list[int] = []
        self._extra: list[int] = []
        self._hand: list[_CardInst] = []
        self._mzones: list = [None] * 5
        self._szones: list = [None] * 5
        self._emz: list = [None, None]
        self._field = None
        self._gy: list[_CardInst] = []
        self._banished: list[_CardInst] = []
        self._held: _CardInst | None = None
        self._undo: list[dict] = []
        self._detail_dialog: CardDetailDialog | None = None
        self.open_combo_callback = None   # setzt MainWindow

        # Kombo-Recorder (alles wandert mit in die Undo-Snapshots).
        self._recording = False
        self._rec_log: list[str] = []          # Schritte (Notation-Entwurf)
        self._rec_used: dict[int, int] = {}    # Exemplar-uid -> card_id
        self._rec_ns_used = False               # NS-Heuristik: 1x je Aufnahme
        self._rec_last_ed: int | None = None    # letzter ED-SS = Boss-Vorschlag
        self._rec_start: list[str] = []         # Starthand bei Aufnahmebeginn

        # Nachladen fehlender Bilder (meist sind sie lokal gecacht).
        self._loading_imgs: set[int] = set()
        self._img_signals = _ImageSignals()
        self._img_signals.loaded.connect(self._on_img_loaded)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(40)
        self._render_timer.timeout.connect(self._render)

        outer = QVBoxLayout(self)

        # -- Werkzeugleiste --------------------------------------------------
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Deck:"))
        self.deck_cb = QComboBox()
        self.deck_cb.currentIndexChanged.connect(self._on_deck_selected)
        bar.addWidget(self.deck_cb, stretch=1)
        self.hand_size_cb = QComboBox()
        self.hand_size_cb.addItem("5 (First)", 5)
        self.hand_size_cb.addItem("6 (Second)", 6)
        bar.addWidget(self.hand_size_cb)
        for text, slot in (
            ("Neue Starthand", self._reset_clicked),
            ("Ziehen", self.draw),
            ("Mischen", self.shuffle),
            ("Deck durchsuchen…", self._search_deck),
            ("Top ansehen…", self._peek_top),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        self.rec_btn = QPushButton("● Aufzeichnen")
        self.rec_btn.setToolTip(
            "Züge als Kombo-Schritte protokollieren und als Kombo mit "
            "diesem Deck als Heimat-Deck speichern"
        )
        self.rec_btn.clicked.connect(self._toggle_recording)
        bar.addWidget(self.rec_btn)
        self.undo_btn = QPushButton("↶ Rückgängig")
        self.undo_btn.setToolTip("Letzte Aktion zurücknehmen (Strg+Z)")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.undo)
        bar.addWidget(self.undo_btn)
        QShortcut(
            QKeySequence.StandardKey.Undo, self, self.undo,
            context=Qt.ShortcutContext.WidgetWithChildrenShortcut,
        )
        outer.addLayout(bar)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        # -- Feld-Grid -------------------------------------------------------
        self._zones: dict[str, _Zone] = {}
        grid = QGridLayout()
        grid.setSpacing(4)

        def zone(key, r, c, rowspan=1, colspan=1):
            z = _Zone(key, _ZONE_W, _ZONE_H)
            z.clicked.connect(self._on_zone_clicked)
            self._zones[key] = z
            grid.addWidget(z, r, c, rowspan, colspan)

        # Links: Feldzauber + Extra-Deck. Mitte (Spalten 1-5): EMZ / Monster /
        # Zauber-Fallen. Rechts: Deck / Friedhof / Verbannt.
        zone("field", 0, 0)
        zone("extra", 2, 0)
        zone("e0", 0, 2)
        zone("e1", 0, 4)
        for i in range(5):
            zone(f"m{i}", 1, 1 + i)
        for i in range(5):
            zone(f"s{i}", 2, 1 + i)
        # Rechte Spalte von unten nach oben: Deck, Friedhof, Verbannt.
        zone("banished", 0, 6)
        zone("gy", 1, 6)
        zone("deck", 2, 6)
        board = QWidget()
        board.setLayout(grid)
        outer.addWidget(board, alignment=Qt.AlignmentFlag.AlignHCenter)

        # -- Hand ------------------------------------------------------------
        self.hand_label = QLabel("Hand")
        self.hand_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(self.hand_label)
        self._hand_host = QWidget()
        self._hand_row = QHBoxLayout(self._hand_host)
        self._hand_row.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(_CARD_H + 24)
        scroll.setWidget(self._hand_host)
        outer.addWidget(scroll)
        outer.addStretch()

        self.refresh()

    # -- Deck-Auswahl / Laden ----------------------------------------------

    def refresh(self) -> None:
        """Deck-Liste aktuell halten (z. B. nach Anlegen/Loeschen von Decks)."""
        keep = self.deck_cb.currentData()
        self.deck_cb.blockSignals(True)
        self.deck_cb.clear()
        decks = ydb.list_decks(self.repo.db_path) if self.repo.exists() else []
        for d in decks:
            self.deck_cb.addItem(d["name"], d["deck_id"])
        idx = self.deck_cb.findData(keep)
        if idx < 0 and self.deck_cb.count():
            idx = 0
        self.deck_cb.setCurrentIndex(max(idx, 0))
        self.deck_cb.blockSignals(False)
        new_id = self.deck_cb.currentData()
        if new_id != self._deck_id:
            self._deck_id = new_id
            self.reset()
        else:
            self._render()

    def _on_deck_selected(self, _index: int) -> None:
        self._deck_id = self.deck_cb.currentData()
        self.reset()

    def _hand_size(self) -> int:
        return self.hand_size_cb.currentData() or 5

    def _reset_clicked(self) -> None:
        """Button-Variante von reset(): warnt, wenn eine Aufzeichnung läuft."""
        if self._recording and self._rec_log:
            answer = QMessageBox.question(
                self, "Aufzeichnung verwerfen?",
                "Eine Kombo-Aufzeichnung läuft. Eine neue Starthand "
                f"verwirft die {len(self._rec_log)} protokollierten "
                "Schritte — fortfahren?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.reset()

    def reset(self) -> None:
        """Feld/Hand leeren, Deck+Extra neu laden, mischen, Starthand ziehen.
        Beendet eine laufende Aufzeichnung (der Spielzustand, auf den sich
        das Protokoll bezieht, ist danach weg)."""
        self._stop_recording()
        self._held = None
        self._undo = []
        self._hand = []
        self._mzones = [None] * 5
        self._szones = [None] * 5
        self._emz = [None, None]
        self._field = None
        self._gy = []
        self._banished = []
        if self._deck_id is not None and self.repo.exists():
            data = ydb.deck_play_lists(self.repo.db_path, self._deck_id)
            self._names = data["names"]
            self._deck = list(data["main"])
            self._extra = list(data["extra"])
            random.shuffle(self._deck)
            for _ in range(self._hand_size()):
                if self._deck:
                    self._hand.append(self._mk_inst(self._deck.pop(0)))
        else:
            self._names, self._deck, self._extra = {}, [], []
        self._render()

    def _mk_inst(self, card_id: int) -> _CardInst:
        return _CardInst(card_id, self._names.get(card_id, str(card_id)))

    # -- Undo (Snapshot-Stack) ----------------------------------------------

    @staticmethod
    def _snap_inst(c: _CardInst | None):
        return None if c is None else (
            c.card_id, c.name, c.face_down, c.defense, c.uid, c.origin
        )

    def _push_undo(self) -> None:
        """Kompletten Spielzustand als wertbasierten Snapshot sichern (die
        _CardInst-Exemplare werden beim Undo neu gebaut, behalten aber ihre
        uid). Das Recorder-Protokoll wandert mit — Undo nimmt also auch
        protokollierte Schritte zurück."""
        si = self._snap_inst
        self._undo.append({
            "deck": list(self._deck), "extra": list(self._extra),
            "hand": [si(c) for c in self._hand],
            "m": [si(c) for c in self._mzones],
            "s": [si(c) for c in self._szones],
            "e": [si(c) for c in self._emz],
            "field": si(self._field),
            "gy": [si(c) for c in self._gy],
            "ban": [si(c) for c in self._banished],
            "rec": (self._recording, list(self._rec_log),
                    dict(self._rec_used), self._rec_ns_used,
                    self._rec_last_ed, list(self._rec_start)),
        })
        del self._undo[:-self._UNDO_MAX]

    def undo(self) -> None:
        if not self._undo:
            return
        s = self._undo.pop()

        def mk(t):
            return None if t is None else _CardInst(*t)

        self._deck = list(s["deck"])
        self._extra = list(s["extra"])
        self._hand = [mk(t) for t in s["hand"]]
        self._mzones = [mk(t) for t in s["m"]]
        self._szones = [mk(t) for t in s["s"]]
        self._emz = [mk(t) for t in s["e"]]
        self._field = mk(s["field"])
        self._gy = [mk(t) for t in s["gy"]]
        self._banished = [mk(t) for t in s["ban"]]
        (self._recording, self._rec_log, self._rec_used,
         self._rec_ns_used, self._rec_last_ed, self._rec_start) = s["rec"]
        self._held = None
        self._render()

    # -- Kombo-Recorder -------------------------------------------------------

    def _rec(self, text: str, inst: _CardInst | None = None) -> None:
        """Protokolliert einen Schritt (nur während der Aufzeichnung) und
        merkt das benutzte Exemplar für die Bausteinliste."""
        if not self._recording:
            return
        self._rec_log.append(text)
        if inst is not None:
            self._rec_used[inst.uid] = inst.card_id

    def _rec_pile_take(self, inst: _CardInst, action: str, source: str) -> None:
        """Protokolliert eine Stapel-Entnahme. 'hold' erzeugt hier noch
        keinen Schritt — die Herkunft steckt in inst.origin und wird erst
        beim Ablegen zu 'SS <X> (<Ort>)'."""
        if action == "hand":
            self._rec(f"Add {inst.name} ({source})", inst)
        elif action == "gy":
            self._rec(f"Send {inst.name} ({source} -> GY)", inst)
        elif action == "banish":
            self._rec(f"Banish {inst.name} ({source})", inst)

    def _rec_placement(self, inst: _CardInst, zone_key: str) -> None:
        """Protokolliert das Ablegen: Act/Set für Zauber/Fallen-Zonen,
        'SS (<Ort>)' für aufgenommene Stapel-Karten, sonst die NS/SS-
        Heuristik (die erste Handbeschwörung der Aufzeichnung ist NS —
        eine pro Zug —, alle weiteren SS)."""
        if not self._recording:
            return
        src, inst.origin = inst.origin, None
        if zone_key == "field" or zone_key.startswith("s"):
            kw = "Set" if inst.face_down else "Act"
            where = f" ({src})" if src else ""
            self._rec(f"{kw} {inst.name}{where}", inst)
        elif src:
            self._rec(f"SS {inst.name} ({src})", inst)
            if src == "ED":
                self._rec_last_ed = inst.card_id
        elif not self._rec_ns_used:
            self._rec(f"NS {inst.name}", inst)
            self._rec_ns_used = True
        else:
            self._rec(f"SS {inst.name} (Hand)", inst)

    def _toggle_recording(self) -> None:
        if self._recording:
            self._finish_recording()
            return
        if self._deck_id is None:
            return
        self._recording = True
        self._rec_log = []
        self._rec_used = {}
        self._rec_ns_used = False
        self._rec_last_ed = None
        self._rec_start = [c.name for c in self._hand]
        self._render()

    def _stop_recording(self) -> None:
        self._recording = False
        self._rec_log = []
        self._rec_used = {}
        self._rec_ns_used = False
        self._rec_last_ed = None
        self._rec_start = []

    def _end_board_text(self) -> str:
        """Offene Feldkarten als 'End:'-Zeile (verdeckte nur gezählt)."""
        cards = [c for c in (*self._mzones, *self._emz, self._field,
                             *self._szones) if c is not None]
        names = [c.name for c in cards if not c.face_down]
        sets = sum(1 for c in cards if c.face_down)
        if sets:
            names.append(f"{sets} Set")
        return " + ".join(names) if names else "—"

    def _finish_recording(self) -> None:
        """'■ Speichern…': Dialog zeigen; speichern, weiter aufzeichnen
        oder verwerfen."""
        if not self._rec_log:
            self._stop_recording()
            self._render()
            return
        boss_ids: list[int] = []
        for cid in self._rec_used.values():
            if cid not in boss_ids:
                boss_ids.append(cid)
        choices = [(cid, self._names.get(cid, str(cid))) for cid in boss_ids]
        dlg = _RecordSaveDialog(
            self.deck_cb.currentText(), list(self._rec_log), choices,
            self._rec_last_ed, self,
        )
        result = dlg.exec()
        if result == QDialog.DialogCode.Accepted:
            name, boss_id, with_notes = dlg.values()
            combo_id = self._save_recording(name, boss_id, with_notes)
            if self.open_combo_callback is not None:
                self.open_combo_callback(combo_id)
        elif result == _RecordSaveDialog.DISCARD:
            self._stop_recording()
            self._render()
        # Rejected ('Weiter aufzeichnen'): Zustand unverändert lassen.

    def _save_recording(self, name: str, boss_id: int | None,
                        with_notes: bool) -> int:
        """Schreibt die Aufzeichnung als Kombo mit Heimat-Deck =
        Spielfeld-Deck: Bausteine (benutzte Exemplare je Karte, via uid
        gezählt), Schritte als Notation-Entwurf, optional Start:/End:-
        Notizen. Danach ist der Recorder wieder aus."""
        db = self.repo.db_path
        combo_id = ydb.create_combo(db, name, deck_id=self._deck_id)
        counts: dict[int, int] = {}
        for cid in self._rec_used.values():
            counts[cid] = counts.get(cid, 0) + 1
        for cid, qty in counts.items():
            ydb.add_combo_card(db, combo_id, cid, qty)
        ydb.set_combo_steps(db, combo_id, list(self._rec_log))
        if boss_id is not None:
            ydb.set_combo_boss(db, combo_id, boss_id)
        if with_notes:
            start = ", ".join(self._rec_start) if self._rec_start else "—"
            notes = f"Start: {start}\nEnd: {self._end_board_text()}"
            ydb.update_combo(db, combo_id, name, None, notes)
        self._stop_recording()
        self._render()
        return combo_id

    # -- Aktionen -----------------------------------------------------------

    def draw(self) -> None:
        if self._deck:
            self._push_undo()
            self._hand.append(self._mk_inst(self._deck.pop(0)))
            if self._recording:
                # Mehrfach-Ziehen zu 'Draw n' zusammenfassen.
                if self._rec_log and self._rec_log[-1].startswith("Draw "):
                    n = int(self._rec_log[-1].split()[1])
                    self._rec_log[-1] = f"Draw {n + 1}"
                else:
                    self._rec_log.append("Draw 1")
            self._render()

    def shuffle(self) -> None:
        if self._deck:
            self._push_undo()
            random.shuffle(self._deck)
            self._render()

    def _dispose(self, inst: _CardInst, action: str,
                 source: str | None = None) -> None:
        """Bringt eine bereits entnommene Karte ans Aktions-Ziel. 'hold' legt
        sie in die Hand UND nimmt sie auf — der naechste Zonen-Klick legt sie
        ab; so kann kein Exemplar verloren gehen. 'source' merkt sich dabei
        die Herkunft fuer den Recorder ('SS <X> (<Ort>)')."""
        if action == "gy":
            self._gy.append(inst)
        elif action == "banish":
            self._banished.append(inst)
        else:  # "hand" oder "hold"
            self._hand.append(inst)
            if action == "hold":
                self._held = inst
                inst.origin = source

    def _search_deck(self) -> None:
        if not self._deck:
            return
        pairs = sorted(
            ((self._names.get(c, str(c)), c) for c in self._deck),
            key=lambda x: x[0],
        )
        dlg = _PileDialog(
            "Deck durchsuchen", [p[0] for p in pairs], self,
            actions=[("Auf die Hand", "hand"), ("Aufnehmen", "hold"),
                     ("→ Friedhof", "gy"), ("→ Verbannt", "banish")],
        )
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected >= 0:
            self._push_undo()
            cid = pairs[dlg.selected][1]
            self._deck.remove(cid)
            inst = self._mk_inst(cid)
            self._dispose(inst, dlg.action, source="Deck")
            self._rec_pile_take(inst, dlg.action, "Deck")
            random.shuffle(self._deck)
            self._render()

    def _open_pile(self, kind: str) -> None:
        if kind == "extra":
            labels = [self._names.get(c, str(c)) for c in self._extra]
            # Extra-Deck-Monster wollen aufs Feld: Aufnehmen ist Default.
            actions = [("Aufnehmen", "hold"), ("Auf die Hand", "hand"),
                       ("→ Friedhof", "gy"), ("→ Verbannt", "banish")]
        else:
            pile = self._gy if kind == "gy" else self._banished
            labels = [c.name for c in pile]
            other = ("→ Verbannt", "banish") if kind == "gy" else ("→ Friedhof", "gy")
            actions = [("Auf die Hand", "hand"), ("Aufnehmen", "hold"), other]
        if not labels:
            return
        titles = {"extra": "Extra-Deck", "gy": "Friedhof", "banished": "Verbannt"}
        dlg = _PileDialog(titles[kind], labels, self, actions=actions)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected >= 0:
            self._push_undo()
            i = dlg.selected
            if kind == "extra":
                inst = self._mk_inst(self._extra.pop(i))
            else:
                pile = self._gy if kind == "gy" else self._banished
                inst = pile.pop(i)
            src = {"extra": "ED", "gy": "GY", "banished": "Banished"}[kind]
            self._dispose(inst, dlg.action, source=src)
            self._rec_pile_take(inst, dlg.action, src)
            self._render()

    def _peek_top(self) -> None:
        """Die obersten N Deck-Karten ansehen (Excavate/Mill): Karten einzeln
        auf Hand/GY/Verbannt/nach unten verteilen, der Rest geht in gleicher
        Reihenfolge zurueck nach oben."""
        if not self._deck:
            return
        n, ok = QInputDialog.getInt(
            self, "Deck-Top ansehen", "Wie viele Karten ansehen?",
            3, 1, min(10, len(self._deck)),
        )
        if not ok:
            return
        self._push_undo()
        top = self._deck[:n]
        del self._deck[:n]
        dlg = _TopDeckDialog(top, self._names, self)
        dlg.exec()
        for cid, action in dlg.moves:
            if action == "bottom":
                self._deck.append(cid)
                self._rec(f"Send {self._names.get(cid, str(cid))} "
                          "(Deck -> Bottom)")
            else:
                inst = self._mk_inst(cid)
                self._dispose(inst, action)
                if action == "gy":
                    self._rec(f"Mill {inst.name}", inst)
                elif action == "banish":
                    self._rec(f"Banish {inst.name} (Deck)", inst)
                else:
                    self._rec(f"Add {inst.name} (Deck)", inst)
        self._deck[0:0] = dlg.cards   # Rest zurueck, Reihenfolge erhalten
        if not dlg.moves:
            self._undo.pop()          # nichts passiert -> kein Undo-Schritt
        self._render()

    # -- Bewegen (Klick-Aufnehmen + Ablegen) -------------------------------

    def _on_card_clicked(self, inst: _CardInst) -> None:
        if self._held is inst:
            self._held = None
        elif inst in self._hand or self._on_field(inst):
            self._held = inst
        self._render()

    def _on_zone_clicked(self, zone_key: str) -> None:
        if zone_key == "deck":
            self.draw()
            return
        if zone_key in ("gy", "banished", "extra"):
            self._open_pile(zone_key)
            return
        if self._held is None:
            return
        if zone_key == "field":
            if self._field is None:
                self._push_undo()
                from_board = self._on_field(self._held)
                self._remove_inst(self._held)
                self._field = self._held
                if not from_board:   # Umstellen auf dem Feld ist kein Schritt
                    self._rec_placement(self._field, zone_key)
                self._held = None
        else:
            arr, i = self._slot_ref(zone_key)
            if arr[i] is None:
                self._push_undo()
                from_board = self._on_field(self._held)
                self._remove_inst(self._held)
                arr[i] = self._held
                if not from_board:
                    self._rec_placement(arr[i], zone_key)
                self._held = None
        self._render()

    def _slot_ref(self, zone_key: str):
        arr = {"m": self._mzones, "s": self._szones, "e": self._emz}[zone_key[0]]
        return arr, int(zone_key[1:])

    def _on_field(self, inst: _CardInst) -> bool:
        if inst is self._field:
            return True
        return any(inst is c for c in (*self._mzones, *self._szones, *self._emz))

    def _remove_inst(self, inst: _CardInst) -> None:
        """Entfernt ein Exemplar aus Hand/Zonen/GY/Verbannt (per Identitaet)."""
        if inst in self._hand:
            self._hand.remove(inst)
            return
        for arr in (self._mzones, self._szones, self._emz):
            for i, c in enumerate(arr):
                if c is inst:
                    arr[i] = None
                    return
        if inst is self._field:
            self._field = None
            return
        for pile in (self._gy, self._banished):
            if inst in pile:
                pile.remove(inst)
                return

    def _on_card_context(self, inst: _CardInst, pos: QPoint) -> None:
        menu = QMenu(self)
        in_hand = inst in self._hand
        a_face = a_pos = a_hand = None
        if not in_hand:
            a_face = menu.addAction("Offen" if inst.face_down else "Verdeckt")
            a_pos = menu.addAction("ATK" if inst.defense else "DEF")
            a_hand = menu.addAction("→ Hand")
        a_gy = menu.addAction("→ Friedhof")
        a_ban = menu.addAction("→ Verbannt")
        a_deck = menu.addAction("→ Deck (oben)")
        menu.addSeparator()
        a_det = menu.addAction("Details…")
        chosen = menu.exec(pos)
        if chosen is None:
            return
        if chosen is a_det:
            self._open_detail(inst.card_id)
            return
        self._push_undo()
        here = "Hand" if in_hand else "Field"
        if chosen is a_face:
            was_set = inst.face_down
            inst.face_down = not inst.face_down
            # Aufdecken in der Zauber/Fallen- oder Feldzone = Aktivierung.
            if was_set and not inst.face_down and (
                inst is self._field
                or any(c is inst for c in self._szones)
            ):
                self._rec(f"Act {inst.name}", inst)
        elif chosen is a_pos:
            inst.defense = not inst.defense
        elif chosen is a_hand:
            self._remove_inst(inst); self._hand.append(inst)
            self._rec(f"Send {inst.name} (Field -> Hand)", inst)
        elif chosen is a_gy:
            self._remove_inst(inst); self._gy.append(inst)
            self._rec(f"Discard {inst.name}" if in_hand
                      else f"Send {inst.name} (Field -> GY)", inst)
        elif chosen is a_ban:
            self._remove_inst(inst); self._banished.append(inst)
            self._rec(f"Banish {inst.name} ({here})", inst)
        elif chosen is a_deck:
            self._remove_inst(inst); self._deck.insert(0, inst.card_id)
            self._rec(f"Send {inst.name} ({here} -> Deck)", inst)
        self._render()

    def _open_detail(self, card_id: int) -> None:
        if self._detail_dialog is None:
            self._detail_dialog = CardDetailDialog(self.repo, self)
        self._detail_dialog.load(card_id)
        self._detail_dialog.show()
        self._detail_dialog.raise_()
        self._detail_dialog.activateWindow()

    # -- Bilder -------------------------------------------------------------

    def _pixmap_for(self, inst: _CardInst) -> QPixmap:
        if inst.face_down:
            pm = _card_back_pixmap(_CARD_W, _CARD_H)
        else:
            pm = _lookup_card_pixmap(inst.card_id, _CARD_W, _CARD_H, "board")
            if pm is None:
                self._ensure_image(inst.card_id)
                pm = _placeholder_pixmap(inst.name, _CARD_W, _CARD_H)
        if inst.defense:
            pm = pm.transformed(
                QTransform().rotate(90), Qt.TransformationMode.SmoothTransformation
            )
        return pm

    def _ensure_image(self, card_id: int) -> None:
        if card_id in self._loading_imgs:
            return
        self._loading_imgs.add(card_id)
        QThreadPool.globalInstance().start(
            _ImageLoader(card_id, IMAGE_URL.format(card_id), self._img_signals)
        )

    def _on_img_loaded(self, card_id: int, img: QImage) -> None:
        QPixmapCache.insert(
            f"board:{card_id}", _scale_pixmap(QPixmap.fromImage(img), _CARD_W, _CARD_H)
        )
        self._loading_imgs.discard(card_id)
        self._render_timer.start()   # gebuendelt neu zeichnen

    # -- Rendern ------------------------------------------------------------

    def _make_card(self, inst: _CardInst) -> _BoardCard:
        card = _BoardCard(inst, self._pixmap_for(inst), held=(inst is self._held))
        card.clicked.connect(self._on_card_clicked)
        card.context.connect(self._on_card_context)
        return card

    def _zone_placeholder(self, key: str) -> QLabel:
        lbl = QLabel(self._PLACEMENT_LABELS.get(key[0], ""))
        lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lbl.setStyleSheet("color: #5b5273;")
        return lbl

    def _pile_widget(self, name: str, count: int,
                     back: bool = False, top: _CardInst = None) -> QWidget:
        host = QWidget()
        host.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img = QLabel()
        img.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        if top is not None:
            img.setPixmap(self._pixmap_for(top))
        elif back and count:
            img.setPixmap(_card_back_pixmap(_CARD_W, _CARD_H))
        v.addWidget(img, alignment=Qt.AlignmentFlag.AlignCenter)
        cap = QLabel(f"{name} {count}")
        cap.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        cap.setStyleSheet("color: #cfc6e0; font-size: 9px;")
        v.addWidget(cap, alignment=Qt.AlignmentFlag.AlignCenter)
        return host

    def _render(self) -> None:
        slots = {"field": self._field, "e0": self._emz[0], "e1": self._emz[1]}
        for i in range(5):
            slots[f"m{i}"] = self._mzones[i]
            slots[f"s{i}"] = self._szones[i]
        for key, inst in slots.items():
            self._zones[key].set_content(
                self._make_card(inst) if inst is not None
                else self._zone_placeholder(key)
            )
        self._zones["deck"].set_content(
            self._pile_widget("Deck", len(self._deck), back=True)
        )
        self._zones["extra"].set_content(
            self._pile_widget("Extra", len(self._extra), back=True)
        )
        self._zones["gy"].set_content(
            self._pile_widget("GY", len(self._gy),
                              top=self._gy[-1] if self._gy else None)
        )
        self._zones["banished"].set_content(
            self._pile_widget("Bann", len(self._banished),
                              top=self._banished[-1] if self._banished else None)
        )
        # Hand
        while self._hand_row.count():
            old = self._hand_row.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
        for inst in self._hand:
            self._hand_row.addWidget(self._make_card(inst))
        self.hand_label.setText(f"Hand ({len(self._hand)})")
        held = (
            f"  ·  aufgenommen: {self._held.name} — Zone anklicken"
            if self._held else ""
        )
        rec = f"  ·  ● Aufzeichnung ({len(self._rec_log)})" if self._recording else ""
        self.status.setText(
            f"Deck {len(self._deck)}  ·  Extra {len(self._extra)}  ·  "
            f"GY {len(self._gy)}  ·  Verbannt {len(self._banished)}{rec}{held}"
        )
        self.undo_btn.setEnabled(bool(self._undo))
        self.rec_btn.setText(
            f"■ Speichern… ({len(self._rec_log)})" if self._recording
            else "● Aufzeichnen"
        )
        self.rec_btn.setEnabled(self._recording or self._deck_id is not None)


class HelpView(QWidget):
    """Benutzerhandbuch als eigener Tab: links die Abschnittsliste, rechts der
    gewaehlte Abschnitt als gerenderter Text. Rein statisch (kein Repo-Zugriff),
    daher kein refresh()."""

    def __init__(self) -> None:
        super().__init__()
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.index = QListWidget()
        for title, _md in _MANUAL_SECTIONS:
            self.index.addItem(QListWidgetItem(title))
        self.index.currentRowChanged.connect(self._show_section)
        splitter.addWidget(self.index)

        self.content = QTextBrowser()
        self.content.setOpenExternalLinks(True)
        splitter.addWidget(self.content)
        splitter.setSizes([240, 760])

        outer = QVBoxLayout(self)
        outer.addWidget(splitter)
        self.index.setCurrentRow(0)

    def _show_section(self, row: int) -> None:
        if 0 <= row < len(_MANUAL_SECTIONS):
            self.content.setMarkdown(_MANUAL_SECTIONS[row][1])
            self.content.verticalScrollBar().setValue(0)


# ---------------------------------------------------------------------------
# Hauptfenster
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, db_path: str = ydb.DEFAULT_DB, restore_session: bool = False):
        super().__init__()
        # Sitzungs-/Fensterstatus nur im echten App-Lauf (main()) wiederher-
        # stellen/speichern -- in Tests/Smokes bleibt der Zustand neutral.
        self._restore_session = restore_session
        self.setWindowTitle(
            f"Yu-Gi-Oh -- Sammlung & Suche  ·  v{ydb.APP_VERSION}"
        )
        self.resize(1100, 700)
        self.repo = CardRepository(db_path)
        # Gepackter Erststart: mitgelieferte Seed-DB in den Nutzerordner kopieren.
        ydb.ensure_user_db(db_path)
        if self.repo.exists():
            # Erster Start einer neuen App-Version: Benutzerdaten sichern,
            # BEVOR ensure_schema die DB migriert.
            try:
                ydb.migration_backup(db_path)
            except OSError as exc:
                QMessageBox.warning(
                    self, "Sicherung fehlgeschlagen",
                    f"Versions-Sicherung der Datenbank fehlgeschlagen ({exc}).\n"
                    "Die App startet trotzdem.",
                )
            ydb.ensure_schema(db_path)  # ggf. fehlende Tabellen nachruesten
        self._loading = True  # unterdrueckt Suche waehrend Initialisierung
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self.search)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_filter_panel())
        self.results = QListWidget()
        self.results.currentItemChanged.connect(self._on_select)
        splitter.addWidget(self.results)
        self.detail = DetailPanel(self.repo)
        splitter.addWidget(self.detail)
        splitter.setSizes([280, 380, 440])
        self.splitter = splitter

        self.collection_view = CollectionView(self.repo)
        self.deck_view = DeckView(self.repo)
        self.combo_view = ComboView(self.repo)
        self.playtest_view = PlayTestView(self.repo)
        # Detailansicht -> aktives Deck bzw. aktive Kombo
        self.detail.add_to_deck_callback = self.deck_view.add_card
        self.detail.add_to_combo_callback = self.combo_view.add_piece
        # Deck-Tab -> neue Kombo im Kombos-Tab oeffnen
        self.deck_view.open_combo_callback = self._open_combo
        self.playtest_view.open_combo_callback = self._open_combo
        # Deck-Tab (Vorschlaege) -> Karte im DetailPanel des Suche-Tabs
        self.deck_view.open_card_callback = self._show_card

        self.tabs = QTabWidget()
        self.tabs.addTab(splitter, "Suche")
        self.tabs.addTab(self.collection_view, "Sammlung")
        self.tabs.addTab(self.deck_view, "Deck")
        self.tabs.addTab(self.playtest_view, "Spielfeld")
        self.tabs.addTab(self.combo_view, "Kombos")
        self.tabs.addTab(HelpView(), "Handbuch")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)
        self._build_data_menu()

        if self.repo.exists():
            self._populate_filters()
        elif getattr(sys, "frozen", False):
            QMessageBox.warning(
                self, "Datenbank fehlt",
                "Die mitgelieferte Kartendatenbank konnte nicht angelegt werden.\n\n"
                "Mit Internetverbindung lässt sie sich über das Menü\n"
                "'Daten → Kartendaten aktualisieren' herunterladen.",
            )
        else:
            QMessageBox.information(
                self, "Datenbank fehlt",
                "Keine Datenbank gefunden.\n\n"
                "Anlegen über das Menü 'Daten → Kartendaten aktualisieren'\n"
                "oder per Kommandozeile:  python yugioh_db.py build",
            )
        self._loading = False
        self.search()
        if self._restore_session:
            self._restore_session_state()
        # Stiller Hinweis auf neue App-Versionen, kurz nach dem Start (ein
        # Mini-Request; offline/Fehler bleibt einfach unsichtbar).
        QTimer.singleShot(2000, lambda: self._check_app_version(manual=False))

    def _build_filter_panel(self) -> QWidget:
        panel = QWidget()
        form = QVBoxLayout(panel)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Name oder Kartentext ...")
        self.search_box.returnPressed.connect(self.search)

        box = QGroupBox("Filter")
        fl = QFormLayout(box)
        self.type_cb = QComboBox()
        self.attr_cb = QComboBox()
        self.arch_cb = QComboBox()
        self.level_cb = QComboBox()
        self.level_cb.addItem("(alle)", None)
        for lvl in range(1, 13):
            self.level_cb.addItem(str(lvl), lvl)

        self.atk_min = QSpinBox(); self.atk_min.setRange(0, 5000)
        self.atk_min.setSingleStep(100); self.atk_min.setSpecialValueText("egal")
        self.atk_max = QSpinBox(); self.atk_max.setRange(0, 5000)
        self.atk_max.setSingleStep(100); self.atk_max.setSpecialValueText("egal")

        for cb in (self.type_cb, self.attr_cb, self.arch_cb):
            cb.addItem("(alle)", None)

        fl.addRow("Typ", self.type_cb)
        fl.addRow("Attribut", self.attr_cb)
        fl.addRow("Archetyp", self.arch_cb)
        fl.addRow("Level/Rank", self.level_cb)
        fl.addRow("ATK ab", self.atk_min)
        fl.addRow("ATK bis", self.atk_max)

        self.only_coll = QCheckBox("Nur meine Sammlung")
        search_btn = QPushButton("Suchen")
        search_btn.setObjectName("primary")  # goldener Primaer-Button (Theme)
        search_btn.clicked.connect(self.search)

        # Aenderungen an Filtern loesen direkt eine neue Suche aus.
        for cb in (self.type_cb, self.attr_cb, self.arch_cb, self.level_cb):
            cb.currentIndexChanged.connect(self._on_filter_changed)
        self.atk_min.valueChanged.connect(self._on_filter_changed)
        self.atk_max.valueChanged.connect(self._on_filter_changed)
        self.only_coll.stateChanged.connect(self._on_filter_changed)

        form.addWidget(self.search_box)
        form.addWidget(box)
        form.addWidget(self.only_coll)
        form.addWidget(search_btn)
        form.addStretch()
        self.count_label = QLabel("")
        form.addWidget(self.count_label)
        return panel

    def _populate_filters(self) -> None:
        _col_trans = {"type": _TYPE_DE, "attribute": _ATTR_DE}
        for cb, col in (
            (self.type_cb, "type"),
            (self.attr_cb, "attribute"),
            (self.arch_cb, "archetype"),
        ):
            trans = _col_trans.get(col, {})
            cb.blockSignals(True)
            for val in self.repo.distinct(col):
                cb.addItem(trans.get(val, val), val)
            cb.blockSignals(False)

    def _on_filter_changed(self, *_):
        if not self._loading:
            self._search_timer.start()

    def search(self) -> None:
        if not self.repo.exists():
            return
        cards = self.repo.query(
            text=self.search_box.text(),
            type=self.type_cb.currentData(),
            attribute=self.attr_cb.currentData(),
            archetype=self.arch_cb.currentData(),
            level=self.level_cb.currentData(),
            atk_min=self.atk_min.value(),
            atk_max=self.atk_max.value(),
            only_collection=self.only_coll.isChecked(),
        )
        self.results.clear()
        for c in cards:
            stat = ""
            if c["atk"] is not None:
                # Link-Monster haben keine DEF (def ist NULL) -> nicht "None"
                # anzeigen, wie auch DetailPanel.show_card.
                if c["def"] is not None:
                    stat = f"  [ATK {c['atk']} / DEF {c['def']}]"
                else:
                    stat = f"  [ATK {c['atk']}]"
            item = QListWidgetItem(f"{c['name_de'] or c['name']}{stat}")
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            self.results.addItem(item)
        self.count_label.setText(f"{len(cards)} Treffer")

    def _on_select(self, current: QListWidgetItem, _previous=None) -> None:
        if current is None:
            return
        card_id = current.data(Qt.ItemDataRole.UserRole)
        card = self.repo.get_card(card_id)
        if card:
            self.detail.show_card(card)

    def _on_tab_changed(self, index: int) -> None:
        # Sammlung beim Wechsel auf den Tab aktualisieren, damit gerade
        # hinzugefuegte Karten sofort erscheinen.
        widget = self.tabs.widget(index)
        if widget is self.collection_view:
            self.collection_view.refresh()
        elif widget is self.deck_view:
            self.deck_view.refresh()
        elif widget is self.playtest_view:
            self.playtest_view.refresh()
        elif widget is self.combo_view:
            self.combo_view.refresh()

    def _open_combo(self, combo_id: int) -> None:
        """Aus dem Deck-Tab: in den Kombos-Tab wechseln und die Kombo zeigen."""
        self.tabs.setCurrentWidget(self.combo_view)
        self.combo_view.focus_combo(combo_id)

    def _show_card(self, card_id: int) -> None:
        """Aus dem Deck-Tab: Karte im DetailPanel zeigen (lebt im Suche-Tab);
        von dort fuegt '+ Deck' sie direkt dem aktiven Deck hinzu."""
        card = self.repo.get_card(card_id)
        if card is None:
            return
        self.tabs.setCurrentIndex(0)  # Suche-Tab haelt das DetailPanel
        self.detail.show_card(card)

    def closeEvent(self, event) -> None:
        # Noch nicht gespeicherte Kombo-Eingaben sichern (Auto-Save-Timer
        # koennte sonst verfallen).
        self.combo_view.flush_pending()
        if self._restore_session:
            self._save_session_state()
        super().closeEvent(event)

    # -- Sitzungs-/Fensterstatus (QSettings) ----------------------------------

    def _save_session_state(self) -> None:
        """Fenster-, Tab-, Deck- und Sammlungsfilter-Zustand sichern."""
        s = QSettings()
        s.setValue("win/geometry", self.saveGeometry())
        s.setValue("win/splitter", self.splitter.saveState())
        s.setValue("ui/tab", self.tabs.currentIndex())
        did = self.deck_view.deck_id
        s.setValue("ui/deck_id", -1 if did is None else int(did))
        cv = self.collection_view
        s.setValue("coll/text", cv.filter_text.text())
        s.setValue("coll/cat", cv.filter_cat.currentData() or "")
        s.setValue("coll/attr", cv.filter_attr.currentData() or "")
        s.setValue("coll/arch", cv.filter_arch.currentData() or "")
        s.setValue("coll/untranslated", cv.filter_untranslated.isChecked())

    def _restore_session_state(self) -> None:
        """Gesicherten Zustand wiederherstellen (nach dem Aufbau der Views)."""
        s = QSettings()
        geo = s.value("win/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        spl = s.value("win/splitter")
        if spl is not None:
            self.splitter.restoreState(spl)
        # Sammlungsfilter ohne Zwischen-Refreshes setzen, danach einmal anwenden.
        cv = self.collection_view
        widgets = (
            cv.filter_text, cv.filter_cat, cv.filter_attr,
            cv.filter_arch, cv.filter_untranslated,
        )
        for w in widgets:
            w.blockSignals(True)
        cv.filter_text.setText(s.value("coll/text", "") or "")
        for combo, key in (
            (cv.filter_cat, "coll/cat"), (cv.filter_attr, "coll/attr"),
            (cv.filter_arch, "coll/arch"),
        ):
            idx = combo.findData(s.value(key, "") or None)
            combo.setCurrentIndex(max(idx, 0))
        cv.filter_untranslated.setChecked(
            s.value("coll/untranslated", False, type=bool)
        )
        for w in widgets:
            w.blockSignals(False)
        if self.repo.exists():
            cv._apply_filters()
        # Gewaehltes Deck wiederherstellen.
        did = s.value("ui/deck_id", -1, type=int)
        if did is not None and did >= 0:
            self.deck_view.select_deck(did)
        # Zuletzt aktives Tab.
        tab = s.value("ui/tab", 0, type=int)
        if tab is not None and 0 <= tab < self.tabs.count():
            self.tabs.setCurrentIndex(tab)

    # -- Kartendaten-Update (Menü 'Daten') ------------------------------------

    def _build_data_menu(self) -> None:
        menu = self.menuBar().addMenu("Daten")
        self._check_action = menu.addAction("Auf Updates prüfen")
        self._check_action.triggered.connect(self._check_for_update)
        self._update_action = menu.addAction("Kartendaten aktualisieren…")
        self._update_action.triggered.connect(self._start_update)
        menu.addSeparator()
        self._app_check_action = menu.addAction("Auf neue App-Version prüfen")
        self._app_check_action.triggered.connect(
            lambda: self._check_app_version(manual=True)
        )

    # -- App-Update-Hinweis (GitHub-Releases) ---------------------------------

    def _check_app_version(self, manual: bool) -> None:
        """Neueste Version bei GitHub abfragen (im Hintergrund). manual=True
        meldet auch 'aktuell' und Fehler; der Start-Check bleibt still."""
        self._app_check_action.setEnabled(False)
        self._app_check_signals = _DbTaskSignals()
        self._app_check_signals.done.connect(
            lambda info: self._on_app_check_done(info, manual)
        )
        self._app_check_signals.failed.connect(
            lambda msg: self._on_app_check_failed(msg, manual)
        )
        QThreadPool.globalInstance().start(
            _DbTask(ydb.check_app_update, self._app_check_signals)
        )

    def _on_app_check_failed(self, msg: str, manual: bool) -> None:
        self._app_check_action.setEnabled(True)
        if manual:
            QMessageBox.warning(
                self, "Auf neue App-Version prüfen",
                f"Keine Antwort von GitHub ({msg}).\n"
                "Besteht eine Internetverbindung?",
            )

    def _on_app_check_done(self, info, manual: bool) -> None:
        self._app_check_action.setEnabled(True)
        if info is None:
            if manual:
                QMessageBox.information(
                    self, "Auf neue App-Version prüfen",
                    f"Du nutzt die aktuelle Version (v{ydb.APP_VERSION}).",
                )
            return
        notes = info["notes"]
        if len(notes) > 600:
            notes = notes[:600] + "…"
        text = (
            f"Version {info['version']} ist verfügbar "
            f"(installiert: {ydb.APP_VERSION}).\n\n"
            "Deine Daten (Sammlung, Decks, Kombos) bleiben beim Update "
            "erhalten — einfach den alten App-Ordner durch den neuen "
            "ersetzen."
        )
        if notes:
            text += f"\n\nÄnderungen:\n{notes}"
        box = QMessageBox(
            QMessageBox.Icon.Information, "Neue App-Version", text,
            parent=self,
        )
        open_btn = box.addButton(
            "Download-Seite öffnen", QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton("Später", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl(info["url"]))

    def _set_data_actions_enabled(self, on: bool) -> None:
        self._check_action.setEnabled(on)
        self._update_action.setEnabled(on)

    def _check_for_update(self) -> None:
        """Billige Versionsabfrage (ein Mini-Request), im Hintergrund, damit
        ein totes Netz die Oberfläche nicht blockiert."""
        self._set_data_actions_enabled(False)
        self._check_signals = _DbTaskSignals()
        self._check_signals.done.connect(self._on_check_done)
        self._check_signals.failed.connect(lambda _msg: self._on_check_done(None))
        QThreadPool.globalInstance().start(
            _DbTask(ydb.fetch_db_version, self._check_signals)
        )

    def _on_check_done(self, remote) -> None:
        self._set_data_actions_enabled(True)
        if not remote:
            QMessageBox.warning(
                self, "Auf Updates prüfen",
                "Keine Antwort von der YGOPRODeck-API.\n"
                "Besteht eine Internetverbindung?",
            )
            return
        local = (
            ydb.local_db_version(self.repo.db_path)
            if self.repo.exists() else None
        )
        if local == remote:
            QMessageBox.information(
                self, "Auf Updates prüfen",
                f"Die Kartendaten sind aktuell (Version {remote}).",
            )
            return
        reply = QMessageBox.question(
            self, "Auf Updates prüfen",
            f"Neue Kartendaten verfügbar (lokal: {local or 'unbekannt'}, "
            f"online: {remote}).\n\nJetzt aktualisieren?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_update(confirmed=True)

    def _start_update(self, confirmed: bool = False) -> None:
        """Laedt alle Kartendaten neu. Benutzerdaten (Sammlung, Decks, Kombos,
        eigene Übersetzungen) bleiben unberührt: build_database arbeitet per
        UPSERT ohne DELETE auf cards und wendet Übersetzungs-Overrides nach
        dem Update wieder an. Vorher wird eine .bak-Sicherung angelegt."""
        if not confirmed:
            reply = QMessageBox.question(
                self, "Kartendaten aktualisieren",
                "Alle Kartendaten von der YGOPRODeck-API herunterladen?\n\n"
                "Eigene Daten (Sammlung, Decks, Kombos, eigene Übersetzungen) "
                "bleiben erhalten.",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        if os.path.exists(self.repo.db_path):
            try:
                shutil.copy(self.repo.db_path, self.repo.db_path + ".bak")
            except OSError as exc:
                QMessageBox.warning(
                    self, "Kartendaten aktualisieren",
                    f"Sicherungskopie fehlgeschlagen ({exc}).\n"
                    "Update abgebrochen.",
                )
                return
        self._set_data_actions_enabled(False)
        self._progress = QProgressDialog(
            "Lade Kartendaten von der YGOPRODeck-API …", "", 0, 0, self
        )
        self._progress.setCancelButton(None)  # mitten im Schreiben kein Abbruch
        self._progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._progress.setWindowTitle("Aktualisierung")
        self._progress.setMinimumDuration(0)
        self._progress.show()
        self._update_signals = _DbTaskSignals()
        self._update_signals.done.connect(self._on_update_done)
        self._update_signals.failed.connect(self._on_update_failed)
        db_path = self.repo.db_path
        QThreadPool.globalInstance().start(
            _DbTask(lambda: ydb.build_database(db_path), self._update_signals)
        )

    def _on_update_done(self, count) -> None:
        self._progress.close()
        self._set_data_actions_enabled(True)
        self._refresh_all()
        version = ydb.local_db_version(self.repo.db_path) or "unbekannt"
        QMessageBox.information(
            self, "Aktualisierung",
            f"{count} Karten aktualisiert (Datenbank-Version {version}).",
        )

    def _on_update_failed(self, msg: str) -> None:
        self._progress.close()
        self._set_data_actions_enabled(True)
        QMessageBox.warning(
            self, "Aktualisierung fehlgeschlagen",
            f"{msg}\n\nDie Datenbank wurde nicht verändert; zur Not liegt "
            f"eine Sicherung neben ihr ({os.path.basename(self.repo.db_path)}"
            ".bak).",
        )

    def _refresh_all(self) -> None:
        """Nach einem Daten-Update: Such-Filter neu befüllen (neue Archetypen
        usw.) und alle Views neu laden."""
        self._loading = True
        for cb in (self.type_cb, self.attr_cb, self.arch_cb):
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("(alle)", None)
            cb.blockSignals(False)
        if self.repo.exists():
            self._populate_filters()
        self._loading = False
        self.search()
        self.collection_view.refresh()
        self.deck_view.refresh()
        self.combo_view.refresh()



def main() -> None:
    app = QApplication(sys.argv)
    # Identitaet fuer QSettings (Sitzungs-/Fensterstatus).
    app.setOrganizationName(ydb.APP_DIR_NAME)
    app.setApplicationName(ydb.APP_DIR_NAME)
    apply_theme(app)
    win = MainWindow(restore_session=True)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
