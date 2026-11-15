"""VFR Flight Weather: Flugwetter-Ampel, METAR, TAF und GAFOR für Flugplätze."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, FRONTEND_FILE, FRONTEND_URL_BASE, VERSION
from .coordinator import FlugwetterConfigEntry, FlugwetterCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Liefert Karte und Dashboard-Strategie an den Browser aus."""
    path = Path(__file__).parent / "frontend" / FRONTEND_FILE
    url = f"{FRONTEND_URL_BASE}/{FRONTEND_FILE}"
    await hass.http.async_register_static_paths([StaticPathConfig(url, str(path), cache_headers=True)])
    add_extra_js_url(hass, f"{url}?v={VERSION}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: FlugwetterConfigEntry) -> bool:
    coordinator = FlugwetterCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: FlugwetterConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FlugwetterConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
