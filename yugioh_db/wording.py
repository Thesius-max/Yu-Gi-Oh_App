"""Lesehilfe fuer Kartentexte: zerlegt den (englischen) Kartentext nach dem
standardisierten Wortlaut (Problem-Solving Card Text, PSCT) in Effekte und
markiert Aktivierungsbedingung, Kosten, Ziele, Aufloesung und Einschraenkungen.

Der englische Text ist die Grundlage, weil sein Wortlaut streng genormt und
fuer jede Karte vorhanden ist (deutscher Text fehlt bei einem Teil der
Karten). Die Einteilung ist eine **Heuristik** und erklaert Grammatik --
sie entscheidet keine Spielsituation (Leitprinzip: nichts aus Kartentext
'loesen'). Jede Erklaerung nennt das passende Regelwerk-Kapitel (topic).
Reine Funktionen, keine Importe aus dem Paket.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

WORDING_NOTE = (
    "Lesehilfe: Die Einteilung folgt dem genormten englischen Kartentext "
    "(PSCT) und ist heuristisch — sie ersetzt keine Regelentscheidung. "
    "Im Zweifel gelten die offiziellen Rulings."
)

# Regelwerk-Kapitel (Schluessel in yugioh_gui/rulebook_text.RULEBOOK_SECTIONS).
TOPIC_SUMMON = "beschwoerung"
TOPIC_EFFECTS = "effekte"
TOPIC_CHAIN = "ketten"
TOPIC_TEXT = "kartentext"
TOPIC_RULINGS = "rulings"
TOPIC_CARDS = "kartenarten"

ROLE_LABELS = {
    "condition": "Bedingung", "cost": "Kosten/Ziel", "effect": "Auflösung",
    "limit": "Einschränkung", "plain": "",
}


@dataclass
class Tag:
    label: str          # kurz, z. B. "Schnelleffekt"
    explanation: str    # ein deutscher Satz
    topic: str          # Regelwerk-Kapitel


@dataclass
class Part:
    text: str
    role: str           # condition | cost | effect | limit | plain


@dataclass
class Segment:
    text: str
    kind: str           # siehe KIND_LABELS
    parts: list[Part] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)
    scope: str = ""     # 'Pendeleffekt' / 'Monstereffekt' bei Pendelkarten

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


KIND_LABELS = {
    "materials": "Beschwörungsmaterial",
    "summon_condition": "Beschwörungsbedingung",
    "inherent": "Eingebaute Beschwörung",
    "flip": "Flippeffekt",
    "trigger": "Auslöseeffekt",
    "quick": "Schnelleffekt",
    "ignition": "Zündeffekt",
    "activation": "Aktivierung der Karte",
    "continuous": "Dauereffekt",
    "limit": "Einschränkung",
    "note": "Hinweis",
    "rule": "Sonderregel",
    "flavor": "Beschreibung (kein Effekt)",
}

_EXTRA_WORDS = ("Fusion", "Synchro", "XYZ", "Xyz", "Link")
_ACTIVATION_SUBTYPES = {"Normal", "Quick-Play", "Ritual", "Counter"}
_EVENT = re.compile(
    r"\b(?:is|are|was|were|gets?)\s+(?:(?:Normal|Special|Tribute|Flip|Link|"
    r"Synchro|Xyz|Fusion|Ritual|Pendulum)\s+)?(?:Summoned|sent|destroyed|"
    r"banished|added|detached|discarded|Tributed|returned|flipped|targeted|"
    r"used as|attached|Set|revealed|shuffled|excavated)\b"
    r"|\bleaves? the field\b|\bis activated\b|\bactivates?\b|\bdeclares? an attack\b"
    r"|\battacks\b|\binflicts?\b|\btakes? (?:battle )?damage\b|\bresolves?\b"
    r"|\bis (?:Normal|Special) Summoned\b|\bare (?:Normal|Special) Summoned\b"
    r"|\bdraws?\b|\bbattles?\b",
    re.I,
)
_SENTENCE = re.compile(r"(?<=[.!])\s+(?=[A-Z(\"●])")


def _split_outside_quotes(text: str, sep: str) -> tuple[str, str] | None:
    """(vor, nach) am ersten 'sep' ausserhalb von "…" (Kartennamen)."""
    inside = False
    for i, ch in enumerate(text):
        if ch == '"':
            inside = not inside
        elif ch == sep and not inside:
            return text[: i + 1], text[i + 1:]
    return None


def _limit_tags(s: str) -> list[Tag]:
    """OPT- und Aktivierungs-Beschraenkungen im Satz."""
    tags: list[Tag] = []
    low = s.lower()
    if re.search(r"you can only use each effect of .+? once per turn", s, re.I):
        tags.append(Tag("Hartes OPT (je Effekt)",
                        "Jeder Effekt dieses Kartennamens nur einmal pro Zug — gilt "
                        "für alle Exemplare zusammen, auch nach erneuter Beschwörung.",
                        TOPIC_TEXT))
    elif re.search(r"you can only use (?:this|that|the) effect of .+? once per turn", s, re.I):
        tags.append(Tag("Hartes OPT",
                        "Dieser Effekt nur einmal pro Zug, gebunden an den "
                        "Kartennamen — ein zweites Exemplar kann ihn nicht erneut nutzen.",
                        TOPIC_TEXT))
    if re.search(r"you can only use 1 .+? per turn", s, re.I) or \
            "only use 1 of the following effects" in low:
        tags.append(Tag("Geteiltes OPT",
                        "Von den genannten Effekten nur einer pro Zug (namensgebunden).",
                        TOPIC_TEXT))
    if re.search(r"you can only activate 1 .+? per turn", s, re.I):
        tags.append(Tag("Aktivierungs-Limit",
                        "Nur eine Karte dieses Namens pro Zug aktivieren — schon die "
                        "Aktivierung zählt, auch wenn sie annulliert wird.",
                        TOPIC_TEXT))
    if re.search(r"you can only special summon .+? once per turn", s, re.I):
        tags.append(Tag("Beschwörungs-Limit",
                        "Karten dieses Namens nur einmal pro Zug als Spezialbeschwörung "
                        "— egal auf welchem Weg.", TOPIC_SUMMON))
    if re.search(r"you can only control 1", s, re.I):
        tags.append(Tag("Kontroll-Limit",
                        "Du darfst nur eine Karte dieses Namens gleichzeitig kontrollieren.",
                        TOPIC_TEXT))
    if re.match(r"\s*once per turn\b", s, re.I):
        tags.append(Tag("Weiches OPT",
                        "„Einmal pro Spielzug“ gilt je Exemplar: ein anderes Exemplar — "
                        "oder dieselbe Karte, nachdem sie das Feld verlassen hat — "
                        "darf den Effekt erneut nutzen.", TOPIC_TEXT))
    if "once per duel" in low:
        tags.append(Tag("Einmal pro Duell",
                        "Nur einmal im ganzen Duell nutzbar (namensgebunden).",
                        TOPIC_TEXT))
    if "once per chain" in low:
        tags.append(Tag("Einmal pro Kette",
                        "Innerhalb einer Kette nur einmal aktivierbar.", TOPIC_CHAIN))
    return tags


def _resolution_tags(res: str) -> list[Tag]:
    tags: list[Tag] = []
    low = res.lower()
    if "and if you do" in low or "and if it does" in low or "and if they do" in low:
        tags.append(Tag("„und falls du dies tust“",
                        "Der zweite Teil passiert nur, wenn der erste tatsächlich "
                        "geklappt hat — beide gelten als gleichzeitig.", TOPIC_TEXT))
    if re.search(r"\bthen\b", low):
        tags.append(Tag("„dann“",
                        "Nacheinander: erst der erste Teil, danach der zweite (nicht "
                        "gleichzeitig; kann Auslöser das Timing verpassen lassen). "
                        "Klappt der erste Teil nicht, entfällt der Rest.", TOPIC_TEXT))
    if "also, after that" in low:
        tags.append(Tag("„außerdem, danach“",
                        "Zweiter Teil nacheinander, aber unabhängig davon, ob der "
                        "erste geklappt hat.", TOPIC_TEXT))
    elif re.search(r"\balso\b", low):
        tags.append(Tag("„außerdem“",
                        "Unabhängige Teile: der zweite gilt auch, wenn der erste "
                        "nicht ausgeführt werden kann.", TOPIC_TEXT))
    if re.search(r"you cannot .+?(for the rest of this turn|this turn)", low):
        tags.append(Tag("Einschränkung (Lock)",
                        "Beschränkung für den Rest des Zugs. Als Teil der "
                        "Auflösung greift sie nur, wenn der Effekt aufgelöst "
                        "(nicht annulliert) wird; nach „außerdem“ auch dann, "
                        "wenn der übrige Teil nichts bewirkt.", TOPIC_TEXT))
    if "negate the activation" in low:
        tags.append(Tag("Aktivierung annullieren",
                        "Annulliert die Aktivierung: Karte/Effekt gilt als nicht "
                        "aktiviert (bei Karten: bleibt zerstört im Friedhof).",
                        TOPIC_RULINGS))
    elif re.search(r"negate (?:that|its|the) effects?", low):
        tags.append(Tag("Effekt annullieren",
                        "Annulliert nur den Effekt: die Aktivierung bleibt bestehen "
                        "(zählt z. B. für OPT), bezahlte Kosten sind weg.", TOPIC_RULINGS))
    return tags


def _cost_tags(cost: str) -> list[Tag]:
    body = re.sub(r"^\s*you can\s+", "", cost, flags=re.I).rstrip(";").strip()
    tags: list[Tag] = []
    if re.search(r"\btarget", body, re.I):
        tags.append(Tag("Ziel bei Aktivierung",
                        "„Wähle …“ (target) steht vor dem Semikolon: das Ziel wird "
                        "beim Aktivieren bestimmt — Effekte, die nicht als Ziel "
                        "gewählt werden können, schützen davor.", TOPIC_EFFECTS))
    if body and not re.match(r"target\b", body, re.I):
        tags.append(Tag("Kosten",
                        "Was vor dem Semikolon getan wird, ist Kosten: wird beim "
                        "Aktivieren bezahlt und ist auch weg, wenn der Effekt "
                        "annulliert wird.", TOPIC_EFFECTS))
    return tags


def _condition_kind(cond: str, quick: bool) -> tuple[str, list[Tag]]:
    """Art eines Effekts mit Doppelpunkt anhand der Bedingung."""
    c = cond.strip()
    tags: list[Tag] = []
    if quick:
        tags.append(Tag("Schnelleffekt",
                        "(Quick Effect) = Zauberschnelligkeit 2: auch im Zug des "
                        "Gegners und als Antwort in einer Kette.", TOPIC_CHAIN))
    if quick and re.match(r"(?:once per turn,\s*)?when\b.*\bactivat", c, re.I):
        tags.append(Tag("Antwort auf eine Aktivierung",
                        "Schnelleffekt, der nur als direktes Kettenglied auf die "
                        "genannte Aktivierung aktiviert werden kann — nicht "
                        "später, wenn die Kette schon aufgelöst ist.", TOPIC_CHAIN))
        return "quick", tags
    if re.match(r"(?:once per turn,\s*)?when\b", c, re.I):
        tags.append(Tag("„Wenn“",
                        "Auslöser auf ein Ereignis, das genau das Letzte gewesen sein "
                        "muss. Optional („Du kannst“) kann er das Timing verpassen, "
                        "wenn danach noch etwas passiert ist.", TOPIC_CHAIN))
        return ("quick" if quick else "trigger"), tags
    if re.match(r"(?:once per turn,\s*)?if\b", c, re.I):
        if _EVENT.search(c):
            tags.append(Tag("„Falls“ (Ereignis)",
                            "Auslöser, der das Timing nicht verpassen kann — er wird "
                            "aktiviert, sobald die Kette leer ist.", TOPIC_CHAIN))
            return ("quick" if quick else "trigger"), tags
        tags.append(Tag("„Falls“ (Zustand)",
                        "Beschreibt, wann der Effekt verfügbar ist (z. B. Karte im "
                        "Friedhof), kein Auslöser — aktiviert wird er frei.",
                        TOPIC_EFFECTS))
        return ("quick" if quick else "ignition"), tags
    if re.match(r"at the (?:start|end) of the (?:damage step|battle phase)", c, re.I):
        tags.append(Tag("Kampf-Auslöser",
                        "Wird zu einem festen Zeitpunkt des Kampfs ausgelöst.",
                        TOPIC_EFFECTS))
        return ("quick" if quick else "trigger"), tags
    if re.match(r"during (?:the|each) (?:standby|end) phase", c, re.I):
        tags.append(Tag("Phasen-Auslöser",
                        "Wird in der genannten Phase ausgelöst.", TOPIC_EFFECTS))
        return "trigger", tags
    if re.match(r"(?:once per turn,\s*)?during", c, re.I) or \
            re.match(r"once per turn\b", c, re.I):
        return ("quick" if quick else "ignition"), tags
    return ("quick" if quick else "ignition"), tags


def _analyze(s: str, card_type: str, subtype: str | None) -> Segment:
    t = card_type or ""
    is_spelltrap = "Spell" in t or "Trap" in t
    low = s.lower()
    seg = Segment(text=s, kind="continuous")
    if re.match(r"\(this card is always treated as", low) or \
            re.match(r"\(this card'?s? name", low):
        seg.kind = "note"
        seg.parts = [Part(s, "plain")]
        return seg
    limits = _limit_tags(s)
    if re.search(r"(?:you can activate this card|can be activated) (?:from your hand|"
                 r"the turn it was set)", low):
        seg.kind = "rule"
        seg.tags.append(Tag("Sonderregel zur Aktivierung",
                            "Erlaubt eine Aktivierung, die sonst verboten wäre (Falle "
                            "aus der Hand bzw. im Zug des Setzens) — kein eigener Effekt.",
                            TOPIC_CARDS))
        seg.parts = [Part(s, "limit")]
        return seg
    if re.match(r"(?:this card )?(?:cannot be normal summoned|must be special summoned|"
                r"must first be|must be (?:ritual|fusion|synchro|xyz|link) summoned)",
                low):
        seg.kind = "summon_condition"
        if "must first be" in low:
            seg.tags.append(Tag(
                "Semi-Nomi",
                "Muss zuerst auf die genannte Weise (ordnungsgemäß) beschworen "
                "werden — danach darf es auch per Effekt, z. B. aus dem "
                "Friedhof, beschworen werden.", TOPIC_SUMMON))
        elif re.match(r"(?:this card )?must be", low):
            seg.tags.append(Tag(
                "Nomi",
                "Kann nur auf die genannte Weise beschworen werden — nie per "
                "anderem Effekt, auch nicht aus dem Friedhof wiederbelebt.",
                TOPIC_SUMMON))
        else:
            seg.tags.append(Tag(
                "Keine Normalbeschwörung",
                "Kann nicht normalbeschworen oder gesetzt werden; der folgende "
                "Satz bzw. Effekt sagt, wie es auf das Feld kommt.", TOPIC_SUMMON))
        seg.parts = [Part(s, "limit")]
        seg.tags += limits
        return seg
    if re.match(r"\s*you cannot .+ the turn you activate", low):
        seg.kind = "limit"
        seg.parts = [Part(s, "limit")]
        seg.tags.append(Tag("Einschränkung bei Aktivierung",
                            "Gilt für den ganzen Zug der Aktivierung: du darfst vorher "
                            "nicht dagegen verstoßen haben, und sie bleibt, auch wenn "
                            "der Effekt annulliert wird.", TOPIC_TEXT))
        return seg
    if limits and not _split_outside_quotes(s, ":") and \
            re.match(r"\s*you can only", low):
        seg.kind = "limit"
        seg.parts = [Part(s, "limit")]
        seg.tags = limits
        return seg
    body = s
    if s.upper().startswith("FLIP:"):
        seg.kind = "flip"
        seg.tags.append(Tag("Flippeffekt",
                            "Wird ausgelöst, wenn die Karte aufgedeckt wird (auch per "
                            "Flippbeschwörung oder durch einen Angriff).", TOPIC_EFFECTS))
        head, body = s[:5], s[5:]
        seg.parts.append(Part(head, "condition"))
    elif (split := _split_outside_quotes(s, ":")) is not None:
        cond, body = split
        quick = "(quick effect)" in cond.lower()
        seg.kind, tags = _condition_kind(cond, quick)
        seg.tags += tags
        seg.parts.append(Part(cond, "condition"))
        # Zauber/Fallen ohne „You can“: die Bedingung gehoert zur Aktivierung
        # der Karte selbst (kein eigener Monster-artiger Effekt).
        if is_spelltrap and not re.search(r"\byou can\b", cond + body[:40], re.I):
            seg.kind = "activation"
    cost_split = _split_outside_quotes(body, ";")
    if seg.kind == "continuous" and cost_split is None:
        if re.match(r"\s*you can (?:special|normal|ritual|tribute) summon this card", low) \
                or re.search(r"\bspecial summon(?:ed)? this card .*\bby\b", low) and "you can" in low:
            seg.kind = "inherent"
            seg.tags.append(Tag("Eingebaute Beschwörung",
                                "Beschwörung nach eigener Regel der Karte: kein "
                                "Effekt, startet keine Kette — Effekt-Annullierer "
                                "wie Ash Blossom greifen nicht, Karten, die eine "
                                "Beschwörung annullieren, schon.", TOPIC_SUMMON))
        elif re.match(r"\s*you can\b", low):
            seg.kind = "ignition"
        elif is_spelltrap and (subtype or "Normal") in _ACTIVATION_SUBTYPES:
            seg.kind = "activation"
    elif seg.kind == "continuous":
        seg.kind = ("activation" if is_spelltrap and not re.match(r"\s*you can\b", low)
                    else "ignition")
    if cost_split is not None:
        cost, res = cost_split
        seg.tags += _cost_tags(cost)
        seg.parts.append(Part(cost, "cost"))
        seg.parts.append(Part(res, "effect"))
    else:
        cost, res = "", body
        seg.parts.append(Part(body, "effect" if seg.kind != "continuous" else "plain"))
    cond_text = seg.parts[0].text if seg.parts and seg.parts[0].role == "condition" else ""
    if seg.kind in ("trigger", "quick", "ignition", "flip") and \
            not re.search(r"\byou can\b", cond_text + (cost or res)[:40], re.I):
        seg.tags.append(Tag("Pflichteffekt",
                            "Ohne „Du kannst“: der Effekt muss aktiviert werden, "
                            "sobald er kann.", TOPIC_EFFECTS))
    if seg.kind == "continuous":
        seg.tags.append(Tag("Nicht aktiviert",
                            "Ohne Doppelpunkt/Semikolon: wirkt ständig, solange die "
                            "Karte offen liegt — startet keine Kette.", TOPIC_EFFECTS))
    if seg.kind == "ignition":
        seg.tags.append(Tag("Zündeffekt",
                            "Zauberschnelligkeit 1: nur in deiner Main Phase bei offener "
                            "Spielsituation aktivierbar.", TOPIC_EFFECTS))
    seg.tags += _resolution_tags(res)
    seg.tags += limits
    return seg


# Suchfilter aus der Lesehilfe: (Schluessel, Beschriftung). Die Merkmale
# werden je Karte vorberechnet (cards.wording_flags); WORDING_VERSION hoch-
# zaehlen, wenn sich die Heuristik aendert -- dann wird neu berechnet.
WORDING_VERSION = 1
WORDING_FILTERS = (
    ("handtrap", "Handtraps (heuristisch)"),
    ("quick", "Schnelleffekte"),
    ("trigger", "Auslöseeffekte"),
    ("ignition", "Zündeffekte"),
    ("continuous", "Dauereffekte"),
    ("flip", "Flippeffekte"),
    ("targets", "wählt Ziele"),
    ("negate", "annulliert"),
    ("hard_opt", "hartes OPT"),
    ("soft_opt", "weiches OPT"),
    ("no_opt", "ohne OPT-Beschränkung"),
    ("nomi", "nicht normalbeschwörbar"),
    ("inherent", "eingebaute Beschwörung"),
    ("lock", "mit Lock"),
)
_HAND_COST = re.compile(
    r"discard this card|send this card from your hand|banish this card from your hand"
    r"|reveal this card in your hand|special summon this card from your hand"
    r"|this card is in your hand",
    re.I,
)
_OPPONENTS_TURN = re.compile(r"(?:either player's|your opponent's) turn|quick effect", re.I)


def card_flags(description: str | None, card_type: str | None = "",
               subtype: str | None = None) -> set[str]:
    """Wortlaut-Merkmale einer Karte (Schluessel aus WORDING_FILTERS) --
    dieselbe Heuristik wie explain_card_text. 'handtrap': Monster mit einem
    Schnelleffekt (oder Effekt im Zug des Gegners), den es aus der Hand
    einsetzt, bzw. eine Falle, die aus der Hand aktiviert werden darf."""
    flags: set[str] = set()
    segs = explain_card_text(description, card_type, subtype)
    t = card_type or ""
    is_monster = "Monster" in t
    activated = False
    for seg in segs:
        labels = {tag.label for tag in seg.tags}
        if seg.kind in ("quick", "trigger", "ignition", "continuous", "flip", "inherent"):
            flags.add(seg.kind)
        if seg.kind in ("quick", "trigger", "ignition", "flip", "activation"):
            activated = True
        if seg.kind == "summon_condition":
            flags.add("nomi")
        if "Ziel bei Aktivierung" in labels:
            flags.add("targets")
        if labels & {"Effekt annullieren", "Aktivierung annullieren"}:
            flags.add("negate")
        if labels & {"Hartes OPT", "Hartes OPT (je Effekt)", "Geteiltes OPT",
                     "Aktivierungs-Limit", "Einmal pro Duell"}:
            flags.add("hard_opt")
        if "Weiches OPT" in labels:
            flags.add("soft_opt")
        if labels & {"Einschränkung (Lock)", "Einschränkung bei Aktivierung"}:
            flags.add("lock")
        head = "".join(p.text for p in seg.parts if p.role in ("condition", "cost"))
        if is_monster and seg.kind in ("quick", "trigger", "ignition") and \
                _HAND_COST.search(seg.text) and \
                (seg.kind == "quick" or _OPPONENTS_TURN.search(head)):
            flags.add("handtrap")
        if seg.kind == "rule" and "from your hand" in seg.text.lower():
            flags.add("handtrap")
    if activated and not flags & {"hard_opt", "soft_opt"}:
        flags.add("no_opt")
    return flags


def flags_to_text(flags: set[str]) -> str:
    """Speicherform ',a,b,' -- LIKE '%,a,%' findet ein Merkmal exakt."""
    return "," + ",".join(sorted(flags)) + "," if flags else ","


def explain_card_text(description: str | None, card_type: str | None = "",
                      subtype: str | None = None) -> list[Segment]:
    """Kartentext (englisch) in Segmente zerlegen und erklaeren.
    'card_type' wie in cards.type, 'subtype' wie cards.race (fuer Zauber/
    Fallen: 'Normal', 'Quick-Play', 'Continuous', …)."""
    text = (description or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    t = card_type or ""
    if t in ("Normal Monster", "Normal Tuner Monster", "Pendulum Normal Monster") \
            and "[ Pendulum Effect ]" not in text:
        return [Segment(text=text, kind="flavor", parts=[Part(text, "plain")])]
    is_extra = any(w in t for w in _EXTRA_WORDS)
    segments: list[Segment] = []
    scope = ""
    first_content = True
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or set(line) <= {"-"}:
            continue
        header = re.fullmatch(r"\[\s*(Pendulum Effect|Monster Effect|Flavor Text)\s*\]", line)
        if header:
            scope = {"Pendulum Effect": "Pendeleffekt", "Monster Effect": "Monstereffekt",
                     "Flavor Text": "Beschreibung"}[header.group(1)]
            continue
        if line.startswith("●") and segments:
            prev = segments[-1]
            prev.text += "\n" + line
            prev.parts.append(Part("\n" + line, "effect"))
            continue
        if is_extra and first_content and ":" not in line and ";" not in line \
                and not re.match(r"you can|cannot|must", line, re.I):
            segments.append(Segment(
                text=line, kind="materials", parts=[Part(line, "condition")],
                tags=[Tag("Materialzeile",
                          "Die erste Zeile eines Extra-Deck-Monsters nennt die "
                          "Materialien für die ordnungsgemäße Beschwörung.",
                          TOPIC_SUMMON)],
                scope=scope))
            first_content = False
            continue
        first_content = False
        if scope == "Beschreibung":
            segments.append(Segment(text=line, kind="flavor",
                                    parts=[Part(line, "plain")], scope=scope))
            continue
        for sentence in _SENTENCE.split(line):
            sentence = sentence.strip()
            if not sentence:
                continue
            seg = _analyze(sentence, t, subtype)
            seg.scope = scope
            segments.append(seg)
    return segments
