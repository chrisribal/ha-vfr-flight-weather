"""Bewertung der Flugbedingungen (ohne Abhängigkeit von Home Assistant, testbar).

Stufen: 0 = grün, 1 = gelb (eingeschränkt), 2 = rot (kein Flugwetter).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import math
from typing import Any
from zoneinfo import ZoneInfo

from .const import (
    CONF_CEIL_LIMIT,
    CONF_CEIL_WARN,
    CONF_GUST_LIMIT,
    CONF_GUST_SPREAD_WARN,
    CONF_GUST_WARN,
    CONF_PRECIP_LIMIT,
    CONF_PRECIP_WARN,
    CONF_VIS_LIMIT,
    CONF_VIS_WARN,
    CONF_WIND_LIMIT,
    CONF_WIND_WARN,
    CONF_XW_LIMIT,
    CONF_XW_WARN,
)

STATUS_GOOD = "gut"
STATUS_LIMITED = "eingeschraenkt"
STATUS_BAD = "schlecht"
STATUS_NONE = "keine_daten"
STATUS_OPTIONS = [STATUS_GOOD, STATUS_LIMITED, STATUS_BAD, STATUS_NONE]

WC_THUNDER = {95, 96, 99}
WC_FREEZING = {56, 57, 66, 67}
WC_FOG = {45, 48}
WC_SNOW_HEAVY = {73, 75, 86}
WC_SNOW_LIGHT = {71, 77, 85}


@dataclass
class GaforWindow:
    """Ein GAFOR-Zeitfenster (UTC-Stunden, Ende exklusiv)."""

    start_utc: int
    end_utc: int
    code: str


@dataclass
class HourRow:
    """Bewertung einer Stunde."""

    d: str
    h: str
    lvl: int = 0
    ws: int = 0
    wg: int = 0
    wd: int = 0
    xw: int = 0
    bahn: str = ""
    ceil: int | None = None
    vis: int | None = None
    pr: float = 0.0
    wc: int = 0
    gafor: str = ""
    why: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Für Attribute und das Frontend."""
        return {
            "d": self.d,
            "h": self.h,
            "lvl": self.lvl,
            "ws": self.ws,
            "wg": self.wg,
            "wd": self.wd,
            "xw": self.xw,
            "bahn": self.bahn,
            "ceil": self.ceil,
            "vis": self.vis,
            "pr": self.pr,
            "wc": self.wc,
            "gafor": self.gafor,
            "why": ", ".join(self.why),
        }


def runway_designator(heading: float) -> str:
    """Bahnbezeichnung aus Kurs, z. B. 102 -> '10'."""
    num = round((heading % 360) / 10) or 36
    return f"{num:02d}"


def crosswind(wind_dir: float, wind_speed: float, runway: float) -> tuple[float, float, float]:
    """Seitenwind, Gegenwind und aktiver Bahnkurs für eine Bahn (beide Richtungen)."""
    ang = math.radians(wind_dir - runway)
    head = wind_speed * math.cos(ang)
    active = runway if head >= 0 else (runway + 180) % 360
    return abs(wind_speed * math.sin(ang)), abs(head), active


def estimate_ceiling(temp: float | None, dew: float | None, low_cover: float | None) -> int | None:
    """Geschätzte Wolkenuntergrenze in ft AGL, nur bei mindestens 50 % tiefer Bewölkung."""
    if temp is None or dew is None or low_cover is None or low_cover < 50:
        return None
    return int(round(max(temp - dew, 0) * 400 / 100) * 100)


def _parse_hm(value: str) -> float:
    return int(value[11:13]) + int(value[14:16]) / 60


