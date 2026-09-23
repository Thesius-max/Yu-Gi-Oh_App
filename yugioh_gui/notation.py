"""Kombo-Notation als eingebettete Kurzreferenz (Blatt-Modul).

Geteilt von Kombos-Tab (Notation-Dialog) und Handbuch -- eigenes Modul,
damit keine View eine andere importiert.
"""

# In-App-Kurzreferenz der Kombo-Notation (Langform: KOMBO-NOTATION.md).
# Eingebettet statt aus der Datei gelesen, damit sie auch im gepackten
# Build ohne Repo-Dateien verfuegbar ist.
NOTATION_MD = """\
### Schritt-Syntax

```
<AKTION> <Karte> (<Quelle>) [Req: <Bedingung>] -> <Folge> -> <Folge> | Lock: <Einschränkung>
```

Nur `<AKTION> <Karte>` ist Pflicht. **Ein Schritt = eine Aktion** — eine
Beschwörung *oder* eine Effekt-Aktivierung samt direkter Auflösung.
Beschwörungsformeln bekommen immer eine eigene Zeile (Tuner zuerst):

```
Synchro: Soul (2) + Bone (4) -> Red Rising (6)
Xyz: A (4) + B (4) -> Ziel (R4)      Link: A + B -> Ziel (L2)
```

- `->` verkettet Kosten → Wirkung → Resultat
- `| Lock: …` für dauerhafte Einschränkungen, die der Schritt auslöst
- `[Req: …]` für Bedingungen, damit der Schritt legal ist
- `(Ort)` = woher die Karte kommt, `(A -> B)` = Bewegung; offensichtliche
  Ziele entfallen (SS → Feld, Add → Hand)
- Doppelpunkt für die konkrete Wahl: `Add 1 Resonator (Deck): Darkness Resonator`
- Kurznamen sind okay, sobald eindeutig — die Bausteinliste ist die Legende

### Keywords

| Kürzel | Bedeutung |
|---|---|
| NS / SS | Normal / Special Summon |
| Act | Zauber/Falle aktivieren |
| Eff, Eff1, Eff2 | (ersten/zweiten) Effekt aktivieren |
| Add | auf die Hand nehmen (Suche) |
| Send / Banish / Mill | verschieben / verbannen / Deck → GY |
| Draw / Discard / Set | ziehen / abwerfen / setzen |
| Synchro: / Xyz: / Link: / Fusion: | Beschwörungsformel (eigene Zeile) |
| GY / ED | Graveyard / Extra Deck |
| Lvl | Level, z. B. `Lvl ≤4` |

### Notizen der Kombo

```
Start: benötigte Hand-/Feldkarten
End: Endboard / was die Kombo erreicht
```
"""
