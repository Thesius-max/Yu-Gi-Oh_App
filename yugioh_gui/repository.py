"""Datenzugriff der GUI: kapselt alle SQL-Abfragen, die die UI braucht.

Einzige Stelle der GUI mit eigenem SQL (Whitelist gegen Spalten aus
Benutzereingaben); alles andere laeuft ueber die yugioh_db-Fassade.
"""

from __future__ import annotations

import os

import yugioh_db as ydb


# ---------------------------------------------------------------------------
# Datenzugriff -- kapselt alle SQL-Abfragen, die die UI braucht
# ---------------------------------------------------------------------------

# Monster-Merkmale fuer den Suchfilter: (Schluessel, Beschriftung, SQL).
TRAITS = (
    ("tuner", "Empfänger (Tuner)", "c.type LIKE '%Tuner%'"),
    ("flip", "Flipp", "c.type LIKE '%Flip%'"),
    ("pendulum", "Pendel", "c.frame_type LIKE '%pendulum%'"),
    ("ritual", "Ritual", "c.frame_type LIKE 'ritual%'"),
    ("gemini", "Zwilling (Gemini)", "c.type LIKE '%Gemini%'"),
    ("spirit", "Spirit", "c.type LIKE '%Spirit%'"),
    ("union", "Union", "c.type LIKE '%Union%'"),
    ("toon", "Toon", "c.type LIKE '%Toon%'"),
    ("normal", "ohne Effekt (Normal)", "c.frame_type IN ('normal', 'normal_pendulum')"),
    ("extra", "Extra Deck", "c.frame_type IN ('fusion','synchro','xyz','link',"
     "'fusion_pendulum','synchro_pendulum','xyz_pendulum')"),
)
TRAIT_SQL = {key: sql for key, _label, sql in TRAITS}


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
        type: str | None = None,
        attribute: str | None = None,
        archetype: str | None = None,
        level: int | None = None,
        atk_min: int = 0,
        atk_max: int = 0,
        race: str | None = None,
        trait: str | None = None,
        link_value: int | None = None,
        def_min: int = 0,
        def_max: int = 0,
        wording: str | None = None,
        only_collection: bool = False,
        limit: int = 300,
    ) -> list:
        """Kartensuche: Volltext (FTS) plus Filter. 'trait' = Monster-
        Merkmal (Schluessel aus TRAIT_SQL), 'wording' = Wortlaut-Merkmal
        (yugioh_db.WORDING_FILTERS; setzt ensure_wording_flags voraus)."""
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
        if race:
            clauses.append("c.race = ?"); params.append(race)
        if trait:
            clauses.append(TRAIT_SQL[trait])
        if link_value:
            clauses.append("c.link_value = ?"); params.append(link_value)
        if def_min > 0:
            clauses.append("c.def >= ?"); params.append(def_min)
        if def_max > 0:
            clauses.append("c.def <= ?"); params.append(def_max)
        if wording:
            clauses.append("c.wording_flags LIKE ?"); params.append(f"%,{wording},%")
        if only_collection:
            clauses.append(
                "EXISTS (SELECT 1 FROM collection col WHERE col.card_id = c.id)"
            )

        sql = base + "".join(" AND " + c for c in clauses)
        sql += (" ORDER BY cards_fts.rank LIMIT ?" if text.strip()
                else " ORDER BY COALESCE(c.name_de, c.name) LIMIT ?")
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

    def get_card(self, card_id: int):
        conn = ydb._connect(self.db_path)
        try:
            return conn.execute(
                "SELECT * FROM cards WHERE id = ?", (card_id,)
            ).fetchone()
        finally:
            conn.close()
