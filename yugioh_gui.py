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
import sys

from PySide6.QtCore import Qt, QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap, QPixmapCache
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
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
        except Exception:
            self._signals.failed.emit(self._card_id)


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
        layout.addWidget(self.name)
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
        self.owned.setText(f"im Bestand: {self.repo.owned_count(card['id'])}")
        self._set_enabled(True)

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
        self.owned.setText(f"im Bestand: {self.repo.owned_count(self.current_id)}")


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
        layout.addWidget(self.table)
        self.refresh()

    def refresh(self) -> None:
        if not self.repo.exists():
            self.table.setRowCount(0)
            self.summary.setText("Keine Datenbank vorhanden.")
            return
        rows = ydb.list_collection(self.repo.db_path)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            name_item = QTableWidgetItem(row["name"])
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
        self._update_summary()

    def _on_qty_changed(self, entry_id: int, value: int) -> None:
        ydb.set_collection_quantity(self.repo.db_path, entry_id, value)
        self._update_summary()

    def _remove_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        entry_id = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, "Eintrag entfernen",
            f"'{item.text()}' aus der Sammlung entfernen?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            ydb.remove_collection_entry(self.repo.db_path, entry_id)
            self.refresh()

    def _update_summary(self) -> None:
        entries, unique, total = ydb.collection_stats(self.repo.db_path)
        self.summary.setText(
            f"{entries} Einträge  ·  {unique} verschiedene Karten  ·  "
            f"{total} Karten gesamt"
        )


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

    def populate(self, rows) -> int:
        self.list.clear()
        total = 0
        for r in rows:
            item = QListWidgetItem(f"{r['quantity']}x  {r['name']}")
            item.setData(Qt.ItemDataRole.UserRole, r["card_id"])
            self.list.addItem(item)
            total += r["quantity"]
        return total


class DeckView(QWidget):
    def __init__(self, repo: CardRepository):
        super().__init__()
        self.repo = repo
        self.deck_id: int | None = None

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.deck_cb = QComboBox()
        self.deck_cb.currentIndexChanged.connect(self._on_deck_selected)
        new_btn = QPushButton("Neues Deck")
        new_btn.clicked.connect(self._new_deck)
        del_btn = QPushButton("Deck löschen")
        del_btn.clicked.connect(self._delete_deck)
        top.addWidget(QLabel("Deck:"))
        top.addWidget(self.deck_cb, stretch=1)
        top.addWidget(new_btn)
        top.addWidget(del_btn)
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
        cv.addWidget(QLabel("Kombos nach Abdeckung:"))
        self.combo_list = QListWidget()
        self.combo_list.currentItemChanged.connect(self._on_combo_selected)
        cv.addWidget(self.combo_list, stretch=1)
        cv.addWidget(QLabel("Bausteine (vorhanden / benötigt):"))
        self.combo_pieces = QListWidget()
        cv.addWidget(self.combo_pieces, stretch=1)
        self.add_missing_btn = QPushButton("Fehlende Bausteine ins Deck")
        self.add_missing_btn.clicked.connect(self._add_missing)
        cv.addWidget(self.add_missing_btn)

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

    # -- Anzeige ------------------------------------------------------------

    def refresh(self) -> None:
        if self.deck_id is None:
            for p in self.panels.values():
                p.list.clear()
            self.status.setText(
                "Kein Deck ausgewählt. Lege über 'Neues Deck' eines an."
            )
            self._refresh_combos()
            return
        for zone, panel in self.panels.items():
            rows = ydb.deck_cards(self.repo.db_path, self.deck_id, zone)
            total = panel.populate(rows)
            panel.setTitle(f"{ZONE_LABELS[zone]}  ({total})")
        checks = ydb.validate_deck(self.repo.db_path, self.deck_id)
        parts = [("\u2713 " if ok else "\u2717 ") + txt for ok, txt in checks]
        self.status.setText("     ".join(parts))
        self._refresh_combos()

    # -- Kombo-Hilfe --------------------------------------------------------

    def _refresh_combos(self) -> None:
        self.combo_list.clear()
        self.combo_pieces.clear()
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
        if current is None or self.deck_id is None:
            return
        combo_id = current.data(Qt.ItemDataRole.UserRole)
        cov = ydb.combo_coverage(self.repo.db_path, combo_id, self.deck_id)
        for p in cov["pieces"]:
            mark = "\u2713" if p["missing"] == 0 else "\u2717"
            self.combo_pieces.addItem(
                f"{mark}  {p['name']}   {p['have']}/{p['needed']}"
            )

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

