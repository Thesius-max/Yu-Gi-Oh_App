"""Text-/Markdown-/CSV-Exporte fuer Sammlung, Decks und Kombo-Linien.

Die Datenschicht erzeugt nur den fertigen Text; die GUI schreibt ihn als
.txt/.md (woertlich), .csv (mit BOM fuer Excel) oder .pdf (Monospace-Satz).
Grundsatz (Benutzer-Vorgabe 2026-10-06): Die **Sammlung** wird immer ohne
Effekttexte exportiert (Bestandsliste), ein **einzelnes Deck** immer mit
allem -- Typzeile, Effekttext, Kombo-Rolle, eigene Rulings und (ausser in
der CSV-Tabelle) Konsistenz und Kombo-Linien.
"""

from __future__ import annotations

import csv
import datetime
import io
import re
import sqlite3
from typing import Optional

from .analysis import deck_consistency
from .cards import card_category
from .collection import list_collection_cards, prints_summary
from .combos import (
    COMBO_ROLES, ROLE_LABEL, combo_cards, combo_coverage, combo_steps,
    combo_variants, combos_for_deck, deck_role_summary, get_combo,
)
from .decks import deck_cards, shopping_list
from .rulings import list_card_rulings
from .schema import _conn


def export_deck_combos_text(db_path: str, deck_id: int) -> str:
    """Kombo-Linien eines Decks als lesbarer Text -- zum Weitergeben,
    z.B. wenn ein erfahrener Spieler ueber Deck und Linien schauen soll.
    Enthaelt Notation-Legende, Konsistenz, Rollen-Uebersicht und alle
    Kombos, die mindestens einen Baustein im Deck haben oder dieses Deck
    als Heimat-Deck fuehren (Reihenfolge wie im Deck-Tab: nach Abdeckung)."""
    with _conn(db_path) as conn:
        deck = conn.execute(
            "SELECT name FROM decks WHERE deck_id = ?", (deck_id,)
        ).fetchone()
    if deck is None:
        raise ValueError(f"Deck {deck_id} existiert nicht.")

    def pct(p: float) -> str:
        return f"{100 * p:.1f}".replace(".", ",") + " %"

    lines = [
        f"Kombo-Linien — {deck['name']}",
        f"Stand: {datetime.date.today().strftime('%d.%m.%Y')}",
        "",
        "Notation:  NS/SS = Normal/Special Summon · Act = Zauber/Falle"
        " aktivieren ·",
        "Eff1/Eff2 = (ersten/zweiten) Effekt aktivieren · Add = auf die"
        " Hand (Suche) ·",
        "GY = Friedhof · ED = Extra Deck · '->' verkettet Kosten/Wirkung/"
        "Resultat ·",
        "'| Lock: …' = Einschränkung · '[Req: …]' = Bedingung",
    ]

    stats = deck_consistency(db_path, deck_id)
    if stats["deck_size"] and stats["roles"].get("starter"):
        lines += ["", "== Konsistenz =="]
        lines.append("Kopien im Main: " + " · ".join(
            f"{ROLE_LABEL[r]} {stats['roles'][r]}"
            for r in COMBO_ROLES if stats["roles"].get(r)
        ))
        for hand, p in stats["hands"].items():
            ln = f"Starthand {hand}: ≥1 Starter {pct(p['starter'])}"
            if stats["roles"].get("handtrap"):
                ln += f" · ≥1 Handtrap {pct(p['handtrap'])}"
            lines.append(ln + f" · Brick {pct(p['brick'])}")

    summary = deck_role_summary(db_path, deck_id)
    if summary:
        lines += ["", "== Rollen im Deck (Main + Extra) =="]
        for role in COMBO_ROLES:
            cards = summary.get(role)
            if not cards:
                continue
            total = sum(c["copies"] for c in cards)
            lines.append(f"{ROLE_LABEL[role]} ({total}):")
            lines += [f"  {c['copies']}x {c['name']}" for c in cards]

    lines += ["", "== Kombo-Linien (nach Abdeckung im Deck) =="]
    count = 0
    for cb in combos_for_deck(db_path, deck_id):
        combo = get_combo(db_path, cb["combo_id"])
        if cb["present"] == 0 and combo["deck_id"] != deck_id:
            continue  # gehoert erkennbar nicht zu diesem Deck
        count += 1
        head = f"{count}) {combo['name']}"
        if combo["archetype"]:
            head += f"  [{combo['archetype']}]"
        lines += ["", head]
        info = []
        if combo["boss_name"]:
            info.append(f"Boss: {combo['boss_name']}")
        info.append(f"Abdeckung: {cb['covered']}/{cb['total']} Bausteine im Deck")
        lines.append("   " + " · ".join(info))
        if combo["notes"]:
            lines += [
                f"   {ln.strip()}"
                for ln in combo["notes"].splitlines() if ln.strip()
            ]
        cov = combo_coverage(db_path, cb["combo_id"], deck_id)
        roles = {
            p["card_id"]: p["role"] for p in combo_cards(db_path, cb["combo_id"])
        }
        if cov["pieces"]:
            lines.append("   Bausteine:")
        for p in cov["pieces"]:
            role = roles.get(p["card_id"])
            tag = f"  [{ROLE_LABEL[role]}]" if role else ""
            if p["missing"] == 0:
                gap = ""
            elif p["have"] == 0:
                gap = "  (fehlt im Deck)"
            else:
                gap = f"  (nur {p['have']}/{p['needed']} im Deck)"
            lines.append(f"     {p['needed']}x {p['name']}{tag}{gap}")
        steps = combo_steps(db_path, cb["combo_id"])
        if steps:
            lines.append("   Schritte:")
            lines += [f"     {s['step_no']}. {s['text']}" for s in steps]
        # Varianten (Interruption-Branches) unter der Hauptlinie auffuehren.
        for var in combo_variants(db_path, cb["combo_id"]):
            lines += ["", f"   ↳ Variante: {var['name']}"]
            vcombo = get_combo(db_path, var["combo_id"])
            if vcombo["notes"]:
                lines += [
                    f"     {ln.strip()}"
                    for ln in vcombo["notes"].splitlines() if ln.strip()
                ]
            vsteps = combo_steps(db_path, var["combo_id"])
            lines += [f"     {s['step_no']}. {s['text']}" for s in vsteps]
    if count == 0:
        lines.append("(Keine Kombos mit Bausteinen aus diesem Deck erfasst.)")
    return "\n".join(lines) + "\n"


