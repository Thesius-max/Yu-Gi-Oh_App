"""
yugioh_db.py
============
Selbststaendiges Fundament fuer eine Yu-Gi-Oh-Sammlungs- und Nachschlage-App.

Prinzip:
  - Einmalig die komplette Kartendatenbank von der YGOPRODeck-API ziehen.
  - Lokal in SQLite ablegen (inkl. Volltextsuche ueber FTS5).
  - Danach laeuft alles offline; Update nur bei neuer DB-Version.

Nur Standardbibliothek (urllib, json, sqlite3) -- keine Drittpakete.

Datenmodell (zwei klar getrennte Ebenen):
  cards / card_sets   -> Referenz: alle Karten, die es gibt (aus der API)
  collection          -> dein Bestand: was du tatsaechlich besitzt

Hinweis zu Bildern: Die API-Bilder NICHT dauerhaft hotlinken. Wer Bilder
anzeigen will, laedt sie einmal herunter und legt sie lokal ab
(siehe cache_image()), sonst droht eine IP-Sperre.
"""

from __future__ import annotations

import datetime
import itertools
import math
import random
import re
import sqlite3
from typing import Iterable, Optional

from .cards import card_category
from .collection import list_collection
from .decks import deck_cards, deck_counts
from .schema import _conn


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
    "Mill", "Draw", "Discard", "Set", "Synchro:",
)


