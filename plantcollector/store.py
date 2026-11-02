"""SQLite telemetry store: every reading with timestamp + quality, plus an event log."""

from __future__ import annotations

import os
import sqlite3
import threading
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


class TelemetryStore:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")      # WAL + NORMAL: safe on power loss, far fewer fsyncs
        self._lock = threading.Lock()
        self._bulk = 0
        self._path = db_path
        self._local = threading.local()     # one read connection per thread: WAL readers never block or trample each other
        self._db.execute("""CREATE TABLE IF NOT EXISTS readings (
            ts TEXT NOT NULL, device_id TEXT NOT NULL, point_id TEXT NOT NULL,
            value REAL, text_value TEXT, quality TEXT NOT NULL, error TEXT)""")
        self._db.execute("CREATE INDEX IF NOT EXISTS ix_readings ON readings(device_id, point_id, ts)")
        self._db.execute("""CREATE TABLE IF NOT EXISTS events (
            ts TEXT NOT NULL, kind TEXT NOT NULL, device_id TEXT, severity TEXT, code TEXT, message TEXT)""")
        self._db.commit()
        self._latest: dict[str, dict[str, Reading]] = {}

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

    def _read(self, sql: str, args=()) -> list:
        """Run a read-only query. API requests run in parallel threads, so they must not share the writer's connection."""
        if self._path == ":memory:":
            with self._lock:
                return self._db.execute(sql, args).fetchall()
        con = getattr(self._local, "con", None)
        if con is None:
            con = self._local.con = sqlite3.connect(self._path, check_same_thread=False)
        return con.execute(sql, args).fetchall()

    def latest(self, device_id: str) -> dict[str, Reading]:
        with self._lock:                    # the live reader updates these while API threads read them
            return dict(self._latest.get(device_id, {}))

    def value(self, device_id: str, point_id: str):
        r = self._latest.get(device_id, {}).get(point_id)
        if r is None or r.quality != Quality.GOOD:
            return None
        return r.value

    def history(self, device_id: str, point_id: str, since: datetime, until: datetime) -> list[tuple[str, float | None]]:
        return self._read(
            "SELECT ts, value FROM readings WHERE device_id=? AND point_id=? AND quality='good' AND ts>=? AND ts<=? ORDER BY ts",
            (device_id, point_id, since.isoformat(), until.isoformat()))

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
        sql = ("SELECT device_id, point_id, (CAST(strftime('%s', ts) AS INTEGER) / ?) * ? AS b, AVG(value) "
               "FROM readings WHERE quality='good' AND value IS NOT NULL AND ts>=? AND ts<=?")
        args: list = [step_s, step_s, since.isoformat(), until.isoformat()]
        if pairs:
            sql += " AND (" + " OR ".join("(device_id=? AND point_id=?)" for _ in pairs) + ")"
            for d, p in pairs:
                args += [d, p]
        sql += " GROUP BY device_id, point_id, b ORDER BY b"
        out: dict[tuple[str, str], dict[int, float]] = {}
        for d, p, b, v in self._read(sql, args):
            out.setdefault((d, p), {})[int(b)] = v
        return out

    def pairs_in_range(self, since: datetime, until: datetime) -> list[tuple[str, str]]:
        rows = self._read(
            "SELECT DISTINCT device_id, point_id FROM readings WHERE ts>=? AND ts<=? ORDER BY device_id, point_id",
            (since.isoformat(), until.isoformat()))
        return [(d, p) for d, p in rows]

    def count(self) -> int:
        return self._read("SELECT COUNT(*) FROM readings")[0][0]

    def first_ts(self) -> str | None:
        rows = self._read("SELECT MIN(ts) FROM readings")
        return rows[0][0] if rows else None

    def purge_older_than(self, days: int) -> int:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        with self._lock:
            n = self._db.execute("DELETE FROM readings WHERE ts < ?", (iso,)).rowcount
            self._db.commit()
        return n
