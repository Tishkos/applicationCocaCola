"""The analyzer export (plantcollector/energy_export.py): kWh for today and the two days before, from the meters'
energy counters, in the workbook layout agreed with the plant."""
import io
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from plantcollector import energy_export as ee
from plantcollector.api import create_app
from plantcollector.core import Collector
from plantcollector.store import Reading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "config", "site.demo.yaml")
TZ = ZoneInfo("Asia/Baghdad")
NOW = datetime(2026, 11, 17, 10, 47, 20, tzinfo=TZ)               # a Tuesday morning at the plant
START = datetime(2026, 11, 14, 22, 0, tzinfo=TZ)                  # readings begin two hours before the first day


@pytest.fixture()
def plant(tmp_path):
    c = Collector(DEMO, str(tmp_path / "d"), initial_poll=False, fresh=True)
    try:
        yield c
    finally:
        c.close()


def feed(c, did, kw, counter0=1000.0, every=307, skip=lambda t: False, until=NOW, start=START, reset_at=None):
    """Counter readings of a meter running at a constant `kw`, one every `every` seconds (not on the 5-minute
    marks, as on the plant)."""
    t, e = start, counter0
    while t <= until:
        if reset_at is not None and t >= reset_at:
            e, reset_at = 5.0, None                                # the meter was replaced: its counter starts again
        if not skip(t):
            c.store.ingest(did, {"e_in_kwh": Reading("e_in_kwh", round(e, 3), "kWh", t.astimezone(timezone.utc))})
        t += timedelta(seconds=every)
        e += kw * every / 3600


def rows(data):
    return {a["id"]: a for a in data["analyzers"]}


def test_daily_kwh_is_the_counter_difference_and_today_runs_to_the_latest_reading(plant):
    feed(plant, "analyzer-1", 36.0)                                # 36 kW: 864 kWh a day, 3 kWh per 5 minutes
    feed(plant, "analyzer-2", 12.0, counter0=50.0)
    d = ee.analyzer_energy(plant, NOW)
    assert d["days"] == ["2026-11-15", "2026-11-16", "2026-11-17"] and d["utc_offset"] == "+03:00"
    a1, a2 = rows(d)["analyzer-1"], rows(d)["analyzer-2"]
    assert a1["daily"][0] == pytest.approx(864.0, abs=0.01) and a1["daily"][1] == pytest.approx(864.0, abs=0.01)
    # today: from midnight to the latest reading (10:43:35 with these timings), not to "now"
    last = datetime.fromisoformat(a1["last_reading"])
    hours = (last - datetime(2026, 11, 17, tzinfo=TZ)).total_seconds() / 3600
    assert 10.6 < hours < 10.79 and a1["daily"][2] == pytest.approx(36.0 * hours, abs=0.01)
    assert a1["total"] == pytest.approx(sum(a1["daily"]), abs=0.001)
    assert a2["daily"][0] == pytest.approx(288.0, abs=0.01)
    assert d["all"]["daily"][0] == pytest.approx(864.0 + 288.0, abs=0.02)
    assert d["all"]["total"] == pytest.approx(a1["total"] + a2["total"], abs=0.002)
    # every finished 5-minute interval of the full days is 3 kWh; today's stop at the last full interval
    assert all(x == pytest.approx(3.0, abs=0.002) for x in a1["intervals"][0]) and len(a1["intervals"][0]) == 288
    today = a1["intervals"][2]
    done = [x for x in today if x is not None]
    assert len(done) == a1["through"] == 128 and today[128:] == [None] * 160 and d["through"] == "10:40"
    # 129 intervals have ended by 10:47; the one ending 10:45 waits for the next reading
    assert d["coverage_pct"][:2] == [100.0, 100.0] and d["coverage_pct"][2] == pytest.approx(100 * 128 / 129, abs=0.1) and d["notes"] == []


def test_a_missed_reading_leaves_blank_cells_but_the_day_total_stays_exact(plant):
    gap = (datetime(2026, 11, 16, 13, 0, tzinfo=TZ), datetime(2026, 11, 16, 15, 10, tzinfo=TZ))
    feed(plant, "analyzer-1", 36.0, skip=lambda t: gap[0] <= t < gap[1])
    d = ee.analyzer_energy(plant, NOW)
    a = rows(d)["analyzer-1"]
    assert a["daily"][1] == pytest.approx(864.0, abs=0.01)          # the counter kept counting through the gap
    day = a["intervals"][1]
    blank = [i for i, x in enumerate(day) if x is None]
    assert blank and blank[0] >= 12 * 13 - 1 and blank[-1] <= 12 * 15 + 3 and 24 <= len(blank) <= 28
    assert all(x == pytest.approx(3.0, abs=0.002) for x in day if x is not None)      # no false zero, no spike
    assert sum(x for x in day if x is not None) < a["daily"][1] - 60
    assert any("16 Nov: no reading" in n and "blank" in n for n in d["notes"])
    assert d["coverage_pct"][1] < 100


