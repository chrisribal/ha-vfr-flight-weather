"""Datenquellen: Aviation Weather Center (Flugplatz, METAR, TAF), Open-Meteo (DWD ICON), DWD GAFOR."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from html.parser import HTMLParser
import logging
import math
import re
from typing import Any

import aiohttp

from .const import AWC_BASE, GAFOR_URLS, OPEN_METEO_URL, USER_AGENT
from .evaluation import PRESSURE_LEVELS, GaforWindow

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=30)
HEADERS = {"User-Agent": USER_AGENT}


class FlugwetterApiError(Exception):
    """Fehler beim Abruf einer Datenquelle."""


RETRY_STATUS = {429, 502, 503, 504}


def _describe(url: str, err: Exception) -> str:
    """Verständliche Fehlermeldung (Zeitüberschreitungen haben sonst keinen Text)."""
    if isinstance(err, asyncio.TimeoutError):
        return f"{url}: Zeitüberschreitung nach {int(TIMEOUT.total or 0)} s"
    if isinstance(err, aiohttp.ClientResponseError) and err.status == 429:
        return f"{url}: Dienst überlastet (429), neuer Versuch später"
    return f"{url}: {err}"


async def _request(session: aiohttp.ClientSession, url: str, params: dict[str, Any] | None, as_json: bool) -> Any:
    """GET mit einem zweiten Versuch bei Überlastung oder Zeitüberschreitung."""
    last: Exception | None = None
    for attempt in range(2):
        try:
            async with session.get(url, params=params, timeout=TIMEOUT, headers=HEADERS) as resp:
                if as_json and resp.status == 204:
                    return None
                resp.raise_for_status()
                return await (resp.json(content_type=None) if as_json else resp.text())
        except aiohttp.ClientResponseError as err:
            last = err
            if err.status not in RETRY_STATUS:
                break
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            last = err
            if not isinstance(err, asyncio.TimeoutError):
                break
        if attempt == 0:
            await asyncio.sleep(3)
    assert last is not None
    raise FlugwetterApiError(_describe(url, last)) from last


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict[str, Any] | None = None) -> Any:
    return await _request(session, url, params, True)


async def _get_text(session: aiohttp.ClientSession, url: str) -> str:
    return await _request(session, url, None, False)


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
        "hourly": ",".join(
            [
                "temperature_2m",
                "dew_point_2m",
                "wind_speed_10m",
                "wind_direction_10m",
                "wind_gusts_10m",
                "cloud_cover_low",
                "visibility",
                "precipitation",
                "weather_code",
            ]
            + [f"cloud_cover_{p}hPa" for p in PRESSURE_LEVELS]
            + [f"geopotential_height_{p}hPa" for p in PRESSURE_LEVELS]
        ),
        "daily": "sunrise,sunset",
        "wind_speed_unit": "kn",
        "timezone": tz,
        "forecast_days": "3",
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
    overview: dict[str, Any] | None = None


@dataclass
class GaforBulletin:
    issued: date | None
    regions: dict[str, GaforRegion]


_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.S | re.I)
_HEADING_RE = re.compile(r"^([A-ZÄÖÜ][A-Za-zÄÖÜäöüß \-/]{2,45}):\s*$")
_GROUP_RE = re.compile(r"^GAFOR-Gebiete\s+(\d+)\s*-\s*(\d+)\s*:\s*$")
_TABLE_SECTIONS = ("Höhenwind",)


def parse_overview(html: str) -> dict[str, Any] | None:
    """Flugwetterübersicht (FBEU40) unverändert in Abschnitte gliedern.

    Der Text wird nicht umformuliert: Fließtext-Zeilen eines Absatzes werden nur
    wieder zusammengefügt, Tabellen bleiben zeilengenau erhalten.
    """
    m = _PRE_RE.search(html)
    if not m:
        return None
    text = unescape(m.group(1)).replace("\r", "")
    lines = [ln.rstrip() for ln in text.split("\n")]
    titel = gueltig = naechste = None
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    buf: list[str] = []

    def flush() -> None:
        if current is None:
            return
        if current["tabelle"]:
            current["gruppen"] = _split_groups(buf)
        else:
            paras, para = [], []
            for ln in buf + [""]:
                if ln.strip():
                    para.append(ln.strip())
                elif para:
                    paras.append(" ".join(para))
                    para = []
            current["text"] = "\n\n".join(paras)
        sections.append(current)

    for ln in lines:
        stripped = ln.strip()
        if titel is None and stripped.startswith("Flugwetterübersicht"):
            titel = stripped
            continue
        if gueltig is None and stripped.startswith("gültig"):
            gueltig = stripped
            continue
        if stripped.startswith("Zeiten in UTC") or stripped.startswith("Die nächste planmäßige"):
            flush()
            current, buf = None, []
            if stripped.startswith("Die nächste planmäßige"):
                idx = lines.index(ln)
                naechste = " ".join(x.strip() for x in lines[idx : idx + 2] if x.strip())
            continue
        hm = _HEADING_RE.match(stripped)
        if hm and not _GROUP_RE.match(stripped) and not stripped.startswith("Vorhersagebereich"):
            flush()
            name = hm.group(1).strip()
            current = {"titel": name, "tabelle": name.startswith(_TABLE_SECTIONS)}
            buf = []
            continue
        if current is not None:
            buf.append(ln)
    flush()
    if not sections:
        return None
    return {"titel": titel, "gueltig": gueltig, "abschnitte": sections, "naechste": naechste}


def _split_groups(lines: list[str]) -> list[dict[str, Any]]:
    """Höhenwind-Tabellen je Gebietsgruppe."""
    groups: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for ln in lines:
        gm = _GROUP_RE.match(ln.strip())
        if gm:
            cur = {"von": int(gm.group(1)), "bis": int(gm.group(2)), "zeilen": []}
            groups.append(cur)
            continue
        if not ln.strip():
            continue
        if cur is None:
            cur = {"von": 0, "bis": 99, "zeilen": []}
            groups.append(cur)
        cur["zeilen"].append(ln.rstrip())
    return groups


def overview_for_region(overview: dict[str, Any] | None, region: str) -> dict[str, Any] | None:
    """Übersicht auf die Höhenwind-Tabelle des eigenen Gebiets reduzieren."""
    if not overview:
        return None
    try:
        n = int(region)
    except ValueError:
        n = -1
    out = {**overview, "abschnitte": []}
    for sec in overview["abschnitte"]:
        if sec.get("tabelle"):
            groups = [g for g in sec.get("gruppen", []) if g["von"] <= n <= g["bis"]] or sec.get("gruppen", [])
            out["abschnitte"].append({"titel": sec["titel"], "tabelle": True, "text": "\n".join(groups[0]["zeilen"]) if groups else ""})
        else:
            out["abschnitte"].append({"titel": sec["titel"], "tabelle": False, "text": sec.get("text", "")})
    return out


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
        overview = parse_overview(html)
        for num, reg in bulletin.regions.items():
            reg.overview = overview_for_region(overview, num)
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
