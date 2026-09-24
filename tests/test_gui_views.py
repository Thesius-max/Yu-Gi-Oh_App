"""
Offscreen-Tests der Tab-Views (DeckView, CollectionView, ComboView):
Aktionen wirken auf die DB, Auswahl/Listen bleiben konsistent, modale
Dialoge laufen ueber die UiStubs aus tests/_support.py. Gegen eine
Temp-Kopie der Dev-DB, ohne Netz.

    python -m unittest tests.test_gui_views
"""

from __future__ import annotations

import os
import random
import unittest

import yugioh_db as ydb

from tests._support import HAS_QT, QtTestCase

if HAS_QT:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QDialog, QMessageBox, QSpinBox

    from yugioh_gui.collection import CollectionView
    from yugioh_gui.combos import ComboView
    from yugioh_gui.deck import DeckView
    from yugioh_gui.repository import CardRepository

UR = 0x0100  # Qt.ItemDataRole.UserRole


class _ViewTestCase(QtTestCase):
    def setUp(self):
        super().setUp()
        self.repo = CardRepository(self.db)

    @staticmethod
    def texts(listw):
        return [listw.item(i).text() for i in range(listw.count())]

    @staticmethod
    def select(listw, data):
        for i in range(listw.count()):
            if listw.item(i).data(UR) == data:
                listw.setCurrentRow(i)
                return listw.item(i)
        raise AssertionError(f"{data} nicht in der Liste")


# ---------------------------------------------------------------------------
# Aus test_gui.py uebernommen
# ---------------------------------------------------------------------------

class LegacyGuiTests(_ViewTestCase):
    def _main_card(self, n: int = 1) -> list[int]:
        return [r[0] for r in self.query(
            "SELECT id FROM cards WHERE frame_type = 'effect' LIMIT ?", (n,))]

    def test_deck_selection_survives_quantity_change(self):
        cid = self._main_card()[0]
        deck = ydb.create_deck(self.db, "AAA Test")
        ydb.add_card_to_deck(self.db, deck, cid, count=1)
        view = self.track(DeckView(self.repo))
        view.deck_cb.setCurrentIndex(view.deck_cb.findData(deck))
        panel = view.panels["main"]
        row = next(i for i in range(panel.list.count())
                   if panel.list.item(i).data(0x0100) == cid)
        panel.list.setCurrentRow(row)
        view.adjust("main", +1)
        self.assertEqual(panel.selected_card_id(), cid)
        view.adjust("main", +1)          # zweiter Klick wirkt ohne Neu-Anwahl
        self.assertEqual(ydb.deck_counts(self.db, deck)["main"], 3)

    def test_collection_header_and_summary_follow_quantity_edit(self):
        view = self.track(CollectionView(self.repo))
        view.refresh()
        view.filter_cat.setCurrentIndex(view.filter_cat.findData("monster"))
        view._apply_filters()
        header = view.table.item(0, 0)
        count = int(header.text().rsplit("(", 1)[1].rstrip(")"))
        qty = view.table.item(1, 1)
        old = int(qty.data(Qt.ItemDataRole.EditRole))
        qty.setData(Qt.ItemDataRole.EditRole, old + 5)   # loest itemChanged aus
        self.assertTrue(header.text().endswith(f"({count + 5})"))
        self.assertIn("Filter:", view.summary.text())

    def test_variant_label_keeps_indent_after_autosave(self):
        main = ydb.create_combo(self.db, "Haupt")
        var = ydb.create_combo(self.db, "Var")
        ydb.set_combo_parent(self.db, var, main)
        view = self.track(ComboView(self.repo))
        view.refresh()
        self.assertTrue(view._select_combo(var))
        view.name_edit.setText("Var2")
        view.flush_pending()
        texts = [view.combo_list.item(i).text()
                 for i in range(view.combo_list.count())]
        self.assertIn("    ↳ Var2", texts)

    def test_combo_list_shows_coverage_and_variants(self):
        main = ydb.create_combo(self.db, "Haupt")
        ydb.add_combo_card(self.db, main, self._main_card()[0], 1)
        var = ydb.create_combo(self.db, "Var")
        ydb.set_combo_parent(self.db, var, main)
        view = self.track(ComboView(self.repo))
        view.refresh()
        texts = [view.combo_list.item(i).text()
                 for i in range(view.combo_list.count())]
        cov = ydb.combo_coverage_collection(self.db, main)
        expected = view._combo_label(ydb.get_combo(self.db, main), cov)
        self.assertEqual(texts, [expected, "    ↳ Var"])


# ---------------------------------------------------------------------------
# DeckView
# ---------------------------------------------------------------------------