# -- Sammlungs-/Deck-Export (Text, PDF-Quelle, KI-Markdown) -----------------
#
_EXPORT_CATEGORY_DE = {
    "monster": "Monster", "spell": "Zauber",
    "trap": "Falle", "other": "Sonstige",
}
_EXPORT_CATEGORY_ORDER = ("monster", "spell", "trap", "other")


def _export_filter_note(
    text: Optional[str], category: Optional[str],
    attribute: Optional[str], archetype: Optional[str],
    untranslated_only: bool = False,
) -> str:
    """Kurzbeschreibung der aktiven Sammlungs-Filter (leer = keine)."""
    parts = []
    if text and text.strip():
        parts.append(f"Name~„{text.strip()}“")
    if category:
        parts.append("Klasse=" + _EXPORT_CATEGORY_DE.get(category, category))
    if attribute:
        parts.append(f"Attribut={attribute}")
    if archetype:
        parts.append(f"Archetyp={archetype}")
    if untranslated_only:
        parts.append("nur unübersetzte")
    return " · ".join(parts)


def _card_details(db_path: str, card_ids: list[int]) -> dict[int, sqlite3.Row]:
    """Vollstaendige Anzeige-/Effektdaten je Karten-id (DE bevorzugt).
    Liefert ein Dict id -> Row; fehlende ids fehlen schlicht im Dict."""
    if not card_ids:
        return {}
    ph = ",".join("?" * len(card_ids))
    with _conn(db_path) as conn:
        rows = conn.execute(
            f"""SELECT id, COALESCE(name_de, name) AS name, name AS name_en,
                       type, frame_type, race, attribute, atk, def, level,
                       link_value, archetype,
                       COALESCE(desc_de, description) AS text
                FROM cards WHERE id IN ({ph})""",
            card_ids,
        ).fetchall()
    return {r["id"]: r for r in rows}


