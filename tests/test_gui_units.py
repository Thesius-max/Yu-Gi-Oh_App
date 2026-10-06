"""
GUI-Bausteine (offscreen): CardRepository, Beschriftungs-Tabellen,
Format-/Export-Helfer, Dialoge ohne echte Modalitaet, Bild-Helfer,
Hintergrund-Tasks, Theme und Handbuch. Gegen eine Temp-Kopie der Dev-DB.
"""

from __future__ import annotations

import datetime
import os
import unittest
from unittest import mock

import yugioh_db as ydb

from tests._support import HAS_QT, QtTestCase, pump

if HAS_QT:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QListWidget

    from yugioh_gui import carddetail, deck_dialogs, exporting, images, labels, tasks
    from yugioh_gui.combos import ComboPlaybackDialog
    from yugioh_gui.mainwindow import _BusyDialog
    from yugioh_gui.repository import CardRepository


class RepositoryTests(QtTestCase):
    def setUp(self):
        super().setUp()
        self.repo = CardRepository(self.db)

    def test_exists(self):
        self.assertTrue(self.repo.exists())
        self.assertFalse(CardRepository(os.path.join(self._dir, "fehlt")).exists())

    def test_distinct_whitelist(self):
        with self.assertRaises(ValueError):
            self.repo.distinct("name")
        attrs = self.repo.distinct("attribute")
        self.assertEqual(attrs, sorted(attrs))
        self.assertIn("DARK", attrs)
        self.assertNotIn("", attrs)

    def test_text_search_german_and_english(self):
        cid = self.translated_card()
        card = self.repo.get_card(cid)
        for text in (card["name"], card["name_de"]):
            ids = [r["id"] for r in self.repo.query(text=text)]
            self.assertIn(cid, ids, text)

    def test_prefix_search(self):
        cid = self.translated_card()
        word = self.repo.get_card(cid)["name_de"].split()[0][:4]
        self.assertIn(cid, [r["id"] for r in self.repo.query(text=word, limit=5000)])

    def test_fts_syntax_in_input_is_harmless(self):
        for text in ('"', 'a"b', "*", "NEAR(", "AND OR", "-x", "^", "(", "'"):
            self.assertIsInstance(self.repo.query(text=text), list, text)

    def test_kind_filter(self):
        for kind, check in (("spell", lambda r: r["type"] == "Spell Card"),
                            ("trap", lambda r: r["type"] == "Trap Card"),
                            ("monster", lambda r: "Monster" in r["type"])):
            rows = self.repo.query(kind=kind, limit=5000)
            self.assertTrue(rows and all(check(r) for r in rows), kind)

    def test_value_ranges(self):
        rows = self.repo.query(kind="monster", attributes=["LIGHT"], level_min=4,
                               level_max=4, atk_min=1000, atk_max=1800)
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual((r["attribute"], r["level"]), ("LIGHT", 4))
            self.assertTrue(1000 <= r["atk"] <= 1800)
            self.assertNotEqual(r["frame_type"], "link")
        arch = self.repo.distinct("archetype")[0]
        self.assertTrue(all(r["archetype"] == arch for r in self.repo.query(archetype=arch)))
        rows = self.repo.query(race="Dragon", def_min=2000, def_max=2500, limit=100)
        self.assertTrue(rows and all(r["race"] == "Dragon" and 2000 <= r["def"] <= 2500
                                     for r in rows))
        links = self.repo.query(link_min=2, link_max=3, limit=1000)
        self.assertTrue(links and all(r["link_value"] in (2, 3) for r in links))
        scales = self.repo.query(scale_min=8, scale_max=13, limit=1000)
        self.assertTrue(scales and all(r["scale"] >= 8 for r in scales))
        # ATK 0 ist ein Wert, kein 'egal' (Ash Blossom hat ATK 0).
        zero = {r["id"] for r in self.repo.query(atk_min=0, atk_max=0, limit=5000)}
        self.assertIn(14558127, zero)
        self.assertEqual(len(zero), self.scalar("SELECT COUNT(*) FROM cards WHERE atk = 0"))

    def test_groups_or_inside_and_between(self):
        rows = self.repo.query(frames=["synchro", "xyz"], attributes=["DARK", "LIGHT"],
                               limit=5000)
        frames = {r["frame_type"].replace("_pendulum", "") for r in rows}
        self.assertEqual(frames, {"synchro", "xyz"})
        self.assertEqual({r["attribute"] for r in rows}, {"DARK", "LIGHT"})
        pend = self.repo.query(frames=["pendulum"], limit=5000)
        self.assertTrue(pend and all(r["frame_type"].endswith("_pendulum") for r in pend))
        # 'Effekt' ist der orange Rahmen -- keine Synchro-/Xyz-Monster mit Effekt.
        effect = self.repo.query(frames=["effect"], limit=5000)
        self.assertEqual({r["frame_type"] for r in effect}, {"effect", "effect_pendulum"})
        quick = self.repo.query(kind="spell", st_kinds=["Quick-Play", "Field"], limit=5000)
        self.assertEqual({r["race"] for r in quick}, {"Quick-Play", "Field"})
        counter = self.repo.query(kind="trap", st_kinds=["Counter"], limit=5000)
        self.assertTrue(counter and all(r["race"] == "Counter" for r in counter))

    def test_traits(self):
        from yugioh_gui.repository import TRAIT_SQL
        for key in TRAIT_SQL:
            self.assertTrue(self.repo.query(traits=[key], limit=5), key)
        tuners = self.repo.query(traits=["tuner"], limit=5000)
        self.assertIn(14558127, {r["id"] for r in tuners})         # Ash Blossom
        self.assertTrue(all("Tuner" in r["type"] for r in tuners))

    def test_passcode_and_set_code_search(self):
        from yugioh_gui.repository import is_set_code
        self.assertEqual(self.repo.query(text="14558127")[0]["id"], 14558127)
        # Set-Nummer: englischer Code der Kartendaten, deutscher Druck und
        # Kleinschreibung finden dieselbe Karte.
        code = self.scalar("SELECT set_code FROM card_sets WHERE card_id = 14558127 "
                           "AND set_code LIKE '%-EN%' LIMIT 1")
        de = ydb.localize_set_code(code, "DE")
        for text in (code, de, de.lower()):
            self.assertIn(14558127, [r["id"] for r in self.repo.query(text=text)], text)
        self.assertTrue(is_set_code("LOB-001") and is_set_code("RA01-DE008"))
        self.assertFalse(is_set_code("Blue-Eyes") or is_set_code("Ash"))
        # Eigener Druck mit freiem Code findet die Karte ebenfalls.
        ydb.add_to_collection(self.db, 46986414, 1, set_code="XYZ9-DE999")
        self.assertEqual([r["id"] for r in self.repo.query(text="xyz9-de999")], [46986414])

    def test_owned_column_and_helpers(self):
        ydb.add_to_collection(self.db, 14558127, 2, set_code="RA01-DE008")
        owned = self.repo.owned_count(14558127)
        row = self.repo.query(text="14558127")[0]
        self.assertEqual(row["owned"], owned)
        self.assertEqual(self.repo.set_count(14558127), self.scalar(
            "SELECT COUNT(DISTINCT set_code) FROM card_sets WHERE card_id = 14558127"))
        races = self.repo.monster_races()
        self.assertIn("Dragon", races)
        self.assertNotIn("Quick-Play", races)
        self.assertNotIn("Counter", races)

    def test_wording_filter_needs_precomputed_flags(self):
        self.assertEqual(self.repo.query(wording="handtrap"), [])  # noch nicht berechnet
        self.assertGreater(ydb.ensure_wording_flags(self.db), 10_000)
        self.assertEqual(ydb.ensure_wording_flags(self.db), 0)     # aktuell
        handtraps = {r["id"] for r in self.repo.query(wording="handtrap", limit=5000)}
        self.assertTrue({14558127, 10045474} <= handtraps)         # Ash, Imperm
        self.assertNotIn(25784595, handtraps)                      # Bone Archfiend
        quick = {r["id"] for r in self.repo.query(wording="quick", limit=5000)}
        self.assertIn(14558127, quick)
        # 'quick' darf nicht in 'quick_x' o. ae. hineintreffen: exakte Merkmale.
        self.assertTrue(all(",quick," in r["wording_flags"]
                            for r in self.repo.query(wording="quick", limit=50)))

    def test_only_collection(self):
        owned = {r[0] for r in self.query("SELECT card_id FROM collection")}
        rows = self.repo.query(only_collection=True, limit=5000)
        self.assertEqual({r["id"] for r in rows}, owned)

    def test_browse_order_and_limit(self):
        rows = self.repo.query()
        self.assertEqual(len(rows), 300)
        names = [r["name_de"] or r["name"] for r in rows]
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(self.repo.query(limit=7)), 7)

    def test_owned_count_and_get_card(self):
        cid = self.scalar("SELECT card_id FROM collection LIMIT 1")
        self.assertEqual(self.repo.owned_count(cid), self.scalar(
            "SELECT SUM(quantity) FROM collection WHERE card_id = ?", (cid,)))
        self.assertEqual(self.repo.owned_count(self.unowned_ids(1)[0]), 0)
        self.assertIsNone(self.repo.get_card(999_999_999))
        self.assertEqual(self.repo.get_card(cid)["id"], cid)


