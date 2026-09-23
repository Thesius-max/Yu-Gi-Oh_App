"""Kombo-Bibliothek: Bausteine, Rollen, Schritte, Varianten, Abdeckung.

Der Dreh- und Angelpunkt der App (siehe CLAUDE.md): benutzer-erfasste
Kombos liefern Guides, Deck-Fahrplan und den Synergie-Graphen. Abdeckung
zaehlt nur Main + Extra (Side bewusst ausgenommen); Varianten (Branch-
Modell, zweistufig) zaehlen in keinem Aggregat.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Iterable, Optional

from .schema import _conn, _like_contains


# ---------------------------------------------------------------------------
# Kombo-Bibliothek + Deckbuilding-Hilfe
# ---------------------------------------------------------------------------

MAX_COMBO_PIECE = 3  # mehr als 3 Kopien je Karte sind ohnehin nicht spielbar

# Rolle eines Bausteins INNERHALB einer Kombo (dieselbe Karte kann anderswo
# eine andere Rolle haben). NULL = noch nicht eingestuft.
COMBO_ROLES = ("starter", "extender", "payoff", "handtrap")

# Verbindliche Notation fuer Kombo-Schritte (siehe KOMBO-NOTATION.md):
#   <AKTION> <Karte> (<Quelle>) [Req: <Bedingung>] -> <Folge> | Lock: <Lock>
# Erlaubte Aktions-Keywords am Zeilenanfang:
COMBO_STEP_KEYWORDS = (
    "NS", "SS", "Act", "Eff", "Eff1", "Eff2", "Add", "Send", "Banish",
    "Mill", "Draw", "Discard", "Set",
    "Synchro:", "Xyz:", "Link:", "Fusion:",
)
# Beschwoerungsformeln: eigene Zeile, '<Art>: A + B -> Ziel'.
_FORMULA_KEYWORDS = ("synchro", "xyz", "link", "fusion")


def lint_combo_steps(steps: Iterable[str]) -> list[str]:
    """Prueft Schritte gegen die Kombo-Notation und liefert Warnungen
    (leer = konform). Bewusst tolerant und nur beratend -- die App warnt,
    blockiert aber nie."""
    allowed = {k.rstrip(":").lower() for k in COMBO_STEP_KEYWORDS}
    warnings: list[str] = []
    for no, text in enumerate(steps, start=1):
        problems: list[str] = []
        tokens = text.split()
        raw_first = tokens[0].lower() if tokens else ""
        first = raw_first.rstrip(":")
        if first not in allowed:
            problems.append(
                "beginnt nicht mit einem Notation-Keyword "
                "(NS, SS, Act, Eff/Eff1/Eff2, Add, Send, Banish, Mill, "
                "Draw, Discard, Set, Synchro:/Xyz:/Link:/Fusion:)"
            )
        head, *locks = (seg.strip() for seg in text.split("|"))
        for seg in locks:
            if not seg.lower().startswith("lock:"):
                problems.append("nach '|' fehlt das 'Lock:'-Praefix")
        if text.count("[") != text.count("]"):
            problems.append("eckige Klammer nicht geschlossen")
        else:
            for inner in re.findall(r"\[([^\]]*)\]", text):
                if not inner.strip().lower().startswith("req:"):
                    problems.append(
                        "eckige Klammern sind fuer Bedingungen "
                        "reserviert: [Req: ...]"
                    )
        lowered = head.lower()
        if first in _FORMULA_KEYWORDS:
            kind = tokens[0].rstrip(":")
            if not raw_first.endswith(":"):
                problems.append(
                    f"Formel braucht einen Doppelpunkt: '{kind}: A + B -> Ziel'"
                )
            elif "+" not in head or "->" not in head:
                problems.append(
                    "Formel unvollstaendig -- erwartet: "
                    f"{kind}: Material + Material -> Ziel"
                )
        elif any(f"{k}:" in lowered for k in _FORMULA_KEYWORDS):
            problems.append(
                "Beschwoerungsformeln ('Synchro:/Xyz:/Link:/Fusion: ...') "
                "bekommen eine eigene Zeile"
            )
        warnings.extend(f"Schritt {no}: {p}" for p in problems)
    return warnings


def create_combo(
    db_path: str, name: str, archetype: Optional[str] = None,
    deck_id: Optional[int] = None,
) -> int:
    with _conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO combos (name, archetype, deck_id) VALUES (?,?,?)",
            (name, archetype, deck_id),
        )
        conn.commit()
        return cur.lastrowid


def list_combos(
    db_path: str, deck_id: Optional[int] = None, text: Optional[str] = None
) -> list[sqlite3.Row]:
    """Hauptlinien (parent_combo_id IS NULL), optional nach Heimat-Deck
    gefiltert: deck_id=None -> alle, deck_id=0 -> nur ohne Heimat-Deck, sonst
    das Deck. 'text' filtert zusaetzlich nach Name, Archetyp oder einem
    Baustein-Kartennamen (de UND en, Gross/Klein inkl. Umlaute egal);
    passt eine Variante, erscheint ihre Hauptlinie. Varianten haengen an
    ihrer Hauptlinie (siehe combo_variants) und erscheinen hier bewusst
    nicht direkt."""
    sql = """SELECT cb.combo_id, cb.name, cb.archetype, cb.deck_id,
                    d.name AS deck_name
             FROM combos cb LEFT JOIN decks d ON d.deck_id = cb.deck_id
             WHERE cb.parent_combo_id IS NULL"""
    args: tuple = ()
    if deck_id == 0:
        sql += " AND cb.deck_id IS NULL"
    elif deck_id is not None:
        sql += " AND cb.deck_id = ?"
        args = (deck_id,)
    if text and text.strip():
        like = _like_contains(text)
        # Treffer-Menge ueber Hauptlinien UND Varianten (m); eine passende
        # Variante bringt ihre Hauptlinie mit.
        sql += """ AND cb.combo_id IN (
            SELECT COALESCE(m.parent_combo_id, m.combo_id) FROM combos m
            WHERE casefold(m.name) LIKE ? ESCAPE '\\'
               OR casefold(m.archetype) LIKE ? ESCAPE '\\'
               OR m.combo_id IN (
                   SELECT cc.combo_id FROM combo_cards cc
                   JOIN cards c ON c.id = cc.card_id
                   WHERE casefold(c.name) LIKE ? ESCAPE '\\'
                      OR casefold(c.name_de) LIKE ? ESCAPE '\\'))"""
        args = args + (like, like, like, like)
    sql += " ORDER BY cb.name"
    with _conn(db_path) as conn:
        return conn.execute(sql, args).fetchall()


def get_combo(db_path: str, combo_id: int) -> Optional[sqlite3.Row]:
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT cb.combo_id, cb.name, cb.archetype, cb.notes,
                      cb.boss_card_id, COALESCE(b.name_de, b.name) AS boss_name,
                      cb.deck_id, d.name AS deck_name,
                      cb.parent_combo_id, p.name AS parent_name
               FROM combos cb
               LEFT JOIN cards b ON b.id = cb.boss_card_id
               LEFT JOIN decks d ON d.deck_id = cb.deck_id
               LEFT JOIN combos p ON p.combo_id = cb.parent_combo_id
               WHERE cb.combo_id = ?""",
            (combo_id,),
        ).fetchone()