def _card_meta_line(d: sqlite3.Row) -> str:
    """Kompakte Typzeile fuer den KI-Export (Monster mit ATK/DEF, sonst Art)."""
    parts: list[str] = [d["type"] or "?"]
    if card_category(d["type"]) == "monster":
        if d["attribute"]:
            parts.append(d["attribute"])
        if d["race"]:
            parts.append(d["race"])
        if d["link_value"] is not None:
            parts.append(f"LINK-{d['link_value']}")
            parts.append(f"ATK {d['atk'] if d['atk'] is not None else '?'}")
        else:
            if d["level"] is not None:
                parts.append(f"Stufe/Rang {d['level']}")
            atk = d["atk"] if d["atk"] is not None else "?"
            dfn = d["def"] if d["def"] is not None else "?"
            parts.append(f"ATK {atk} / DEF {dfn}")
    elif d["race"]:
        parts.append(d["race"])
    return " · ".join(str(p) for p in parts)


def _md_inline(text: str) -> str:
    """Kartennamen fuer Markdown-Ueberschriften entschaerfen: '<P>' (z.B.
    'Maliss <P> Dormouse') wuerde sonst als HTML-Tag verschluckt,
    Backticks oeffneten Code-Spans."""
    return (text.replace("\\", "\\\\").replace("`", "\\`")
            .replace("<", "\\<").replace(">", "\\>"))


def _md_fence(content: str) -> str:
    """Code-Fence, die laenger ist als jede Backtick-Folge im Inhalt --
    Benutzer-Schritte/-Notizen mit ``` sprengen den Block so nicht."""
    longest = max((len(m) for m in re.findall(r"`+", content)), default=0)
    return "`" * max(3, longest + 1)


def _card_md_block(
    d: Optional[sqlite3.Row], lead: str, roles: Optional[list[str]] = None,
    rulings: Optional[list[sqlite3.Row]] = None, with_text: bool = True,
) -> list[str]:
    """Markdown-Block einer Karte: Ueberschrift (lead = z.B. '3x '), Typzeile,
    optional Archetyp/Rolle, der volle Effekttext (eingerueckt) und eigene
    Rulings. with_text=False (Sammlung): nur Typzeile/Archetyp."""
    if d is None:
        return [f"### {lead}(unbekannte Karte)", ""]
    block = [f"### {lead}{_md_inline(d['name'])}", f"- Typ: {_card_meta_line(d)}"]
    if d["archetype"]:
        block.append(f"- Archetyp: {d['archetype']}")
    if roles:
        block.append("- Rolle: " + ", ".join(ROLE_LABEL.get(r, r) for r in roles))
    if not with_text:
        block.append("")
        return block
    txt = (d["text"] or "").strip()
    if txt:
        indented = txt.replace("\r\n", "\n").replace("\r", "\n").replace(
            "\n", "\n  "
        )
        block.append(f"- Effekt: {indented}")
    else:
        block.append("- Effekt: (kein Kartentext vorhanden)")
    for r in rulings or ():
        src = f" (Quelle: {_md_inline(r['source'])})" if r["source"] else ""
        text = r["text"].replace("\r\n", "\n").replace("\n", "\n  ")
        block.append(f"- Ruling: {text}{src}")
    block.append("")
    return block


def _indent(text: str, prefix: str) -> str:
    """Mehrzeiligen Text einruecken (Folgezeilen mit 'prefix')."""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\n" + prefix)


