"""Deck-Tab: drei Zonen-Panels plus Kombo-Hilfe (vier Reiter).

ZonePanel (Main/Extra/Side mit Bestands-Warnung '⚠ fehlt n') und
DeckView: Deck-Auswahl, .ydk-Import/-Export, Korpus/Diff, dazu die
Kombo-Hilfe mit Kombos/Fahrplan/Vorschlaegen/Starthand-Simulator.
Wirkt nach aussen nur ueber Callbacks (open_combo/open_card).
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSplitter, QTabWidget, QVBoxLayout, QWidget
)

import yugioh_db as ydb

from .carddetail import CardDetailDialog, CardSearchDialog
from .deck_dialogs import (
    ComboFromDeckDialog, DeckCorpusDiffDialog, ReferenceDeckDialog
)
from .exporting import resolve_export_path, write_text_file, write_text_pdf
from .images import HoverCardPreview
from .labels import (
    CATEGORY_DE, CATEGORY_ORDER, ROLE_DE, SIM_COPIES_DATA, ZONE_LABELS,
    import_report_lines, pct
)
from .repository import CardRepository


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
        self._hover = HoverCardPreview(self.list, self._hover_card_id)
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
            buckets: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
            for r in rows:
                buckets[ydb.card_category(r["type"])].append(r)
            for cat in CATEGORY_ORDER:
                group = buckets[cat]
                if not group:
                    continue
                self._add_group_header(
                    CATEGORY_DE[cat], sum(r["quantity"] for r in group)
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

        lever_tab = QWidget()
        lv = QVBoxLayout(lever_tab)
        self.lever_base = QLabel("")
        self.lever_base.setWordWrap(True)
        lv.addWidget(self.lever_base)
        self.lever_list = QListWidget()
        self.lever_list.currentItemChanged.connect(self._show_lever_detail)
        lv.addWidget(self.lever_list, stretch=1)
        self.lever_detail = QLabel("")
        self.lever_detail.setWordWrap(True)
        lv.addWidget(self.lever_detail)
        self.helper_tabs.addTab(lever_tab, "Hebel")

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
                "Eventuell hilft ein Daten-Update (python -m yugioh_db build).",
            )
            return
        lines = import_report_lines(report)
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
        path, ext = resolve_export_path(
            path, selected,
            {"YGOPro": ".ydk", "Text": ".txt", "PDF": ".pdf", "Markdown": ".md"},
            default_ext=".ydk",
        )
        db, did = self.repo.db_path, self.deck_id
        try:
            if ext == ".ydk":
                write_text_file(path, ydb.export_deck_ydk(db, did))
            elif ext == ".md":
                write_text_file(path, ydb.export_deck_markdown(db, did))
            elif ext == ".pdf":
                write_text_pdf(path, ydb.export_deck_text(db, did))
            else:
                write_text_file(path, ydb.export_deck_text(db, did))
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
            self._refresh_leverage()
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
        self._refresh_leverage()

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
        path, ext = resolve_export_path(
            path, selected, {"PDF": ".pdf", "Text": ".txt"}, default_ext=".txt"
        )
        try:
            text = ydb.export_deck_combos_text(self.repo.db_path, self.deck_id)
            if ext == ".pdf":
                write_text_pdf(path, text)
            else:
                write_text_file(path, text)
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
                f"{ROLE_DE[r]} {roles[r]}" for r in ydb.COMBO_ROLES if roles.get(r)
            )
        ]
        for hand, p in stats["hands"].items():
            line = f"Starthand {hand}: ≥1 Starter {pct(p['starter'])}"
            if roles.get("handtrap"):
                line += f"  ·  ≥1 Handtrap {pct(p['handtrap'])}"
            line += f"  ·  Brick {pct(p['brick'])}"
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
            self._add_plan_header(self.role_summary, f"{ROLE_DE[role]} ({total})")
            for c in cards:
                self.role_summary.addItem(f"{c['copies']}x  {c['name']}")
        groups = ydb.deck_boss_lines(self.repo.db_path, self.deck_id)
        if not groups:
            self._add_plan_note(self.boss_lines, "Noch keine Kombos angelegt.")
        # Startbarkeit je Linie (>=1 ihrer Starter in der Starthand) und
        # Resilienz (Interruption-Branches) an jeder Linie anzeigen.
        play = ydb.deck_line_playability(self.repo.db_path, self.deck_id)
        for g in groups:
            self._add_plan_header(self.boss_lines, g["boss_name"] or "Ohne Boss")
            for line in g["lines"]:
                cov = (f"{line['covered']}/{line['total']}"
                       if line["total"] else "leer")
                label = f"{cov}  {line['name']}"
                tips = []
                pl = play.get(line["combo_id"])
                if pl and pl["starters"]:
                    label = f"{cov} · {pct(pl['hands'][5])}  {line['name']}"
                    tips.append(
                        f"Startbar (≥1 Starter der Linie): "
                        f"Hand 5 {pct(pl['hands'][5])} · "
                        f"Hand 6 {pct(pl['hands'][6])} — "
                        f"{pl['starters']} Starter-Baustein(e), "
                        f"{pl['copies']} Kopie(n) im Main Deck"
                    )
                else:
                    tips.append(
                        "Kein Baustein als Starter eingestuft — "
                        "Startbarkeit unbekannt (Rollen im Tab 'Kombos')."
                    )
                if line["variants"]:
                    label += f"  ↳{len(line['variants'])}"
                    tips.append("Branches: " + ", ".join(line["variants"]))
                else:
                    tips.append(
                        "Keine Interruption-Branches erfasst — die Linie "
                        "steht bei gegnerischer Störung ohne Plan B da."
                    )
                item = QListWidgetItem(label)
                item.setToolTip("\n".join(tips))
                item.setData(Qt.ItemDataRole.UserRole, line["combo_id"])
                self.boss_lines.addItem(item)

    def _refresh_leverage(self) -> None:
        """Hebel-Reiter: Was-wäre-wenn je Main-Deck-Karte (deck_what_if).
        Die Liste sortiert nach Starter-Gewinn bei +1 (Hand 5) — 'wo lohnt
        die nächste Kopie am meisten'; Karten am 3-Kopien-Limit stehen
        unten. Klick auf eine Zeile zeigt die volle Aufschlüsselung."""
        self.lever_base.setText("")
        self.lever_list.clear()
        self.lever_detail.setText("")
        if self.deck_id is None or not self.repo.exists():
            return
        res = ydb.deck_what_if(self.repo.db_path, self.deck_id)
        if res["deck_size"] == 0 or not res["cards"]:
            self.lever_base.setText("Main Deck ist leer.")
            return
        base = res["base"]
        base5 = base[5]
        self.lever_base.setText(
            f"Basis (Hand 5): ≥1 Starter {pct(base5['starter'])}  ·  "
            f"≥1 Handtrap {pct(base5['handtrap'])}\n"
            "Was ändert eine Kopie mehr oder weniger? ±1 verschiebt auch "
            "die Deckgröße — deshalb bewegen selbst Karten ohne Rolle die "
            "Werte. Klick auf eine Zeile zeigt Details."
        )

        def gain(c) -> float:
            return c["plus"][5]["starter"] - base5["starter"]

        def sort_key(c):
            if c["plus"] is None:
                return (1, 0.0, c["name"])
            return (0, -gain(c), c["name"])

        for c in sorted(res["cards"], key=sort_key):
            roles = " [" + ", ".join(ROLE_DE[r] for r in c["roles"]) + "]" \
                if c["roles"] else ""
            if c["plus"] is None:
                text = f"{c['name']} ({c['copies']}x{roles}): 3-Kopien-Limit"
            else:
                # Die Kennzahl zeigen, die sich staerker bewegt (Handtraps
                # interessieren fuer Handtrap-Karten, sonst der Starter-Wert).
                d_start = gain(c)
                d_trap = c["plus"][5]["handtrap"] - base5["handtrap"]
                metric, d = ("starter", d_start)
                if abs(d_trap) > abs(d_start):
                    metric, d = ("handtrap", d_trap)
                label = "Starter" if metric == "starter" else "Handtrap"
                text = (
                    f"+1 {c['name']} ({c['copies']}x{roles}): {label} "
                    f"{pct(base5[metric])} → {pct(c['plus'][5][metric])}"
                )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, self._lever_detail_text(c, base))
            self.lever_list.addItem(item)

    @staticmethod
    def _lever_detail_text(c: dict, base: dict) -> str:
        """Volle Was-wäre-wenn-Aufschlüsselung einer Karte (beide
        Richtungen, beide Handgrößen, Starter/Handtrap/Brick)."""
        roles = " [" + ", ".join(ROLE_DE[r] for r in c["roles"]) + "]" \
            if c["roles"] else ""
        lines = [f"{c['name']} ({c['copies']}x{roles})"]
        for sign, vals in (("+1", c["plus"]), ("−1", c["minus"])):
            if vals is None:
                lines.append(f"{sign}: nicht möglich (3-Kopien-Limit)")
                continue
            for hand in sorted(vals):
                b, v = base[hand], vals[hand]
                lines.append(
                    f"{sign} · Hand {hand}:  "
                    f"Starter {pct(b['starter'])} → {pct(v['starter'])}  ·  "
                    f"Handtrap {pct(b['handtrap'])} → {pct(v['handtrap'])}  ·  "
                    f"Brick {pct(1 - b['starter'])} → {pct(1 - v['starter'])}"
                )
        return "\n".join(lines)

    def _show_lever_detail(self, current: QListWidgetItem, _previous=None) -> None:
        if current is None:
            self.lever_detail.setText("")
            return
        self.lever_detail.setText(current.data(Qt.ItemDataRole.UserRole) or "")

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
            f"Engpass: {ROLE_DE[gap]} ({copies} Kopien im Main Deck)"
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
            (f"Füllt die Lücke: {ROLE_DE[gap]}", fills_gap),
            ("Weitere Vorschläge", others),
        ):
            if not group:
                continue
            self._add_plan_header(self.suggestion_list, header)
            for s in group:
                roles = ", ".join(ROLE_DE[r] for r in s["roles"])
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
                out.append(it.data(SIM_COPIES_DATA))
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
            f"5er {pct(p5)}  ·  6er {pct(p6)}"
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
                tag = "  [" + ", ".join(ROLE_DE[r] for r in c["roles"]) + "]"
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
                label += "  [" + ", ".join(ROLE_DE[r] for r in c["roles"]) + "]"
            it = QListWidgetItem(label)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, c["card_id"])
            it.setData(SIM_COPIES_DATA, c["copies"])
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
