"""Kombos-Tab: Bibliothek, Editor und Schritt-Durchspielen.

ComboView (Liste + Filter links, Editor mit Debounce-Speichern rechts,
Notation-Lint beratend) und ComboPlaybackDialog (Schritte einzeln
durchgehen). Die eingebettete Notation-Kurzreferenz (NOTATION_MD)
liegt hier, damit sie auch im gepackten Build verfuegbar ist.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter, QTextEdit,
    QVBoxLayout, QWidget
)

import yugioh_db as ydb

from .carddetail import CardSearchDialog
from .labels import PIECE_ROLE_DATA, ROLE_DE
from .repository import CardRepository


# ---------------------------------------------------------------------------
# Kombo-Bibliothek (eigener Tab zum Anlegen/Bearbeiten)
# ---------------------------------------------------------------------------

# In-App-Kurzreferenz der Kombo-Notation (Langform: KOMBO-NOTATION.md).
# Eingebettet statt aus der Datei gelesen, damit sie auch im gepackten
# Build ohne Repo-Dateien verfuegbar ist.
NOTATION_MD = """\
### Schritt-Syntax

```
<AKTION> <Karte> (<Quelle>) [Req: <Bedingung>] -> <Folge> -> <Folge> | Lock: <Einschränkung>
```

Nur `<AKTION> <Karte>` ist Pflicht. **Ein Schritt = eine Aktion** — eine
Beschwörung *oder* eine Effekt-Aktivierung samt direkter Auflösung.
Beschwörungsformeln bekommen immer eine eigene Zeile (Tuner zuerst):

```
Synchro: Soul (2) + Bone (4) -> Red Rising (6)
Xyz: A (4) + B (4) -> Ziel (R4)      Link: A + B -> Ziel (L2)
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
| Synchro: / Xyz: / Link: / Fusion: | Beschwörungsformel (eigene Zeile) |
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
# (NOTATION_MD) -- hier nur die haeufigsten Bausteine kompakt in einer Reihe.
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
            self.role_cb.addItem(ROLE_DE[r], r)
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
        combos, coverage, variants = [], {}, {}
        if self.repo.exists():
            db = self.repo.db_path
            combos = ydb.list_combos(
                db, self.deck_filter_cb.currentData(),
                text=self.search_edit.text(),
            )
            # Gebuendelt statt je Kombo (frueher 2 Verbindungen pro Zeile).
            coverage = {c["combo_id"]: c for c in ydb.combos_for_collection(db)}
            variants = ydb.combo_variants_by_parent(db)
        for c in combos:
            item = QListWidgetItem(
                self._combo_label(c, coverage.get(c["combo_id"]))
            )
            item.setData(Qt.ItemDataRole.UserRole, c["combo_id"])
            self.combo_list.addItem(item)
            # Varianten (Branches) eingerückt direkt unter der Hauptlinie.
            for v in variants.get(c["combo_id"], []):
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

    def _combo_label(self, combo, cov=None) -> str:
        """Listentext einer Kombo inkl. Heimat-Deck und Baubarkeit aus der
        Sammlung (✓ = vollständig baubar, sonst 'vorhanden/gesamt'). 'cov'
        (covered/total) kann vorab gebündelt geliefert werden."""
        arch = f"   [{combo['archetype']}]" if combo["archetype"] else ""
        deck = f"   · {combo['deck_name']}" if combo["deck_name"] else ""
        if cov is None:
            cov = ydb.combo_coverage_collection(
                self.repo.db_path, combo["combo_id"]
            )
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
        if combo is not None:
            item.setText(self._list_label(combo))

    def _list_label(self, combo) -> str:
        """Listentext passend zur Stufe: Variante eingerückt, sonst Hauptlinie."""
        if combo["parent_combo_id"] is not None:
            return self._variant_label(combo)
        return self._combo_label(combo)

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
        role = f"   [{ROLE_DE[p['role']]}]" if p["role"] else ""
        return f"{p['quantity']}x  {p['name']}{role}"

    def _load_pieces(self) -> None:
        self.pieces.clear()
        if self.combo_id is None:
            return
        pieces = ydb.combo_cards(self.repo.db_path, self.combo_id)
        for p in pieces:
            item = QListWidgetItem(self._piece_label(p))
            item.setData(Qt.ItemDataRole.UserRole, p["card_id"])
            item.setData(PIECE_ROLE_DATA, p["role"])
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
            idx = self.role_cb.findData(current.data(PIECE_ROLE_DATA))
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
        item.setData(PIECE_ROLE_DATA, role)
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
                    item.setText(self._list_label(combo))
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
        text.setMarkdown(NOTATION_MD)
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