def _card_text_block(
    d: Optional[sqlite3.Row], lead: str, roles: Optional[list[str]] = None,
    rulings: Optional[list[sqlite3.Row]] = None,
) -> list[str]:
    """Text-Pendant zu _card_md_block fuer den vollstaendigen Deck-Export
    (.txt/.pdf): Name, Typzeile, Archetyp, Rolle, Effekttext, Rulings."""
    if d is None:
        return [f"{lead}(unbekannte Karte)", ""]
    block = [f"{lead}{d['name']}", f"    Typ: {_card_meta_line(d)}"]
    if d["archetype"]:
        block.append(f"    Archetyp: {d['archetype']}")
    if roles:
        block.append("    Rolle: " + ", ".join(ROLE_LABEL.get(r, r) for r in roles))
    txt = (d["text"] or "").strip()
    block.append("    Effekt: " + (_indent(txt, "      ") if txt
                                     else "(kein Kartentext vorhanden)"))
    for r in rulings or ():
        src = f" (Quelle: {r['source']})" if r["source"] else ""
        block.append(f"    Ruling: {_indent(r['text'], '      ')}{src}")
    block.append("")
    return block


# CSV fuer deutsches Excel: Semikolon, CRLF; das BOM setzt die GUI beim
# Schreiben (utf-8-sig). Zeilenumbrueche in Zellen quotet der csv-Writer.
_CARD_COLUMNS = (
    "Name", "Name (EN)", "Passcode", "Kartenklasse", "Typ", "Attribut",
    "Typ-Linie/Art", "Stufe/Rang", "Link-Wert", "ATK", "DEF", "Archetyp",
)


def _card_csv_cells(cid: int, d: Optional[sqlite3.Row]) -> list:
    if d is None:
        return ["(unbekannte Karte)", "", cid] + [""] * (len(_CARD_COLUMNS) - 3)
    is_monster = card_category(d["type"]) == "monster"

    def num(v):
        return "" if v is None else v
    return [
        d["name"], d["name_en"], cid,
        _EXPORT_CATEGORY_DE.get(card_category(d["type"]), ""),
        d["type"] or "", d["attribute"] or "", d["race"] or "",
        num(d["level"]) if is_monster and d["link_value"] is None else "",
        num(d["link_value"]),
        num(d["atk"]) if is_monster else "",
        num(d["def"]) if is_monster and d["link_value"] is None else "",
        d["archetype"] or "",
    ]


def _to_csv(header, rows) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n",
                        quoting=csv.QUOTE_MINIMAL)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def _card_notes(card: dict) -> str:
    """Notizen aller Drucke einer Karte, verbunden."""
    return "; ".join(p["notes"].strip() for p in card["prints"]
                     if p["notes"] and p["notes"].strip())


def export_collection_text(
    db_path: str, text: Optional[str] = None, category: Optional[str] = None,
    attribute: Optional[str] = None, archetype: Optional[str] = None,
    untranslated_only: bool = False,
) -> str:
    """Sammlung als lesbarer Text -- **eine Zeile je Karte** mit Gesamtmenge
    und Aufschluesselung nach Drucken, gruppiert nach Kartenklasse.
    Beruecksichtigt dieselben Filter wie die Sammlungs-Ansicht; ohne Filter
    ist es die gesamte Sammlung."""
    cards = list_collection_cards(
        db_path, text, category, attribute, archetype,
        untranslated_only=untranslated_only,
    )
    note = _export_filter_note(
        text, category, attribute, archetype, untranslated_only
    )
    total = sum(c["quantity"] for c in cards)
    n_prints = sum(len(c["prints"]) for c in cards)
    out = [
        "Sammlung" + (f" — {note}" if note else ""),
        f"Stand: {datetime.date.today().strftime('%d.%m.%Y')}",
        f"{len(cards)} verschiedene Karten · {total} Karten gesamt · "
        f"{n_prints} Druck(e)",
    ]
    by_cat: dict[str, list[dict]] = {}
    for c in cards:
        by_cat.setdefault(card_category(c["type"]), []).append(c)
    for cat in _EXPORT_CATEGORY_ORDER:
        group = by_cat.get(cat)
        if not group:
            continue
        cat_total = sum(c["quantity"] for c in group)
        out += ["", f"== {_EXPORT_CATEGORY_DE[cat]} ({cat_total}) =="]
        for c in group:
            name = c["name_de"] or c["name"]
            summary = prints_summary(c["prints"])
            tail = f"  [{summary}]" if summary else ""
            notes = _card_notes(c)
            note_t = f"  ({notes})" if notes else ""
            out.append(f"  {c['quantity']}x {name}{tail}{note_t}")
    return "\n".join(out) + "\n"


