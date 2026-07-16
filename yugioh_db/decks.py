"""Decks: CRUD, Zonen, Bestands-Abgleich, Regeln, .ydk-Import/-Export.

Die 3-Kopien-Regel wird hier erzwungen (ueber alle Zonen zusammen);
Referenz-Decks (kind='reference', Meta-Korpus) binden keinen Bestand.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from .cards import deck_zone_for
from .schema import _conn


# ---------------------------------------------------------------------------
# Deckbuilding
# ---------------------------------------------------------------------------

MAIN_MIN, MAIN_MAX = 40, 60
EXTRA_MAX = SIDE_MAX = 15
MAX_COPIES = 3


def create_deck(
    db_path: str, name: str, kind: Optional[str] = None,
    source: Optional[str] = None, format_date: Optional[str] = None,
) -> int:
    """kind='reference' legt ein Referenz-Deck (Korpus) an; Standard ist
    ein eigenes Deck (kind NULL)."""
    with _conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO decks (name, kind, source, format_date) "
            "VALUES (?,?,?,?)",
            (name, kind, source, format_date),
        )
        conn.commit()
        return cur.lastrowid


def list_decks(db_path: str) -> list[sqlite3.Row]:
    """Nur eigene Decks -- Referenz-Decks (Korpus) bleiben bewusst draussen,
    damit Deck-Auswahl, Heimat-Deck und Filter sie nie anbieten."""
    with _conn(db_path) as conn:
        return conn.execute(
            "SELECT deck_id, name FROM decks WHERE kind IS NULL ORDER BY name"
        ).fetchall()


def list_reference_decks(db_path: str) -> list[sqlite3.Row]:
    """Der Korpus: importierte Meta-Listen mit Quelle, Stand und Kartenzahl,
    neueste zuerst (Listen ohne Datum zuletzt)."""
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT d.deck_id, d.name, d.source, d.format_date,
                      COALESCE((SELECT SUM(dc.quantity) FROM deck_cards dc
                                WHERE dc.deck_id = d.deck_id), 0) AS cards
               FROM decks d WHERE d.kind = 'reference'
               ORDER BY d.format_date IS NULL, d.format_date DESC, d.name"""
        ).fetchall()


def delete_deck(db_path: str, deck_id: int) -> None:
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM decks WHERE deck_id = ?", (deck_id,))
        conn.commit()


def deck_cards(db_path: str, deck_id: int, zone: str) -> list[sqlite3.Row]:
    with _conn(db_path) as conn:
        return conn.execute(
            """SELECT dc.card_id, COALESCE(c.name_de, c.name) AS name,
                      c.type, c.frame_type, dc.quantity
               FROM deck_cards dc
               JOIN cards c ON c.id = dc.card_id
               WHERE dc.deck_id = ? AND dc.zone = ?
               ORDER BY COALESCE(c.name_de, c.name)""",
            (deck_id, zone),
        ).fetchall()


def _total_copies(conn: sqlite3.Connection, deck_id: int, card_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS n FROM deck_cards "
        "WHERE deck_id = ? AND card_id = ?",
        (deck_id, card_id),
    ).fetchone()
    return int(row["n"])