class DeckViewTests(_ViewTestCase):
    def _view(self, deck=None):
        view = self.track(DeckView(self.repo))
        if deck is not None:
            view.select_deck(deck)
        return view

    def test_initial_deck_and_status(self):
        view = self._view()
        own = [d["name"] for d in ydb.list_decks(self.db)]       # ohne Korpus
        self.assertEqual([view.deck_cb.itemText(i) for i in range(view.deck_cb.count())], own)
        self.assertEqual(view.deck_cb.currentText(), own[0])       # erstes nach Name
        status = view.status.text()
        for ok, text in ydb.validate_deck(self.db, view.deck_id):
            self.assertIn(("✓ " if ok else "✗ ") + text, status)
        missing = sum(a["missing"] for a in ydb.deck_availability(self.db, view.deck_id))
        self.assertIn(f"Bestand: {missing} Kopie(n) fehlen" if missing else "✓ Bestand gedeckt",
                      status)
        main = ydb.deck_counts(self.db, view.deck_id)["main"]
        self.assertEqual(view.panels["main"].title(), f"Main Deck  ({main})")

    def test_main_zone_grouped_by_category(self):
        view = self._view(self.own_deck())
        headers = [t for t in self.texts(view.panels["main"].list) if t.startswith("— ")]
        self.assertTrue(headers[0].startswith("— Monster ("))
        total = sum(int(h.rsplit("(", 1)[1].split(")")[0]) for h in headers)
        self.assertEqual(total, ydb.deck_counts(self.db, view.deck_id)["main"])
        self.assertFalse(any(t.startswith("— ") for t in self.texts(view.panels["extra"].list)))

    def test_shortage_marker(self):
        cid = self.unowned_ids(1)[0]
        deck = ydb.create_deck(self.db, "AAA Mangel")
        ydb.add_card_to_deck(self.db, deck, cid, count=2)
        view = self._view(deck)
        item = self.select(view.panels["main"].list, cid)
        self.assertTrue(item.text().endswith("⚠ fehlt 2"))
        self.assertIn("2 Kopie(n) fehlen", item.toolTip())
        self.assertIn("✗ Bestand: 2 Kopie(n) fehlen (1 Karten)", view.status.text())

    def test_new_and_delete_deck(self):
        view = self._view()
        n_decks = view.deck_cb.count()
        self.ui.texts.append(("  Neues Deck  ", True))
        view._new_deck()
        self.assertEqual(view.deck_cb.currentText(), "Neues Deck")
        new_id = view.deck_id
        self.ui.texts.append(("", True))                      # leerer Name: nichts
        view._new_deck()
        self.assertEqual(view.deck_cb.count(), n_decks + 1)
        self.ui.question = QMessageBox.StandardButton.No
        view._delete_deck()
        self.assertIn(new_id, [d["deck_id"] for d in ydb.list_decks(self.db)])
        self.ui.question = QMessageBox.StandardButton.Yes
        view._delete_deck()
        self.assertNotIn(new_id, [d["deck_id"] for d in ydb.list_decks(self.db)])
        self.assertEqual(view.deck_cb.count(), n_decks)

    def test_zone_actions(self):
        a = self.main_ids(1)[0]
        e = self.extra_ids(1)[0]
        deck = ydb.create_deck(self.db, "AAA Zonen")
        ydb.add_card_to_deck(self.db, deck, a, count=2)
        ydb.add_card_to_deck(self.db, deck, e)
        view = self._view(deck)
        self.select(view.panels["main"].list, a)
        view.move("main", "side")
        self.assertEqual(ydb.deck_counts(self.db, deck), {"main": 1, "extra": 1, "side": 1})
        self.select(view.panels["side"].list, a)
        view.move_from_side()
        self.select(view.panels["extra"].list, e)
        view.move("extra", "side")
        self.select(view.panels["side"].list, e)
        view.move_from_side()                          # zurueck ins Extra, nicht Main
        self.assertEqual(ydb.deck_counts(self.db, deck), {"main": 2, "extra": 1, "side": 0})
        self.select(view.panels["main"].list, a)
        view.adjust("main", -1)
        view.remove("extra")                            # ohne Auswahl: nichts
        self.assertEqual(ydb.deck_counts(self.db, deck)["extra"], 1)
        self.select(view.panels["extra"].list, e)
        view.remove("extra")
        self.assertEqual(ydb.deck_counts(self.db, deck), {"main": 1, "extra": 0, "side": 0})
        view.panels["main"].list.setCurrentRow(0)       # Kopfzeile: keine Karte
        view.adjust("main", +1)
        self.assertEqual(ydb.deck_counts(self.db, deck)["main"], 1)

    def test_add_card_from_outside(self):
        cid, other = self.main_ids(2)
        view = self._view()
        for _ in range(3):
            self.assertEqual(view.add_card(cid), (1, ""))
        self.assertEqual(view.add_card(cid), (0, "Maximal 3 Kopien je Karte erreicht."))
        self.assertEqual(view.add_card(other, to_side=True), (1, ""))
        self.assertEqual(ydb.deck_counts(self.db, view.deck_id)["side"], 1)

    def test_add_card_without_deck(self):
        for d in ydb.list_decks(self.db):
            ydb.delete_deck(self.db, d["deck_id"])
        view = self._view()
        self.assertIsNone(view.deck_id)
        self.assertIn("Kein Deck ausgewählt", view.status.text())
        self.assertEqual(view.add_card(self.main_ids(1)[0]), (0, ""))
        self.assertEqual(self.ui.titles(), ["Kein Deck"])

    def test_add_card_dialog(self):
        cid = self.translated_card()
        deck = ydb.create_deck(self.db, "AAA Dialog")
        view = self._view(deck)

        def pick(dlg):
            dlg.search_box.setText(self.display_name(cid))
            self.select(dlg.list, cid)
            return QDialog.DialogCode.Accepted
        self.ui.dialogs["CardSearchDialog"] = pick
        view.add_card_dialog("side")
        self.assertEqual(ydb.deck_counts(self.db, deck)["side"], 1)

    def _seed_lines(self, deck):
        rows = ydb.deck_cards(self.db, deck, "main")
        starters = [r["card_id"] for r in rows[:2]]
        boss = ydb.deck_cards(self.db, deck, "extra")[0]["card_id"]
        missing = self.unowned_ids(1)[0]
        main = self.seed_combo("Hauptlinie", {starters[0]: "starter", starters[1]: "extender",
                                              boss: "payoff", missing: None},
                               steps=["NS A", "SS B"])
        ydb.set_combo_boss(self.db, main, boss)
        var = self.seed_combo("Gegen Ash", {starters[0]: None})
        ydb.set_combo_parent(self.db, var, main)
        return main, starters, boss, missing

    def test_helper_tabs_with_seeded_combo(self):
        deck = self.own_deck()
        main, starters, boss, missing = self._seed_lines(deck)
        view = self._view(deck)
        self.assertIn("Starthand 5: ≥1 Starter", view.consistency.text())
        roles = self.texts(view.role_summary)
        self.assertTrue(any(t.startswith("— Starter (") for t in roles))
        lines = self.texts(view.boss_lines)
        self.assertEqual(lines[0], f"— {self.display_name(boss)} —")
        self.assertIn("Hauptlinie", lines[1])
        self.assertTrue(lines[1].endswith("↳1"))
        self.assertIn("Branches: Gegen Ash", view.boss_lines.item(1).toolTip())
        self.assertEqual(self.texts(view.combo_list), ["Hauptlinie  —  3/4"])
        self.assertTrue(view.gap_label.text().startswith("Engpass: Handtrap"))
        sug = {view.suggestion_list.item(i).data(UR): view.suggestion_list.item(i)
               for i in range(view.suggestion_list.count())}
        self.assertIn(missing, sug)
        self.assertIn("in 'Hauptlinie' zusammen mit", sug[missing].toolTip())
        self.assertEqual(view.lever_list.count(), len(ydb.deck_main_cards(self.db, deck)))

    def test_combo_tab_pieces_steps_and_line_jump(self):
        deck = self.own_deck()
        main, *_ = self._seed_lines(deck)
        view = self._view(deck)
        view.helper_tabs.setCurrentIndex(1)
        view._open_line(view.boss_lines.item(1))
        self.assertEqual(view.helper_tabs.currentIndex(), 0)
        self.assertEqual(view.combo_list.currentItem().data(UR), main)
        self.assertEqual(self.texts(view.combo_steps), ["1.  NS A", "2.  SS B"])
        self.assertEqual(sum(t.startswith("✗") for t in self.texts(view.combo_pieces)), 1)
        view._open_line(view.boss_lines.item(0))       # Kopfzeile: nichts
        self.assertEqual(view.combo_list.currentItem().data(UR), main)

    def test_add_missing_pieces(self):
        deck = self.own_deck()
        main, starters, boss, missing = self._seed_lines(deck)
        view = self._view(deck)
        view._select_combo(main)
        view._add_missing()
        self.assertEqual(ydb.combo_coverage(self.db, main, deck)["covered"], 4)
        self.assertEqual(self.ui.messages, [])
        self.assertEqual(view.combo_list.currentItem().data(UR), main)

    def test_add_missing_reports_copy_limit(self):
        cid = self.main_ids(1)[0]
        deck = ydb.create_deck(self.db, "AAA Limit")
        ydb.add_card_to_deck(self.db, deck, cid, zone="side", count=3)
        combo = ydb.create_combo(self.db, "K")
        ydb.add_combo_card(self.db, combo, cid, 2)
        view = self._view(deck)
        view._select_combo(combo)
        view._add_missing()
        self.assertEqual(self.ui.titles(), ["Nicht alle Bausteine hinzugefügt"])
        self.assertIn("Maximal 3 Kopien", self.ui.last_text())

    def test_simulator(self):
        deck = self.own_deck()
        main, starters, *_ = self._seed_lines(deck)
        view = self._view(deck)
        self.assertEqual(view.sim_cards.count(), len(ydb.deck_main_cards(self.db, deck)))
        self.assertEqual(view.sim_prob.text(), "Karten ankreuzen für die Wahrscheinlichkeit.")
        view._fill_from_combo()
        self.assertEqual(view.sim_prob.text(), "Erst im Reiter 'Kombos' eine Kombo wählen.")
        view._select_combo(main)
        view._fill_from_combo()
        checked = view._checked_sim_copies()
        self.assertEqual(len(checked), 2)
        p5 = ydb.prob_open_all(ydb.deck_counts(self.db, deck)["main"], checked, 5)
        self.assertIn(f"5er {100 * p5:.1f}".replace(".", ","), view.sim_prob.text())
        view.sim_mode_cb.setCurrentIndex(1)            # mindestens eine
        self.assertIn("mindestens eine", view.sim_prob.text())
        random.seed(3)
        view.sim_hand_cb.setCurrentIndex(1)
        view._draw_hand()
        self.assertEqual(view.sim_hand.count(), 6)
        self.assertRegex(view.sim_verdict.text(), r"^(✓ Hand enthält einen Starter\.|✗ Brick)")

    def test_simulator_keeps_checks_across_refresh(self):
        view = self._view()
        view.sim_cards.item(0).setCheckState(Qt.CheckState.Checked)
        view.refresh()
        self.assertEqual(view.sim_cards.item(0).checkState(), Qt.CheckState.Checked)

    def test_leverage_detail(self):
        deck = self.own_deck()
        self._seed_lines(deck)
        view = self._view(deck)
        self.assertTrue(view.lever_base.text().startswith("Basis (Hand 5): ≥1 Starter"))
        view.lever_list.setCurrentRow(0)
        detail = view.lever_detail.text()
        self.assertIn("· Hand 5:", detail)
        self.assertIn("· Hand 6:", detail)

    def test_suggestion_opens_card_callback(self):
        deck = self.own_deck()
        *_, missing = self._seed_lines(deck)
        view = self._view(deck)
        opened = []
        view.open_card_callback = opened.append
        item = next(view.suggestion_list.item(i) for i in range(view.suggestion_list.count())
                    if view.suggestion_list.item(i).data(UR) == missing)
        view._open_suggestion(item)
        self.assertEqual(opened, [missing])

    def test_new_combo_from_deck(self):
        deck = self.own_deck()
        view = self._view(deck)
        opened = []
        view.open_combo_callback = opened.append

        def fill(dlg):
            dlg.name_edit.setText("Aus dem Deck")
            dlg.list.item(1).setCheckState(Qt.CheckState.Checked)
            dlg.list.item(2).setCheckState(Qt.CheckState.Checked)
            dlg._accept()
            return dlg.result()
        self.ui.dialogs["ComboFromDeckDialog"] = fill
        view._new_combo_from_deck()
        combo = ydb.get_combo(self.db, opened[0])
        self.assertEqual((combo["name"], combo["deck_id"]), ("Aus dem Deck", deck))
        self.assertEqual(len(ydb.combo_cards(self.db, opened[0])), 2)
        self.assertIn("Aus dem Deck  —  2/2", self.texts(view.combo_list))

    def test_export_all_formats(self):
        view = self._view(self.own_deck())
        main = ydb.deck_counts(self.db, view.deck_id)["main"]
        base = os.path.join(self._dir, "export")
        for selected, ext, check in (
            ("YGOPro-Deck (*.ydk)", ".ydk", lambda t: t.startswith("#created by")),
            ("Textdatei (*.txt)", ".txt", lambda t: f"== Main Deck ({main}) ==" in t),
            ("Markdown für KI (*.md)", ".md", lambda t: t.startswith("# Deck: RDA-Mitsu")),
        ):
            self.ui.save_paths.append((base, selected))
            view._export_deck()
            with open(base + ext, encoding="utf-8") as fh:
                self.assertTrue(check(fh.read()), ext)
            self.assertEqual(view.status.text(), f"Deck exportiert nach {base + ext}")
        self.ui.save_paths.append((base, "PDF-Datei (*.pdf)"))
        view._export_deck()
        with open(base + ".pdf", "rb") as fh:
            self.assertTrue(fh.read(4) == b"%PDF")
        self.ui.save_paths.append((base + "-linien", "Textdatei (*.txt)"))
        view._export_combos()
        with open(base + "-linien.txt", encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith("Kombo-Linien — RDA-Mitsu"))
        view._export_deck()                          # Abbruch im Dialog
        self.assertEqual(self.ui.messages, [])

    def test_import_deck(self):
        view = self._view()
        source = self.own_deck("Sky Striker")
        path = os.path.join(self._dir, "Import.ydk")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(ydb.export_deck_ydk(self.db, source) + "kaputt\n")
        self.ui.open_paths.append((path, ""))
        self.ui.texts.append(("AAA Import", True))
        view._import_deck()
        self.assertEqual(view.deck_cb.currentText(), "AAA Import")
        self.assertEqual(ydb.deck_counts(self.db, view.deck_id), ydb.deck_counts(self.db, source))
        self.assertEqual(self.ui.titles(), ["Deck importiert"])      # 'kaputt' gemeldet
        self.assertIn("Nicht lesbare Zeilen (übersprungen): kaputt", self.ui.last_text())

    def test_import_unknown_cards_only(self):
        view = self._view()
        n_decks = view.deck_cb.count()
        path = os.path.join(self._dir, "Leer.ydk")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#main\n999999999\n")
        self.ui.open_paths.append((path, ""))
        self.ui.texts.append(("Leer", True))
        view._import_deck()
        self.assertEqual(self.ui.titles("warning"), ["Import fehlgeschlagen"])
        self.assertEqual(view.deck_cb.count(), n_decks)

    def test_corpus_buttons(self):
        view = self._view()
        self.ui.dialogs["DeckCorpusDiffDialog"] = lambda d: (
            self.assertEqual(d.ref_cb.count(), self.n_refs()) or QDialog.DialogCode.Accepted)
        self.ui.dialogs["ReferenceDeckDialog"] = lambda d: QDialog.DialogCode.Accepted
        view._open_corpus_diff()
        view._open_corpus()
        for r in ydb.list_reference_decks(self.db):
            ydb.delete_deck(self.db, r["deck_id"])
        view._open_corpus_diff()
        self.assertEqual(self.ui.titles(), ["Vergleich"])

    def test_card_detail_dialog_is_reused(self):
        view = self._view(self.own_deck())
        cid = ydb.deck_cards(self.db, view.deck_id, "extra")[0]["card_id"]
        view.panels["extra"]._on_double_click(view.panels["extra"].list.item(0))
        dlg = view._detail_dialog
        self.assertTrue(dlg.isVisible())
        self.assertEqual(dlg.current_id, cid)
        view.open_card_detail(self.main_ids(1)[0])
        self.assertIs(view._detail_dialog, dlg)
        dlg.close()


