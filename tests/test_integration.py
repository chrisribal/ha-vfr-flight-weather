"""Einrichtung und Sensoren mit simulierten Datenquellen."""

from datetime import date
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from custom_components.flugwetter.api import AirportInfo, GaforRegion
from custom_components.flugwetter.const import DOMAIN
from custom_components.flugwetter.evaluation import GaforWindow

P = "custom_components.flugwetter"


def _forecast(today: str, tomorrow: str):
    times = [f"{d}T{h:02d}:00" for d in (today, tomorrow) for h in range(24)]
    n = len(times)
    return {
        "hourly": {
            "time": times,
            "wind_speed_10m": [6] * n,
            "wind_direction_10m": [100] * n,
            "wind_gusts_10m": [10] * n,
            "temperature_2m": [8] * n,
            "dew_point_2m": [2] * n,
            "cloud_cover_low": [0] * n,
            "visibility": [None] * n,
            "precipitation": [0] * n,
            "weather_code": [1] * n,
        },
        "daily": {
            "time": [today, tomorrow],
            "sunrise": [f"{today}T07:25", f"{tomorrow}T07:27"],
            "sunset": [f"{today}T16:20", f"{tomorrow}T16:18"],
        },
    }


METAR = {"icaoId": "EDAH", "rawOb": "METAR EDAH 130020Z AUTO 28012G22KT 9999 BKN030 05/01 Q1012", "wdir": 280, "wspd": 12, "wgst": 22, "visib": "6+", "clouds": [{"cover": "BKN", "base": 3000}], "temp": 5, "dewp": 1, "altim": 1012, "fltCat": "VFR", "obsTime": int(__import__("time").time()) - 600}
TAF = {"icaoId": "EDAH", "rawTAF": "TAF EDAH 122300Z 1300/1309 28010KT 9999 SCT030", "issueTime": "2026-11-12T23:00:00Z", "validTimeTo": 4102444800}


async def test_full_setup(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    hass.config.language = "de"
    now = dt_util.now()
    today = now.date().isoformat()
    tomorrow = date.fromordinal(now.date().toordinal() + 1).isoformat()
    gafor = {"13": (now.date(), GaforRegion("13", "Vorpommern", [GaforWindow(0, 24, "O")]))}

    with (
        patch(f"{P}.config_flow.async_lookup_airport", return_value=AirportInfo("EDAH", "Heringsdorf Airport", 53.879, 14.152, 102)),
        patch(f"{P}.config_flow.async_nearest_stations", return_value=[("EDAH", "Heringsdorf", 0.2), ("EPSC", "Szczecin", 59.0)]),
        patch(f"{P}.config_flow.async_fetch_gafor", return_value=gafor),
        patch(f"{P}.coordinator.async_fetch_forecast", return_value=_forecast(today, tomorrow)),
        patch(f"{P}.coordinator.async_fetch_metar", return_value=METAR),
        patch(f"{P}.coordinator.async_fetch_taf", return_value=TAF),
        patch(f"{P}.coordinator.async_fetch_gafor", return_value=gafor),
        patch(f"{P}.async_setup", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"icao": "edah"})
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "details"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "EDAH Heringsdorf",
                "latitude": 53.879,
                "longitude": 14.152,
                "runway": 102,
                "metar_station": "EDAH",
                "taf_station": "EDAH",
                "gafor_region": "13",
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "EDAH Heringsdorf"
        await hass.async_block_till_done()

        jetzt = hass.states.get("sensor.edah_heringsdorf_flugwetter_jetzt")
        assert jetzt is not None, [s.entity_id for s in hass.states.async_all()]
        assert jetzt.state in ("fliegbar", "eingeschraenkt", "nacht")
        assert jetzt.attributes["bahn_kurs"] == 102
        assert jetzt.attributes["bahnen"] == "10/28"
        stunden = jetzt.attributes["stunden"]
        assert 1 <= len(stunden) <= 36 and stunden[0]["q"] == "metar" and stunden[0]["ceil"] == 3000
        assert jetzt.attributes["naechstes_fenster_start"] is not None
        assert hass.states.get("sensor.edah_heringsdorf_nachstes_flugfenster") is not None
        ceil = hass.states.get("sensor.edah_heringsdorf_wolkenuntergrenze")
        assert ceil is not None and float(ceil.state) == 3000
        heute = hass.states.get("sensor.edah_heringsdorf_flugtag_heute")
        assert heute.state in ("gut", "eingeschraenkt", "vorbei")
        morgen = hass.states.get("sensor.edah_heringsdorf_flugtag_morgen")
        assert morgen.state == "gut"
        assert __import__("re").fullmatch(r"\d\d:\d\d–\d\d:\d\d", morgen.attributes["fenster"])

        metar = hass.states.get("sensor.edah_heringsdorf_metar")
        assert metar.state.startswith("METAR EDAH")
        assert metar.attributes["qnh"] == 1012

        xw = hass.states.get("sensor.edah_heringsdorf_seitenwind_aktuell")
        assert xw is not None and float(xw.state) < 1
        assert xw.attributes["aktive_bahn"] == "28"

        taf = hass.states.get("sensor.edah_heringsdorf_taf")
        assert taf.state == "EDAH"
        gafor_state = hass.states.get("sensor.edah_heringsdorf_gafor")
        assert gafor_state.state == "O"

        # Doppelte Einrichtung wird abgelehnt
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"icao": "EDAH"})
        assert result["type"] is FlowResultType.ABORT

        # Optionen: Grenzwerte ändern
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.MENU
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "limits"})
        assert result["step_id"] == "limits"
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"gust_warn": 10})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        morgen = hass.states.get("sensor.edah_heringsdorf_flugtag_morgen")
        assert morgen.state == "eingeschraenkt"


async def test_invalid_icao(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"icao": "ab!"})
    assert result["errors"] == {"icao": "invalid_icao"}
