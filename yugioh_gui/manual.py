"""Handbuch-Tab: statische Abschnitte, im Code eingebettet.

_MANUAL_SECTIONS haelt die Markdown-Texte (kein Datei-Zugriff, damit
der gepackte Build sie sicher findet); HelpView rendert sie.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QListWidget, QListWidgetItem, QSplitter, QTextBrowser, QVBoxLayout,
    QWidget
)

from .combos import NOTATION_MD



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

## Die fünf Tabs

| Tab | Wofür |
|---|---|
| **Suche** | Karten finden, Details ansehen, in Sammlung/Deck/Kombo übernehmen |
| **Sammlung** | dein physischer Kartenbestand |
| **Deck** | Decks bauen, prüfen, importieren/exportieren, Kombo-Hilfe |
| **Kombos** | Kombo-Linien dokumentieren (Bausteine, Rollen, Schritte) |
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

## Suchen & filtern

- **Textfeld oben:** durchsucht **Name und Kartentext** (Volltext).
- **Filter:** Typ, Attribut, Archetyp, Level/Rank, ATK von–bis.
  Jede Änderung löst sofort eine neue Suche aus.
- **„Nur meine Sammlung":** beschränkt die Treffer auf Karten, die du besitzt.
- Die Trefferzahl steht unten links.

## Detailansicht (rechts)

Zeigt Bild, Werte und Kartentext der gewählten Karte. Das Bild wird beim
ersten Aufruf einmal lokal zwischengespeichert.

- **✎ DE** — eigene **deutsche Übersetzung** für Name und/oder Kartentext
  hinterlegen. Leere Felder lassen die Originaldaten unangetastet. Deine
  Übersetzungen überleben Kartendaten-Updates.
- **Sammlung → Hinzufügen** — legt die Karte in deinen Bestand (gleiche Drucke
  werden zusammengeführt, nicht dupliziert).
- **Deck → + Deck / + Side** — fügt die Karte dem **aktiven Deck** in die
  passende Zone bzw. ins Side Deck hinzu.
- **Kombo → + als Baustein zur aktiven Kombo** — übernimmt die Karte als
  Baustein der gerade im Kombos-Tab geöffneten Kombo.
"""),

    ("Sammlung-Tab", """\
# Sammlung

Hier steht dein **physischer Kartenbestand** — was du tatsächlich besitzt.

- **Filter:** nach Name, Attribut und Archetyp eingrenzen.
- **Exportieren…** — speichert die Sammlung als lesbare Liste (`.txt`/`.pdf`)
  oder als **Markdown für KI** (`.md`, jede Karte mit vollem Effekttext und
  Menge). Sind Filter aktiv, wird nur die gefilterte Ansicht exportiert,
  sonst die gesamte Sammlung.
- **Aktualisieren** — Liste neu laden.
- **Ausgewählten Eintrag entfernen** — nimmt den markierten Bestand heraus.

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
- **Karten hinzufügen:** im Suche-Tab über **+ Deck / + Side**, oder per
  **+ Karte hinzufügen** im Deck-Tab.
- **−1 / +1 / Entfernen** — Kopienzahl der markierten Karte ändern.
- **→ Deck / → Side** — Karte zwischen Main/Extra und Side verschieben.

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
- **Exportieren…** schreibt das Deck wahlweise als `.ydk` (Passcodes), als
  lesbare Liste (`.txt`/`.pdf`) oder als **Markdown für KI** (`.md`) — Letzteres
  enthält jede Karte mit vollem Effekttext, Rolle, Konsistenz und Kombo-Linien,
  damit ein KI-System das Deck ohne Nachschlagen beurteilen kann.

## Korpus… (Referenz-Decks)

Über **Korpus…** importierst du fremde Meta-Decklisten als Referenz. Sie
**binden keinen Bestand** und tauchen nicht in deiner Deck-Auswahl auf — sie
speisen nur die Vorschläge (siehe Kombo-Hilfe → Vorschläge). Tagge sie mit
**Quelle** und **Stand (Datum)**; neuere Listen werden höher gewichtet.

## Kombo-Hilfe (rechte Box)

Drei Reiter:

- **Kombos** — Kombos nach Abdeckung im Deck, mit Bausteinen und Schritten.
  **Fehlende Bausteine ins Deck** ergänzt fehlende Karten; **Neue Kombo aus
  diesem Deck…** legt eine Kombo aus angekreuzten Deck-Karten an.
- **Fahrplan** — Karten je **Rolle** und **Linien zum Boss**; Doppelklick auf
  eine Linie öffnet die Kombo. Oben steht die **Konsistenz** (Wahrscheinlichkeit
  für Starter/Handtrap in der Starthand, Brick-Quote).
- **Vorschläge** — Kartenempfehlungen aus dem Synergie-Graphen samt
  **Begründung** (Tooltip). Doppelklick zeigt die Karte im Suche-Tab.

**Kombo-Linien exportieren…** schreibt die Linien des Decks als `.txt` oder
`.pdf` — z. B. um sie einem erfahrenen Spieler zum Drüberschauen zu geben.
"""),

    ("Spielfeld-Tab", """\
# Spielfeld (Goldfishing)

Ein **Solitaire-Sandkasten**: Deck wählen, Starthand ziehen und Linien frei
durchspielen — **ohne Regeln, ohne Gegner**. Nichts wird gespeichert;
**Neue Starthand** beginnt von vorn.

## Karten bewegen

- **Linksklick** auf eine Karte (Hand oder Feld) nimmt sie auf (Gold-Rahmen),
  ein Klick auf eine freie Zone legt sie dort ab. Erneuter Klick auf die
  Karte legt sie zurück.
- **Rechtsklick** öffnet das Kontextmenü: offen/verdeckt, ATK/DEF,
  → Hand / Friedhof / Verbannt / Deck (oben), Details.
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
Notation (`NS`/`SS`/`Act`/`Set`/`Add`/`Send`/`Mill`/…). Dabei gilt: die
**erste** Beschwörung aus der Hand wird `NS`, alle weiteren `SS`; Karten,
die du aus einem Stapel **aufnimmst** und ablegst, werden `SS <X> (ED/GY/…)`;
das Aufdecken einer gesetzten Zauber/Falle wird `Act`. **Rückgängig** nimmt
auch protokollierte Schritte zurück.

**■ Speichern…** legt die Aufzeichnung als **Kombo** an: mit den benutzten
Karten als Bausteinen, dem Spielfeld-Deck als **Heimat-Deck** (dadurch
erscheint sie sofort in der Kombo-Hilfe des Deck-Tabs), einem
**Boss-Vorschlag** (letzte Extra-Deck-Beschwörung) und `Start:`/`End:` in
den Notizen. Danach springt die App in den Kombos-Tab.

> Das Protokoll ist ein **Entwurf**: Es hält fest, *was sich bewegt hat* —
> welcher **Effekt** eine Suche oder Beschwörung ausgelöst hat, weißt nur
> du. Ergänze im Kombos-Tab die `Eff`-Ursachen und Synchro-Formeln; die
> beratende Prüfung zeigt, wo noch etwas fehlt.
"""),

    ("Kombos-Tab", """\
# Kombos

Die **Kombo-Bibliothek** ist das Herz der App: Hier dokumentierst du Spiel-
Linien. Sie liefern Guides, den Deck-Fahrplan **und** die Kartenvorschläge.

Links die Kombo-Liste (mit **Deck-Filter**), rechts der Editor.

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

> **Auto-Speichern:** Name, Archetyp, Notizen und Schritte werden kurz nach der
> Eingabe automatisch gesichert (und immer vor einem Wechsel). Rollen, Boss und
> Heimat-Deck speichern sofort.

Die genaue Syntax findest du im Abschnitt **Kombo-Notation**.
"""),

    ("Kombo-Notation", NOTATION_MD),

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


class HelpView(QWidget):
    """Benutzerhandbuch als eigener Tab: links die Abschnittsliste, rechts der
    gewaehlte Abschnitt als gerenderter Text. Rein statisch (kein Repo-Zugriff),
    daher kein refresh()."""

    def __init__(self) -> None:
        super().__init__()
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.index = QListWidget()
        for title, _md in _MANUAL_SECTIONS:
            self.index.addItem(QListWidgetItem(title))
        self.index.currentRowChanged.connect(self._show_section)
        splitter.addWidget(self.index)

        self.content = QTextBrowser()
        self.content.setOpenExternalLinks(True)
        splitter.addWidget(self.content)
        splitter.setSizes([240, 760])

        outer = QVBoxLayout(self)
        outer.addWidget(splitter)
        self.index.setCurrentRow(0)

    def _show_section(self, row: int) -> None:
        if 0 <= row < len(_MANUAL_SECTIONS):
            self.content.setMarkdown(_MANUAL_SECTIONS[row][1])
            self.content.verticalScrollBar().setValue(0)