class LabelTests(QtTestCase):
    def test_pct(self):
        self.assertEqual(labels.pct(0.842), "84,2 %")
        self.assertEqual(labels.pct(1), "100,0 %")
        self.assertEqual(labels.pct(0), "0,0 %")

    def test_import_report_lines(self):
        base = {"imported": {"main": 40, "extra": 15, "side": 0},
                "unknown": [], "capped": [], "moved": [], "unreadable": []}
        self.assertEqual(labels.import_report_lines(base),
                         ["Importiert: Main 40, Extra 15, Side 0."])
        full = {**base, "unknown": [1, 2], "capped": ["A"], "moved": ["B", "C"],
                "unreadable": [f"z{i}" for i in range(7)]}
        lines = labels.import_report_lines(full)
        self.assertEqual(lines[1], "Nicht in der Datenbank (übersprungen): 1, 2")
        self.assertEqual(lines[2], "Über der 3-Kopien-Grenze gekürzt: A")
        self.assertEqual(lines[3], "In die passende Zone verschoben: B, C")
        self.assertEqual(lines[4], "Nicht lesbare Zeilen (übersprungen): "
                                   "z0, z1, z2, z3, z4 … und 2 weitere")

    def test_tables_cover_real_card_data(self):
        for column, table, extra in (
            ("type", labels.TYPE_DE, ""),
            ("attribute", labels.ATTR_DE, ""),
            # Skill-Karten tragen Figurennamen als 'race' -- bewusst ohne Tabelle.
            ("race", labels.RACE_DE, "AND frame_type != 'skill' AND race != ''"),
        ):
            values = {r[0] for r in self.query(
                f"SELECT DISTINCT {column} FROM cards WHERE {column} IS NOT NULL {extra}")}
            self.assertEqual(values - set(table), set(), column)

    def test_category_and_role_tables(self):
        self.assertEqual(set(labels.CATEGORY_DE), set(labels.CATEGORY_ORDER))
        self.assertEqual(set(labels.ROLE_DE), set(ydb.COMBO_ROLES))
        self.assertEqual(set(labels.ZONE_LABELS), {"main", "extra", "side"})