def export_collection_markdown(
    db_path: str, text: Optional[str] = None, category: Optional[str] = None,
    attribute: Optional[str] = None, archetype: Optional[str] = None,
    untranslated_only: bool = False,
) -> str:
    """Sammlung als Markdown-Bestandsliste: je Karte Gesamtmenge, Typzeile,
    Archetyp und Drucke -- bewusst **ohne Effekttext** (Effekttexte gibt es
    nur im Deck-Export). Gleiche Filter wie die Sammlungs-Ansicht."""
    cards = list_collection_cards(
        db_path, text, category, attribute, archetype,
        untranslated_only=untranslated_only,
    )
    details = _card_details(db_path, [c["card_id"] for c in cards])
    note = _export_filter_note(
        text, category, attribute, archetype, untranslated_only
    )
    lines = [
        "# Yu-Gi-Oh!-Sammlung" + (f" — {note}" if note else ""),
        "",
        f"Stand: {datetime.date.today().strftime('%d.%m.%Y')} · "
        f"{len(cards)} verschiedene Karten · "
        f"{sum(c['quantity'] for c in cards)} gesamt",
        "",
        "Bestandsliste: jede Karte mit besessener Menge, Typ, Archetyp und "
        "Drucken (ohne Effekttexte).",
    ]
    by_cat: dict[str, list[dict]] = {}
    for c in cards:
        d = details.get(c["card_id"])
        by_cat.setdefault(card_category(d["type"] if d else None), []).append(c)
    for cat in _EXPORT_CATEGORY_ORDER:
        group = by_cat.get(cat)
        if not group:
            continue
        lines += ["", f"## {_EXPORT_CATEGORY_DE[cat]} ({len(group)})", ""]
        for c in group:
            block = _card_md_block(details.get(c["card_id"]), f"{c['quantity']}x ",
                                   with_text=False)
            summary = prints_summary(c["prints"])
            if summary:
                block.insert(-1, f"- Drucke: {_md_inline(summary)}")
            lines += block
    return "\n".join(lines) + "\n"


def export_collection_csv(
    db_path: str, text: Optional[str] = None, category: Optional[str] = None,
    attribute: Optional[str] = None, archetype: Optional[str] = None,
    untranslated_only: bool = False,
) -> str:
    """Sammlung als CSV (Semikolon, fuer Excel): **eine Zeile je Karte**
    mit Gesamtmenge, Kartendaten, Drucken (Aufschluesselung) und Notizen --
    ohne Effekttext. Gleiche Filter wie die Sammlungs-Ansicht."""
    cards = list_collection_cards(
        db_path, text, category, attribute, archetype,
        untranslated_only=untranslated_only,
    )
    details = _card_details(db_path, [c["card_id"] for c in cards])
    header = ("Anzahl",) + _CARD_COLUMNS + ("Drucke", "Notizen")
    return _to_csv(header, [
        [c["quantity"]] + _card_csv_cells(c["card_id"], details.get(c["card_id"]))
        + [prints_summary(c["prints"]), _card_notes(c)]
        for c in cards
    ])


def _deck_name(db_path: str, deck_id: int) -> str:
    with _conn(db_path) as conn:
        deck = conn.execute(
            "SELECT name FROM decks WHERE deck_id = ?", (deck_id,)
        ).fetchone()
    if deck is None:
        raise ValueError(f"Deck {deck_id} nicht gefunden.")
    return deck["name"]


