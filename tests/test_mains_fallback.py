"""Grid import from MAIN 1-3 when the InteliNeo's Mains Import P reads about 0 while the incomers carry power
(seen on the plant after a configuration change on 3 Nov 2026). The master's reading is kept otherwise."""

import os
from datetime import datetime, timedelta, timezone

import pytest

from plantcollector import views
from plantcollector.core import Collector, carry_forward, mains_suspect
from plantcollector.registry import Quality
from plantcollector.store import Reading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "site.demo.yaml")
INC = ("main-01", "main-02", "main-03")


@pytest.fixture
def c(tmp_path):
    c = Collector(CFG, str(tmp_path), initial_poll=False, fresh=True)
    c.device_every_s = lambda did: 600.0             # as on the plant: the incomers at the 10-min site read
    return c


def neo(c, ts, mains, gens=300.0, pv=200.0, grid_on=None):
    c.store.ingest("neo-01", {p: Reading(p, v, "kW", ts) for p, v in
                              (("mains_power_kw", mains), ("power_kw", gens), ("pv_prod_kw", pv), ("load_kw", gens + mains + pv))})
    if grid_on is not None:
        c.store.ingest("neo-01", {"mains_available": Reading("mains_available", grid_on, "", ts),
                                  "alarm_mains_fail": Reading("alarm_mains_fail", not grid_on, "", ts)})


def incomers(c, ts, each=(760.0, 760.0, 757.0), quality=Quality.GOOD):
    for did, v in zip(INC, each):
        c.store.ingest(did, {"power_kw": Reading("power_kw", v, "kW", ts, quality)})


def test_rule_and_carry_forward():
    assert mains_suspect(0, 2277) and mains_suspect(-3.0, 2277) and mains_suspect(0, -656)
    assert not mains_suspect(1500, 2277)            # the master reads something: believed
    assert not mains_suspect(0, 30)                 # the incomers carry nothing to speak of
    assert not mains_suspect(None, 2277) and not mains_suspect(0, None) and not mains_suspect(True, 2277)
    assert carry_forward([(10, 1.0), (20, 2.0)], [5, 10, 15, 25, 31], 10) == [None, (10, 1.0), (10, 1.0), (20, 2.0), None]


def test_summary_takes_the_incomers_when_the_neo_reads_zero(c):
    now = c.now()
    neo(c, now, 0.0)
    incomers(c, now - timedelta(minutes=5))
    k = views.summary(c)["kpi"]
    assert k["mains_kw"] == 2277.0 and k["mains_source"] == "incomers"
    assert (k["mains_incomers"], k["mains_incomers_of"], k["mains_incomers_age_s"]) == (3, 3, 300)
    assert k["load_kw"] == 300 + 200 + 2277.0
    assert k["pv_share_now_pct"] == round(100 * 200 / 2777, 1)
    assert c.live_point()[1] == 2777.0 and c.live_point()[4] == 2277.0


def test_summary_keeps_the_neo_when_it_reads_power(c):
    now = c.now()
    neo(c, now, 1500.0)
    incomers(c, now - timedelta(minutes=5))
    k = views.summary(c)["kpi"]
    assert k["mains_kw"] == 1500.0 and k["mains_source"] == "master" and k["load_kw"] == 2000.0


def test_old_or_bad_incomer_readings_do_not_count(c):
    now = c.now()
    neo(c, now, 0.0)
    incomers(c, now - timedelta(seconds=c.incomer_hold_s("main-01") + 60))
    k = views.summary(c)["kpi"]
    assert k["mains_kw"] == 0.0 and k["mains_source"] == "master" and k["load_kw"] == 500.0
    incomers(c, now, quality=Quality.BAD)
    assert views.summary(c)["kpi"]["mains_source"] == "master"
    # two of three answering: their sum, and the tile says so
    incomers(c, now - timedelta(minutes=1), each=(760.0, 760.0, 0.0))
    c.store.ingest("main-03", {"power_kw": Reading("power_kw", None, "kW", now, Quality.BAD)})
    k = views.summary(c)["kpi"]
    assert k["mains_kw"] == 1520.0 and (k["mains_incomers"], k["mains_incomers_of"]) == (2, 3)


def _day_with_a_broken_reading(c):
    """2 Nov 2026 UTC: the Neo every minute 10:00-14:00, its grid reading right until 12:00 and 0 after;
    the incomers every 10 min, 2277 kW together all the time."""
    t0 = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
    for i in range(241):
        neo(c, t0 + timedelta(minutes=i), 2277.0 if i < 120 else 0.0)
    for i in range(0, 241, 10):
        incomers(c, t0 + timedelta(minutes=i, seconds=20))
    return t0