class FormatHelperTests(QtTestCase):
    def _card(self, where):
        return CardRepository(self.db).get_card(self.pick_ids(where)[0])

    def _get(self, cid):
        return CardRepository(self.db).get_card(cid)

    def test_stats_link_monster_without_def(self):
        self.set_real_link_markers()
        cid = next(iter(self.REAL_LINK_MARKERS))           # S:P Little Knight
        card = self._get(cid)
        text = carddetail._format_card_stats(card, 3)
        self.assertIn(f"ATK {card['atk']} / LINK-{card['link_value']}   → ←", text)  # im Uhrzeigersinn
        self.assertEqual(text.count("LINK-"), 1)            # nicht auch oben
        self.assertNotIn("DEF", text)
        self.assertNotIn("None", text)
        self.assertNotIn("★", text)
        self.assertIn(f"Kartennummer {cid:08d}  ·  in 3 Sets", text)

    def test_stats_follow_the_card(self):
        self.set_real_typelines()
        self.assertEqual(carddetail._format_card_stats(self._get(14558127), 17).split("\n"), [
            "FEUER  ·  ★ Stufe 3", "[Zombie/Empfänger/Effekt]", "ATK 0 / DEF 1800",
            "Kartennummer 14558127  ·  in 17 Sets"])
        utopia = carddetail._format_card_stats(self._get(84013237))
        self.assertTrue(utopia.startswith("LICHT  ·  Rang 4\n[Krieger/Xyz/Effekt]\n"))
        odd = carddetail._format_card_stats(self._get(16178681))
        self.assertIn("[Drache/Pendel/Effekt]", odd)
        self.assertIn("\nPendelskala 4\n", odd)

    def test_stats_spell_and_trap(self):
        text = carddetail._format_card_stats(self._get(12580477))          # Raigeki
        self.assertTrue(text.startswith("Zauberkarte · Normal\nKartennummer 12580477"))
        self.assertNotIn("ATK", text)
        trap = carddetail._format_card_stats(self._get(44095762))          # Mirror Force
        self.assertTrue(trap.startswith("Fallenkarte · Normal"))

    def test_typeline_real_and_fallback(self):
        # Ohne API-Typzeile (Dev-DB): aus 'type' hergeleitet, 'Effekt' nur
        # wenn sicher -- Stardust (Synchro) bleibt ohne.
        self.assertEqual(labels.typeline_parts(self._get(44508094)), ["Drache", "Synchro"])
        self.assertEqual(labels.typeline_parts(self._get(14558127)),
                         ["Zombie", "Empfänger", "Effekt"])
        self.assertEqual(labels.typeline_parts(self._get(46986414)), ["Hexer", "Normal"])
        self.assertEqual(labels.typeline_parts(self._get(64631466)),
                         ["Hexer", "Ritual", "Effekt"])
        self.assertEqual(labels.typeline_parts(self._get(12580477)), [])
        self.set_real_typelines()
        expected = {
            14558127: "[Zombie/Empfänger/Effekt]", 46986414: "[Hexer/Normal]",
            44508094: "[Drache/Synchro/Effekt]", 84013237: "[Krieger/Xyz/Effekt]",
            16178681: "[Drache/Pendel/Effekt]", 64631466: "[Hexer/Ritual/Effekt]",
            68934651: "[Cyberse/Link/Effekt]",
        }
        for cid, text in expected.items():
            self.assertEqual(labels.typeline_text(self._get(cid)), text, cid)

    def test_level_and_kind_texts(self):
        cases = {
            14558127: ("★3", "★ Stufe 3", "FEUER · Effekt"),
            84013237: ("R4", "Rang 4", "LICHT · Xyz"),
            68934651: ("L5", "LINK-5", "FINSTERNIS · Link"),
            16178681: ("★7", "★ Stufe 7", "FINSTERNIS · Effekt-Pendel"),
            12580477: ("", "", "Zauber · Normal"),
            44095762: ("", "", "Falle · Normal"),
        }
        for cid, (short, full, kind) in cases.items():
            card = self._get(cid)
            self.assertEqual((labels.level_text(card, short=True), labels.level_text(card),
                              labels.card_kind_text(card)), (short, full, kind), cid)

    def test_symbol_tables_cover_real_data(self):
        for ctype, table in (("Spell Card", labels.SPELL_KIND_DE),
                             ("Trap Card", labels.TRAP_KIND_DE)):
            values = {r[0] for r in self.query(
                "SELECT DISTINCT race FROM cards WHERE type = ? AND race != ''", (ctype,))}
            self.assertEqual(values, set(table), ctype)

    def test_corpus_diff_text(self):
        mine = self.own_deck()
        ref = self.scalar("SELECT deck_id FROM decks WHERE kind = 'reference' LIMIT 1")
        d = ydb.deck_corpus_diff(self.db, mine, ref)
        text = deck_dialogs._format_corpus_diff(d)
        self.assertTrue(text.startswith(f"Referenz: {d['ref_name']}\n"))
        self.assertIn(f"Übereinstimmung: {d['shared_cards']}/{d['ref_cards']}", text)
        same = deck_dialogs._format_corpus_diff(ydb.deck_corpus_diff(self.db, mine, mine))
        self.assertIn("Fehlt dir: nichts", same)
        self.assertIn("Du hast extra: nichts.", same)


class ExportHelperTests(QtTestCase):
    def test_export_path_appends_extension_despite_dot(self):
        # Aus test_gui.py uebernommen.
        exts = {"Text": ".txt", "PDF": ".pdf"}
        self.assertEqual(
            exporting.resolve_export_path("C:/x/RDA v1.2", "PDF-Datei (*.pdf)", exts, ".txt"),
            ("C:/x/RDA v1.2.pdf", ".pdf"),
        )
        self.assertEqual(
            exporting.resolve_export_path("C:/x/a.PDF", "Text (*.txt)", exts, ".txt"),
            ("C:/x/a.PDF", ".pdf"),
        )

    def test_export_path_default_extension(self):
        self.assertEqual(
            exporting.resolve_export_path("deck", "", {"PDF": ".pdf"}, ".ydk"),
            ("deck.ydk", ".ydk"),
        )

    def test_write_text_file_is_utf8_with_unix_newlines(self):
        path = os.path.join(self._dir, "a.txt")
        exporting.write_text_file(path, "Zeile 1\nÄrger ✓\n")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), "Zeile 1\nÄrger ✓\n".encode("utf-8"))

    def test_write_csv_file_has_bom_and_keeps_crlf(self):
        path = os.path.join(self._dir, "a.csv")
        exporting.write_csv_file(path, "Name;Text\r\nÄrger;\"a\r\nb\"\r\n")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), "\ufeffName;Text\r\nÄrger;\"a\r\nb\"\r\n"
                             .encode("utf-8"))

    def test_write_text_pdf(self):
        path = os.path.join(self._dir, "a.pdf")
        exporting.write_text_pdf(path, ydb.export_deck_text(self.db, self.own_deck()))
        with open(path, "rb") as fh:
            data = fh.read()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertGreater(len(data), 1000)


