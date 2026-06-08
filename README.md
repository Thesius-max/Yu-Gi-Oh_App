# Yu-Gi-Oh! Sammlung & Deckbuilder

Eine eigenständige Desktop-Anwendung zum Durchsuchen von Yu-Gi-Oh!-Karten,
zum Verwalten der eigenen Sammlung, zum Deckbuilding und zum Anlegen einer
eigenen Kombo-Bibliothek, die beim Deckbau hilft.

Die Kartendaten werden einmalig von der frei verfügbaren
[YGOPRODeck-API](https://ygoprodeck.com/api-guide/) geladen und lokal in
einer SQLite-Datenbank gespeichert. Danach läuft die Anwendung offline.

## Funktionen

- **Suche** – Volltextsuche über Kartenname und Kartentext sowie Filter nach
  Typ, Attribut, Archetyp, Level/Rank und ATK-Bereich. Detailansicht mit Bild,
  Werten und Kartentext.
- **Sammlung** – eigener Bestand getrennt von der Kartenreferenz, mit Menge,
  Set, Edition, Zustand und Sprache. Identische Drucke werden zusammengeführt.
- **Deck** – Main-, Extra- und Side-Deck mit automatischer Zonenzuordnung
  (Fusion/Synchro/Xyz/Link → Extra Deck), 3-Kopien-Regel und Live-Validierung
  (Main 40–60, Extra/Side je max. 15).
- **Kombos** – eigene Kombo-Guides aus Bausteinen (Karten) und Schritten.
  Im Deck-Tab zeigt die Kombo-Hilfe pro Deck die Abdeckung jeder Kombo und
  ergänzt fehlende Bausteine auf Knopfdruck in der richtigen Zone.

## Voraussetzungen

- Python 3.10 oder neuer
- [PySide6](https://pypi.org/project/PySide6/)

Sonst keine externen Abhängigkeiten – der Datenzugriff nutzt ausschließlich
die Python-Standardbibliothek.

## Installation

```bash
git clone https://github.com/<dein-name>/yugioh-sammlung.git
cd yugioh-sammlung
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
neuere Datenbankversion bereitstellt.

## Bedienung in Kürze

- In **Suche** eine Karte finden und auswählen. In der Detailansicht lässt sie
  sich zur Sammlung, zum aktiven Deck (`+ Deck` / `+ Side`) oder als Baustein
  zur aktiven Kombo hinzufügen.
- Im **Deck**-Tab oben ein Deck anlegen/auswählen. Mengen über `−1`/`+1`
  ändern, Karten zwischen Zonen verschieben, und rechts über die Kombo-Hilfe
  fehlende Bausteine ergänzen.
- Im **Kombos**-Tab eine Kombo anlegen, ihre Bausteine pflegen und die
  Schritte erfassen (eine Zeile pro Schritt).

## Projektstruktur

```
yugioh_db.py     Datenschicht: API-Abruf, SQLite-Schema, Suche, Sammlung,
                 Decks und Kombos (nur Standardbibliothek)
yugioh_gui.py    PySide6-Oberfläche mit den vier Tabs
requirements.txt Abhängigkeiten
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

- Kartenbilder werden beim ersten Auswählen synchron geladen (kurze
  Verzögerung); eine Auslagerung in einen Worker-Thread steht noch aus.
- Die Trefferliste zeigt Text statt Thumbnails.
- Mögliche Erweiterungen: Bann-Listen-Auswertung, `.ydk`-Im-/Export, Anzeige
  der Kombo-Schritte im Deck-Tab, Abgleich der Kombos gegen die Sammlung.

## Lizenz

Veröffentlicht unter der MIT-Lizenz – siehe [LICENSE](LICENSE).