class ComboView(QWidget):
    def __init__(self, repo: CardRepository):
        super().__init__()
        self.repo = repo
        self.combo_id: int | None = None

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Links: Liste + Neu/Loeschen
        left = QWidget()
        lv = QVBoxLayout(left)
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
        form.addRow("Name", self.name_edit)
        form.addRow("Archetyp", self.arch_edit)
        rv.addLayout(form)

        rv.addWidget(QLabel("Bausteine:"))
        self.pieces = QListWidget()
        rv.addWidget(self.pieces, stretch=1)
        piece_row = QHBoxLayout()
        minus = QPushButton("\u22121")
        plus = QPushButton("+1")
        rem = QPushButton("Entfernen")
        minus.clicked.connect(lambda: self._adjust_piece(-1))
        plus.clicked.connect(lambda: self._adjust_piece(+1))
        rem.clicked.connect(self._remove_piece)
        piece_row.addWidget(minus)
        piece_row.addWidget(plus)
        piece_row.addWidget(rem)
        piece_row.addStretch()
        rv.addLayout(piece_row)
        rv.addWidget(QLabel(
            "Karten über den Tab 'Suche' mit '+ als Baustein' hinzufügen."
        ))

        # Abgleich der Bausteine mit der eigenen Sammlung.
        coll_box = QGroupBox("Mit deiner Sammlung")
        cb_l = QVBoxLayout(coll_box)
        self.coll_status = QLabel("")
        self.coll_status.setWordWrap(True)
        cb_l.addWidget(self.coll_status)
        self.coll_missing = QListWidget()
        cb_l.addWidget(self.coll_missing)
        rv.addWidget(coll_box, stretch=1)

        rv.addWidget(QLabel("Schritte (eine Zeile pro Schritt):"))
        self.steps_edit = QTextEdit()
        rv.addWidget(self.steps_edit, stretch=1)
        save_btn = QPushButton("Name, Archetyp und Schritte speichern")
        save_btn.clicked.connect(self._save)
        rv.addWidget(save_btn)

        splitter.addWidget(left)
        splitter.addWidget(self.editor)
        splitter.setSizes([260, 640])
        outer = QVBoxLayout(self)
        outer.addWidget(splitter)

        self.editor.setEnabled(False)
        self.refresh()

    # -- Liste / Auswahl ----------------------------------------------------

    def refresh(self) -> None:
        keep = self.combo_id
        self.combo_list.blockSignals(True)
        self.combo_list.clear()
        combos = ydb.list_combos(self.repo.db_path) if self.repo.exists() else []
        for c in combos:
            item = QListWidgetItem(self._combo_label(c))
            item.setData(Qt.ItemDataRole.UserRole, c["combo_id"])
            self.combo_list.addItem(item)
        self.combo_list.blockSignals(False)
        if keep is not None and self._select_combo(keep):
            return
        if not self.combo_list.count():
            self._clear_editor()
            self.editor.setEnabled(False)

    def _combo_label(self, combo) -> str:
        """Listentext einer Kombo inkl. Baubarkeit aus der Sammlung
        (✓ = vollständig baubar, sonst 'vorhanden/gesamt')."""
        arch = f"   [{combo['archetype']}]" if combo["archetype"] else ""
        cov = ydb.combo_coverage_collection(self.repo.db_path, combo["combo_id"])
        if cov["total"] == 0:
            mark = ""
        elif cov["covered"] == cov["total"]:
            mark = "   ✓"
        else:
            mark = f"   ({cov['covered']}/{cov['total']})"
        return f"{combo['name']}{arch}{mark}"

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
        if current is None:
            self.combo_id = None
            self._clear_editor()
            self.editor.setEnabled(False)
            return
        self.combo_id = current.data(Qt.ItemDataRole.UserRole)
        combo = ydb.get_combo(self.repo.db_path, self.combo_id)
        self.name_edit.setText(combo["name"] or "")
        self.arch_edit.setText(combo["archetype"] or "")
        self._load_pieces()
        steps = ydb.combo_steps(self.repo.db_path, self.combo_id)
        self.steps_edit.setPlainText("\n".join(s["text"] for s in steps))
        self._refresh_collection_coverage()
        self.editor.setEnabled(True)

    def _clear_editor(self) -> None:
        self.name_edit.clear()
        self.arch_edit.clear()
        self.pieces.clear()
        self.steps_edit.clear()
        self.coll_status.clear()
        self.coll_missing.clear()

    def _after_piece_change(self) -> None:
        """Nach Änderung der Bausteine: Liste, Sammlungs-Abgleich und den
        Baubarkeit-Marker der aktiven Kombo aktualisieren."""
        self._load_pieces()
        self._refresh_collection_coverage()
        self._update_current_label()

    def _load_pieces(self) -> None:
        self.pieces.clear()
        if self.combo_id is None:
            return
        for p in ydb.combo_cards(self.repo.db_path, self.combo_id):
            item = QListWidgetItem(f"{p['quantity']}x  {p['name']}")
            item.setData(Qt.ItemDataRole.UserRole, p["card_id"])
            self.pieces.addItem(item)

    # -- Aktionen -----------------------------------------------------------

    def _new_combo(self) -> None:
        if not self.repo.exists():
            return
        name, ok = QInputDialog.getText(self, "Neue Kombo", "Name der Kombo:")
        if ok and name.strip():
            combo_id = ydb.create_combo(self.repo.db_path, name.strip())
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
            ydb.delete_combo(self.repo.db_path, self.combo_id)
            self.combo_id = None
            self.refresh()

    def _save(self) -> None:
        if self.combo_id is None:
            return
        ydb.update_combo(
            self.repo.db_path, self.combo_id,
            name=self.name_edit.text().strip() or "Unbenannt",
            archetype=self.arch_edit.text().strip() or None,
        )
        lines = [
            ln.strip() for ln in self.steps_edit.toPlainText().splitlines()
            if ln.strip()
        ]
        ydb.set_combo_steps(self.repo.db_path, self.combo_id, lines)
        self.refresh()
        self._select_combo(self.combo_id)

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

        self.tabs = QTabWidget()
        self.tabs.addTab(splitter, "Suche")
        self.tabs.addTab(self.collection_view, "Sammlung")
        self.tabs.addTab(self.deck_view, "Deck")
        self.tabs.addTab(self.combo_view, "Kombos")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        if self.repo.exists():
            self._populate_filters()
        else:
            QMessageBox.information(
                self, "Datenbank fehlt",
                "Keine Datenbank gefunden.\n\n"
                "Bitte zuerst anlegen:\n    python yugioh_db.py build",
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
        conn = ydb._connect(self.repo.db_path)
        try:
            card = conn.execute(
                "SELECT * FROM cards WHERE id = ?", (card_id,)
            ).fetchone()
        finally:
            conn.close()
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


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