class DialogTests(QtTestCase):
    def test_playback_navigation(self):
        dlg = self.track(ComboPlaybackDialog(["NS A", "SS B", "Link: A + B -> C"], "K"))
        self.assertEqual(dlg.windowTitle(), "Durchspielen — K")
        self.assertEqual(dlg.pos_label.text(), "Schritt 1/3")
        self.assertFalse(dlg.prev_btn.isEnabled())
        for _ in range(5):
            dlg._next()                                  # stoppt am Ende
        self.assertEqual(dlg.pos_label.text(), "Schritt 3/3")
        self.assertFalse(dlg.next_btn.isEnabled())
        self.assertTrue(dlg.list.item(2).font().bold())
        self.assertFalse(dlg.list.item(0).font().bold())
        QTest.keyClick(dlg, Qt.Key.Key_Left)
        self.assertEqual(dlg.pos_label.text(), "Schritt 2/3")
        self.assertEqual(dlg.list.item(1).foreground().color(), QColor("#d4af37"))
        QTest.keyClick(dlg, Qt.Key.Key_Right)
        self.assertEqual(dlg.pos_label.text(), "Schritt 3/3")

    def test_playback_single_step(self):
        dlg = self.track(ComboPlaybackDialog(["NS A"], "K"))
        self.assertFalse(dlg.prev_btn.isEnabled() or dlg.next_btn.isEnabled())

    def test_busy_dialog_cannot_be_dismissed_while_locked(self):
        dlg = self.track(_BusyDialog("Lade …"))
        dlg.show()
        dlg.reject()
        dlg.close()
        QTest.keyClick(dlg, Qt.Key.Key_Escape)
        self.assertTrue(dlg.isVisible())
        dlg.unlock_and_close()
        self.assertFalse(dlg.isVisible())

    def test_card_search_dialog(self):
        repo = CardRepository(self.db)
        dlg = self.track(carddetail.CardSearchDialog(repo))
        self.assertIsNone(dlg.chosen_card_id())
        dlg.search_box.setText("   ")
        self.assertEqual(dlg.list.count(), 0)
        cid = self.translated_card()
        dlg.search_box.setText(repo.get_card(cid)["name_de"])
        self.assertGreater(dlg.list.count(), 0)
        self.assertEqual(dlg.list.currentRow(), 0)
        ids = [dlg.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dlg.list.count())]
        self.assertIn(cid, ids)
        self.assertEqual(dlg.chosen_card_id(), ids[0])

    def test_combo_from_deck_dialog(self):
        deck = self.own_deck()
        dlg = self.track(deck_dialogs.ComboFromDeckDialog(CardRepository(self.db), deck))
        n_cards = len(ydb.deck_cards(self.db, deck, "main")) + len(ydb.deck_cards(self.db, deck, "extra"))
        self.assertEqual(dlg.list.count(), n_cards + 2)          # + 2 Kopfzeilen
        self.assertEqual(dlg.list.item(0).flags(), Qt.ItemFlag.NoItemFlags)
        for row in (1, 2):
            dlg.list.item(row).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(dlg.selected_card_ids(),
                         [dlg.list.item(r).data(Qt.ItemDataRole.UserRole) for r in (1, 2)])
        dlg._accept()                                           # ohne Namen
        self.assertEqual(self.ui.titles("information"), ["Kombo"])
        self.assertNotEqual(dlg.result(), QDialog.DialogCode.Accepted)
        dlg.name_edit.setText("  Linie  ")
        dlg._accept()
        self.assertEqual(dlg.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dlg.combo_name(), "Linie")

    def test_reference_meta_dialog_validation(self):
        dlg = self.track(deck_dialogs._ReferenceMetaDialog("Liste"))
        self.assertEqual(dlg.date_edit.text(), datetime.date.today().isoformat())
        dlg.date_edit.setText("31.12.2026")
        dlg._accept_checked()
        self.assertEqual(self.ui.titles("warning"), ["Ungültiges Datum"])
        dlg.name_edit.setText(" ")
        dlg._accept_checked()
        self.assertEqual(self.ui.titles("warning")[-1], "Eingabe fehlt")
        self.assertNotEqual(dlg.result(), QDialog.DialogCode.Accepted)
        dlg.name_edit.setText("Liste")
        dlg.date_edit.setText("")
        dlg.source_edit.setText(" YCS ")
        dlg._accept_checked()
        self.assertEqual(dlg.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dlg.values(), ("Liste", "YCS", None))

    def test_reference_deck_dialog_import_and_delete(self):
        repo = CardRepository(self.db)
        path = os.path.join(self._dir, "Meta Liste.ydk")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(ydb.export_deck_ydk(self.db, self.own_deck()))
        dlg = self.track(deck_dialogs.ReferenceDeckDialog(repo))
        n_refs = self.n_refs()
        self.assertEqual(dlg.listing.count(), n_refs)
        n_cards = sum(ydb.deck_counts(self.db, self.own_deck()).values())

        def fill_meta(meta):
            self.assertEqual(meta.name_edit.text(), "Meta Liste")   # aus Dateiname
            meta.source_edit.setText("Test-Turnier")
            meta.date_edit.setText("2099-01-01")
            meta._accept_checked()
            return meta.result()

        self.ui.open_paths.append((path, ""))
        self.ui.dialogs["_ReferenceMetaDialog"] = fill_meta
        with self.assert_no_resource_warnings():         # Datei wird geschlossen
            dlg._import()
        self.assertEqual(dlg.listing.count(), n_refs + 1)
        self.assertEqual(dlg.listing.item(0).text(),         # neueste zuerst
                         f"Meta Liste — 2099-01-01 — Test-Turnier  ({n_cards} Karten)")
        dlg.listing.setCurrentRow(0)
        dlg._delete()
        self.assertEqual(self.ui.titles("question"), ["Referenz-Deck löschen"])
        self.assertEqual(dlg.listing.count(), n_refs)

    def test_reference_deck_dialog_empty_corpus(self):
        for r in ydb.list_reference_decks(self.db):
            ydb.delete_deck(self.db, r["deck_id"])
        dlg = self.track(deck_dialogs.ReferenceDeckDialog(CardRepository(self.db)))
        self.assertEqual(dlg.listing.count(), 1)
        self.assertEqual(dlg.listing.item(0).flags(), Qt.ItemFlag.NoItemFlags)
        dlg.listing.setCurrentRow(0)
        dlg._delete()                                    # Hinweiszeile: nichts tun
        self.assertEqual(self.ui.messages, [])

    def test_corpus_diff_dialog(self):
        dlg = self.track(deck_dialogs.DeckCorpusDiffDialog(CardRepository(self.db), self.own_deck()))
        self.assertEqual(dlg.ref_cb.count(), self.n_refs())
        first = dlg.report.toPlainText()
        self.assertTrue(first.startswith("Referenz: "))
        dlg.ref_cb.setCurrentIndex(3)
        self.assertNotEqual(dlg.report.toPlainText().splitlines()[0], first.splitlines()[0])

    def test_card_detail_dialog(self):
        repo = CardRepository(self.db)
        dlg = self.track(carddetail.CardDetailDialog(repo))
        cid = self.translated_card()
        dlg.load(cid)
        self.assertEqual(dlg.name.text(), self.display_name(cid))
        self.assertEqual(dlg.windowTitle(), self.display_name(cid))
        self.assertTrue(dlg.text.toPlainText())
        dlg.load(999_999_999)                             # unbekannt: bleibt
        self.assertEqual(dlg.current_id, cid)

    def test_card_detail_dialog_translation_signal(self):
        repo = CardRepository(self.db)
        dlg = self.track(carddetail.CardDetailDialog(repo))
        cid = self.untranslated_card()
        dlg.load(cid)
        fired = []
        dlg.translation_changed.connect(lambda: fired.append(True))

        def translate(d):
            d.findChild(QLineEdit).setText("Neuer Name")
            return QDialog.DialogCode.Accepted
        self.ui.dialogs["QDialog"] = translate
        dlg._edit_translation()
        self.assertEqual(fired, [True])
        self.assertEqual(dlg.name.text(), "Neuer Name")

    def test_edit_card_translation(self):
        repo = CardRepository(self.db)
        cid = self.translated_card()
        seen = {}

        def inspect_and_cancel(d):
            edit = d.findChild(QLineEdit)
            seen["text"], seen["placeholder"] = edit.text(), edit.placeholderText()
            return QDialog.DialogCode.Rejected
        self.ui.dialogs["QDialog"] = inspect_and_cancel
        self.assertFalse(carddetail.edit_card_translation(repo, cid, None))
        # Nur eigene Overrides werden vorbelegt; der API-Wert steht grau dahinter.
        self.assertEqual(seen, {"text": "", "placeholder": self.display_name(cid)})
        self.assertIsNone(ydb.get_card_translation(self.db, cid))

        def save(d):
            d.findChild(QLineEdit).setText("Mein Name")
            return QDialog.DialogCode.Accepted
        self.ui.dialogs["QDialog"] = save
        self.assertTrue(carddetail.edit_card_translation(repo, cid, None))
        self.assertEqual(ydb.get_card_translation(self.db, cid)["name_de"], "Mein Name")
        self.assertFalse(carddetail.edit_card_translation(repo, 999_999_999, None))