# ---------------------------------------------------------------------------
# CollectionView
# ---------------------------------------------------------------------------

class CollectionViewTests(_ViewTestCase):
    def _data_rows(self, view):
        return [r for r in range(view.table.rowCount()) if view._entry_id_at(r) is not None]

    def test_initial_table_and_summary(self):
        view = self.track(CollectionView(self.repo))
        self.assertEqual(len(self._data_rows(view)), self.count("collection"))
        entries, unique, total, untranslated = ydb.collection_summary_stats(self.db)
        self.assertGreater(untranslated, 0, "Dev-DB braucht unuebersetzte Bestandskarten")
        self.assertEqual(view.summary.text(),
                         f"{entries} Einträge  ·  {unique} verschiedene Karten  ·  "
                         f"{total} Karten gesamt  ·  {untranslated} ohne deutsche Übersetzung")
        self.assertTrue(view.table.item(0, 0).text().startswith("Monster  ("))
        self.assertEqual(view.filter_attr.itemData(0), None)
        self.assertEqual(view.filter_attr.count() - 1,
                         len(ydb.collection_distinct(self.db, "attribute")))

    def test_text_and_untranslated_filters(self):
        view = self.track(CollectionView(self.repo))
        view.filter_untranslated.setChecked(True)
        rows = self._data_rows(view)
        self.assertEqual(len(rows), len(ydb.list_collection(self.db, untranslated_only=True)))
        for r in rows:
            self.assertIn("Noch keine deutsche Übersetzung", view.table.item(r, 0).toolTip())
        view.filter_untranslated.setChecked(False)
        name = view.table.item(self._data_rows(view)[0], 0).text()
        view.filter_text.setText(name)
        view._apply_filters()                     # statt auf den Debounce-Timer zu warten
        self.assertTrue(all(name.casefold() in view.table.item(r, 0).text().casefold()
                            for r in self._data_rows(view)))
        self.assertIn("Filter:", view.summary.text())

    def test_quantity_edit_writes_through(self):
        view = self.track(CollectionView(self.repo))
        row = self._data_rows(view)[0]
        entry = view._entry_id_at(row)
        view.table.item(row, 1).setData(Qt.ItemDataRole.EditRole, 9)
        self.assertEqual(self.scalar("SELECT quantity FROM collection WHERE entry_id = ?",
                                     (entry,)), 9)
        view.table.item(row, 1).setData(Qt.ItemDataRole.EditRole, 0)    # ignoriert
        self.assertEqual(self.scalar("SELECT quantity FROM collection WHERE entry_id = ?",
                                     (entry,)), 9)

    def test_quantity_delegate(self):
        view = self.track(CollectionView(self.repo))
        delegate = view.table.itemDelegateForColumn(1)
        row = self._data_rows(view)[0]
        index = view.table.model().index(row, 1)
        editor = delegate.createEditor(view.table.viewport(), None, index)
        self.assertIsInstance(editor, QSpinBox)
        self.assertEqual((editor.minimum(), editor.maximum()), (1, 9999))
        delegate.setEditorData(editor, index)
        self.assertEqual(editor.value(), int(index.data(Qt.ItemDataRole.EditRole)))
        editor.setValue(42)
        delegate.setModelData(editor, view.table.model(), index)
        self.assertEqual(self.scalar("SELECT quantity FROM collection WHERE entry_id = ?",
                                     (view._entry_id_at(row),)), 42)

    def test_remove_selected(self):
        view = self.track(CollectionView(self.repo))
        view.table.setCurrentCell(0, 0)                      # Kopfzeile
        view._remove_selected()
        self.assertEqual(self.ui.messages, [])
        row = self._data_rows(view)[0]
        entry = view._entry_id_at(row)
        before = self.count("collection")
        view.table.setCurrentCell(row, 0)
        self.ui.question = QMessageBox.StandardButton.No
        view._remove_selected()
        self.assertEqual(self.count("collection"), before)
        self.ui.question = QMessageBox.StandardButton.Yes
        view._remove_selected()
        self.assertIsNone(self.scalar("SELECT 1 FROM collection WHERE entry_id = ?", (entry,)))
        self.assertEqual(len(self._data_rows(view)), before - 1)

    def test_export_uses_active_filter(self):
        view = self.track(CollectionView(self.repo))
        view.filter_cat.setCurrentIndex(view.filter_cat.findData("trap"))
        base = os.path.join(self._dir, "sammlung")
        for selected, ext in (("Textdatei (*.txt)", ".txt"), ("Markdown für KI (*.md)", ".md"),
                              ("PDF-Datei (*.pdf)", ".pdf")):
            self.ui.save_paths.append((base, selected))
            view._export()
            self.assertTrue(os.path.getsize(base + ext) > 0, ext)
            self.assertEqual(view.summary.text(), f"Sammlung exportiert nach {base + ext}")
        with open(base + ".txt", encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("Sammlung — Klasse=Falle\n"))
        self.assertNotIn("== Monster", text)

    def test_detail_popup_and_hover_resolver(self):
        view = self.track(CollectionView(self.repo))
        view._open_detail(0, 0)                       # Kopfzeile: nichts
        view._open_detail(1, 1)                       # Mengenspalte: Editor statt Pop-up
        self.assertIsNone(view._detail_dialog)
        view._open_detail(1, 0)
        cid = view.table.item(1, 0).data(Qt.ItemDataRole.UserRole + 1)
        self.assertEqual(view._detail_dialog.current_id, cid)
        view._detail_dialog.close()
        view.resize(900, 600)
        view.show()
        header_rect = view.table.visualItemRect(view.table.item(0, 0))
        self.assertIsNone(view._hover_card_id(QPoint(header_rect.left() + 3,
                                                     header_rect.center().y())))
        rect = view.table.visualItemRect(view.table.item(1, 0))
        self.assertEqual(view._hover_card_id(QPoint(rect.left() + 3, rect.center().y())), cid)

    def test_without_database(self):
        view = self.track(CollectionView(CardRepository(os.path.join(self._dir, "fehlt"))))
        self.assertEqual(view.summary.text(), "Keine Datenbank vorhanden.")
        view._export()
        self.assertEqual(self.ui.titles("warning"), ["Export nicht möglich"])