def set_combo_deck(db_path: str, combo_id: int, deck_id: Optional[int]) -> None:
    """Setzt das Heimat-Deck einer Kombo; None entfernt die Verknuepfung."""
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE combos SET deck_id = ? WHERE combo_id = ?",
            (deck_id, combo_id),
        )
        conn.commit()


def set_combo_parent(
    db_path: str, combo_id: int, parent_id: Optional[int]
) -> None:
    """Macht combo_id zur Variante (Branch) von parent_id; parent_id=None
    loest die Verknuepfung (wird wieder Hauptlinie). Haelt die Struktur
    bewusst ZWEISTUFIG: die Hauptlinie muss selbst eine Hauptlinie sein, und
    eine Kombo mit eigenen Varianten kann nicht selbst Variante werden.
    Dadurch sind Selbst-/Zyklus-Verknuepfungen ausgeschlossen.
    Wirft ValueError, wenn die Regeln verletzt wuerden."""
    if parent_id is not None and parent_id == combo_id:
        raise ValueError("Eine Kombo kann keine Variante ihrer selbst sein.")
    with _conn(db_path) as conn:
        if parent_id is not None:
            prow = conn.execute(
                "SELECT parent_combo_id FROM combos WHERE combo_id = ?",
                (parent_id,),
            ).fetchone()
            if prow is None:
                raise ValueError("Hauptlinie nicht gefunden.")
            if prow["parent_combo_id"] is not None:
                raise ValueError(
                    "Die gewaehlte Kombo ist selbst eine Variante."
                )
            if conn.execute(
                "SELECT 1 FROM combos WHERE parent_combo_id = ? LIMIT 1",
                (combo_id,),
            ).fetchone():
                raise ValueError(
                    "Diese Kombo hat eigene Varianten und kann nicht selbst "
                    "Variante werden."
                )
        conn.execute(
            "UPDATE combos SET parent_combo_id = ? WHERE combo_id = ?",
            (parent_id, combo_id),
        )
        conn.commit()


