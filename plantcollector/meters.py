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
  - a connection the gateway accepted while it was still ignoring new connections is never served (after a restart,
    or when another program had just used the gateway). So a connection that has not answered for a while
    (`first_answer_s`, 1 min, on a new one; `reopen_after_s`, 3 min, on one that answered before) is closed, and
    then the reader stays away from that gateway for `rest_s` (3.5 min) before it opens a new one: opened at once,
    the new one would again fall in the gateway's quiet time, and so would every one after it (1.0.7 - 1.0.9 did
    that after a restart and got no data for hours). The same rest follows a connection the gateway closed;
  - the time a meter connection was last open is kept in data/meters-connection.json: after a restart (an update)
    the reader waits until `rest_s` after it before connecting, so the first connection is served. On 2026-11-06 a
    new connection was served again 2.5 min after the last one closed;
  - while it waits, and while a connection is not answered, the readings are marked bad ("no data" is written to
    the event log only after `comms_debounce` cycles in a row);
  - before connecting, it checks which address this PC would send from: one in the meter's network (192.168.141.x),
    or the address it uses for the rest of the plant (the plant network adapter, whose netmask may well cover
    192.168.141.x: on 2026-11-06 the plant PC reached the gateways that way, with no address of its own in
    192.168.141.x). Any other address means another adapter (the CCI Wi-Fi), and then the request is not sent at
    all (meters.any_route: true sends it anyway). 1.0.7 - 1.0.11 wanted an address in 192.168.141.x and never
    asked the meters on that PC.

The database gets every value once every `store_every_s` (5 min by default), and at once when a meter stops or
starts answering: two meters, about 24 values each, 288 times a day, so about 14,000 rows a day.

Read-only like everything else: the adapter only has read requests.
"""

from __future__ import annotations

import json
import math
import os
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


def plant_sources(reg, skip=()) -> set[str]:
    """The addresses this PC sends from to reach the plant's other Modbus devices: its plant network adapter."""
    out = set()
    for d in reg.devices.values():
        host = (d.connection or {}).get("host")
        if d.id in skip or d.adapter != "modbus_tcp" or not host or str(host).startswith("127."):
            continue
        src = route_source(str(host))
        if src:
            out.add(src)
    return out


