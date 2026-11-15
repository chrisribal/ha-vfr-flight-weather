# VFR Flight Weather für Home Assistant

Flugwetter-Ampel für Sichtflieger (VFR). Für jeden eingerichteten Flugplatz bewertet die Integration jede Stunde Wind, Böen, Seitenwind, Wolkenuntergrenze, Sicht, Niederschlag, Gewitter und den GAFOR des DWD. Sie zeigt, ob jetzt Flugwetter ist und wann das nächste Flugfenster beginnt. Dazu gibt es einen 24-Stunden-Zeitstrahl mit Wind und Wolkenschichten, METAR, TAF und die Flugwetterübersicht des DWD.

> ⚠️ **Nur eine Orientierungshilfe.** Die Ampel ersetzt keine Flugvorbereitung nach SERA/LuftVO, kein Briefing und keinen Blick auf NOTAMs. Modelldaten können falsch sein.

## Funktionen

- Einrichtung über die Oberfläche: ICAO-Code eingeben, Platzdaten, nächste METAR-/TAF-Station und GAFOR-Gebiet auswählen
- Mehrere Flugplätze parallel, jeder als eigenes Gerät
- Persönliche Grenzwerte (gelb/rot) für Seitenwind, Wind, Böen, Wolken, Sicht und Niederschlag
- Bewertung der laufenden Stunde aus dem aktuellen METAR, sonst aus der Modellvorhersage
- Wolkenschichten (FEW/SCT/BKN/OVC) aus dem ICON-Modell
- Dashboard-Strategie „VFR-Flugwetter“ im Dialog *Neues Dashboard → Community-Dashboards*
- Karte `custom:vfr-flight-weather-card` mit den Ansichten `jetzt`, `zeitstrahl`, `wind`, `meldungen`, `dwd` oder `komplett`
- Farben und Schriften aus dem aktiven Home-Assistant-Theme

## Sensoren

| Sensor | Zustand | Wichtige Attribute |
|---|---|---|
| Flugwetter jetzt | `fliegbar`, `eingeschraenkt`, `nicht_fliegbar`, `nacht` | `gruende`, `fliegbar_bis`, `naechstes_fenster_start`, `naechstes_fenster_ende`, `stunden` |
| Nächstes Flugfenster | Zeitpunkt (Beginn des nächsten grünen Fensters) | `ende` |
| Wolkenuntergrenze | ft über Platz, leer wenn keine | `bewoelkung` |
| Flugtag heute / morgen | `gut`, `eingeschraenkt`, `schlecht`, `vorbei` | `fenster`, `gruende` |
| METAR, TAF | Rohtext bzw. Station | Wind, Sicht, Wolken, QNH |
| GAFOR | Code des laufenden Fensters | `fenster`, `uebersicht` (DWD-Text) |
| Seitenwind aktuell | kt laut METAR | `gegenwind_kt`, `aktive_bahn` |

Beispiel-Automation: Push am Vorabend, wenn morgen ein guter Flugtag wird.

```yaml
triggers:
  - trigger: time
    at: "19:00:00"
conditions:
  - condition: state
    entity_id: sensor.edbm_magdeburg_city_flugtag_morgen
    state: gut
actions:
  - action: notify.mobile_app_mein_handy
    data:
      title: "Morgen Flugwetter ✈️"
      message: "{{ state_attr('sensor.edbm_magdeburg_city_flugtag_morgen', 'fenster') }}"
```

## Installation

1. **Einstellungen → Marketplace → Benutzerdefinierte Repositories**: `https://github.com/chrisribal/ha-vfr-flight-weather` als Integration hinzufügen und „VFR Flight Weather“ installieren. Home Assistant neu starten.
2. **Einstellungen → Geräte & Dienste → Integration hinzufügen → VFR Flight Weather**.
3. Dashboard anlegen: **Einstellungen → Dashboards → Dashboard hinzufügen → VFR-Flugwetter**.

## Bewertung

| Kriterium | Gelb ab (Standard) | Rot ab (Standard) |
|---|---|---|
| Seitenwind | 8 kt | 15 kt |
| Wind | 15 kt | 20 kt |
| Böen | 20 kt oder 10 kt über Mittelwind | 25 kt |
| Wolkenuntergrenze (unterste BKN/OVC-Schicht) | unter 2500 ft AGL | unter 1500 ft AGL |
| Sicht | unter 8 km | unter 5 km |
| Niederschlag | 0,2 mm/h | 1,0 mm/h |
| GAFOR | D | M oder X |
| Wetter | leichter Schneefall, Regen (METAR) | Gewitter, Nebel, gefrierender Niederschlag, Schneefall |

Als Flugtag zählt die Zeit zwischen Beginn und Ende der bürgerlichen Dämmerung (BDA–BDE). „Heute“ bewertet nur die verbleibenden Stunden. Ein Tag ist **gut**, wenn mindestens drei Stunden am Stück grün sind, **eingeschränkt**, wenn mindestens zwei Stunden am Stück nicht rot sind.

## Datenquellen

- Modellvorhersage und Wolkenschichten: [Open-Meteo](https://open-meteo.com/) (DWD ICON)
- METAR, TAF, Flugplatzdaten: [Aviation Weather Center](https://aviationweather.gov/data/api/)
- GAFOR und Flugwetterübersicht: [Deutscher Wetterdienst](https://www.dwd.de/DE/fachnutzer/luftfahrt/teaser/luftsportberichte/luftsportberichte_node.html)

Bitte beachte die Nutzungsbedingungen der Datenquellen. Die Produkte des DWD sind nur zur individuellen Flugvorbereitung bestimmt.

## Entwicklung

Voraussetzungen: Python 3.13, ein Home Assistant mit der App „Advanced SSH & Web Terminal“ (SSH-Zugang mit Schlüssel).

```bash
python -m venv .venv && . .venv/bin/activate
pip install pytest-homeassistant-custom-component home-assistant-frontend
pytest -q                      # Tests ohne Home Assistant

cp .env.example .env           # HA_SSH=root@homeassistant.local eintragen
./deploy.sh                    # Integration ins eigene HA kopieren, Konfiguration prüfen, neu starten
./deploy.sh --no-restart       # nur kopieren (z. B. reine Frontend-Änderungen, danach Browser neu laden)
```

In VS Code gibt es dieselben Schritte als Tasks (*Terminal → Run Task*): „Tests“, „Deploy to Home Assistant“ und „Deploy (ohne Neustart)“.

## Lizenz

MIT
