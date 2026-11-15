"""Datenabruf und Bewertung für einen Flugplatz."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import logging
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import (
    FlugwetterApiError,
    GaforRegion,
    async_fetch_forecast,
    async_fetch_gafor,
    async_fetch_metar,
    async_fetch_taf,
    gafor_area_for_region,
)
from .const import (
    CONF_GAFOR_REGION,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_METAR_STATION,
    CONF_RUNWAY,
    CONF_TAF_STATION,
    DEFAULT_LIMITS,
    DOMAIN,
    FORECAST_MAX_AGE,
    GAFOR_MAX_AGE,
    GAFOR_NONE,
    UPDATE_INTERVAL,
)
from .evaluation import (
    HourRow,
    apply_metar,
    day_bounds_list,
    day_keys,
    evaluate_hours,
    summarize_day,
    summarize_now,
)

METAR_FOR_NOW_MAX_AGE = 90 * 60  # Sekunden: so lange gilt eine Beobachtung als "jetzt"
HOURS_AHEAD = 36

_LOGGER = logging.getLogger(__name__)

type FlugwetterConfigEntry = ConfigEntry[FlugwetterCoordinator]


@dataclass
class FlugwetterData:
    """Ergebnis eines Abrufs."""

    metar: dict[str, Any] | None = None
    taf: dict[str, Any] | None = None
    gafor_region: GaforRegion | None = None
    gafor_date: date | None = None
    rows: list[HourRow] = field(default_factory=list)
    upcoming: list[HourRow] = field(default_factory=list)
    now: dict[str, Any] = field(default_factory=dict)
    days: list[dict[str, str]] = field(default_factory=list)
    elevation_m: float | None = None
    today: dict[str, Any] = field(default_factory=dict)
    tomorrow: dict[str, Any] = field(default_factory=dict)
    forecast_time: datetime | None = None


class FlugwetterCoordinator(DataUpdateCoordinator[FlugwetterData]):
    """Holt METAR, TAF, Modellvorhersage und GAFOR und bewertet sie."""

    config_entry: FlugwetterConfigEntry

    def __init__(self, hass: HomeAssistant, entry: FlugwetterConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=UPDATE_INTERVAL,
        )
        self._session = async_get_clientsession(hass)
        self._forecast: dict[str, Any] | None = None
        self._forecast_at: datetime | None = None
        self._gafor: tuple[date | None, GaforRegion | None] = (None, None)
        self._gafor_at: datetime | None = None

    @property
    def settings(self) -> dict[str, Any]:
        """Platzdaten, Optionen überschreiben die Ersteinrichtung."""
        return {**self.config_entry.data, **self.config_entry.options}

    @property
    def limits(self) -> dict[str, float]:
        s = self.settings
        return {k: float(s.get(k, v)) for k, v in DEFAULT_LIMITS.items()}

    @property
    def runway(self) -> int:
        return int(self.settings[CONF_RUNWAY])

    async def _async_update_data(self) -> FlugwetterData:
        s = self.settings
        now = dt_util.utcnow()
        data = FlugwetterData()

        # Modellvorhersage: höchstens alle 30 Minuten
        if self._forecast is None or self._forecast_at is None or now - self._forecast_at > FORECAST_MAX_AGE:
            try:
                self._forecast = await async_fetch_forecast(
                    self._session, float(s[CONF_LATITUDE]), float(s[CONF_LONGITUDE]), self.hass.config.time_zone
                )
                self._forecast_at = now
            except FlugwetterApiError as err:
                # Ohne Vorhersage trotzdem weiterlaufen: METAR und TAF bleiben nutzbar,
                # beim nächsten Abruf (alle 10 Minuten) wird es erneut versucht.
                if self._forecast is None:
                    _LOGGER.warning("Vorhersage noch nicht abrufbar, neuer Versuch beim nächsten Abruf: %s", err)
                else:
                    _LOGGER.warning("Vorhersage nicht abrufbar, nutze letzten Stand: %s", err)

        # METAR / TAF: bei Fehlern leer lassen
        if s.get(CONF_METAR_STATION):
            try:
                data.metar = await async_fetch_metar(self._session, s[CONF_METAR_STATION])
            except FlugwetterApiError as err:
                _LOGGER.debug("METAR nicht abrufbar: %s", err)
                data.metar = self.data.metar if self.data else None
        if s.get(CONF_TAF_STATION):
            try:
                data.taf = await async_fetch_taf(self._session, s[CONF_TAF_STATION])
            except FlugwetterApiError as err:
                _LOGGER.debug("TAF nicht abrufbar: %s", err)
                data.taf = self.data.taf if self.data else None

        # Veraltete Meldungen verwerfen: METAR älter als 3 Stunden, TAF nach Ablauf
        ts = now.timestamp()
        if data.metar and isinstance(data.metar.get("obsTime"), (int, float)) and ts - data.metar["obsTime"] > 3 * 3600:
            data.metar = None
        if data.taf and isinstance(data.taf.get("validTimeTo"), (int, float)) and data.taf["validTimeTo"] < ts:
            data.taf = None

        # GAFOR: höchstens alle 30 Minuten
        region = s.get(CONF_GAFOR_REGION) or GAFOR_NONE
        if region != GAFOR_NONE and (self._gafor_at is None or now - self._gafor_at > GAFOR_MAX_AGE):
            area = gafor_area_for_region(region)
            regions = await async_fetch_gafor(self._session, [area] if area else None)
            if region not in regions and area:
                regions = await async_fetch_gafor(self._session)
            if region in regions:
                self._gafor = regions[region]
                self._gafor_at = now
        data.gafor_date, data.gafor_region = self._gafor if region != GAFOR_NONE else (None, None)

        # Bewertung
        tz = ZoneInfo(self.hass.config.time_zone)
        local_now = dt_util.now()
        gafor_valid = data.gafor_region is not None and data.gafor_date == local_now.date()
        gafor_windows = data.gafor_region.windows if gafor_valid and data.gafor_region else []
        gafor_date = data.gafor_date if gafor_valid else None
        lat, lon = float(s[CONF_LATITUDE]), float(s[CONF_LONGITUDE])
        data.rows = evaluate_hours(
            self._forecast or {}, self.limits, self.runway, gafor_windows, gafor_date, tz, lat, lon
        )

        # Laufende Stunde: frische METAR-Beobachtung statt Modell
        metar = data.metar
        if metar and isinstance(metar.get("obsTime"), (int, float)) and ts - metar["obsTime"] <= METAR_FOR_NOW_MAX_AGE:
            for i, row in enumerate(data.rows):
                if row.t <= local_now < row.end:
                    data.rows[i] = apply_metar(row, metar, self.limits, self.runway, gafor_windows, gafor_date)
                    break

        data.upcoming = [r for r in data.rows if r.end > local_now][:HOURS_AHEAD]
        data.now = summarize_now(data.rows, local_now)
        data.days = day_bounds_list(data.rows, lat, lon, tz)
        today, tomorrow = day_keys(local_now)
        data.today = summarize_day(data.rows, today, local_now)
        data.tomorrow = summarize_day(data.rows, tomorrow)
        data.forecast_time = self._forecast_at
        if self._forecast and isinstance(self._forecast.get("elevation"), (int, float)):
            data.elevation_m = float(self._forecast["elevation"])
        return data