def on_its_network(host: str, plant: set | frozenset = frozenset(), any_route: bool = False) -> bool:
    """True when this PC would send to `host` from an address in the host's /24, or from the address it uses for the
    rest of the plant (`plant`: the same adapter, its netmask covers the host), or any address with any_route."""
    if str(host).startswith("127."):
        return True
    src = route_source(host)
    if not src:
        return False
    return src.split(".")[:3] == str(host).split(".")[:3] or src in plant or any_route


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
        self.reopen_after_s = max(0.1, float(cfg.get("reopen_after_s", 180)))      # no answer on one that answered
        self.first_answer_s = max(0.1, float(cfg.get("first_answer_s", 60)))       # no answer on a new one
        self.rest_s = max(0.0, float(cfg.get("rest_s", 210)))                      # then away from the gateway
        self._ep = {d: endpoint(reg.devices[d]) for d in self.device_ids}
        # per gateway (host, port): since when its connection has not answered, when that connection was opened,
        # whether it ever answered, and until when no new connection is opened
        self._quiet_since: dict[tuple, float] = {}
        self._conn_since: dict[tuple, float | None] = {}
        self._conn_answered: dict[tuple, bool] = {}
        self._rest_until: dict[tuple, float] = {}
        self._off_net: dict[str, bool] = {}
        self.endpoints = frozenset(self._ep.values())
        dd = getattr(collector, "data_dir", None)
        self.state_path = os.path.join(dd, "meters-connection.json") if dd and cfg.get("remember", True) else None
        self.any_route = bool(cfg.get("any_route", False))
        self._plant: tuple[float, frozenset] = (-1e9, frozenset())     # (when, the plant adapter's addresses)
        self.routes: dict[str, dict] = {}                              # device -> {"from": address, "ok": bool}
        self._seen_written = 0.0
        self._last_store: dict[str, float] = {}
        self._answered: dict[str, bool] = {}
        self._bad_run = {d: 0 for d in self.device_ids}
        self._ok_run = {d: 0 for d in self.device_ids}
        self.stop = threading.Event()
        self._wake = threading.Event()                       # "Read devices now": the next cycle at once
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
        now = time.monotonic()
        return {"running": self.running, "every_s": self.every_s, "store_every_s": self.store_every_s,
                "devices": self.device_ids, "cycles": self.cycles, "last_ms": self.last_ms,
                "last_cycle": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
                "last_ok": {d: t.isoformat() for d, t in dict(self.last_ok_at).items()},
                "waiting_s": {f"{h}:{p}": round(t - now) for (h, p), t in dict(self._rest_until).items() if t > now},
                "routes": dict(self.routes)}

    def _plant_sources(self) -> frozenset:
        """The plant adapter's addresses, looked up again every minute (a cable or an address may change)."""
        at, got = self._plant
        if time.monotonic() - at > 60:
            got = frozenset(plant_sources(self.c.registry, skip=set(self.device_ids)))
            self._plant = (time.monotonic(), got)
        return got

    # ------------------------------------------------------------------ when a meter connection was last open
    def _note_open(self, force: bool = False) -> None:
        """Write the time a meter connection was open (at most every 30 s): a restart then knows how long to wait."""
        if not self.state_path:
            return
        now = time.time()
        if not force and now - self._seen_written < 30:
            return
        self._seen_written = now
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump({"open_at": datetime.fromtimestamp(now, timezone.utc).isoformat(), "epoch": now}, f)
        except OSError:
            pass

    def _rest_after_restart(self) -> float:
        """Seconds to wait before the first connection: until rest_s after a meter connection was last open."""
        if not self.state_path or not self.rest_s:
            return 0.0
        try:
            with open(self.state_path, encoding="utf-8") as f:
                seen = float(json.load(f).get("epoch"))
        except (OSError, ValueError, TypeError, AttributeError):
            return 0.0
        ago = time.time() - seen
        return self.rest_s - ago if 0 <= ago < self.rest_s else 0.0

    # ------------------------------------------------------------------ thread
    def start(self) -> bool:
        if not self.device_ids or self.running:
            return False
        wait = self._rest_after_restart()
        if wait > 0:                                         # the gateways saw a connection of ours close just now
            until = time.monotonic() + wait
            for ep in self.endpoints:
                self._rest_until[ep] = max(self._rest_until.get(ep, 0.0), until)
            self.c.store.event("meters", f"the meters are read from {wait / 60:.1f} min on: their gateways ignore a new "
                               "connection for a few minutes after the last one closed (this program was just restarted)",
                               severity="info", code="WAIT")
        self.stop.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="meter-reader")
        self.thread.start()
        return True

    def wake_now(self, min_gap_s: float = 3.0) -> bool:
        """Read the meters at once (on their kept connections, in this reader's own thread), unless they were read
        less than min_gap_s ago. True when a cycle was asked for."""
        last = self.last_cycle_at
        if last is not None and (datetime.now(timezone.utc) - last).total_seconds() < min_gap_s:
            return False
        self._wake.set()
        return True

    def close(self, wait_s: float = 5.0) -> None:
        self.stop.set()
        self._wake.set()
        if any(self._conn_since.get(ep) is not None for ep in self.endpoints):
            self._note_open(force=True)                      # the connections close with this program
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
            now = time.monotonic()
            nxt = self.every_s - (now - t0)
            ends = [t - now for t in dict(self._rest_until).values() if t > now]
            if ends:                                           # a gateway's rest ends sooner: read it right then
                nxt = min(nxt, min(ends) + 0.05)
            self._wake.wait(max(0.2, nxt))
            self._wake.clear()

    # ------------------------------------------------------------------ one cycle
    def _rest(self, ep: tuple, now_m: float, why: str, device_id: str | None = None) -> None:
        """Close the connection to a gateway (if open) and open no new one for rest_s."""
        for did, e in self._ep.items():
            if e == ep:
                try:
                    self.c.adapters[did].close()
                except Exception:
                    pass
        self._conn_since[ep] = None
        self._conn_answered[ep] = False
        self._quiet_since.pop(ep, None)
        self._rest_until[ep] = now_m + self.rest_s
        self.c.store.event("meters", f"{device_id or ep[0]}: {why}; a new connection is opened in {self.rest_s / 60:.1f} min "
                           "(the gateway ignores a new connection for a few minutes after one closed)",
                           device_id=device_id, severity="info", code="REOPEN")

    def cycle(self) -> None:
        """Read every kept device once, on its kept connection."""
        from .adapters import reading
        t0 = time.perf_counter()
        alive: dict[tuple, bool] = {}
        for did in self.device_ids:
            if self.stop.is_set():
                break
            ad = self.c.adapters[did]
            dev = self.c.registry.devices[did]
            ep = self._ep[did]
            host = str((dev.connection or {}).get("host"))
            now_m = time.monotonic()
            plant = self._plant_sources()
            reach = on_its_network(host, plant, self.any_route)
            src = route_source(host) if not str(host).startswith("127.") else "127.0.0.1"
            self.routes[did] = {"from": src, "ok": bool(reach)}
            why_not = (f"this PC would ask {host} from {src}, not from its plant network address" if src else
                       f"this PC has no route to {host}")
            if reach != self._off_net.get(did, True) and not reach:
                self.c.store.event("meters", f"{did}: {why_not} ({', '.join(sorted(plant)) or 'none found'}), so it is not "
                                   "asked (it would go out through another network)", device_id=did,
                                   severity="warning", code="NO_ROUTE")
            self._off_net[did] = reach
            is_open = getattr(ad, "is_open", lambda: True)
            if reach and self._conn_since.get(ep) is not None and not is_open():
                self._rest(ep, now_m, "the connection to the gateway closed", did)   # closed by the gateway, or broke
            rest = self._rest_until.get(ep, 0.0) - now_m
            failed, ok_blocks, link = 1, 0, False
            if not reach:
                rows = {p.id: reading(p, None, Quality.BAD, why_not) for p in dev.points}
            elif rest > 0:
                left = f"{int(math.ceil(rest / 5) * 5)} s"
                rows = {p.id: reading(p, None, Quality.BAD, f"the gateway takes a new connection in {left}") for p in dev.points}
            else:
                if not is_open():
                    self._conn_since[ep] = now_m             # a new connection, opened by this read
                    self._conn_answered[ep] = False
                    self._quiet_since[ep] = now_m
                try:
                    rows = ad.read()
                    failed = getattr(ad, "last_failed_blocks", 0)
                    ok_blocks = getattr(ad, "last_ok_blocks", 0 if failed else 1)
                    # an exception answer ("busy", "no meter behind me") is an answer: the link itself works
                    link = ok_blocks > 0 or getattr(ad, "last_refused", 0) > 0
                except Exception as exc:
                    rows = {p.id: reading(p, None, Quality.BAD, f"read failed: {exc}") for p in dev.points}
                if not is_open():
                    self._conn_since[ep] = None              # could not connect at all: nothing to wait for
                else:
                    self._note_open()
                alive[ep] = alive.get(ep, False) or link
            answered = ok_blocks > 0
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
        # a connection that is not served: closed, and the gateway left alone for a while
        now_m = time.monotonic()
        for ep, ok in alive.items():
            if ok:
                self._quiet_since[ep] = now_m
                self._conn_answered[ep] = True
                continue
            if self._conn_since.get(ep) is None:
                continue
            since = max(self._quiet_since.get(ep, now_m), self._conn_since[ep])
            limit = self.reopen_after_s if self._conn_answered.get(ep) else self.first_answer_s
            if now_m - since >= limit:
                did = next(d for d, e in self._ep.items() if e == ep)
                self._rest(ep, now_m, f"no answer on its connection for {limit / 60:.1f} min, so it is closed", did)
        self.last_ms = round((time.perf_counter() - t0) * 1000, 1)
        self.cycles += 1
        self.last_cycle_at = datetime.now(timezone.utc)