def lint_combo_steps(steps: Iterable[str]) -> list[str]:
    """Prueft Schritte gegen die Kombo-Notation und liefert Warnungen
    (leer = konform). Bewusst tolerant und nur beratend -- die App warnt,
    blockiert aber nie."""
    allowed = {k.rstrip(":").lower() for k in COMBO_STEP_KEYWORDS}
    warnings: list[str] = []
    for no, text in enumerate(steps, start=1):
        problems: list[str] = []
        tokens = text.split()
        first = tokens[0].rstrip(":").lower() if tokens else ""
        if first not in allowed:
            problems.append(
                "beginnt nicht mit einem Notation-Keyword "
                "(NS, SS, Act, Eff/Eff1/Eff2, Add, Send, Banish, Mill, "
                "Draw, Discard, Set, Synchro:)"
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
        if lowered.startswith("synchro:"):
            if "+" not in head or "->" not in head:
                problems.append(
                    "Formel unvollstaendig -- erwartet: "
                    "Synchro: Tuner (Lvl) + Non-Tuner (Lvl) -> Ziel (Lvl)"
                )
        elif "synchro:" in lowered:
            problems.append(
                "Beschwoerungsformeln ('Synchro: ...') bekommen eine "
                "eigene Zeile"
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
    Baustein-Kartennamen (de/en). Varianten haengen an ihrer Hauptlinie (siehe
    combo_variants) und erscheinen hier bewusst nicht direkt."""
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
        like = f"%{text.strip()}%"
        sql += """ AND (cb.name LIKE ? OR cb.archetype LIKE ?
                        OR cb.combo_id IN (
                            SELECT cc.combo_id FROM combo_cards cc
                            JOIN cards c ON c.id = cc.card_id
                            WHERE COALESCE(c.name_de, c.name) LIKE ?))"""
        args = args + (like, like, like)
    sql += " ORDER BY cb.name"
    with _conn(db_path) as conn:
        return conn.execute(sql, args).fetchall()


def get_combo(db_path: str, combo_id: int) -> Optional[sqlite3.Row]:
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT cb.combo_id, cb.name, cb.archetype, cb.notes,
                      cb.boss_card_id, b.name AS boss_name,
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
    letzte Gruppe (boss_card_id None).
    Rueckgabe: [{'boss_card_id', 'boss_name', 'lines': [wie combos_for_deck]}]."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT cb.combo_id, cb.boss_card_id,
                      COALESCE(b.name_de, b.name) AS boss_name
               FROM combos cb LEFT JOIN cards b ON b.id = cb.boss_card_id"""
        ).fetchall()
    boss_of = {r["combo_id"]: (r["boss_card_id"], r["boss_name"]) for r in rows}
    groups: dict = {}
    for line in combos_for_deck(db_path, deck_id):
        boss_id, boss_name = boss_of.get(line["combo_id"], (None, None))
        g = groups.setdefault(
            boss_id,
            {"boss_card_id": boss_id, "boss_name": boss_name, "lines": []},
        )
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


# ---------------------------------------------------------------------------
# Konsistenz-Mathematik (hypergeometrisch, exakt, reine Standardbibliothek)
# ---------------------------------------------------------------------------

def hypergeom_at_least(
    population: int, successes: int, draws: int, min_hits: int = 1
) -> float:
    """Wahrscheinlichkeit, beim Ziehen ohne Zuruecklegen mindestens
    'min_hits' Erfolge zu ziehen. Unsinnige Eingaben werden gekappt
    (successes/draws auf population), statt zu raten oder zu werfen."""
    population = max(0, population)
    successes = max(0, min(successes, population))
    draws = max(0, min(draws, population))
    if min_hits <= 0:
        return 1.0
    if successes == 0 or draws == 0:
        return 0.0
    total = math.comb(population, draws)
    misses = sum(
        math.comb(successes, k) * math.comb(population - successes, draws - k)
        for k in range(min(min_hits, draws + 1))
    )
    return 1.0 - misses / total


def prob_open_all(deck_size: int, copies: Iterable[int], hand: int) -> float:
    """Wahrscheinlichkeit, von JEDER genannten Karte mindestens eine Kopie in
    der Starthand zu haben ('alle gewuenschten Karten zusammen oeffnen').

    'copies' = Kopienzahl je verschiedener Wunschkarte (disjunkte Karten).
    Exakt ueber Inklusion-Exklusion: P(alle da) =
        Sum_S (-1)^|S| * C(N - Sum_{i in S} c_i, h) / C(N, h)
    ueber alle Teilmengen S der Wunschkarten (math.comb, reine stdlib). Fuer
    die 'mindestens eine davon'-Frage genuegt hypergeom_at_least(N, sum(c), h).
    Unsinnige Eingaben werden gekappt statt geworfen; leere Auswahl -> 1.0
    (keine Bedingung)."""
    deck_size = max(0, deck_size)
    hand = max(0, min(hand, deck_size))
    cs = [c for c in (max(0, c) for c in copies) if c > 0]
    if not cs:
        return 1.0           # keine Wunschkarte -> Bedingung trivial erfuellt
    if hand == 0:
        return 0.0
    total = math.comb(deck_size, hand)
    acc = 0.0
    for r in range(len(cs) + 1):
        for subset in itertools.combinations(cs, r):
            remaining = deck_size - sum(subset)
            # remaining < hand (auch negativ bei Unsinn) -> C() = 0, kein Term.
            term = math.comb(remaining, hand) if remaining >= hand else 0
            acc += ((-1) ** r) * term
    return acc / total


def deck_role_copies(db_path: str, deck_id: int) -> dict[str, int]:
    """Kopien je Rolle im MAIN Deck (nur daraus wird gezogen).
    Eine Karte zaehlt je Rolle einmal, auch wenn mehrere Kombos ihr dieselbe
    Rolle geben; traegt sie in verschiedenen Kombos verschiedene Rollen,
    zaehlt sie in jeder davon."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT role, SUM(quantity) AS n FROM (
                   SELECT DISTINCT dc.card_id, cc.role, dc.quantity
                   FROM deck_cards dc
                   JOIN combo_cards cc ON cc.card_id = dc.card_id
                   WHERE dc.deck_id = ? AND dc.zone = 'main'
                     AND cc.role IS NOT NULL
                     AND cc.combo_id IN (SELECT combo_id FROM combos
                                         WHERE parent_combo_id IS NULL)
               ) GROUP BY role""",
            (deck_id,),
        ).fetchall()
        return {r["role"]: int(r["n"]) for r in rows}


# Anzeigenamen der Baustein-Rollen (siehe COMBO_ROLES). Oeffentlich, weil die
# GUI dieselbe Beschriftung nutzt -- eine Quelle der Wahrheit.
ROLE_LABEL = {
    "starter": "Starter", "extender": "Extender",
    "payoff": "Payoff", "handtrap": "Handtrap",
}


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
        if cb["covered"] == 0 and combo["deck_id"] != deck_id:
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


