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
    _put(st, datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC), 1000.0, pid="edge")
    t = datetime(2026, 9, 20, tzinfo=UTC)
    while t < datetime(2026, 11, 10, tzinfo=UTC):
        _put(st, t, float(t.timetuple().tm_yday))
        t += timedelta(hours=6)
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
    assert _all(store) == before and store.count() > n_before                # copied; the copies wait one run
    assert store.archive_months(now=datetime(2026, 11, 15, 1, tzinfo=UTC), pause_s=0) == []
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
    assert store.count() == n_before
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
    store.archive_months(now=datetime(2026, 10, 15, 1, tzinfo=UTC), pause_s=0)       # and its copies deleted
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


# ---------------------------------------------------------------- 1.0.9: a month file stays exact

def _main_rows(st, where="1=1", args=()):
    return sqlite3.connect(st._path).execute(f"SELECT COUNT(*) FROM readings WHERE {where}", args).fetchone()[0]


def test_readings_written_late_for_an_archived_month_are_shown_and_never_lost(store):
    # the PC clock ran a month ahead for one run: November was moved while it was still going on
    store.archive_months(now=datetime(2026, 12, 5, tzinfo=UTC), pause_s=0)
    assert "2026-11" in store._months
    late = [datetime(2026, 11, 20, tzinfo=UTC) + timedelta(minutes=i) for i in range(76)]
    for t in late:                                               # the clock is right again: more November readings
        _put(store, t, 5.0, pid="late")
    span = (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))
    assert len(store.history("neo-01", "late", *span)) == 76     # shown at once, from the main file
    store.archive_months(now=datetime(2026, 11, 21, tzinfo=UTC), pause_s=0)     # November is not over: left as it is
    assert len(store.history("neo-01", "late", *span)) == 76
    store.archive_months(now=datetime(2026, 12, 2, tzinfo=UTC), pause_s=0)      # over now: into November's file
    assert len(store.history("neo-01", "late", *span)) == 76
    store.archive_months(now=datetime(2026, 12, 2, 1, tzinfo=UTC), pause_s=0)   # and out of the main file
    got = store.history("neo-01", "late", *span)
    assert [t for t, _v in got] == [t.isoformat() for t in late]
    assert _main_rows(store, "point_id='late'") <= 1             # at most the newest row waits for the next run
    arch = sqlite3.connect(store._months["2026-11"]).execute("SELECT COUNT(*) FROM readings WHERE point_id='late'").fetchone()[0]
    assert arch == 76
    # a new store reads the record of the file and finds everything once
    again = TelemetryStore(store._path)
    assert [t for t, _v in again.history("neo-01", "late", *span)] == [t.isoformat() for t in late]
    assert _all(again) == _all(store)


def test_the_newest_row_is_kept_until_a_newer_one_so_no_row_number_comes_twice(tmp_path):
    st = TelemetryStore(str(tmp_path / "data" / "telemetry.sqlite"))
    for d in range(1, 31):
        _put(st, datetime(2026, 10, d, tzinfo=UTC), float(d))
    st.archive_months(now=datetime(2026, 11, 1, 1, tzinfo=UTC), pause_s=0)
    assert _main_rows(st) == 30                                 # copied; the copies go at the next run
    st.archive_months(now=datetime(2026, 11, 1, 2, tzinfo=UTC), pause_s=0)
    assert _main_rows(st) == 1                                  # the newest row stays for now ...
    oct_ = st.history("neo-01", "power_kw", datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))
    assert len(oct_) == 30                                      # ... but is never counted twice
    upto = st._meta["2026-10"]["upto"]
    _put(st, datetime(2026, 11, 1, 2, tzinfo=UTC), 1.0)
    assert sqlite3.connect(st._path).execute("SELECT MAX(rowid) FROM readings").fetchone()[0] > upto
    st.archive_months(now=datetime(2026, 11, 1, 3, tzinfo=UTC), pause_s=0)
    st.archive_months(now=datetime(2026, 11, 1, 4, tzinfo=UTC), pause_s=0)
    assert _main_rows(st) == 1 and _main_rows(st, "ts>='2026-11'") == 1
    assert len(st.history("neo-01", "power_kw", datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))) == 30


def test_a_file_made_by_1_0_8_is_checked_once_against_the_main_file(store):
    store.archive_months(now=datetime(2026, 11, 15, tzinfo=UTC), pause_s=0)
    store.archive_months(now=datetime(2026, 11, 15, 1, tzinfo=UTC), pause_s=0)
    want = _all(store)
    path = store._months["2026-10"]
    con = sqlite3.connect(path)
    con.execute("DROP TABLE meta")                              # as 1.0.8 made it
    copies = con.execute("SELECT * FROM readings ORDER BY ts LIMIT 5").fetchall()
    con.commit()
    con.close()
    # what 1.0.8 could leave: rows a delete that stopped half way left behind, and rows written after its copy
    main = sqlite3.connect(store._path)
    main.executemany("INSERT INTO readings VALUES (?,?,?,?,?,?,?)", copies)
    main.commit()
    main.close()
    old = TelemetryStore(store._path)
    assert old._meta["2026-10"]["upto"] is None
    assert _all(old) == want                                    # like 1.0.8: October from its file only
    _put(old, datetime(2026, 10, 20, 12, 30, tzinfo=UTC), 7.0, pid="late")
    _put(old, datetime(2026, 11, 12, tzinfo=UTC), 1.0)          # the newest row is November's
    old.archive_months(now=datetime(2026, 11, 15, tzinfo=UTC), pause_s=0)
    old.archive_months(now=datetime(2026, 11, 15, 1, tzinfo=UTC), pause_s=0)
    assert old._meta["2026-10"]["upto"] is not None and old._meta["2026-10"]["rows"] > 0
    after = old.history("neo-01", "power_kw", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 11, 11, tzinfo=UTC))
    assert after == want                                        # the 5 left-behind rows are not counted twice
    assert len(old.history("neo-01", "late", datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))) == 1
    assert _main_rows(old, "ts<'2026-11'") == 0


