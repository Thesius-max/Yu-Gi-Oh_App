"""
End-to-End-Ablaeufe ueber das echte Hauptfenster (MainWindow, offscreen):
ein Benutzer-Workflow je Test, quer durch mehrere Tabs, bedient ueber die
sichtbaren Knoepfe und Callbacks; geprueft wird UI UND Datenbank.

Wie alle GUI-Tests gegen eine Temp-Kopie der Dev-DB, ohne Netz: die
YGOPRODeck-/GitHub-Antworten werden gepatcht, modale Dialoge laufen ueber
die UiStubs (tests/_support.py). restore_session bleibt aus (sonst echte
QSettings + verzoegerter GitHub-Check); die Sitzungslogik wird mit
umgeleiteten INI-Settings direkt geprueft.

    QT_QPA_PLATFORM=offscreen python -m unittest tests.test_e2e_mainwindow
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import yugioh_db as ydb
from yugioh_db import api

from tests._support import HAS_QT, QtTestCase, api_payload_from_db, pump, remove_tree

if HAS_QT:
    from PySide6.QtCore import QCoreApplication, QSettings, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QDialog, QLineEdit, QListWidget, QMessageBox, QPushButton
    )

    from yugioh_gui import mainwindow
    from yugioh_gui.mainwindow import MainWindow

UR = 0x0100  # Qt.ItemDataRole.UserRole


class E2ETestCase(QtTestCase):
    def window(self, db_path=None) -> "MainWindow":
        w = self.track(MainWindow(db_path=db_path or self.db))
        w.resize(1200, 800)
        w.show()
        return w

    def use_deck(self, w, name: str = "RDA-Mitsu") -> int:
        """Deck in Deck-Tab und Spielfeld waehlen (wie per Dropdown) -- statt
        sich auf 'erstes Deck nach Name' zu verlassen."""
        deck = self.own_deck(name)
        w.deck_view.select_deck(deck)
        pv = w.playtest_view
        pv.deck_cb.setCurrentIndex(pv.deck_cb.findData(deck))
        self.assertEqual((w.deck_view.deck_id, pv._deck_id), (deck, deck))
        return deck

    # -- Bedienung wie ein Benutzer ------------------------------------------

    @staticmethod
    def click(widget, text: str) -> None:
        """Den Knopf mit genau dieser Beschriftung innerhalb von 'widget'
        ausloesen (wie ein Mausklick: clicked-Signal)."""
        buttons = [b for b in widget.findChildren(QPushButton) if b.text() == text]
        assert len(buttons) == 1, f"Knopf {text!r}: {len(buttons)} Treffer"
        buttons[0].click()

    @staticmethod
    def texts(listw):
        return [listw.item(i).text() for i in range(listw.count())]

    @staticmethod
    def select_data(listw, data):
        for i in range(listw.count()):
            if listw.item(i).data(UR) == data:
                listw.setCurrentRow(i)
                return listw.item(i)
        raise AssertionError(f"{data} nicht in der Liste {listw}")

    def search_and_select(self, w, card_id: int) -> None:
        w.tabs.setCurrentIndex(0)
        w.search_box.setText(self.display_name(card_id))
        QTest.keyClick(w.search_box, Qt.Key.Key_Return)
        self.select_data(w.results, card_id)
        self.assertEqual(w.detail.current_id, card_id)

    def table_names(self, view):
        return [view.table.item(r, 0).text() for r in range(view.table.rowCount())
                if view._entry_id_at(r) is not None]

    def user_data(self):
        return {t: [tuple(r) for r in self.query(f"SELECT * FROM {t} ORDER BY 1, 2")]
                for t in ("collection", "decks", "deck_cards", "card_translations",
                          "combos", "combo_cards", "combo_steps")}


class StartupTests(E2ETestCase):
    def test_first_start_of_new_version(self):
        self.set_meta("app_version", "0.8.0")     # Kopie stammt aus aelterer Version
        w = self.window()
        # Migrations-Sicherung der 0.8.0-DB vor ensure_schema, Version vermerkt.
        self.assertTrue(os.path.exists(self.db + ".bak-v0.8.0"))
        self.assertEqual(self.scalar("SELECT value FROM meta WHERE key='app_version'"),
                         ydb.APP_VERSION)
        self.assertIn(f"v{ydb.APP_VERSION}", w.windowTitle())
        self.assertEqual([w.tabs.tabText(i) for i in range(w.tabs.count())],
                         ["Suche", "Sammlung", "Deck", "Spielfeld", "Kombos", "Handbuch"])
        self.assertEqual(w.results.count(), 300)
        self.assertEqual(w.count_label.text(), "300 Treffer")
        self.assertEqual(w.type_cb.count(), 1 + len(w.repo.distinct("type")))
        self.assertEqual(w.type_cb.itemText(w.type_cb.findData("Spell Card")), "Zauberkarte")
        # Zweiter Start derselben Version: keine weitere Sicherung.
        os.remove(self.db + ".bak-v0.8.0")
        self.window()
        self.assertFalse(os.path.exists(self.db + ".bak-v0.8.0"))

    def test_start_without_database(self):
        missing = os.path.join(self._dir, "gibt-es-nicht.sqlite3")
        w = self.window(missing)
        self.assertEqual(self.ui.titles(), ["Datenbank fehlt"])
        self.assertEqual(w.results.count(), 0)
        self.assertEqual(w.collection_view.summary.text(), "Keine Datenbank vorhanden.")
        self.assertIn("Kein Deck ausgewählt", w.deck_view.status.text())
        for i in range(w.tabs.count()):                 # jeder Tab laesst sich oeffnen
            w.tabs.setCurrentIndex(i)
        self.assertFalse(os.path.exists(missing))       # nichts angelegt

    def test_failed_version_backup_does_not_block_start(self):
        with mock.patch.object(ydb, "migration_backup", side_effect=OSError("Platte voll")):
            w = self.window()
        self.assertEqual(self.ui.titles("warning"), ["Sicherung fehlgeschlagen"])
        self.assertIn("Platte voll", self.ui.last_text())
        self.assertEqual(w.results.count(), 300)            # App laeuft trotzdem

    def test_packaged_start_without_seed_warns(self):
        missing = os.path.join(self._dir, "fehlt.sqlite3")
        with mock.patch.object(mainwindow.sys, "frozen", True, create=True):
            self.window(missing)
        self.assertEqual(self.ui.titles("warning"), ["Datenbank fehlt"])
        self.assertIn("Kartendaten aktualisieren", self.ui.last_text())

    def test_link_monster_hit_shows_no_def(self):
        w = self.window()
        link = self.pick_ids("frame_type = 'link' AND atk IS NOT NULL")[0]
        self.search_and_select(w, link)
        item = w.results.currentItem()
        self.assertRegex(item.text(), r"\[ATK \d+\]$")
        self.assertNotIn("None", w.detail.stats.text())


class SearchCollectionFlowTests(E2ETestCase):
    def test_search_add_to_collection_and_see_it_there(self):
        w = self.window()
        cid = self.pick_ids("name_de IS NOT NULL AND frame_type = 'effect' AND id NOT IN "
                            "(SELECT card_id FROM collection) AND id NOT IN "
                            "(SELECT card_id FROM deck_cards)")[0]
        name = self.display_name(cid)
        entries = self.count("collection")
        self.search_and_select(w, cid)
        self.assertEqual(w.detail.name.text(), name)
        self.assertEqual(w.detail.owned.text(), "im Bestand: 0")
        w.detail.qty.setValue(2)
        self.click(w.detail, "Hinzufügen")
        self.click(w.detail, "Hinzufügen")               # gleicher Druck -> zusammengefuehrt
        self.assertEqual(w.detail.owned.text(), "im Bestand: 4")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM collection WHERE card_id = ?", (cid,)), 1)

        # 'Nur meine Sammlung' findet die Karte jetzt.
        w.only_coll.setChecked(True)
        w.search()
        self.assertIn(cid, [w.results.item(i).data(UR) for i in range(w.results.count())])

        # Tabwechsel -> Sammlung zeigt die neue Karte samt Summen.
        w.tabs.setCurrentWidget(w.collection_view)
        self.assertIn(name, self.table_names(w.collection_view))
        self.assertTrue(w.collection_view.summary.text().startswith(f"{entries + 1} Einträge"))

    def test_filters_combine(self):
        w = self.window()
        w.type_cb.setCurrentIndex(w.type_cb.findData("Effect Monster"))
        w.attr_cb.setCurrentIndex(w.attr_cb.findData("DARK"))
        w.level_cb.setCurrentIndex(w.level_cb.findData(4))
        w.atk_min.setValue(1500)
        self.click(w, "Suchen")
        ids = [w.results.item(i).data(UR) for i in range(w.results.count())]
        self.assertTrue(ids)
        placeholders = ",".join("?" * len(ids))
        bad = self.scalar(
            f"SELECT COUNT(*) FROM cards WHERE id IN ({placeholders}) AND NOT "
            "(type = 'Effect Monster' AND attribute = 'DARK' AND level = 4 AND atk >= 1500)",
            ids)
        self.assertEqual(bad, 0)
        self.assertEqual(w.count_label.text(), f"{len(ids)} Treffer")


class SearchDeckFlowTests(E2ETestCase):
    def test_build_a_deck_from_the_search_tab(self):
        w = self.window()
        w.tabs.setCurrentWidget(w.deck_view)
        self.ui.texts.append(("E2E Deck", True))
        self.click(w.deck_view, "Neues Deck")
        deck = w.deck_view.deck_id
        self.assertEqual(w.deck_view.deck_cb.currentText(), "E2E Deck")

        main, side = self.main_ids(2)
        extra = self.extra_ids(1)[0]
        self.search_and_select(w, main)
        for _ in range(4):
            self.click(w.detail, "+ Deck")
        self.assertEqual(self.ui.messages[-1],
                         ("information", "Deck", "Maximal 3 Kopien je Karte erreicht."))
        self.search_and_select(w, extra)
        self.click(w.detail, "+ Deck")                     # landet automatisch im Extra
        self.search_and_select(w, side)
        self.click(w.detail, "+ Side")
        self.assertEqual(ydb.deck_counts(self.db, deck), {"main": 3, "extra": 1, "side": 1})
        # Detail zeigt jetzt die Bindung im Deck (ohne Bestand -> Warnung).
        self.search_and_select(w, main)
        self.assertEqual(w.detail.owned.text(), "im Bestand: 0  ·  in Decks: 3  ⚠")

        w.tabs.setCurrentWidget(w.deck_view)
        dv = w.deck_view
        self.assertEqual([dv.panels[z].title() for z in ("main", "extra", "side")],
                         ["Main Deck  (3)", "Extra Deck  (1)", "Side Deck  (1)"])
        self.assertIn("✗ Main 3 (40-60)", dv.status.text())
        self.assertIn("✗ Bestand: 5 Kopie(n) fehlen (3 Karten)", dv.status.text())

    def test_deck_roundtrip_through_files(self):
        w = self.window()
        self.use_deck(w)
        w.tabs.setCurrentWidget(w.deck_view)
        dv = w.deck_view
        src = dv.deck_id
        base = os.path.join(self._dir, "RDA-Mitsu")
        for selected in ("YGOPro-Deck (*.ydk)", "Textdatei (*.txt)", "PDF-Datei (*.pdf)",
                         "Markdown für KI (*.md)"):
            self.ui.save_paths.append((base, selected))
            self.click(dv, "Exportieren…")
        for ext in (".ydk", ".txt", ".pdf", ".md"):
            self.assertGreater(os.path.getsize(base + ext), 100, ext)

        self.ui.open_paths.append((base + ".ydk", ""))
        self.ui.texts.append(("Reimport", True))
        self.click(dv, "Importieren…")
        new = dv.deck_id
        self.assertNotEqual(new, src)
        self.assertEqual(dv.deck_cb.currentText(), "Reimport")
        content = lambda d: sorted(tuple(r) for r in self.query(
            "SELECT card_id, zone, quantity FROM deck_cards WHERE deck_id = ?", (d,)))
        self.assertEqual(content(new), content(src))
        # Das neue Deck steht sofort auch im Spielfeld und im Kombo-Filter.
        w.tabs.setCurrentWidget(w.playtest_view)
        self.assertNotEqual(w.playtest_view.deck_cb.findData(new), -1)
        w.tabs.setCurrentWidget(w.combo_view)
        self.assertNotEqual(w.combo_view.deck_filter_cb.findData(new), -1)

    def test_suggestion_opens_card_and_can_be_added(self):
        w = self.window()
        self.use_deck(w)
        w.tabs.setCurrentWidget(w.deck_view)
        dv = w.deck_view
        item = next(dv.suggestion_list.item(i) for i in range(dv.suggestion_list.count())
                    if dv.suggestion_list.item(i).data(UR) is not None)
        cid = item.data(UR)
        self.assertIn(f"Korpus ({self.n_refs()} Referenz-Listen)", item.toolTip())
        dv._open_suggestion(item)                       # Doppelklick
        self.assertEqual(w.tabs.currentIndex(), 0)
        self.assertEqual(w.detail.current_id, cid)
        self.click(w.detail, "+ Deck")
        w.tabs.setCurrentWidget(dv)
        self.assertNotIn(cid, [dv.suggestion_list.item(i).data(UR)
                               for i in range(dv.suggestion_list.count())])


class ComboFlowTests(E2ETestCase):
    def test_combo_from_deck_roles_and_fahrplan(self):
        w = self.window()
        self.use_deck(w)
        w.tabs.setCurrentWidget(w.deck_view)
        dv, cv = w.deck_view, w.combo_view
        deck = dv.deck_id

        def pick_pieces(dlg):
            dlg.name_edit.setText("E2E-Linie")
            rows = [i for i in range(dlg.list.count()) if dlg.list.item(i).data(UR)][:3]
            for i in rows:
                dlg.list.item(i).setCheckState(Qt.CheckState.Checked)
            dlg._accept()
            return dlg.result()
        self.ui.dialogs["ComboFromDeckDialog"] = pick_pieces
        self.click(dv, "Neue Kombo aus diesem Deck…")

        # Callback: Sprung in den Kombos-Tab, Kombo ist gewaehlt.
        self.assertIs(w.tabs.currentWidget(), cv)
        combo = cv.combo_id
        self.assertEqual(cv.name_edit.text(), "E2E-Linie")
        self.assertEqual(cv.home_cb.currentData(), deck)
        self.assertEqual(cv.pieces.count(), 3)
        for row, role in ((0, "starter"), (1, "starter"), (2, "extender")):
            cv.pieces.setCurrentRow(row)
            cv.role_cb.setCurrentIndex(cv.role_cb.findData(role))
        cv.steps_edit.setPlainText("NS A\nEff1 A -> Add B (Deck)")

        # Ueber das DetailPanel einen weiteren Baustein zur aktiven Kombo.
        extra_piece = self.unowned_ids(1)[0]
        self.search_and_select(w, extra_piece)
        self.click(w.detail, "+ als Baustein zur aktiven Kombo")
        self.assertEqual(len(ydb.combo_cards(self.db, combo)), 4)

        w.tabs.setCurrentWidget(cv)                     # refresh sichert Schritte
        self.assertEqual([s["text"] for s in ydb.combo_steps(self.db, combo)],
                         ["NS A", "Eff1 A -> Add B (Deck)"])
        self.assertTrue(cv.combo_list.currentItem().text().startswith("E2E-Linie"))

        w.tabs.setCurrentWidget(dv)
        self.assertIn("Starthand 5: ≥1 Starter", dv.consistency.text())
        self.assertIn("E2E-Linie  —  3/4", self.texts(dv.combo_list))
        line = next(dv.boss_lines.item(i) for i in range(dv.boss_lines.count())
                    if dv.boss_lines.item(i).data(UR) == combo)
        self.assertIn("E2E-Linie", line.text())
        self.assertIn("Keine Interruption-Branches", line.toolTip())
        # Der fehlende Baustein wird vorgeschlagen, mit Begruendung.
        sug = {dv.suggestion_list.item(i).data(UR): dv.suggestion_list.item(i)
               for i in range(dv.suggestion_list.count())}
        self.assertIn("in 'E2E-Linie' zusammen mit", sug[extra_piece].toolTip())

        # Doppelklick auf die Linie springt in den Kombos-Reiter der Hilfe.
        dv._open_line(line)
        self.assertEqual(dv.combo_list.currentItem().data(UR), combo)
        self.click(dv, "Fehlende Bausteine ins Deck")
        self.assertIn("E2E-Linie  —  4/4", self.texts(dv.combo_list))

    def test_variant_shows_as_resilience_in_fahrplan(self):
        w = self.window()
        self.use_deck(w)
        deck = w.deck_view.deck_id
        card = ydb.deck_cards(self.db, deck, "main")[0]["card_id"]
        main = self.seed_combo("Hauptlinie", {card: "starter"}, deck_id=deck)
        w.tabs.setCurrentWidget(w.combo_view)
        w.combo_view._select_combo(main)
        self.ui.texts.append(("Gegen Ash", True))
        self.click(w.combo_view, "Neue Variante…")
        self.assertIn("    ↳ Gegen Ash", self.texts(w.combo_view.combo_list))
        w.tabs.setCurrentWidget(w.deck_view)
        line = next(w.deck_view.boss_lines.item(i) for i in range(w.deck_view.boss_lines.count())
                    if w.deck_view.boss_lines.item(i).data(UR) == main)
        self.assertTrue(line.text().endswith("↳1"))

    def test_closing_window_saves_pending_combo_edits(self):
        w = self.window()
        combo = ydb.create_combo(self.db, "Vorher")
        w.tabs.setCurrentWidget(w.combo_view)
        w.combo_view._select_combo(combo)
        w.combo_view.name_edit.setText("Nachher")
        w.combo_view.steps_edit.setPlainText("NS A")
        self.assertTrue(w.combo_view._save_timer.isActive())   # noch nicht gespeichert
        w.close()
        self.assertEqual(ydb.get_combo(self.db, combo)["name"], "Nachher")
        self.assertEqual([s["text"] for s in ydb.combo_steps(self.db, combo)], ["NS A"])


class PlaytestRecorderFlowTests(E2ETestCase):
    def test_record_on_board_and_refine_in_combos_tab(self):
        w = self.window()
        self.use_deck(w)
        w.tabs.setCurrentWidget(w.playtest_view)
        pv = w.playtest_view
        deck = pv._deck_id
        self.assertEqual(len(pv._hand), 5)
        self.click(pv, "● Aufzeichnen")
        first = pv._hand[0]
        pv._on_card_clicked(first)                      # aufnehmen ...
        pv._on_zone_clicked("m0")                       # ... und beschwoeren
        self.assertEqual(pv._rec_log, [f"NS {first.name}"])
        self.click(pv, "Ziehen")
        self.click(pv, "Ziehen")
        self.assertEqual(pv._rec_log[-1], "Draw 2")

        def save(dlg):
            preview = dlg.findChild(QListWidget)
            self.assertEqual(self.texts(preview), [f"1  NS {first.name}", "2  Draw 2"])
            self.assertEqual(dlg.name_edit.text(), f"{pv.deck_cb.currentText()} — neue Linie")
            dlg.name_edit.setText("Vom Spielfeld")
            return QDialog.DialogCode.Accepted
        self.ui.dialogs["_RecordSaveDialog"] = save
        pv._finish_recording()

        cv = w.combo_view
        self.assertIs(w.tabs.currentWidget(), cv)       # Sprung zum Nachschaerfen
        combo = cv.combo_id
        got = ydb.get_combo(self.db, combo)
        self.assertEqual((got["name"], got["deck_id"]), ("Vom Spielfeld", deck))
        self.assertTrue(got["notes"].startswith("Start: "))
        self.assertIn(f"End: {first.name}", got["notes"])
        self.assertEqual(cv.steps_edit.toPlainText(), f"NS {first.name}\nDraw 2")
        self.assertEqual([p["card_id"] for p in ydb.combo_cards(self.db, combo)],
                         [first.card_id])
        self.assertFalse(pv._recording)

    def test_new_start_hand_after_deck_change(self):
        w = self.window()
        w.tabs.setCurrentWidget(w.playtest_view)
        pv = w.playtest_view
        pv.deck_cb.setCurrentIndex(pv.deck_cb.findText("Sky Striker"))
        deck = self.own_deck("Sky Striker")
        self.assertEqual(pv._deck_id, deck)
        counts = ydb.deck_counts(self.db, deck)
        self.assertEqual(len(pv._hand) + len(pv._deck), counts["main"])
        self.assertEqual(len(pv._extra), counts["extra"])
        pv.hand_size_cb.setCurrentIndex(1)
        self.click(pv, "Neue Starthand")
        self.assertEqual(len(pv._hand), 6)


class TranslationFlowTests(E2ETestCase):
    def test_own_translation_shows_everywhere_and_can_be_removed(self):
        w = self.window()
        cid = self.scalar(
            "SELECT col.card_id FROM collection col JOIN cards c ON c.id = col.card_id "
            "WHERE c.name_de IS NULL ORDER BY col.entry_id LIMIT 1")
        english = self.display_name(cid)
        before = ydb.collection_untranslated_count(self.db)
        self.search_and_select(w, cid)

        def rename(value):
            def handler(dlg):
                dlg.findChild(QLineEdit).setText(value)
                return QDialog.DialogCode.Accepted
            return handler
        self.ui.dialogs["QDialog"] = rename("E2E Übersetzung")
        self.click(w.detail, "✎ DE")
        self.assertEqual(w.detail.name.text(), "E2E Übersetzung")

        w.search_box.setText("E2E Übersetzung")
        self.click(w, "Suchen")
        self.assertEqual([w.results.item(i).data(UR) for i in range(w.results.count())], [cid])
        w.tabs.setCurrentWidget(w.collection_view)
        self.assertIn("E2E Übersetzung", self.table_names(w.collection_view))
        self.assertIn(f"{before - 1} ohne deutsche Übersetzung",
                      w.collection_view.summary.text())

        self.search_and_select(w, cid)
        self.ui.dialogs["QDialog"] = rename("")
        self.click(w.detail, "✎ DE")
        self.assertEqual(w.detail.name.text(), english)
        self.assertIsNone(ydb.get_card_translation(self.db, cid))


class DataMenuFlowTests(E2ETestCase):
    """Menue 'Daten': Kartendaten-Update im Hintergrund-Thread (ohne Netz)."""

    def _patched_api(self, cards, de, version="999.1"):
        stack = [
            mock.patch.object(api, "fetch_all_cards", return_value=cards),
            mock.patch.object(api, "fetch_all_cards_de", return_value=de),
            mock.patch.object(api, "fetch_db_version", return_value=version),
        ]
        for p in stack:
            p.start()
            self.addCleanup(p.stop)

    def test_card_update_keeps_user_data(self):
        w = self.window()
        cards, de = api_payload_from_db(self.db)
        self._patched_api(cards, de)
        before = self.user_data()
        n_cards = self.count("cards")
        w._update_action.trigger()
        self.assertEqual(self.ui.titles("question"), ["Kartendaten aktualisieren"])
        self.assertTrue(os.path.exists(self.db + ".bak"))
        self.assertTrue(w._progress.isVisible())
        self.assertFalse(w._update_action.isEnabled())
        pump()
        self.assertFalse(w._progress.isVisible())
        self.assertTrue(w._update_action.isEnabled())
        self.assertEqual(self.ui.messages[-1],
                         ("information", "Aktualisierung",
                          f"{n_cards} Karten aktualisiert (Datenbank-Version 999.1)."))
        self.assertEqual(self.user_data(), before)
        self.assertEqual(w.type_cb.count(), 1 + len(w.repo.distinct("type")))
        self.assertEqual(w.results.count(), 300)

    def test_broken_api_answer_changes_nothing(self):
        w = self.window()
        self._patched_api([], {})
        before = self.user_data()
        cards_before = self.scalar("SELECT COUNT(*) FROM cards WHERE name_de IS NOT NULL")
        w._update_action.trigger()
        pump()
        kind, title, text = self.ui.messages[-1]
        self.assertEqual((kind, title), ("warning", "Aktualisierung fehlgeschlagen"))
        self.assertIn("API lieferte nur 0 Karten", text)
        self.assertEqual(self.user_data(), before)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM cards WHERE name_de IS NOT NULL"),
                         cards_before)
        self.assertTrue(w._update_action.isEnabled())

    def test_update_aborts_when_backup_fails(self):
        w = self.window()
        with mock.patch.object(mainwindow.shutil, "copy", side_effect=OSError("gesperrt")):
            w._update_action.trigger()
        self.assertEqual(self.ui.messages[-1][:2], ("warning", "Kartendaten aktualisieren"))
        self.assertIn("Update abgebrochen", self.ui.last_text())
        self.assertFalse(hasattr(w, "_progress"))            # kein Update gestartet
        self.assertTrue(w._update_action.isEnabled())

    def test_declined_update_does_nothing(self):
        w = self.window()
        self.ui.question = QMessageBox.StandardButton.No
        w._update_action.trigger()
        self.assertFalse(os.path.exists(self.db + ".bak"))
        self.assertTrue(w._update_action.isEnabled())

    def test_check_for_card_updates(self):
        self.set_meta("db_version", "145.88")
        w = self.window()
        cases = [
            (None, "warning", "Keine Antwort von der YGOPRODeck-API"),
            ("145.88", "information", "Die Kartendaten sind aktuell (Version 145.88)."),
            ("146.0", "question", "Neue Kartendaten verfügbar (lokal: 145.88, online: 146.0)"),
        ]
        self.ui.question = QMessageBox.StandardButton.No
        for remote, kind, snippet in cases:
            with mock.patch.object(ydb, "fetch_db_version", return_value=remote):
                w._check_action.trigger()
                self.assertFalse(w._check_action.isEnabled())
                pump()
            self.assertEqual(self.ui.messages[-1][0], kind, remote)
            self.assertIn(snippet, self.ui.messages[-1][2])
            self.assertTrue(w._check_action.isEnabled())
        self.assertFalse(os.path.exists(self.db + ".bak"))   # 'Nein' -> kein Update

    def test_check_for_app_update(self):
        w = self.window()
        news = {"version": "99.0.0", "url": "https://example.invalid/rel", "notes": "x" * 700}
        with mock.patch.object(mainwindow.QDesktopServices, "openUrl") as open_url:
            for result, kind, snippet in (
                (None, "information", f"Du nutzt die aktuelle Version (v{ydb.APP_VERSION})."),
                (news, "box", "Version 99.0.0 ist verfügbar"),
                (OSError("offline"), "warning", "Keine Antwort von GitHub (offline)"),
            ):
                effect = {"side_effect": result} if isinstance(result, Exception) \
                    else {"return_value": result}
                with mock.patch.object(ydb, "check_app_update", **effect):
                    w._app_check_action.trigger()
                    pump()
                self.assertEqual(self.ui.messages[-1][0], kind)
                self.assertIn(snippet, self.ui.messages[-1][2])
                self.assertTrue(w._app_check_action.isEnabled())
            # Lange Release-Notes werden gekuerzt; 'Spaeter' oeffnet nichts.
            self.assertIn("x" * 600 + "…", self.ui.messages[1][2])
            self.assertNotIn("x" * 601, self.ui.messages[1][2])
            open_url.assert_not_called()


class SessionStateTests(E2ETestCase):
    """Sitzungs-/Fensterstatus mit auf einen Temp-Ordner umgeleiteten
    INI-Settings -- die echten Einstellungen (Registry) bleiben unberuehrt."""

    def setUp(self):
        super().setUp()
        self.settings_dir = tempfile.mkdtemp(prefix="ygo_test_settings_")
        self.addCleanup(remove_tree, self.settings_dir)
        app = QCoreApplication.instance()
        old = (app.organizationName(), app.applicationName())
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                          self.settings_dir)
        app.setOrganizationName("YgoTestSuite")
        app.setApplicationName("YgoTestSuite")

        def restore():
            QSettings.setDefaultFormat(QSettings.Format.NativeFormat)
            app.setOrganizationName(old[0])
            app.setApplicationName(old[1])
        self.addCleanup(restore)

    def test_save_and_restore_roundtrip(self):
        w = self.window()
        cv = w.collection_view
        cv.filter_text.setText("Drache")
        cv.filter_cat.setCurrentIndex(cv.filter_cat.findData("monster"))
        cv.filter_untranslated.setChecked(True)
        w.deck_view.select_deck(self.own_deck("Sky Striker"))
        w.tabs.setCurrentWidget(w.deck_view)
        w._save_session_state()
        self.assertEqual(QSettings().format(), QSettings.Format.IniFormat)
        self.assertTrue(any(f.endswith(".ini") for _, _, files in os.walk(self.settings_dir)
                            for f in files))

        w2 = self.window()
        self.assertEqual(w2.tabs.currentIndex(), 0)
        w2._restore_session_state()
        self.assertEqual(w2.tabs.currentWidget(), w2.deck_view)
        self.assertEqual(w2.deck_view.deck_cb.currentText(), "Sky Striker")
        cv2 = w2.collection_view
        self.assertEqual(cv2.filter_text.text(), "Drache")
        self.assertEqual(cv2.filter_cat.currentData(), "monster")
        self.assertTrue(cv2.filter_untranslated.isChecked())
        shown = [cv2.table.item(r, 0).text() for r in range(cv2.table.rowCount())
                 if cv2._entry_id_at(r) is not None]
        expected = ydb.list_collection(self.db, text="Drache", category="monster",
                                       untranslated_only=True)
        self.assertEqual(len(shown), len(expected))

    def test_restore_with_empty_settings_keeps_defaults(self):
        w = self.window()
        w._restore_session_state()
        self.assertEqual(w.tabs.currentIndex(), 0)
        self.assertEqual(w.deck_view.deck_cb.currentText(), ydb.list_decks(self.db)[0]["name"])
        self.assertEqual(w.collection_view.filter_text.text(), "")


if __name__ == "__main__":
    unittest.main()
