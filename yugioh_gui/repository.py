"""Datenzugriff der GUI: kapselt alle SQL-Abfragen, die die UI braucht.

Einzige Stelle der GUI mit eigenem SQL (Whitelist gegen Spalten aus
Benutzereingaben); alles andere laeuft ueber die yugioh_db-Fassade.
"""

from __future__ import annotations

import os
import re

import yugioh_db as ydb


# ---------------------------------------------------------------------------
# Datenzugriff -- kapselt alle SQL-Abfragen, die die UI braucht
# ---------------------------------------------------------------------------

# Suchfilter nach dem Aufbau der Karte. Innerhalb einer Gruppe gilt ODER
# (Synchro oder Xyz), zwischen den Gruppen UND (Synchro und FINSTERNIS).
KIND_SQL = {
    "monster": "c.type LIKE '%Monster%'",
    "spell": "c.type = 'Spell Card'",
    "trap": "c.type = 'Trap Card'",
}
# Monsterart = Rahmen der Karte ('effect' ist der orange Rahmen, nicht
# "hat einen Effekt" -- Fusions-/Synchro-/... Monster haben eigene Rahmen).
FRAME_SQL = {
    "normal": "c.frame_type IN ('normal', 'normal_pendulum')",
    "effect": "c.frame_type IN ('effect', 'effect_pendulum')",
    "ritual": "c.frame_type LIKE 'ritual%'",
    "fusion": "c.frame_type LIKE 'fusion%'",
    "synchro": "c.frame_type LIKE 'synchro%'",
    "xyz": "c.frame_type LIKE 'xyz%'",
    "pendulum": "c.frame_type LIKE '%pendulum'",
    "link": "c.frame_type = 'link'",
}
# Merkmale aus der Typzeile (Empfaenger, Flipp, ...).
TRAIT_SQL = {
    "tuner": "c.type LIKE '%Tuner%'",
    "flip": "c.type LIKE '%Flip%'",
    "gemini": "c.type LIKE '%Gemini%'",
    "spirit": "c.type LIKE '%Spirit%'",
    "union": "c.type LIKE '%Union%'",
    "toon": "c.type LIKE '%Toon%'",
}
# Sortierung der Treffertabelle in SQL (sonst saehe man bei 300+ Treffern
# nur die Reihenfolge der ersten 300 nach Name). Fehlende Werte zaehlen wie
# in der Tabelle als -1, Namen casefold -- gleiche Schluessel wie dort.
ORDER_SQL = {
    "name": "casefold(COALESCE(c.name_de, c.name))",
    "level": "COALESCE(CASE WHEN c.frame_type = 'link' THEN c.link_value "
             "ELSE c.level END, -1)",
    "atk": "COALESCE(c.atk, -1)",
    "def": "COALESCE(c.def, -1)",
    "owned": "owned",
}
# Set-Nummer wie auf der Karte (RA01-DE008, LOB-001, L5DD-ENC09).
_SET_CODE_RE = re.compile(r"^[A-Za-z0-9]{2,5}-(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{3,6}$")
_OWNED_SQL = ("(SELECT COALESCE(SUM(col.quantity), 0) FROM collection col "
              "WHERE col.card_id = c.id) AS owned")


def _in_group(sql_map: dict[str, str], keys) -> str | None:
    """ODER-Verknuepfung der gewaehlten Schluessel einer Filtergruppe."""
    parts = [sql_map[k] for k in keys or () if k in sql_map]
    return "(" + " OR ".join(parts) + ")" if parts else None


def is_set_code(text: str) -> bool:
    return bool(_SET_CODE_RE.match(text.strip()))


