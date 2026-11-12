"""Datenquellen: Aviation Weather Center (Flugplatz, METAR, TAF), Open-Meteo (DWD ICON), DWD GAFOR."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
import logging
import math
import re
from typing import Any

import aiohttp

from .const import AWC_BASE, GAFOR_URLS, OPEN_METEO_URL, USER_AGENT
from .evaluation import GaforWindow

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=30)
HEADERS = {"User-Agent": USER_AGENT}


class FlugwetterApiError(Exception):
    """Fehler beim Abruf einer Datenquelle."""


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict[str, Any] | None = None) -> Any:
    try:
        async with session.get(url, params=params, timeout=TIMEOUT, headers=HEADERS) as resp:
            if resp.status == 204:
                return None
            resp.raise_for_status()
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
        raise FlugwetterApiError(f"{url}: {err}") from err


async def _get_text(session: aiohttp.ClientSession, url: str) -> str:
    try:
        async with session.get(url, timeout=TIMEOUT, headers=HEADERS) as resp:
            resp.raise_for_status()
            return await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise FlugwetterApiError(f"{url}: {err}") from err


# ---------------------------------------------------------------- Flugplatz


@dataclass
class AirportInfo:
    """Stammdaten eines Flugplatzes."""

    icao: str
    name: str
    latitude: float
    longitude: float
    runway: int | None


def _normalize_runway(alignment: Any) -> int | None:
    try:
        hdg = int(round(float(alignment))) % 360
    except (TypeError, ValueError):
        return None
    if hdg == 0:
        hdg = 360
    return hdg - 180 if hdg > 180 else hdg


async def async_lookup_airport(session: aiohttp.ClientSession, icao: str) -> AirportInfo | None:
    """Sucht einen Flugplatz beim Aviation Weather Center. Kleine Plätze fehlen dort oft."""
    data = await _get_json(session, f"{AWC_BASE}/airport", {"ids": icao, "format": "json"})
    if not data:
        return None
    ap = data[0]
    runways = ap.get("runways") or []
    runway = None
    if runways:
        longest = max(runways, key=lambda r: int(str(r.get("dimension", "0x0")).split("x")[0] or 0))
        runway = _normalize_runway(longest.get("alignment"))
    return AirportInfo(
        icao=icao,
        name=str(ap.get("name") or icao).title(),
        latitude=float(ap["lat"]),
        longitude=float(ap["lon"]),
        runway=runway,
    )


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def async_nearest_stations(
    session: aiohttp.ClientSession, kind: str, lat: float, lon: float, radius_deg: float = 1.5
) -> list[tuple[str, str, float]]:
    """Stationen mit aktueller METAR- bzw. TAF-Meldung in der Nähe, nach Entfernung sortiert."""
    bbox = f"{lat - radius_deg:.3f},{lon - radius_deg * 1.6:.3f},{lat + radius_deg:.3f},{lon + radius_deg * 1.6:.3f}"
    data = await _get_json(session, f"{AWC_BASE}/{kind}", {"bbox": bbox, "format": "json"}) or []
    seen: dict[str, tuple[str, str, float]] = {}
    for item in data:
        sid = item.get("icaoId")
        if not sid or sid in seen or item.get("lat") is None:
            continue
        dist = _distance_km(lat, lon, float(item["lat"]), float(item["lon"]))
        seen[sid] = (sid, str(item.get("name") or sid), dist)
    return sorted(seen.values(), key=lambda s: s[2])


# ---------------------------------------------------------------- Wetter


async def async_fetch_metar(session: aiohttp.ClientSession, station: str) -> dict[str, Any] | None:
    data = await _get_json(session, f"{AWC_BASE}/metar", {"ids": station, "format": "json"})
    return data[0] if data else None


async def async_fetch_taf(session: aiohttp.ClientSession, station: str) -> dict[str, Any] | None:
    data = await _get_json(session, f"{AWC_BASE}/taf", {"ids": station, "format": "json"})
    return data[0] if data else None


async def async_fetch_forecast(
    session: aiohttp.ClientSession, lat: float, lon: float, tz: str
) -> dict[str, Any]:
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "models": "icon_seamless",
        "hourly": "temperature_2m,dew_point_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
        "cloud_cover_low,visibility,precipitation,weather_code",
        "daily": "sunrise,sunset",
        "wind_speed_unit": "kn",
        "timezone": tz,
        "forecast_days": "2",
    }
    data = await _get_json(session, OPEN_METEO_URL, params)
    if not isinstance(data, dict) or "hourly" not in data:
        raise FlugwetterApiError("Open-Meteo lieferte keine Stundenwerte")
    return data


# ---------------------------------------------------------------- GAFOR


class _TableParser(HTMLParser):
    """Sammelt alle Tabellenzeilen als Listen von Zelltexten."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[bool, list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._is_header = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row, self._is_header = [], False
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            if tag == "th":
                self._is_header = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append((self._is_header, self._row))
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


