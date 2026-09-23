"""
test_gui.py
===========
Offscreen-Tests fuer Deck- und Kombos-Tab: Auswahl ueberlebt Aktionen,
Listentexte bleiben nach dem Autosave korrekt. Wie die anderen Tests gegen
eine Temp-Kopie der Dev-DB, ohne Netz.

    python -m unittest test_gui
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import yugioh_db as ydb

DEV_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yugioh.sqlite3")
HAS_DEV_DB = os.path.exists(DEV_DB)

try:
    from PySide6.QtWidgets import QApplication

    HAS_QT = True
except ImportError:  # pragma: no cover
    HAS_QT = False


@unittest.skipUnless(HAS_DEV_DB and HAS_QT, "Dev-DB oder PySide6 fehlt")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.no_net = mock.patch.object(
            ydb, "cache_image", side_effect=OSError("offline")
        )
        cls.no_net.start()

    @classmethod
    def tearDownClass(cls):
        cls.no_net.stop()

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="ygo_gui_")
        self.db = os.path.join(self._dir, "test.sqlite3")
        shutil.copy(DEV_DB, self.db)
        ydb.ensure_schema(self.db)
        from yugioh_gui.repository import CardRepository

        self.repo = CardRepository(self.db)

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def _main_card(self, n: int = 1) -> list[int]:
        conn = sqlite3.connect(self.db)
        try:
            return [r[0] for r in conn.execute(
                "SELECT id FROM cards WHERE frame_type = 'effect' LIMIT ?", (n,)
            )]
        finally:
            conn.close()

    def test_deck_selection_survives_quantity_change(self):
        from yugioh_gui.deck import DeckView

        cid = self._main_card()[0]
        deck = ydb.create_deck(self.db, "AAA Test")
        ydb.add_card_to_deck(self.db, deck, cid, count=1)
        view = DeckView(self.repo)
        view.deck_cb.setCurrentIndex(view.deck_cb.findData(deck))
        panel = view.panels["main"]
        row = next(i for i in range(panel.list.count())
                   if panel.list.item(i).data(0x0100) == cid)
        panel.list.setCurrentRow(row)
        view.adjust("main", +1)
        self.assertEqual(panel.selected_card_id(), cid)
        view.adjust("main", +1)          # zweiter Klick wirkt ohne Neu-Anwahl
        self.assertEqual(ydb.deck_counts(self.db, deck)["main"], 3)

    def test_export_path_appends_extension_despite_dot(self):
        from yugioh_gui.exporting import resolve_export_path
        exts = {"Text": ".txt", "PDF": ".pdf"}
        self.assertEqual(
            resolve_export_path("C:/x/RDA v1.2", "PDF-Datei (*.pdf)", exts, ".txt"),
            ("C:/x/RDA v1.2.pdf", ".pdf"),
        )
        self.assertEqual(
            resolve_export_path("C:/x/a.PDF", "Text (*.txt)", exts, ".txt"),
            ("C:/x/a.PDF", ".pdf"),
        )

    def test_collection_header_and_summary_follow_quantity_edit(self):
        from PySide6.QtCore import Qt
        from yugioh_gui.collection import CollectionView

        view = CollectionView(self.repo)
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
        from yugioh_gui.combos import ComboView

        main = ydb.create_combo(self.db, "Haupt")
        var = ydb.create_combo(self.db, "Var")
        ydb.set_combo_parent(self.db, var, main)
        view = ComboView(self.repo)
        view.refresh()
        self.assertTrue(view._select_combo(var))
        view.name_edit.setText("Var2")
        view.flush_pending()
        texts = [view.combo_list.item(i).text()
                 for i in range(view.combo_list.count())]
        self.assertIn("    ↳ Var2", texts)

    def test_combo_list_shows_coverage_and_variants(self):
        from yugioh_gui.combos import ComboView

        main = ydb.create_combo(self.db, "Haupt")
        ydb.add_combo_card(self.db, main, self._main_card()[0], 1)
        var = ydb.create_combo(self.db, "Var")
        ydb.set_combo_parent(self.db, var, main)
        view = ComboView(self.repo)
        view.refresh()
        texts = [view.combo_list.item(i).text()
                 for i in range(view.combo_list.count())]
        cov = ydb.combo_coverage_collection(self.db, main)
        expected = view._combo_label(ydb.get_combo(self.db, main), cov)
        self.assertEqual(texts, [expected, "    ↳ Var"])


if __name__ == "__main__":
    unittest.main()
