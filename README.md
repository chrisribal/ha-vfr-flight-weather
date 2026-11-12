# Flugwetter für Home Assistant

Flugtag-Ampel für Sichtflieger: Für jeden eingerichteten Flugplatz bewertet die Integration stündlich Wind, Böen, Seitenwind, Wolkenuntergrenze, Sicht, Niederschlag, Gewitter und den GAFOR des DWD und fasst das zu einer Ampel für heute und morgen zusammen. Dazu gibt es METAR, TAF und eine Dashboard-Karte mit Windrose, Bahn und Stundenverlauf.

> ⚠️ **Nur eine Orientierungshilfe.** Die Ampel ersetzt keine Flugvorbereitung nach SERA/LuftVO, kein Briefing und keinen Blick auf NOTAMs. Modelldaten und Schätzungen können falsch sein.

## Funktionen

- Einrichtung über die Oberfläche: ICAO-Code eingeben, Platzdaten, nächste METAR-/TAF-Station und GAFOR-Gebiet auswählen
- Mehrere Flugplätze parallel, jeder als eigenes Gerät
- Persönliche Grenzwerte (gelb/rot) für Seitenwind, Wind, Böen, Wolken, Sicht und Niederschlag
- Sensoren: Flugtag heute, Flugtag morgen, METAR, TAF, GAFOR, Seitenwind aktuell
- Dashboard-Strategie „Flugwetter“ im Dialog *Neues Dashboard → Community-Dashboards*
- Karte `custom:flugwetter-card` für eigene Dashboards

## Installation

1. In Home Assistant ab 2026.11: **Einstellungen → Marketplace**, Repository hinzufügen und „Flugwetter“ installieren.
2. **Einstellungen → Geräte & Dienste → Integration hinzufügen → Flugwetter**.
3. Dashboard anlegen: **Einstellungen → Dashboards → Dashboard hinzufügen → Flugwetter**.

## Bewertung

| Kriterium | Gelb ab (Standard) | Rot ab (Standard) |
|---|---|---|
| Seitenwind | 8 kt | 15 kt |
| Wind | 15 kt | 20 kt |
| Böen | 20 kt oder 10 kt über Mittelwind | 25 kt |
| Wolkenuntergrenze (geschätzt, ab 50 % tiefer Bewölkung) | unter 2500 ft AGL | unter 1500 ft AGL |
| Sicht | unter 8 km | unter 5 km |
| Niederschlag | 0,2 mm/h | 1,0 mm/h |
| GAFOR | D | M oder X |
| Wetter | leichter Schneefall | Gewitter, Nebel, gefrierender Niederschlag, Schneefall |

Bewertet werden nur Stunden zwischen Sonnenauf- und -untergang. Ein Tag ist **gut**, wenn mindestens drei Stunden am Stück grün sind, **eingeschränkt**, wenn mindestens zwei Stunden am Stück nicht rot sind.

## Datenquellen

- Modellvorhersage: [Open-Meteo](https://open-meteo.com/) (DWD ICON)
- METAR, TAF, Flugplatzdaten: [Aviation Weather Center](https://aviationweather.gov/data/api/)
- GAFOR: [Deutscher Wetterdienst](https://www.dwd.de/DE/fachnutzer/luftfahrt/teaser/luftsportberichte/luftsportberichte_node.html)

Bitte beachte die Nutzungsbedingungen der Datenquellen.

## Lizenz

MIT