def evaluate_hours(
    forecast: dict[str, Any],
    limits: dict[str, float],
    runway: float,
    gafor: list[GaforWindow],
    gafor_date: date | None,
    tz: ZoneInfo,
) -> list[HourRow]:
    """Bewertet alle Tageslichtstunden der Open-Meteo-Vorhersage."""
    hourly = forecast.get("hourly") or {}
    daily = forecast.get("daily") or {}
    times: list[str] = hourly.get("time") or []
    sun: dict[str, tuple[float, float]] = {}
    for i, d in enumerate(daily.get("time") or []):
        try:
            sun[d] = (_parse_hm(daily["sunrise"][i]), _parse_hm(daily["sunset"][i]))
        except (KeyError, IndexError, TypeError, ValueError):
            continue

    def val(key: str, i: int) -> Any:
        arr = hourly.get(key)
        if not arr or i >= len(arr):
            return None
        return arr[i]

    rows: list[HourRow] = []
    for i, ts in enumerate(times):
        d, hh = ts[:10], int(ts[11:13])
        if d not in sun:
            continue
        sunrise, sunset = sun[d]
        if not (hh >= sunrise and hh + 1 <= sunset + 0.5):
            continue

        ws = float(val("wind_speed_10m", i) or 0)
        wd = float(val("wind_direction_10m", i) or 0)
        wg = float(val("wind_gusts_10m", i) or ws)
        xw, _hw, active = crosswind(wd, ws, runway)
        ceil = estimate_ceiling(val("temperature_2m", i), val("dew_point_2m", i), val("cloud_cover_low", i))
        vis = val("visibility", i)
        vis = float(vis) if isinstance(vis, (int, float)) else None
        pr = float(val("precipitation", i) or 0)
        wc = int(val("weather_code", i) or 0)

        row = HourRow(
            d=d,
            h=ts[11:16],
            ws=round(ws),
            wg=round(wg),
            wd=round(wd),
            xw=round(xw),
            bahn=runway_designator(active),
            ceil=ceil,
            vis=round(vis / 1000) if vis is not None else None,
            pr=pr,
            wc=wc,
        )

        def flag(level: int, reason: str) -> None:
            row.lvl = max(row.lvl, level)
            row.why.append(reason)

        def check(value: float | None, warn: float, limit: float, reason: str, below: bool = False) -> None:
            if value is None:
                return
            if (value < limit) if below else (value >= limit):
                flag(2, reason)
            elif (value < warn) if below else (value >= warn):
                flag(1, reason)

        check(row.xw, limits[CONF_XW_WARN], limits[CONF_XW_LIMIT], f"Seitenwind {row.xw} kt")
        check(ws, limits[CONF_WIND_WARN], limits[CONF_WIND_LIMIT], f"Wind {row.ws} kt")
        if wg >= limits[CONF_GUST_LIMIT]:
            flag(2, f"Böen {row.wg} kt")
        elif wg >= limits[CONF_GUST_WARN] or (wg - ws) >= limits[CONF_GUST_SPREAD_WARN]:
            flag(1, f"Böen {row.wg} kt")
        check(ceil, limits[CONF_CEIL_WARN], limits[CONF_CEIL_LIMIT], f"Wolken {ceil} ft", below=True)
        if vis is not None:
            check(vis, limits[CONF_VIS_WARN], limits[CONF_VIS_LIMIT], f"Sicht {vis / 1000:.1f} km", below=True)
        check(pr, limits[CONF_PRECIP_WARN], limits[CONF_PRECIP_LIMIT], f"Niederschlag {pr} mm")

        if wc in WC_THUNDER:
            flag(2, "Gewitter")
        elif wc in WC_FREEZING:
            flag(2, "Vereisung")
        elif wc in WC_FOG:
            flag(2, "Nebel")
        elif wc in WC_SNOW_HEAVY:
            flag(2, "Schneefall")
        elif wc in WC_SNOW_LIGHT:
            flag(1, "Schneefall")

        if gafor and gafor_date and d == gafor_date.isoformat():
            local = datetime(int(d[:4]), int(d[5:7]), int(d[8:10]), hh, tzinfo=tz)
            u = local.astimezone(timezone.utc).hour
            for w in gafor:
                if w.start_utc <= u < w.end_utc and w.code:
                    row.gafor = w.code
                    first = w.code[:1]
                    if first in ("X", "M"):
                        flag(2, f"GAFOR {w.code}")
                    elif first == "D":
                        flag(1, f"GAFOR {w.code}")
                    break

        rows.append(row)
    return rows


def summarize_day(rows: list[HourRow], day: str) -> dict[str, Any]:
    """Tagesampel: grün ab 3 grünen Stunden am Stück, gelb ab 2 Stunden ohne Rot."""
    day_rows = [r for r in rows if r.d == day]
    if not day_rows:
        return {"status": STATUS_NONE, "stufe": -1, "fenster": None, "gruen_h": 0, "gruende": []}

    def longest(pred) -> tuple[int, str | None]:
        best, best_win, run, start = 0, None, 0, None
        for r in day_rows:
            if pred(r):
                run += 1
                if run == 1:
                    start = r.h
                if run > best:
                    end = f"{int(r.h[:2]) + 1:02d}:00"
                    best, best_win = run, f"{start}–{end}"
            else:
                run = 0
        return best, best_win

    g_len, g_win = longest(lambda r: r.lvl == 0)
    n_len, n_win = longest(lambda r: r.lvl < 2)
    reasons: list[str] = []
    for r in day_rows:
        for w in r.why:
            k = w.split(" ")[0]
            if k not in reasons:
                reasons.append(k)

    if g_len >= 3:
        status, stufe, win = STATUS_GOOD, 0, g_win
    elif n_len >= 2:
        status, stufe, win = STATUS_LIMITED, 1, n_win
    else:
        status, stufe, win = STATUS_BAD, 2, None
    return {"status": status, "stufe": stufe, "fenster": win, "gruen_h": g_len, "gruende": reasons}


def day_keys(now: datetime) -> tuple[str, str]:
    """ISO-Datum für heute und morgen in lokaler Zeit."""
    today = now.date()
    return today.isoformat(), (today + timedelta(days=1)).isoformat()
