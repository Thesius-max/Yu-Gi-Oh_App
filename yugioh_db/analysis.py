"""Konsistenz-Mathematik, Starthand-Simulation und Synergie-Graph.

Exakte Hypergeometrie (math.comb), Rollen-Kopien je Deck, Zufallshand
(RNG injizierbar), Kombo-Kanten (synergy_edges) + PPMI-normierte
Korpus-Kanten (corpus_edges) und die daraus gemischte Vorschlags-Engine
(deck_suggestions) -- erklaerbar statt opak, jede Empfehlung traegt
ihre Begruendung.
"""

from __future__ import annotations

import datetime
import itertools
import math
import random
import sqlite3
from typing import Iterable, Optional

from .combos import COMBO_ROLES
from .decks import MAX_COPIES, deck_counts
from .schema import _conn


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


def deck_what_if(
    db_path: str, deck_id: int, hand_sizes: Iterable[int] = (5, 6)
) -> dict:
    """Was-waere-wenn je Main-Deck-Karte: wie veraendern +1/-1 Kopie die
    Starthand-Kennzahlen P(>=1 Starter) und P(>=1 Handtrap)?

    Beantwortet die haeufigste Deckbau-Frage ('Wo lohnt die dritte Kopie?')
    exakt statt gefuehlt. Auch rollenlose Karten bewegen die Werte -- +1
    verduennt das Deck (Nenner waechst), -1 verdichtet es. Brick ist
    1 - Starter (rechnet der Aufrufer). 'plus' ist None, wenn die Karte
    schon am 3-Kopien-Limit liegt (MAX_COPIES).

    Rueckgabe: {'deck_size', 'base': {hand: {'starter', 'handtrap'}},
    'cards': [{'card_id', 'name', 'copies', 'roles',
               'plus': {hand: {...}} | None, 'minus': {hand: {...}}}]}."""
    size = deck_counts(db_path, deck_id)["main"]
    roles = deck_role_copies(db_path, deck_id)
    hand_sizes = tuple(hand_sizes)

    def metrics(n: int, role_copies: dict[str, int]) -> dict:
        return {
            hand: {
                "starter": hypergeom_at_least(
                    n, role_copies.get("starter", 0), hand),
                "handtrap": hypergeom_at_least(
                    n, role_copies.get("handtrap", 0), hand),
            }
            for hand in hand_sizes
        }

    def shifted(card_roles: list[str], delta: int) -> dict[str, int]:
        out = dict(roles)
        for r in card_roles:
            out[r] = out.get(r, 0) + delta
        return out

    cards = []
    for c in deck_main_cards(db_path, deck_id):
        plus = None
        if c["copies"] < MAX_COPIES:
            plus = metrics(size + 1, shifted(c["roles"], +1))
        minus = metrics(size - 1, shifted(c["roles"], -1))
        cards.append({
            "card_id": c["card_id"], "name": c["name"],
            "copies": c["copies"], "roles": c["roles"],
            "plus": plus, "minus": minus,
        })
    return {"deck_size": size, "base": metrics(size, roles), "cards": cards}


def deck_line_playability(
    db_path: str, deck_id: int, hand_sizes: Iterable[int] = (5, 6)
) -> dict[int, dict]:
    """Startbarkeit je Kombo-Hauptlinie: P(>=1 der als 'starter'
    eingestuften Bausteine DIESER Linie in der Starthand). Zaehlt nur
    Main-Deck-Kopien (nur daraus wird gezogen); Varianten (Branches)
    bekommen keinen Eintrag (Branch-Modell: nur Hauptlinien in Aggregaten).

    Rueckgabe je Hauptlinie: {combo_id: {'starters': verschiedene
    Starter-Bausteine der Kombo, 'copies': Summe ihrer Main-Kopien,
    'hands': {hand: p}}}. starters == 0 heisst: kein Baustein als Starter
    eingestuft -- die Anzeige zeigt dann keinen Wert statt 0 %."""
    size = deck_counts(db_path, deck_id)["main"]
    hand_sizes = tuple(hand_sizes)
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT cb.combo_id,
                      COUNT(cc.card_id) AS starters,
                      COALESCE(SUM((SELECT dc.quantity FROM deck_cards dc
                                    WHERE dc.deck_id = ? AND dc.zone = 'main'
                                      AND dc.card_id = cc.card_id)), 0) AS copies
               FROM combos cb
               LEFT JOIN combo_cards cc
                 ON cc.combo_id = cb.combo_id AND cc.role = 'starter'
               WHERE cb.parent_combo_id IS NULL
               GROUP BY cb.combo_id""",
            (deck_id,),
        ).fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        copies = int(r["copies"])
        out[r["combo_id"]] = {
            "starters": int(r["starters"]),
            "copies": copies,
            "hands": {
                hand: hypergeom_at_least(size, copies, hand)
                for hand in hand_sizes
            },
        }
    return out


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
