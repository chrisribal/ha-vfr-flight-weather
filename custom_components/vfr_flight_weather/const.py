"""Konstanten der Integration VFR Flight Weather."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "vfr_flight_weather"
VERSION = "0.2.3"

# Konfiguration (config entry data)
CONF_ICAO = "icao"
CONF_NAME = "name"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_RUNWAY = "runway"
CONF_METAR_STATION = "metar_station"
CONF_TAF_STATION = "taf_station"
CONF_GAFOR_REGION = "gafor_region"

# Grenzwerte (options)
CONF_XW_WARN = "xw_warn"
CONF_XW_LIMIT = "xw_limit"
CONF_WIND_WARN = "wind_warn"
CONF_WIND_LIMIT = "wind_limit"
CONF_GUST_WARN = "gust_warn"
CONF_GUST_LIMIT = "gust_limit"
CONF_GUST_SPREAD_WARN = "gust_spread_warn"
CONF_CEIL_WARN = "ceil_warn"
CONF_CEIL_LIMIT = "ceil_limit"
CONF_VIS_WARN = "vis_warn"
CONF_VIS_LIMIT = "vis_limit"
CONF_PRECIP_WARN = "precip_warn"
CONF_PRECIP_LIMIT = "precip_limit"

DEFAULT_LIMITS: dict[str, float] = {
    CONF_XW_WARN: 8,
    CONF_XW_LIMIT: 15,
    CONF_WIND_WARN: 15,
    CONF_WIND_LIMIT: 20,
    CONF_GUST_WARN: 20,
    CONF_GUST_LIMIT: 25,
    CONF_GUST_SPREAD_WARN: 10,
    CONF_CEIL_WARN: 2500,
    CONF_CEIL_LIMIT: 1500,
    CONF_VIS_WARN: 8000,
    CONF_VIS_LIMIT: 5000,
    CONF_PRECIP_WARN: 0.2,
    CONF_PRECIP_LIMIT: 1.0,
}

GAFOR_NONE = "none"

UPDATE_INTERVAL = timedelta(minutes=10)
FORECAST_MAX_AGE = timedelta(minutes=30)
GAFOR_MAX_AGE = timedelta(minutes=30)

AWC_BASE = "https://aviationweather.gov/api/data"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
GAFOR_URLS: dict[str, str] = {
    "nord": "https://www.dwd.de/DE/fachnutzer/luftfahrt/teaser/luftsportberichte/fbeu40_edzh_node.html",
    "ost": "https://www.dwd.de/DE/fachnutzer/luftfahrt/teaser/luftsportberichte/fbeu40_edzb_node.html",
    "west": "https://www.dwd.de/DE/fachnutzer/luftfahrt/teaser/luftsportberichte/fbeu40_edze_node.html",
    "mitte": "https://www.dwd.de/DE/fachnutzer/luftfahrt/teaser/luftsportberichte/fbeu40_edzf_node.html",
    "sued": "https://www.dwd.de/DE/fachnutzer/luftfahrt/teaser/luftsportberichte/fbeu40_edzm_node.html",
}

USER_AGENT = f"HomeAssistant-VFRFlightWeather/{VERSION} (+https://github.com/chrisribal/ha-vfr-flight-weather)"

FRONTEND_URL_BASE = "/vfr_flight_weather_static"
FRONTEND_FILE = "vfr-flight-weather.js"