def add_card_to_deck(
    db_path: str, deck_id: int, card_id: int,
    zone: Optional[str] = None, count: int = 1,
) -> tuple[int, str]:
    """Fuegt Kopien hinzu. zone=None -> automatische Zuordnung (main/extra).
    Beachtet die 3-Kopien-Regel und die Zonen-Logik.
    Rueckgabe: (tatsaechlich hinzugefuegt, Hinweistext)."""
    with _conn(db_path) as conn:
        card = conn.execute(
            "SELECT type, frame_type FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        if card is None:
            return (0, "Karte nicht gefunden.")
        natural = deck_zone_for(card["frame_type"], card["type"])
        if zone is None:
            zone = natural
        if zone not in ("main", "extra", "side"):
            return (0, "Unbekannte Zone.")
        if zone != "side" and zone != natural:
            target = "Extra Deck" if natural == "extra" else "Main Deck"
            return (0, f"Diese Karte gehört ins {target}.")

        allowed = max(0, MAX_COPIES - _total_copies(conn, deck_id, card_id))
        add = min(count, allowed)
        if add <= 0:
            return (0, f"Maximal {MAX_COPIES} Kopien je Karte erreicht.")

        existing = conn.execute(
            "SELECT quantity FROM deck_cards "
            "WHERE deck_id = ? AND card_id = ? AND zone = ?",
            (deck_id, card_id, zone),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE deck_cards SET quantity = quantity + ? "
                "WHERE deck_id = ? AND card_id = ? AND zone = ?",
                (add, deck_id, card_id, zone),
            )
        else:
            conn.execute(
                "INSERT INTO deck_cards (deck_id, card_id, zone, quantity) "
                "VALUES (?,?,?,?)",
                (deck_id, card_id, zone, add),
            )
        conn.commit()
        msg = "" if add == count else f"Nur {add} hinzugefügt ({MAX_COPIES}-Kopien-Grenze)."
        return (add, msg)


def change_deck_quantity(
    db_path: str, deck_id: int, card_id: int, zone: str, delta: int
) -> None:
    """Erhoeht/senkt die Menge in einer Zone. Auf 0 -> Eintrag entfernt.
    Erhoehung beachtet die 3-Kopien-Regel ueber alle Zonen."""
    with _conn(db_path) as conn:
        if delta > 0:
            allowed = MAX_COPIES - _total_copies(conn, deck_id, card_id)
            if allowed <= 0:
                return
            delta = min(delta, allowed)
        row = conn.execute(
            "SELECT quantity FROM deck_cards "
            "WHERE deck_id = ? AND card_id = ? AND zone = ?",
            (deck_id, card_id, zone),
        ).fetchone()
        if row is None:
            return
        new = row["quantity"] + delta
        if new <= 0:
            conn.execute(
                "DELETE FROM deck_cards "
                "WHERE deck_id = ? AND card_id = ? AND zone = ?",
                (deck_id, card_id, zone),
            )
        else:
            conn.execute(
                "UPDATE deck_cards SET quantity = ? "
                "WHERE deck_id = ? AND card_id = ? AND zone = ?",
                (new, deck_id, card_id, zone),
            )
        conn.commit()


def remove_deck_card(db_path: str, deck_id: int, card_id: int, zone: str) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            "DELETE FROM deck_cards "
            "WHERE deck_id = ? AND card_id = ? AND zone = ?",
            (deck_id, card_id, zone),
        )
        conn.commit()


def move_deck_card(
    db_path: str, deck_id: int, card_id: int,
    from_zone: str, to_zone: str, count: int = 1,
) -> tuple[int, str]:
    """Verschiebt Kopien zwischen Zonen (z.B. Main<->Side). Prueft die Zonen-Logik.
    Die Gesamtzahl bleibt gleich, die 3-Kopien-Regel ist daher nicht betroffen."""
    with _conn(db_path) as conn:
        card = conn.execute(
            "SELECT type, frame_type FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        if card is None:
            return (0, "Karte nicht gefunden.")
        natural = deck_zone_for(card["frame_type"], card["type"])
        if to_zone != "side" and to_zone != natural:
            return (0, "Zielzone passt nicht zum Kartentyp.")
        src = conn.execute(
            "SELECT quantity FROM deck_cards "
            "WHERE deck_id = ? AND card_id = ? AND zone = ?",
            (deck_id, card_id, from_zone),
        ).fetchone()
        if src is None:
            return (0, "")
        move = min(count, src["quantity"])
        if src["quantity"] - move <= 0:
            conn.execute(
                "DELETE FROM deck_cards "
                "WHERE deck_id = ? AND card_id = ? AND zone = ?",
                (deck_id, card_id, from_zone),
            )
        else:
            conn.execute(
                "UPDATE deck_cards SET quantity = quantity - ? "
                "WHERE deck_id = ? AND card_id = ? AND zone = ?",
                (move, deck_id, card_id, from_zone),
            )
        dst = conn.execute(
            "SELECT quantity FROM deck_cards "
            "WHERE deck_id = ? AND card_id = ? AND zone = ?",
            (deck_id, card_id, to_zone),
        ).fetchone()
        if dst:
            conn.execute(
                "UPDATE deck_cards SET quantity = quantity + ? "
                "WHERE deck_id = ? AND card_id = ? AND zone = ?",
                (move, deck_id, card_id, to_zone),
            )
        else:
            conn.execute(
                "INSERT INTO deck_cards (deck_id, card_id, zone, quantity) "
                "VALUES (?,?,?,?)",
                (deck_id, card_id, to_zone, move),
            )
        conn.commit()
        return (move, "")


def deck_availability(db_path: str, deck_id: int) -> list[dict]:
    """Sammlung<->Deck-Abgleich fuer ein Deck. Karten liegen physisch vor:
    Kopien, die in anderen Decks stecken, stehen diesem Deck nicht zur
    Verfuegung. Je Karte im Deck (alle Zonen):
      in_deck   -- Kopien in diesem Deck
      owned     -- Kopien im Bestand (alle Drucke zusammen)
      elsewhere -- Kopien, die andere Decks binden
      missing   -- fuer dieses Deck fehlende Kopien
    Nur Hinweis-Charakter: das Deckbuilding wird nicht blockiert (geplante
    Kaeufe / Proxies bleiben moeglich)."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT dc.card_id, c.name, c.name_de,
                      SUM(dc.quantity) AS in_deck,
                      COALESCE((SELECT SUM(col.quantity) FROM collection col
                                WHERE col.card_id = dc.card_id), 0) AS owned,
                      COALESCE((SELECT SUM(o.quantity) FROM deck_cards o
                                JOIN decks od ON od.deck_id = o.deck_id
                                WHERE o.card_id = dc.card_id
                                  AND o.deck_id != dc.deck_id
                                  AND od.kind IS NULL), 0) AS elsewhere
               FROM deck_cards dc JOIN cards c ON c.id = dc.card_id
               WHERE dc.deck_id = ?
               GROUP BY dc.card_id
               ORDER BY COALESCE(c.name_de, c.name)""",
            (deck_id,),
        ).fetchall()
    out = []
    for r in rows:
        available = max(0, r["owned"] - r["elsewhere"])
        out.append({
            "card_id": r["card_id"],
            "name": r["name_de"] or r["name"],
            "in_deck": r["in_deck"],
            "owned": r["owned"],
            "elsewhere": r["elsewhere"],
            "missing": max(0, r["in_deck"] - available),
        })
    return out