def _deck_role_map(db_path: str, deck_id: int) -> dict[int, list[str]]:
    """{card_id: [Rollen]} aus den Kombos (Reihenfolge wie COMBO_ROLES)."""
    role_map: dict[int, list[str]] = {}
    for role, cards in deck_role_summary(db_path, deck_id).items():
        for c in cards:
            role_map.setdefault(c["card_id"], []).append(role)
    return {cid: sorted(rs, key=COMBO_ROLES.index) for cid, rs in role_map.items()}


_DECK_ZONES = (("main", "Main Deck"), ("extra", "Extra Deck"), ("side", "Side Deck"))


def export_deck_text(db_path: str, deck_id: int) -> str:
    """Deck vollstaendig als lesbarer Text (.txt/.pdf): je Zone jede Karte
    mit Menge, Typzeile, Archetyp, Kombo-Rolle, Effekttext und eigenen
    Rulings, danach Konsistenz und Kombo-Linien."""
    out = [
        f"Deck: {_deck_name(db_path, deck_id)}",
        f"Stand: {datetime.date.today().strftime('%d.%m.%Y')}",
    ]
    role_map = _deck_role_map(db_path, deck_id)
    for zone, label in _DECK_ZONES:
        rows = deck_cards(db_path, deck_id, zone)
        if not rows:
            continue
        details = _card_details(db_path, [r["card_id"] for r in rows])
        total = sum(r["quantity"] for r in rows)
        out += ["", f"== {label} ({total}) ==", ""]
        for r in rows:
            out += _card_text_block(
                details.get(r["card_id"]), f"{r['quantity']}x ",
                role_map.get(r["card_id"]),
                list_card_rulings(db_path, r["card_id"]),
            )
    out += ["", "=" * 60, "Konsistenz & Kombo-Linien", "=" * 60, "",
            export_deck_combos_text(db_path, deck_id).rstrip("\n")]
    return "\n".join(out) + "\n"


def export_deck_csv(db_path: str, deck_id: int) -> str:
    """Deck vollstaendig als CSV (Semikolon, fuer Excel): eine Zeile je
    Karte und Zone mit Kartendaten, Kombo-Rolle, Effekttext und eigenen
    Rulings. Kombo-Linien passen in keine Tabelle (siehe .txt/.md)."""
    _deck_name(db_path, deck_id)                  # unbekanntes Deck -> Fehler
    role_map = _deck_role_map(db_path, deck_id)
    header = ("Zone", "Anzahl") + _CARD_COLUMNS + ("Rolle", "Effekttext", "Rulings")
    out_rows = []
    for zone, label in _DECK_ZONES:
        rows = deck_cards(db_path, deck_id, zone)
        details = _card_details(db_path, [r["card_id"] for r in rows])
        for r in rows:
            d = details.get(r["card_id"])
            rulings = "\n".join(
                x["text"] + (f" (Quelle: {x['source']})" if x["source"] else "")
                for x in list_card_rulings(db_path, r["card_id"])
            )
            out_rows.append(
                [label, r["quantity"]] + _card_csv_cells(r["card_id"], d)
                + [", ".join(ROLE_LABEL.get(x, x) for x in role_map.get(r["card_id"], [])),
                   (d["text"] or "").strip() if d else "", rulings]
            )
    return _to_csv(header, out_rows)