_WINDOW_RE = re.compile(r"(\d{1,2})\s*-\s*(\d{1,2})\s*UTC")
_DATE_RE = re.compile(r"GAFOR[^<]{0,80}?(\d{2})\.(\d{2})\.(\d{4})")


@dataclass
class GaforRegion:
    number: str
    name: str
    windows: list[GaforWindow] = field(default_factory=list)


@dataclass
class GaforBulletin:
    issued: date | None
    regions: dict[str, GaforRegion]


def parse_gafor_page(html: str) -> GaforBulletin:
    """Liest die GAFOR-Tabelle(n) einer DWD-Seite."""
    parser = _TableParser()
    parser.feed(html)
    regions: dict[str, GaforRegion] = {}
    windows: list[tuple[int, int]] = []
    for is_header, cells in parser.rows:
        if is_header or (cells and cells[0].lower().startswith("gebiet")):
            parsed = [_WINDOW_RE.search(c) for c in cells]
            windows = [(int(m.group(1)), int(m.group(2))) for m in parsed if m]
            continue
        if len(cells) < 3 or not re.fullmatch(r"\d{2}", cells[0]):
            continue
        region = GaforRegion(number=cells[0], name=cells[1])
        for (start, end), code in zip(windows, cells[2:]):
            region.windows.append(GaforWindow(start_utc=start, end_utc=end if end > start else end + 24, code=code.strip()))
        regions[region.number] = region
    issued = None
    m = _DATE_RE.search(html)
    if m:
        try:
            issued = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            issued = None
    return GaforBulletin(issued=issued, regions=regions)


async def async_fetch_gafor(session: aiohttp.ClientSession, areas: list[str] | None = None) -> dict[str, tuple[date | None, GaforRegion]]:
    """Alle GAFOR-Gebiete der gewünschten Bereiche (Standard: alle)."""
    result: dict[str, tuple[date | None, GaforRegion]] = {}
    keys = areas or list(GAFOR_URLS)

    async def one(key: str) -> None:
        try:
            html = await _get_text(session, GAFOR_URLS[key])
        except FlugwetterApiError as err:
            _LOGGER.debug("GAFOR %s nicht abrufbar: %s", key, err)
            return
        bulletin = parse_gafor_page(html)
        for num, reg in bulletin.regions.items():
            result[num] = (bulletin.issued, reg)

    await asyncio.gather(*(one(k) for k in keys))
    return result


def gafor_area_for_region(number: str) -> str | None:
    """Welcher DWD-Bereich ein Gebiet enthält (für gezielten Abruf)."""
    try:
        n = int(number)
    except ValueError:
        return None
    if n <= 10:
        return "nord"
    if n <= 28:
        return "ost"
    if n <= 39:
        return "west"
    if n <= 53 or n == 61:
        return "mitte"
    return "sued"
