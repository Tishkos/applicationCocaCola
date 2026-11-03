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


class LiveReader:
    def __init__(self, collector, cfg: dict):
        self.c = collector
        reg = collector.registry
        self.every_s = max(1.0, float(cfg.get("every_s", 2)))
        # never more than this many requests a second on the master: a controller that refuses a block across
        # its "Not Used" registers is read as several smaller blocks, and then the live rate slows down
        self.max_rps = max(0.5, float(cfg.get("max_requests_per_s", 2)))
        self.wanted_every_s = self.every_s
        self._store_base = float(cfg.get("store_every_s", 60))
        self.store_every_s = max(self.every_s, self._store_base)
        # an unchanged value is still stored every 5 min, so every 10-minute bucket of the trends has a reading
        self._idle_base = float(cfg.get("idle_store_s", 300))
        self.idle_store_s = max(self.store_every_s, self._idle_base)
        self.device_ids = [d for d in (cfg.get("devices") or []) if d in reg.devices]
        # the live devices' (host, port): their connections are kept open between site reads
        self.endpoints = frozenset(endpoint(reg.devices[d]) for d in self.device_ids if reg.devices[d].connection.get("host"))
        # the master's comm-fail bits: point id -> (name, [devices we read that it belongs to])
        self.links: dict[str, tuple[str, list[str]]] = {}
        for pid, v in (cfg.get("links") or {}).items():
            name, devs = (v[0], list(v[1:])) if isinstance(v, (list, tuple)) and v else (str(v), [])
            self.links[str(pid)] = (str(name), [d for d in devs if d in reg.devices])
        self.back_off = {d for d in (cfg.get("back_off") or []) if d in reg.devices}
        # how soon after this program's read a comm fail on the master counts as "right after": the configured window,
        # plus one live interval when the master is read slowly (the fail is only seen at the next live read)
        self._guard_base = float(cfg.get("guard_window_s", 15))
        self.guard_window_s = self._guard_window()
        self.guard_count = int(cfg.get("guard_count", 2))
        self.guard_period_s = float(cfg.get("guard_period_s", 86400))      # the hits must fall within a day ...
        self.guard_share = float(cfg.get("guard_share", 0.5))              # ... and be at least half of that link's fails
        self.back_off_s = int(cfg.get("back_off_s", 3600))
        # a few readings in a row before "no data" / "data restored" is written (a 2 s blip is not an event)
        self.comms_debounce = max(1, int(cfg.get("comms_debounce", 3)))
        # an on/off signal or a state is stored the moment it changes; only a signal that changes more than
        # state_burst times a minute (a flapping bit) is held to one row every state_gap_s
        self.state_gap_s = 10.0
        self.state_burst = 6
        self._state_rows: dict[tuple[str, str], deque] = {}
        # states: on/off signals and unit-less words without a scale (mode, breaker state, CAN mask ...)
        self.state_points = {d: {p.id for p in reg.devices[d].points
                                 if p.type == "boolean" or (p.unit == "" and "scale" not in p.raw and "derived" not in p.raw)}
                             for d in self.device_ids}
        self._persisted: dict[tuple[str, str], tuple] = {}       # (device, point) -> (value, quality, monotonic time)
        self._link_state: dict[str, tuple[bool, float]] = {}    # point -> (failing, since epoch)
        self._near_misses: dict[str, list[float]] = {}          # device -> times the master lost it right after our read
        self._edges: dict[str, list[float]] = {}                # device -> times the master lost it at all
        self._bad_run = {d: 0 for d in self.device_ids}
        self._ok_run = {d: 0 for d in self.device_ids}
        self.history: deque = deque(maxlen=self._history_len())   # the live chart: 15 min, or 1 h when read slowly
        self.stop = threading.Event()
        self._wake = threading.Event()                           # a new rate from Settings: the next cycle at once
        self.thread: threading.Thread | None = None
        self.cycles = 0
        self.failures_in_row = 0
        self.last_ok_at: datetime | None = None
        self.last_cycle_at: datetime | None = None
        self.last_ms: float | None = None

    GUARD_MAX_EVERY_S = 60.0          # the guard judges only while the master is read at least once a minute

    def guard_active(self) -> bool:
        return self.every_s <= self.GUARD_MAX_EVERY_S

    def _guard_window(self) -> float:
        return self._guard_base if self.every_s <= 5 else self._guard_base + min(self.every_s, self.GUARD_MAX_EVERY_S)

    def _history_len(self) -> int:
        """Points kept for the live chart: 15 minutes, or an hour when the master is read every 30 s or slower."""
        minutes = 60 if self.every_s >= 30 else 15
        return int(minutes * 60 / self.every_s) + 5

    def chart_minutes(self) -> int:
        return 60 if self.every_s >= 30 else 15

    def set_every(self, seconds: float) -> None:
        """A new live rate from Settings, applied at the next cycle (which starts at once)."""
        self.wanted_every_s = max(1.0, float(seconds))
        self.every_s = self.wanted_every_s
        self._pace()
        self.store_every_s = max(self.every_s, self._store_base)
        self.idle_store_s = max(self.store_every_s, self._idle_base)
        self.guard_window_s = self._guard_window()
        self.history = deque(self.history, maxlen=self._history_len())
        self._wake.set()

    # ------------------------------------------------------------------ state
    @property
    def running(self) -> bool:
        """True while the thread is alive and has finished a cycle lately (two and a half live intervals, at
        least 90 s; or is backing off on purpose): the site read then leaves these devices to it."""
        if self.thread is None or not self.thread.is_alive() or self.last_cycle_at is None:
            return False
        return (datetime.now(timezone.utc) - self.last_cycle_at).total_seconds() < max(90.0, 2.5 * self.every_s + 30)

    def status(self) -> dict:
        return {"enabled": True, "running": self.running, "every_s": self.every_s, "devices": self.device_ids,
                "requests_per_cycle": self.requests_per_cycle(),
                "cycles": self.cycles, "last_ok": self.last_ok_at.isoformat() if self.last_ok_at else None,
                "last_ms": self.last_ms, "backing_off": self.failures_in_row > 0,
                "links": {pid: {"name": name, "failing": self._link_state.get(pid, (None, 0))[0],
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
        self._wake.set()

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
            wait = min(max(60.0, self.every_s), wait)          # backing off never asks more often than normal
            elapsed = time.monotonic() - t0
            # a slow master gets more rest, not less: never busy with it more than half of the time
            self._wake.wait(max(0.2, wait - elapsed, min(elapsed, 60.0)))
            self._wake.clear()

    # ------------------------------------------------------------------ one cycle
    def cycle(self) -> bool:
        """Read every live device once. Returns True when the cycle was healthy: something answered, nothing
        timed out and nothing said "busy" (a block the device refuses for its address is not a reason to slow
        down: it answers at once). Anything else counts towards the back-off."""
        from .adapters import reading
        healthy, any_ok = True, False
        t0 = time.perf_counter()
        for did in self.device_ids:
            ad = self.c.adapters[did]
            dev = self.c.registry.devices[did]
            try:
                rows = ad.read()
                failed = getattr(ad, "last_failed_blocks", 0)
                ok_blocks = getattr(ad, "last_ok_blocks", 0 if failed else 1)
                io_failed, busy = getattr(ad, "last_io_failed", failed), getattr(ad, "last_busy", 0)
            except Exception as exc:
                rows = {p.id: reading(p, None, Quality.BAD, f"read failed: {exc}") for p in dev.points}
                failed, ok_blocks, io_failed, busy = 1, 0, 1, 0
            if io_failed:                                     # no answer: a fresh connection next time, so a late answer
                try:                                          # is never taken for the answer to the next request
                    ad.close()
                except Exception:
                    pass
            any_ok = any_ok or ok_blocks > 0
            healthy = healthy and ok_blocks > 0 and not io_failed and not busy
            self.c.store.ingest(did, rows, persist=self._to_store(did, rows))
            all_bad = ok_blocks == 0 and failed > 0
            self._bad_run[did] = self._bad_run[did] + 1 if all_bad else 0
            self._ok_run[did] = 0 if all_bad else self._ok_run[did] + 1
            lost = did in self.c._comms_lost
            if (all_bad and self._bad_run[did] >= self.comms_debounce) or \
                    (not all_bad and (not lost or self._ok_run[did] >= self.comms_debounce)):
                self.c._health(did, rows)                     # comms lost / restored only after a few readings in a row
            self._watch_links(did, rows)
        self.last_ms = round((time.perf_counter() - t0) * 1000, 1)
        self._pace()
        now = datetime.now(timezone.utc)
        self.cycles += 1
        self.last_cycle_at = now
        if any_ok:
            self.last_ok_at = now
        self.history.append(self.c.live_point())
        return healthy

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
            state = pid in self.state_points.get(did, ())
            if last is None:
                due = True
            elif last[1] != r.quality:                        # a flapping link is stored at most once a minute
                due = t - last[2] >= self.store_every_s
            elif state:
                recent = self._state_rows.setdefault(key, deque(maxlen=self.state_burst))
                calm = len(recent) < self.state_burst or t - recent[0] >= 60
                due = (v != last[0] and (calm or t - last[2] >= self.state_gap_s)) or t - last[2] >= self.idle_store_s
            else:
                due = (t - last[2] >= self.store_every_s and v != last[0]) or t - last[2] >= self.idle_store_s
            if due:
                keep[pid] = r
                self._persisted[key] = (v, r.quality, t)
                if state:
                    self._state_rows.setdefault(key, deque(maxlen=self.state_burst)).append(t)
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
                # read slower than once a minute, the master's fail is seen too late to tell whether it followed one
                # of our reads: no "N s after" and no automatic slow-down then (the fail itself is still logged)
                after = self._our_recent_read(devs, now) if self.guard_active() else None
                msg = f"{did}: {name} comm fail on the master"
                if after is not None:
                    msg += f", {after[1]:.0f} s after this program read {after[0]}"
                self.c.store.event("master_link", msg, device_id=did, severity="warning", code="MASTER_LINK_FAIL")
                cut = now - self.guard_period_s
                for d in devs:                                # every fail of the link, the last day only
                    self._edges[d] = [x for x in self._edges.get(d, []) if x >= cut] + [now]
                if after is not None:
                    self._near_misses.setdefault(after[0], []).append(now)
                    self._judge(after[0], name, now)
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

    def _judge(self, did: str, name: str, now: float) -> None:
        """Slow `did` down only when the master lost it right after our read at least guard_count times within
        guard_period_s AND those were at least guard_share of all the times it lost it: a link that drops every
        minute for its own reasons falls after our read now and then by chance, and must not be blamed on us."""
        cut = now - self.guard_period_s
        hits = self._near_misses[did] = [x for x in self._near_misses.get(did, []) if x >= cut]
        edges = self._edges[did] = [x for x in self._edges.get(did, []) if x >= cut]
        if did not in self.back_off or self.c.device_every_s(did) >= self.back_off_s:
            return
        if len(hits) >= self.guard_count and len(hits) >= self.guard_share * max(1, len(edges)):
            self.c.slow_down(did, self.back_off_s,
                             f"{did}: the master lost {name} right after this program read it, {len(hits)} of "
                             f"{len(edges)} times in the last {self.guard_period_s / 3600:g} h; {did} is read only every "
                             f"{self.back_off_s // 60} min from now on")


def endpoint(dev) -> tuple[str, int]:
    c = dev.connection
    return (str(c.get("host")), int(c.get("port", 502)))


def history_since(reader: "LiveReader | None", c, minutes: float = 15.0) -> list:
    if reader is None:
        return []
    cut = (c.now() - timedelta(minutes=minutes)).timestamp() * 1000
    return [p for p in list(reader.history) if p[0] >= cut]