def test_a_counter_reset_and_a_late_start_are_counted_honestly(plant):
    # analyzer-1: its counter starts again on the 16th at 09:00; analyzer-2: first reading on the 15th at 10:37
    feed(plant, "analyzer-1", 36.0, reset_at=datetime(2026, 11, 16, 9, 0, tzinfo=TZ))
    feed(plant, "analyzer-2", 12.0, start=datetime(2026, 11, 15, 10, 37, tzinfo=TZ))
    d = ee.analyzer_energy(plant, NOW)
    a1, a2 = rows(d)["analyzer-1"], rows(d)["analyzer-2"]
    assert a1["daily"][1] == pytest.approx(864.0, abs=3.1)           # all but the one reading interval of the reset
    assert a1["daily"][1] > 0 and all(x is None or x >= 0 for x in a1["intervals"][1])
    assert any("analyzer 1, 16 nov" in n.lower() and "backwards" in n for n in d["notes"])
    assert a2["daily"][0] == pytest.approx(12.0 * (24 - 10 - 37 / 60), abs=0.02)
    assert a2["intervals"][0][:12 * 10 + 7] == [None] * (12 * 10 + 7) and a2["intervals"][0][-1] == pytest.approx(1.0, abs=0.002)
    assert any("15 Nov: counter readings start at 10:37" in n for n in d["notes"])


def test_an_analyzer_without_readings_is_a_blank_row_not_zeros(plant):
    feed(plant, "analyzer-1", 36.0)
    d = ee.analyzer_energy(plant, NOW)
    a2 = rows(d)["analyzer-2"]
    assert a2["daily"] == [None, None, None] and a2["total"] is None and all(x is None for day in a2["intervals"] for x in day)
    assert d["all"]["daily"][0] == pytest.approx(864.0, abs=0.01)
    assert any(n.startswith("Analyzer 2: no energy-counter reading") for n in d["notes"])


def test_the_workbook_has_the_three_sheets_a_chart_per_analyzer_and_the_same_numbers(plant):
    feed(plant, "analyzer-1", 36.0)
    feed(plant, "analyzer-2", 12.0, counter0=50.0)
    d = ee.analyzer_energy(plant, NOW)
    wb = load_workbook(io.BytesIO(ee.workbook(plant, d)))
    assert wb.sheetnames == ["Daily kWh Summary", "5-Min Charts", "5-Min Data"]
    s, ch, dt = wb["Daily kWh Summary"], wb["5-Min Charts"], wb["5-Min Data"]
    assert s["B2"].value == "Analyzer energy export" and s["B3"].value == "Daily energy consumption (kWh), 15–17 November 2026"
    assert s["C6"].value == 2 and [s[f"{c}9"].value.day for c in "CDE"] == [15, 16, 17] and s["F9"].value == "3-day total (kWh)"
    assert [s["B10"].value, s["B11"].value, s["B12"].value] == ["Analyzer 1", "Analyzer 2", "ALL ANALYZERS"]
    a1 = rows(d)["analyzer-1"]
    assert [s[f"{c}10"].value for c in "CDEF"] == a1["daily"] + [a1["total"]]
    assert [s[f"{c}12"].value for c in "CDEF"] == d["all"]["daily"] + [d["all"]["total"]]
    assert s["C10"].number_format == "#,##0.0" and s.sheet_view.showGridLines is False
    # one line chart per analyzer, three series each, reading that analyzer's block of the data sheet
    assert len(ch._charts) == 2 and all(len(c.series) == 3 for c in ch._charts)
    refs = [c.series[2].val.numRef.f for c in ch._charts]
    assert refs == ["'5-Min Data'!$E$10:$E$297", "'5-Min Data'!$E$304:$E$591"]
    assert ch["B8"].value == "ANALYZER 1" and ch["K8"].value == a1["total"] and ch["B28"].value == "ANALYZER 2"
    assert ch["F5"].value == "17 Nov: partial through 10:40" and ch["B5"].value == "15 Nov: complete"
    # the numbers behind the charts: 288 rows per analyzer, blank after the latest reading
    assert dt["B8"].value == "ANALYZER 1" and [dt[f"{c}9"].value for c in "BCDE"] == ["Time of day", "15 Nov 2026", "16 Nov 2026", "17 Nov 2026"]
    assert dt["B10"].value == "00:00" and dt["B297"].value == "23:55" and dt["B302"].value == "ANALYZER 2"
    assert dt["C10"].value == pytest.approx(3.0, abs=0.002) and dt["E137"].value == pytest.approx(3.0, abs=0.002)
    assert dt["E138"].value is None and dt["E297"].value is None and dt["C304"].value == pytest.approx(1.0, abs=0.002)


def test_the_api_serves_the_numbers_and_the_file(plant):
    feed(plant, "analyzer-1", 36.0, until=datetime.now(timezone.utc), start=datetime.now(timezone.utc) - timedelta(days=3, hours=2))
    api = TestClient(create_app(plant))
    j = api.get("/api/v1/local/analyzers/energy").json()
    assert len(j["days"]) == 3 and j["analyzers"][0]["id"] == "analyzer-1" and j["analyzers"][0]["daily"][1] == pytest.approx(864.0, abs=0.05)
    x = api.get("/api/v1/local/analyzers/energy.xlsx")
    assert x.status_code == 200 and "_analyzer-kwh_" in x.headers["content-disposition"] and x.headers["content-disposition"].endswith('.xlsx"')
    assert load_workbook(io.BytesIO(x.content)).sheetnames[0] == "Daily kWh Summary"
