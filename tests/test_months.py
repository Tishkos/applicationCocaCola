"""One database file per month (1.0.8): finished months move to archive/readings-YYYY-MM.sqlite, and every query for a
time range reads the months it covers, so charts, exports and day reports of earlier months work as before."""

import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from plantcollector.registry import Quality
from plantcollector.store import Reading, TelemetryStore

UTC = timezone.utc


def _put(st, ts, v, dev="neo-01", pid="power_kw"):
    st.ingest(dev, {pid: Reading(pid, v, "kW", ts, Quality.GOOD)})


@pytest.fixture()
def store(tmp_path):
    # a folder with a space, like C:\ProgramData\CCI Plant Monitoring
    st = TelemetryStore(str(tmp_path / "CCI Plant Monitoring" / "data" / "telemetry.sqlite"))
    # readings every 6 h from 2026-09-20 to 2026-11-10, value = day of the year, plus one exactly at a month start
    t = datetime(2026, 9, 20, tzinfo=UTC)
    while t < datetime(2026, 11, 10, tzinfo=UTC):
        _put(st, t, float(t.timetuple().tm_yday))
        t += timedelta(hours=6)
    _put(st, datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC), 1000.0, pid="edge")
    return st


def _all(st):
    return st.history("neo-01", "power_kw", datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))


def test_finished_months_move_to_their_own_files(store):
    before = _all(store)
    n_before = store.count()
    moved = store.archive_months(now=datetime(2026, 11, 15, tzinfo=UTC), pause_s=0)
    assert moved == ["2026-09", "2026-10"]
    files = sorted(os.listdir(store.archive_dir))
    assert files == ["readings-2026-09.sqlite", "readings-2026-10.sqlite"]
    # the main file holds only November now
    left = sqlite3.connect(store._path).execute("SELECT MIN(ts), COUNT(*) FROM readings").fetchone()
    assert left[0].startswith("2026-11")
    # every query reads across the months as before
    assert _all(store) == before
    assert store.count() == n_before
    assert store.first_ts().startswith("2026-09-20")
    edge = store.history("neo-01", "edge", datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))
    assert len(edge) == 1                                    # a reading exactly at a month start counted once
    assert ("neo-01", "edge") in store.pairs_in_range(datetime(2026, 9, 30, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC))
    assert [m["month"] for m in store.months()] == ["2026-09", "2026-10"]
    # nothing more to move in the same month; the event log says what was moved
    assert store.archive_months(now=datetime(2026, 11, 20, tzinfo=UTC), pause_s=0) == []
    assert len(store.events(datetime(2000, 1, 1, tzinfo=UTC))) == 2


def test_averages_across_a_month_boundary(store):
    since, until = datetime(2026, 9, 25, tzinfo=UTC), datetime(2026, 10, 5, tzinfo=UTC)
    a = store.resample(since, until, 86400, [("neo-01", "power_kw")])
    store.archive_months(now=datetime(2026, 11, 15, tzinfo=UTC), pause_s=0)
    b = store.resample(since, until, 86400, [("neo-01", "power_kw")])
    assert a == b and len(b[("neo-01", "power_kw")]) == 11
    # a bucket that spans both months (4-day buckets counted from 1 Jan 1970: 28 Sep - 2 Oct) is the mean of all its readings
    lo, hi = datetime(2026, 9, 24, tzinfo=UTC), datetime(2026, 10, 8, tzinfo=UTC)
    wk = store.resample(lo, hi, 4 * 86400, [("neo-01", "power_kw")])[("neo-01", "power_kw")]
    want: dict = {}
    for t, v in store.history("neo-01", "power_kw", lo, hi):
        bk = int(datetime.fromisoformat(t).timestamp()) // (4 * 86400) * (4 * 86400)
        want.setdefault(bk, []).append(v)
    assert any(datetime.fromtimestamp(bk, UTC).month == 9 and datetime.fromtimestamp(bk + 4 * 86400 - 1, UTC).month == 10 for bk in want)
    assert wk == pytest.approx({bk: sum(vs) / len(vs) for bk, vs in want.items()})


def test_a_move_that_stopped_half_way_is_finished_and_never_counted_twice(store):
    store.archive_months(now=datetime(2026, 10, 15, tzinfo=UTC), pause_s=0)          # September moved
    n = store.count()
    # as if the copy of October had been made but the delete had not run yet
    store._copy_month("2026-10")
    assert len(_all(store)) == len({t for t, _v in _all(store)})                      # no duplicates in a query
    assert store.archive_months(now=datetime(2026, 11, 15, tzinfo=UTC), pause_s=0) == []   # October was already copied
    assert store.count() == n
    left = sqlite3.connect(store._path).execute("SELECT COUNT(*) FROM readings WHERE ts < '2026-11'").fetchone()[0]
    assert left == 0


def test_writing_goes_on_while_a_month_moves(store):
    stop = threading.Event()
    wrote = []

    def writer():
        t = datetime(2026, 11, 12, tzinfo=UTC)
        while not stop.is_set():
            _put(store, t, 1.0, pid="live")
            wrote.append(t)
            t += timedelta(seconds=1)
    th = threading.Thread(target=writer)
    th.start()
    store.archive_months(now=datetime(2026, 11, 15, tzinfo=UTC), chunk=50, pause_s=0.001)
    stop.set()
    th.join()
    assert len(store.history("neo-01", "live", datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))) == len(wrote)


def test_a_new_store_finds_the_month_files(store, tmp_path):
    store.archive_months(now=datetime(2026, 11, 15, tzinfo=UTC), pause_s=0)
    again = TelemetryStore(store._path)
    assert sorted(again._months) == ["2026-09", "2026-10"]
    assert _all(again) == _all(store)


def test_memory_and_demo_stores_never_move_anything(tmp_path):
    st = TelemetryStore(":memory:")
    _put(st, datetime(2026, 9, 1, tzinfo=UTC), 1.0)
    assert st.archive_months(now=datetime(2026, 11, 15, tzinfo=UTC)) == []
    assert len(st.history("neo-01", "power_kw", datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))) == 1
