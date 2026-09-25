"""Text-/Markdown-Exporte fuer Sammlung, Decks und Kombo-Linien.

Die Datenschicht erzeugt nur den fertigen Text; die GUI schreibt ihn als
.txt/.md (woertlich) oder .pdf (Monospace-Satz). KI-Exporte sind Markdown
mit vollem Kartentext, damit ein LLM Deck bzw. Sammlung ohne Nachschlagen
beurteilen kann.
"""

from __future__ import annotations

import datetime
import re
import sqlite3
from typing import Optional

from .analysis import deck_consistency
from .cards import card_category
from .collection import list_collection
from .combos import (
    COMBO_ROLES, ROLE_LABEL, combo_cards, combo_coverage, combo_steps,
    combo_variants, combos_for_deck, deck_role_summary, get_combo,
)
from .decks import deck_cards
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
# Die Datenschicht erzeugt nur den fertigen Text; die GUI schreibt ihn als
# .txt/.md (woertlich) oder .pdf (Monospace-Satz). KI-Exporte sind Markdown
# mit vollem Kartentext, damit ein LLM Deck bzw. Sammlung ohne Nachschlagen
# beurteilen kann.

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
            f"""SELECT id, COALESCE(name_de, name) AS name, type, race,
                       attribute, atk, def, level, link_value, archetype,
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
    rulings: Optional[list[sqlite3.Row]] = None,
) -> list[str]:
    """Markdown-Block einer Karte: Ueberschrift (lead = z.B. '3x '), Typzeile,
    optional Archetyp/Rolle, der volle Effekttext (eingerueckt) und eigene
    Rulings."""
    if d is None:
        return [f"### {lead}(unbekannte Karte)", ""]
    block = [f"### {lead}{_md_inline(d['name'])}", f"- Typ: {_card_meta_line(d)}"]
    if d["archetype"]:
        block.append(f"- Archetyp: {d['archetype']}")
    if roles:
        block.append("- Rolle: " + ", ".join(ROLE_LABEL.get(r, r) for r in roles))
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


def export_collection_text(
    db_path: str, text: Optional[str] = None, category: Optional[str] = None,
    attribute: Optional[str] = None, archetype: Optional[str] = None,
    untranslated_only: bool = False,
) -> str:
    """Sammlung als lesbarer Text -- ein Druck (Eintrag) je Zeile, gruppiert
    nach Kartenklasse. Beruecksichtigt dieselben Filter wie die Sammlungs-
    Ansicht; ohne Filter ist es die gesamte Sammlung."""
    rows = list_collection(
        db_path, text, category, attribute, archetype,
        untranslated_only=untranslated_only,
    )
    note = _export_filter_note(
        text, category, attribute, archetype, untranslated_only
    )
    total = sum(r["quantity"] for r in rows)
    unique = len({r["card_id"] for r in rows})
    out = [
        "Sammlung" + (f" — {note}" if note else ""),
        f"Stand: {datetime.date.today().strftime('%d.%m.%Y')}",
        f"{len(rows)} Eintrag(e) · {unique} verschiedene Karten · "
        f"{total} Karten gesamt",
    ]
    by_cat: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_cat.setdefault(card_category(r["type"]), []).append(r)
    for cat in _EXPORT_CATEGORY_ORDER:
        crows = by_cat.get(cat)
        if not crows:
            continue
        cat_total = sum(r["quantity"] for r in crows)
        out += ["", f"== {_EXPORT_CATEGORY_DE[cat]} ({cat_total}) =="]
        for r in crows:
            name = r["name_de"] or r["name"]
            detail = [
                r[k] for k in ("set_code", "edition", "condition", "language")
                if r[k]
            ]
            tail = f"  [{', '.join(detail)}]" if detail else ""
            nt = r["notes"].strip() if r["notes"] else ""
            note_t = f"  ({nt})" if nt else ""
            out.append(f"  {r['quantity']}x {name}{tail}{note_t}")
    return "\n".join(out) + "\n"


def export_collection_markdown(
    db_path: str, text: Optional[str] = None, category: Optional[str] = None,
    attribute: Optional[str] = None, archetype: Optional[str] = None,
    untranslated_only: bool = False,
) -> str:
    """Sammlung als KI-tauglicher Markdown-Block: je Karte ein Eintrag mit
    Typ, Attribut und vollem Effekttext (DE bevorzugt) plus Gesamtmenge --
    damit ein KI-System Decks aus dem Bestand vorschlagen kann, ohne Karten
    nachzuschlagen. Gleiche Filter wie die Sammlungs-Ansicht."""
    rows = list_collection(
        db_path, text, category, attribute, archetype,
        untranslated_only=untranslated_only,
    )
    qty: dict[int, int] = {}
    order: list[int] = []
    for r in rows:
        if r["card_id"] not in qty:
            order.append(r["card_id"])
            qty[r["card_id"]] = 0
        qty[r["card_id"]] += r["quantity"]
    details = _card_details(db_path, order)
    note = _export_filter_note(
        text, category, attribute, archetype, untranslated_only
    )
    lines = [
        "# Yu-Gi-Oh!-Sammlung" + (f" — {note}" if note else ""),
        "",
        f"Stand: {datetime.date.today().strftime('%d.%m.%Y')} · "
        f"{len(order)} verschiedene Karten · {sum(qty.values())} gesamt",
        "",
        "Jede Karte mit vollem Effekttext und besessener Menge. Nutzbar, um "
        "eine KI Decks aus diesem Bestand vorschlagen zu lassen.",
    ]
    by_cat: dict[str, list[int]] = {}
    for cid in order:
        d = details.get(cid)
        by_cat.setdefault(card_category(d["type"] if d else None), []).append(cid)
    for cat in _EXPORT_CATEGORY_ORDER:
        cids = by_cat.get(cat)
        if not cids:
            continue
        lines += ["", f"## {_EXPORT_CATEGORY_DE[cat]} ({len(cids)})", ""]
        for cid in cids:
            lines += _card_md_block(details.get(cid), f"{qty[cid]}x ")
    return "\n".join(lines) + "\n"


def export_deck_text(db_path: str, deck_id: int) -> str:
    """Deck als lesbare Liste je Zone (Main/Extra/Side) mit Mengen --
    menschenlesbare Ergaenzung zum technischen .ydk."""
    with _conn(db_path) as conn:
        deck = conn.execute(
            "SELECT name FROM decks WHERE deck_id = ?", (deck_id,)
        ).fetchone()
    if deck is None:
        raise ValueError(f"Deck {deck_id} nicht gefunden.")
    out = [
        f"Deck: {deck['name']}",
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
        out += ["", f"== {label} ({total}) =="]
        for r in rows:
            d = details.get(r["card_id"])
            name = d["name"] if d else r["name"]
            out.append(f"  {r['quantity']}x {name}")
    return "\n".join(out) + "\n"


def export_deck_markdown(db_path: str, deck_id: int) -> str:
    """Deck als KI-tauglicher Markdown-Block: alle Karten je Zone mit vollem
    Effekttext und (sofern erfasst) Kombo-Rolle, gefolgt von Konsistenz und
    Kombo-Linien -- damit eine KI das Deck ohne Nachschlagen analysiert."""
    with _conn(db_path) as conn:
        deck = conn.execute(
            "SELECT name FROM decks WHERE deck_id = ?", (deck_id,)
        ).fetchone()
    if deck is None:
        raise ValueError(f"Deck {deck_id} nicht gefunden.")
    role_map: dict[int, list[str]] = {}
    for role, cards in deck_role_summary(db_path, deck_id).items():
        for c in cards:
            role_map.setdefault(c["card_id"], []).append(role)
    lines = [
        f"# Deck: {deck['name']}",
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
            roles = sorted(role_map.get(r["card_id"], []), key=COMBO_ROLES.index)
            lines += _card_md_block(
                details.get(r["card_id"]), f"{r['quantity']}x ", roles or None,
                list_card_rulings(db_path, r["card_id"]),
            )
    # Konsistenz + Kombo-Linien aus der bestehenden Funktion (eine Quelle der
    # Wahrheit); woertlich in einen Codeblock gesetzt, damit das Textlayout
    # (== Ueberschriften, '->'-Ketten) im Markdown erhalten bleibt.
    combo_text = export_deck_combos_text(db_path, deck_id).rstrip("\n")
    fence = _md_fence(combo_text)
    lines += ["", "## Konsistenz & Kombo-Linien", "", fence, combo_text, fence, ""]
    return "\n".join(lines) + "\n"
