"""Sensoren der Flugwetter-Integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfLength, UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_GAFOR_REGION, CONF_ICAO, CONF_METAR_STATION, DOMAIN, VERSION
from .coordinator import FlugwetterConfigEntry, FlugwetterCoordinator, FlugwetterData
from .evaluation import (
    NOW_OPTIONS,
    STATUS_OPTIONS,
    crosswind,
    describe_layers,
    runway_designator,
)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FlugwetterSensorDescription(SensorEntityDescription):
    """Beschreibung mit Wert- und Attributfunktion."""

    value_fn: Callable[[FlugwetterCoordinator, FlugwetterData], Any]
    attr_fn: Callable[[FlugwetterCoordinator, FlugwetterData], dict[str, Any]] | None = None


def _day_attrs(key: str) -> Callable[[FlugwetterCoordinator, FlugwetterData], dict[str, Any]]:
    def fn(coord: FlugwetterCoordinator, data: FlugwetterData) -> dict[str, Any]:
        summary = getattr(data, key)
        return {
            "stufe": summary.get("stufe"),
            "fenster": summary.get("fenster"),
            "gruende": summary.get("gruende", []),
            "gruen_stunden": summary.get("gruen_h", 0),
        }

    return fn


def _now_attrs(coord: FlugwetterCoordinator, data: FlugwetterData) -> dict[str, Any]:
    s = coord.settings
    n = data.now
    return {
        "stufe": n.get("stufe"),
        "gruende": n.get("gruende", []),
        "quelle": n.get("quelle"),
        "fliegbar_bis": n.get("fliegbar_bis"),
        "naechstes_fenster_start": n.get("fenster_start"),
        "naechstes_fenster_ende": n.get("fenster_ende"),
        "naechstes_fenster_stufe": n.get("fenster_stufe"),
        "icao": s.get(CONF_ICAO),
        "bahn_kurs": coord.runway,
        "bahnen": f"{runway_designator(coord.runway)}/{runway_designator(coord.runway + 180)}",
        "platzhoehe_ft": round(data.elevation_m * 3.28084) if data.elevation_m is not None else None,
        "grenzwerte": coord.limits,
        "tage": data.days,
        "stunden": [r.as_dict() for r in data.upcoming],
        "vorhersage_stand": data.forecast_time.isoformat() if data.forecast_time else None,
    }


def _next_window(coord: FlugwetterCoordinator, data: FlugwetterData) -> datetime | None:
    n = data.now
    if n.get("fenster_stufe") != 0 or not n.get("fenster_start"):
        return None
    return datetime.fromisoformat(n["fenster_start"])


def _ceiling_value(coord: FlugwetterCoordinator, data: FlugwetterData) -> int | None:
    return data.upcoming[0].ceil if data.upcoming else None


def _ceiling_attrs(coord: FlugwetterCoordinator, data: FlugwetterData) -> dict[str, Any]:
    if not data.upcoming:
        return {}
    r = data.upcoming[0]
    return {"quelle": r.quelle, "bewoelkung": describe_layers(r.wolken), "wolken": [[ft, pct] for ft, pct in r.wolken]}


def _metar_attrs(coord: FlugwetterCoordinator, data: FlugwetterData) -> dict[str, Any]:
    m = data.metar or {}
    obs = m.get("obsTime")
    return {
        "station": m.get("icaoId") or coord.settings.get(CONF_METAR_STATION),
        "stationsname": m.get("name"),
        "beobachtet": datetime.fromtimestamp(obs, timezone.utc).isoformat() if isinstance(obs, (int, float)) else None,
        "wind_richtung": m.get("wdir"),
        "wind_kt": m.get("wspd"),
        "boeen_kt": m.get("wgst"),
        "sicht": m.get("visib"),
        "wolken": m.get("clouds") or [],
        "temperatur": m.get("temp"),
        "taupunkt": m.get("dewp"),
        "qnh": m.get("altim"),
        "flugkategorie": m.get("fltCat"),
    }


def _crosswind_now(coord: FlugwetterCoordinator, data: FlugwetterData) -> float | None:
    m = data.metar or {}
    wd, ws = m.get("wdir"), m.get("wspd")
    if ws is None:
        return None
    if not isinstance(wd, (int, float)):
        return 0.0 if ws == 0 else None
    xw, _hw, _act = crosswind(float(wd), float(ws), coord.runway)
    return round(xw, 1)


def _crosswind_attrs(coord: FlugwetterCoordinator, data: FlugwetterData) -> dict[str, Any]:
    m = data.metar or {}
    wd, ws, wg = m.get("wdir"), m.get("wspd"), m.get("wgst")
    if not isinstance(wd, (int, float)) or ws is None:
        return {}
    _xw, hw, active = crosswind(float(wd), float(ws), coord.runway)
    attrs = {"gegenwind_kt": round(hw, 1), "aktive_bahn": runway_designator(active)}
    if isinstance(wg, (int, float)):
        attrs["seitenwind_boeen_kt"] = round(crosswind(float(wd), float(wg), coord.runway)[0], 1)
    return attrs


def _gafor_value(coord: FlugwetterCoordinator, data: FlugwetterData) -> str | None:
    reg = data.gafor_region
    if not reg or not reg.windows:
        return None
    hour = datetime.now(timezone.utc).hour
    for w in reg.windows:
        if w.start_utc <= hour < w.end_utc:
            return w.code
    return reg.windows[0].code


def _gafor_attrs(coord: FlugwetterCoordinator, data: FlugwetterData) -> dict[str, Any]:
    reg = data.gafor_region
    return {
        "gebiet": coord.settings.get(CONF_GAFOR_REGION),
        "gebietsname": reg.name if reg else None,
        "datum": data.gafor_date.isoformat() if data.gafor_date else None,
        "fenster": [{"von_utc": w.start_utc, "bis_utc": w.end_utc, "code": w.code} for w in reg.windows] if reg else [],
        "uebersicht": reg.overview if reg else None,
    }


SENSORS: tuple[FlugwetterSensorDescription, ...] = (
    FlugwetterSensorDescription(
        key="jetzt",
        translation_key="jetzt",
        device_class=SensorDeviceClass.ENUM,
        options=NOW_OPTIONS,
        value_fn=lambda c, d: d.now.get("status"),
        attr_fn=_now_attrs,
    ),
    FlugwetterSensorDescription(
        key="naechstes_flugfenster",
        translation_key="naechstes_flugfenster",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_window,
        attr_fn=lambda c, d: {"ende": d.now.get("fenster_ende")} if d.now.get("fenster_stufe") == 0 else {},
    ),
    FlugwetterSensorDescription(
        key="wolkenuntergrenze",
        translation_key="wolkenuntergrenze",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.FEET,
        suggested_display_precision=0,
        value_fn=_ceiling_value,
        attr_fn=_ceiling_attrs,
    ),
    FlugwetterSensorDescription(
        key="flugtag_heute",
        translation_key="flugtag_heute",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=lambda c, d: d.today.get("status"),
        attr_fn=_day_attrs("today"),
    ),
    FlugwetterSensorDescription(
        key="flugtag_morgen",
        translation_key="flugtag_morgen",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=lambda c, d: d.tomorrow.get("status"),
        attr_fn=_day_attrs("tomorrow"),
    ),
    FlugwetterSensorDescription(
        key="metar",
        translation_key="metar",
        value_fn=lambda c, d: ((d.metar or {}).get("rawOb") or "")[:255] or None,
        attr_fn=_metar_attrs,
    ),
    FlugwetterSensorDescription(
        key="taf",
        translation_key="taf",
        value_fn=lambda c, d: (d.taf or {}).get("icaoId"),
        attr_fn=lambda c, d: {"text": (d.taf or {}).get("rawTAF"), "ausgegeben": (d.taf or {}).get("issueTime")},
    ),
    FlugwetterSensorDescription(
        key="gafor",
        translation_key="gafor",
        value_fn=_gafor_value,
        attr_fn=_gafor_attrs,
    ),
    FlugwetterSensorDescription(
        key="seitenwind",
        translation_key="seitenwind",
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.KNOTS,
        suggested_display_precision=0,
        value_fn=_crosswind_now,
        attr_fn=_crosswind_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FlugwetterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(FlugwetterSensor(coordinator, desc) for desc in SENSORS)


class FlugwetterSensor(CoordinatorEntity[FlugwetterCoordinator], SensorEntity):
    """Ein Flugwetter-Sensor."""

    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"stunden", "grenzwerte", "wolken", "text", "fenster", "uebersicht", "tage"})
    entity_description: FlugwetterSensorDescription

    def __init__(self, coordinator: FlugwetterCoordinator, description: FlugwetterSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Flugwetter",
            model="Flugplatz",
            sw_version=VERSION,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator, self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None or self.entity_description.attr_fn is None:
            return None
        return self.entity_description.attr_fn(self.coordinator, self.coordinator.data)
