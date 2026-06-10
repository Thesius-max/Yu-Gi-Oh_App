# Kombo-Notation

Feste Schreibweise für Kombo-Schritte im Kombos-Tab. Ziel: jede Kombo ist
ohne Nachdenken lesbar und alle Kombos sehen gleich aus.

In der App integriert: der Kombo-Editor zeigt diese Notation über den
Button „Notation…" als Kurzreferenz an und weist unter dem Schritte-Editor
auf Abweichungen hin (beratend — gespeichert wird immer).

## Grundregeln

1. **Ein Schritt = eine Aktion** — eine Beschwörung *oder* eine
   Effekt-Aktivierung, samt ihrer direkten Auflösung. Beschwörungsformeln
   (Synchro etc.) bekommen immer eine eigene Zeile.
2. **`->` verkettet** die Teilfolgen innerhalb des Schritts:
   erst Kosten, dann Wirkung, dann Resultat.
3. **`|` am Zeilenende** trägt dauerhafte Einschränkungen, die der Schritt
   auslöst, eingeleitet mit `Lock:`.
4. **`[Req: …]`** direkt hinter der Aktion nennt die Bedingung, damit der
   Schritt überhaupt legal ist.
5. **`(Ort)`** hinter einer Karte sagt, woher sie kommt; `(A -> B)` notiert
   eine Bewegung. Selbstverständliche Ziele weglassen (SS → Feld,
   Add → Hand).
6. **Doppelpunkt für die konkrete Wahl:** erst der generische Effekt, dann
   was tatsächlich genommen wird —
   `Add 1 Resonator (Deck): Darkness Resonator`.
7. **Kurznamen sind erlaubt**, sobald sie im Kombo-Kontext eindeutig sind
   (Soul, Bone, Vision, …) — die Bausteinliste der Kombo ist die Legende.
8. **Sprache:** Keywords und Kartennamen englisch (passt zu den
   API-Kartennamen), freie Anmerkungen nach Belieben.

## Schritt-Syntax

```
<AKTION> <Karte> (<Quelle>) [Req: <Bedingung>] -> <Folge> -> <Folge> | Lock: <Einschränkung>
```

Nur `<AKTION> <Karte>` ist Pflicht, alles andere nach Bedarf.

## Keywords

| Kürzel | Bedeutung |
|---|---|
| `NS` | Normal Summon |
| `SS` | Special Summon (Ziel ohne Angabe: das Feld) |
| `Act` | Zauber/Falle aktivieren bzw. spielen |
| `Eff` / `Eff1` / `Eff2` | Effekt (bzw. ersten/zweiten Effekt) aktivieren |
| `Add` | auf die Hand nehmen (Suche; ersetzt „Search") |
| `Send` | verschieben, z. B. `Send Crimson Gaia (Field -> GY)` |
| `Banish` | verbannen |
| `Mill` | oberste Karte(n) vom Deck in den GY |
| `Draw` / `Discard` / `Set` | ziehen / abwerfen / setzen |
| `Synchro:` | Synchrobeschwörung (Formel, siehe unten) |
| `Req:` | Voraussetzung des Schritts (in `[…]`) |
| `Lock:` | Einschränkung/Lock (nach `|`) |

## Zonen & Sonstiges

| Kürzel | Bedeutung |
|---|---|
| `GY` | Graveyard / Friedhof |
| `ED` | Extra Deck |
| `Hand`, `Deck`, `Field` | wie benannt |
| `Lvl` | Level/Stufe, z. B. `Lvl ≤4` |
| `opp` | Gegner |

## Beschwörungsformel

Eigene Zeile, Tuner zuerst, Level in Klammern:

```
Synchro: <Tuner> (<Lvl>) + <Non-Tuner> (<Lvl>) -> <Ziel> (<Lvl>)
```

## Notizen-Feld der Kombo

Die Schritte beschreiben nur den Ablauf. Rahmendaten gehören in die Notizen:

```
Start: <benötigte Hand-/Feldkarten>
End: <Endboard / was die Kombo erreicht>
Hinweis: <optional, z. B. anfällige Stellen für Handtraps>
```

## Beispiele (die drei Resonator-Kombos)

### Fast Crimson King

Notizen: `Start: Power Vice Dragon — End: The Crimson King`

```
1  SS Power Vice (Hand) [Req: kein eigenes Monster oder nur DARK Synchros] | Lock: nur DARK Synchros
2  Eff Power Vice -> Add 1 Resonator (Deck): Darkness Resonator
3  Eff Darkness Resonator (Hand): reveal 1 "Red Dragon Archfiend"-Synchro (ED) -> SS Darkness Resonator | Lock: nur Synchros
4  Synchro: Darkness Resonator (3) + Power Vice (5) -> The Crimson King (8)
```

### Fast Red Rising

Notizen: `Start: Crimson Gaia — End: Red Rising Dragon + Crimson Resonator im GY`

```
1  Act Crimson Gaia -> Eff1: Add 1 Karte, die "Red Dragon Archfiend" ist/nennt (Deck/GY): Soul Resonator
2  NS Soul -> Eff1: Add 1 Archfiend Lvl ≤4 (Deck): Bone Archfiend | Lock: nur DARK Synchros
3  Eff1 Bone (Hand): Send Crimson Gaia (Field -> GY) -> SS Bone | Lock: nur DARK Dragon Synchros
4  Eff2 Bone: Send Crimson Resonator (Deck -> GY) -> Soul auf Lvl 2
5  Synchro: Soul (2) + Bone (4) -> Red Rising (6)
```

### Red Rising to Crimson King with Tuners on Board

Notizen: `Start: Crimson Gaia — End: The Crimson King + Darkness Resonator auf dem Feld, Spell/Trap-Suche`

```
1  Act Crimson Gaia -> Eff1: Add 1 Karte, die "Red Dragon Archfiend" ist/nennt (Deck/GY): Soul Resonator
2  NS Soul -> Eff1: Add 1 Archfiend Lvl ≤4 (Deck): Bone Archfiend | Lock: nur DARK Synchros
3  Eff1 Bone (Hand): Send Crimson Gaia (Field -> GY) -> SS Bone | Lock: nur DARK Dragon Synchros
4  Eff2 Bone: Send Crimson Resonator (Deck -> GY) -> Soul auf Lvl 2
5  Synchro: Soul (2) + Bone (4) -> Red Rising (6)
6  Eff1 Red Rising -> SS Crimson Resonator (GY)
7  Eff Crimson Resonator -> SS Vision + Darkness Resonator (Deck) | Lock: nur DARK Dragon Synchros
8  Synchro: Vision (2) + Red Rising (6) -> The Crimson King (8)
9  Eff Vision (GY): Add 1 Spell/Trap, die "Red Dragon Archfiend" nennt (Deck)
```