def test_series_and_daily_totals(c):
    t0 = _day_with_a_broken_reading(c)
    s = views.series(c, t0, t0 + timedelta(hours=4), step=60)["series"]
    assert all(abs(v - 2277) < 1 for v in s["mains_kw"][:-1] if v is not None)
    assert all(abs(v - 2777) < 1 for v in s["load_kw"][:-1] if v is not None)
    # only the load asked for: still corrected
    s2 = views.series(c, t0, t0 + timedelta(hours=4), step=60, keys=["load_kw"])["series"]
    assert s2["load_kw"] == s["load_kw"]
    # hourly buckets: an hour with the master's reading fully zero is corrected
    h = views.series(c, t0, t0 + timedelta(hours=4), step=3600)["series"]
    assert abs(h["mains_kw"][3] - 2277) < 1
    tot = c.daily_totals(t0)["totals"]
    assert abs(tot["grid_kwh"] - 2277 * 4) < 2277 * 0.02
    assert abs(tot["load_kwh"] - 2777 * 4) < 2777 * 0.02
    assert abs(c.mains_correction_kwh(t0, t0 + timedelta(hours=4)) - 2277 * 2) < 2277 * 0.02


def test_a_grid_trip_between_two_incomer_reads(c):
    """The grid goes at 11:05, between the incomers' reads at 11:00:20 and 11:10:20; the Neo reads right and
    sees it go. Its 0 from 11:05 is believed: the incomers' 11:00 reading no longer counts."""
    t0 = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
    for i in range(121):
        neo(c, t0 + timedelta(minutes=i), 2277.0 if i < 65 else 0.0, grid_on=i < 65)
    for i in range(0, 121, 10):
        incomers(c, t0 + timedelta(minutes=i, seconds=20), each=(760.0, 760.0, 757.0) if i < 65 else (0.0, 0.0, 0.0))
    assert c.mains_correction_kwh(t0, t0 + timedelta(hours=2)) == 0.0
    s = views.series(c, t0, t0 + timedelta(hours=2), step=60)["series"]["mains_kw"]
    assert all(v == 0.0 for v in s[66:120])
    tot = c.daily_totals(t0)["totals"]
    assert abs(tot["grid_kwh"] - 2277 * 65 / 60) < 40


def test_a_grid_trip_seen_live(c):
    now = c.now()
    incomers(c, now - timedelta(minutes=6))                         # read with the grid on
    neo(c, now - timedelta(minutes=4), 0.0, grid_on=True)
    neo(c, now - timedelta(minutes=2), 0.0, grid_on=False)          # then the Neo sees the grid go
    neo(c, now, 0.0, gens=1800.0, grid_on=False)
    k = views.summary(c)["kpi"]
    assert k["mains_source"] == "master" and k["mains_kw"] == 0.0 and k["load_kw"] == 2000.0


def test_without_the_neo_state_bits_a_trip_counts_until_the_next_read(c):
    """No mains-available or Mains Fail readings: the incomers' last reading counts until the next one
    (at most one read interval), and not past it."""
    t0 = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
    for i in range(121):
        neo(c, t0 + timedelta(minutes=i), 2277.0 if i < 65 else 0.0)
    for i in range(0, 121, 10):
        incomers(c, t0 + timedelta(minutes=i, seconds=20), each=(760.0, 760.0, 757.0) if i < 65 else (0.0, 0.0, 0.0))
    assert 0 < c.mains_correction_kwh(t0, t0 + timedelta(hours=2)) <= 2277 * 6 / 60


def test_rows_as_the_live_reader_stores_them(c):
    """The live reader stores a value that does not change only every 5 min: the Neo's 0 every 5 min, the
    load (solar moving) every minute, the state bits every 5 min. Every minute of the chart is corrected, and a
    trip at 11:05 is still seen."""
    t0 = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
    for i in range(121):
        ts, on = t0 + timedelta(minutes=i), i < 65
        pv = 200.0 + i
        rows = {"load_kw": Reading("load_kw", 300.0 + pv, "kW", ts), "pv_prod_kw": Reading("pv_prod_kw", pv, "kW", ts)}
        if i % 5 == 0:
            rows["mains_power_kw"] = Reading("mains_power_kw", 0.0, "kW", ts)
        if i % 5 == 0 or i == 65:
            rows["mains_available"] = Reading("mains_available", on, "", ts)
        c.store.ingest("neo-01", rows)
    for i in range(0, 121, 10):
        incomers(c, t0 + timedelta(minutes=i, seconds=20), each=(760.0, 760.0, 757.0) if i < 65 else (0.0, 0.0, 0.0))
    s = views.series(c, t0 + timedelta(minutes=1), t0 + timedelta(hours=2), step=60)["series"]
    load, mains = s["load_kw"], s["mains_kw"]
    for i in range(0, 63):                       # 10:01 .. 11:03: every minute corrected
        assert abs(load[i] - (500.0 + i + 1 + 2277)) < 1 and abs(mains[i] - 2277) < 1, i
    assert all(abs(load[i] - (500.0 + i + 1)) < 1 for i in range(65, 118))   # after the trip: as read
