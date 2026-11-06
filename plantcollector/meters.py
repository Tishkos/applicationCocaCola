"""
The meter reader: power meters behind serial gateways that need one connection kept open (connection.keep_open).

Why a reader of its own: the meters' gateways ("GATEWAY TR" meter modems, 192.168.141.1 and .3) answer every request
on a connection they have accepted, but after a connection closes they ignore a new one for a few minutes, and they
drop a connection that stays idle for 480 s ("Statute of Limitations"). Seen on 2026-11-06: a new connection for each
read answered once and then nothing for minutes; one kept connection answered 10 reads of 10, and 10,000 registers
without a miss, each in about 0.1 s.

So:
  - one connection per gateway, opened once and never closed by this program (close_all keeps it);
  - the meters are read every `every_s` (60 s by default, never slower than 240 s, so a gateway never sees the
    connection idle long enough to drop it);
  - the site read leaves these devices to this reader while it runs, so nothing else opens a second connection;
  - a broken connection is opened again at the next cycle; the gateway may ignore it for a few minutes, and those
    readings are marked bad ("no data" is written to the event log only after `comms_debounce` cycles in a row);
  - a connection that has not answered for `reopen_after_s` (3 min) is closed and opened again: one the gateway
    accepted while it was still ignoring new connections may never be served (after a restart, or when another
    program had just used the gateway). On 2026-11-06 a new connection was served again 2.5 min after the last one
    closed, so after a restart the meters are back within about 3 minutes;
  - before connecting, it checks that this PC has an address in the meter's network (192.168.141.x): without one the
    request would go out through another adapter (the CCI Wi-Fi), so it is not sent at all.

The database gets every value once every `store_every_s` (5 min by default), and at once when a meter stops or
starts answering: two meters, about 24 values each, 288 times a day, so about 14,000 rows a day.

Read-only like everything else: the adapter only has read requests.
"""

from __future__ import annotations

import socket
import threading
import time
from datetime import datetime, timezone

from .registry import Quality


def endpoint(dev) -> tuple[str, int]:
    c = dev.connection or {}
    return (str(c.get("host")), int(c.get("port", 502)))


def route_source(host: str, port: int = 502) -> str | None:
    """The address this PC would send from to reach `host` (a UDP socket is "connected" without sending anything)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, port))
            return s.getsockname()[0]
    except OSError:
        return None


def on_its_network(host: str) -> bool:
    """True when this PC has an address in the host's /24 (or the host is this PC)."""
    if str(host).startswith("127."):
        return True
    src = route_source(host)
    return bool(src) and src.split(".")[:3] == str(host).split(".")[:3]


def kept_device_ids(reg) -> list[str]:
    """The devices whose connection is kept open (Modbus TCP only)."""
    return [d.id for d in reg.devices.values() if (d.connection or {}).get("keep_open") and d.adapter == "modbus_tcp"]