def card_bound_in_decks(db_path: str, card_id: int) -> int:
    """Wie viele Kopien einer Karte ueber alle EIGENEN Decks zusammen
    verplant sind (Referenz-Decks binden keinen physischen Bestand)."""
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(dc.quantity), 0) AS n FROM deck_cards dc "
            "JOIN decks d ON d.deck_id = dc.deck_id "
            "WHERE dc.card_id = ? AND d.kind IS NULL",
            (card_id,),
        ).fetchone()
        return int(row["n"])


def deck_counts(db_path: str, deck_id: int) -> dict:
    """Kartenzahl je Zone als {'main': n, 'extra': n, 'side': n}."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT zone, COALESCE(SUM(quantity), 0) AS n FROM deck_cards "
            "WHERE deck_id = ? GROUP BY zone",
            (deck_id,),
        ).fetchall()
        counts = {"main": 0, "extra": 0, "side": 0}
        for r in rows:
            counts[r["zone"]] = int(r["n"])
        return counts


def validate_deck(db_path: str, deck_id: int) -> list[tuple[bool, str]]:
    """Prueft die offiziellen Grundregeln. Rueckgabe: Liste von (ok, Text)."""
    counts = deck_counts(db_path, deck_id)
    m, e, s = counts["main"], counts["extra"], counts["side"]
    checks = [
        (MAIN_MIN <= m <= MAIN_MAX, f"Main {m} ({MAIN_MIN}-{MAIN_MAX})"),
        (e <= EXTRA_MAX, f"Extra {e} (max. {EXTRA_MAX})"),
        (s <= SIDE_MAX, f"Side {s} (max. {SIDE_MAX})"),
    ]
    with _conn(db_path) as conn:
        over = conn.execute(
            """SELECT COALESCE(c.name_de, c.name) AS name, SUM(dc.quantity) AS n
               FROM deck_cards dc JOIN cards c ON c.id = dc.card_id
               WHERE dc.deck_id = ?
               GROUP BY dc.card_id HAVING n > ?""",
            (deck_id, MAX_COPIES),
        ).fetchall()
    for r in over:
        checks.append((False, f"{r['name']}: {r['n']} Kopien"))
    return checks


# ---------------------------------------------------------------------------
# Deck-Import/-Export (.ydk)
# ---------------------------------------------------------------------------
# .ydk ist das YGOPro-Format: je Zeile ein Passcode, Abschnitte "#main",
# "#extra" und "!side"; weitere "#..."-Zeilen sind Kommentare. Die Passcodes
# sind identisch mit unseren Karten-IDs, daher braucht es kein Mapping.

def export_deck_ydk(db_path: str, deck_id: int) -> str:
    """Serialisiert ein Deck als .ydk-Text (je Kopie eine Zeile)."""
    with _conn(db_path) as conn:
        deck = conn.execute(
            "SELECT name FROM decks WHERE deck_id = ?", (deck_id,)
        ).fetchone()
        if deck is None:
            raise ValueError(f"Deck {deck_id} nicht gefunden.")
        lines = [f"#created by YugiohSammlung - {deck['name']}"]
        for zone, header in (("main", "#main"), ("extra", "#extra"), ("side", "!side")):
            lines.append(header)
            rows = conn.execute(
                """SELECT dc.card_id, dc.quantity
                   FROM deck_cards dc JOIN cards c ON c.id = dc.card_id
                   WHERE dc.deck_id = ? AND dc.zone = ?
                   ORDER BY COALESCE(c.name_de, c.name)""",
                (deck_id, zone),
            ).fetchall()
            for r in rows:
                lines.extend([str(r["card_id"])] * r["quantity"])
        return "\n".join(lines) + "\n"


def parse_ydk(text: str) -> dict[str, list[int]]:
    """Zerlegt .ydk-Text in {'main': [ids...], 'extra': [...], 'side': [...]}.
    Jede Kopie steht einzeln in der Liste. Unbekannte Zeilen werden ignoriert."""
    zones: dict[str, list[int]] = {"main": [], "extra": [], "side": []}
    current = "main"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        if lower in ("#main", "#extra"):
            current = lower[1:]
        elif lower == "!side":
            current = "side"
        elif line.startswith(("#", "!")):
            continue  # Kommentar (z.B. "#created by ...")
        elif line.isdigit():
            zones[current].append(int(line))
    return zones


def import_deck_ydk(
    db_path: str, name: str, text: str, kind: Optional[str] = None,
    source: Optional[str] = None, format_date: Optional[str] = None,
) -> tuple[Optional[int], dict]:
    """Legt aus .ydk-Text ein neues Deck an. Erzwingt die 3-Kopien-Regel und
    korrigiert Main/Extra anhand des Kartentyps (Side bleibt Side).
    kind='reference' (plus source/format_date) importiert in den Korpus
    statt in die eigenen Decks.

    Rueckgabe: (deck_id, report). deck_id ist None, wenn keine einzige Karte
    importiert werden konnte (dann wird auch kein Deck angelegt). report:
      imported  -- importierte Kopien je Zone {'main': n, ...}
      unknown   -- Passcodes ohne Karte in der DB (DB veraltet/fremde Karte)
      capped    -- Kartennamen, bei denen Kopien ueber der 3er-Grenze wegfielen
      moved     -- Kartennamen, die in die zum Typ passende Zone wandern mussten
    """
    zones = parse_ydk(text)
    report: dict = {
        "imported": {"main": 0, "extra": 0, "side": 0},
        "unknown": [], "capped": [], "moved": [],
    }
    with _conn(db_path) as conn:
        # Kopien je (zone, card_id) zaehlen; Zone ggf. korrigieren.
        counts: dict[tuple[str, int], int] = {}
        cards: dict[int, sqlite3.Row] = {}
        for zone, ids in zones.items():
            for cid in ids:
                if cid not in cards:
                    row = conn.execute(
                        "SELECT name, type, frame_type FROM cards WHERE id = ?",
                        (cid,),
                    ).fetchone()
                    cards[cid] = row
                card = cards[cid]
                if card is None:
                    if cid not in report["unknown"]:
                        report["unknown"].append(cid)
                    continue
                target = zone
                if zone != "side":
                    natural = deck_zone_for(card["frame_type"], card["type"])
                    if natural != zone:
                        target = natural
                        if card["name"] not in report["moved"]:
                            report["moved"].append(card["name"])
                counts[(target, cid)] = counts.get((target, cid), 0) + 1

        # 3-Kopien-Grenze ueber alle Zonen zusammen durchsetzen.
        totals: dict[int, int] = {}
        for (zone, cid), n in sorted(counts.items()):
            have = totals.get(cid, 0)
            keep = min(n, max(0, MAX_COPIES - have))
            totals[cid] = have + keep
            if keep < n and cards[cid]["name"] not in report["capped"]:
                report["capped"].append(cards[cid]["name"])
            counts[(zone, cid)] = keep

        if not any(n > 0 for n in counts.values()):
            return (None, report)

        cur = conn.execute(
            "INSERT INTO decks (name, kind, source, format_date) "
            "VALUES (?,?,?,?)",
            (name, kind, source, format_date),
        )
        deck_id = cur.lastrowid
        for (zone, cid), n in counts.items():
            if n <= 0:
                continue
            conn.execute(
                "INSERT INTO deck_cards (deck_id, card_id, zone, quantity) "
                "VALUES (?,?,?,?)",
                (deck_id, cid, zone, n),
            )
            report["imported"][zone] += n
        conn.commit()
        return (deck_id, report)


def deck_play_lists(db_path: str, deck_id: int) -> dict:
    """Karten fuer den Spielfeld-Test: Main- und Extra-Deck je als nach Kopien
    expandierte card_id-Liste (jede Kopie = ein ziehbares Exemplar) plus
    Anzeigenamen. Side bleibt aussen vor (es wird aus dem Main gezogen, das
    Extra ist sein eigener Stapel). Rueckgabe:
      {'main': [card_id, ...], 'extra': [card_id, ...], 'names': {id: name}}."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT dc.card_id, dc.zone, dc.quantity,
                      COALESCE(c.name_de, c.name) AS name
               FROM deck_cards dc JOIN cards c ON c.id = dc.card_id
               WHERE dc.deck_id = ? AND dc.zone IN ('main', 'extra')""",
            (deck_id,),
        ).fetchall()
    main: list[int] = []
    extra: list[int] = []
    names: dict[int, str] = {}
    for r in rows:
        names[r["card_id"]] = r["name"]
        target = main if r["zone"] == "main" else extra
        target.extend([r["card_id"]] * int(r["quantity"]))
    return {"main": main, "extra": extra, "names": names}


def _deck_zone_cards(conn: sqlite3.Connection, deck_id: int) -> dict[int, dict]:
    """{card_id: {'name', 'copies'}} fuer Main+Extra eines Decks (Kopien je
    Karte zusammengezaehlt) -- Basis fuer den Korpus-Vergleich."""
    rows = conn.execute(
        """SELECT dc.card_id, COALESCE(c.name_de, c.name) AS name,
                  SUM(dc.quantity) AS copies
           FROM deck_cards dc JOIN cards c ON c.id = dc.card_id
           WHERE dc.deck_id = ? AND dc.zone IN ('main', 'extra')
           GROUP BY dc.card_id""",
        (deck_id,),
    ).fetchall()
    return {
        r["card_id"]: {"name": r["name"], "copies": int(r["copies"])}
        for r in rows
    }


def deck_corpus_diff(db_path: str, deck_id: int, ref_deck_id: int) -> dict:
    """Vergleicht das eigene Deck kopiengenau mit einer Referenz-/Korpus-Liste
    (jeweils Main+Extra, wie combo_coverage/corpus_edges; Side bleibt aussen
    vor). Rueckgabe:
      {'ref_name', 'my_total', 'ref_total', 'ref_cards', 'shared_cards',
       'missing': [{card_id, name, mine, theirs, diff}],  # Referenz hat mehr
       'extra':   [{card_id, name, mine, theirs, diff}]}  # du hast mehr
    'missing'/'extra' nach Differenz absteigend, dann Name."""
    with _conn(db_path) as conn:
        ref = conn.execute(
            "SELECT name FROM decks WHERE deck_id = ?", (ref_deck_id,)
        ).fetchone()
        if ref is None:
            raise ValueError(f"Referenz-Deck {ref_deck_id} nicht gefunden.")
        mine = _deck_zone_cards(conn, deck_id)
        theirs = _deck_zone_cards(conn, ref_deck_id)
    missing, extra = [], []
    for cid in set(mine) | set(theirs):
        m = mine.get(cid, {}).get("copies", 0)
        t = theirs.get(cid, {}).get("copies", 0)
        name = (theirs.get(cid) or mine.get(cid))["name"]
        if t > m:
            missing.append({"card_id": cid, "name": name,
                            "mine": m, "theirs": t, "diff": t - m})
        elif m > t:
            extra.append({"card_id": cid, "name": name,
                          "mine": m, "theirs": t, "diff": m - t})
    missing.sort(key=lambda x: (-x["diff"], x["name"]))
    extra.sort(key=lambda x: (-x["diff"], x["name"]))
    return {
        "ref_name": ref["name"],
        "my_total": sum(c["copies"] for c in mine.values()),
        "ref_total": sum(c["copies"] for c in theirs.values()),
        "ref_cards": len(theirs),
        "shared_cards": len(set(mine) & set(theirs)),
        "missing": missing,
        "extra": extra,
    }
