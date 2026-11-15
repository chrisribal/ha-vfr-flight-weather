"""Einrichtung über die Oberfläche: ICAO-Code eingeben, Platzdaten prüfen, Grenzwerte als Optionen."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FlugwetterApiError, async_fetch_gafor, async_lookup_airport, async_nearest_stations
from .const import (
    CONF_GAFOR_M,
    CONF_GAFOR_REGION,
    CONF_ICAO,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_METAR_STATION,
    CONF_NAME,
    CONF_RUNWAY,
    CONF_TAF_STATION,
    DEFAULT_LIMITS,
    DOMAIN,
    GAFOR_M_AMBER,
    GAFOR_M_RED,
    GAFOR_NONE,
)

_LOGGER = logging.getLogger(__name__)
ICAO_RE = re.compile(r"^[A-Z0-9]{3,4}$")
NONE_STATION = "-"


def _station_options(stations: list[tuple[str, str, float]], current: str | None) -> list[selector.SelectOptionDict]:
    opts = [selector.SelectOptionDict(value=NONE_STATION, label="– keine –")]
    seen = set()
    for sid, name, dist in stations[:12]:
        seen.add(sid)
        opts.append(selector.SelectOptionDict(value=sid, label=f"{sid} – {name} ({dist:.0f} km)"))
    if current and current not in seen and current != NONE_STATION:
        opts.append(selector.SelectOptionDict(value=current, label=current))
    return opts


def _gafor_options(regions: dict[str, tuple[Any, Any]], current: str | None) -> list[selector.SelectOptionDict]:
    opts = [selector.SelectOptionDict(value=GAFOR_NONE, label="– kein GAFOR –")]
    for num in sorted(regions):
        opts.append(selector.SelectOptionDict(value=num, label=f"{num} – {regions[num][1].name}"))
    if current and current != GAFOR_NONE and current not in regions:
        opts.append(selector.SelectOptionDict(value=current, label=current))
    return opts


def _details_schema(
    defaults: dict[str, Any],
    metar: list[tuple[str, str, float]],
    taf: list[tuple[str, str, float]],
    gafor: dict[str, tuple[Any, Any]],
    include_name: bool = True,
) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if include_name:
        fields[vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, ""))] = selector.TextSelector()
    fields.update(
        {
            vol.Required(CONF_LATITUDE, default=defaults.get(CONF_LATITUDE)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=-90, max=90, step="any", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_LONGITUDE, default=defaults.get(CONF_LONGITUDE)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=-180, max=180, step="any", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_RUNWAY, default=defaults.get(CONF_RUNWAY, 90)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=180, step=1, unit_of_measurement="°", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_METAR_STATION, default=defaults.get(CONF_METAR_STATION) or NONE_STATION): selector.SelectSelector(
                selector.SelectSelectorConfig(options=_station_options(metar, defaults.get(CONF_METAR_STATION)), custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_TAF_STATION, default=defaults.get(CONF_TAF_STATION) or NONE_STATION): selector.SelectSelector(
                selector.SelectSelectorConfig(options=_station_options(taf, defaults.get(CONF_TAF_STATION)), custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_GAFOR_REGION, default=defaults.get(CONF_GAFOR_REGION) or GAFOR_NONE): selector.SelectSelector(
                selector.SelectSelectorConfig(options=_gafor_options(gafor, defaults.get(CONF_GAFOR_REGION)), mode=selector.SelectSelectorMode.DROPDOWN)
            ),
        }
    )
    return vol.Schema(fields)


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    out = dict(user_input)
    for key in (CONF_METAR_STATION, CONF_TAF_STATION):
        val = str(out.get(key) or "").strip().upper()
        out[key] = "" if val in ("", NONE_STATION) else val
    out[CONF_RUNWAY] = int(out[CONF_RUNWAY])
    return out


class FlugwetterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Einrichtung eines Flugplatzes."""

    VERSION = 1

    def __init__(self) -> None:
        self._defaults: dict[str, Any] = {}
        self._metar: list[tuple[str, str, float]] = []
        self._taf: list[tuple[str, str, float]] = []
        self._gafor: dict[str, tuple[Any, Any]] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            icao = user_input[CONF_ICAO].strip().upper()
            if not ICAO_RE.match(icao):
                errors[CONF_ICAO] = "invalid_icao"
            else:
                await self.async_set_unique_id(icao)
                self._abort_if_unique_id_configured()
                session = async_get_clientsession(self.hass)
                airport = None
                try:
                    airport = await async_lookup_airport(session, icao)
                except FlugwetterApiError as err:
                    _LOGGER.debug("Flugplatzsuche fehlgeschlagen: %s", err)
                lat = airport.latitude if airport else self.hass.config.latitude
                lon = airport.longitude if airport else self.hass.config.longitude
                try:
                    self._metar = await async_nearest_stations(session, "metar", lat, lon)
                    self._taf = await async_nearest_stations(session, "taf", lat, lon)
                except FlugwetterApiError as err:
                    _LOGGER.debug("Stationssuche fehlgeschlagen: %s", err)
                self._gafor = await async_fetch_gafor(session)
                self._defaults = {
                    CONF_ICAO: icao,
                    CONF_NAME: f"{icao} {airport.name}" if airport else icao,
                    CONF_LATITUDE: round(lat, 4),
                    CONF_LONGITUDE: round(lon, 4),
                    CONF_RUNWAY: (airport.runway if airport and airport.runway else 90),
                    CONF_METAR_STATION: self._metar[0][0] if self._metar else "",
                    CONF_TAF_STATION: self._taf[0][0] if self._taf else "",
                    CONF_GAFOR_REGION: GAFOR_NONE,
                }
                self._found = airport is not None
                return await self.async_step_details()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ICAO): selector.TextSelector()}),
            errors=errors,
        )

    async def async_step_details(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            data = {CONF_ICAO: self._defaults[CONF_ICAO], **_clean(user_input)}
            title = data.pop(CONF_NAME) or data[CONF_ICAO]
            return self.async_create_entry(title=title, data=data, options=dict(DEFAULT_LIMITS))

        return self.async_show_form(
            step_id="details",
            data_schema=_details_schema(self._defaults, self._metar, self._taf, self._gafor),
            description_placeholders={
                "icao": self._defaults[CONF_ICAO],
                "found": "gefunden" if getattr(self, "_found", False) else "nicht in der Datenbank – bitte Koordinaten und Bahnrichtung prüfen",
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return FlugwetterOptionsFlow()


LIMIT_FIELDS: list[tuple[str, float, float, float, str]] = [
    ("xw_warn", 0, 40, 1, "kt"),
    ("xw_limit", 0, 40, 1, "kt"),
    ("wind_warn", 0, 60, 1, "kt"),
    ("wind_limit", 0, 60, 1, "kt"),
    ("gust_warn", 0, 80, 1, "kt"),
    ("gust_limit", 0, 80, 1, "kt"),
    ("gust_spread_warn", 0, 40, 1, "kt"),
    ("ceil_warn", 0, 10000, 100, "ft"),
    ("ceil_limit", 0, 10000, 100, "ft"),
    ("vis_warn", 0, 20000, 500, "m"),
    ("vis_limit", 0, 20000, 500, "m"),
    ("precip_warn", 0, 20, 0.1, "mm/h"),
    ("precip_limit", 0, 20, 0.1, "mm/h"),
]


class FlugwetterOptionsFlow(OptionsFlow):
    """Grenzwerte und Platzdaten nachträglich ändern."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["limits", "airfield"])

    async def async_step_limits(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**DEFAULT_LIMITS, **self.config_entry.options}
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **user_input})
        fields: dict[Any, Any] = {
            vol.Required(key, default=current.get(key)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=lo, max=hi, step=step, unit_of_measurement=unit, mode=selector.NumberSelectorMode.BOX)
            )
            for key, lo, hi, step, unit in LIMIT_FIELDS
        }
        fields[vol.Required(CONF_GAFOR_M, default=current.get(CONF_GAFOR_M, GAFOR_M_RED))] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[GAFOR_M_RED, GAFOR_M_AMBER],
                translation_key="gafor_m",
                mode=selector.SelectSelectorMode.LIST,
            )
        )
        return self.async_show_form(step_id="limits", data_schema=vol.Schema(fields))

    async def async_step_airfield(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **_clean(user_input)})
        session = async_get_clientsession(self.hass)
        lat, lon = float(current[CONF_LATITUDE]), float(current[CONF_LONGITUDE])
        try:
            metar = await async_nearest_stations(session, "metar", lat, lon)
            taf = await async_nearest_stations(session, "taf", lat, lon)
        except FlugwetterApiError:
            metar, taf = [], []
        gafor = await async_fetch_gafor(session)
        return self.async_show_form(
            step_id="airfield",
            data_schema=_details_schema(current, metar, taf, gafor, include_name=False),
        )