class MeterReader:
    MIN_EVERY_S = 10.0
    MAX_EVERY_S = 240.0               # well inside the gateways' 480 s idle limit

    def __init__(self, collector, cfg: dict | None = None):
        cfg = dict(cfg or {})
        self.c = collector
        reg = collector.registry
        self.device_ids = [d for d in kept_device_ids(reg) if d in collector.adapters]
        self.every_s = min(self.MAX_EVERY_S, max(self.MIN_EVERY_S, float(cfg.get("every_s", 60))))
        self.store_every_s = max(self.every_s, float(cfg.get("store_every_s", 300)))
        self.comms_debounce = max(1, int(cfg.get("comms_debounce", 3)))
        self.reopen_after_s = max(60.0, float(cfg.get("reopen_after_s", 180)))
        self._quiet_since: dict[str, float] = {}             # device -> since when its connection has not answered
        self._off_net: dict[str, bool] = {}
        self.endpoints = frozenset(endpoint(reg.devices[d]) for d in self.device_ids)
        self._last_store: dict[str, float] = {}
        self._answered: dict[str, bool] = {}
        self._bad_run = {d: 0 for d in self.device_ids}
        self._ok_run = {d: 0 for d in self.device_ids}
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.cycles = 0
        self.last_cycle_at: datetime | None = None
        self.last_ok_at: dict[str, datetime] = {}
        self.last_ms: float | None = None

    # ------------------------------------------------------------------ state
    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive() and not self.stop.is_set()

    def status(self) -> dict:
        return {"running": self.running, "every_s": self.every_s, "store_every_s": self.store_every_s,
                "devices": self.device_ids, "cycles": self.cycles, "last_ms": self.last_ms,
                "last_cycle": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
                "last_ok": {d: t.isoformat() for d, t in dict(self.last_ok_at).items()}}

    # ------------------------------------------------------------------ thread
    def start(self) -> bool:
        if not self.device_ids or self.running:
            return False
        self.stop.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="meter-reader")
        self.thread.start()
        return True

    def close(self, wait_s: float = 5.0) -> None:
        self.stop.set()
        t = self.thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=wait_s)

    def _run(self) -> None:
        while not self.stop.is_set():
            t0 = time.monotonic()
            try:
                self.cycle()
            except Exception as exc:                           # never let the thread die on a surprise
                print(f"[meters] cycle failed: {exc}")
            self.stop.wait(max(1.0, self.every_s - (time.monotonic() - t0)))

    # ------------------------------------------------------------------ one cycle
    def cycle(self) -> None:
        """Read every kept device once, on its kept connection."""
        from .adapters import reading
        t0 = time.perf_counter()
        for did in self.device_ids:
            if self.stop.is_set():
                break
            ad = self.c.adapters[did]
            dev = self.c.registry.devices[did]
            host = str((dev.connection or {}).get("host"))
            now_m = time.monotonic()
            reach = on_its_network(host)
            if reach != self._off_net.get(did, True) and not reach:
                self.c.store.event("meters", f"{did}: this PC has no address in {'.'.join(host.split('.')[:3])}.x, so {host} "
                                   "is not asked (it would go out through another network)", device_id=did,
                                   severity="warning", code="NO_ROUTE")
            self._off_net[did] = reach
            if not reach:
                rows = {p.id: reading(p, None, Quality.BAD, f"this PC has no address in {'.'.join(host.split('.')[:3])}.x")
                        for p in dev.points}
                failed, ok_blocks = 1, 0
            else:
                try:
                    rows = ad.read()
                    failed = getattr(ad, "last_failed_blocks", 0)
                    ok_blocks = getattr(ad, "last_ok_blocks", 0 if failed else 1)
                except Exception as exc:
                    rows = {p.id: reading(p, None, Quality.BAD, f"read failed: {exc}") for p in dev.points}
                    failed, ok_blocks = 1, 0
            answered = ok_blocks > 0
            if answered or did not in self._quiet_since:
                self._quiet_since[did] = now_m
            elif reach and now_m - self._quiet_since[did] >= self.reopen_after_s:
                try:                                          # never served: a fresh connection next cycle
                    ad.close()
                except Exception:
                    pass
                self._quiet_since[did] = now_m
                self.c.store.event("meters", f"{did}: no answer on its connection for {self.reopen_after_s / 60:g} min; "
                                   "a new connection is opened", device_id=did, severity="info", code="REOPEN")
            turned = did in self._answered and self._answered[did] != answered
            due = did not in self._last_store or now_m - self._last_store[did] >= self.store_every_s - 1 or turned
            if due:
                self._last_store[did] = now_m
            self._answered[did] = answered
            self.c.store.ingest(did, rows, persist=None if due else {})
            if answered:
                self.c.read_times[did] = time.time()
                self.last_ok_at[did] = datetime.now(timezone.utc)
            self.c._last_read_mono[did] = time.monotonic()
            all_bad = not answered
            self._bad_run[did] = self._bad_run[did] + 1 if all_bad else 0
            self._ok_run[did] = 0 if all_bad else self._ok_run[did] + 1
            lost = did in self.c._comms_lost
            if (all_bad and self._bad_run[did] >= self.comms_debounce) or \
                    (not all_bad and (not lost or self._ok_run[did] >= 1)):
                self.c._health(did, rows)                     # "no data" only after a few cycles in a row
        self.last_ms = round((time.perf_counter() - t0) * 1000, 1)
        self.cycles += 1
        self.last_cycle_at = datetime.now(timezone.utc)
