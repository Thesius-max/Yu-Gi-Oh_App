"""Hauptfenster: Tabs, Callback-Verdrahtung, Daten-Menue, Session.

Kompositionswurzel der App -- die Views kennen sich nicht, MainWindow
setzt die Callbacks (Detail -> Deck/Kombo, Deck -> Kombo/Karte).
Dazu der Suche-Tab (SearchView mit dem DetailPanel), das
"Daten"-Menue (Update-Checks im Hintergrund) und der Sitzungs-/
Fensterstatus via QSettings (nur mit restore_session=True).
"""

from __future__ import annotations

import os
import shutil
import sys

from PySide6.QtCore import QSettings, QThreadPool, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QMainWindow, QMessageBox, QProgressDialog, QTabWidget
)

import yugioh_db as ydb

from .carddetail import CardDetailDialog, DetailPanel
from .collection import CollectionView
from .combos import ComboView
from .deck import DeckView
from .manual import HelpView
from ._rules import MODES as R_MODES
from .playtest import PlayTestView
from .rulebook import RulebookView
from . import navigation
from .repository import CardRepository
from .search import SearchView
from .tasks import DbTask, DbTaskSignals


# ---------------------------------------------------------------------------
# Hauptfenster
# ---------------------------------------------------------------------------

class _BusyDialog(QProgressDialog):
    """Fortschrittsdialog ohne Abbruch: Esc und Fenster-Schliessen wirken
    nicht, solange das Update schreibt (sonst waere die App mitten im
    build_database wieder bedienbar). unlock_and_close() beendet ihn."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, "", 0, 0, parent)
        self._locked = True
        self.setCancelButton(None)

    def unlock_and_close(self) -> None:
        self._locked = False
        self.close()

    def reject(self) -> None:
        if not self._locked:
            super().reject()

    def closeEvent(self, event) -> None:
        if self._locked:
            event.ignore()
        else:
            super().closeEvent(event)


# Tab-Reihenfolge, als die Sitzung den Tab noch als Index speicherte.
_LEGACY_TABS = ("Suche", "Sammlung", "Deck", "Spielfeld", "Kombos", "Handbuch")


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
        self.search_view = SearchView(self.repo)
        # Kurzwege fuer Callbacks und Sitzungsstatus
        self.detail = self.search_view.detail
        self.splitter = self.search_view

        self.collection_view = CollectionView(self.repo)
        self.deck_view = DeckView(self.repo)
        self.combo_view = ComboView(self.repo)
        self.playtest_view = PlayTestView(self.repo)
        # Deck-Tab -> neue Kombo im Kombos-Tab oeffnen
        self.deck_view.open_combo_callback = self._open_combo
        self.playtest_view.open_combo_callback = self._open_combo
        # Deck-Tab (Vorschlaege) -> Karte im DetailPanel des Suche-Tabs
        self.deck_view.open_card_callback = self._show_card

        self.tabs = QTabWidget()
        self.tabs.addTab(self.search_view, "Suche")
        self.tabs.addTab(self.collection_view, "Sammlung")
        self.tabs.addTab(self.deck_view, "Deck")
        self.tabs.addTab(self.playtest_view, "Spielfeld")
        self.tabs.addTab(self.combo_view, "Kombos")
        self.rulebook_view = RulebookView()
        self.tabs.addTab(self.rulebook_view, "Regelwerk")
        self.tabs.addTab(HelpView(), "Handbuch")
        # Spruenge ins Regelwerk (Kartendetails, Wortlaut, Spielfeld-Protokoll)
        navigation.set_rulebook_opener(self.open_rulebook)
        # Karten-Details von ueberall (Regelwerk-Links, Einkaufsliste)
        self._card_dialog: CardDetailDialog | None = None
        navigation.set_card_opener(self.open_card_detail)
        # Geaenderte Rulings: Spielfeld-Tooltips aktualisieren.
        self.detail.rulings_changed.connect(self.playtest_view.reload_ruling_counts)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)
        self._build_data_menu()

        if self.repo.exists():
            pass  # Suchfilter befuellt SearchView selbst
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
                "oder per Kommandozeile:  python -m yugioh_db build",
            )
        self.search_view.search()
        if self._restore_session:
            self._restore_session_state()
            # Stiller Hinweis auf neue App-Versionen, kurz nach dem Start
            # (ein Mini-Request; offline/Fehler bleibt einfach unsichtbar).
            # Nur in der echten Sitzung -- Test-Instanzen gehen nie ins Netz.
            QTimer.singleShot(
                2000, lambda: self._check_app_version(manual=False)
            )
            # Nach einem App-Update: Kartendaten ohne Link-Pfeile/Typzeilen?
            QTimer.singleShot(1500, self._hint_missing_link_markers)

    def _on_tab_changed(self, index: int) -> None:
        # Sammlung beim Wechsel auf den Tab aktualisieren, damit gerade
        # hinzugefuegte Karten sofort erscheinen.
        widget = self.tabs.widget(index)
        if widget is self.search_view:
            self.search_view.refresh()          # Bestand-Spalte aktuell halten
        elif widget is self.collection_view:
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
        ins Deck uebernimmt sie der Deck-Tab ('Ins Deck übernehmen')."""
        card = self.repo.get_card(card_id)
        if card is None:
            return
        self.tabs.setCurrentWidget(self.search_view)  # haelt das DetailPanel
        self.search_view.show_card(card_id)

    def open_rulebook(self, key: str) -> None:
        """Regelwerk-Tab mit Kapitel 'key' zeigen (Ziel von navigation)."""
        if self.rulebook_view.show_section(key):
            self.tabs.setCurrentWidget(self.rulebook_view)
            self.raise_()
            self.activateWindow()

    def open_card_detail(self, card_id: int) -> None:
        """Karten-Pop-up (mit Wortlaut/Rulings) zeigen -- Ziel von
        navigation.open_card; eine Instanz wird wiederverwendet."""
        if self.repo.get_card(card_id) is None:
            return
        if self._card_dialog is None:
            self._card_dialog = CardDetailDialog(self.repo, self)
            self._card_dialog.rulings_changed.connect(
                self.playtest_view.reload_ruling_counts)
        self._card_dialog.load(card_id)
        self._card_dialog.show()
        self._card_dialog.raise_()
        self._card_dialog.activateWindow()

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
        # Tab per Titel statt Index: neue Tabs verschieben sonst den Stand.
        s.setValue("ui/tab", self.tabs.tabText(self.tabs.currentIndex()))
        did = self.deck_view.deck_id
        s.setValue("ui/deck_id", -1 if did is None else int(did))
        cv = self.collection_view
        s.setValue("coll/text", cv.filter_text.text())
        s.setValue("coll/cat", cv.filter_cat.currentData() or "")
        s.setValue("coll/attr", cv.filter_attr.currentData() or "")
        s.setValue("coll/arch", cv.filter_arch.currentData() or "")
        s.setValue("coll/untranslated", cv.filter_untranslated.isChecked())
        s.setValue("play/rules", self.playtest_view.rule_mode)

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
        # Regel-Modus des Spielfelds (aus/warnen/erzwingen).
        mode = s.value("play/rules", "warn") or "warn"
        if mode in {key for key, _ in R_MODES}:
            self.playtest_view.set_rule_mode(mode)
        # Zuletzt aktives Tab.
        tab = s.value("ui/tab", "")
        titles = [self.tabs.tabText(i) for i in range(self.tabs.count())]
        if isinstance(tab, str) and tab in titles:
            self.tabs.setCurrentIndex(titles.index(tab))
        elif str(tab).isdigit() and int(tab) < len(_LEGACY_TABS):
            # Alter Stand (Index vor dem Regelwerk-Tab) -> gleicher Tab per Titel.
            old = _LEGACY_TABS[int(tab)]
            if old in titles:
                self.tabs.setCurrentIndex(titles.index(old))

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
        self._app_check_signals = DbTaskSignals()
        self._app_check_signals.done.connect(
            lambda info: self._on_app_check_done(info, manual)
        )
        self._app_check_signals.failed.connect(
            lambda msg: self._on_app_check_failed(msg, manual)
        )
        QThreadPool.globalInstance().start(
            DbTask(ydb.check_app_update, self._app_check_signals)
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
        self._check_signals = DbTaskSignals()
        self._check_signals.done.connect(self._on_check_done)
        self._check_signals.failed.connect(lambda _msg: self._on_check_done(None))
        QThreadPool.globalInstance().start(
            DbTask(ydb.fetch_db_version, self._check_signals)
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

    def _hint_missing_link_markers(self) -> None:
        """Einmaliger Hinweis, wenn die Kartendaten aelter als die App sind:
        Link-Pfeile (Spielfeld-Zonenregeln) oder Typzeilen ([Drache/Synchro/
        Effekt] in der Kartenansicht) fehlen. 'Nein' merkt sich je Luecke
        (QSettings 'hints/card_data_declined'; das fruehere
        'hints/link_markers_declined' zaehlt fuer die Link-Pfeile) -- eine
        neue Luecke fragt also erneut. Die App funktioniert trotzdem
        (Spielfeld weist hin, die Typzeile wird hergeleitet)."""
        if not self.repo.exists():
            return
        settings = QSettings()
        declined = set(settings.value("hints/card_data_declined", [], type=list) or [])
        if settings.value("hints/link_markers_declined", False, type=bool):
            declined.add("link_markers")
        gaps = ydb.card_data_gaps(self.repo.db_path)
        if not any(n and key not in declined for key, n in gaps.items()):
            return
        parts = []
        if gaps["link_markers"]:
            parts.append(f"Link-Pfeile ({gaps['link_markers']} Link-Monster) — "
                         "das Spielfeld braucht sie für die Zonenregeln")
        if gaps["typeline"]:
            parts.append(f"Typzeilen ({gaps['typeline']} Monster) — z. B. "
                         "[Drache/Synchro/Effekt] in der Kartenansicht")
        reply = QMessageBox.question(
            self, "Kartendaten unvollständig",
            "Deinen Kartendaten fehlen:\n• " + "\n• ".join(parts)
            + "\n\nJetzt die Kartendaten aktualisieren? (braucht Internet; "
            "eigene Daten bleiben erhalten)",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_update(confirmed=True)
        else:
            settings.setValue("hints/card_data_declined",
                              sorted(declined | {k for k, n in gaps.items() if n}))

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
        # Mitten im Schreiben kein Abbruch (auch nicht per Esc).
        self._progress = _BusyDialog(
            "Lade Kartendaten von der YGOPRODeck-API …", self
        )
        self._progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._progress.setWindowTitle("Aktualisierung")
        self._progress.setMinimumDuration(0)
        self._progress.show()
        self._update_signals = DbTaskSignals()
        self._update_signals.done.connect(self._on_update_done)
        self._update_signals.failed.connect(self._on_update_failed)
        db_path = self.repo.db_path
        QThreadPool.globalInstance().start(
            DbTask(lambda: ydb.build_database(db_path), self._update_signals)
        )

    def _on_update_done(self, count) -> None:
        self._progress.unlock_and_close()
        self._set_data_actions_enabled(True)
        self._refresh_all()
        version = ydb.local_db_version(self.repo.db_path) or "unbekannt"
        QMessageBox.information(
            self, "Aktualisierung",
            f"{count} Karten aktualisiert (Datenbank-Version {version}).",
        )

    def _on_update_failed(self, msg: str) -> None:
        self._progress.unlock_and_close()
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
        if self.repo.exists():
            self.search_view.populate_filters()
        self.search_view.refresh()
        self.collection_view.refresh()
        self.deck_view.refresh()
        self.playtest_view.refresh()
        self.combo_view.refresh()