def combo_variants(db_path: str, combo_id: int) -> list[sqlite3.Row]:
    """Varianten (Branches) einer Hauptlinie, nach Name."""
    with _conn(db_path) as conn:
        return conn.execute(
            "SELECT combo_id, name, archetype FROM combos "
            "WHERE parent_combo_id = ? ORDER BY name",
            (combo_id,),
        ).fetchall()


def combo_variants_by_parent(db_path: str) -> dict[int, list[sqlite3.Row]]:
    """Alle Varianten, gruppiert nach Hauptlinie (je Gruppe nach Name) --
    eine Abfrage statt combo_variants() je Hauptlinie (Listenaufbau)."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT combo_id, name, archetype, parent_combo_id FROM combos "
            "WHERE parent_combo_id IS NOT NULL ORDER BY name"
        ).fetchall()
    out: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        out.setdefault(r["parent_combo_id"], []).append(r)
    return out


def update_combo(
    db_path: str, combo_id: int, name: str, archetype: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE combos SET name = ?, archetype = ?, notes = ?"
            " WHERE combo_id = ?",
            (name, archetype, notes, combo_id),
        )
        conn.commit()


def set_combo_boss(db_path: str, combo_id: int, card_id: Optional[int]) -> None:
    """Setzt das Zielmonster (Boss) einer Kombo; None entfernt es."""
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE combos SET boss_card_id = ? WHERE combo_id = ?",
            (card_id, combo_id),
        )
        conn.commit()


def delete_combo(db_path: str, combo_id: int) -> None:
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM combos WHERE combo_id = ?", (combo_id,))
        conn.commit()


def add_combo_card(db_path: str, combo_id: int, card_id: int, quantity: int = 1) -> None:
    with _conn(db_path) as conn:
        ex = conn.execute(
            "SELECT quantity FROM combo_cards WHERE combo_id = ? AND card_id = ?",
            (combo_id, card_id),
        ).fetchone()
        if ex:
            new = min(MAX_COMBO_PIECE, ex["quantity"] + quantity)
            conn.execute(
                "UPDATE combo_cards SET quantity = ? WHERE combo_id = ? AND card_id = ?",
                (new, combo_id, card_id),
            )
        else:
            conn.execute(
                "INSERT INTO combo_cards (combo_id, card_id, quantity) VALUES (?,?,?)",
                (combo_id, card_id, min(MAX_COMBO_PIECE, quantity)),
            )
        conn.commit()


def set_combo_card_quantity(
    db_path: str, combo_id: int, card_id: int, quantity: int
) -> None:
    with _conn(db_path) as conn:
        if quantity <= 0:
            conn.execute(
                "DELETE FROM combo_cards WHERE combo_id = ? AND card_id = ?",
                (combo_id, card_id),
            )
        else:
            conn.execute(
                "UPDATE combo_cards SET quantity = ? WHERE combo_id = ? AND card_id = ?",
                (min(MAX_COMBO_PIECE, quantity), combo_id, card_id),
            )
        conn.commit()


def set_combo_card_role(
    db_path: str, combo_id: int, card_id: int, role: Optional[str]
) -> None:
    """Setzt die Rolle eines Bausteins (siehe COMBO_ROLES); None loescht sie."""
    if role is not None and role not in COMBO_ROLES:
        raise ValueError(f"Unbekannte Rolle: {role}")
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE combo_cards SET role = ? WHERE combo_id = ? AND card_id = ?",
            (role, combo_id, card_id),
        )
        conn.commit()


def remove_combo_card(db_path: str, combo_id: int, card_id: int) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            "DELETE FROM combo_cards WHERE combo_id = ? AND card_id = ?",
            (combo_id, card_id),
        )
        conn.commit()


def combo_cards(db_path: str, combo_id: int) -> list[sqlite3.Row]:
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT cc.card_id, COALESCE(c.name_de, c.name) AS name,
                      c.type, c.frame_type, cc.quantity, cc.role
               FROM combo_cards cc JOIN cards c ON c.id = cc.card_id
               WHERE cc.combo_id = ? ORDER BY COALESCE(c.name_de, c.name)""",
            (combo_id,),
        ).fetchall()