def _card_md_block(
    d: Optional[sqlite3.Row], lead: str, roles: Optional[list[str]] = None
) -> list[str]:
    """Markdown-Block einer Karte: Ueberschrift (lead = z.B. '3x '), Typzeile,
    optional Archetyp/Rolle und der volle Effekttext (eingerueckt)."""
    if d is None:
        return [f"### {lead}(unbekannte Karte)", ""]
    block = [f"### {lead}{d['name']}", f"- Typ: {_card_meta_line(d)}"]
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
                details.get(r["card_id"]), f"{r['quantity']}x ", roles or None
            )
    # Konsistenz + Kombo-Linien aus der bestehenden Funktion (eine Quelle der
    # Wahrheit); woertlich in einen Codeblock gesetzt, damit das Textlayout
    # (== Ueberschriften, '->'-Ketten) im Markdown erhalten bleibt.
    combo_text = export_deck_combos_text(db_path, deck_id).rstrip("\n")
    lines += ["", "## Konsistenz & Kombo-Linien", "", "```", combo_text, "```", ""]
    return "\n".join(lines) + "\n"


def deck_consistency(
    db_path: str, deck_id: int, hand_sizes: Iterable[int] = (5, 6)
) -> dict:
    """Konsistenz-Kennzahlen der Starthand (5 = First, 6 = Second).
    'brick' ist als Hand ohne Starter definiert.
    Rueckgabe: {'deck_size', 'roles': {rolle: kopien},
    'hands': {handgroesse: {'starter', 'handtrap', 'brick'}}}."""
    size = deck_counts(db_path, deck_id)["main"]
    roles = deck_role_copies(db_path, deck_id)
    hands = {}
    for hand in hand_sizes:
        p_starter = hypergeom_at_least(size, roles.get("starter", 0), hand)
        hands[hand] = {
            "starter": p_starter,
            "handtrap": hypergeom_at_least(size, roles.get("handtrap", 0), hand),
            "brick": 1.0 - p_starter,
        }
    return {"deck_size": size, "roles": roles, "hands": hands}


def deck_main_cards(db_path: str, deck_id: int) -> list[dict]:
    """Verschiedene Main-Deck-Karten mit Kopienzahl und (ggf.) Rollen --
    Grundlage fuer den Starthand-Simulator (Karten-Picker + Zufallshand).
    Eine Karte traegt alle Rollen, die ihr in irgendeiner Kombo gegeben
    wurden. Rueckgabe: [{'card_id', 'name', 'copies', 'roles': [...]}],
    nach Anzeigename sortiert."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT dc.card_id, COALESCE(c.name_de, c.name) AS name,
                      dc.quantity AS copies
               FROM deck_cards dc JOIN cards c ON c.id = dc.card_id
               WHERE dc.deck_id = ? AND dc.zone = 'main'
               ORDER BY name""",
            (deck_id,),
        ).fetchall()
        role_rows = conn.execute(
            """SELECT DISTINCT cc.card_id, cc.role FROM combo_cards cc
               WHERE cc.role IS NOT NULL
                 AND cc.combo_id IN (SELECT combo_id FROM combos
                                     WHERE parent_combo_id IS NULL)
                 AND cc.card_id IN
                   (SELECT card_id FROM deck_cards
                    WHERE deck_id = ? AND zone = 'main')""",
            (deck_id,),
        ).fetchall()
    roles_by: dict[int, list[str]] = {}
    for r in role_rows:
        roles_by.setdefault(r["card_id"], []).append(r["role"])
    return [
        {"card_id": r["card_id"], "name": r["name"], "copies": int(r["copies"]),
         "roles": sorted(roles_by.get(r["card_id"], []))}
        for r in rows
    ]


