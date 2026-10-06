"""Handbuch-Tab: statische Abschnitte, im Code eingebettet.

_MANUAL_SECTIONS haelt die Markdown-Texte (kein Datei-Zugriff, damit
der gepackte Build sie sicher findet); HelpView rendert sie.
"""

from __future__ import annotations

from .docview import DocView
from .notation import NOTATION_MD


# ---------------------------------------------------------------------------
# Benutzerhandbuch (eigener Tab)
# ---------------------------------------------------------------------------

# Das Handbuch ist eingebettet (nicht aus einer Datei gelesen), damit es auch
# im gepackten Build ohne Repo-Dateien vollstaendig vorliegt -- gleiche
# Begruendung wie bei NOTATION_MD. Jeder Eintrag: (Titel, Markdown). Die
# Reihenfolge ist die Lese-Reihenfolge; sie folgt den Tabs der App.
_MANUAL_SECTIONS: list[tuple[str, str]] = [
    ("Überblick & erste Schritte", """\
# Überblick

Diese App verwaltet deine **Yu-Gi-Oh!-Sammlung**, hilft beim **Deckbuilding**
und pflegt eine eigene **Kombo-Bibliothek**. Sie läuft eigenständig auf deinem
Rechner; die Kartendaten werden einmalig aus dem Internet geladen und danach
lokal gehalten — **danach arbeitet die App offline**.

## Die sechs Tabs

| Tab | Wofür |
|---|---|
| **Suche** | Karten finden, Details ansehen, in die Sammlung übernehmen |
| **Sammlung** | dein physischer Kartenbestand |
| **Deck** | Decks bauen, prüfen, importieren/exportieren, Kombo-Hilfe |
| **Spielfeld** | Decks testen — mit Regelwerk, Dummy-Gegner und Kombo-Recorder |
| **Kombos** | Kombo-Linien dokumentieren (Bausteine, Rollen, Schritte) |
| **Regelwerk** | wie man Yu-Gi-Oh spielt: Phasen, Kartenarten, Beschwörungen, Effekte, Ketten, Kartentext lesen |
| **Handbuch** | diese Hilfe |

## Beim allerersten Start

Ist noch keine Kartendatenbank vorhanden, lädst du sie über das Menü
**Daten → Kartendaten aktualisieren…** herunter (braucht einmalig Internet).
Danach füllen sich Such-Filter und Listen automatisch.

> **Deine Daten sind getrennt von den Kartendaten.** Sammlung, Decks,
> Übersetzungen und Kombos bleiben bei jedem Karten- oder App-Update erhalten.
"""),

    ("Suche-Tab", """\
# Suche

Der Suche-Tab hat drei Spalten (frei per Trennbalken verschiebbar):
**Filter** links, **Trefferliste** in der Mitte, **Detailansicht** rechts.
Die Filter folgen dem **Aufbau einer Karte** — du suchst nach dem, was auf
der Karte steht.

## Suchfeld

Oben links. Es findet
- **Name und Kartentext** (deutsch oder englisch, der Wortanfang genügt),
- die **Kartennummer** (Passcode unten links auf der Karte, z. B. `14558127`),
- eine **Set-Nummer** (rechts unter dem Bild, z. B. `RA01-DE008` — deutsche
  und englische Codes, Groß-/Kleinschreibung egal; auch deine eigenen
  Drucke aus der Sammlung).

## Kartenart

Der Umschalter **Alle · Monster · Zauber · Falle** wählt die Kartenart.
Darunter erscheinen nur die Filter, die für diese Art etwas bedeuten:

- **Monster:**
  - **Monsterart** = Rahmenfarbe: Normal, Effekt, Ritual, Fusion, Synchro,
    Xyz, Pendel, Link. *Effekt* meint den orangen Rahmen — Synchro-Monster
    mit Effekt findest du unter *Synchro*.
  - **Eigenschaft** (LICHT, FINSTERNIS …) und **Merkmal** aus der Typzeile
    (Empfänger, Flipp, Zwilling, Spirit, Union, Toon).
  - **Typ** (Drache, Hexer …).
  - Wertebereiche für **Stufe/Rang**, **Link**, **Pendelskala**, **ATK** und
    **DEF**. „egal“ heißt kein Filter; `0` ist ein echter Wert (z. B. ATK 0).
- **Zauber:** das Symbol — Normal, Schnell, Permanent, Ausrüstung, Feld,
  Ritual.
- **Falle:** das Symbol — Normal, Permanent, Konter.

Die Filter-Knöpfe (**Chips**) schalten sich per Klick an und aus. Innerhalb
einer Gruppe gilt **oder** (Synchro *oder* Xyz), zwischen den Gruppen
**und** (Synchro *und* FINSTERNIS). Wechselst du die Kartenart, wirken die
ausgeblendeten Filter nicht — zurück bei *Monster* sind sie wieder da.

## Allgemein

- **Archetyp:** auswählen oder eintippen (Vorschläge erscheinen beim
  Tippen).
- **Wortlaut:** filtert nach dem, was der Kartentext *tut* — z. B.
  **Handtraps (heuristisch)** — genauer: Handeffekte im Gegnerzug (Monster,
  die einen Effekt aus der Hand als Schnelleffekt oder im Zug des Gegners
  einsetzen, und Fallen, die aus der Hand aktiviert werden dürfen — enthält
  alle klassischen Handtraps, aber auch Karten, die nicht stören),
  Schnelleffekte, Auslöse-/Zünd-/Dauereffekte, „wählt Ziele“, „annulliert“,
  hartes/weiches OPT, ohne OPT-Beschränkung, nicht normalbeschwörbar, mit
  Lock. Grundlage ist dieselbe Heuristik wie bei **Wortlaut ?** (englischer
  Kartentext); beim ersten Mal rechnet die App die Merkmale einmalig vor
  (etwa eine Sekunde), nach einem Kartendaten-Update erneut.
- **„Nur meine Sammlung“:** nur Karten, die du besitzt.
- **Filter zurücksetzen** stellt alles auf „egal“ (der Suchtext bleibt).

Jede Änderung sucht sofort neu.

## Trefferliste

- Je Karte eine Zeile: der Name und bei Monstern `[ATK … / DEF …]`
  (Link-Monster nur ATK). Ohne Suchtext nach Namen sortiert, bei einer
  Textsuche stehen die besten Treffer zuerst.
- Angezeigt werden höchstens 300 Treffer; gibt es mehr, steht unten
  „300+ Treffer – Suche eingrenzen“.
- Maus über dem Namen zeigt den englischen Namen.

## Detailansicht (rechts)

Zeigt Bild, Kartendaten und Kartentext der gewählten Karte — in der
Reihenfolge der Karte: **Eigenschaft** und **Stufe/Rang**, die **Typzeile**
(z. B. `[Drache/Synchro/Effekt]`), **ATK/DEF** bzw. **ATK/LINK** mit den
**Link-Pfeilen**, die **Pendelskala**, dann **Kartennummer**, in wie vielen
**Sets** die Karte erschien und der **Archetyp**. Fehlen Typzeile oder
Link-Pfeile (Kartendaten von vor diesem App-Stand), fragt die App beim Start
einmal, ob sie die Kartendaten aktualisieren soll. Das Bild wird beim ersten
Aufruf einmal lokal zwischengespeichert.

- **✎ DE** — eigene **deutsche Übersetzung** für Name und/oder Kartentext
  hinterlegen. Der aktuelle Wert steht grau im Feld; leere Felder lassen die
  Originaldaten unangetastet. Leerst du eine eigene Übersetzung wieder, fällt
  die Karte bis zum nächsten Kartendaten-Update auf Englisch zurück. Deine
  Übersetzungen überleben Kartendaten-Updates.
- **Sammlung → Hinzufügen** — legt die Karte in deinen Bestand (gleiche Drucke
  werden zusammengeführt, nicht dupliziert). Davor wählst du optional das
  **Set** (alle bekannten Drucke der Karte) und die **Sprache** (DE ist
  vorbelegt): Die Kartendaten kennen nur englische Set-Codes — bei Deutsch
  wird der Code passend umgeschrieben (`RA01-EN008` → `RA01-DE008`, alte
  Codes `PSV-E088` → `PSV-G088`). Wie viele Kopien du schon hast, steht im
  Titel der Box.
- **Wortlaut ?** — zerlegt den Kartentext nach dem genormten Wortlaut:
  Bedingung, Kosten, Ziel, Auflösung und Einschränkungen farbig markiert,
  dazu die Effektart (Zünd-, Auslöse-, Schnell-, Dauereffekt …), die Art des
  „Einmal pro Spielzug“ und was „dann“, „und falls du dies tust“ oder
  „außerdem“ bedeuten. Jede Erklärung verlinkt das passende Kapitel im
  Regelwerk. Das ist eine **Lesehilfe** — keine Regelentscheidung.
- **Rulings (n)** — deine **eigenen Rulings** zur Karte (Text + Quelle,
  hinzufügen/bearbeiten/löschen) und Knöpfe zur **Konami-Datenbank**
  (offiziell, mit FAQ) und zu **Yugipedia**. Die App lädt selbst nichts aus
  dem Netz; deine Rulings überleben Kartendaten-Updates, stehen im
  KI-Deck-Export und zeigen sich auf dem Spielfeld als 📌 im Tooltip.
- **Wortlaut ?** und **Rulings** gibt es auch im Detail-Pop-up (Doppelklick
  in Sammlung und Deck, „Details…“ auf dem Spielfeld).
"""),

    ("Sammlung-Tab", """\
# Sammlung

Hier steht dein **physischer Kartenbestand** — was du tatsächlich besitzt.
**Jede Karte steht genau einmal in der Liste**, mit ihrer Gesamtmenge. Hast
du sie in mehreren Drucken (verschiedene Sets oder Sprachen), schlüsselt die
Spalte **Drucke** die Menge auf, z. B. `2× RA01-DE008 (DE) · 1× MP22-EN257
(EN)`.

- **Filter:** nach Name (deutsch oder englisch), Kartenklasse, Attribut und
  Archetyp eingrenzen.
- **Nur unübersetzte** — zeigt nur Karten ohne deutsche Übersetzung. Solche
  Karten sind in der Tabelle **orange** markiert; die Zusammenfassung zählt sie.
- **Menge** ändern: Doppelklick auf die Mengen-Zelle — direkt, solange die
  Karte nur einen Druck hat; bei mehreren Drucken öffnet sich stattdessen
  der Drucke-Dialog.
- **Drucke** bearbeiten: Doppelklick auf die Spalte **Drucke** öffnet einen
  Dialog mit allen Drucken der Karte. Dort änderst du je Druck **Set**
  (Auswahl aus den bekannten Drucken, eigener Code geht auch), **Sprache**
  (schreibt den Set-Code um) und **Menge**, fügst mit **+ Druck** weitere
  hinzu oder entfernst einzelne. Wird ein Druck dadurch zu einem, den es
  schon gibt, werden beide **zusammengeführt** (Mengen addiert, Notizen
  verbunden).
- **Kartenbild:** Maus über den Kartennamen zeigt eine Vorschau,
  **Doppelklick** auf den Namen öffnet die Kartendetails (inkl. ✎ DE).
- **Exportieren…** — speichert die Sammlung als `.txt`, `.pdf`, Markdown
  (`.md`) oder **CSV für Excel** (`.csv`) — wie die Liste **eine Zeile je
  Karte** mit Gesamtmenge, Kartendaten, Drucken und Notizen. Die Sammlung wird
  **immer ohne Effekttexte** exportiert — die gibt es nur beim Deck-Export.
  Sind Filter aktiv, wird nur die gefilterte Ansicht exportiert, sonst die
  gesamte Sammlung.
- **Aktualisieren** — Liste neu laden.
- **Ausgewählte Karte entfernen** — nimmt die markierte Karte mit allen
  Drucken aus dem Bestand (einzelne Drucke entfernst du im Drucke-Dialog).

Identische Drucke einer Karte werden **zusammengeführt** statt mehrfach
gelistet. Neue Karten kommen am einfachsten über den **Suche-Tab**
(„Hinzufügen") in die Sammlung.

> Die Sammlung ist die Grundlage für die Baubarkeits-Anzeigen in Deck- und
> Kombo-Tab („besitzt du schon / müsstest du holen").
"""),

    ("Deck-Tab", """\
# Deck

Links die **Deckliste**, rechts der Deck-Inhalt mit den drei Zonen
**Main / Extra / Side** und der **Kombo-Hilfe**.

## Decks verwalten

- **Neues Deck / Deck löschen** — Decks anlegen und entfernen.
- **Karten hinzufügen:** per **+ Karte hinzufügen** unter der jeweiligen
  Zone (Suche nach Name oder Text).
- **−1 / +1 / Entfernen** — Kopienzahl der markierten Karte ändern.
- **→ Deck / → Side** — Karte zwischen Main/Extra und Side verschieben.
- Maus über dem Kartennamen zeigt das **Kartenbild**, **Doppelklick** öffnet
  die Kartendetails.

**Automatik & Regeln:**

- Die **Zone** (Main oder Extra) wird automatisch aus dem Kartentyp bestimmt
  (Fusion/Synchro/Xyz/Link → Extra Deck).
- Die **3-Kopien-Regel** gilt über alle Zonen zusammen und wird erzwungen.
- **⚠ fehlt n** markiert Karten, für die dein Sammlungsbestand nicht reicht
  (Kopien in anderen Decks binden Bestand). Das ist nur ein **Hinweis** —
  Deckbuilding über den Bestand hinaus bleibt für geplante Käufe erlaubt.

## Import & Export

- **Importieren…** liest `.ydk`-Dateien (das Standardformat; die Karten-ID
  ist der Passcode). Der Import erzwingt die 3-Kopien-Regel, sortiert
  Main/Extra korrekt und meldet unbekannte Passcodes im Bericht, statt
  abzubrechen.
- **Exportieren…** schreibt das Deck als `.ydk` (Passcodes, für YGOPro/
  EDOPro) oder **vollständig** als `.txt`, `.pdf`, Markdown (`.md`) bzw.
  **CSV für Excel** (`.csv`): jede Karte mit Menge, Typzeile, Archetyp,
  vollem Effekttext, Kombo-Rolle und eigenen Rulings; `.txt`/`.pdf`/`.md`
  enthalten zusätzlich Konsistenz und Kombo-Linien (die passen nicht in
  eine Tabelle). So kann z. B. ein KI-System das Deck ohne Nachschlagen
  beurteilen.
- **CSV** ist für deutsches Excel/LibreOffice eingestellt (Semikolon,
  UTF-8 mit BOM) und öffnet sich per Doppelklick mit korrekten Umlauten;
  mehrzeilige Effekttexte stehen in einer Zelle.

## Korpus… (Referenz-Decks)

Über **Korpus…** importierst du fremde Meta-Decklisten als Referenz. Sie
**binden keinen Bestand** und tauchen nicht in deiner Deck-Auswahl auf — sie
speisen nur die Vorschläge (siehe Kombo-Hilfe → Vorschläge). Tagge sie mit
**Quelle** und **Stand (Datum)**; neuere Listen werden höher gewichtet.

## Vergleich… (gegen eine Referenz-Liste)

**Vergleich…** stellt das aktive Deck kopiengenau einer Korpus-/Referenz-
Liste gegenüber (Main + Extra, Side außen vor): was die Referenz **mehr** hat
und was **du mehr** hast, nach Differenz sortiert.

## Einkaufsliste…

**Einkaufsliste…** zeigt, welche Karten dir fehlen, um **alle eigenen Decks
gleichzeitig** aus dem Bestand zu bauen (der Bedarf ist die Summe über alle
Decks; Referenz-Decks zählen nicht) — je Karte Fehlmenge, Bedarf, Bestand und
in welchen Decks sie steckt. Doppelklick öffnet die Kartendetails,
**Exportieren…** speichert die Liste als `.txt`, `.pdf`, `.md` oder
**CSV für Excel**.

## Kombo-Hilfe (rechte Box)

Fünf Reiter:

- **Kombos** — Kombos nach Abdeckung im Deck, mit Bausteinen und Schritten.
  **Fehlende Bausteine ins Deck** ergänzt fehlende Karten; **Neue Kombo aus
  diesem Deck…** legt eine Kombo aus angekreuzten Deck-Karten an.
- **Fahrplan** — Karten je **Rolle** und **Linien zum Boss**; Doppelklick auf
  eine Linie öffnet die Kombo. Oben steht die **Konsistenz** (Wahrscheinlichkeit
  für Starter/Handtrap in der Starthand, Brick-Quote). Jede Linie zeigt ihre
  **Startbarkeit** (Wahrscheinlichkeit, mindestens einen als *Starter*
  eingestuften Baustein der Linie in der Starthand zu haben) und mit `↳n`
  ihre **Interruption-Branches** (Varianten) — eine Linie ohne Branches steht
  bei gegnerischer Störung ohne Plan B da. Details im Tooltip.
- **Vorschläge** — Kartenempfehlungen aus dem Synergie-Graphen samt
  **Begründung** (Tooltip). Doppelklick zeigt die Karte im Suche-Tab,
  **Ins Deck übernehmen** legt den gewählten Vorschlag ins Deck. Oben
  steht der **Engpass** — die Rolle mit den wenigsten Kopien in Main + Extra;
  passende Vorschläge stehen unter „Füllt die Lücke".
- **Starthand** — interaktiver Simulator: Karten ankreuzen (Wahrscheinlichkeit
  „alle zusammen" oder „mindestens eine") und Zufallshände ziehen, mit
  Rollen-/Brick-Verdikt.
- **Hebel** — Was-wäre-wenn je Main-Deck-Karte: Wie verändert **eine Kopie
  mehr oder weniger** die Starter-/Handtrap-Wahrscheinlichkeit? Sortiert
  danach, wo +1 am meisten bringt; ±1 verschiebt auch die Deckgröße, deshalb
  bewegen selbst Karten ohne Rolle die Werte. Klick auf eine Zeile zeigt die
  volle Aufschlüsselung (beide Handgrößen, +1 und −1, inkl. Brick). Liegt eine
  Karte über alle Zonen (inkl. Side) schon 3× im Deck, gibt es kein +1.

**Kombo-Linien exportieren…** schreibt die Linien des Decks als `.txt` oder
`.pdf` — z. B. um sie einem erfahrenen Spieler zum Drüberschauen zu geben.
"""),

    ("Spielfeld-Tab", """\
# Spielfeld

Deck wählen, Starthand ziehen und Linien durchspielen — frei als
Sandkasten oder mit **Regelwerk** und einem **Dummy-Gegner**. Der
Spielzustand selbst wird nicht gespeichert; **Neue Starthand** beginnt von
vorn. Dauerhaft bleibt nur, was du mit dem **Kombo-Recorder** (siehe unten)
ausdrücklich als Kombo speicherst.

## Zug, Phasen und Lebenspunkte

Die Leiste unter der Werkzeugleiste zeigt **Zug** und **Phase** (Draw,
Standby, Main 1, Battle, Main 2, End). Eine Phase anklicken springt dorthin,
**Nächste Phase ▸** geht einen Schritt weiter. **Zug beenden** übergibt an
den Gegner, **Gegnerzug beenden** beginnt deinen nächsten Zug — mit
automatischem Ziehen in der Draw Phase. Mit **5 (First)** beginnst du in
Zug 1, mit **6 (Second)** bist du Zweiter (Zug 2).

Rechts stehen die **Lebenspunkte** beider Spieler (**± LP** für Effekt-
Schaden oder -Gewinne) und das **Protokoll** mit Zugwechseln, Kämpfen und
Regel-Hinweisen. Ein **Doppelklick** auf eine Protokollzeile öffnet das
passende Kapitel im **Regelwerk**-Tab.

## Regelwerk: aus · warnen · erzwingen

Das Auswahlfeld rechts in der Phasenleiste schaltet das Regelwerk (die
Wahl bleibt über App-Neustarts erhalten):

- **Regeln aus** — freier Sandkasten wie früher, keine Rückfragen.
- **Regeln: warnen** (Standard) — Verstöße landen als ⚠ im Protokoll, die
  Aktion passiert trotzdem.
- **Regeln: erzwingen** — bei einem Verstoß fragt die App nach. Mit **Ja**
  führst du die Aktion trotzdem aus („per Effekt erlaubt“), mit **Nein**
  wird sie abgelehnt.

Geprüft wird die **allgemeine Spielmechanik** — nie Kartentexte:

- **Normalbeschwörung/Setzen**: einmal pro Zug in der Main Phase
  (**+1 Normalbeschwörung** gewährt eine zusätzliche per Effekt), ab Stufe 5
  mit Tribut-Auswahl (5–6: 1 Tribut, ab 7: 2 Tribute). Ritual- und Extra-
  Deck-Monster können nicht normalbeschworen werden; Ritualmonster haben
  eine eigene **Ritualbeschwörung** mit Tribut-Auswahl (Stufensumme).
- **Extra Deck**: Beim Ablegen fragt die App nach der Beschwörungsart und
  lässt dich die **Materialien** ankreuzen — mit Live-Prüfung:
  Synchro = genau 1 Empfänger + Stufensumme, Xyz = mindestens 2 Monster
  gleicher Stufe (sie bleiben als Xyz-Material **unter** dem Monster),
  Link = Anzahl passend zum Link-Wert (Link-Monster zählen als 1 oder mit
  ihrem Link-Wert), Fusion = mindestens 2 Materialien (auch aus der Hand).
  Materialien gehen danach automatisch in den Friedhof.
- **Zonen** (Master Rule 2020): Extra-Monsterzonen nur für Monster aus dem
  Extra Deck und nur eine pro Spieler; **Link-Monster aus dem Extra Deck**
  brauchen eine Extra-Monsterzone oder eine Zone, auf die ein **Link-Pfeil**
  zeigt (Fusion/Synchro/Xyz dürfen in jede freie Zone). Gültige Zielzonen leuchten gold, sobald du ein
  Extra-Deck-Monster aufnimmst.
- **Zauber und Fallen**: Zauber nur im eigenen Zug (normale nur in der Main
  Phase, Schnellzauber jederzeit im eigenen Zug), **Fallen erst setzen**
  und frühestens im nächsten Zug aktivieren (gesetzte Schnellzauber
  ebenso). Feldzauber gehören in die Feldzone — ein neuer ersetzt den alten.
  Pendelmonster als Skala nur in die äußeren Zauber-/Fallenzonen.
- **Positionen**: einmal pro Zug, nicht im Zug der Beschwörung; Link-
  Monster nie in Verteidigung. Verdeckte Monster per **Flippbeschwörung**.
- **Kampf**: nur in der eigenen Battle Phase, nicht im ersten Zug des
  Duells, jedes Monster einmal; direkt nur, wenn der Gegner keine Monster
  hat. Die **Schadensberechnung** läuft automatisch (zerstörte Monster in
  den Friedhof ihres Besitzers, Kampfschaden auf die LP).
- **Handlimit**: Wer mit mehr als 6 Karten den Zug beendet, wählt Karten
  zum Abwerfen.

> **Ehrliche Grenze:** Karteneffekte kennt die App nicht — ob eine Karte
> „nicht als Normalbeschwörung“ gerufen werden darf, Materialien eines
> bestimmten Typs braucht oder eine Ausnahme erlaubt, steht im Kartentext.
> Deshalb gibt es im Modus *erzwingen* immer das „Trotzdem ausführen“.
> Pendelbeschwörungen und Gegner-Monster in der Extra-Monsterzone werden
> nicht simuliert, der Gegner spielt keine eigenen Züge. Fehlen Link-Pfeile
> (Kartendaten älter als diese Version), weist das Protokoll darauf hin —
> **Daten → Kartendaten aktualisieren…** lädt sie nach.

## Gegner und Spielmarken

- **Gegner-Karte…** sucht eine beliebige Karte und legt sie auf die
  Gegnerseite (offen/verdeckt) — oder aktiviert sie **aus der Hand**
  (Handtrap direkt in den Gegner-Friedhof, im Recorder als `Eff X (opp)`).
  So spielst du Unterbrechungen wie Ash Blossom durch und hast Ziele für
  Angriffe. Gegner-Karten lassen sich wie eigene verschieben, drehen,
  zerstören (→ Gegner-Friedhof) oder entfernen; ihre Link-Pfeile stehen
  aus deiner Sicht auf dem Kopf.
- **Spielmarke…** erzeugt Spielmarken (Tokens) mit Name, Stufe, ATK/DEF —
  für dich oder den Gegner. Verlassen sie das Feld, verschwinden sie.
- **Gegnerseite zeigen** blendet die obere Hälfte aus, wenn du nur
  goldfishen willst.

## Karten bewegen

- **Linksklick** auf eine Karte (Hand oder Feld) nimmt sie auf (Gold-Rahmen),
  ein Klick auf eine freie Zone legt sie dort ab. Erneuter Klick auf die
  Karte legt sie zurück.
- Mit Regelwerk fragt ein kleines Menü beim Ablegen nach der Art:
  **Normalbeschwörung / Setzen / Spezialbeschwörung (Effekt)** für Monster,
  **Aktivieren / Setzen** für Zauber und Fallen, die Beschwörungsart für
  Extra-Deck-Monster.
- **Rechtsklick** öffnet das Kontextmenü: offen/verdeckt (mit Regeln:
  Flippbeschwörung bzw. Aktivieren), ATK/DEF, **Angreifen…**,
  **Xyz-Material abhängen…**, **Zählmarke ±1**, **Werte ändern…** (ATK/DEF/
  Stufe durch Effekte — veränderte Werte erscheinen gold in der Werte-Leiste
  und zählen im Kampf und bei Materialprüfungen), → Hand / Friedhof /
  Verbannt / Deck (oben), Details. Extra-Deck-Monster gehen stattdessen
  **→ Extra-Deck** zurück (nie in Hand oder Main Deck). Verdeckt/DEF,
  Zählmarken und Werte-Änderungen gelten nur auf dem Feld und werden beim
  Verlassen zurückgesetzt; Xyz-Material fällt dabei in den Friedhof.
- **Klick auf das Deck** zieht eine Karte.

## Stapel (Deck / Extra / Friedhof / Verbannt)

Ein Klick auf einen Stapel öffnet seine Liste. Jede Karte kann direkt ans
Ziel: **Auf die Hand**, **Aufnehmen** (in die Hand + angewählt — der nächste
Zonen-Klick legt sie ab, z. B. für Spezialbeschwörungen aus dem Extra-Deck),
**→ Friedhof** oder **→ Verbannt**. Nach **Deck durchsuchen…** wird
automatisch gemischt.

## Deck-Top ansehen

**Top ansehen…** blättert die obersten N Karten (oberste zuerst) — für
Excavate- und Mill-Effekte. Jede Karte einzeln **auf die Hand**, in den
**Friedhof**, ins **Verbannt** oder **nach unten** legen; der Rest geht in
unveränderter Reihenfolge zurück auf das Deck.

## Rückgängig

**↶ Rückgängig** (oder **Strg+Z**) nimmt die letzte Aktion zurück — Ziehen,
Ablegen, Stapel-Aktionen, Zustandswechsel, auch das Mischen. **Neue
Starthand** leert den Verlauf.

## Kombo aufzeichnen

**● Aufzeichnen** protokolliert ab jetzt jeden Zug als Kombo-Schritt in
Notation (`NS`/`SS`/`Act`/`Set`/`Add`/`Send`/`Mill`/…). **Mit Regelwerk**
steht die Beschwörungsart fest (`NS`, `Set`, `Flip`, `SS`) und Extra-Deck-
Beschwörungen werden zu Formeln wie
`Synchro: Soul (3) + Bone (4) -> Crimson Blade (7)`. **Ohne Regelwerk**
gilt die Heuristik: die **erste** Beschwörung aus der Hand wird `NS`, alle
weiteren `SS`; Karten,
die du aus einem Stapel **aufnimmst** und ablegst, werden `SS <X> (ED/GY/…)`;
das Aufdecken einer gesetzten Zauber/Falle wird `Act`. Drehst du eine eben
abgelegte Karte gleich auf **verdeckt**, wird daraus `Set`. **Rückgängig**
nimmt auch protokollierte Schritte zurück. Ein Deckwechsel oder eine neue
Starthand fragt nach, bevor eine laufende Aufzeichnung verworfen wird.

**■ Speichern…** legt die Aufzeichnung als **Kombo** an: mit den benutzten
Karten als Bausteinen, dem Spielfeld-Deck als **Heimat-Deck** (dadurch
erscheint sie sofort in der Kombo-Hilfe des Deck-Tabs), einem
**Boss-Vorschlag** (letzte Extra-Deck-Beschwörung) und `Start:`/`End:` in
den Notizen. Danach springt die App in den Kombos-Tab. Die Baustein-Mengen
sind auf die Kopien im Deck begrenzt.

> Das Protokoll ist ein **Entwurf**: Es hält fest, *was sich bewegt hat* —
> welcher **Effekt** eine Suche oder Beschwörung ausgelöst hat, weißt nur
> du. Ergänze im Kombos-Tab die `Eff`-Ursachen (ohne Regelwerk auch die
> Synchro-Formeln); die beratende Prüfung zeigt, wo noch etwas fehlt.
> Spielmarken und Gegner-Karten werden nie zu Bausteinen.
"""),

    ("Kombos-Tab", """\
# Kombos

Die **Kombo-Bibliothek** ist das Herz der App: Hier dokumentierst du Spiel-
Linien. Sie liefern Guides, den Deck-Fahrplan **und** die Kartenvorschläge.

Links die Kombo-Liste (mit **Deck-Filter** und **Suchfeld**), rechts der
Editor. Die Suche findet Kombos über Name, Archetyp oder einen **Baustein**
(deutscher oder englischer Kartenname, Groß/Klein egal); passt eine Variante,
erscheint ihre Hauptlinie.

## Varianten (Interruption-Branches)

Eine **Variante** beschreibt, wie eine Hauptlinie bei gegnerischer Störung
weitergeht. **Neue Variante…** legt eine an; über **Variante von** hängst du
eine bestehende Kombo unter eine Hauptlinie. Varianten stehen eingerückt
(`↳`) unter ihrer Hauptlinie. Die Struktur ist bewusst **zweistufig**, und nur
Hauptlinien zählen in Abdeckung, Fahrplan und Vorschlägen.

## Kopfdaten

- **Name / Archetyp** — frei.
- **Heimat-Deck** — optionale Verknüpfung/Filter, **kein Besitz**: jede Kombo
  bleibt gegen jedes Deck abgleichbar.
- **Boss** — das Zielmonster der Linie, gewählt aus den Bausteinen.
- **Notizen** — Rahmendaten der Linie: `Start:` (benötigte Hand-/Feldkarten)
  und `End:` (Endboard). Diese gehören in die Notizen, **nicht** in die Schritte.

## Bausteine

Die Karten der Kombo. **+ Baustein…** sucht und fügt direkt hinzu (kein
Tab-Wechsel); **−1 / +1 / Entfernen** ändern die Anzahl. Über **Rolle** stufst
du den markierten Baustein ein: *Starter / Extender / Payoff / Handtrap*. Die
Rolle gilt **je Kombo**, nicht global für die Karte.

Die Box **„Mit deiner Sammlung"** zeigt, welche Bausteine du schon besitzt und
welche fehlen.

## Schritte & Notation

Im Schritte-Editor steht **eine Zeile pro Schritt**. Darüber liegt die
dauerhafte **Notations-Leiste**: ein Klick auf ein Token (`NS`, `SS`, `Act`,
`Eff1`, `Add`, `Send`, `Banish`, `→`, `[Req:]`, `| Lock:`) fügt es an der
Cursor-Position ein — nur die **Kartennamen** tippst du selbst. Der Button
**Notation…** öffnet die ausführliche Syntax-Referenz.

Eine beratende **Prüfung** warnt unter dem Editor, wenn ein Schritt von der
Notation abweicht — sie **blockiert nie**.

**Durchspielen…** zeigt die Schritte einzeln: der aktuelle Schritt gold,
erledigte normal, kommende abgeblendet (Pfeiltasten oder Buttons).

> **Auto-Speichern:** Name, Archetyp, Notizen und Schritte werden kurz nach der
> Eingabe automatisch gesichert (und immer vor einem Wechsel). Rollen, Boss und
> Heimat-Deck speichern sofort.

Die genaue Syntax findest du im Abschnitt **Kombo-Notation**.
"""),

    ("Kombo-Notation", NOTATION_MD),

    ("Regelwerk-Tab", """\
# Regelwerk

Der Tab **Regelwerk** erklärt, **wie man Yu-Gi-Oh spielt** — auf Deutsch mit
den englischen Fachbegriffen: Spielziel & Deckbau, Spielfeld & Zonen, Zug &
Phasen, Kartenarten, Beschwörungsarten, Positionen & Kampf, Effektarten,
Ketten & Zauberschnelligkeit, **Kartentext lesen**, ein Glossar Deutsch ↔
Englisch und allgemeine Rulings.

- **Suchfeld** über der Kapitelliste: filtert die Kapitel (Groß-/
  Kleinschreibung egal) und markiert die erste Fundstelle.
- **Sprünge hierher:** Links in der **Wortlaut**-Lesehilfe und ein
  **Doppelklick auf eine Zeile im Spielfeld-Protokoll** (z. B. eine
  ⚠-Warnung) öffnen das passende Kapitel.
- **Kartenlinks:** Beispielkarten im Text (Ash Blossom, Infinite
  Impermanence …) sind anklickbar und öffnen die Kartendetails — mit
  **Wortlaut ?** und **Rulings**.

Stand ist die Master Rule (April 2020); bei Widersprüchen gilt das
offizielle Regelheft.
"""),

    ("Daten & Updates", """\
# Daten & Updates

Alles über das Menü **„Daten"**.

## Kartendaten

- **Auf Updates prüfen** — günstige Abfrage, ob eine neuere Kartendatenbank
  vorliegt.
- **Kartendaten aktualisieren…** — lädt die Daten neu (braucht Internet). Läuft
  im Hintergrund, legt vorher eine `.bak`-Sicherung an und aktualisiert Karten
  per UPSERT — **deine Sammlung, Decks und Kombos bleiben unangetastet**.

## App-Version

- **Auf neue App-Version prüfen** — fragt bei GitHub nach einer neueren App.
  Es gibt **keinen Auto-Updater**: Bei einer neuen Version erscheint ein Hinweis
  mit Link zur Download-Seite. Zum Aktualisieren den alten App-Ordner durch den
  neuen ersetzen — deine Daten bleiben erhalten (beim ersten Start einer neuen
  Version wird die Datenbank zusätzlich gesichert).

Die App prüft beim Start still auf neue Versionen; ohne Internet bleibt das
einfach unsichtbar.
"""),

    ("Konzepte & Prinzipien", """\
# Konzepte & Prinzipien

Hintergrund, warum die App sich so verhält:

- **Offline & eigenständig.** Nach dem einmaligen Laden der Kartendaten braucht
  die App kein Internet (außer für Updates).
- **Referenzdaten vs. Benutzerdaten.** Karten sind Referenz; Sammlung, Decks,
  Übersetzungen und Kombos sind deine Daten. Diese Trennung erlaubt gefahrlose
  Karten-Updates.
- **Hinweisen statt blockieren.** Bestands-Warnungen im Deck, die Notations-
  Prüfung in Kombos und die Sammlungs-Abgleiche **warnen nur** — du behältst
  die Kontrolle (z. B. für geplante Käufe).
- **Erklärbar statt opak.** Kartenvorschläge tragen immer ihre Begründung. Die
  App liefert Struktur und Mathematik rund um deine Kombos — sie **löst nicht
  das Spiel** und leitet keine Kombos aus dem Kartentext her.
- **Du lieferst die Daten.** Kombos und Referenz-Decks pflegst du selbst; die
  App scrapt nichts.
"""),
]


class HelpView(DocView):
    """Benutzerhandbuch als eigener Tab: Abschnittsliste mit Suche, rechts
    der gewaehlte Abschnitt (geteilte DocView). Rein statisch (kein
    Repo-Zugriff), daher kein refresh()."""

    def __init__(self) -> None:
        super().__init__([(title, title, md) for title, md in _MANUAL_SECTIONS],
                         placeholder="Handbuch durchsuchen …")
