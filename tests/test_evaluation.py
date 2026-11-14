"""Tests der Bewertungslogik und des GAFOR-Parsers."""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from custom_components.flugwetter.api import _normalize_runway, overview_for_region, parse_gafor_page, parse_overview
from custom_components.flugwetter.const import DEFAULT_LIMITS
from custom_components.flugwetter.evaluation import (
    NOW_BAD,
    NOW_GOOD,
    NOW_NIGHT,
    STATUS_BAD,
    STATUS_GOOD,
    STATUS_LIMITED,
    STATUS_OVER,
    GaforWindow,
    apply_metar,
    cover_code,
    crosswind,
    describe_layers,
    estimate_ceiling,
    metar_ceiling,
    summarize_now,
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


def _day(rows, day="2026-11-13"):
    return [r for r in rows if r.d == day and r.tag]


def test_all_hours_rated_daylight_flag():
    fc = _forecast("2026-11-13", range(0, 24))
    rows = evaluate_hours(fc, DEFAULT_LIMITS, 90, [], None, TZ)
    assert len(rows) == 24
    tag = _day(rows)
    # Sonnenauf-/untergang als Ersatz: 07:25–16:20, mindestens 30 Minuten zählen
    assert tag[0].h == "07:00" and tag[-1].h == "15:00"
    s = summarize_day(rows, "2026-11-13")
    assert s["status"] == STATUS_GOOD and s["fenster"] == "07:00–16:00"


def test_civil_twilight_bounds():
    fc = _forecast("2026-11-13", range(0, 24))
    rows = evaluate_hours(fc, DEFAULT_LIMITS, 90, [], None, TZ, 52.07, 11.63)
    tag = _day(rows)
    # BDA ~06:50, BDE ~16:55 in Magdeburg
    assert tag[0].h == "07:00" and tag[-1].h == "16:00"


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


def test_cloud_layers_from_pressure_levels():
    fc = _forecast(
        "2026-11-13",
        range(10, 13),
        **{
            "cloud_cover_1000hPa": [0, 0, 0],
            "geopotential_height_1000hPa": [60, 60, 60],  # unter Platzhöhe -> ignoriert
            "cloud_cover_950hPa": [20, 40, 80],
            "geopotential_height_950hPa": [530, 530, 530],
            "cloud_cover_850hPa": [70, 100, 0],
            "geopotential_height_850hPa": [1450, 1450, 1450],
        },
    )
    fc["elevation"] = 78
    rows = evaluate_hours(fc, DEFAULT_LIMITS, 90, [], None, TZ)
    assert rows[0].wolken == [(1500, 20), (4500, 70)]
    assert rows[0].ceil == 4500 and rows[0].lvl == 0
    assert describe_layers(rows[1].wolken) == "SCT015 OVC045"
    assert rows[2].ceil == 1500 and rows[2].lvl == 1
    assert cover_code(5) == "" and cover_code(95) == "OVC"


def test_now_and_next_window():
    # 07–09 Nebel, ab 10 grün, 13 Böen gelb, 14–15 grün
    codes = [45, 45, 45, 1, 1, 1, 1, 1, 1]
    gusts = [8, 8, 8, 8, 8, 8, 21, 8, 8]
    fc = _forecast("2026-11-13", range(7, 16), weather_code=codes, wind_gusts_10m=gusts)
    rows = evaluate_hours(fc, DEFAULT_LIMITS, 90, [], None, TZ)
    now = datetime(2026, 11, 13, 8, 20, tzinfo=TZ)
    n = summarize_now(rows, now)
    assert n["status"] == NOW_BAD and "Nebel" in n["gruende"]
    assert n["fliegbar_bis"] is None
    assert n["fenster_start"].startswith("2026-11-13T10:00") and n["fenster_ende"].startswith("2026-11-13T13:00")
    assert n["fenster_stufe"] == 0

    now = datetime(2026, 11, 13, 11, 30, tzinfo=TZ)
    n = summarize_now(rows, now)
    assert n["status"] == NOW_GOOD
    assert n["fliegbar_bis"].startswith("2026-11-13T13:00")
    assert n["fenster_start"].startswith("2026-11-13T11:30")

    # Heute ab 11:30: nur Reststunden zählen
    s = summarize_day(rows, "2026-11-13", now)
    assert s["status"] == STATUS_LIMITED

    late = datetime(2026, 11, 13, 17, 0, tzinfo=TZ)
    assert summarize_day(rows, "2026-11-13", late)["status"] == STATUS_OVER


def test_night_status():
    fc = _forecast("2026-11-13", range(0, 24))
    rows = evaluate_hours(fc, DEFAULT_LIMITS, 90, [], None, TZ)
    n = summarize_now(rows, datetime(2026, 11, 13, 2, 0, tzinfo=TZ))
    assert n["status"] == NOW_NIGHT
    assert n["fenster_start"].startswith("2026-11-13T07:00")


def test_metar_overrides_current_hour():
    fc = _forecast("2026-11-13", range(10, 12))
    rows = evaluate_hours(fc, DEFAULT_LIMITS, 90, [], None, TZ)
    metar = {"wdir": 360, "wspd": 12, "wgst": 18, "visib": "6+", "clouds": [{"cover": "FEW", "base": 800}, {"cover": "BKN", "base": 1200}], "wxString": "-RA"}
    obs = apply_metar(rows[0], metar, DEFAULT_LIMITS, 90, [], None)
    assert obs.quelle == "metar" and obs.ceil == 1200 and obs.vis >= 10000
    assert obs.lvl == 2 and "Wolken 1200 ft" in obs.why and "Niederschlag" in obs.why
    assert round(obs.xw) == 12
    assert metar_ceiling({"clouds": [{"cover": "SCT", "base": 2000}]}) is None


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


def test_parse_overview():
    html = (Path(__file__).parent / "fixtures" / "fbeu40_beispiel.html").read_text(encoding="utf-8")
    ov = overview_for_region(parse_overview(html), "20")
    assert ov["titel"] == "Flugwetterübersicht Bereich Ost"
    titles = [a["titel"] for a in ov["abschnitte"]]
    assert titles == ["Wetterlage und -entwicklung", "Wettergeschehen", "Sichtweite", "Höhenwind und -temperatur", "Nullgradgrenze"]
    assert ov["abschnitte"][0]["text"] == "Beispieltext Zeile eins, die im Original umgebrochen ist."
    assert ov["abschnitte"][1]["text"] == "Erster Absatz.\n\nZweiter Absatz & mehr."
    assert "180/10KT" in ov["abschnitte"][3]["text"] and "190/20KT" not in ov["abschnitte"][3]["text"]
    assert ov["naechste"].endswith("05.00 UTC")