def export_deck_markdown(db_path: str, deck_id: int) -> str:
    """Deck als KI-tauglicher Markdown-Block: alle Karten je Zone mit vollem
    Effekttext und (sofern erfasst) Kombo-Rolle, gefolgt von Konsistenz und
    Kombo-Linien -- damit eine KI das Deck ohne Nachschlagen analysiert."""
    name = _deck_name(db_path, deck_id)
    role_map = _deck_role_map(db_path, deck_id)
    lines = [
        f"# Deck: {name}",
        "",
        f"Stand: {datetime.date.today().strftime('%d.%m.%Y')}",
    ]
    for zone, label in (
        ("main", "Main Deck"), ("extra", "Extra Deck"), ("side", "Side Deck"),
    ):
        rows = deck_cards(db_path, deck_id, zone)
        if not rows:
            continue
        details = _card_details(db_path, [r["card_id"] for r in rows])
        total = sum(r["quantity"] for r in rows)
        lines += ["", f"## {label} ({total})", ""]
        for r in rows:
            lines += _card_md_block(
                details.get(r["card_id"]), f"{r['quantity']}x ",
                role_map.get(r["card_id"]),
                list_card_rulings(db_path, r["card_id"]),
            )
    # Konsistenz + Kombo-Linien aus der bestehenden Funktion (eine Quelle der
    # Wahrheit); woertlich in einen Codeblock gesetzt, damit das Textlayout
    # (== Ueberschriften, '->'-Ketten) im Markdown erhalten bleibt.
    combo_text = export_deck_combos_text(db_path, deck_id).rstrip("\n")
    fence = _md_fence(combo_text)
    lines += ["", "## Konsistenz & Kombo-Linien", "", fence, combo_text, fence, ""]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Einkaufsliste (fehlende Karten ueber alle eigenen Decks)
# ---------------------------------------------------------------------------

def _shopping_summary(items: list[dict]) -> str:
    return (f"{len(items)} Karte(n) · {sum(i['missing'] for i in items)} "
            "fehlende Kopie(n) · Bedarf = alle eigenen Decks gleichzeitig")


def _decks_text(item: dict) -> str:
    return ", ".join(f"{name} {n}x" for name, n in item["decks"])


def export_shopping_list_text(db_path: str) -> str:
    """Einkaufsliste als lesbarer Text (.txt/.pdf): je Karte die Fehlmenge,
    Bedarf, Bestand und in welchen Decks sie steckt."""
    items = shopping_list(db_path)
    out = ["Einkaufsliste", f"Stand: {datetime.date.today().strftime('%d.%m.%Y')}",
           _shopping_summary(items), ""]
    if not items:
        out.append("Es fehlt nichts — alle eigenen Decks sind aus dem Bestand baubar.")
    for i in items:
        out.append(f"  {i['missing']}x {i['name']}   (benötigt {i['needed']}, "
                   f"im Bestand {i['owned']}; {_decks_text(i)})")
    return "\n".join(out) + "\n"


def export_shopping_list_markdown(db_path: str) -> str:
    """Einkaufsliste als Markdown-Tabelle (.md)."""
    items = shopping_list(db_path)
    lines = ["# Einkaufsliste", "",
             f"Stand: {datetime.date.today().strftime('%d.%m.%Y')} · "
             + _shopping_summary(items), ""]
    if not items:
        lines.append("Es fehlt nichts — alle eigenen Decks sind aus dem Bestand baubar.")
        return "\n".join(lines) + "\n"
    lines += ["| Fehlt | Karte | Benötigt | Im Bestand | Decks |",
              "|---:|---|---:|---:|---|"]
    for i in items:
        name = _md_inline(i["name"]).replace("|", "\\|")
        decks = _md_inline(_decks_text(i)).replace("|", "\\|")
        lines.append(f"| {i['missing']} | {name} | {i['needed']} | {i['owned']} | {decks} |")
    return "\n".join(lines) + "\n"


def export_shopping_list_csv(db_path: str) -> str:
    """Einkaufsliste als CSV fuer Excel: Fehlmenge, Kartenspalten, Bedarf,
    Bestand und Decks."""
    items = shopping_list(db_path)
    details = _card_details(db_path, [i["card_id"] for i in items])
    header = ("Fehlt",) + _CARD_COLUMNS + ("Benötigt", "Im Bestand", "Decks")
    return _to_csv(header, [
        [i["missing"]] + _card_csv_cells(i["card_id"], details.get(i["card_id"]))
        + [i["needed"], i["owned"], _decks_text(i)]
        for i in items
    ])