def draw_sample_hand(
    db_path: str, deck_id: int, hand_size: int = 5, rng=None
) -> list[dict]:
    """Zieht zufaellig hand_size Karten aus dem MAIN Deck (Kopien expandiert,
    ohne Zuruecklegen). 'rng' (random.Random) ist injizierbar -- so wird das
    Ziehen deterministisch testbar. Handgroesse wird auf die Main-Deck-Groesse
    gekappt. Rueckgabe: gezogene Karten [{'card_id', 'name', 'roles'}]."""
    pool = []
    for c in deck_main_cards(db_path, deck_id):
        pool.extend([c] * c["copies"])
    rng = rng or random
    k = min(max(0, hand_size), len(pool))
    return [
        {"card_id": c["card_id"], "name": c["name"], "roles": c["roles"]}
        for c in rng.sample(pool, k)
    ]


# ---------------------------------------------------------------------------
# Synergie-Graph & Vorschlaege (Roadmap-Schritt 4)
# ---------------------------------------------------------------------------

# Abschlag fuer Zwei-Hop-Verbindungen: eine Brueckenkarte (selbst nicht im
# Deck) zaehlt deutlich weniger als eine direkte gemeinsame Kombo.
_TRANSITIVE_DISCOUNT = 0.3

# --- Korpus-Kanten (Co-Occurrence ueber Referenz-Decks) ---------------------
# Mindestens so viele Referenz-Decks muessen ein Kartenpaar enthalten, damit
# eine Kante entsteht: PMI gibt sonst gerade den seltensten Paaren (eine
# einzige schraege Liste) die hoechsten Werte.
_CORPUS_MIN_DECKS = 2
# Halbwertszeit der Alterung: eine ein Jahr alte Liste zaehlt halb so viel
# (das Format dreht sich weiter; format_date NULL wird wie heute behandelt).
_CORPUS_HALF_LIFE_DAYS = 365.0
# Daempfung der Korpus-Kanten gegenueber den praezisen Kombo-Kanten im Score.
_CORPUS_WEIGHT = 0.5
# Je Kandidat gehen nur die staerksten PMI-Verbindungen in den Score ein,
# sonst schlaegt die schiere Deckgroesse jede Kombo-Evidenz.
_CORPUS_TOP_LINKS = 5


