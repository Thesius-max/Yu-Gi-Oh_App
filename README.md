# Yu-Gi-Oh! Sammlung & Deckbuilder

Eine eigenständige Desktop-Anwendung zum Durchsuchen von Yu-Gi-Oh!-Karten,
zum Verwalten der eigenen Sammlung, zum Deckbuilding und zum Anlegen einer
eigenen Kombo-Bibliothek, die beim Deckbau hilft.

Die Kartendaten werden einmalig von der frei verfügbaren
[YGOPRODeck-API](https://ygoprodeck.com/api-guide/) geladen und lokal in
einer SQLite-Datenbank gespeichert. Danach läuft die Anwendung offline.

## Funktionen

- **Suche** – Volltextsuche über Kartenname und Kartentext (deutsch und
  englisch) sowie Filter nach Typ, Attribut, Archetyp, Level/Rank und
  ATK-Bereich, optional begrenzt auf die eigene Sammlung. Detailansicht mit
  Bild, Werten und Kartentext; fehlende deutsche Übersetzungen lassen sich
  über „✎ DE" selbst ergänzen und überleben Daten-Updates.
- **Sammlung** – eigener Bestand getrennt von der Kartenreferenz, mit Menge,
  Set, Edition, Zustand und Sprache. Identische Drucke werden zusammengeführt,
  die Liste ist nach Monster/Zauber/Falle gruppiert und per Namenssuche,
  Klasse, Attribut und Archetyp filterbar.
- **Deck** – Main-, Extra- und Side-Deck mit automatischer Zonenzuordnung
  (Fusion/Synchro/Xyz/Link → Extra Deck), 3-Kopien-Regel und Live-Validierung
  (Main 40–60, Extra/Side je max. 15). Decks lassen sich im `.ydk`-Format
  importieren und exportieren (YGOPro-kompatibel). Der Abgleich mit der
  Sammlung warnt, wenn mehr Kopien verplant sind als physisch vorhanden –
  Kopien in anderen Decks zählen dabei als gebunden.
- **Kombos** – eigene Kombo-Guides aus Bausteinen (Karten) und Schritten,
  mit Rolle je Baustein (Starter/Extender/Payoff/Handtrap), Boss-Zielmonster
  und optionalem Heimat-Deck. Neue Kombos entstehen direkt aus den Karten
  eines Decks („Neue Kombo aus diesem Deck…"); Eingaben speichern
  automatisch. Die Kombo-Hilfe im Deck-Tab zeigt Abdeckung, Schritte, einen
  Deck-Fahrplan (Rollen-Übersicht, Linien je Boss) und exakte
  Starthand-Wahrscheinlichkeiten (≥1 Starter, ≥1 Handtrap, Brick-Quote).
- **Daten-Update** – Menü „Daten" prüft auf neue Kartendaten und lädt sie
  auf Wunsch nach. Eigene Daten (Sammlung, Decks, Kombos, Übersetzungen)
  bleiben dabei erhalten; vorher wird automatisch eine Sicherung angelegt.

## Voraussetzungen

- Python 3.10 oder neuer
- [PySide6](https://pypi.org/project/PySide6/)

Sonst keine externen Abhängigkeiten – der Datenzugriff nutzt ausschließlich
die Python-Standardbibliothek.

## Installation

```bash
git clone https://github.com/Thesius-max/Yu-Gi-Oh_App.git
cd Yu-Gi-Oh_App
pip install -r requirements.txt
```

## Erste Schritte

```bash
# Einmalig die Kartendatenbank laden (benötigt eine Internetverbindung)
python yugioh_db.py build

# Anwendung starten
python yugioh_gui.py
```

`build` legt die Datei `yugioh.sqlite3` im aktuellen Verzeichnis an. Mit
`python yugioh_db.py check` lässt sich später günstig prüfen, ob die API eine
neuere Datenbankversion bereitstellt. Beides geht alternativ direkt in der
App über das Menü **„Daten"** – auch die Erstanlage, falls noch keine
Datenbank existiert.

## Bedienung in Kürze

- In **Suche** eine Karte finden und auswählen. In der Detailansicht lässt sie
  sich zur Sammlung, zum aktiven Deck (`+ Deck` / `+ Side`) oder als Baustein
  zur aktiven Kombo hinzufügen.
- Im **Deck**-Tab oben ein Deck anlegen/auswählen oder eine `.ydk`-Datei
  importieren. Mengen über `−1`/`+1` ändern, Karten zwischen Zonen
  verschieben; rechts hilft die Kombo-Hilfe mit Abdeckung, Fahrplan und
  Konsistenz-Werten und ergänzt fehlende Bausteine auf Knopfdruck.
- Im **Kombos**-Tab Kombos pflegen: Bausteine über „+ Baustein…" suchen,
  je Baustein eine Rolle vergeben, Boss und Heimat-Deck wählen, Schritte
  erfassen (eine Zeile pro Schritt). Name, Archetyp und Schritte speichern
  automatisch. Am schnellsten startet eine Kombo aus dem Deck-Tab heraus
  („Neue Kombo aus diesem Deck…").

## Verteilbares Bundle bauen (für Tester)

Für Endnutzer/Tester ohne Python lässt sich ein eigenständiges Bundle erzeugen
(PyInstaller, One-Folder). Die Kartendatenbank wird als `seed.sqlite3`
mitgeliefert und beim ersten Start in den Nutzerordner kopiert
(`%LOCALAPPDATA%\YugiohSammlung\` bzw. `~/Library/Application Support/…`);
dort liegen anschließend Sammlung, Decks, Kombos und Bilder.

```bash
pip install pyinstaller
python yugioh_db.py build seed.sqlite3   # einmalig die Seed-DB erzeugen
python build_app.py                      # Bundle nach dist/YugiohSammlung/
```

Anschließend `dist/YugiohSammlung/` zippen und weitergeben. Die mitgelieferte
`TESTER_LIESMICH.txt` erklärt Tester*innen Start, SmartScreen-Hinweis,
Datenort und Feedback-Weg.

**Hinweis:** PyInstaller kann nicht cross-kompilieren – ein Windows-Build muss
auf Windows, ein macOS-`.app` auf einem Mac erzeugt werden. Das Skript läuft auf
beiden Plattformen identisch.

## Projektstruktur

```
yugioh_db.py         Datenschicht: API-Abruf, SQLite-Schema, Suche, Sammlung,
                     Decks und Kombos (nur Standardbibliothek)
yugioh_gui.py        PySide6-Oberfläche mit den vier Tabs
build_app.py         Erzeugt das verteilbare PyInstaller-Bundle
TESTER_LIESMICH.txt  Anleitung, die mit ins Tester-Bundle gelegt wird
requirements.txt     Abhängigkeiten
```

Die Anwendung erzeugt zur Laufzeit `yugioh.sqlite3` (Datenbank) und einen
Ordner `card_images/` (lokal zwischengespeicherte Kartenbilder). Beide sind
über `.gitignore` von der Versionskontrolle ausgenommen.

## Hinweise zu den Daten

- Kartendaten und Bilder stammen von YGOPRODeck. Kartenbilder werden gemäß den
  API-Regeln **einmalig heruntergeladen und lokal gespeichert** statt dauerhaft
  verlinkt.
- Dieses Projekt steht in keiner Verbindung zu Konami oder YGOPRODeck.
  „Yu-Gi-Oh!" und alle zugehörigen Marken und Bilder sind Eigentum ihrer
  jeweiligen Rechteinhaber.

## Bekannte Einschränkungen / Ideen

- Kombo-Linien werden bewusst nicht aus dem Kartentext hergeleitet – Kombos
  sind benutzergepflegt, die App liefert Struktur und Mathematik drumherum.
- Die Trefferliste zeigt Text statt Thumbnails.
- Geplante Erweiterungen: Kartenvorschläge über einen Synergie-Graphen aus
  der Kombo-Bibliothek, Referenz-Deck-Korpus (`.ydk`-Import vorhanden) mit
  Co-Occurrence-Statistik, Varianten/Verzweigungen für Kombos (z.B. nach
  einer Interruption).

## Lizenz

Veröffentlicht unter der MIT-Lizenz – siehe [LICENSE](LICENSE).
