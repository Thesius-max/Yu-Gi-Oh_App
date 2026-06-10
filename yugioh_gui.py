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

import os
import shutil
import sys

from PySide6.QtCore import (
    Qt, QMarginsF, QObject, QRunnable, QThreadPool, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QImage, QPageLayout, QPageSize, QPdfWriter, QPixmap,
    QPixmapCache, QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressDialog, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

import yugioh_db as ydb

# Standard-Bildquelle. Bilder werden lokal zwischengespeichert (kein Hotlinking).
# Bei Bedarf gegen die in der API gelieferte card_images-URL austauschbar.
IMAGE_URL = "https://images.ygoprodeck.com/images/cards/{}.jpg"

# ---------------------------------------------------------------------------
# Asynchroner Bild-Loader
# ---------------------------------------------------------------------------

class _ImageSignals(QObject):
    loaded = Signal(int, QImage)  # (card_id, image)
    failed = Signal(int)          # card_id


class _ImageLoader(QRunnable):
    def __init__(self, card_id: int, url: str, signals: _ImageSignals):
        super().__init__()
        self._card_id = card_id
        self._url = url
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            path = ydb.cache_image(self._card_id, self._url)
            img = QImage(str(path))
            if img.isNull():
                self._signals.failed.emit(self._card_id)
            else:
                self._signals.loaded.emit(self._card_id, img)
        except RuntimeError:
            pass  # Empfaenger beim App-Ende schon abgebaut -- nichts zu melden
        except Exception:
            try:
                self._signals.failed.emit(self._card_id)
            except RuntimeError:
                pass


class _DbTaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _DbTask(QRunnable):
    """Fuehrt eine laengere DB-/Netzwerkfunktion abseits des UI-Threads aus
    (z.B. das Kartendaten-Update); Ergebnis kommt per Signal zurueck."""

    def __init__(self, fn, signals: _DbTaskSignals):
        super().__init__()
        self._fn = fn
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:
            try:
                self._signals.failed.emit(str(exc))
            except RuntimeError:
                pass
            return
        try:
            self._signals.done.emit(result)
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Übersetzungstabellen (Englisch → Deutsch)
# ---------------------------------------------------------------------------

_ATTR_DE: dict[str, str] = {
    "DARK":   "FINSTERNIS",
    "DIVINE": "GÖTTLICH",
    "EARTH":  "ERDE",
    "FIRE":   "FEUER",
    "LIGHT":  "LICHT",
    "WATER":  "WASSER",
    "WIND":   "WIND",
}

_TYPE_DE: dict[str, str] = {
    "Effect Monster":                  "Effekt-Monster",
    "Flip Effect Monster":             "Flipp-Effekt-Monster",
    "Flip Tuner Effect Monster":       "Flipp-Tuner-Effekt-Monster",
    "Fusion Monster":                  "Fusionsmonster",
    "Gemini Monster":                  "Gemini-Monster",
    "Link Monster":                    "Linkmonster",
    "Normal Monster":                  "Normalmonster",
    "Normal Tuner Monster":            "Tuner (Normal)",
    "Pendulum Effect Fusion Monster":  "Pendel-Effekt-Fusionsmonster",
    "Pendulum Effect Monster":         "Pendel-Effekt-Monster",
    "Pendulum Effect Ritual Monster":  "Pendel-Effekt-Ritualmonster",
    "Pendulum Flip Effect Monster":    "Pendel-Flipp-Effekt-Monster",
    "Pendulum Normal Monster":         "Pendel-Normalmonster",
    "Pendulum Tuner Effect Monster":   "Pendel-Tuner-Effekt-Monster",
    "Ritual Effect Monster":           "Ritual-Effekt-Monster",
    "Ritual Monster":                  "Ritualmonster",
    "Skill Card":                      "Skill-Karte",
    "Spell Card":                      "Zauberkarte",
    "Spirit Monster":                  "Geist-Monster",
    "Synchro Monster":                 "Synchromonster",
    "Synchro Pendulum Effect Monster": "Synchro-Pendel-Effekt-Monster",
    "Synchro Tuner Monster":           "Synchro-Tuner",
    "Token":                           "Spielmarke",
    "Toon Monster":                    "Toon-Monster",
    "Trap Card":                       "Fallenkarte",
    "Tuner Monster":                   "Tuner",
    "Union Effect Monster":            "Union-Effekt-Monster",
    "XYZ Monster":                     "Xyz-Monster",
    "XYZ Pendulum Effect Monster":     "Xyz-Pendel-Effekt-Monster",
}

# Kartenklassen für die Gruppierung (Sammlung, Main-Deck).
_CATEGORY_DE: dict[str, str] = {
    "monster": "Monster",
    "spell":   "Zauber",
    "trap":    "Falle",
    "other":   "Sonstige",
}
_CATEGORY_ORDER = ("monster", "spell", "trap", "other")
_GROUP_HEADER_BG = QColor("#d8d8d8")  # dezente Kopfzeile in der Sammlungstabelle

# Anzeigenamen der Baustein-Rollen (die Begriffe sind im deutschen
# Yu-Gi-Oh-Sprachgebrauch etabliert, daher unübersetzt).
_ROLE_DE: dict[str, str] = {
    "starter":  "Starter",
    "extender": "Extender",
    "payoff":   "Payoff",
    "handtrap": "Handtrap",
}
# Zweiter Daten-Slot an Bausteine-Listeneinträgen: die Rolle (UserRole = card_id).
_PIECE_ROLE_DATA = Qt.ItemDataRole.UserRole + 1


def _pct(p: float) -> str:
    """Wahrscheinlichkeit als deutschen Prozentwert formatieren (84,2 %)."""
    return f"{100 * p:.1f}".replace(".", ",") + " %"


_RACE_DE: dict[str, str] = {
    # Monster-Typen
    "Aqua":         "Aqua",
    "Beast":        "Ungeheuer",
    "Beast-Warrior":"Ungeheuer-Krieger",
    "Creator God":  "Creator God",
    "Cyberse":      "Cyberse",
    "Dinosaur":     "Dinosaurier",
    "Divine-Beast": "Göttliches Ungeheuer",
    "Dragon":       "Drache",
    "Fairy":        "Fee",
    "Fiend":        "Unterweltler",
    "Fish":         "Fisch",
    "Illusion":     "Illusion",
    "Insect":       "Insekt",
    "Machine":      "Maschine",
    "Plant":        "Pflanze",
    "Psychic":      "Psi",
    "Pyro":         "Pyro",
    "Reptile":      "Reptil",
    "Rock":         "Fels",
    "Sea Serpent":  "Seeschlange",
    "Spellcaster":  "Hexer",
    "Thunder":      "Donner",
    "Warrior":      "Krieger",
    "Winged Beast": "Geflügeltes Ungeheuer",
    "Wyrm":         "Wyrm",
    "Zombie":       "Zombie",
    # Zauber/Fallen-Untertypen
    "Continuous":   "Permanent",
    "Counter":      "Konter",
    "Equip":        "Ausrüstung",
    "Field":        "Feld",
    "Normal":       "Normal",
    "Quick-Play":   "Schnelleffekt",
    "Ritual":       "Ritual",
}


# ---------------------------------------------------------------------------
# Datenzugriff -- kapselt alle SQL-Abfragen, die die UI braucht
# ---------------------------------------------------------------------------

class CardRepository:
    # Whitelist, damit Spaltennamen nie aus Benutzereingaben kommen.
    _FILTER_COLUMNS = ("type", "attribute", "archetype")

    def __init__(self, db_path: str = ydb.DEFAULT_DB):
        self.db_path = db_path

    def exists(self) -> bool:
        return os.path.exists(self.db_path)

    def distinct(self, column: str) -> list[str]:
        if column not in self._FILTER_COLUMNS:
            raise ValueError(f"Unzulässige Spalte: {column}")
        conn = ydb._connect(self.db_path)
        try:
            rows = conn.execute(
                f"SELECT DISTINCT {column} FROM cards "
                f"WHERE {column} IS NOT NULL AND {column} != '' "
                f"ORDER BY {column}"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def query(
        self,
        *,
        text: str = "",
        type: str | None = None,
        attribute: str | None = None,
        archetype: str | None = None,
        level: int | None = None,
        atk_min: int = 0,
        atk_max: int = 0,
        only_collection: bool = False,
        limit: int = 300,
    ) -> list:
        clauses: list[str] = []
        params: list = []

        if text.strip():
            # Benutzereingabe als Phrase + Praefix; Anfuehrungszeichen escapen,
            # damit kein FTS-Syntaxfehler entsteht.
            safe = text.strip().replace('"', '""')
            base = (
                "SELECT c.* FROM cards_fts "
                "JOIN cards c ON c.id = cards_fts.rowid "
                'WHERE cards_fts MATCH ?'
            )
            params.append(f'"{safe}"*')
        else:
            base = "SELECT c.* FROM cards c WHERE 1=1"

        if type:
            clauses.append("c.type = ?"); params.append(type)
        if attribute:
            clauses.append("c.attribute = ?"); params.append(attribute)
        if archetype:
            clauses.append("c.archetype = ?"); params.append(archetype)
        if level:
            clauses.append("c.level = ?"); params.append(level)
        if atk_min > 0:
            clauses.append("c.atk >= ?"); params.append(atk_min)
        if atk_max > 0:
            clauses.append("c.atk <= ?"); params.append(atk_max)
        if only_collection:
            clauses.append(
                "EXISTS (SELECT 1 FROM collection col WHERE col.card_id = c.id)"
            )

        sql = base + "".join(" AND " + c for c in clauses)
        sql += (" ORDER BY cards_fts.rank LIMIT ?" if text.strip() else " ORDER BY c.name LIMIT ?")
        params.append(limit)

        conn = ydb._connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def owned_count(self, card_id: int) -> int:
        conn = ydb._connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) AS n "
                "FROM collection WHERE card_id = ?",
                (card_id,),
            ).fetchone()
            return int(row["n"]) if row else 0
        finally:
            conn.close()

    def get_card(self, card_id: int):
        conn = ydb._connect(self.db_path)
        try:
            return conn.execute(
                "SELECT * FROM cards WHERE id = ?", (card_id,)
            ).fetchone()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Karten-Suchdialog (für das Hinzufügen aus dem Deck-Tab)