class DetailPanelTests(QtTestCase):
    def test_disabled_until_card_shown(self):
        panel = self.track(carddetail.DetailPanel(CardRepository(self.db)))
        self.assertFalse(panel.add_btn.isEnabled())
        panel._add_to_collection()                        # ohne Karte: nichts
        cid = self.unowned_ids(1)[0]
        panel.show_card(CardRepository(self.db).get_card(cid))
        self.assertTrue(panel.add_btn.isEnabled())
        self.assertEqual(panel.coll_box.title(), "Sammlung — im Bestand: 0")

    def test_add_to_collection_and_bound_warning(self):
        repo = CardRepository(self.db)
        panel = self.track(carddetail.DetailPanel(repo))
        cid = self.unowned_ids(1)[0]
        deck = ydb.create_deck(self.db, "T")
        ydb.add_card_to_deck(self.db, deck, cid, count=3)
        panel.show_card(repo.get_card(cid))
        self.assertEqual(panel.coll_box.title(), "Sammlung — im Bestand: 0  ·  in Decks: 3  ⚠")
        panel.qty.setValue(3)
        QTest.mouseClick(panel.add_btn, Qt.MouseButton.LeftButton)
        self.assertEqual(panel.coll_box.title(), "Sammlung — im Bestand: 3  ·  in Decks: 3")
        self.assertEqual(repo.owned_count(cid), 3)

    def test_offline_image_placeholder(self):
        repo = CardRepository(self.db)
        panel = self.track(carddetail.DetailPanel(repo))
        cid = self.unowned_ids(1)[0]
        panel.show_card(repo.get_card(cid))
        self.assertEqual(panel.image.text(), "Lade …")
        pump()                          # ImageLoader scheitert (kein Netz)
        self.assertEqual(panel.image.text(), "(Bild offline nicht verfügbar)")