def test_counting_while_months_move_never_fails(store):
    errs, stop = [], threading.Event()

    def api():
        while not stop.is_set():
            try:
                store.count(); store.first_ts(); store.months(); _all(store)
            except Exception as exc:                            # noqa: BLE001
                errs.append(repr(exc))
    th = threading.Thread(target=api)
    th.start()
    try:
        store.archive_months(now=datetime(2026, 11, 15, tzinfo=UTC), chunk=10, pause_s=0.001)
    finally:
        stop.set()
        th.join()
    assert errs == []
    assert store.months()[0]["rows"] > 0 and store.first_ts().startswith("2026-09-20")


def test_ranges_in_another_time_zone_mean_the_same_instants(store):
    plant = timezone(timedelta(hours=3))
    store.archive_months(now=datetime(2026, 11, 15, tzinfo=UTC), pause_s=0)
    a = store.history("neo-01", "power_kw", datetime(2026, 10, 31, 12, tzinfo=UTC), datetime(2026, 11, 1, 12, tzinfo=UTC))
    b = store.history("neo-01", "power_kw", datetime(2026, 10, 31, 15, tzinfo=plant), datetime(2026, 11, 1, 15, tzinfo=plant))
    assert a == b and len(a) == 5


def test_a_copy_that_fails_leaves_no_half_made_file(store, monkeypatch):
    def boom(*_a):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.archive_months(now=datetime(2026, 11, 15, tzinfo=UTC), pause_s=0)
    monkeypatch.undo()
    assert not [f for f in os.listdir(store.archive_dir) if f.endswith(".tmp")]
    assert store._months == {} and _main_rows(store, "ts<'2026-10'") > 0       # nothing deleted
    assert store.archive_months(now=datetime(2026, 11, 15, tzinfo=UTC), pause_s=0) == ["2026-09", "2026-10"]


def test_a_query_while_late_rows_move_counts_each_once(store, monkeypatch):
    """A query reads a month's file and its "upto" in one go, then the main file's rows above that upto: the rows
    the mover appends meanwhile are neither counted twice nor missed (their copies go one run later)."""
    store.archive_months(now=datetime(2026, 12, 5, tzinfo=UTC), pause_s=0)      # November moved early (clock ahead)
    for i in range(10):
        _put(store, datetime(2026, 11, 20, tzinfo=UTC) + timedelta(minutes=i), 5.0, pid="late")
    span = (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))
    real = store._read_month
    for run in (datetime(2026, 12, 6, tzinfo=UTC), datetime(2026, 12, 6, 1, tzinfo=UTC), datetime(2026, 12, 6, 2, tzinfo=UTC)):
        def racing(sql, args, path, run=run):
            out = real(sql, args, path)
            if not store._roll_lock.locked():
                store.archive_months(now=run, pause_s=0)    # appends and deletes between the two halves
            return out
        monkeypatch.setattr(store, "_read_month", racing)
        assert len(store.history("neo-01", "late", *span)) == 10, run
    monkeypatch.undo()
    assert len(store.history("neo-01", "late", *span)) == 10
    assert _main_rows(store, "point_id='late'") <= 1


def test_a_month_file_left_half_written_is_repaired_at_the_start(store):
    store.archive_months(now=datetime(2026, 11, 15, tzinfo=UTC), pause_s=0)
    path = store._months["2026-10"]
    want = _all(store)
    # a write into the month file that never finished: its journal is still there ("hot")
    con = sqlite3.connect(path)
    con.execute("PRAGMA cache_size=1")
    con.execute("BEGIN IMMEDIATE")
    con.executemany("INSERT INTO readings VALUES (?,?,?,?,?,?,?)", [("2026-10-05T00:00:00+00:00", "x", "y", 1.0, None, "good", None)] * 5000)
    import shutil
    shutil.copy(path, path + ".crash")
    shutil.copy(path + "-journal", path + ".crash-journal")
    con.rollback()
    con.close()
    shutil.move(path + ".crash", path)
    shutil.move(path + ".crash-journal", path + "-journal")
    again = TelemetryStore(store._path)
    assert again._meta["2026-10"]["upto"] is not None             # read after the roll-back, not taken for a 1.0.8 file
    assert _all(again) == want and again.count() > 0
