"""Bewertung der Flugbedingungen (ohne Abhängigkeit von Home Assistant, testbar).

Stufen: 0 = grün, 1 = gelb (eingeschränkt), 2 = rot (nicht fliegbar).
Jede Vorhersagestunde wird bewertet. Ob sie am "Tag" liegt (bürgerliche Dämmerung,
also zwischen BDA und BDE), steht getrennt im Feld ``tag``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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

# Tagesbewertung
STATUS_GOOD = "gut"
STATUS_LIMITED = "eingeschraenkt"
STATUS_BAD = "schlecht"
STATUS_OVER = "vorbei"
STATUS_NONE = "keine_daten"
STATUS_OPTIONS = [STATUS_GOOD, STATUS_LIMITED, STATUS_BAD, STATUS_OVER, STATUS_NONE]

# Bewertung der aktuellen Stunde
NOW_GOOD = "fliegbar"
NOW_LIMITED = "eingeschraenkt"
NOW_BAD = "nicht_fliegbar"
NOW_NIGHT = "nacht"
NOW_OPTIONS = [NOW_GOOD, NOW_LIMITED, NOW_BAD, NOW_NIGHT, STATUS_NONE]

WC_THUNDER = {95, 96, 99}
WC_FREEZING = {56, 57, 66, 67}
WC_FOG = {45, 48}
WC_SNOW_HEAVY = {73, 75, 86}
WC_SNOW_LIGHT = {71, 77, 85}

# Druckflächen für die Wolkenschichten (Open-Meteo, DWD ICON)
PRESSURE_LEVELS = (1000, 975, 950, 925, 900, 850, 800, 700, 600)
FT_PER_M = 3.28084
# Bedeckungsgrade: FEW 1–2/8, SCT 3–4/8, BKN 5–7/8, OVC 8/8 (Grenzen in Prozent)
COVER_FEW, COVER_SCT, COVER_BKN, COVER_OVC = 7, 32, 57, 94


@dataclass
class GaforWindow:
    """Ein GAFOR-Zeitfenster (UTC-Stunden, Ende exklusiv)."""

    start_utc: int
    end_utc: int
    code: str


@dataclass
class HourRow:
    """Werte und Bewertung einer Stunde."""

    t: datetime
    tag: bool = True
    lvl: int = 0
    ws: float = 0.0
    wg: float = 0.0
    wd: float = 0.0
    xw: float = 0.0
    bahn: str = ""
    ceil: int | None = None
    vis: float | None = None
    pr: float = 0.0
    wc: int = 0
    wolken: list[tuple[int, int]] = field(default_factory=list)
    wx: str = ""
    gafor: str = ""
    quelle: str = "modell"
    why: list[str] = field(default_factory=list)

    @property
    def d(self) -> str:
        return self.t.date().isoformat()

    @property
    def h(self) -> str:
        return self.t.strftime("%H:%M")

    @property
    def end(self) -> datetime:
        return self.t + timedelta(hours=1)

    def as_dict(self) -> dict[str, Any]:
        """Kompakt für Attribute und das Frontend."""
        return {
            "t": self.t.isoformat(),
            "tag": self.tag,
            "lvl": self.lvl,
            "ws": round(self.ws),
            "wg": round(self.wg),
            "wd": round(self.wd),
            "xw": round(self.xw),
            "bahn": self.bahn,
            "ceil": self.ceil,
            "vis": round(self.vis) if self.vis is not None else None,
            "pr": round(self.pr, 1),
            "wc": self.wc,
            "wk": [[ft, pct] for ft, pct in self.wolken],
            "gafor": self.gafor,
            "q": self.quelle,
            "why": ", ".join(self.why),
        }


# ---------------------------------------------------------------- Hilfsfunktionen


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
    """Geschätzte Wolkenuntergrenze in ft AGL (Spread-Regel), nur bei mindestens 50 % tiefer Bewölkung."""
    if temp is None or dew is None or low_cover is None or low_cover < 50:
        return None
    return int(round(max(temp - dew, 0) * 400 / 100) * 100)


def cover_code(pct: float) -> str:
    """Bedeckungsgrad in Prozent als METAR-Kürzel."""
    if pct >= COVER_OVC:
        return "OVC"
    if pct >= COVER_BKN:
        return "BKN"
    if pct >= COVER_SCT:
        return "SCT"
    if pct >= COVER_FEW:
        return "FEW"
    return ""


def cloud_layers(hourly: dict[str, Any], i: int, elevation_m: float) -> list[tuple[int, int]] | None:
    """Wolkenschichten (ft AGL, Bedeckung %) aus den Druckflächen, None wenn keine Daten."""
    layers: list[tuple[int, int]] = []
    found = False
    for p in PRESSURE_LEVELS:
        cc_arr, gh_arr = hourly.get(f"cloud_cover_{p}hPa"), hourly.get(f"geopotential_height_{p}hPa")
        if not cc_arr or not gh_arr or i >= len(cc_arr) or i >= len(gh_arr):
            continue
        cc, gh = cc_arr[i], gh_arr[i]
        if cc is None or gh is None:
            continue
        found = True
        agl_ft = (float(gh) - elevation_m) * FT_PER_M
        if agl_ft < 0:
            continue
        layers.append((int(round(agl_ft / 100) * 100), int(round(float(cc)))))
    if not found:
        return None
    layers.sort()
    return layers


def ceiling_from_layers(layers: list[tuple[int, int]]) -> int | None:
    """Unterste Schicht mit mindestens BKN."""
    for ft, pct in layers:
        if pct >= COVER_BKN:
            return ft
    return None


def describe_layers(layers: list[tuple[int, int]]) -> str:
    """Kurztext wie 'SCT012 BKN045' (Hunderte ft)."""
    parts = []
    for ft, pct in layers:
        code = cover_code(pct)
        if code:
            parts.append(f"{code}{min(ft // 100, 999):03d}")
    return " ".join(parts)


def civil_twilight(lat: float, lon: float, day: date, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    """Beginn und Ende der bürgerlichen Dämmerung (BDA/BDE) – Grenzen des Flugtags."""
    try:
        from astral import Observer
        from astral.sun import dawn, dusk

        obs = Observer(latitude=lat, longitude=lon)
        return dawn(obs, day, tzinfo=tz), dusk(obs, day, tzinfo=tz)
    except Exception:  # noqa: BLE001 – Polarregionen o. ä.
        return None


def _parse_local(value: str, tz: ZoneInfo) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=tz)


# ---------------------------------------------------------------- Bewertung


def rate_row(row: HourRow, limits: dict[str, float], gafor: list[GaforWindow], gafor_date: date | None) -> HourRow:
    """Ampelstufe und Gründe einer Stunde aus ihren Werten."""
    row.lvl, row.why = 0, []

    def flag(level: int, reason: str) -> None:
        row.lvl = max(row.lvl, level)
        if reason not in row.why:
            row.why.append(reason)

    def check(value: float | None, warn: float, limit: float, reason: str, below: bool = False) -> None:
        if value is None:
            return
        if (value < limit) if below else (value >= limit):
            flag(2, reason)
        elif (value < warn) if below else (value >= warn):
            flag(1, reason)

    check(row.xw, limits[CONF_XW_WARN], limits[CONF_XW_LIMIT], f"Seitenwind {round(row.xw)} kt")
    check(row.ws, limits[CONF_WIND_WARN], limits[CONF_WIND_LIMIT], f"Wind {round(row.ws)} kt")
    if row.wg >= limits[CONF_GUST_LIMIT]:
        flag(2, f"Böen {round(row.wg)} kt")
    elif row.wg >= limits[CONF_GUST_WARN] or (row.wg - row.ws) >= limits[CONF_GUST_SPREAD_WARN]:
        flag(1, f"Böen {round(row.wg)} kt")
    check(row.ceil, limits[CONF_CEIL_WARN], limits[CONF_CEIL_LIMIT], f"Wolken {row.ceil} ft", below=True)
    if row.vis is not None:
        check(row.vis, limits[CONF_VIS_WARN], limits[CONF_VIS_LIMIT], f"Sicht {row.vis / 1000:.1f} km", below=True)
    check(row.pr, limits[CONF_PRECIP_WARN], limits[CONF_PRECIP_LIMIT], f"Niederschlag {round(row.pr, 1)} mm")

    if row.wc in WC_THUNDER:
        flag(2, "Gewitter")
    elif row.wc in WC_FREEZING:
        flag(2, "Vereisung")
    elif row.wc in WC_FOG:
        flag(2, "Nebel")
    elif row.wc in WC_SNOW_HEAVY:
        flag(2, "Schneefall")
    elif row.wc in WC_SNOW_LIGHT:
        flag(1, "Schneefall")

    wx = row.wx.upper()
    if wx:
        if "TS" in wx:
            flag(2, "Gewitter")
        if "FZ" in wx:
            flag(2, "Vereisung")
        if "FG" in wx and not any(p in wx for p in ("BCFG", "MIFG", "PRFG")):
            flag(2, "Nebel")
        if "+SN" in wx or "+RA" in wx or "+SHRA" in wx:
            flag(2, "Starker Niederschlag")
        elif "SN" in wx:
            flag(1, "Schneefall")
        elif any(p in wx for p in ("RA", "DZ", "SH")):
            flag(1, "Niederschlag")

    row.gafor = ""
    if gafor and gafor_date and row.d == gafor_date.isoformat():
        u = row.t.astimezone(timezone.utc).hour
        for w in gafor:
            if w.start_utc <= u < w.end_utc and w.code:
                row.gafor = w.code
                first = w.code[:1]
                if first in ("X", "M"):
                    flag(2, f"GAFOR {w.code}")
                elif first == "D":
                    flag(1, f"GAFOR {w.code}")
                break
    return row


def evaluate_hours(
    forecast: dict[str, Any],
    limits: dict[str, float],
    runway: float,
    gafor: list[GaforWindow],
    gafor_date: date | None,
    tz: ZoneInfo,
    lat: float | None = None,
    lon: float | None = None,
) -> list[HourRow]:
    """Bewertet alle Stunden der Open-Meteo-Vorhersage."""
    hourly = forecast.get("hourly") or {}
    daily = forecast.get("daily") or {}
    times: list[str] = hourly.get("time") or []
    elevation = float(forecast.get("elevation") or 0)

    # Tagesgrenzen: bürgerliche Dämmerung, ersatzweise Sonnenauf- und -untergang
    day_bounds: dict[str, tuple[datetime, datetime]] = {}
    for i, d in enumerate(daily.get("time") or []):
        bounds = None
        if lat is not None and lon is not None:
            bounds = civil_twilight(lat, lon, date.fromisoformat(d), tz)
        if bounds is None:
            try:
                bounds = (_parse_local(daily["sunrise"][i], tz), _parse_local(daily["sunset"][i], tz))
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        day_bounds[d] = bounds

    def val(key: str, i: int) -> Any:
        arr = hourly.get(key)
        if not arr or i >= len(arr):
            return None
        return arr[i]

    rows: list[HourRow] = []
    for i, ts in enumerate(times):
        t = _parse_local(ts, tz)
        bounds = day_bounds.get(ts[:10])
        # Eine Stunde zählt als Tag, wenn mindestens 30 Minuten zwischen BDA und BDE liegen
        tag = False
        if bounds:
            overlap = min(t + timedelta(hours=1), bounds[1]) - max(t, bounds[0])
            tag = overlap >= timedelta(minutes=30)

        ws = float(val("wind_speed_10m", i) or 0)
        wd = float(val("wind_direction_10m", i) or 0)
        wg = float(val("wind_gusts_10m", i) or ws)
        xw, _hw, active = crosswind(wd, ws, runway)
        layers = cloud_layers(hourly, i, elevation)
        if layers is not None:
            ceil = ceiling_from_layers(layers)
        else:
            layers = []
            ceil = estimate_ceiling(val("temperature_2m", i), val("dew_point_2m", i), val("cloud_cover_low", i))
        vis = val("visibility", i)

        row = HourRow(
            t=t,
            tag=tag,
            ws=ws,
            wg=wg,
            wd=wd,
            xw=xw,
            bahn=runway_designator(active),
            ceil=ceil,
            vis=float(vis) if isinstance(vis, (int, float)) else None,
            pr=float(val("precipitation", i) or 0),
            wc=int(val("weather_code", i) or 0),
            wolken=layers,
        )
        rows.append(rate_row(row, limits, gafor, gafor_date))
    return rows


# ---------------------------------------------------------------- METAR


def _metar_visibility_m(visib: Any) -> float | None:
    """AWC liefert die Sicht in Statute Miles, z. B. '6+' oder 3.5."""
    if visib is None:
        return None
    s = str(visib).strip()
    plus = s.endswith("+")
    try:
        miles = float(s.rstrip("+"))
    except ValueError:
        return None
    meters = miles * 1609.34
    return max(meters, 10000.0) if plus or miles >= 6 else meters


def metar_ceiling(metar: dict[str, Any]) -> int | None:
    """Unterste BKN/OVC/VV-Basis aus einer AWC-METAR-Meldung."""
    bases = [
        c.get("base")
        for c in metar.get("clouds") or []
        if isinstance(c, dict) and c.get("cover") in ("BKN", "OVC", "OVX", "VV") and isinstance(c.get("base"), (int, float))
    ]
    return int(min(bases)) if bases else None


def apply_metar(
    row: HourRow,
    metar: dict[str, Any],
    limits: dict[str, float],
    runway: float,
    gafor: list[GaforWindow],
    gafor_date: date | None,
) -> HourRow:
    """Ersetzt die Modellwerte der laufenden Stunde durch die Beobachtung."""
    obs = replace(row, why=[], wolken=list(row.wolken), quelle="metar")
    ws = metar.get("wspd")
    if isinstance(ws, (int, float)):
        obs.ws = float(ws)
        wg = metar.get("wgst")
        obs.wg = float(wg) if isinstance(wg, (int, float)) else obs.ws
        wd = metar.get("wdir")
        if isinstance(wd, (int, float)):
            obs.wd = float(wd)
            obs.xw, _hw, active = crosswind(obs.wd, obs.ws, runway)
            obs.bahn = runway_designator(active)
        else:
            # umlaufender Wind: ungünstigster Fall, voller Wind als Seitenwind
            obs.xw = obs.ws
    obs.ceil = metar_ceiling(metar)
    vis = _metar_visibility_m(metar.get("visib"))
    if vis is not None:
        obs.vis = vis
    obs.wx = str(metar.get("wxString") or "")
    return rate_row(obs, limits, gafor, gafor_date)


# ---------------------------------------------------------------- Zusammenfassungen


def _runs(rows: list[HourRow], pred) -> list[tuple[int, int]]:
    """Zusammenhängende Läufe (Start, Ende exklusiv) in einer Stundenliste."""
    out, start = [], None
    for i, r in enumerate(rows):
        if pred(r):
            if start is None:
                start = i
        elif start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(rows)))
    return out


def summarize_now(rows: list[HourRow], now: datetime) -> dict[str, Any]:
    """Wie ist es jetzt, und wann wird es (wieder) fliegbar?"""
    future = [r for r in rows if r.end > now]
    if not future or future[0].t > now:
        return {"status": STATUS_NONE, "stufe": -1, "gruende": []}
    cur = future[0]
    if not cur.tag:
        status = NOW_NIGHT
    else:
        status = (NOW_GOOD, NOW_LIMITED, NOW_BAD)[cur.lvl]

    result: dict[str, Any] = {
        "status": status,
        "stufe": cur.lvl if cur.tag else 3,
        "gruende": list(cur.why),
        "quelle": cur.quelle,
        "fliegbar_bis": None,
        "fenster_start": None,
        "fenster_ende": None,
        "fenster_stufe": None,
    }

    # Läuft gerade ein Flugfenster, bis wann?
    if cur.tag and cur.lvl <= 1:
        end = cur.end
        for r in future[1:]:
            if r.tag and r.lvl <= cur.lvl:
                end = r.end
            else:
                break
        result["fliegbar_bis"] = end.isoformat()

    # Nächstes grünes Fenster (das laufende zählt mit), sonst nächstes gelbes
    for stufe in (0, 1):
        runs = _runs(future, lambda r, s=stufe: r.tag and r.lvl <= s)
        if runs:
            a, b = runs[0]
            start = max(future[a].t, now)
            result.update(
                fenster_start=start.isoformat(),
                fenster_ende=future[b - 1].end.isoformat(),
                fenster_stufe=stufe,
            )
            break
    return result


def summarize_day(rows: list[HourRow], day: str, now: datetime | None = None) -> dict[str, Any]:
    """Tagesampel über die (verbleibenden) Tagstunden: grün ab 3 grünen Stunden am Stück, gelb ab 2 ohne Rot."""
    all_day = [r for r in rows if r.d == day and r.tag]
    day_rows = [r for r in all_day if now is None or r.end > now]
    if not all_day:
        return {"status": STATUS_NONE, "stufe": -1, "fenster": None, "gruen_h": 0, "gruende": []}
    if not day_rows:
        return {"status": STATUS_OVER, "stufe": -1, "fenster": None, "gruen_h": 0, "gruende": []}

    def longest(pred) -> tuple[int, str | None]:
        best, win = 0, None
        for a, b in _runs(day_rows, pred):
            if b - a > best:
                best = b - a
                win = f"{day_rows[a].h}–{day_rows[b - 1].end.strftime('%H:%M')}"
        return best, win

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


def day_bounds_list(rows: list[HourRow], lat: float, lon: float, tz: ZoneInfo) -> list[dict[str, str]]:
    """BDA/BDE je Vorhersagetag, für die Zeitleiste."""
    out = []
    for d in sorted({r.d for r in rows}):
        b = civil_twilight(lat, lon, date.fromisoformat(d), tz)
        if b:
            out.append({"datum": d, "bda": b[0].isoformat(), "bde": b[1].isoformat()})
    return out
