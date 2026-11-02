"""
The live reader: the site master (InteliNeo) every few seconds, for the local dashboard only.

Why only the master: it already knows what every source gives - gensets P (over CAN), mains import P,
PV actual P (from its own reads of the SAU), the breakers, the mode, which genset is on the bus and its
own comm-fail bits for every device it reads. Reading it fast costs the SAU and the InteliMains nothing.

How it stays gentle:
  - one TCP connection, kept open (opening and closing one every 2 s would cost the controller more);
    after any failed request it is closed and opened fresh on the next cycle, so a late answer can never
    be taken for the answer to the next request;
  - the same few block reads as the site read; nothing else;
  - when the master does not answer it backs off: 2 s, 4 s, 8 s ... up to a minute, never faster.

What it does NOT do: it never refreshes the Turkey feed. Turkey's Modbus registers and /api/v1/tags change
only at the full site read (every 10 minutes), exactly as before.

The database gets: every on/off signal and every state at the moment it changes, and the numbers once a
minute; a value that does not change is still stored every 5 minutes. About 100,000 rows a day for the
master (a few MB), against 4 million if every 2-second reading were kept.

The master's comm-fail bits (ECU n Comm Fail) are watched too: every fail and its end go to the event log
with the device's name, and when the master loses a device a few seconds after this program read that
device, it says so. After two such cases for a device in `back_off`, that device is read only hourly from
then on (remembered in data/settings.json): our reads must never be the reason the master loses a device.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from .registry import Quality

STATE_UNITS = ("",)          # an integer point without a unit is a state or a counter: stored when it changes


class LiveReader:
    def __init__(self, collector, cfg: dict):
        self.c = collector
        reg = collector.registry
        self.every_s = max(1.0, float(cfg.get("every_s", 2)))
        # never more than this many requests a second on the master: a controller that refuses a block across
        # its "Not Used" registers is read as several smaller blocks, and then the live rate slows down
        self.max_rps = max(0.5, float(cfg.get("max_requests_per_s", 2)))
        self.wanted_every_s = self.every_s
        self.store_every_s = max(self.every_s, float(cfg.get("store_every_s", 60)))
        # an unchanged value is still stored every 5 min, so every 10-minute bucket of the trends has a reading
        self.idle_store_s = max(self.store_every_s, float(cfg.get("idle_store_s", 300)))
        self.device_ids = [d for d in (cfg.get("devices") or []) if d in reg.devices]
        # the live devices' (host, port): their connections are kept open between site reads
        self.endpoints = frozenset(endpoint(reg.devices[d]) for d in self.device_ids if reg.devices[d].connection.get("host"))
        # the master's comm-fail bits: point id -> (name, [devices we read that it belongs to])
        self.links: dict[str, tuple[str, list[str]]] = {}
        for pid, v in (cfg.get("links") or {}).items():
            name, devs = (v[0], list(v[1:])) if isinstance(v, (list, tuple)) and v else (str(v), [])
            self.links[str(pid)] = (str(name), [d for d in devs if d in reg.devices])
        self.back_off = {d for d in (cfg.get("back_off") or []) if d in reg.devices}
        self.guard_window_s = float(cfg.get("guard_window_s", 15))
        self.guard_count = int(cfg.get("guard_count", 2))
        self.back_off_s = int(cfg.get("back_off_s", 3600))
        self._persisted: dict[tuple[str, str], tuple] = {}       # (device, point) -> (value, quality, monotonic time)
        self._link_state: dict[str, tuple[bool, float]] = {}    # point -> (failing, since epoch)
        self._near_misses: dict[str, int] = {}
        self.history: deque = deque(maxlen=int(900 / self.every_s) + 5)   # the last 15 min for the live chart
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.cycles = 0
        self.failures_in_row = 0
        self.last_ok_at: datetime | None = None
        self.last_cycle_at: datetime | None = None
        self.last_ms: float | None = None

    # ------------------------------------------------------------------ state
    @property
    def running(self) -> bool:
        """True while the thread is alive and has finished a cycle in the last minute (or is backing off
        on purpose): the site read then leaves these devices to it."""
        if self.thread is None or not self.thread.is_alive() or self.last_cycle_at is None:
            return False
        return (datetime.now(timezone.utc) - self.last_cycle_at).total_seconds() < 90

    def status(self) -> dict:
        return {"enabled": True, "running": self.running, "every_s": self.every_s, "devices": self.device_ids,
                "requests_per_cycle": self.requests_per_cycle(),
                "cycles": self.cycles, "last_ok": self.last_ok_at.isoformat() if self.last_ok_at else None,
                "last_ms": self.last_ms, "backing_off": self.failures_in_row > 0,
                "links": {pid: {"name": name, "failing": self._link_state.get(pid, (False, 0))[0],
                                "since": datetime.fromtimestamp(self._link_state[pid][1], timezone.utc).isoformat()
                                if pid in self._link_state and self._link_state[pid][1] else None}
                          for pid, (name, _d) in self.links.items()}}

    # ------------------------------------------------------------------ thread
    def start(self) -> None:
        if not self.device_ids:
            return
        self.thread = threading.Thread(target=self._run, daemon=True, name="live-reader")
        self.thread.start()

    def close(self) -> None:
        self.stop.set()

    def _run(self) -> None:
        while not self.stop.is_set():
            t0 = time.monotonic()
            try:
                ok = self.cycle()
            except Exception as exc:                           # never let the thread die on a surprise
                print(f"[live] cycle failed: {exc}")
                ok = False
            self.failures_in_row = 0 if ok else min(self.failures_in_row + 1, 6)
            wait = self.every_s * (2 ** self.failures_in_row) if self.failures_in_row else self.every_s
            wait = min(60.0, wait)
            self.stop.wait(max(0.2, wait - (time.monotonic() - t0)))

    # ------------------------------------------------------------------ one cycle
    def cycle(self) -> bool:
        """Read every live device once. Returns True when at least one block answered."""
        from .adapters import reading
        any_ok = False
        t0 = time.perf_counter()
        for did in self.device_ids:
            ad = self.c.adapters[did]
            dev = self.c.registry.devices[did]
            try:
                rows = ad.read()
            except Exception as exc:
                rows = {p.id: reading(p, None, Quality.BAD, f"read failed: {exc}") for p in dev.points}
            failed = getattr(ad, "last_failed_blocks", 0)
            ok_blocks = getattr(ad, "last_ok_blocks", 0 if failed else 1)
            if failed:                                        # a fresh connection next time: no late answer is ever
                try:                                          # taken for the answer to the next request
                    ad.close()
                except Exception:
                    pass
            any_ok = any_ok or ok_blocks > 0
            self.c.store.ingest(did, rows, persist=self._to_store(did, rows))
            self.c._health(did, rows)
            self._watch_links(did, rows)
        self.last_ms = round((time.perf_counter() - t0) * 1000, 1)
        self._pace()
        now = datetime.now(timezone.utc)
        self.cycles += 1
        self.last_cycle_at = now
        if any_ok:
            self.last_ok_at = now
        self.history.append(self.c.live_point())
        return any_ok

    def requests_per_cycle(self) -> int:
        return sum(len(getattr(self.c.adapters[d], "_blocks", [])) or 1 for d in self.device_ids)

    def _pace(self) -> None:
        """Slow the live rate down when a cycle needs more requests than max_requests_per_s allows."""
        n = self.requests_per_cycle()
        every = max(self.wanted_every_s, round(n / self.max_rps, 1))
        if every != self.every_s:
            self.c.store.event("read_rate", f"live reading of {', '.join(self.device_ids)}: {n} requests per cycle, "
                               f"so every {every:g} s (at most {self.max_rps:g} requests a second)",
                               severity="info", code="LIVE_RATE")
            self.every_s = every

    def _to_store(self, did: str, rows: dict) -> dict:
        """Which of this cycle's readings go to the database (see the module text)."""
        keep, t = {}, time.monotonic()
        for pid, r in rows.items():
            key = (did, pid)
            last = self._persisted.get(key)
            v = r.value
            state = isinstance(v, bool) or (isinstance(v, int) and r.unit in STATE_UNITS)
            if last is None or last[1] != r.quality:
                due = True
            elif state:
                due = v != last[0] or t - last[2] >= self.idle_store_s
            else:
                due = (t - last[2] >= self.store_every_s and v != last[0]) or t - last[2] >= self.idle_store_s
            if due:
                keep[pid] = r
                self._persisted[key] = (v, r.quality, t)
        return keep

    # ------------------------------------------------------------------ the master's comm-fail bits
    def _watch_links(self, did: str, rows: dict) -> None:
        for pid, (name, devs) in self.links.items():
            r = rows.get(pid)
            if r is None or r.quality != Quality.GOOD or not isinstance(r.value, bool):
                continue
            was, since = self._link_state.get(pid, (None, 0.0))
            now = time.time()
            if was is None:                                   # first look: remember, report a fail already on
                self._link_state[pid] = (r.value, now if r.value else 0.0)
                if r.value:
                    self.c.store.event("master_link", f"{did}: {name} comm fail on the master (already on at start)",
                                       device_id=did, severity="warning", code="MASTER_LINK_FAIL")
                continue
            if r.value and not was:
                self._link_state[pid] = (True, now)
                after = self._our_recent_read(devs, now)
                msg = f"{did}: {name} comm fail on the master"
                if after is not None:
                    msg += f", {after[1]:.0f} s after this program read {after[0]}"
                self.c.store.event("master_link", msg, device_id=did, severity="warning", code="MASTER_LINK_FAIL")
                if after is not None:
                    self._near_miss(after[0], name)
            elif was and not r.value:
                self._link_state[pid] = (False, 0.0)
                self.c.store.event("master_link", f"{did}: {name} comm back on the master after {now - since:.0f} s",
                                   device_id=did, severity="info", code="MASTER_LINK_OK")

    def _our_recent_read(self, devs: list[str], now: float):
        """(device, seconds) when this program read one of `devs` - or another device behind the same
        gateway (host and port) - within the guard window before `now`."""
        reg = self.c.registry
        eps = {endpoint(reg.devices[d]) for d in devs}
        best = None
        for d, at in list(self.c.read_times.items()):
            if d in devs or endpoint(reg.devices[d]) in eps:
                dt = now - at
                if 0 <= dt <= self.guard_window_s and (best is None or dt < best[1]):
                    best = (d, dt)
        if best is None:
            return None
        # name the device of the link (e.g. pv-array) even when an inverter on the same gateway was read last
        own = next((d for d in devs if endpoint(reg.devices[d]) == endpoint(reg.devices[best[0]])), best[0])
        return own, best[1]

    def _near_miss(self, did: str, name: str) -> None:
        n = self._near_misses[did] = self._near_misses.get(did, 0) + 1
        if did in self.back_off and n >= self.guard_count and self.c.device_every_s(did) < self.back_off_s:
            self.c.slow_down(did, self.back_off_s,
                             f"{did}: the master lost {name} right after this program read it, {n} times; "
                             f"{did} is read only every {self.back_off_s // 60} min from now on")


def endpoint(dev) -> tuple[str, int]:
    c = dev.connection
    return (str(c.get("host")), int(c.get("port", 502)))


def history_since(reader: "LiveReader | None", c, minutes: float = 15.0) -> list:
    if reader is None:
        return []
    cut = (c.now() - timedelta(minutes=minutes)).timestamp() * 1000
    return [p for p in list(reader.history) if p[0] >= cut]