class CardRepository:
    # Whitelist, damit Spaltennamen nie aus Benutzereingaben kommen.
    _FILTER_COLUMNS = ("type", "attribute", "archetype", "race")

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
        kind: str | None = None,
        frames=(),
        attributes=(),
        race: str | None = None,
        traits=(),
        level_min: int | None = None,
        level_max: int | None = None,
        link_min: int | None = None,
        link_max: int | None = None,
        scale_min: int | None = None,
        scale_max: int | None = None,
        atk_min: int | None = None,
        atk_max: int | None = None,
        def_min: int | None = None,
        def_max: int | None = None,
        st_kinds=(),
        archetype: str | None = None,
        wording: str | None = None,
        only_collection: bool = False,
        order: tuple[str, bool] | None = None,
        limit: int = 300,
    ) -> list:
        """Kartensuche nach dem Aufbau der Karte. Text: Name/Kartentext
        (FTS), zusaetzlich die Kartennummer (Passcode, nur Ziffern) oder
        eine Set-Nummer (RA01-DE008: deutsche Codes werden auf die
        englischen der Kartendaten abgebildet, eigene Drucke zaehlen mit).
        Gruppen (frames, attributes, traits, st_kinds) verknuepfen ODER,
        alle Filter untereinander UND; None/leer = egal. 'wording' setzt
        ensure_wording_flags voraus. Jede Zeile traegt 'owned' (Bestand).
        order = (Schluessel aus ORDER_SQL, absteigend?) ersetzt die
        Standard-Reihenfolge (Name bzw. Relevanz der Textsuche)."""
        clauses: list[str] = []
        params: list = []
        text = (text or "").strip()
        order_default = " ORDER BY COALESCE(c.name_de, c.name) LIMIT ?"
        base_select = f"SELECT c.*, {_OWNED_SQL} FROM cards c"
        if text and is_set_code(text):
            # Set-Nummer: kein Volltext (der zerlegte die Nummer in Woerter).
            base = (base_select + " WHERE (c.id IN (SELECT card_id FROM card_sets "
                    "WHERE set_code = ?) OR c.id IN (SELECT card_id FROM collection "
                    "WHERE casefold(set_code) = ?))")
            params += [ydb.localize_set_code(text.upper(), "EN"), text.casefold()]
        elif text:
            # Benutzereingabe als Phrase + Praefix; Anfuehrungszeichen escapen,
            # damit kein FTS-Syntaxfehler entsteht.
            safe = text.replace('"', '""')
            if text.isdigit():
                # Ziffern: Kartennummer ODER Name/Text ("7" ist auch ein Name).
                base = (base_select + " WHERE (c.id IN (SELECT rowid FROM cards_fts "
                        "WHERE cards_fts MATCH ?) OR c.id = ?)")
                params += [f'"{safe}"*', int(text)]
                order_default = (" ORDER BY (c.id = ?) DESC, "
                                 "COALESCE(c.name_de, c.name) LIMIT ?")
            else:
                base = (f"SELECT c.*, {_OWNED_SQL} FROM cards_fts "
                        "JOIN cards c ON c.id = cards_fts.rowid "
                        "WHERE cards_fts MATCH ?")
                params.append(f'"{safe}"*')
                order_default = " ORDER BY cards_fts.rank LIMIT ?"
        else:
            base = base_select + " WHERE 1=1"

        if kind in KIND_SQL:
            clauses.append(KIND_SQL[kind])
        for group in (_in_group(FRAME_SQL, frames), _in_group(TRAIT_SQL, traits)):
            if group:
                clauses.append(group)
        if attributes:
            clauses.append(f"c.attribute IN ({','.join('?' * len(attributes))})")
            params += list(attributes)
        if st_kinds:
            clauses.append(f"c.race IN ({','.join('?' * len(st_kinds))})")
            params += list(st_kinds)
        if race:
            clauses.append("c.race = ?"); params.append(race)
        if archetype:
            clauses.append("c.archetype = ?"); params.append(archetype)
        for column, lo, hi in (("c.level", level_min, level_max),
                               ("c.link_value", link_min, link_max),
                               ("c.scale", scale_min, scale_max),
                               ("c.atk", atk_min, atk_max),
                               ("c.def", def_min, def_max)):
            if lo is not None:
                clauses.append(f"{column} >= ?"); params.append(lo)
            if hi is not None:
                clauses.append(f"{column} <= ?"); params.append(hi)
        if level_min is not None or level_max is not None:
            clauses.append("c.frame_type != 'link'")      # Link hat keine Stufe
        if wording:
            clauses.append("c.wording_flags LIKE ?"); params.append(f"%,{wording},%")
        if only_collection:
            clauses.append(
                "EXISTS (SELECT 1 FROM collection col WHERE col.card_id = c.id)"
            )

        if order and order[0] in ORDER_SQL:
            direction = "DESC" if order[1] else "ASC"
            order = (f" ORDER BY {ORDER_SQL[order[0]]} {direction}, "
                     f"{ORDER_SQL['name']} LIMIT ?")
        elif text.isdigit() and not is_set_code(text):
            params.append(int(text))            # (c.id = ?) DESC: Passcode zuerst
            order = order_default
        else:
            order = order_default
        sql = base + "".join(" AND " + c for c in clauses) + order
        params.append(limit)

        conn = ydb._connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def monster_races(self) -> list[str]:
        """Typen (Drache, Hexer, ...) der Monster -- ohne die Zauber-/
        Fallen-Arten, die die API ebenfalls in 'race' ablegt."""
        conn = ydb._connect(self.db_path)
        try:
            return [r[0] for r in conn.execute(
                "SELECT DISTINCT race FROM cards WHERE type LIKE '%Monster%' "
                "AND race IS NOT NULL AND race != '' ORDER BY race")]
        finally:
            conn.close()

    def set_count(self, card_id: int) -> int:
        """In wie vielen Sets (verschiedene Set-Nummern) die Karte erschien."""
        conn = ydb._connect(self.db_path)
        try:
            return conn.execute(
                "SELECT COUNT(DISTINCT set_code) FROM card_sets WHERE card_id = ?",
                (card_id,)).fetchone()[0]
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