def set_combo_steps(db_path: str, combo_id: int, steps: list[str]) -> None:
    """Ersetzt alle Schritte einer Kombo durch die uebergebene Liste."""
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM combo_steps WHERE combo_id = ?", (combo_id,))
        for i, text in enumerate(steps, start=1):
            conn.execute(
                "INSERT INTO combo_steps (combo_id, step_no, text) VALUES (?,?,?)",
                (combo_id, i, text),
            )
        conn.commit()


def combo_steps(db_path: str, combo_id: int) -> list[sqlite3.Row]:
    with _conn(db_path) as conn:
        return conn.execute(
            "SELECT step_no, text FROM combo_steps WHERE combo_id = ? ORDER BY step_no",
            (combo_id,),
        ).fetchall()


def combos_for_card(db_path: str, card_id: int) -> list[sqlite3.Row]:
    """Alle Kombos, die diese Karte als Baustein verwenden."""
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT cb.combo_id, cb.name FROM combos cb
               JOIN combo_cards cc ON cc.combo_id = cb.combo_id
               WHERE cc.card_id = ? ORDER BY cb.name""",
            (card_id,),
        ).fetchall()


def _coverage_result(pieces) -> dict:
    """Baut die Coverage-Rueckgabe (total/covered/pieces) aus Baustein-Zeilen
    mit den Feldern needed/have. Gemeinsame Logik von combo_coverage (gegen
    ein Deck) und combo_coverage_collection (gegen die Sammlung)."""
    result, covered = [], 0
    for p in pieces:
        missing = max(0, p["needed"] - p["have"])
        if missing == 0:
            covered += 1
        result.append({
            "card_id": p["card_id"], "name": p["name"], "type": p["type"],
            "frame_type": p["frame_type"], "needed": p["needed"],
            "have": p["have"], "missing": missing,
        })
    return {"total": len(result), "covered": covered, "pieces": result}


def _combo_coverage_deck(conn: sqlite3.Connection, combo_id: int, deck_id: int) -> dict:
    """Abdeckung gegen ein Deck (Main+Extra) auf einer offenen Verbindung --
    so koennen combos_for_deck/Export sie ohne N+1-Verbindungen wiederholen."""
    pieces = conn.execute(
        """SELECT cc.card_id, COALESCE(c.name_de, c.name) AS name,
                  c.type, c.frame_type,
                  cc.quantity AS needed,
                  COALESCE((SELECT SUM(dc.quantity) FROM deck_cards dc
                            WHERE dc.deck_id = ? AND dc.card_id = cc.card_id
                              AND dc.zone IN ('main','extra')), 0) AS have
           FROM combo_cards cc JOIN cards c ON c.id = cc.card_id
           WHERE cc.combo_id = ? ORDER BY COALESCE(c.name_de, c.name)""",
        (deck_id, combo_id),
    ).fetchall()
    return _coverage_result(pieces)


def _combo_coverage_collection(conn: sqlite3.Connection, combo_id: int) -> dict:
    """Abdeckung gegen die Sammlung auf einer offenen Verbindung."""
    pieces = conn.execute(
        """SELECT cc.card_id, COALESCE(c.name_de, c.name) AS name,
                  c.type, c.frame_type,
                  cc.quantity AS needed,
                  COALESCE((SELECT SUM(col.quantity) FROM collection col
                            WHERE col.card_id = cc.card_id), 0) AS have
           FROM combo_cards cc JOIN cards c ON c.id = cc.card_id
           WHERE cc.combo_id = ? ORDER BY COALESCE(c.name_de, c.name)""",
        (combo_id,),
    ).fetchall()
    return _coverage_result(pieces)


def combo_coverage(db_path: str, combo_id: int, deck_id: int) -> dict:
    """Abgleich einer Kombo mit einem Deck (Main+Extra).
    Rueckgabe: {'total', 'covered', 'pieces': [{card_id, name, type,
    frame_type, needed, have, missing}]}."""
    with _conn(db_path) as conn:
        return _combo_coverage_deck(conn, combo_id, deck_id)


def combos_for_deck(db_path: str, deck_id: int) -> list[dict]:
    """Alle Kombos mit ihrer Abdeckung gegen das Deck, nach Abdeckung sortiert.
    Eine Verbindung fuer alle Kombos (kein N+1)."""
    with _conn(db_path) as conn:
        combos = conn.execute(
            "SELECT combo_id, name, archetype FROM combos "
            "WHERE parent_combo_id IS NULL ORDER BY name"
        ).fetchall()
        out = []
        for cb in combos:
            cov = _combo_coverage_deck(conn, cb["combo_id"], deck_id)
            total = cov["total"]
            out.append({
                "combo_id": cb["combo_id"], "name": cb["name"],
                "archetype": cb["archetype"], "total": total,
                "covered": cov["covered"],
                "coverage": (cov["covered"] / total) if total else 0.0,
            })
    out.sort(key=lambda x: (-x["coverage"], x["name"]))
    return out


def deck_role_summary(db_path: str, deck_id: int) -> dict[str, list[dict]]:
    """Karten je Rolle im Deck (Main + Extra, wie combo_coverage; das Side
    Deck bleibt aussen vor). Eine Karte erscheint je Rolle einmal, auch wenn
    mehrere Kombos ihr dieselbe Rolle geben; verschiedene Rollen aus
    verschiedenen Kombos sind moeglich.
    Rueckgabe: {rolle: [{'card_id', 'name', 'copies'}, ...]}."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT DISTINCT cc.role, dc.card_id,
                      COALESCE(c.name_de, c.name) AS name, dc.quantity
               FROM deck_cards dc
               JOIN combo_cards cc ON cc.card_id = dc.card_id
               JOIN cards c ON c.id = dc.card_id
               WHERE dc.deck_id = ? AND dc.zone IN ('main', 'extra')
                 AND cc.role IS NOT NULL
                 AND cc.combo_id IN (SELECT combo_id FROM combos
                                     WHERE parent_combo_id IS NULL)
               ORDER BY name""",
            (deck_id,),
        ).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["role"], []).append({
            "card_id": r["card_id"], "name": r["name"],
            "copies": int(r["quantity"]),
        })
    return out


def deck_boss_lines(db_path: str, deck_id: int) -> list[dict]:
    """Linien (Kombos) gruppiert nach Bossmonster; innerhalb der Gruppen
    bleibt die Abdeckungs-Sortierung aus combos_for_deck erhalten, ebenso
    zwischen den Gruppen (beste Linie zuerst). Kombos ohne Boss bilden die
    letzte Gruppe (boss_card_id None). Jede Linie traegt ihre Varianten
    (Interruption-Branches) als Namensliste -- eine Linie ohne Branches
    steht bei gegnerischer Stoerung 'nackt' da (Resilienz-Anzeige).
    Rueckgabe: [{'boss_card_id', 'boss_name',
                 'lines': [wie combos_for_deck + 'variants': [name, ...]]}]."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT cb.combo_id, cb.boss_card_id,
                      COALESCE(b.name_de, b.name) AS boss_name
               FROM combos cb LEFT JOIN cards b ON b.id = cb.boss_card_id"""
        ).fetchall()
        variant_rows = conn.execute(
            "SELECT parent_combo_id, name FROM combos "
            "WHERE parent_combo_id IS NOT NULL ORDER BY name"
        ).fetchall()
    boss_of = {r["combo_id"]: (r["boss_card_id"], r["boss_name"]) for r in rows}
    variants_of: dict[int, list[str]] = {}
    for r in variant_rows:
        variants_of.setdefault(r["parent_combo_id"], []).append(r["name"])
    groups: dict = {}
    for line in combos_for_deck(db_path, deck_id):
        boss_id, boss_name = boss_of.get(line["combo_id"], (None, None))
        g = groups.setdefault(
            boss_id,
            {"boss_card_id": boss_id, "boss_name": boss_name, "lines": []},
        )
        line["variants"] = variants_of.get(line["combo_id"], [])
        g["lines"].append(line)
    out = [g for k, g in groups.items() if k is not None]
    if None in groups:
        out.append(groups[None])
    return out


def combo_coverage_collection(db_path: str, combo_id: int) -> dict:
    """Abgleich einer Kombo mit der eigenen Sammlung (collection).
    Gleiche Struktur wie combo_coverage, aber 'have' zaehlt den Bestand --
    beantwortet: 'Welche Bausteine besitze ich schon?'"""
    with _conn(db_path) as conn:
        return _combo_coverage_collection(conn, combo_id)


def combos_for_collection(db_path: str) -> list[dict]:
    """Alle Kombos mit ihrer Abdeckung gegen die Sammlung, nach Abdeckung
    sortiert. Beantwortet: 'Welche Kombos kann ich mit meinen Karten bauen?'
    Eine Verbindung fuer alle Kombos (kein N+1)."""
    with _conn(db_path) as conn:
        combos = conn.execute(
            "SELECT combo_id, name, archetype FROM combos "
            "WHERE parent_combo_id IS NULL ORDER BY name"
        ).fetchall()
        out = []
        for cb in combos:
            cov = _combo_coverage_collection(conn, cb["combo_id"])
            total = cov["total"]
            out.append({
                "combo_id": cb["combo_id"], "name": cb["name"],
                "archetype": cb["archetype"], "total": total,
                "covered": cov["covered"],
                "coverage": (cov["covered"] / total) if total else 0.0,
            })
    out.sort(key=lambda x: (-x["coverage"], x["name"]))
    return out


# Anzeigenamen der Baustein-Rollen (siehe COMBO_ROLES). Oeffentlich, weil die
# GUI dieselbe Beschriftung nutzt -- eine Quelle der Wahrheit.
ROLE_LABEL = {
    "starter": "Starter", "extender": "Extender",
    "payoff": "Payoff", "handtrap": "Handtrap",
}