class ImageTests(QtTestCase):
    def _write_image(self, card_id, w=300, h=438):
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor("#336699"))
        path = os.path.join(ydb.IMAGE_DIR, f"{card_id}.jpg")
        self.assertTrue(img.save(path, "JPG"))
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_lookup_reads_local_file_and_caches(self):
        cid = self.main_ids(1)[0]
        self.assertIsNone(images.lookup_card_pixmap(cid, 100, 146, "t"))
        path = self._write_image(cid)
        pix = images.lookup_card_pixmap(cid, 100, 146, "t")
        self.assertIsNotNone(pix)
        self.assertLessEqual(pix.width(), 100)
        self.assertLessEqual(pix.height(), 146)
        os.remove(path)                                   # jetzt aus dem Cache
        self.assertIsNotNone(images.lookup_card_pixmap(cid, 100, 146, "t"))
        self.assertIsNone(images.lookup_card_pixmap(cid, 100, 146, "anderer-prefix"))

    def test_scale_keeps_aspect_ratio(self):
        from PySide6.QtGui import QPixmap
        pix = images.scale_pixmap(QPixmap(300, 600), 100, 100)
        self.assertEqual((pix.width(), pix.height()), (50, 100))

    def test_generated_pixmaps(self):
        back = images.card_back_pixmap(60, 88)
        self.assertEqual((back.width(), back.height()), (60, 88))
        self.assertEqual(images.card_back_pixmap(60, 88).cacheKey(), back.cacheKey())
        ph = images.placeholder_pixmap("Sehr langer Kartenname " * 5, 60, 88)
        self.assertEqual((ph.width(), ph.height()), (60, 88))

    def test_pos_over_item_text(self):
        view = self.track(QListWidget())
        view.addItem("Kurz")
        view.resize(400, 100)
        view.show()
        item = view.item(0)
        rect = view.visualItemRect(item)
        self.assertTrue(images.pos_over_item_text(view, item, QPoint(rect.left() + 3, rect.center().y())))
        self.assertFalse(images.pos_over_item_text(view, item, QPoint(rect.right() - 2, rect.center().y())))

    def test_detail_panel_uses_local_image(self):
        repo = CardRepository(self.db)
        cid = self.main_ids(1)[0]
        self._write_image(cid)
        panel = self.track(carddetail.DetailPanel(repo))
        panel.show_card(repo.get_card(cid))
        self.assertFalse(panel.image.pixmap().isNull())

    def _download_to(self, cid):
        """Bild ausserhalb von IMAGE_DIR ablegen und cache_image darauf
        zeigen lassen -- simuliert einen erfolgreichen Download."""
        path = os.path.join(self._dir, f"dl-{cid}.jpg")
        img = QImage(120, 175, QImage.Format.Format_RGB32)
        img.fill(QColor("#aa3355"))
        img.save(path, "JPG")
        return mock.patch.object(ydb, "cache_image", return_value=path)

    def test_detail_panel_background_download(self):
        repo = CardRepository(self.db)
        cid = self.main_ids(1)[0]
        panel = self.track(carddetail.DetailPanel(repo))
        with self._download_to(cid):
            panel.show_card(repo.get_card(cid))
            self.assertEqual(panel.image.text(), "Lade …")
            pump()
        self.assertFalse(panel.image.pixmap().isNull())

    # -- HoverCardPreview ---------------------------------------------------

    def _hover_setup(self, resolver):
        view = self.track(QListWidget())
        view.addItem("Karte")
        view.resize(300, 200)
        view.show()
        preview = images.HoverCardPreview(view, resolver)
        return view, preview

    @staticmethod
    def _move(view, x=10, y=10):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(x, y), QPointF(x + 50, y + 50),
                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(), event)

    def test_hover_preview_local_image_and_hide(self):
        from PySide6.QtCore import QEvent
        cid = self.main_ids(1)[0]
        self._write_image(cid)
        view, preview = self._hover_setup(lambda pos: cid)
        self._move(view)
        self.assertTrue(preview._popup.isVisible())
        self.assertFalse(preview._popup.pixmap().isNull())
        self._move(view, 12, 12)                       # gleiche Karte: nur nachfuehren
        self.assertTrue(preview._popup.isVisible())
        QApplication.sendEvent(view.viewport(), QEvent(QEvent.Type.Leave))
        self.assertFalse(preview._popup.isVisible())

    def test_hover_preview_without_card(self):
        view, preview = self._hover_setup(lambda pos: None)
        self._move(view)
        self.assertFalse(preview._popup.isVisible())

    def test_hover_preview_downloads_or_reports_offline(self):
        cid, offline = self.main_ids(2)
        target = {"cid": cid}
        view, preview = self._hover_setup(lambda pos: target["cid"])
        with self._download_to(cid):
            self._move(view)
            self.assertEqual(preview._popup.text(), "Lade …")
            pump()
        self.assertFalse(preview._popup.pixmap().isNull())
        target["cid"] = offline                        # cache_image wirft (kein Netz)
        self._move(view)
        pump()
        self.assertEqual(preview._popup.text(), "(Bild offline nicht verfügbar)")


