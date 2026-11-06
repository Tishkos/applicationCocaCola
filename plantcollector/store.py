"""SQLite telemetry store: every reading with timestamp + quality, plus an event log.

One file per month: the readings of the current month (UTC) are in the main file (telemetry.sqlite); once a month is
over, its readings move to a file of their own, archive/readings-YYYY-MM.sqlite (archive_months, run in the
background by the service: the copy reads the main file beside the writer, and the delete goes a few thousand rows
at a time, so the live reading is never held up). Every query for a time range reads the months it covers - an
archived month from its own file, the others from the main file - so the charts, exports and day reports of an
earlier month find it by themselves, the main file stays the size of about one month, and a look at last month
opens only last month's file. The event log stays in the main file (it is small).

A month is read from its own file as soon as that file exists, whatever the main file still holds of it (a move that
stopped half way is finished at the next run), so no reading is ever counted twice.
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
        self._arch_info: dict[str, tuple[int, str | None]] = {}     # path -> (rows, first ts): an archive never changes
        self._roll_lock = threading.Lock()
        if self.archive_dir and os.path.isdir(self.archive_dir):
            for f in os.listdir(self.archive_dir):
                m = self.ARCHIVE.match(f)
                if m:
                    self._months[m.group(1)] = os.path.join(self.archive_dir, f)

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
                rows.append((r.source_ts.isoformat(), device_id, r.point_id, num, txt, r.quality.value, r.error))
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
        cons = getattr(self._local, "arch", None)
        if cons is None:
            cons = self._local.arch = {}
        con = cons.get(path)
        if con is None:
            con = cons[path] = sqlite3.connect(_uri(path), uri=True, check_same_thread=False)
        return con.execute(sql, args).fetchall()

    def _segments(self, since: datetime, until: datetime) -> list[tuple[str | None, str, str]]:
        """[(file, from, to)] covering [since, until]: each archived month from its own file, the rest from the main
        file (None), as ISO-text bounds (the readings' times are UTC ISO text, so text order is time order)."""
        s, u = since.isoformat(), until.isoformat()
        arch = dict(self._months)
        if not arch or s > u:
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

    def latest(self, device_id: str) -> dict[str, Reading]:
        with self._lock:                    # the live reader updates these while API threads read them
            return dict(self._latest.get(device_id, {}))

    def value(self, device_id: str, point_id: str):
        r = self._latest.get(device_id, {}).get(point_id)
        if r is None or r.quality != Quality.GOOD:
            return None
        return r.value

    def history(self, device_id: str, point_id: str, since: datetime, until: datetime) -> list[tuple[str, float | None]]:
        out: list = []
        for path, lo, hi in self._segments(since, until):
            out += self._read(
                "SELECT ts, value FROM readings WHERE device_id=? AND point_id=? AND quality='good' AND ts>=? AND ts<=? ORDER BY ts",
                (device_id, point_id, lo, hi), path)
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
        sql += " GROUP BY device_id, point_id, b"
        acc: dict[tuple[str, str], dict[int, list]] = {}
        for path, lo, hi in self._segments(since, until):          # a bucket across two months adds up both
            for d, p, b, total, n in self._read(sql, [step_s, step_s, lo, hi] + extra, path):
                cell = acc.setdefault((d, p), {}).setdefault(int(b), [0.0, 0])
                cell[0] += total or 0.0
                cell[1] += n or 0
        return {k: {b: t / n for b, (t, n) in sorted(v.items()) if n} for k, v in acc.items()}

    def pairs_in_range(self, since: datetime, until: datetime) -> list[tuple[str, str]]:
        found: set[tuple[str, str]] = set()
        for path, lo, hi in self._segments(since, until):
            found.update((d, p) for d, p in self._read(
                "SELECT DISTINCT device_id, point_id FROM readings WHERE ts>=? AND ts<=?", (lo, hi), path))
        return sorted(found)

    def _archive_info(self, path: str) -> tuple[int, str | None]:
        if path not in self._arch_info:
            n, first = self._read("SELECT COUNT(*), MIN(ts) FROM readings", (), path)[0]
            self._arch_info[path] = (int(n or 0), first)
        return self._arch_info[path]

    def months(self) -> list[dict]:
        """The months in their own files: [{"month", "file", "rows"}], oldest first."""
        return [{"month": m, "file": os.path.basename(p), "rows": self._archive_info(p)[0]} for m, p in sorted(self._months.items())]

    def count(self) -> int:
        """All readings: the main file's and every month file's (during a move, a month's rows briefly count twice)."""
        return self._read("SELECT COUNT(*) FROM readings")[0][0] + sum(self._archive_info(p)[0] for p in self._months.values())

    def first_ts(self) -> str | None:
        firsts = [self._archive_info(p)[1] for p in self._months.values()]
        rows = self._read("SELECT MIN(ts) FROM readings")
        firsts.append(rows[0][0] if rows else None)
        firsts = [f for f in firsts if f]
        return min(firsts) if firsts else None

    @staticmethod
    def month_now(now: datetime | None = None) -> str:
        return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m")

    # ------------------------------------------------------------------ months into their own files
    def archive_months(self, now: datetime | None = None, chunk: int = 20000, pause_s: float = 0.05) -> list[str]:
        """Move every finished month (UTC) from the main file into archive/readings-YYYY-MM.sqlite. Returns the months
        moved. Safe at any time and from any thread (one run at a time); the live reading goes on meanwhile."""
        if not self.archive_dir or not self._roll_lock.acquire(blocking=False):
            return []
        try:
            cur = self.month_now(now)
            moved = []
            for m in sorted(self._months):                 # what a move that stopped half way left in the main file
                if m < cur:
                    self._delete_month(m, chunk, pause_s)
            while True:
                first = self._read("SELECT MIN(ts) FROM readings")[0][0]
                if not first or first[:7] >= cur or not re.match(r"^\d{4}-\d{2}$", first[:7]):
                    break
                m = first[:7]
                if m not in self._months:
                    self._copy_month(m)
                    moved.append(m)
                self._delete_month(m, chunk, pause_s)
            return moved
        finally:
            self._roll_lock.release()

    def _copy_month(self, m: str) -> None:
        os.makedirs(self.archive_dir, exist_ok=True)
        path = os.path.join(self.archive_dir, f"readings-{m}.sqlite")
        tmp = path + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        con = sqlite3.connect(_uri(tmp, ro=False), uri=True)
        try:
            con.execute(_SCHEMA)
            con.execute("ATTACH DATABASE ? AS src", (_uri(self._path),))          # read beside the writer (WAL)
            con.execute("INSERT INTO readings SELECT * FROM src.readings WHERE ts>=? AND ts<=? ORDER BY rowid", (m, m + "-99"))
            con.commit()
            want = con.execute("SELECT COUNT(*) FROM src.readings WHERE ts>=? AND ts<=?", (m, m + "-99")).fetchone()[0]
            got = con.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
            con.execute("DETACH DATABASE src")
            if got != want:
                raise RuntimeError(f"month {m}: {got} rows copied of {want}")
            con.execute("CREATE INDEX IF NOT EXISTS ix_readings ON readings(device_id, point_id, ts)")
            con.commit()
        finally:
            con.close()
        os.replace(tmp, path)
        self._months[m] = path
        self.event("database", f"readings of {m} moved to archive\\{os.path.basename(path)} ({got} rows)", severity="info",
                   code="DB_MONTH")

    def _delete_month(self, m: str, chunk: int, pause_s: float) -> None:
        while True:
            with self._lock:
                n = self._db.execute("DELETE FROM readings WHERE rowid IN (SELECT rowid FROM readings WHERE ts>=? AND ts<=? LIMIT ?)",
                                     (m, m + "-99", chunk)).rowcount
                self._db.commit()
            if n <= 0:
                return
            time.sleep(pause_s)

    def purge_older_than(self, days: int) -> int:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        with self._lock:
            n = self._db.execute("DELETE FROM readings WHERE ts < ?", (iso,)).rowcount
            self._db.commit()
        for m, path in sorted(self._months.items()):       # a whole month older than the cutoff: its file goes
            if m + "-99" < iso:
                self._months.pop(m, None)
                self._arch_info.pop(path, None)
                try:
                    os.remove(path)
                except OSError:
                    pass
        return n
