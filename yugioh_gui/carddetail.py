"""Karten-Detailansichten und -Suche (von mehreren Tabs geteilt).

DetailPanel (Suche-Tab, wirkt per Callbacks auf Deck/Kombo),
CardDetailDialog (read-only Pop-up in Sammlung/Deck), CardSearchDialog
(Karte nachschlagen, z.B. '+ Baustein' im Kombo-Editor), der
gemeinsame Uebersetzungs-Editor edit_card_translation ('DE'-Knopf) sowie
die Regel-Hilfen je Karte: WordingDialog (Wortlaut-Lesehilfe mit Spruengen
ins Regelwerk) und CardRulingsDialog (eigene Rulings + offizielle Links).
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox,
    QTextBrowser, QTextEdit, QVBoxLayout, QWidget
)

import yugioh_db as ydb

from . import navigation
from .images import CardImageView
from .labels import ATTR_DE, RACE_DE, TYPE_DE
from .repository import CardRepository
from .rulebook_text import section_title
from .theme import WORDING_COLORS


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

def _format_card_stats(card) -> str:
    """Karten-Stat-Zeile mit deutscher Beschriftung (Typ • Sorte • Attribut •
    Stufe • ATK/DEF • Archetyp). Geteilt von DetailPanel und CardDetailDialog."""
    parts = []
    if card["type"]:
        parts.append(TYPE_DE.get(card["type"], card["type"]))
    if card["race"]:
        parts.append(RACE_DE.get(card["race"], card["race"]))
    if card["attribute"]:
        parts.append(ATTR_DE.get(card["attribute"], card["attribute"]))
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
    return "  •  ".join(parts)


def edit_card_translation(repo: "CardRepository", card_id: int, parent) -> bool:
    """Eigene DE-Uebersetzung erfassen/aendern (Override; ueberlebt
    Karten-Updates). Leere Felder = kein Override fuer dieses Feld. Rueckgabe:
    True, wenn gespeichert wurde. Geteilt von DetailPanel und CardDetailDialog;
    der Aufrufer aktualisiert danach seine Anzeige."""
    card = repo.get_card(card_id)
    if card is None:
        return False
    dlg = QDialog(parent)
    dlg.setWindowTitle("Deutsche Übersetzung bearbeiten")
    dlg.resize(420, 360)
    v = QVBoxLayout(dlg)
    v.addWidget(QLabel(f"Karte: {card['name']}"))
    # Nur eigene Overrides vorbelegen; der aktuelle Wert steht grau als
    # Platzhalter -- sonst wuerde ein unveraenderter API-Text beim Speichern
    # still zum Override und kuenftige API-Korrekturen kaemen nie mehr an.
    own = ydb.get_card_translation(repo.db_path, card_id)
    form = QFormLayout()
    name_edit = QLineEdit((own["name_de"] if own else None) or "")
    name_edit.setPlaceholderText(card["name_de"] or card["name"])
    form.addRow("Name (DE)", name_edit)
    v.addLayout(form)
    v.addWidget(QLabel("Kartentext (DE):"))
    desc_edit = QTextEdit()
    desc_edit.setPlainText((own["desc_de"] if own else None) or "")
    desc_edit.setPlaceholderText(card["desc_de"] or card["description"] or "")
    v.addWidget(desc_edit, stretch=1)
    hint = QLabel(
        "Leere Felder = keine eigene Übersetzung (grau: aktueller Wert). "
        "Eine entfernte eigene Übersetzung fällt bis zum nächsten "
        "Daten-Update auf Englisch zurück."
    )
    hint.setWordWrap(True)
    hint.setObjectName("HintLabel")
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
        return False
    ydb.set_card_translation(
        repo.db_path, card_id,
        name_de=name_edit.text(), desc_de=desc_edit.toPlainText(),
    )
    return True


# ---------------------------------------------------------------------------
# Regel-Hilfen je Karte: Wortlaut-Lesehilfe und eigene Rulings
# ---------------------------------------------------------------------------

def wording_html(card) -> str:
    """Lesehilfe als HTML: je Segment Art, farbig markierter Kartentext
    (Bedingung/Kosten+Ziel/Aufloesung/Einschraenkung) und Erklaerungen mit
    Links 'rulebook:<kapitel>' ins Regelwerk; darunter der deutsche Text."""
    esc = html.escape
    segments = ydb.explain_card_text(card["description"], card["type"], card["race"])
    legend = " · ".join(
        f'<span style="color:{WORDING_COLORS[role]}">{label}</span>'
        for role, label in (("condition", "Bedingung"), ("cost", "Kosten/Ziel"),
                            ("effect", "Auflösung"), ("limit", "Einschränkung"))
    )
    out = [f"<p><i>{esc(ydb.WORDING_NOTE)}</i></p><p>{legend}</p>"]
    if not segments:
        out.append("<p>Kein Kartentext vorhanden.</p>")
    for seg in segments:
        scope = f" · {esc(seg.scope)}" if seg.scope else ""
        out.append(f"<h3>{esc(seg.kind_label)}{scope}</h3>")
        text = "".join(
            f'<span style="color:{WORDING_COLORS.get(p.role, WORDING_COLORS["plain"])}">'
            f"{esc(p.text).replace(chr(10), '<br>')}</span>"
            for p in seg.parts
        )
        out.append(f"<p>{text}</p>")
        if seg.tags:
            items = []
            for t in seg.tags:
                link = (f' — <a href="rulebook:{esc(t.topic)}">Regelwerk: '
                        f"{esc(section_title(t.topic))}</a>"
                        if section_title(t.topic) else "")
                items.append(f"<li><b>{esc(t.label)}</b>: {esc(t.explanation)}{link}</li>")
            out.append("<ul>" + "".join(items) + "</ul>")
    if card["desc_de"]:
        out.append("<h3>Deutscher Kartentext</h3><p>"
                   + esc(card["desc_de"]).replace("\n", "<br>") + "</p>")
    return "".join(out)


class WordingDialog(QDialog):
    """Wortlaut-Lesehilfe einer Karte. Ein Klick auf einen Regelwerk-Link
    springt (ueber navigation) in den Regelwerk-Tab und schliesst den Dialog."""

    def __init__(self, card, parent=None):
        super().__init__(parent)
        name = card["name_de"] or card["name"]
        self.setWindowTitle(f"Wortlaut — {name}")
        self.resize(640, 640)
        lay = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._on_link)
        self.browser.setHtml(wording_html(card))
        lay.addWidget(self.browser, stretch=1)
        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton("Schließen")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def _on_link(self, url: QUrl) -> None:
        if url.scheme() == "rulebook":
            if navigation.open_rulebook(url.path()):
                self.accept()
        else:
            QDesktopServices.openUrl(url)


class _RulingEditDialog(QDialog):
    """Ein Ruling erfassen/aendern: Text + Quelle (z. B. Konami-FAQ, Datum)."""

    def __init__(self, text: str = "", source: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ruling bearbeiten" if text else "Neues Ruling")
        self.resize(460, 320)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Ruling:"))
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(text)
        self.text_edit.setPlaceholderText(
            "z. B. „Kann auch auf Effekte in der Hand antworten“")
        lay.addWidget(self.text_edit, stretch=1)
        form = QFormLayout()
        self.source_edit = QLineEdit(source)
        self.source_edit.setPlaceholderText("Quelle, z. B. Konami-FAQ 2024-05")
        form.addRow("Quelle:", self.source_edit)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addStretch()
        ok = QPushButton("Speichern")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        cancel = QPushButton("Abbrechen")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)

    def values(self) -> tuple[str, str]:
        return self.text_edit.toPlainText().strip(), self.source_edit.text().strip()


class CardRulingsDialog(QDialog):
    """Eigene Rulings einer Karte pflegen (Benutzerdaten, ueberleben
    Updates) und offizielle Quellen im Browser oeffnen (kein Abruf durch die
    App). 'changed' ist True, sobald etwas gespeichert wurde."""

    def __init__(self, repo: "CardRepository", card, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.card_id = card["id"]
        self.changed = False
        self.setWindowTitle(f"Rulings — {card['name_de'] or card['name']}")
        self.resize(560, 440)
        lay = QVBoxLayout(self)
        self.listw = QListWidget()
        self.listw.setWordWrap(True)
        self.listw.itemDoubleClicked.connect(lambda _i: self._edit())
        lay.addWidget(self.listw, stretch=1)
        row = QHBoxLayout()
        for text, slot in (("+ Ruling", self._add), ("Bearbeiten", self._edit),
                           ("Löschen", self._delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        links = QHBoxLayout()
        links.addWidget(QLabel("Offizielle Rulings:"))
        for label, url in ydb.ruling_links(card["name"], card["name_de"]):
            b = QPushButton(label.replace("Konami-Datenbank", "Konami")
                            .replace(" (Rulings-Sammlung)", ""))
            b.setToolTip(f"{label}\n{url}")
            b.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            links.addWidget(b)
        links.addStretch()
        lay.addLayout(links)
        hint = QLabel("Die App lädt keine Rulings aus dem Netz — trage hier ein, "
                      "was du nachgelesen hast (mit Quelle).")
        hint.setWordWrap(True)
        hint.setObjectName("HintLabel")
        lay.addWidget(hint)
        close = QPushButton("Schließen")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self._reload()

    def _reload(self) -> None:
        self.listw.clear()
        for r in ydb.list_card_rulings(self.repo.db_path, self.card_id):
            meta = " · ".join(x for x in (r["source"], r["created"]) if x)
            item = QListWidgetItem(r["text"] + (f"\n— {meta}" if meta else ""))
            item.setData(Qt.ItemDataRole.UserRole, (r["ruling_id"], r["text"], r["source"]))
            self.listw.addItem(item)
        if self.listw.count():
            self.listw.setCurrentRow(0)

    def _selected(self):
        item = self.listw.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _add(self) -> None:
        dlg = _RulingEditDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            text, source = dlg.values()
            if text:
                ydb.add_card_ruling(self.repo.db_path, self.card_id, text, source)
                self.changed = True
                self._reload()
        dlg.deleteLater()

    def _edit(self) -> None:
        sel = self._selected()
        if sel is None:
            return
        rid, text, source = sel
        dlg = _RulingEditDialog(text, source or "", self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_text, new_source = dlg.values()
            if new_text:
                ydb.update_card_ruling(self.repo.db_path, rid, new_text, new_source)
                self.changed = True
                self._reload()
        dlg.deleteLater()

    def _delete(self) -> None:
        sel = self._selected()
        if sel is None:
            return
        answer = QMessageBox.question(self, "Ruling löschen?",
                                      "Dieses Ruling wirklich löschen?")
        if answer == QMessageBox.StandardButton.Yes:
            ydb.delete_card_ruling(self.repo.db_path, sel[0])
            self.changed = True
            self._reload()


class _CardHelpMixin:
    """Knoepfe 'Wortlaut ?' und 'Rulings (n)' fuer DetailPanel und
    CardDetailDialog (beide haben repo und current_id). Nach Aenderungen an
    Rulings wird 'rulings_changed' gemeldet, falls der Host es anbietet."""

    def _init_help_buttons(self, row: QHBoxLayout) -> None:
        """Knoepfe in die Namenszeile setzen (keine eigene Zeile -- die
        Detailspalte bestimmt die Mindesthoehe des Hauptfensters mit)."""
        self.wording_btn = QPushButton("Wortlaut ?")
        self.wording_btn.setToolTip(
            "Kartentext erklären: Bedingung, Kosten, Ziel, Effektart, OPT")
        self.wording_btn.clicked.connect(self._open_wording)
        self.rulings_btn = QPushButton("Rulings")
        self.rulings_btn.setToolTip("Eigene Rulings zur Karte und offizielle Quellen")
        self.rulings_btn.clicked.connect(self._open_rulings)
        for b in (self.wording_btn, self.rulings_btn):
            row.addWidget(b, alignment=Qt.AlignmentFlag.AlignTop)

    def _update_rulings_btn(self) -> None:
        n = (ydb.card_ruling_counts(self.repo.db_path, [self.current_id])
             .get(self.current_id, 0) if self.current_id is not None else 0)
        self.rulings_btn.setText(f"Rulings ({n})" if n else "Rulings")

    def _open_wording(self) -> None:
        card = self.repo.get_card(self.current_id) if self.current_id is not None else None
        if card is None:
            return
        dlg = WordingDialog(card, self)
        dlg.exec()
        dlg.deleteLater()

    def _open_rulings(self) -> None:
        card = self.repo.get_card(self.current_id) if self.current_id is not None else None
        if card is None:
            return
        dlg = CardRulingsDialog(self.repo, card, self)
        dlg.exec()
        changed = dlg.changed
        dlg.deleteLater()
        if changed:
            self._update_rulings_btn()
            signal = getattr(self, "rulings_changed", None)
            if signal is not None:
                signal.emit()


class DetailPanel(_CardHelpMixin, CardImageView, QWidget):
    rulings_changed = Signal()

    def __init__(self, repo: CardRepository):
        super().__init__()
        self.repo = repo
        self.current_id: int | None = None
        self._init_card_image()

        layout = QVBoxLayout(self)

        self.image = QLabel("Keine Karte ausgewählt")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(220, 320)
        self.image.setObjectName("CardImage")

        self.name = QLabel("")
        self.name.setObjectName("CardTitle")
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
        self._init_help_buttons(name_row)
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
        self.wording_btn.setEnabled(on)
        self.rulings_btn.setEnabled(on)

    def _edit_translation(self) -> None:
        if self.current_id is None:
            return
        if edit_card_translation(self.repo, self.current_id, self):
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
        self.stats.setText(_format_card_stats(card))
        self.text.setPlainText(card["desc_de"] or card["description"] or "")

        self._load_image(card["id"])
        self._update_owned(card["id"])
        self._update_rulings_btn()
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

    def _add_to_collection(self) -> None:
        if self.current_id is None:
            return
        ydb.add_to_collection(self.repo.db_path, self.current_id, self.qty.value())
        self._update_owned(self.current_id)


class CardDetailDialog(_CardHelpMixin, CardImageView, QDialog):
    """Read-only Detail-Pop-up einer Karte (Bild, deutsche Infos, Kartentext)
    -- geoeffnet per Doppelklick im Sammlung-Tab. Bietet zusaetzlich den
    „✎ DE"-Button zum Uebersetzung-Bearbeiten; nach dem Speichern meldet
    'translation_changed', damit der aufrufende Tab seine Liste aktualisiert.
    Eine Instanz wird je View wiederverwendet (load() zeigt die naechste
    Karte). Bild und Cache wie DetailPanel, nur in groesserer Darstellung."""

    translation_changed = Signal()
    rulings_changed = Signal()
    IMG_W, IMG_H = 300, 438
    _CACHE_PREFIX = "detail"

    def __init__(self, repo: "CardRepository", parent=None):
        super().__init__(parent)
        self.repo = repo
        self.current_id: int | None = None
        self.resize(360, 660)
        self._init_card_image()

        layout = QVBoxLayout(self)

        self.image = QLabel("")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(self.IMG_W, self.IMG_H)
        self.image.setObjectName("CardImage")

        self.name = QLabel("")
        self.name.setObjectName("CardTitle")
        self.name.setWordWrap(True)
        self.edit_trans_btn = QPushButton("✎ DE")
        self.edit_trans_btn.setToolTip("Deutsche Übersetzung bearbeiten")
        self.edit_trans_btn.setFixedWidth(48)
        self.edit_trans_btn.clicked.connect(self._edit_translation)

        self.stats = QLabel("")
        self.stats.setWordWrap(True)

        self.text = QTextEdit()
        self.text.setReadOnly(True)

        close_btn = QPushButton("Schließen")
        close_btn.clicked.connect(self.close)

        layout.addWidget(self.image)
        name_row = QHBoxLayout()
        name_row.addWidget(self.name, stretch=1)
        name_row.addWidget(
            self.edit_trans_btn, alignment=Qt.AlignmentFlag.AlignTop
        )
        self._init_help_buttons(name_row)
        layout.addLayout(name_row)
        layout.addWidget(self.stats)
        layout.addWidget(self.text, stretch=1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def load(self, card_id: int) -> None:
        """Karte anzeigen (auch beim Wiederverwenden der Instanz)."""
        card = self.repo.get_card(card_id)
        if card is None:
            return
        self.current_id = card_id
        title = card["name_de"] or card["name"]
        self.setWindowTitle(title)
        self.name.setText(title)
        self.stats.setText(_format_card_stats(card))
        self.text.setPlainText(card["desc_de"] or card["description"] or "")
        self._update_rulings_btn()
        self._load_image(card_id)

    def _edit_translation(self) -> None:
        if self.current_id is None:
            return
        if edit_card_translation(self.repo, self.current_id, self):
            self.load(self.current_id)
            self.translation_changed.emit()