class TaskTests(QtTestCase):
    def test_db_task_done_and_failed(self):
        got = []
        sig = tasks.DbTaskSignals()
        sig.done.connect(lambda r: got.append(("done", r)))
        sig.failed.connect(lambda m: got.append(("failed", m)))
        from PySide6.QtCore import QThreadPool
        deck = self.own_deck()
        QThreadPool.globalInstance().start(tasks.DbTask(lambda: ydb.deck_counts(self.db, deck), sig))
        pump()
        QThreadPool.globalInstance().start(tasks.DbTask(lambda: 1 / 0, sig))
        pump()
        self.assertEqual(got, [("done", ydb.deck_counts(self.db, deck)),
                               ("failed", "division by zero")])

    def test_image_loader_signals(self):
        got = []
        sig = tasks.ImageSignals()
        sig.loaded.connect(lambda cid, img: got.append(("loaded", cid, img.isNull())))
        sig.failed.connect(lambda cid: got.append(("failed", cid)))
        images.image_pool().start(tasks.ImageLoader(1, "https://x/1.jpg", sig))   # offline
        pump()
        path = os.path.join(self._dir, "2.jpg")
        img = QImage(10, 10, QImage.Format.Format_RGB32)
        img.fill(QColor("red"))
        img.save(path, "JPG")
        with mock.patch.object(ydb, "cache_image", return_value=path):
            images.image_pool().start(tasks.ImageLoader(2, "https://x/2.jpg", sig))
            pump()
        broken = os.path.join(self._dir, "3.jpg")
        with open(broken, "wb") as fh:
            fh.write(b"kein jpeg")
        with mock.patch.object(ydb, "cache_image", return_value=broken):
            images.image_pool().start(tasks.ImageLoader(3, "https://x/3.jpg", sig))
            pump()
        self.assertEqual(got, [("failed", 1), ("loaded", 2, False), ("failed", 3)])

    def test_tasks_survive_deleted_receivers(self):
        # Beim App-Ende sind die Signal-Objekte evtl. schon abgebaut: emit()
        # wirft dann RuntimeError -- das darf den Worker nicht abstuerzen lassen.
        class _Gone:
            def emit(self, *_a):
                raise RuntimeError("Signal source has been deleted")

        class _Signals:
            done = failed = loaded = _Gone()

        tasks.DbTask(lambda: 1, _Signals()).run()
        tasks.DbTask(lambda: 1 / 0, _Signals()).run()
        tasks.ImageLoader(1, "https://x/1.jpg", _Signals()).run()      # offline
        broken = os.path.join(self._dir, "kaputt.jpg")
        with open(broken, "wb") as fh:
            fh.write(b"kein jpeg")
        with mock.patch.object(ydb, "cache_image", return_value=broken):
            tasks.ImageLoader(2, "https://x/2.jpg", _Signals()).run()


class ThemeAndManualTests(QtTestCase):
    def test_apply_theme(self):
        from yugioh_gui import theme
        app = QApplication.instance()
        old_sheet, old_palette, old_style = app.styleSheet(), app.palette(), app.style().name()
        self.addCleanup(lambda: (app.setStyleSheet(old_sheet), app.setPalette(old_palette),
                                 app.setStyle(old_style)))
        with mock.patch.object(app, "setStyle", wraps=app.setStyle) as set_style:
            theme.apply_theme(app)
        set_style.assert_called_once_with("Fusion")
        self.assertIn("#LintLabel", app.styleSheet())
        self.assertEqual(app.palette().color(app.palette().ColorRole.Window),
                         theme._build_palette().color(app.palette().ColorRole.Window))

    def test_manual_sections_render(self):
        from yugioh_gui.manual import _MANUAL_SECTIONS, HelpView
        view = self.track(HelpView())
        self.assertEqual(view.index.count(), len(_MANUAL_SECTIONS))
        titles = [view.index.item(i).text() for i in range(view.index.count())]
        self.assertTrue(any("Spielfeld" in t for t in titles))
        for row in range(view.index.count()):
            view.index.setCurrentRow(row)
            self.assertTrue(view.content.toPlainText().strip(), titles[row])

    def test_notation_reference(self):
        from yugioh_gui.notation import NOTATION_MD
        for token in ("Lock:", "Req:", "->", "NS", "SS"):
            self.assertIn(token, NOTATION_MD)