# ---------------------------------------------------------------------------
# ComboView
# ---------------------------------------------------------------------------

class ComboViewTests(_ViewTestCase):
    def _view(self):
        return self.track(ComboView(self.repo))

    def test_empty_library(self):
        view = self._view()
        self.assertEqual(view.combo_list.count(), 0)
        self.assertFalse(view.editor.isEnabled())
        self.assertEqual([view.deck_filter_cb.itemText(i) for i in range(view.deck_filter_cb.count())],
                         ["(alle)", "(ohne Heimat-Deck)",
                          *(d["name"] for d in ydb.list_decks(self.db))])
        view.add_piece(self.main_ids(1)[0])
        self.assertEqual(self.ui.titles(), ["Keine Kombo"])

    def test_new_combo_uses_deck_filter_as_home(self):
        view = self._view()
        deck = self.own_deck()
        view.deck_filter_cb.setCurrentIndex(view.deck_filter_cb.findData(deck))
        self.ui.texts.append(("Neue Linie", True))
        view._new_combo()
        combo = ydb.get_combo(self.db, view.combo_id)
        self.assertEqual((combo["name"], combo["deck_id"]), ("Neue Linie", deck))
        self.assertTrue(view.editor.isEnabled())
        self.assertEqual(view.name_edit.text(), "Neue Linie")
        self.assertEqual(view.home_cb.currentData(), deck)

    def test_pieces_roles_boss_and_coverage(self):
        view = self._view()
        combo = ydb.create_combo(self.db, "K")
        view.refresh()
        view._select_combo(combo)
        owned = self.scalar("SELECT card_id FROM collection LIMIT 1")
        boss = self.extra_ids(1)[0]
        view.add_piece(owned)
        view.add_piece(boss)
        self.assertEqual(view.pieces.count(), 2)
        self.assertEqual(view.coll_status.text(), "1 von 2 Bausteinen im Bestand.")
        self.assertTrue(self.texts(view.coll_missing)[0].startswith("1× fehlt"))
        self.assertTrue(self.texts(view.combo_list)[0].endswith("(1/2)"))

        self.select(view.pieces, owned)
        view.role_cb.setCurrentIndex(view.role_cb.findData("starter"))
        self.assertEqual({p["card_id"]: p["role"] for p in ydb.combo_cards(self.db, combo)}[owned],
                         "starter")
        self.assertTrue(view.pieces.currentItem().text().endswith("[Starter]"))
        self.select(view.pieces, boss)
        self.assertEqual(view.role_cb.currentData(), None)     # Dropdown folgt Auswahl

        view.boss_cb.setCurrentIndex(view.boss_cb.findData(boss))
        self.assertEqual(ydb.get_combo(self.db, combo)["boss_card_id"], boss)

        self.select(view.pieces, owned)
        view._adjust_piece(+1)
        self.assertEqual({p["card_id"]: p["quantity"] for p in ydb.combo_cards(self.db, combo)}[owned], 2)
        self.select(view.pieces, owned)
        view._adjust_piece(-2)                                   # auf 0 -> entfernt
        self.select(view.pieces, boss)
        view._remove_piece()
        self.assertEqual(ydb.combo_cards(self.db, combo), [])
        self.assertEqual(view.coll_status.text(), "Noch keine Bausteine festgelegt.")
        # Boss bleibt gespeichert und bleibt waehlbar, auch ohne Baustein.
        self.assertEqual(view.boss_cb.currentData(), boss)

    def test_home_deck_and_parent_fields(self):
        view = self._view()
        main = ydb.create_combo(self.db, "Haupt")
        other = ydb.create_combo(self.db, "Andere")
        view.refresh()
        view._select_combo(other)
        deck = self.own_deck()
        view.home_cb.setCurrentIndex(view.home_cb.findData(deck))
        self.assertEqual(ydb.get_combo(self.db, other)["deck_id"], deck)
        self.assertIn("· RDA-Mitsu", view.combo_list.currentItem().text())
        view.parent_cb.setCurrentIndex(view.parent_cb.findData(main))
        self.assertEqual(ydb.get_combo(self.db, other)["parent_combo_id"], main)
        self.assertEqual(self.texts(view.combo_list), ["Haupt", "    ↳ Andere"])
        self.assertEqual(view.combo_id, other)
        view._select_combo(main)
        self.assertFalse(view.parent_cb.isEnabled())          # hat eigene Variante

    def test_new_variant(self):
        view = self._view()
        deck = self.own_deck()
        main = ydb.create_combo(self.db, "Haupt", deck_id=deck)
        view.refresh()
        view._select_combo(main)
        self.ui.texts.append(("Gegen Nibiru", True))
        view._new_variant()
        child = view.combo_id
        got = ydb.get_combo(self.db, child)
        self.assertEqual((got["parent_combo_id"], got["deck_id"]), (main, deck))
        view._new_variant()                                    # auf einer Variante
        self.assertEqual(self.ui.titles(), ["Neue Variante"])

    def test_autosave_and_lint(self):
        view = self._view()
        combo = ydb.create_combo(self.db, "K")
        view.refresh()
        view._select_combo(combo)
        view.name_edit.setText("  Umbenannt ")
        view.arch_edit.setText("Albaz")
        view.notes_edit.setPlainText("Start: A\nEnd: B")
        view.steps_edit.setPlainText("NS A\n\nfalsch hier\n")
        self.assertFalse(view.lint_label.isHidden())
        self.assertIn("Schritt 2:", view.lint_label.text())     # Leerzeilen zaehlen nicht
        self.assertTrue(view._save_timer.isActive())
        view.flush_pending()
        got = ydb.get_combo(self.db, combo)
        self.assertEqual((got["name"], got["archetype"], got["notes"]),
                         ("Umbenannt", "Albaz", "Start: A\nEnd: B"))
        self.assertEqual([s["text"] for s in ydb.combo_steps(self.db, combo)],
                         ["NS A", "falsch hier"])
        view.steps_edit.setPlainText("NS A")
        self.assertTrue(view.lint_label.isHidden())
        view.name_edit.setText("")
        view.flush_pending()
        self.assertEqual(ydb.get_combo(self.db, combo)["name"], "Unbenannt")

    def test_switching_combo_flushes_pending_input(self):
        view = self._view()
        a = ydb.create_combo(self.db, "A")
        b = ydb.create_combo(self.db, "B")
        view.refresh()
        view._select_combo(a)
        view.steps_edit.setPlainText("NS X")
        view._select_combo(b)                       # ohne auf den Timer zu warten
        self.assertEqual([s["text"] for s in ydb.combo_steps(self.db, a)], ["NS X"])
        self.assertEqual(view.steps_edit.toPlainText(), "")

    def test_token_bar_inserts_notation(self):
        view = self._view()
        combo = ydb.create_combo(self.db, "K")
        view.refresh()
        view._select_combo(combo)
        view._insert_token("NS ")
        view._insert_token("[Req: ]", back=1)
        view.steps_edit.insertPlainText("x")
        self.assertEqual(view.steps_edit.toPlainText(), "NS [Req: x]")

    def test_play_steps_and_notation_help(self):
        view = self._view()
        combo = ydb.create_combo(self.db, "K")
        view.refresh()
        view._select_combo(combo)
        view._play_steps()
        self.assertEqual(self.ui.titles(), ["Durchspielen"])
        seen = []
        self.ui.dialogs["ComboPlaybackDialog"] = lambda d: seen.append(d.pos_label.text()) or 0
        self.ui.dialogs["QDialog"] = lambda d: seen.append(d.windowTitle()) or 0
        view.steps_edit.setPlainText("NS A\nSS B")
        view._play_steps()
        view._show_notation_help()
        self.assertEqual(seen, ["Schritt 1/2", "Kombo-Notation"])

    def test_delete_with_variants_warns(self):
        view = self._view()
        main = ydb.create_combo(self.db, "Haupt")
        var = ydb.create_combo(self.db, "Var")
        ydb.set_combo_parent(self.db, var, main)
        view.refresh()
        view._select_combo(main)
        self.ui.question = QMessageBox.StandardButton.No
        view._delete_combo()
        self.assertIn("1 Variante(n)", self.ui.last_text())
        self.assertIsNotNone(ydb.get_combo(self.db, main))
        self.ui.question = QMessageBox.StandardButton.Yes
        view._delete_combo()
        self.assertIsNone(ydb.get_combo(self.db, var))
        self.assertEqual(view.combo_list.count(), 0)
        self.assertFalse(view.editor.isEnabled())

    def test_search_and_deck_filter(self):
        deck = self.own_deck()
        ydb.create_combo(self.db, "Albaz-Linie", deck_id=deck)
        ydb.create_combo(self.db, "Anderes")
        view = self._view()
        view.search_edit.setText("albaz")
        view.refresh()                               # statt Debounce-Timer
        self.assertEqual([t.split()[0] for t in self.texts(view.combo_list)], ["Albaz-Linie"])
        view.search_edit.setText("")
        view.deck_filter_cb.setCurrentIndex(view.deck_filter_cb.findData(0))
        self.assertEqual(self.texts(view.combo_list), ["Anderes"])

    def test_focus_combo_resets_filter(self):
        deck = self.own_deck()
        hidden = ydb.create_combo(self.db, "Ohne Deck")
        view = self._view()
        view.deck_filter_cb.setCurrentIndex(view.deck_filter_cb.findData(deck))
        self.assertEqual(view.combo_list.count(), 0)
        view.focus_combo(hidden)
        self.assertEqual(view.deck_filter_cb.currentIndex(), 0)
        self.assertEqual(view.combo_id, hidden)


if __name__ == "__main__":
    unittest.main()
