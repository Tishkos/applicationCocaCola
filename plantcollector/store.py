"""SQLite telemetry store: every reading with timestamp + quality, plus an event log.

One file per month: the readings of the current month (UTC) are in the main file (telemetry.sqlite); once a month is
over, its readings move to a file of their own, archive/readings-YYYY-MM.sqlite (archive_months, run in the
background by the service: the copy reads the main file beside the writer, and the delete goes a few thousand rows
at a time, each step a short look-up by row number, so the live reading is never held up). Every query for a time
range reads the months it covers - an archived month from its own file, the others from the main file - so the
charts, exports and day reports of an earlier month find it by themselves, and a look at last month opens only last
month's file. The event log stays in the main file (it is small). The main file keeps its largest size and reuses
the free space for the new month (it is never compacted: that would renumber the rows, see below).

How a month file stays exact (1.0.9):
  - each month file records the highest row number of the main file it holds (meta "upto"), written in the same
    transaction as its rows. Rows of that month in the main file up to that number are copies waiting to be deleted;
    rows above it came later (the last seconds of the month, or a PC clock that was wrong for a while).
  - a query for an archived month reads its file and that "upto" in one read transaction, then the main file's
    rows of that month above it, so nothing is counted twice while a move is half way, and nothing written late is
    hidden;
  - the next run appends those later rows to the month file; the copies in the main file are deleted one run
    later (an hour), up to the "upto" the file had when that run began, so a query that was under way never misses
    a row. A row leaves the main file only once it is in a month file. The newest row of the main file is never
    deleted, so a row number is never given out twice;
  - a month file made by 1.0.8 (no "upto") is checked once against the main file, row by row, and gets its "upto";
    a month file whose write stopped half way (power loss) is rolled back when the program starts.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .registry import Quality


@dataclass
class Reading:
    point_id: str
    value: Any
    unit: str
    source_ts: datetime
    quality: Quality = Quality.GOOD
    error: str | None = None


def _next_month(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y + 1:04d}-01" if mo == 12 else f"{y:04d}-{mo + 1:02d}"


def _uri(path: str, ro: bool = True) -> str:
    """An SQLite URI for a file (spaces and drive letters on Windows included)."""
    return "file:" + urllib.request.pathname2url(os.path.abspath(path)) + ("?mode=ro" if ro else "")


_SCHEMA = """CREATE TABLE IF NOT EXISTS readings (
            ts TEXT NOT NULL, device_id TEXT NOT NULL, point_id TEXT NOT NULL,
            value REAL, text_value TEXT, quality TEXT NOT NULL, error TEXT)"""
_META = "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)"
_MONTH = re.compile(r"^\d{4}-\d{2}$")


def _utc_text(t: datetime) -> str:
    """A range bound as the readings store their times: UTC ISO text (text order is then time order)."""
    if t.tzinfo is not None and t.utcoffset():
        t = t.astimezone(timezone.utc)
    return t.isoformat()


class TelemetryStore:
    ARCHIVE = re.compile(r"^readings-(\d{4}-\d{2})\.sqlite$")

    def __init__(self, db_path: str, archive: bool = True):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")      # WAL + NORMAL: safe on power loss, far fewer fsyncs
        self._lock = threading.Lock()
        self._bulk = 0
        self._path = db_path
        self._local = threading.local()     # one read connection per thread: WAL readers never block or trample each other
        self._db.execute(_SCHEMA)
        self._db.execute("CREATE INDEX IF NOT EXISTS ix_readings ON readings(device_id, point_id, ts)")
        self._db.execute("""CREATE TABLE IF NOT EXISTS events (
            ts TEXT NOT NULL, kind TEXT NOT NULL, device_id TEXT, severity TEXT, code TEXT, message TEXT)""")
        self._db.commit()
        self._latest: dict[str, dict[str, Reading]] = {}
        # the months already moved to their own file: "2026-10" -> path
        self.archive_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "archive") if archive and db_path != ":memory:" else None
        self._months: dict[str, str] = {}
        # month -> {"upto": highest main-file rowid it holds (None: a 1.0.8 file not checked yet), "rows", "first"}
        self._meta: dict[str, dict] = {}
        self._clean: set[str] = set()        # archived months the main file holds no later rows of (no 2nd query)
        self._late_n = 0                     # rows written for an archived month (a PC clock that was wrong)
        self._info_lock = threading.Lock()
        self._roll_lock = threading.Lock()
        if self.archive_dir and os.path.isdir(self.archive_dir):
            for f in sorted(os.listdir(self.archive_dir)):
                m = self.ARCHIVE.match(f)
                if m:
                    path = os.path.join(self.archive_dir, f)
                    self._months[m.group(1)] = path
                    self._meta[m.group(1)] = self._read_meta(path)

    def ingest(self, device_id: str, readings: dict[str, Reading], persist: dict[str, Reading] | None = None) -> None:
        """Make `readings` the latest values and write them to the database. With `persist`, only those
        readings are written (the live reader keeps every value in memory but stores a few)."""
        with self._lock:
            self._latest.setdefault(device_id, {}).update(readings)
            rows = []
            for r in (readings if persist is None else persist).values():
                num = r.value if isinstance(r.value, (int, float)) and not isinstance(r.value, bool) else (
                    1.0 if r.value is True else 0.0 if r.value is False else None)
                txt = None if num is not None or r.value is None else str(r.value)
                ts = _utc_text(r.source_ts)
                if self._months and ts[:7] in self._months:        # a month already in its own file: read it from both
                    self._clean.discard(ts[:7])
                    self._late_n += 1
                rows.append((ts, device_id, r.point_id, num, txt, r.quality.value, r.error))
            if not rows:
                return
            self._db.executemany("INSERT INTO readings VALUES (?,?,?,?,?,?,?)", rows)
            if not self._bulk:
                self._db.commit()

    def event(self, kind: str, message: str, *, device_id: str | None = None, severity: str = "info",
              code: str | None = None, ts: datetime | None = None) -> None:
        ts = ts or datetime.now(timezone.utc)
        with self._lock:
            self._db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (ts.isoformat(), kind, device_id, severity, code, message))
            if not self._bulk:
                self._db.commit()

    class _Bulk:
        def __init__(self, store): self.store = store
        def __enter__(self):
            with self.store._lock:
                self.store._bulk += 1
        def __exit__(self, *exc):
            with self.store._lock:
                self.store._bulk -= 1
                if not self.store._bulk:
                    self.store._db.commit()

    def bulk(self):
        """with store.bulk(): ...  -> one commit at the end instead of one per device per poll."""
        return TelemetryStore._Bulk(self)

    def _read(self, sql: str, args=(), path: str | None = None) -> list:
        """Run a read-only query, on the main file or (path) on a month's own file. API requests run in parallel
        threads, so they must not share the writer's connection: one read connection per thread and file."""
        if path is None and self._path == ":memory:":
            with self._lock:
                return self._db.execute(sql, args).fetchall()
        if path is None:
            con = getattr(self._local, "con", None)
            if con is None:
                con = self._local.con = sqlite3.connect(self._path, check_same_thread=False)
            return con.execute(sql, args).fetchall()
        return self._month_con(path).execute(sql, args).fetchall()

    def _month_con(self, path: str) -> sqlite3.Connection:
        cons = getattr(self._local, "arch", None)
        if cons is None:
            cons = self._local.arch = {}
        con = cons.get(path)
        if con is None:          # waits (up to 30 s) while the mover appends to this month's file
            con = cons[path] = sqlite3.connect(_uri(path), uri=True, check_same_thread=False, timeout=30)
        return con

    def _read_month(self, sql: str, args, path: str) -> tuple[list, int | None]:
        """A query on a month's file and, in the same read transaction, the file's "upto": the rows of that month
        the main file got later (rowid > upto) are then exactly those the file did not have at that moment."""
        con = self._month_con(path)
        con.execute("BEGIN")
        try:
            try:
                got = con.execute("SELECT v FROM meta WHERE k='upto'").fetchone()
                upto = int(got[0]) if got and got[0] not in (None, "") else None
            except sqlite3.OperationalError:              # a 1.0.8 file, not checked yet: the file alone, as then
                upto = None
            return con.execute(sql, args).fetchall(), upto
        finally:
            con.commit()

    def _segments(self, since: datetime, until: datetime) -> list[tuple[str | None, str, str]]:
        """[(file, from, to)] covering [since, until]: each archived month its own file, the other months the main
        file (None), as ISO-text bounds (the readings' times are UTC ISO text, so text order is time order)."""
        s, u = _utc_text(since), _utc_text(until)
        arch = dict(self._months)
        if not arch or s > u or not _MONTH.match(s[:7]) or not _MONTH.match(u[:7]):
            return [(None, s, u)]
        out: list[tuple[str | None, str, str]] = []
        run = None
        m = s[:7]
        while m <= u[:7]:
            lo, hi = max(s, m), min(u, m + "-99")            # "2026-10" < every October time < "2026-10-99"
            if m in arch:
                if run:
                    out.append((None, run[0], run[1]))
                    run = None
                out.append((arch[m], lo, hi))
            else:
                run = (run[0], hi) if run else (lo, hi)
            m = _next_month(m)
        if run:
            out.append((None, run[0], run[1]))
        return out

    def _query(self, since: datetime, until: datetime, build) -> tuple[list, bool]:
        """Every row of a query over [since, until]: build(lo, hi, after) -> (sql, args), where after is None or a
        row number (only main-file rows above it). An archived month is its file plus the main file's later rows
        of that month. Returns (rows, whether some month was read from two files)."""
        out: list = []
        two = False
        for path, lo, hi in self._segments(since, until):
            if path is None:
                out += self._read(*build(lo, hi, None))
                continue
            clean = lo[:7] in self._clean                  # before the file is read: the mover may mark it meanwhile
            rows, upto = self._read_month(*build(lo, hi, None), path)
            out += rows
            if upto is not None and not clean:
                late = self._read(*build(lo, hi, upto))
                if late:
                    out += late
                    two = True
        return out, two

    def latest(self, device_id: str) -> dict[str, Reading]:
        with self._lock:                    # the live reader updates these while API threads read them
            return dict(self._latest.get(device_id, {}))

    def value(self, device_id: str, point_id: str):
        r = self._latest.get(device_id, {}).get(point_id)
        if r is None or r.quality != Quality.GOOD:
            return None
        return r.value

    def history(self, device_id: str, point_id: str, since: datetime, until: datetime) -> list[tuple[str, float | None]]:
        def build(lo, hi, after):
            w = "" if after is None else " AND rowid>?"
            return (f"SELECT ts, value FROM readings WHERE device_id=? AND point_id=? AND quality='good' AND ts>=? AND ts<=?{w} ORDER BY ts",
                    [device_id, point_id, lo, hi] + ([] if after is None else [after]))
        out, two = self._query(since, until, build)
        if two:                                                # later rows of a month read from two files
            out.sort(key=lambda r: r[0])
        return out

    def events(self, since: datetime, limit: int = 300) -> list[dict]:
        cols = ["ts", "kind", "device_id", "severity", "code", "message"]
        rows = self._read("SELECT ts, kind, device_id, severity, code, message FROM events WHERE ts>=? ORDER BY ts DESC LIMIT ?",
                          (since.isoformat(), limit))
        return [dict(zip(cols, r)) for r in rows]

    def integrate_kwh(self, device_id: str, point_id: str, since: datetime, until: datetime) -> float:
        """Trapezoid integral of a kW series -> kWh."""
        rows = self.history(device_id, point_id, since, until)
        kwh = 0.0
        for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
            if v0 is None or v1 is None:
                continue
            kwh += (v0 + v1) / 2 * (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
        return round(kwh, 1)

    def resample(self, since: datetime, until: datetime, step_s: int,
                 pairs: list[tuple[str, str]] | None = None) -> dict[tuple[str, str], dict[int, float]]:
        """
        Average every good numeric reading into `step_s`-second buckets, in SQL.
        Returns {(device_id, point_id): {bucket_epoch: mean}}. Booleans come back as 0..1.
        """
        step_s = max(1, int(step_s))
        sql = ("SELECT device_id, point_id, (CAST(strftime('%s', ts) AS INTEGER) / ?) * ? AS b, SUM(value), COUNT(value) "
               "FROM readings WHERE quality='good' AND value IS NOT NULL AND ts>=? AND ts<=?")
        extra: list = []
        if pairs:
            sql += " AND (" + " OR ".join("(device_id=? AND point_id=?)" for _ in pairs) + ")"
            for d, p in pairs:
                extra += [d, p]
        acc: dict[tuple[str, str], dict[int, list]] = {}

        def build(lo, hi, after):
            w = "" if after is None else " AND rowid>?"
            return sql + w + " GROUP BY device_id, point_id, b", [step_s, step_s, lo, hi] + extra + ([] if after is None else [after])
        for d, p, b, total, n in self._query(since, until, build)[0]:   # a bucket across two months adds up both
            cell = acc.setdefault((d, p), {}).setdefault(int(b), [0.0, 0])
            cell[0] += total or 0.0
            cell[1] += n or 0
        return {k: {b: t / n for b, (t, n) in sorted(v.items()) if n} for k, v in acc.items()}

    def pairs_in_range(self, since: datetime, until: datetime) -> list[tuple[str, str]]:
        def build(lo, hi, after):
            w = "" if after is None else " AND rowid>?"
            return f"SELECT DISTINCT device_id, point_id FROM readings WHERE ts>=? AND ts<=?{w}", [lo, hi] + ([] if after is None else [after])
        return sorted({(d, p) for d, p in self._query(since, until, build)[0]})

    # ------------------------------------------------------------------ what the month files hold
    @staticmethod
    def _read_meta(path: str) -> dict:
        """A month file's own record: {"upto", "rows", "first"} (a 1.0.8 file has none: upto None). Opened for
        writing once: a write that stopped half way (the PC lost power) is rolled back here, before any read-only
        connection would trip over it. A file that cannot be read is left alone (bad: never moved into or out of)."""
        out = {"upto": None, "rows": None, "first": None}
        try:
            con = sqlite3.connect(_uri(path, ro=False), uri=True, timeout=30)
            try:
                try:
                    kv = dict(con.execute("SELECT k, v FROM meta").fetchall())
                except sqlite3.OperationalError as exc:
                    if "no such table" not in str(exc):
                        raise
                    con.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
                    return out                             # a 1.0.8 file
            finally:
                con.close()
            for k in ("upto", "rows"):
                if kv.get(k) not in (None, ""):
                    out[k] = int(kv[k])
            out["first"] = kv.get("first") or None
        except (sqlite3.Error, ValueError) as exc:
            print(f"[store] {os.path.basename(path)} cannot be read ({exc}); it is left as it is")
            out["bad"] = True
        return out

    def _info(self, m: str) -> tuple[int, str | None]:
        """(rows, first ts) of a month file: from its record, else counted once (a 1.0.8 file)."""
        meta = self._meta.get(m) or {}
        if meta.get("rows") is not None:
            return meta["rows"], meta.get("first")
        with self._info_lock:                     # one count at a time, never the same file twice
            meta = self._meta.get(m) or {}
            if meta.get("rows") is None and m in self._months:
                n, first = self._read("SELECT COUNT(*), MIN(ts) FROM readings", (), self._months[m])[0]
                self._meta[m] = meta = {**meta, "rows": int(n or 0), "first": first}
            return meta.get("rows") or 0, meta.get("first")

    def months(self) -> list[dict]:
        """The months in their own files: [{"month", "file", "rows"}], oldest first."""
        return [{"month": m, "file": os.path.basename(p), "rows": self._info(m)[0]} for m, p in sorted(dict(self._months).items())]

    def count(self) -> int:
        """All readings: the main file's and every month file's (during a move, a month's rows briefly count twice)."""
        return self._read("SELECT COUNT(*) FROM readings")[0][0] + sum(self._info(m)[0] for m in list(self._months))

    def first_ts(self) -> str | None:
        firsts = [self._info(m)[1] for m in sorted(self._months)[:1]]       # the oldest month file holds the first
        rows = self._read("SELECT MIN(ts) FROM readings")
        firsts.append(rows[0][0] if rows else None)
        firsts = [f for f in firsts if f]
        return min(firsts) if firsts else None

    @staticmethod
    def month_now(now: datetime | None = None) -> str:
        return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m")

    # ------------------------------------------------------------------ months into their own files
    def archive_months(self, now: datetime | None = None, chunk: int = 5000, pause_s: float = 0.02) -> list[str]:
        """Move every finished month (UTC) from the main file into archive/readings-YYYY-MM.sqlite. Returns the months
        moved. Safe at any time and from any thread (one run at a time); the live reading goes on meanwhile.

        The main file's copies are deleted one run later (an hour): up to the "upto" a month's file had when this run
        began. A query that started before a file got new rows never misses them that way."""
        if not self.archive_dir or not self._roll_lock.acquire(blocking=False):
            return []
        try:
            cur = self.month_now(now)
            moved: list[str] = []
            with self._lock:                               # rows written but not committed yet are not in a scan
                late_n, open_tx = self._late_n, self._db.in_transaction
            began = {m: (self._meta.get(m) or {}).get("upto") for m in self._months}
            for m in sorted(self._months):                 # a 1.0.8 file: checked once against the main file
                if self._meta.get(m, {}).get("upto") is None and not self._meta.get(m, {}).get("bad"):
                    self._add_late(m, by_key=True)
            seen: set[str] = set()
            after = ""
            while True:                                    # the oldest month the main file holds, then the next
                first = self._read("SELECT MIN(ts) FROM readings WHERE ts>?", (after,))[0][0]
                if not first or not _MONTH.match(first[:7]) or first[:7] >= cur:
                    break
                m = first[:7]
                seen.add(m)
                if m not in self._months:
                    self._copy_month(m)                    # its copies in the main file go at the next run
                    moved.append(m)
                elif not self._meta.get(m, {}).get("bad"):
                    self._add_late(m)                      # rows written after the copy: into the file
                    self._delete_month(m, began.get(m), chunk, pause_s)
                after = m + "-99"
            with self._lock:                               # which months need no second query any more
                for m in list(self._months):
                    upto = self._meta.get(m, {}).get("upto")
                    if upto is None or m >= cur:
                        continue
                    if m in seen:
                        later = self._db.execute("SELECT 1 FROM readings WHERE rowid>? AND ts>=? AND ts<=? LIMIT 1",
                                                 (upto, m, m + "-99")).fetchone()
                        if not later:
                            self._clean.add(m)
                    elif self._late_n == late_n and not open_tx:   # not in the main file at all, nothing came since
                        self._clean.add(m)
            return moved
        finally:
            self._roll_lock.release()

    def _snapshot(self) -> int:
        """The main file's highest row number now (every row up to it is written; later rows get higher numbers)."""
        return int(self._read("SELECT MAX(rowid) FROM readings")[0][0] or 0)

    def _copy_month(self, m: str) -> None:
        """A month into a new file of its own (made as .tmp, then renamed, so a file is always complete)."""
        os.makedirs(self.archive_dir, exist_ok=True)
        path = os.path.join(self.archive_dir, f"readings-{m}.sqlite")
        if os.path.exists(path):                           # never over a file that is there (one that cannot be read)
            raise RuntimeError(f"{os.path.basename(path)} is there already")
        tmp = path + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        con = sqlite3.connect(_uri(tmp, ro=False), uri=True, isolation_level=None)
        try:
            snap = self._snapshot()
            con.execute(_SCHEMA)
            con.execute(_META)
            con.execute("ATTACH DATABASE ? AS src", (_uri(self._path),))          # read beside the writer (WAL)
            con.execute("BEGIN")
            got = con.execute("INSERT INTO readings SELECT * FROM src.readings WHERE rowid<=? AND ts>=? AND ts<=? ORDER BY rowid",
                              (snap, m, m + "-99")).rowcount
            want = con.execute("SELECT COUNT(*) FROM src.readings WHERE rowid<=? AND ts>=? AND ts<=?", (snap, m, m + "-99")).fetchone()[0]
            if got != want:
                raise RuntimeError(f"month {m}: {got} rows copied of {want}")
            first = con.execute("SELECT MIN(ts) FROM readings").fetchone()[0]
            con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [("upto", str(snap)), ("rows", str(got)), ("first", first or "")])
            con.execute("COMMIT")
            con.execute("DETACH DATABASE src")
            con.execute("CREATE INDEX IF NOT EXISTS ix_readings ON readings(device_id, point_id, ts)")
            con.close()
            os.replace(tmp, path)
        except BaseException:
            con.close()
            try:
                os.remove(tmp)                             # never leave a big half-made file behind
            except OSError:
                pass
            raise
        self._meta[m] = {"upto": snap, "rows": got, "first": first}
        self._months[m] = path
        self.event("database", f"readings of {m} moved to archive\\{os.path.basename(path)} ({got} rows)", severity="info",
                   code="DB_MONTH")

    _SAME = ("a.device_id=r.device_id AND a.point_id=r.point_id AND a.ts=r.ts AND a.value IS r.value "
             "AND a.text_value IS r.text_value AND a.quality IS r.quality AND a.error IS r.error")

    def _add_late(self, m: str, by_key: bool = False, batch: int = 50000) -> int:
        """Rows of an archived month that the main file got after the copy (rowid > upto): appended to its file, a
        range of row numbers at a time, each batch with its new "upto" in the same transaction (readers of that
        file wait a moment at most). by_key (a 1.0.8 file, which has no "upto"): every row of that month in the
        main file that the file does not hold yet, compared column by column."""
        path = self._months[m]
        meta = dict(self._meta.get(m) or {})
        snap = self._snapshot()
        lo = 0 if by_key else int(meta.get("upto") or 0)
        if not by_key and snap <= lo:
            return 0
        rows, first = meta.get("rows"), meta.get("first")
        n_all = 0
        con = sqlite3.connect(_uri(path, ro=False), uri=True, isolation_level=None, timeout=30)
        try:
            con.execute(_META)
            con.execute("ATTACH DATABASE ? AS src", (_uri(self._path),))
            while True:
                hi = snap if by_key else min(snap, lo + batch)        # a 1.0.8 file: one pass (usually nothing)
                con.execute("BEGIN IMMEDIATE")
                try:
                    if by_key:
                        n = con.execute(f"""INSERT INTO main.readings SELECT * FROM src.readings r WHERE r.rowid<=? AND r.ts>=? AND r.ts<=?
                            AND NOT EXISTS (SELECT 1 FROM main.readings a WHERE {self._SAME}) ORDER BY r.rowid""", (hi, m, m + "-99")).rowcount
                    else:
                        n = con.execute("INSERT INTO main.readings SELECT * FROM src.readings WHERE rowid>? AND rowid<=? AND ts>=? AND ts<=? ORDER BY rowid",
                                        (lo, hi, m, m + "-99")).rowcount
                    if rows is None or by_key:
                        rows, first = con.execute("SELECT COUNT(*), MIN(ts) FROM main.readings").fetchone()
                    elif n:
                        rows += n
                        f2 = con.execute("SELECT MIN(ts) FROM src.readings WHERE rowid>? AND rowid<=? AND ts>=? AND ts<=?",
                                         (lo, hi, m, m + "-99")).fetchone()[0]
                        first = min(x for x in (first, f2) if x) if (first or f2) else None
                    con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [("upto", str(hi)), ("rows", str(rows)), ("first", first or "")])
                    con.execute("COMMIT")
                except BaseException:
                    try:
                        con.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    raise
                self._meta[m] = {"upto": hi, "rows": int(rows), "first": first}
                n_all += n
                lo = hi
                if by_key or lo >= snap:
                    break
        finally:
            con.close()
        if n_all:
            self.event("database", f"{n_all} later readings of {m} added to archive\\{os.path.basename(path)}", severity="info",
                       code="DB_MONTH_LATE")
        return n_all

    def _delete_month(self, m: str, upto: int | None, chunk: int, pause_s: float) -> None:
        """Delete the main file's rows of month m up to row number `upto` (all of them in its file), a range of row
        numbers at a time: each step is a short look-up by row number under the writer's lock. The newest row is
        kept (row numbers are then never given out twice); it goes at a later run."""
        if upto is None:
            return
        lo = self._read("SELECT MIN(rowid) FROM readings WHERE ts>=? AND ts<=? AND rowid<=?", (m, m + "-99", upto))[0][0]
        if lo is None:
            return
        chunk = max(100, int(chunk))
        while lo <= upto:
            hi = min(lo + chunk, upto + 1)
            with self._lock:
                n = self._db.execute("DELETE FROM readings WHERE rowid>=? AND rowid<? AND ts>=? AND ts<=? "
                                     "AND rowid<(SELECT MAX(rowid) FROM readings)", (lo, hi, m, m + "-99")).rowcount
                self._db.commit()
            lo = hi
            if n > 0 and pause_s:
                time.sleep(pause_s)

    def purge_older_than(self, days: int) -> int:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        with self._lock:
            n = self._db.execute("DELETE FROM readings WHERE ts < ? AND rowid<(SELECT MAX(rowid) FROM readings)", (iso,)).rowcount
            self._db.commit()
        for m, path in sorted(dict(self._months).items()):  # a whole month older than the cutoff: its file goes
            if m + "-99" < iso:
                try:
                    os.remove(path)
                except OSError:
                    continue                               # open somewhere: kept, and still read
                self._months.pop(m, None)
                self._meta.pop(m, None)
        return n