# ---------------------------------------------------------------------------

class CardSearchDialog(QDialog):
    def __init__(self, repo: "CardRepository", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Karte hinzufügen")
        self.resize(420, 520)
        self.repo = repo
        self.selected_id: int | None = None

        layout = QVBoxLayout(self)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Name oder Kartentext suchen …")
        self.search_box.textChanged.connect(self._search)
        layout.addWidget(self.search_box)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list, stretch=1)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Hinzufügen")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Abbrechen")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self.search_box.setFocus()

    def _search(self, text: str) -> None:
        self.list.clear()
        if not text.strip():
            return
        for c in self.repo.query(text=text, limit=80):
            item = QListWidgetItem(c["name_de"] or c["name"])
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def chosen_card_id(self) -> int | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


# ---------------------------------------------------------------------------
# Detailansicht (rechte Spalte)
# ---------------------------------------------------------------------------

class DetailPanel(QWidget):
    def __init__(self, repo: CardRepository):
        super().__init__()
        self.repo = repo
        self.current_id: int | None = None
        self._img_signals = _ImageSignals()
        self._img_signals.loaded.connect(self._on_image_loaded)
        self._img_signals.failed.connect(self._on_image_failed)

        layout = QVBoxLayout(self)

        self.image = QLabel("Keine Karte ausgewählt")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(220, 320)
        self.image.setStyleSheet("border: 1px solid #555; color: #888;")

        self.name = QLabel("")
        self.name.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.name.setWordWrap(True)
        # Eigene Uebersetzung pflegen -- fuer Karten, denen die API keinen
        # deutschen Namen/Text liefert.
        self.edit_trans_btn = QPushButton("✎ DE")
        self.edit_trans_btn.setToolTip("Deutsche Übersetzung bearbeiten")
        self.edit_trans_btn.setFixedWidth(48)
        self.edit_trans_btn.clicked.connect(self._edit_translation)

        self.stats = QLabel("")
        self.stats.setWordWrap(True)

        self.text = QTextEdit()
        self.text.setReadOnly(True)

        # Sammlung
        coll_box = QGroupBox("Sammlung")
        coll_layout = QHBoxLayout(coll_box)
        self.owned = QLabel("im Bestand: 0")
        self.qty = QSpinBox()
        self.qty.setRange(1, 99)
        self.add_btn = QPushButton("Hinzufügen")
        self.add_btn.clicked.connect(self._add_to_collection)
        coll_layout.addWidget(self.owned)
        coll_layout.addStretch()
        coll_layout.addWidget(self.qty)
        coll_layout.addWidget(self.add_btn)

        # Deck
        deck_box = QGroupBox("Deck")
        deck_layout = QHBoxLayout(deck_box)
        self.add_deck_btn = QPushButton("+ Deck")
        self.add_side_btn = QPushButton("+ Side")
        self.add_deck_btn.clicked.connect(lambda: self._add_to_deck(False))
        self.add_side_btn.clicked.connect(lambda: self._add_to_deck(True))
        deck_layout.addWidget(self.add_deck_btn)
        deck_layout.addWidget(self.add_side_btn)

        # Wird von MainWindow gesetzt: callback(card_id, to_side) -> (added, msg)
        self.add_to_deck_callback = None

        # Kombo
        combo_box = QGroupBox("Kombo")
        combo_layout = QHBoxLayout(combo_box)
        self.add_combo_btn = QPushButton("+ als Baustein zur aktiven Kombo")
        self.add_combo_btn.clicked.connect(self._add_to_combo)
        combo_layout.addWidget(self.add_combo_btn)
        # Wird von MainWindow gesetzt: callback(card_id)
        self.add_to_combo_callback = None

        layout.addWidget(self.image)
        name_row = QHBoxLayout()
        name_row.addWidget(self.name, stretch=1)
        name_row.addWidget(
            self.edit_trans_btn, alignment=Qt.AlignmentFlag.AlignTop
        )
        layout.addLayout(name_row)
        layout.addWidget(self.stats)
        layout.addWidget(self.text, stretch=1)
        layout.addWidget(coll_box)
        layout.addWidget(deck_box)
        layout.addWidget(combo_box)
        self._set_enabled(False)

    def _set_enabled(self, on: bool):
        self.qty.setEnabled(on)
        self.add_btn.setEnabled(on)
        self.add_deck_btn.setEnabled(on)
        self.add_side_btn.setEnabled(on)
        self.add_combo_btn.setEnabled(on)
        self.edit_trans_btn.setEnabled(on)

    def _edit_translation(self) -> None:
        """Eigene DE-Uebersetzung erfassen/aendern (Override; ueberlebt
        Karten-Updates). Leere Felder = kein Override fuer dieses Feld."""
        if self.current_id is None:
            return
        card = self.repo.get_card(self.current_id)
        if card is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Deutsche Übersetzung bearbeiten")
        dlg.resize(420, 360)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(f"Karte: {card['name']}"))
        form = QFormLayout()
        name_edit = QLineEdit(card["name_de"] or "")
        form.addRow("Name (DE)", name_edit)
        v.addLayout(form)
        v.addWidget(QLabel("Kartentext (DE):"))
        desc_edit = QTextEdit()
        desc_edit.setPlainText(card["desc_de"] or "")
        v.addWidget(desc_edit, stretch=1)
        hint = QLabel("Leere Felder lassen die API-Daten unangetastet.")
        hint.setStyleSheet("color: #888;")
        v.addWidget(hint)
        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Speichern")
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Abbrechen")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        v.addLayout(btn_row)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        ydb.set_card_translation(
            self.repo.db_path, self.current_id,
            name_de=name_edit.text(), desc_de=desc_edit.toPlainText(),
        )
        updated = self.repo.get_card(self.current_id)
        if updated is not None:
            self.show_card(updated)

    def _add_to_combo(self) -> None:
        if self.current_id is None or self.add_to_combo_callback is None:
            return
        self.add_to_combo_callback(self.current_id)

    def _add_to_deck(self, to_side: bool) -> None:
        if self.current_id is None or self.add_to_deck_callback is None:
            return
        result = self.add_to_deck_callback(self.current_id, to_side)
        if result:
            _added, msg = result
            if msg:
                QMessageBox.information(self, "Deck", msg)

    def show_card(self, card) -> None:
        self.current_id = card["id"]
        self.name.setText(card["name_de"] or card["name"])

        parts = []
        if card["type"]:
            parts.append(_TYPE_DE.get(card["type"], card["type"]))
        if card["race"]:
            parts.append(_RACE_DE.get(card["race"], card["race"]))
        if card["attribute"]:
            parts.append(_ATTR_DE.get(card["attribute"], card["attribute"]))
        if card["level"] is not None:
            parts.append(f"Stufe {card['level']}")
        if card["atk"] is not None or card["def"] is not None:
            atk = card["atk"] if card["atk"] is not None else "—"
            # Link-Monster haben keine DEF (def ist NULL) -> nicht "None" anzeigen.
            if card["def"] is not None:
                parts.append(f"ATK {atk} / DEF {card['def']}")
            else:
                parts.append(f"ATK {atk}")
        if card["archetype"]:
            parts.append(f"Archetyp: {card['archetype']}")
        self.stats.setText("  •  ".join(parts))
        self.text.setPlainText(card["desc_de"] or card["description"] or "")

        self._load_image(card["id"])
        self._update_owned(card["id"])
        self._set_enabled(True)

    def _update_owned(self, card_id: int) -> None:
        owned = self.repo.owned_count(card_id)
        bound = ydb.card_bound_in_decks(self.repo.db_path, card_id)
        text = f"im Bestand: {owned}"
        if bound:
            text += f"  ·  in Decks: {bound}"
            if bound > owned:
                text += "  ⚠"
        self.owned.setText(text)

    @staticmethod
    def _scaled(pixmap: QPixmap) -> QPixmap:
        return pixmap.scaled(
            220, 320,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _load_image(self, card_id: int) -> None:
        # Bereits skalierte Pixmaps im Prozess-Cache halten -- erneutes Anwählen
        # einer Karte erspart so das volle JPEG-Dekodieren und Skalieren.
        key = f"card:{card_id}"
        cached = QPixmapCache.find(key)
        if cached is not None and not cached.isNull():
            self.image.setPixmap(cached)
            return
        path = os.path.join(ydb.IMAGE_DIR, f"{card_id}.jpg")
        if os.path.exists(path):
            pix = self._scaled(QPixmap(path))
            QPixmapCache.insert(key, pix)
            self.image.setPixmap(pix)
            return
        self.image.setText("Lade …")
        QThreadPool.globalInstance().start(
            _ImageLoader(card_id, IMAGE_URL.format(card_id), self._img_signals)
        )

    def _on_image_loaded(self, card_id: int, img: QImage) -> None:
        pix = self._scaled(QPixmap.fromImage(img))
        QPixmapCache.insert(f"card:{card_id}", pix)
        if card_id != self.current_id:
            return
        self.image.setPixmap(pix)

    def _on_image_failed(self, card_id: int) -> None:
        if card_id != self.current_id:
            return
        self.image.setText("(Bild offline nicht verfügbar)")

    def _add_to_collection(self) -> None:
        if self.current_id is None:
            return
        ydb.add_to_collection(self.repo.db_path, self.current_id, self.qty.value())
        self._update_owned(self.current_id)


# ---------------------------------------------------------------------------
# Sammlungsverwaltung (eigener Tab)
# ---------------------------------------------------------------------------

class CollectionView(QWidget):
    COLUMNS = ["Name", "Menge", "Set", "Edition", "Zustand", "Sprache", "Notiz"]

    def __init__(self, repo: CardRepository):
        super().__init__()
        self.repo = repo

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.summary = QLabel("")
        refresh_btn = QPushButton("Aktualisieren")
        refresh_btn.clicked.connect(self.refresh)
        self.remove_btn = QPushButton("Ausgewählten Eintrag entfernen")
        self.remove_btn.clicked.connect(self._remove_selected)
        toolbar.addWidget(self.summary)
        toolbar.addStretch()
        toolbar.addWidget(refresh_btn)
        toolbar.addWidget(self.remove_btn)

        # Filterleiste: Namenssuche + Dropdowns. Die Dropdowns zeigen nur
        # Werte, die im Bestand tatsaechlich vorkommen.
        filters = QHBoxLayout()
        self.filter_text = QLineEdit()
        self.filter_text.setPlaceholderText("Name filtern …")
        self.filter_text.textChanged.connect(self.refresh)
        self.filter_cat = QComboBox()
        self.filter_attr = QComboBox()
        self.filter_arch = QComboBox()
        for cb in (self.filter_cat, self.filter_attr, self.filter_arch):
            cb.addItem("(alle)", None)
            cb.currentIndexChanged.connect(self.refresh)
        for cat in _CATEGORY_ORDER:
            self.filter_cat.addItem(_CATEGORY_DE[cat], cat)
        filters.addWidget(self.filter_text, stretch=1)
        filters.addWidget(QLabel("Klasse:"))
        filters.addWidget(self.filter_cat)
        filters.addWidget(QLabel("Attribut:"))
        filters.addWidget(self.filter_attr)
        filters.addWidget(QLabel("Archetyp:"))
        filters.addWidget(self.filter_arch)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        layout.addLayout(toolbar)
        layout.addLayout(filters)
        layout.addWidget(self.table)
        self.refresh()

    def _reload_filter_values(self) -> None:
        """Attribut-/Archetyp-Dropdowns aus dem Bestand neu befuellen;
        die aktuelle Auswahl bleibt erhalten."""
        for cb, column, trans in (
            (self.filter_attr, "attribute", _ATTR_DE),
            (self.filter_arch, "archetype", {}),
        ):
            keep = cb.currentData()
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("(alle)", None)
            for val in ydb.collection_distinct(self.repo.db_path, column):
                cb.addItem(trans.get(val, val), val)
            idx = cb.findData(keep)
            cb.setCurrentIndex(max(idx, 0))
            cb.blockSignals(False)

    def refresh(self) -> None:
        if not self.repo.exists():
            self.table.clearSpans()
            self.table.setRowCount(0)
            self.summary.setText("Keine Datenbank vorhanden.")
            return
        self._reload_filter_values()
        rows = ydb.list_collection(
            self.repo.db_path,
            text=self.filter_text.text(),
            category=self.filter_cat.currentData(),
            attribute=self.filter_attr.currentData(),
            archetype=self.filter_arch.currentData(),
        )
        # Nach Kartenklasse bucketn; Reihenfolge je Gruppe bleibt (Name).
        buckets: dict[str, list] = {c: [] for c in _CATEGORY_ORDER}
        for row in rows:
            buckets[ydb.card_category(row["type"])].append(row)
        self.table.clearSpans()
        self.table.setRowCount(0)
        for cat in _CATEGORY_ORDER:
            group = buckets[cat]
            if not group:
                continue
            # Gesamtmenge der Karten zählen (nicht Einträge), wie im Deck.
            self._add_header_row(
                _CATEGORY_DE[cat], sum(row["quantity"] for row in group)
            )
            for row in group:
                self._add_data_row(row)
        self._update_summary(rows)

    def _add_header_row(self, label: str, count: int) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        item = QTableWidgetItem(f"{label}  ({count})")
        # Kopfzeile: nur aktiviert (nicht auswählbar/editierbar), kein entry_id.
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        font = item.font(); font.setBold(True); item.setFont(font)
        item.setBackground(_GROUP_HEADER_BG)
        self.table.setItem(r, 0, item)
        self.table.setSpan(r, 0, 1, len(self.COLUMNS))

    def _add_data_row(self, row) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        name_item = QTableWidgetItem(row["name_de"] or row["name"])
        # entry_id an der Namenszelle ablegen -- identifiziert die Zeile.
        name_item.setData(Qt.ItemDataRole.UserRole, row["entry_id"])
        self.table.setItem(r, 0, name_item)

        spin = QSpinBox()
        # Obergrenze nie unter die tatsaechliche Menge -- sonst wuerde die
        # Anzeige stillschweigend auf das Maximum gekappt.
        spin.setRange(1, max(999, row["quantity"]))
        spin.setValue(row["quantity"])
        # Menge live in die DB schreiben (entry_id ueber Default gebunden).
        spin.valueChanged.connect(
            lambda value, e=row["entry_id"]: self._on_qty_changed(e, value)
        )
        self.table.setCellWidget(r, 1, spin)

        for col, key in enumerate(
            ("set_code", "edition", "condition", "language", "notes"), start=2
        ):
            self.table.setItem(r, col, QTableWidgetItem(row[key] or ""))

    def _on_qty_changed(self, entry_id: int, value: int) -> None:
        ydb.set_collection_quantity(self.repo.db_path, entry_id, value)
        self._update_summary()

    def _remove_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        entry_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if entry_id is None:
            return  # Gruppen-Kopfzeile, kein echter Eintrag
        reply = QMessageBox.question(
            self, "Eintrag entfernen",
            f"'{item.text()}' aus der Sammlung entfernen?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            ydb.remove_collection_entry(self.repo.db_path, entry_id)
            self.refresh()

    def _filters_active(self) -> bool:
        return bool(
            self.filter_text.text().strip()
            or self.filter_cat.currentData()
            or self.filter_attr.currentData()
            or self.filter_arch.currentData()
        )

    def _update_summary(self, filtered_rows=None) -> None:
        entries, unique, total = ydb.collection_stats(self.repo.db_path)
        text = (
            f"{entries} Einträge  ·  {unique} verschiedene Karten  ·  "
            f"{total} Karten gesamt"
        )
        if filtered_rows is not None and self._filters_active():
            shown = sum(r["quantity"] for r in filtered_rows)
            text += f"  ·  Filter: {len(filtered_rows)} Einträge ({shown} Karten)"
        self.summary.setText(text)


# ---------------------------------------------------------------------------
# Deckbuilding (eigener Tab)
# ---------------------------------------------------------------------------

ZONE_LABELS = {"main": "Main Deck", "extra": "Extra Deck", "side": "Side Deck"}


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


class ComboFromDeckDialog(QDialog):
    """Neue Kombo direkt aus den Karten des aktiven Decks: Name vergeben,
    Bausteine ankreuzen (Main + Extra — das Side Deck zählt bei der
    Kombo-Abdeckung ohnehin nicht mit)."""

    def __init__(self, repo: CardRepository, deck_id: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kombo aus Deck anlegen")
        self.resize(420, 520)
        v = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("z.B. 'Karte X → Endboard Y'")
        form.addRow("Name", self.name_edit)
        v.addLayout(form)
        v.addWidget(QLabel("Bausteine ankreuzen:"))
        self.list = QListWidget()
        for zone in ("main", "extra"):
            rows = ydb.deck_cards(repo.db_path, deck_id, zone)
            if not rows:
                continue
            header = QListWidgetItem(f"— {ZONE_LABELS[zone]} —")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            font = header.font(); font.setBold(True); header.setFont(font)
            self.list.addItem(header)
            for r in rows:
                item = QListWidgetItem(r["name"])
                item.setData(Qt.ItemDataRole.UserRole, r["card_id"])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.list.addItem(item)
        v.addWidget(self.list, stretch=1)
        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Anlegen")
        ok_btn.clicked.connect(self._accept)
        cancel_btn = QPushButton("Abbrechen")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        v.addLayout(btn_row)

    def _accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.information(self, "Kombo", "Bitte einen Namen vergeben.")
            return
        self.accept()

    def combo_name(self) -> str:
        return self.name_edit.text().strip()

    def selected_card_ids(self) -> list[int]:
        return [
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.CheckState.Checked
        ]


class DeckView(QWidget):
    def __init__(self, repo: CardRepository):
        super().__init__()
        self.repo = repo
        self.deck_id: int | None = None
        # Wird von MainWindow gesetzt: oeffnet eine Kombo im Kombos-Tab.
        self.open_combo_callback = None

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
        top.addWidget(QLabel("Deck:"))
        top.addWidget(self.deck_cb, stretch=1)
        top.addWidget(new_btn)
        top.addWidget(del_btn)
        top.addWidget(import_btn)
        top.addWidget(export_btn)
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
        imp = report["imported"]
        lines = [
            f"Importiert: Main {imp['main']}, Extra {imp['extra']}, "
            f"Side {imp['side']}."
        ]
        if report["unknown"]:
            ids = ", ".join(str(i) for i in report["unknown"])
            lines.append(f"Nicht in der Datenbank (übersprungen): {ids}")
        if report["capped"]:
            lines.append(
                "Über der 3-Kopien-Grenze gekürzt: " + ", ".join(report["capped"])
            )
        if report["moved"]:
            lines.append(
                "In die passende Zone verschoben: " + ", ".join(report["moved"])
            )
        self._reload_decks()
        idx = self.deck_cb.findData(deck_id)
        if idx >= 0:
            self.deck_cb.setCurrentIndex(idx)
        if len(lines) > 1:
            QMessageBox.information(self, "Deck importiert", "\n\n".join(lines))
        else:
            self.status.setText(lines[0] + "  " + self.status.text())

    def _export_deck(self) -> None:
        if self.deck_id is None:
            return
        suggested = self.deck_cb.currentText().strip() or "deck"
        path, _ = QFileDialog.getSaveFileName(
            self, "Deck exportieren", suggested + ".ydk",
            "YGOPro-Deck (*.ydk);;Alle Dateien (*)",
        )
        if not path:
            return
        try:
            text = ydb.export_deck_ydk(self.repo.db_path, self.deck_id)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Export fehlgeschlagen", str(exc))
            return
        self.status.setText(f"Deck exportiert nach {path}")

    # -- Anzeige ------------------------------------------------------------

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
        try:
            text = ydb.export_deck_combos_text(self.repo.db_path, self.deck_id)
            # Format folgt der Endung; ohne Endung entscheidet der Filter.
            as_pdf = path.lower().endswith(".pdf") or (
                "PDF" in selected and "." not in os.path.basename(path)
            )
            if as_pdf:
                if not path.lower().endswith(".pdf"):
                    path += ".pdf"
                self._write_pdf(path, text)
            else:
                if "." not in os.path.basename(path):
                    path += ".txt"
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Export fehlgeschlagen", str(exc))
            return
        self.status.setText(f"Kombo-Linien exportiert nach {path}")

    @staticmethod
    def _write_pdf(path: str, text: str) -> None:
        """Text unveraendert als PDF setzen (Monospace, A4). QPdfWriter
        gehoert zu PySide6 -- keine zusaetzliche Abhaengigkeit."""
        writer = QPdfWriter(path)
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageMargins(
            QMarginsF(15, 12, 15, 12), QPageLayout.Unit.Millimeter
        )
        doc = QTextDocument()
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(9)
        doc.setDefaultFont(font)
        doc.setPlainText(text)
        doc.print_(writer)

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
        lv.addWidget(QLabel("Meine Kombos:"))
        self.combo_list = QListWidget()
        self.combo_list.currentItemChanged.connect(self._on_select)
        lv.addWidget(self.combo_list, stretch=1)
        btn_row = QHBoxLayout()
        new_btn = QPushButton("Neue Kombo")
        new_btn.clicked.connect(self._new_combo)
        del_btn = QPushButton("Löschen")
        del_btn.clicked.connect(self._delete_combo)
        btn_row.addWidget(new_btn)
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
        notation_btn = QPushButton("Notation…")
        notation_btn.clicked.connect(self._show_notation_help)
        steps_head.addWidget(notation_btn)
        rv.addLayout(steps_head)
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
        self.lint_label.setStyleSheet("color: #b36b00;")
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
            ydb.list_combos(self.repo.db_path, self.deck_filter_cb.currentData())
            if self.repo.exists() else []
        )
        for c in combos:
            item = QListWidgetItem(self._combo_label(c))
            item.setData(Qt.ItemDataRole.UserRole, c["combo_id"])
            self.combo_list.addItem(item)
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
        if combo is not None:
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

    def _delete_combo(self) -> None:
        if self.combo_id is None:
            return
        reply = QMessageBox.question(
            self, "Kombo löschen",
            f"Kombo '{self.name_edit.text()}' löschen?",
        )
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
# Hauptfenster
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, db_path: str = ydb.DEFAULT_DB):
        super().__init__()
        self.setWindowTitle("Yu-Gi-Oh -- Sammlung & Suche")
        self.resize(1100, 700)
        self.repo = CardRepository(db_path)
        # Gepackter Erststart: mitgelieferte Seed-DB in den Nutzerordner kopieren.
        ydb.ensure_user_db(db_path)
        if self.repo.exists():
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

        self.collection_view = CollectionView(self.repo)
        self.deck_view = DeckView(self.repo)
        self.combo_view = ComboView(self.repo)
        # Detailansicht -> aktives Deck bzw. aktive Kombo
        self.detail.add_to_deck_callback = self.deck_view.add_card
        self.detail.add_to_combo_callback = self.combo_view.add_piece
        # Deck-Tab -> neue Kombo im Kombos-Tab oeffnen
        self.deck_view.open_combo_callback = self._open_combo

        self.tabs = QTabWidget()
        self.tabs.addTab(splitter, "Suche")
        self.tabs.addTab(self.collection_view, "Sammlung")
        self.tabs.addTab(self.deck_view, "Deck")
        self.tabs.addTab(self.combo_view, "Kombos")
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
                stat = f"  [ATK {c['atk']} / DEF {c['def']}]"
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
        elif widget is self.combo_view:
            self.combo_view.refresh()

    def _open_combo(self, combo_id: int) -> None:
        """Aus dem Deck-Tab: in den Kombos-Tab wechseln und die Kombo zeigen."""
        self.tabs.setCurrentWidget(self.combo_view)
        self.combo_view.focus_combo(combo_id)

    def closeEvent(self, event) -> None:
        # Noch nicht gespeicherte Kombo-Eingaben sichern (Auto-Save-Timer
        # koennte sonst verfallen).
        self.combo_view.flush_pending()
        super().closeEvent(event)

    # -- Kartendaten-Update (Menü 'Daten') ------------------------------------

    def _build_data_menu(self) -> None:
        menu = self.menuBar().addMenu("Daten")
        self._check_action = menu.addAction("Auf Updates prüfen")
        self._check_action.triggered.connect(self._check_for_update)
        self._update_action = menu.addAction("Kartendaten aktualisieren…")
        self._update_action.triggered.connect(self._start_update)

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
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