def synergy_edges(db_path: str) -> dict[tuple[int, int], dict]:
    """Kanten des Synergie-Graphen aus der Kombo-Bibliothek: zwei Karten sind
    verbunden, wenn sie gemeinsam in einer Kombo stehen; Gewicht = Anzahl
    gemeinsamer Kombos. Der Meta-Korpus (Roadmap-Schritt 5) speist spaeter
    zusaetzliche Kanten in denselben Graphen ein.
    Rueckgabe: {(a, b): {'weight': n, 'combos': [combo_id, ...]}} mit a < b."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT cc.combo_id, cc.card_id FROM combo_cards cc "
            "JOIN combos cb ON cb.combo_id = cc.combo_id "
            "WHERE cb.parent_combo_id IS NULL"
        ).fetchall()
    members: dict[int, set[int]] = {}
    for r in rows:
        members.setdefault(r["combo_id"], set()).add(r["card_id"])
    edges: dict[tuple[int, int], dict] = {}
    for combo_id in sorted(members):
        for a, b in itertools.combinations(sorted(members[combo_id]), 2):
            e = edges.setdefault((a, b), {"weight": 0, "combos": []})
            e["weight"] += 1
            e["combos"].append(combo_id)
    return edges


def corpus_edges(db_path: str) -> dict[tuple[int, int], dict]:
    """Co-Occurrence-Kanten aus den Referenz-Decks: jede Liste ist ein
    Datensatz, jedes Kartenpaar (Main+Extra, Praesenz statt Kopienzahl)
    eine gemeinsame Nennung. Statistik, kein ML-Training.

    Normierung ueber PPMI (positive pointwise mutual information) mit nach
    format_date gealterten Deck-Gewichten -- Staples, die in fast jeder
    Liste stehen, fallen dadurch auf (nahe) 0, echte Engine-Partner bleiben
    uebrig. Paare in weniger als _CORPUS_MIN_DECKS Listen entfallen.
    Rueckgabe: {(a, b): {'weight': ppmi, 'decks': k, 'total': N}} mit a < b;
    'decks'/'total' sind ungewichtete Listen-Zaehler fuer die Begruendung."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT d.deck_id, d.format_date, dc.card_id
               FROM decks d JOIN deck_cards dc ON dc.deck_id = d.deck_id
               WHERE d.kind = 'reference' AND dc.zone IN ('main', 'extra')"""
        ).fetchall()

    members: dict[int, set[int]] = {}
    dates: dict[int, Optional[str]] = {}
    for r in rows:
        members.setdefault(r["deck_id"], set()).add(r["card_id"])
        dates[r["deck_id"]] = r["format_date"]
    if not members:
        return {}

    today = datetime.date.today()
    weights: dict[int, float] = {}
    for did, date_str in dates.items():
        age_days = 0.0
        if date_str:
            try:
                age_days = max(0, (today - datetime.date.fromisoformat(date_str)).days)
            except ValueError:
                pass  # unlesbares Datum -> wie heute gewichtet
        weights[did] = 0.5 ** (age_days / _CORPUS_HALF_LIFE_DAYS)

    total_w = sum(weights.values())
    n_decks = len(members)
    card_w: dict[int, float] = {}
    pair_w: dict[tuple[int, int], float] = {}
    pair_n: dict[tuple[int, int], int] = {}
    for did, cards in members.items():
        w = weights[did]
        for c in cards:
            card_w[c] = card_w.get(c, 0.0) + w
        for a, b in itertools.combinations(sorted(cards), 2):
            pair_w[(a, b)] = pair_w.get((a, b), 0.0) + w
            pair_n[(a, b)] = pair_n.get((a, b), 0) + 1

    edges: dict[tuple[int, int], dict] = {}
    for (a, b), w_ab in pair_w.items():
        if pair_n[(a, b)] < _CORPUS_MIN_DECKS:
            continue
        ppmi = max(0.0, math.log(w_ab * total_w / (card_w[a] * card_w[b])))
        if ppmi > 0:
            edges[(a, b)] = {
                "weight": ppmi, "decks": pair_n[(a, b)], "total": n_decks,
            }
    return edges


def _card_names(conn: sqlite3.Connection, ids) -> dict[int, str]:
    """{id -> Anzeigename (DE bevorzugt)} fuer eine Menge Karten-IDs auf einer
    offenen Verbindung. Leere Menge -> leeres dict (kein Query)."""
    ids = tuple(ids)
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    return {
        r["id"]: r["name"]
        for r in conn.execute(
            f"SELECT id, COALESCE(name_de, name) AS name FROM cards "
            f"WHERE id IN ({ph})",
            ids,
        )
    }


def deck_suggestions(db_path: str, deck_id: int, limit: int = 15) -> dict:
    """Kartenvorschlaege fuer ein Deck aus dem Synergie-Graphen.

    Direkt: jede Kombo, die den Kandidaten enthaelt, traegt einen Punkt je
    Baustein, der schon im Deck (Main+Extra, wie combo_coverage) liegt --
    daraus entsteht zugleich die Begruendung ('zusammen mit X, Y in Kombo Z').
    Transitiv: Brueckenkarten (weder im Deck noch der Kandidat), die sowohl
    mit dem Kandidaten als auch mit >=1 Deck-Karte verbunden sind, gehen mit
    _TRANSITIVE_DISCOUNT ein. Korpus: die staerksten PPMI-Verbindungen des
    Kandidaten zu Deck-Karten (corpus_edges, max. _CORPUS_TOP_LINKS) gehen
    mit _CORPUS_WEIGHT ein -- Kombo-Kanten bleiben die praezise, hoeher
    gewichtete Quelle. Kandidaten sind nur Karten, die in KEINER Zone des
    Decks liegen. Die Luecken-Rolle (gap_role = Rolle mit den wenigsten
    Kopien im Main Deck) manipuliert keine Scores, sie steuert nur die
    Gruppierung in der Anzeige.
    Rueckgabe: {'gap_role', 'role_copies', 'suggestions': [{'card_id',
    'name', 'score', 'direct', 'bridges', 'roles', 'reasons',
    'corpus': [{'name', 'decks', 'weight'}, ...], 'corpus_total'}, ...]},
    Score-sortiert, auf 'limit' gekappt."""
    with _conn(db_path) as conn:
        deck_rows = conn.execute(
            "SELECT card_id, zone FROM deck_cards WHERE deck_id = ?",
            (deck_id,),
        ).fetchall()
        combo_rows = conn.execute(
            """SELECT cc.combo_id, cb.name AS combo_name, cc.card_id, cc.role
               FROM combo_cards cc JOIN combos cb ON cb.combo_id = cc.combo_id
               WHERE cb.parent_combo_id IS NULL"""
        ).fetchall()
        card_ids = {r["card_id"] for r in combo_rows}
        names = _card_names(conn, card_ids)

    in_deck_any = {r["card_id"] for r in deck_rows}
    deck_set = {r["card_id"] for r in deck_rows if r["zone"] in ("main", "extra")}

    # Kombo-Mitgliedschaften, Rollen und Direkt-Score samt Begruendung.
    combo_members: dict[int, set[int]] = {}
    combo_names: dict[int, str] = {}
    roles: dict[int, set[str]] = {}
    for r in combo_rows:
        combo_members.setdefault(r["combo_id"], set()).add(r["card_id"])
        combo_names[r["combo_id"]] = r["combo_name"]
        if r["role"]:
            roles.setdefault(r["card_id"], set()).add(r["role"])

    direct: dict[int, int] = {}
    reasons: dict[int, list[dict]] = {}
    for combo_id in sorted(combo_members):
        members = combo_members[combo_id]
        overlap = members & deck_set
        if not overlap:
            continue
        for c in members - in_deck_any:
            direct[c] = direct.get(c, 0) + len(overlap)
            reasons.setdefault(c, []).append({
                "combo_id": combo_id,
                "combo_name": combo_names[combo_id],
                "with": sorted(names.get(d, str(d)) for d in overlap),
            })

    # Adjazenz fuer den Zwei-Hop-Anteil (Brueckenkarten).
    adj: dict[int, set[int]] = {}
    for (a, b), _e in synergy_edges(db_path).items():
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    # Korpus-Kanten (Co-Occurrence der Referenz-Decks) in den Graphen mischen.
    corpus_adj: dict[int, dict[int, dict]] = {}
    corpus_total = 0
    for (a, b), e in corpus_edges(db_path).items():
        corpus_adj.setdefault(a, {})[b] = e
        corpus_adj.setdefault(b, {})[a] = e
        corpus_total = e["total"]

    candidates = (set(adj) | set(corpus_adj)) - in_deck_any
    bridges: dict[int, list[int]] = {}
    for c in candidates:
        bridges[c] = sorted(
            m for m in adj.get(c, ())
            if m not in in_deck_any and adj.get(m, set()) & deck_set
        )

    # Namen fuer Karten nachladen, die nur im Korpus vorkommen.
    missing = set(corpus_adj) - set(names)
    if missing:
        with _conn(db_path) as conn:
            names.update(_card_names(conn, missing))

    suggestions = []
    for c in candidates:
        links = sorted(
            (
                {"card_id": d, "name": names.get(d, str(d)),
                 "weight": e["weight"], "decks": e["decks"]}
                for d, e in corpus_adj.get(c, {}).items() if d in deck_set
            ),
            key=lambda l: -l["weight"],
        )[:_CORPUS_TOP_LINKS]
        corpus_score = _CORPUS_WEIGHT * sum(l["weight"] for l in links)
        n_bridges = len(bridges.get(c, []))
        score = direct.get(c, 0) + _TRANSITIVE_DISCOUNT * n_bridges + corpus_score
        if score <= 0:
            continue
        suggestions.append({
            "card_id": c,
            "name": names.get(c, str(c)),
            "score": score,
            "direct": direct.get(c, 0),
            "bridges": [names.get(m, str(m)) for m in bridges.get(c, [])],
            "roles": sorted(roles.get(c, ())),
            "reasons": reasons.get(c, []),
            "corpus": links,
            "corpus_total": corpus_total,
        })
    suggestions.sort(key=lambda s: (-s["score"], s["name"]))

    role_copies = deck_role_copies(db_path, deck_id)
    gap_role = min(COMBO_ROLES, key=lambda r: role_copies.get(r, 0))
    return {
        "gap_role": gap_role,
        "role_copies": role_copies,
        "suggestions": suggestions[:limit],
    }
