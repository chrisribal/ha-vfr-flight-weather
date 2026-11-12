"""Tests der Bewertungslogik und des GAFOR-Parsers."""

from datetime import date
from zoneinfo import ZoneInfo

from custom_components.flugwetter.api import _normalize_runway, parse_gafor_page
from custom_components.flugwetter.const import DEFAULT_LIMITS
from custom_components.flugwetter.evaluation import (
    STATUS_BAD,
    STATUS_GOOD,
    STATUS_LIMITED,
    GaforWindow,
    crosswind,
    estimate_ceiling,
    evaluate_hours,
    runway_designator,
    summarize_day,
)

TZ = ZoneInfo("Europe/Berlin")


def _forecast(day: str, hours: range, **cols):
    n = len(hours)
    base = {
        "time": [f"{day}T{h:02d}:00" for h in hours],
        "wind_speed_10m": [5] * n,
        "wind_direction_10m": [270] * n,
        "wind_gusts_10m": [8] * n,
        "temperature_2m": [10] * n,
        "dew_point_2m": [2] * n,
        "cloud_cover_low": [0] * n,
        "visibility": [20000] * n,
        "precipitation": [0] * n,
        "weather_code": [1] * n,
    }
    for k, v in cols.items():
        base[k] = v
    return {"hourly": base, "daily": {"time": [day], "sunrise": [f"{day}T07:25"], "sunset": [f"{day}T16:20"]}}


def test_runway_helpers():
    assert runway_designator(90) == "09"
    assert runway_designator(270) == "27"
    assert runway_designator(102) == "10"
    assert runway_designator(360) == "36"
    assert _normalize_runway(282) == 102
    assert _normalize_runway(102) == 102
    xw, hw, active = crosswind(260, 12, 90)
    assert round(xw) == 2 and round(hw) == 12 and active == 270


def test_ceiling_estimate():
    assert estimate_ceiling(10, 8, 80) == 800
    assert estimate_ceiling(10, 8, 20) is None


def test_daylight_only_and_good_day():
    fc = _forecast("2026-11-13", range(0, 24))
    rows = evaluate_hours(fc, DEFAULT_LIMITS, 90, [], None, TZ)
    assert [r.h for r in rows][0] == "08:00" and rows[-1].h == "15:00"
    s = summarize_day(rows, "2026-11-13")
    assert s["status"] == STATUS_GOOD and s["fenster"] == "08:00–16:00"


def test_limits_and_reasons():
    hours = range(8, 16)
    fc = _forecast(
        "2026-11-13",
        hours,
        wind_direction_10m=[0] * 8,  # Seitenwind voll
        wind_speed_10m=[5, 9, 16, 5, 5, 5, 5, 5],
        wind_gusts_10m=[8, 12, 22, 8, 8, 8, 8, 8],
        cloud_cover_low=[0, 0, 0, 90, 0, 0, 0, 0],
        dew_point_2m=[2, 2, 2, 8, 2, 2, 2, 2],
        weather_code=[1, 1, 1, 1, 95, 1, 1, 1],
    )
    rows = evaluate_hours(fc, DEFAULT_LIMITS, 90, [], None, TZ)
    assert rows[0].lvl == 0
    assert rows[1].lvl == 1 and "Seitenwind 9 kt" in rows[1].why
    assert rows[2].lvl == 2
    assert rows[3].lvl == 2 and rows[3].ceil == 800
    assert rows[4].lvl == 2 and "Gewitter" in rows[4].why
    s = summarize_day(rows, "2026-11-13")
    assert s["status"] == STATUS_GOOD and s["fenster"] == "13:00–16:00"


def test_gafor_applies_in_utc():
    fc = _forecast("2026-11-13", range(8, 16))
    gafor = [GaforWindow(12, 14, "M2"), GaforWindow(14, 16, "D1")]
    rows = evaluate_hours(fc, DEFAULT_LIMITS, 90, gafor, date(2026, 11, 13), TZ)
    by_h = {r.h: r for r in rows}
    assert by_h["12:00"].gafor == "" and by_h["12:00"].lvl == 0  # 11 UTC
    assert by_h["13:00"].gafor == "M2" and by_h["13:00"].lvl == 2  # 12 UTC
    assert by_h["15:00"].gafor == "D1" and by_h["15:00"].lvl == 1
    s = summarize_day(rows, "2026-11-13")
    assert s["status"] == STATUS_GOOD  # 08–12 grün


def test_bad_and_limited_days():
    fc = _forecast("2026-11-13", range(8, 16), weather_code=[45] * 8)
    assert summarize_day(evaluate_hours(fc, DEFAULT_LIMITS, 90, [], None, TZ), "2026-11-13")["status"] == STATUS_BAD
    fc = _forecast("2026-11-13", range(8, 16), wind_gusts_10m=[21] * 8)
    assert summarize_day(evaluate_hours(fc, DEFAULT_LIMITS, 90, [], None, TZ), "2026-11-13")["status"] == STATUS_LIMITED


GAFOR_HTML = """
<h2>GAFOR Bereich LBZ Berlin 12.11.2026</h2>
<table><thead><tr><th>Gebiet</th><th>Name</th><th>12-14 UTC</th><th>14-16 UTC</th><th>16-18 UTC</th></tr></thead>
<tbody>
<tr><td>19</td><td>Dahme-Spree-Gebiet</td><td>O</td><td>C</td><td>C</td></tr>
<tr><td>20</td><td>Magdeburger Börde und Nördliches Harzvorland</td><td>M2 </td><td> D1 </td><td>O</td></tr>
</tbody></table>
"""


def test_parse_gafor():
    b = parse_gafor_page(GAFOR_HTML)
    assert b.issued == date(2026, 11, 12)
    r = b.regions["20"]
    assert r.name.startswith("Magdeburger")
    assert [(w.start_utc, w.end_utc, w.code) for w in r.windows] == [(12, 14, "M2"), (14, 16, "D1"), (16, 18, "O")]
