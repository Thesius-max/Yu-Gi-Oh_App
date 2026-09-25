"""Inhalt des Regelwerk-Tabs: wie man Yu-Gi-Oh spielt (Blatt-Modul, nur Text).

Eingebettet statt aus Dateien gelesen -- wie Handbuch und Notation, damit der
gepackte Build alles enthaelt. RULEBOOK_SECTIONS: (Schluessel, Titel,
Markdown) in Lese-Reihenfolge. Die Schluessel sind die Sprungziele fuer
Spielfeld-Protokoll und Wortlaut-Lesehilfe (yugioh_db.wording.TOPIC_*);
tests pruefen, dass jedes verwendete Ziel existiert.

Stand: Master Rule (Regeländerung April 2020). Deutsch mit den englischen
Fachbegriffen in Klammern, weil Kartentexte und Rulings oft englisch sind.
"""

from __future__ import annotations

RULEBOOK_SECTIONS: list[tuple[str, str, str]] = [
    ("ziel", "Spielziel & Deckbau", """\
# Spielziel & Deckbau

Zwei Spieler duellieren sich. Jeder beginnt mit **8000 Lebenspunkten (LP)**,
mischt sein Deck und zieht **5 Karten**.

## Gewonnen hat, wer …

- die LP des Gegners auf **0** bringt,
- den Gegner zwingt, von einem **leeren Deck** zu ziehen (Deck-Out), oder
- eine **Sonder-Siegbedingung** einer Karte erfüllt (z. B. alle fünf Teile
  von „Exodia“ auf der Hand).

Fallen beide Spieler gleichzeitig auf 0 LP, endet das Duell unentschieden.

## Wer beginnt?

Per Zufall (Münze/Schere-Stein-Papier); in einem Match wählt danach der
Verlierer des vorherigen Duells. Der **Startspieler zieht in seinem ersten
Zug keine Karte** und hat in diesem Zug **keine Battle Phase**.

## Deckbau

| Teil | Größe |
|---|---|
| **Main Deck** | 40–60 Karten |
| **Extra Deck** | 0–15 Karten (Fusion, Synchro, Xyz, Link) |
| **Side Deck** | 0–15 Karten (zum Tauschen zwischen den Duellen eines Matches) |

- Von jeder Karte (gleicher Name) dürfen **höchstens 3 Exemplare** in Main,
  Extra und Side Deck **zusammen** sein.
- Die **Bannliste** (Forbidden & Limited List) schränkt einzelne Karten
  weiter ein: verboten (0), limitiert (1), semi-limitiert (2).
- Zwischen den Duellen eines Matches darfst du Karten **1 zu 1** mit dem
  Side Deck tauschen — das Side Deck hat danach **genauso viele Karten** wie
  vorher, und Main/Extra Deck müssen weiter die Größenregeln erfüllen.

> In der App: Der Deck-Tab prüft Größen und die 3-Kopien-Regel; das
> Spielfeld spielt Goldfishing mit 8000 LP und 5 bzw. 6 Startkarten.
"""),

    ("feld", "Spielfeld & Zonen", """\
# Spielfeld & Zonen

Jeder Spieler hat eine eigene Spielfeldseite:

| Zone | Zweck |
|---|---|
| **Hauptmonsterzonen** (Main Monster Zone) | 5 Zonen für Monster |
| **Extra-Monsterzonen** (Extra Monster Zone, EMZ) | 2 Zonen in der Mitte, von **beiden** Spielern genutzt — jeder darf normalerweise nur **eine** davon belegen |
| **Zauber- & Fallenzonen** (Spell & Trap Zone) | 5 Zonen für gesetzte oder aktivierte Zauber/Fallen |
| **Pendelzonen** (Pendulum Zone) | die **äußerste linke und rechte** Zauber- & Fallenzone |
| **Feldzone** (Field Zone) | ein Feldzauber |
| **Friedhof** (Graveyard, GY) | offen, für alle einsehbar |
| **Deck** und **Extra Deck** | verdeckte Stapel |
| **Verbannt** (banished) | außerhalb des Spiels; meist offen |

## Monster aus dem Extra Deck (Master Rule 2020)

- **Fusions-, Synchro- und Xyz-Monster** dürfen in **jede** freie
  Hauptmonsterzone oder in eine Extra-Monsterzone.
- **Link-Monster** und **Pendelmonster, die offen aus dem Extra Deck**
  beschworen werden, dürfen nur in eine **Extra-Monsterzone** oder in eine
  Hauptmonsterzone, auf die ein **Link-Pfeil** zeigt.

## Link-Pfeile (Link Arrows)

Link-Monster haben Pfeile an ihren Rändern. Die Zone, auf die ein Pfeil
zeigt, ist eine **„verlinkte“ Zone** (linked zone). Zeigen zwei Link-Monster
gegenseitig aufeinander, sind sie **„co-verlinkt“** (co-linked). Pfeile eines
Link-Monsters in der Extra-Monsterzone können auch auf Zonen des Gegners
zeigen.

> In der App: Das Spielfeld zeigt die Pfeile auf den Karten und hebt beim
> Aufnehmen eines Extra-Deck-Monsters die gültigen Zonen gold hervor.
"""),

    ("phasen", "Zug & Phasen", """\
# Zug & Phasen

Ein Zug hat sechs Phasen, immer in dieser Reihenfolge:

1. **Draw Phase** — Ziehe 1 Karte (der Startspieler nicht in seinem ersten
   Zug).
2. **Standby Phase** — Zeitpunkt für Effekte „während der Standby Phase“.
3. **Main Phase 1** — Die Hauptphase:
   - **eine** Normalbeschwörung **oder** ein Setzen eines Monsters pro Zug,
   - beliebig viele Spezialbeschwörungen (soweit Karten es erlauben),
   - Zauber aktivieren, Zauber/Fallen setzen,
   - Zündeffekte aktivieren,
   - Kampfpositionen ändern (siehe „Positionen & Kampf“).
4. **Battle Phase** — Kämpfe (nicht im ersten Zug des Duells). Unterteilt in
   **Start Step**, **Battle Step** (Angriff ansagen), **Damage Step**
   (Schadensberechnung) und **End Step**.
5. **Main Phase 2** — wie Main Phase 1; nur, wenn eine Battle Phase
   stattgefunden hat.
6. **End Phase** — Effekte „während der End Phase“; danach gilt das
   **Handlimit**: Wer mehr als **6 Karten** auf der Hand hat, wirft ab, bis
   es 6 sind.

Du musst die Battle Phase nicht durchführen — dann springst du von Main
Phase 1 direkt in die End Phase.

> In der App: Die Phasenleiste des Spielfelds folgt dieser Reihenfolge; mit
> Regelwerk überspringt „Nächste Phase“ die Battle Phase im ersten Zug und
> fragt beim Zugende nach dem Abwerfen.
"""),

    ("kartenarten", "Kartenarten", """\
# Kartenarten

## Monster

| Art | Rahmen | Kurz |
|---|---|---|
| **Normales Monster** (Normal Monster) | gelb | ohne Effekt, mit Beschreibungstext |
| **Effektmonster** (Effect Monster) | orange | hat Effekte |
| **Ritualmonster** (Ritual Monster) | blau | per Ritualzauber beschworen |
| **Fusionsmonster** (Fusion Monster) | violett | Extra Deck |
| **Synchromonster** (Synchro Monster) | weiß | Extra Deck |
| **Xyz-Monster** (Xyz Monster) | schwarz | Extra Deck, hat **Rang** statt Stufe |
| **Pendelmonster** (Pendulum Monster) | Farbverlauf zu grün | Monster **und** Pendelskala |
| **Link-Monster** (Link Monster) | dunkelblau | Extra Deck, **Link-Wert** statt Stufe, keine DEF |
| **Spielmarke** (Token) | — | durch Effekte erzeugt, keine echte Karte |

Zusätzliche Eigenschaften: **Empfänger** (Tuner, für Synchro), **Flipp**
(Effekt beim Aufdecken), **Spirit**, **Union**, **Zwilling** (Gemini),
**Toon**. Jedes Monster hat ein **Attribut** (LICHT, FINSTERNIS, ERDE,
WASSER, FEUER, WIND, GÖTTLICH) und einen **Typ** (Drache, Unterweltler, …).

- **Stufe** (Level, Sterne): Normal-, Effekt-, Ritual-, Fusions-, Synchro-,
  Pendelmonster.
- **Rang** (Rank): Xyz — ein Rang ist **keine** Stufe.
- **Link-Wert** (Link Rating): Link — Link-Monster haben weder Stufe noch
  DEF und stehen immer in **Angriffsposition**.

## Zauber (Spell)

| Art | Symbol | Aktivierung |
|---|---|---|
| **Normal** | — | eigene Main Phase, einmalig, dann in den Friedhof |
| **Schnell** (Quick-Play) | Blitz | aus der Hand nur im **eigenen** Zug (jede Phase); gesetzt auch im Zug des Gegners — aber **nicht im Zug, in dem er gesetzt wurde** |
| **Permanent** (Continuous) | ∞ | bleibt liegen und wirkt weiter |
| **Ausrüstung** (Equip) | + | wird an ein Monster angelegt; geht in den Friedhof, wenn das Monster das Feld verlässt |
| **Feld** (Field) | Kompass | in die Feldzone; ein **neuer** eigener Feldzauber schickt den alten in den Friedhof |
| **Ritual** | Flamme | zum Ritualbeschwören |

Alle Zauber außer Schnellzaubern haben **Zauberschnelligkeit 1** und werden
nur in deiner Main Phase aktiviert.

## Fallen (Trap)

| Art | Zauberschnelligkeit |
|---|---|
| **Normal** | 2 |
| **Permanent** (Continuous) | 2 |
| **Konter** (Counter) | 3 — nur Konterfallen können auf sie antworten |

Fallen müssen **zuerst gesetzt** werden und dürfen **frühestens im nächsten
Zug** aktiviert werden — auch im Zug des Gegners. Ausnahmen stehen im
Kartentext (z. B. „Falls du keine Karten kontrollierst, kannst du diese
Karte von deiner Hand aktivieren“ bei Infinite Impermanence).
"""),

    ("beschwoerung", "Beschwörungsarten", """\
# Beschwörungsarten

## Normalbeschwörung, Setzen, Tribut

- **Einmal pro Zug** darfst du in deiner Main Phase ein Monster
  **normalbeschwören** (offen in Angriffsposition) **oder setzen** (verdeckt
  in Verteidigungsposition). Effekte können zusätzliche Normalbeschwörungen
  erlauben.
- Monster der **Stufe 5–6** brauchen **1 Tribut**, **Stufe 7+** brauchen
  **2 Tribute** (Tributbeschwörung, Tribute Summon). Tribute gehen in den
  Friedhof.
- Ritual-, Extra-Deck- und viele besondere Monster können nicht
  normalbeschworen werden.

## Flippbeschwörung

Ein **gesetztes** Monster in deiner Main Phase offen in Angriffsposition
drehen — nicht in dem Zug, in dem es gesetzt wurde. Dabei werden Flipp-
Effekte ausgelöst.

## Spezialbeschwörung

- **Durch einen Effekt** („beschwöre … als Spezialbeschwörung“): Teil der
  Auflösung eines Effekts.
- **Eingebaut** (inherent): nach eigener Regel der Karte, z. B. „Du kannst
  diese Karte als Spezialbeschwörung von deiner Hand beschwören, indem …“.
  Das ist **kein Effekt** und startet **keine Kette**; Karten, die eine
  Beschwörung annullieren, können darauf antworten.
- Beliebig oft pro Zug, soweit Karten es erlauben.

## Ritualbeschwörung

Mit einem **Ritualzauber**: Tributiere Monster (Hand/Feld), deren Stufen
zusammen mindestens (oder genau, je nach Karte) die Stufe des Ritualmonsters
ergeben, und beschwöre es aus der Hand.

## Fusionsbeschwörung

Mit einer Karte wie „Polymerization“: Die im Text des Fusionsmonsters
genannten **Fusionsmaterialien** (meist aus Hand/Feld) gehen in den
Friedhof, das Fusionsmonster kommt aus dem Extra Deck.

## Synchrobeschwörung

In deiner Main Phase: **1 Empfänger** (Tuner) + beliebig viele
**Nicht-Empfänger** offen auf dem Feld, deren **Stufen zusammen genau** die
Stufe des Synchromonsters ergeben → Materialien in den Friedhof. Eingebaut,
startet keine Kette.

## Xyz-Beschwörung

In deiner Main Phase: die geforderte Anzahl offener Monster **gleicher
Stufe** (= Rang des Xyz). Die Materialien werden **Xyz-Material** (Xyz
Material): Sie liegen **unter** dem Xyz-Monster, gelten nicht als Karten
auf dem Feld und werden zum Aktivieren von Effekten **abgehängt** (detach →
Friedhof). Verlässt das Xyz-Monster das Feld, gehen seine Materialien in den
Friedhof. Spielmarken können kein Xyz-Material sein.

## Pendelbeschwörung

Liegen in beiden Pendelzonen Pendelmonster (Skalen), darfst du **einmal pro
Zug** in deiner Main Phase beliebig viele Monster aus der **Hand** und
**offene Pendelmonster aus dem Extra Deck** beschwören, deren Stufe
**zwischen** den beiden Skalen liegt (nicht gleich). Pendelmonster, die vom
Feld in den Friedhof gehen würden, kommen stattdessen **offen ins Extra
Deck**.

## Link-Beschwörung

In deiner Main Phase: offene Monster als **Link-Material** in den Friedhof,
ihre Anzahl muss dem **Link-Wert** entsprechen. Ein **Link-Monster** als
Material zählt wahlweise als **1** oder als **sein Link-Wert**. Das
Link-Monster kommt in eine Extra-Monsterzone oder eine verlinkte Zone.

## „Ordnungsgemäß beschworen“, Nomi & Semi-Nomi

- **Nomi** — „Kann nicht als Normalbeschwörung beschworen oder gesetzt
  werden. **Muss** als Spezialbeschwörung beschworen werden, indem …“ (ohne
  „zuerst“): Das Monster kann **nur** auf diese Weise beschworen werden —
  nie per anderem Effekt, auch nicht aus dem Friedhof wiederbelebt.
- **Semi-Nomi** — „… **Muss zuerst** … beschworen werden“: Erst nach einer
  **ordnungsgemäßen** Beschwörung auf diese Weise darf es auch per Effekt
  (z. B. aus dem Friedhof) beschworen werden.
- **Extra-Deck-Monster** verhalten sich wie Semi-Nomis: Wurden sie nicht
  ordnungsgemäß (per Formel bzw. Fusion) beschworen — etwa per Effekt direkt
  auf den Friedhof gelegt —, können sie nicht wiederbelebt werden (Ausnahmen
  nennt der Kartentext).
"""),

    ("kampf", "Positionen & Kampf", """\
# Positionen & Kampf

## Kampfpositionen

- **Angriffsposition** (aufrecht, offen) und **Verteidigungsposition**
  (quer, offen oder verdeckt).
- In deiner Main Phase darfst du die Position eines Monsters **einmal pro
  Zug** ändern — **nicht** im Zug, in dem es beschworen/gesetzt wurde, und
  nicht, nachdem es angegriffen hat. Eine Flippbeschwörung zählt als
  Positionswechsel.
- Link-Monster sind immer in Angriffsposition.

## Angreifen

- Nur in deiner **Battle Phase**, nicht im ersten Zug des Duells.
- Jedes offene Monster in **Angriffsposition** darf **einmal** angreifen.
- Hat der Gegner **keine Monster**, greifst du **direkt** an: Schaden in
  Höhe der ATK.
- Ändert sich die Zahl der Gegner-Monster nach der Angriffsansage, darf der
  Angreifer ein neues Ziel wählen oder den Angriff abbrechen (**Replay**).

## Schadensberechnung

Ein verdecktes Ziel wird zuerst aufgedeckt (Flipp-Effekte lösen danach aus).

| Angegriffenes Monster | Vergleich | Ergebnis |
|---|---|---|
| **Angriffsposition** | ATK > ATK | Ziel zerstört, Gegner erhält die Differenz als Schaden |
| | ATK = ATK | beide zerstört, kein Schaden (bei 0/0 nichts) |
| | ATK < ATK | Angreifer zerstört, **du** erhältst die Differenz |
| **Verteidigungsposition** | ATK > DEF | Ziel zerstört, **kein** Schaden |
| | ATK = DEF | nichts passiert |
| | ATK < DEF | nichts zerstört, **du** erhältst die Differenz |

**Durchschlagsschaden** (piercing): Manche Monster fügen beim Angriff auf
ein Monster in Verteidigungsposition trotzdem Schaden in Höhe der Differenz
zu — steht im Kartentext.

> In der App: „Angreifen…“ im Kontextmenü rechnet genau diese Tabelle;
> „Werte ändern…“ berücksichtigt ATK/DEF-Änderungen durch Effekte.
"""),

    ("effekte", "Effektarten", """\
# Effektarten

## Aktivierte Effekte

| Art | Zauberschnelligkeit | Wann |
|---|---|---|
| **Zündeffekt** (Ignition) | 1 | du aktivierst ihn in **deiner Main Phase**, wenn nichts anderes passiert |
| **Auslöseeffekt** (Trigger) | 1 | automatisch nach einem **Ereignis** („Falls/Wenn diese Karte beschworen wird: …“) |
| **Schnelleffekt** (Quick Effect) | 2 | jederzeit, auch im Zug des Gegners und als Antwort |
| **Flippeffekt** (Flip) | 1 | ein Auslöseeffekt beim Aufdecken („FLIPP: …“) |

Zauber und Fallen haben ihre eigene Zauberschnelligkeit (siehe
„Kartenarten“).

## Nicht aktivierte Effekte

- **Dauereffekt** (Continuous Effect): wirkt ständig, solange die Karte offen
  liegt („Monster deines Gegners verlieren 800 ATK.“) — **startet keine
  Kette** und kann nicht mit „annulliere die Aktivierung“ beantwortet werden.
- **Beschwörungsbedingungen** und **eingebaute Beschwörungen** sind Regeln
  der Karte, keine Effekte.

## Aktivieren vs. Auflösen

- **Beim Aktivieren** zahlst du die **Kosten** und wählst **Ziele** (alles,
  was vor dem Semikolon steht).
- **Beim Auflösen** passiert, was hinter dem Semikolon steht. Dazwischen
  kann der Gegner antworten (Kette).
- **Kosten** sind auch dann bezahlt, wenn der Effekt annulliert wird.
- Ein gewähltes **Ziel** (target), das bei der Auflösung nicht mehr da ist
  oder nicht mehr passt, wird nicht betroffen.
- Karten, die **„nicht als Ziel gewählt werden können“**, schützen nur vor
  Effekten mit „Wähle …“ (target).

## Wo wirken Effekte?

Die meisten Monstereffekte wirken auf dem Feld. Effekte, die ausdrücklich
**Hand**, **Friedhof** oder **Verbannt** nennen, werden **dort** aktiviert
(z. B. Handtraps wie Ash Blossom: „Du kannst diese Karte abwerfen; …“).
"""),

    ("ketten", "Ketten & Zauberschnelligkeit", """\
# Ketten & Zauberschnelligkeit

## Die Kette (Chain)

Wird eine Karte oder ein Effekt aktiviert, entsteht **Kettenglied 1**
(Chain Link 1). Jetzt darf der **andere** Spieler antworten, dann wieder
der erste usw. Jede Antwort ist ein neues Kettenglied.

- Antworten braucht mindestens **Zauberschnelligkeit 2** und mindestens die
  Schnelligkeit des vorherigen Kettenglieds.
- Auf **Zauberschnelligkeit 3** (Konterfallen) kann nur mit 3 geantwortet
  werden.
- Wenn niemand mehr antwortet, wird die Kette **rückwärts** aufgelöst: das
  **letzte** Kettenglied zuerst.
- Was während der Auflösung passiert, löst Auslöser erst **nach** der Kette
  aus — sie bilden dann eine neue Kette.

## Zauberschnelligkeiten

| Schnelligkeit | Karten/Effekte |
|---|---|
| **1** | Zünd- und Auslöseeffekte, Flippeffekte, alle Zauber außer Schnellzaubern |
| **2** | Schnelleffekte, Schnellzauber, normale und permanente Fallen |
| **3** | Konterfallen |

## Mehrere Auslöser gleichzeitig (SEGOC)

Lösen mehrere Effekte gleichzeitig aus, baut man die Kette in dieser
Reihenfolge: **Pflicht**-Effekte des Zugspielers → Pflicht-Effekte des
Gegners → **optionale** Effekte des Zugspielers → optionale des Gegners.

## Das Timing verpassen (missing the timing)

Ein **optionaler Auslöser mit „Wenn … : Du kannst“** darf nur aktiviert
werden, wenn das auslösende Ereignis **das Letzte** war, was passiert ist.
Geschah danach noch etwas (z. B. „… dann …“ in derselben Auflösung),
**verpasst** er das Timing und kann nicht aktiviert werden.

- **„Falls … : Du kannst“** kann das Timing **nicht** verpassen.
- **Pflicht-Auslöser** („Wenn …“ ohne „Du kannst“) verpassen das Timing nie.
"""),

    ("kartentext", "Kartentext lesen (PSCT)", """\
# Kartentext lesen (PSCT)

Seit 2011 folgen Kartentexte einer festen Grammatik (Problem-Solving Card
Text, PSCT). Wer die Satzzeichen und Bindewörter kennt, sieht **wie** ein
Effekt funktioniert. Die Schaltfläche **„Wortlaut ?“** in den Kartendetails
zerlegt jede Karte nach diesen Regeln.

## Doppelpunkt und Semikolon

    [Bedingung] : [Kosten / Ziele] ; [Auflösung]

- **Vor dem Doppelpunkt (:)** steht, **wann** der Effekt aktiviert werden
  darf. Ein Doppelpunkt zeigt: das ist ein **aktivierter** Effekt (startet
  eine Kette).
- **Zwischen Doppelpunkt und Semikolon (;)** steht, was **beim Aktivieren**
  passiert: **Kosten** und **Ziele**.
- **Nach dem Semikolon** steht, was **bei der Auflösung** passiert.
- **Ohne Doppelpunkt und Semikolon** ist es meist ein **Dauereffekt**.

**Beispiel — Ash Blossom & Joyous Spring**
„Wenn eine Karte oder ein Effekt aktiviert wird, die/der … (Schnelleffekt):
Du kannst diese Karte abwerfen; annulliere jenen Effekt.“
→ Bedingung *wenn …*, Schnelleffekt, **Kosten**: sich selbst abwerfen,
**Auflösung**: annullieren. Wird Ash annulliert, bleibt sie trotzdem
abgeworfen.

**Beispiel — Infinite Impermanence**
„Wähle 1 offenes Monster, das dein Gegner kontrolliert; annulliere seine
Effekte …“ → das **Ziel** wird beim Aktivieren gewählt.

## „Wenn“ und „Falls“

| Deutsch | Englisch | Bedeutung |
|---|---|---|
| **Wenn** … : Du kannst | **When** … : You can | optionaler Auslöser, **kann das Timing verpassen** |
| **Falls** … : Du kannst | **If** … : You can | Auslöser, verpasst das Timing **nicht** |
| **Falls sich diese Karte in … befindet:** | **If this card is in your …:** | Ort-/Zustandsbedingung eines Zündeffekts, kein Auslöser |
| **Während …** | **During …** | Zeitfenster, in dem der Effekt aktiviert werden darf |

## „Du kannst“

- **Mit „Du kannst“** (You can): optional.
- **Ohne**: Pflicht — der Effekt **muss** aktiviert werden.

## Mehrere Aktionen in einem Effekt

| Deutsch | Englisch | Bedeutung |
|---|---|---|
| **und falls du dies tust** | **and if you do** | Teil 2 nur, wenn Teil 1 geklappt hat; gleichzeitig |
| **dann** | **then** | nacheinander; Teil 2 nur, wenn Teil 1 geklappt hat; kann Timing verpassen lassen |
| **außerdem** / **und** | **also** / **and** | unabhängig; Teil 2 gilt auch, wenn Teil 1 nicht klappt |
| **außerdem, danach** | **also, after that** | nacheinander, aber unabhängig vom Erfolg von Teil 1 |

## Einmal pro Zug (OPT)

| Formulierung | Art | Wirkung |
|---|---|---|
| „**Einmal pro Spielzug**: …“ (Once per turn) | **weich** (soft OPT) | je **Exemplar** — eine andere Kopie, oder dieselbe Karte, nachdem sie das Feld verlassen hat, darf wieder |
| „Du kannst **diesen Effekt von „X“** nur einmal pro Spielzug verwenden.“ | **hart** (hard OPT) | an den **Namen** gebunden — für alle Exemplare zusammen |
| „… **jeden Effekt von „X“** …“ | hart, je Effekt | jeder Effekt einmal, auch unterschiedliche nacheinander |
| „Du kannst nur 1 **„X“ pro Spielzug aktivieren**.“ | Aktivierungs-Limit | schon die Aktivierung zählt — auch wenn sie annulliert wird |
| „Du kannst **„X“ nur einmal pro Spielzug als Spezialbeschwörung** beschwören.“ | Beschwörungs-Limit | egal auf welchem Weg |

Ein **harter OPT** ist auch dann verbraucht, wenn der Effekt annulliert
wurde — er wurde ja verwendet.

## Einschränkungen (Locks)

„…, **außerdem kannst du** für den Rest dieses Spielzugs keine Monster als
Spezialbeschwörung vom Extra Deck beschwören, außer …“ (Bone Archfiend) —
Teil der Auflösung: greift, wenn der Effekt aufgelöst wird.
„Du kannst **in dem Spielzug, in dem du diesen Effekt aktivierst**, keine …“
— gilt für den **ganzen** Zug, auch schon **vor** der Aktivierung, und
bleibt, wenn der Effekt annulliert wird.

## (Schnelleffekt)

„(Schnelleffekt)“ / „(Quick Effect)“ hinter der Bedingung macht einen
Monstereffekt zu Zauberschnelligkeit 2 — aktivierbar im Zug des Gegners und
als Antwort in einer Kette.
"""),

    ("glossar", "Glossar Deutsch ↔ Englisch", """\
# Glossar Deutsch ↔ Englisch

| Deutsch | Englisch | Hinweis |
|---|---|---|
| abhängen (Xyz-Material) | detach | Xyz-Material in den Friedhof |
| abwerfen | discard | von der Hand in den Friedhof — Kosten oder Effekt |
| annullieren | negate | Aktivierung oder Effekt unwirksam machen |
| auf den Friedhof legen | send to the GY | nicht dasselbe wie „zerstören“ |
| Beschwörung annullieren | negate the Summon | das Monster gilt als nicht beschworen |
| der Hand hinzufügen | add to your hand | „Suchen“ |
| Empfänger | Tuner | Synchro-Material |
| Friedhof | Graveyard (GY) | |
| kontrollieren | control | auf deiner Spielfeldseite |
| mischen | shuffle | |
| offen / verdeckt | face-up / face-down | |
| Spielfeld | field | Monster-, Zauber/Fallen-, Feld- und Pendelzonen |
| Spielmarke | Token | |
| Spezialbeschwörung | Special Summon | |
| Tribut | Tribute | |
| verbannen | banish | aus dem Spiel entfernen |
| wählen (als Ziel) | target | |
| Xyz-Material | Xyz Material | liegt unter dem Xyz-Monster |
| zerstören | destroy | durch Kampf oder Karteneffekt; ≠ „auf den Friedhof legen“ |
| Zündeffekt | Ignition Effect | |
| Auslöseeffekt | Trigger Effect | |
| Schnelleffekt | Quick Effect | |
| Dauereffekt | Continuous Effect | |
| Zauberschnelligkeit | Spell Speed | |
| Kette / Kettenglied | Chain / Chain Link | |
"""),

    ("rulings", "Allgemeine Rulings & Sonderfälle", """\
# Allgemeine Rulings & Sonderfälle

- **Aktivierung annullieren vs. Effekt annullieren.** „Annulliere die
  Aktivierung“ (negate the activation): Die Karte/der Effekt gilt als nicht
  aktiviert; eine annullierte Zauber-/Fallenkarte geht in den Friedhof.
  „Annulliere den Effekt“ (negate the effect): Die Aktivierung bleibt
  bestehen (zählt z. B. für harte OPTs), nur die Auflösung entfällt.
- **Kosten bleiben bezahlt**, auch wenn Aktivierung oder Effekt annulliert
  werden.
- **Zerstören ≠ auf den Friedhof legen.** „Falls diese Karte zerstört
  wird“ löst nicht aus, wenn sie als Kosten oder Material in den Friedhof
  geht.
- **Weiches OPT** setzt sich zurück, wenn die Karte das Feld verlässt und
  zurückkommt — sie ist dann eine „neue“ Karte. Harte OPTs nicht.
- **Eine Karte, die das Feld verlässt,** verliert alle Änderungen (ATK/DEF,
  Zählmarken, Ausrüstungen); Xyz-Material geht in den Friedhof.
- **Spielmarken** können nicht in Hand, Deck, Friedhof oder Verbannt —
  verlassen sie das Feld, verschwinden sie.
- **Damage Step (vereinfacht):** Hier dürfen nur wenige Karten aktiviert
  werden — im Wesentlichen Konterfallen, Karten/Effekte, die ATK/DEF direkt
  ändern, und Effekte, die ausdrücklich im Damage Step auslösen. Die
  genauen Zeitpunkte regelt das offizielle Regelheft.
- **Gleichzeitige Auslöser** bilden eine Kette nach der SEGOC-Reihenfolge
  (siehe „Ketten“).

## Eigene Rulings zu einzelnen Karten

Viele Karten haben **eigene offizielle Rulings**. In den Kartendetails
öffnet **„Rulings“** deine eigenen Notizen zur Karte (mit Quelle) und
verlinkt die **Konami-Datenbank** (offiziell, mit FAQ) und **Yugipedia**.
Deine Notizen überleben Kartendaten-Updates und erscheinen im
Deck-Export für KI und als Hinweis auf dem Spielfeld.
"""),

    ("quellen", "Stand & Quellen", """\
# Stand & Quellen

Dieses Regelwerk fasst die **offiziellen Regeln** in eigenen Worten zusammen
— Stand **Master Rule (Regeländerung April 2020)**. Es ersetzt nicht das
offizielle Regelheft; bei Widersprüchen gilt die offizielle Quelle.

- **Offizielles Regelheft** und **Karten-FAQ**: Konami-Kartendatenbank
  (db.yugioh-card.com) — in der App über „Rulings“ je Karte erreichbar.
- **Kartentexte** stammen aus der YGOPRODeck-Datenbank (englisch und, wo
  vorhanden, deutsch).

## Was die App daraus macht

- Das **Spielfeld** prüft die allgemeine Mechanik (Phasen, Beschwörungen,
  Zonen, Kampf) — **keine Karteneffekte**.
- Die **Wortlaut-Lesehilfe** erklärt die Grammatik eines Kartentexts — sie
  ist eine Heuristik und trifft **keine Regelentscheidung**.
- **Rulings zu einzelnen Karten** pflegst du selbst; die App lädt keine
  Rulings aus dem Netz.
"""),
]


def section_title(key: str) -> str:
    """Titel eines Kapitels (leer, wenn es den Schluessel nicht gibt)."""
    for k, title, _md in RULEBOOK_SECTIONS:
        if k == key:
            return title
    return ""