class RulebookAndCardHelpTests(QtTestCase):
    """Regelwerk-Tab (DocView mit Suche), Sprung-Navigation, Wortlaut-
    Lesehilfe und Rulings-Dialog in den Kartendetails."""

    ASH = 14558127

    def setUp(self):
        super().setUp()
        from yugioh_gui import navigation
        self.nav = navigation
        self.addCleanup(navigation.set_rulebook_opener, None)
        patcher = mock.patch.object(carddetail.QDesktopServices, "openUrl")
        self.open_url = patcher.start()
        self.addCleanup(patcher.stop)

    def test_rulebook_search_and_jump(self):
        from yugioh_gui.rulebook import RulebookView
        from yugioh_gui.rulebook_text import RULEBOOK_SECTIONS
        view = self.track(RulebookView())
        self.assertEqual(view.index.count(), len(RULEBOOK_SECTIONS))
        view.search.setText("zauberschnelligkeit")        # casefold-Suche
        visible = [view.index.item(i).data(Qt.ItemDataRole.UserRole)
                   for i in range(view.index.count()) if not view.index.item(i).isHidden()]
        self.assertIn("ketten", visible)
        self.assertNotIn("ziel", visible)
        self.assertIn("Kapitel", view.hits.text())
        self.assertIn(view.current_key(), visible)
        view.search.setText("gibt es garantiert nicht xyz")
        self.assertEqual(view.content.toPlainText(), "")
        self.assertTrue(view.show_section("kampf"))       # leert die Suche
        self.assertEqual(view.search.text(), "")
        self.assertEqual(view.current_key(), "kampf")
        self.assertIn("Schadensberechnung", view.content.toPlainText())
        self.assertFalse(view.show_section("unbekannt"))

    def test_navigation_without_or_with_dead_opener(self):
        self.nav.set_rulebook_opener(None)
        self.assertFalse(self.nav.open_rulebook("kampf"))

        class Host:
            def __init__(self):
                self.calls = []

            def go(self, key):
                self.calls.append(key)
        host = Host()
        self.nav.set_rulebook_opener(host.go)
        self.assertTrue(self.nav.open_rulebook("kampf"))
        self.assertFalse(self.nav.open_rulebook(""))
        self.assertEqual(host.calls, ["kampf"])
        del host                                           # schwach referenziert
        import gc
        gc.collect()
        self.assertFalse(self.nav.open_rulebook("kampf"))

    def test_wording_html_and_links(self):
        repo = CardRepository(self.db)
        card = repo.get_card(self.ASH)
        html_text = carddetail.wording_html(card)
        self.assertIn("Schnelleffekt", html_text)
        self.assertIn('href="rulebook:ketten"', html_text)
        self.assertIn("Deutscher Kartentext", html_text)
        calls = []
        self.nav.set_rulebook_opener(calls.append)
        dlg = self.track(carddetail.WordingDialog(card))
        dlg._on_link(carddetail.QUrl("rulebook:kartentext"))
        self.assertEqual(calls, ["kartentext"])
        self.assertEqual(dlg.result(), QDialog.DialogCode.Accepted)
        dlg._on_link(carddetail.QUrl("https://example.org"))
        self.open_url.assert_called_once()

    def test_detail_panel_rulings_dialog_crud(self):
        repo = CardRepository(self.db)
        panel = self.track(carddetail.DetailPanel(repo))
        panel.show_card(repo.get_card(self.ASH))
        self.assertEqual(panel.rulings_btn.text(), "Rulings")
        emitted = []
        panel.rulings_changed.connect(lambda: emitted.append(1))
        edits = [("Antwortet auch auf Effekte im Friedhof", "Konami-FAQ"),
                 ("Geändert", "")]

        def edit(dlg):
            text, source = edits.pop(0)
            dlg.text_edit.setPlainText(text)
            dlg.source_edit.setText(source)
            return QDialog.DialogCode.Accepted
        self.ui.dialogs["_RulingEditDialog"] = edit

        def add_and_edit(dlg):
            dlg._add()
            self.assertEqual(dlg.listw.count(), 1)
            self.assertIn("Konami-FAQ", dlg.listw.item(0).text())
            dlg._edit()
            self.assertTrue(dlg.listw.item(0).text().startswith("Geändert"))
            buttons = [b.text() for b in dlg.findChildren(carddetail.QPushButton)]
            self.assertIn("Konami (englisch, mit FAQ)", buttons)
            self.assertIn("Yugipedia", buttons)
            return QDialog.DialogCode.Accepted
        self.ui.dialogs["CardRulingsDialog"] = add_and_edit
        panel._open_rulings()
        self.assertEqual(panel.rulings_btn.text(), "Rulings (1)")
        self.assertEqual(emitted, [1])

        def delete(dlg):
            dlg._delete()
            self.assertEqual(dlg.listw.count(), 0)
            return QDialog.DialogCode.Accepted
        self.ui.dialogs["CardRulingsDialog"] = delete
        panel._open_rulings()
        self.assertEqual(panel.rulings_btn.text(), "Rulings")
        self.assertEqual(ydb.list_card_rulings(self.db, self.ASH), [])

    def test_detail_dialog_has_help_buttons(self):
        repo = CardRepository(self.db)
        dlg = self.track(carddetail.CardDetailDialog(repo))
        dlg.load(self.ASH)
        ydb.add_card_ruling(self.db, self.ASH, "x")
        dlg.load(self.ASH)
        self.assertEqual(dlg.rulings_btn.text(), "Rulings (1)")
        seen = []
        self.ui.dialogs["WordingDialog"] = lambda d: seen.append(
            d.browser.toPlainText()) or QDialog.DialogCode.Accepted
        dlg._open_wording()
        self.assertIn("Kosten", seen[0])


class CollectionPrintAndLinkTests(QtTestCase):
    ASH = 14558127

    def setUp(self):
        super().setUp()
        from yugioh_gui import navigation
        self.nav = navigation
        self.addCleanup(navigation.set_card_opener, None)

    def test_detail_panel_adds_with_set_and_language(self):
        repo = CardRepository(self.db)
        panel = self.track(carddetail.DetailPanel(repo))
        panel.show_card(repo.get_card(self.ASH))
        self.assertEqual(panel.set_cb.itemText(0), "(ohne Set)")
        self.assertEqual(panel.lang_cb.currentData(), "DE")
        idx = next(i for i in range(panel.set_cb.count())
                   if panel.set_cb.itemData(i) == "RA01-DE008")
        panel.set_cb.setCurrentIndex(idx)
        panel.lang_cb.setCurrentIndex(panel.lang_cb.findData("EN"))
        self.assertEqual(panel.set_cb.currentData(), "RA01-EN008")   # Auswahl bleibt
        before = repo.owned_count(self.ASH)
        panel.qty.setValue(2)
        panel._add_to_collection()
        self.assertEqual(self.scalar(
            "SELECT quantity FROM collection WHERE card_id = ? AND set_code = 'RA01-EN008' "
            "AND language = 'EN'", (self.ASH,)), 2)
        self.assertIn(f"im Bestand: {before + 2}", panel.coll_box.title())
        panel.show_card(repo.get_card(self.main_ids(1)[0]))         # neue Karte
        self.assertEqual(panel.set_cb.currentIndex(), 0)

    def test_doc_links_open_cards_and_chapters(self):
        from PySide6.QtCore import QUrl
        from yugioh_gui.rulebook import RulebookView
        calls = []
        self.nav.set_card_opener(calls.append)
        view = self.track(RulebookView())
        view._on_link(QUrl(f"card:{self.ASH}"))
        self.assertEqual(calls, [self.ASH])
        view._on_link(QUrl("rulebook:kampf"))
        self.assertEqual(view.current_key(), "kampf")
        with mock.patch("yugioh_gui.docview.QDesktopServices.openUrl") as open_url:
            view._on_link(QUrl("https://example.org"))
            open_url.assert_called_once()

    def test_all_rulebook_card_links_exist(self):
        import re as _re
        from yugioh_gui.rulebook_text import RULEBOOK_SECTIONS
        ids = {int(m) for _k, _t, md in RULEBOOK_SECTIONS
               for m in _re.findall(r"\(card:(\d+)\)", md)}
        self.assertGreaterEqual(len(ids), 5)
        for cid in ids:
            self.assertIsNotNone(CardRepository(self.db).get_card(cid), cid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
