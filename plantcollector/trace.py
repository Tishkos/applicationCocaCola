"""
Fast, read-only trace of a few devices to plain files - the instrument for event studies: how long the
SAU takes to answer, what the master's comm-fail bits do, what the setpoint registers hold second by second.

Separate from the hourly collector: own process, own files, nothing goes into the database. Connections
stay open for the whole run (one per device host), so the trace costs the SAU one Modbus TCP link.

Files in <out>/ (one set per day, the plant's date in the name, Erbil time; rows keep UTC timestamps with Z):
  <device>_<date>.csv   one row per read: ts, ms (response time), ok_blocks, failed_blocks, then every point
                        (booleans 0/1, a value that could not be read is left empty)
  events_<date>.csv     every change of a boolean point, of a point marked 'edge: true' in its profile, and of
                        a device's reachability: ts, device, point, old, new
  connect_<date>.csv    the connection test: a fresh TCP connection to a host every N seconds, ts, host, ok, ms
  watch_<date>.csv      the stall catcher: a ping of every traced host every ping_s seconds (is the box alive,
                        the master as a control for the PC's own network card) and, for the devices in
                        watch={device: seconds}, one small read on the device's existing connection (does the
                        application answer, and in how many ms): ts, device, kind, ok, ms, detail. The read goes
                        to the setpoint words 2100-2101 when the profile maps them (the second the master's value
                        lands), otherwise to the first mapped register; if the device refuses that address the
                        catcher falls back to the first register by itself.

Each device runs in its own thread with its own connection, so a stalled SAU never delays the master's rows -
the master's comm-fail bits are read on time while the SAU is being slow. Everything is read-only: the trace
uses the same adapter as the collector, which has no write function.
"""

from __future__ import annotations

import csv
import os
import platform
import re
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

from .adapters import build_adapter
from . import sitetime
from .registry import Quality, Registry


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _cell(reading) -> str:
    if reading is None or reading.quality != Quality.GOOD or reading.value is None:
        return ""
    v = reading.value
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".")
    return str(v)


class _DailyCsv:
    """A CSV file that starts a new file (with header) when the plant's date changes (Erbil midnight)."""

    def __init__(self, out_dir: str, stem: str, header: list[str]):
        self.out_dir, self.stem, self.header = out_dir, stem, header
        self._date = None
        self._f = None
        self._w = None

    def write(self, ts: datetime, row: list) -> None:
        d = sitetime.day(ts)
        if d != self._date:
            self.close()
            path = os.path.join(self.out_dir, f"{self.stem}_{d}.csv")
            new = not os.path.exists(path) or os.path.getsize(path) == 0
            self._f = open(path, "a", newline="", encoding="utf-8")
            self._w = csv.writer(self._f)
            if new:
                self._w.writerow(self.header)
            self._date = d
        self._w.writerow(row)
        self._f.flush()

    def close(self) -> None:
        if self._f:
            self._f.close()
            self._f = self._w = None
            self._date = None                               # a late write from a thread reopens in append mode


class Trace:
    def __init__(self, registry: Registry, out_dir: str, every: dict[str, float],
                 connect_test_s: float | None = 60.0, connect_hosts: list[tuple[str, int]] | None = None,
                 log=print, watch: dict[str, float] | None = None, ping_s: float | None = None,
                 ping_also: list[str] | None = None, listener=None):
        os.makedirs(out_dir, exist_ok=True)
        self.listener = listener                             # optional: gets every read, ping and change as it happens
        self.registry, self.out_dir, self.log = registry, out_dir, log
        self.every = {k: float(v) for k, v in every.items()}
        self.watch = {k: float(v) for k, v in (watch or {}).items()}
        self.ping_s = float(ping_s) if ping_s else (1.0 if self.watch else None)
        self.ping_also = [d for d in (ping_also or []) if d not in self.every]   # pinged (and ARP-watched), never read
        if self.ping_also and not self.ping_s:
            self.ping_s = 1.0
        self._paused: set[str] = set()                       # devices whose reads are paused (pings go on)
        self._watch_target: dict[str, tuple] = {}
        self._watch_refused: dict[str, int] = {}
        for dev_id in list(self.every) + list(self.watch) + self.ping_also:
            if dev_id not in registry.devices:
                raise ValueError(f"unknown device '{dev_id}' (config has: {', '.join(registry.devices)})")
        for dev_id in self.watch:
            if dev_id not in self.every:
                raise ValueError(f"watch device '{dev_id}' must also be traced (every)")
        self.adapters = {d: build_adapter(registry.devices[d]) for d in self.every}
        self.files = {d: _DailyCsv(out_dir, d, ["ts", "ms", "wait_ms", "ok_blocks", "failed_blocks"] + [p.id for p in registry.devices[d].points])
                      for d in self.every}
        self._done = threading.Event()
        self.events = _DailyCsv(out_dir, "events", ["ts", "device", "point", "old", "new"])
        self.connect_file = _DailyCsv(out_dir, "connect", ["ts", "host", "ok", "ms", "error"])
        self.watch_file = _DailyCsv(out_dir, "watch", ["ts", "device", "kind", "ok", "ms", "detail"])
        self._lock = threading.RLock()                      # events, _last and the watch file are shared by threads
        self.connect_test_s = connect_test_s
        if connect_hosts is None:
            connect_hosts = []
            for d in self.every:
                c = registry.devices[d].connection
                if "host" in c and (c["host"], int(c.get("port", 502))) not in connect_hosts:
                    connect_hosts.append((c["host"], int(c.get("port", 502))))
        self.connect_hosts = connect_hosts
        self._last: dict[tuple[str, str], object] = {}      # (device, point) -> last value, for the event log
        self.rows = {d: 0 for d in self.every}
        self.failed = {d: 0 for d in self.every}
        self.events_logged = 0
        self.watch_rows = 0
        self.stalls = 0                                      # watch reads slower than 1 s or unanswered
        self._ping_off = False
        self._arp_off = False
        self.arp_s = 10.0

    # ---- one read of one device ----------------------------------------------------------------
    def read_once(self, dev_id: str, ts: datetime | None = None) -> None:
        ad = self.adapters[dev_id]
        dev = self.registry.devices[dev_id]
        try:
            out = ad.read()
        except Exception as exc:                            # an adapter never raises on a bad device, but be safe
            out, err = {}, str(exc)
            self.log(f"[trace] {dev_id}: {err}")
        ok = getattr(ad, "last_ok_blocks", 0)
        failed = getattr(ad, "last_failed_blocks", 0)
        ms = getattr(ad, "last_ms", 0.0)
        wait_ms = getattr(ad, "last_wait_ms", 0.0)
        # the row's time is when the request left, not when the thread started waiting for the connection
        ts = ts or (datetime.now(timezone.utc) - timedelta(milliseconds=ms))
        self.files[dev_id].write(ts, [_iso(ts), ms, wait_ms, ok, failed] + [_cell(out.get(p.id)) for p in dev.points])
        self.rows[dev_id] += 1
        if ok == 0:
            self.failed[dev_id] += 1
        if self.listener is not None:
            try:
                self.listener.on_read(dev_id, ts, out, ms, ok > 0)
            except Exception:
                pass
        self._edge(ts, dev_id, "reachable", ok > 0)
        for p in dev.points:
            r = out.get(p.id)
            if r is None or r.quality != Quality.GOOD:
                continue
            if p.type == "boolean" or p.raw.get("edge"):
                self._edge(ts, dev_id, p.id, r.value)

    def _edge(self, ts: datetime, dev_id: str, point: str, value) -> None:
        key = (dev_id, point)
        with self._lock:
            old = self._last.get(key, None)
            if key in self._last and old == value:
                return
            first = key not in self._last
            if not first:                                   # the first value is a baseline, not a change
                self.events.write(ts, [_iso(ts), dev_id, point, _plain(old), _plain(value)])
                self.events_logged += 1
            self._last[key] = value
        if self.listener is not None and not first:
            try:
                self.listener.on_change(ts, dev_id, point, old, value)
            except Exception:
                pass

    # ---- the stall catcher ----------------------------------------------------------------------
    def pause(self, dev_id: str) -> None:
        """Stop reading this device (poll and watch read); its ping goes on; the connection stays open."""
        self._paused.add(dev_id)

    def resume(self, dev_id: str) -> None:
        self._paused.discard(dev_id)

    def paused(self, dev_id: str) -> bool:
        return dev_id in self._paused

    def watch_read(self, dev_id: str, ts: datetime | None = None) -> None:
        """One small read on the device's own connection: did the application answer, in how many ms."""
        if dev_id in self._paused:
            return
        ad = self.adapters[dev_id]
        fn = getattr(ad, "ping_read", None)
        if fn is None:
            return
        target = self._watch_target.get(dev_id) or getattr(ad, "watch_default", lambda: None)()
        status, ms, values, t_req = fn(target)
        ts = ts or t_req
        ok = 1 if status == "ok" else 0
        if status.startswith("exception") and status.split()[-1] in ("2", "3") and target and target[1] != ad._blocks[0][1]:
            self._watch_refused[dev_id] = self._watch_refused.get(dev_id, 0) + 1
            if self._watch_refused[dev_id] >= 3:             # the device refuses that address: fall back for good
                fb = ad._blocks[0]
                self._watch_target[dev_id] = (fb[0], fb[1], 1)
                self.log(f"[trace] {dev_id} refuses the watch read at {target[1]} ({status}); watching {fb[1]} instead")
        elif target:
            self._watch_target[dev_id] = target
        detail = status if not ok else ";".join(str(v) for v in values)
        with self._lock:
            self.watch_file.write(ts, [_iso(ts), dev_id, "read", ok, ms, detail])
            self.watch_rows += 1
            if not ok or ms >= 1000:
                self.stalls += 1
        self._edge(ts, dev_id, "answers_us", ok == 1)

    def watch_arp(self, dev_id: str, ts: datetime | None = None) -> None:
        """The MAC the PC currently has for the host (arp -a / ip neigh): a change means two boxes answer for
        one address (ARP flux, an IP conflict). Logged every arp_s, a change goes to the event log."""
        ts = ts or datetime.now(timezone.utc)
        host = self.registry.devices[dev_id].connection.get("host")
        if not host or self._arp_off:
            return
        mac = arp_lookup(host)
        if mac is None:
            self._arp_off = True
            self.log("[trace] no arp command on this machine; MAC watch off")
            return
        with self._lock:
            self.watch_file.write(ts, [_iso(ts), dev_id, "arp", 1 if mac else 0, 0, mac or "no entry"])
            self.watch_rows += 1
        if mac:
            self._edge(ts, dev_id, "mac", mac)

    def watch_ping(self, dev_id: str, ts: datetime | None = None) -> None:
        ts = ts or datetime.now(timezone.utc)
        host = self.registry.devices[dev_id].connection.get("host")
        if not host:
            return
        if self._ping_off:
            return
        ok, ms, detail = ping(host)
        if detail.startswith("ping unavailable"):           # no ping on this machine: say so once, stop trying
            self._ping_off = True
            self.log(f"[trace] {detail}; no pings from this machine (reads go on)")
            return
        with self._lock:
            self.watch_file.write(ts, [_iso(ts), dev_id, "ping", ok, ms, detail])
            self.watch_rows += 1
        if self.listener is not None:
            try:
                self.listener.on_ping(dev_id, ts, ok == 1, ms)
            except Exception:
                pass
        self._edge(ts, dev_id, "ping_ok", ok == 1)

    # ---- connection test ------------------------------------------------------------------------
    def connect_test(self, ts: datetime | None = None) -> None:
        ts = ts or datetime.now(timezone.utc)
        for host, port in self.connect_hosts:
            t0 = time.perf_counter()
            try:
                s = socket.create_connection((host, port), timeout=3)
                s.close()
                ok, err = 1, ""
            except OSError as exc:
                ok, err = 0, str(exc)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            self.connect_file.write(ts, [_iso(ts), f"{host}:{port}", ok, ms, err])

    # ---- the loop --------------------------------------------------------------------------------
    def _running(self, end: float | None, stop) -> bool:
        return not stop.is_set() and not self._done.is_set() and (end is None or time.monotonic() < end)

    def _device_loop(self, dev_id: str, end: float | None, stop) -> None:
        period = self.every[dev_id]
        due = time.monotonic()
        while self._running(end, stop):
            if time.monotonic() >= due:
                try:
                    if dev_id not in self._paused:
                        self.read_once(dev_id)
                except Exception as exc:                        # a disk error must not kill the thread silently
                    self.log(f"[trace] {dev_id}: {exc}")
                due = max(due + period, time.monotonic())        # no catching up after a slow read
            self._nap(due, end, stop)

    def _watch_loop(self, dev_id: str, fn, end: float | None, stop, period: float) -> None:
        due = time.monotonic()
        while self._running(end, stop):
            if time.monotonic() >= due:
                try:
                    fn(dev_id)
                except Exception as exc:
                    self.log(f"[trace] {dev_id} watch: {exc}")
                due = max(due + period, time.monotonic())
            self._nap(due, end, stop)

    @staticmethod
    def _nap(due: float, end: float | None, stop) -> None:
        wait = due - time.monotonic()
        if end is not None:
            wait = min(wait, end - time.monotonic())
        if wait > 0:
            stop.wait(min(wait, 0.5))

    def run(self, duration_s: float | None = None, stop=None, status_every_s: float = 60.0) -> None:
        start = time.monotonic()
        end = None if duration_s is None else start + duration_s
        stop = stop or threading.Event()
        next_connect = start if self.connect_test_s else None
        next_status = start + status_every_s
        self.log(f"[trace] {', '.join(f'{d} every {s:g} s' for d, s in self.every.items())}"
                 + (f"; watch read {', '.join(f'{d} every {s:g} s' for d, s in self.watch.items())}" if self.watch else "")
                 + (f"; ping every {self.ping_s:g} s" if self.ping_s else "")
                 + (f" (also {', '.join(self.ping_also)}, ping only, no reads)" if self.ping_also else "")
                 + (f"; connection test every {self.connect_test_s:g} s" if self.connect_test_s else "")
                 + f"; files in {self.out_dir}")
        threads = [threading.Thread(target=self._device_loop, args=(d, end, stop), daemon=True, name=f"trace-{d}") for d in self.every]
        for d, period in self.watch.items():
            threads.append(threading.Thread(target=self._watch_loop, args=(d, self.watch_read, end, stop, period), daemon=True, name=f"watch-{d}"))
        if self.ping_s:
            hosts_done = set()
            for d in list(self.every) + self.ping_also:
                host = self.registry.devices[d].connection.get("host")
                if host and host not in hosts_done:               # one ping per host, the master as a control
                    hosts_done.add(host)
                    threads.append(threading.Thread(target=self._watch_loop, args=(d, self.watch_ping, end, stop, self.ping_s), daemon=True, name=f"ping-{d}"))
                    threads.append(threading.Thread(target=self._watch_loop, args=(d, self.watch_arp, end, stop, self.arp_s), daemon=True, name=f"arp-{d}"))
        for t in threads:
            t.start()
        try:
            while not stop.is_set() and (end is None or time.monotonic() < end):
                now = time.monotonic()
                if next_connect is not None and now >= next_connect:
                    self.connect_test()
                    next_connect += self.connect_test_s
                if now >= next_status:
                    self.log("[trace] " + ", ".join(f"{d}: {self.rows[d]} rows, {self.failed[d]} failed"
                                                    for d in self.every) + f", {self.events_logged} events"
                             + (f", watch {self.watch_rows} rows, {self.stalls} stalls" if self.watch else ""))
                    next_status += status_every_s
                pending = [next_status] + ([next_connect] if next_connect is not None else [])
                if end is not None:
                    pending.append(end)
                stop.wait(max(0.05, min(0.5, min(pending) - time.monotonic())))
        finally:
            self._done.set()                                # tell every thread to leave after its current read
            for t in threads:
                t.join(timeout=15)
            self.close()

    def close(self) -> None:
        for f in self.files.values():
            f.close()
        self.events.close()
        self.connect_file.close()
        self.watch_file.close()
        for ad in self.adapters.values():
            try:
                lock = getattr(ad, "_lock", None)
                if lock is not None:
                    with lock:                              # never close a connection under a read in flight
                        ad.close()
                else:
                    ad.close()
            except Exception:
                pass


def _plain(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return "" if v is None else str(v)


_ICMP_HANDLE = None


def _icmp_windows(host: str, timeout_s: float) -> tuple[int, float, str] | None:
    """ICMP echo through Windows' iphlpapi (no admin rights, no process per second, language independent).
    Returns None when this is not Windows or the call is unavailable, so the caller falls back to ping.exe."""
    global _ICMP_HANDLE
    if not platform.system().lower().startswith("win"):
        return None
    try:
        import ctypes
        import struct as _struct
        iphlpapi = ctypes.windll.iphlpapi
        kernel32 = ctypes.windll.kernel32
        iphlpapi.IcmpCreateFile.restype = ctypes.c_void_p
        iphlpapi.IcmpSendEcho.restype = ctypes.c_ulong
        iphlpapi.IcmpSendEcho.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_ushort,
                                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        if _ICMP_HANDLE is None:
            _ICMP_HANDLE = iphlpapi.IcmpCreateFile()
            if not _ICMP_HANDLE or _ICMP_HANDLE == ctypes.c_void_p(-1).value:
                _ICMP_HANDLE = None
                return None
        addr = _struct.unpack("<L", socket.inet_aton(socket.gethostbyname(host)))[0]
        data = b"plantcollector"
        size = 128 + len(data)
        buf = ctypes.create_string_buffer(size)
        n = iphlpapi.IcmpSendEcho(_ICMP_HANDLE, addr, data, len(data), None, buf, size, int(timeout_s * 1000))
        if n == 0:
            err = kernel32.GetLastError()
            return 0, round(timeout_s * 1000, 1), "no reply" if err in (11010, 11003, 11002) else f"icmp error {err}"
        _a, status, rtt = _struct.unpack_from("<LLL", buf.raw, 0)
        if status == 0:
            return 1, float(rtt), ""
        return 0, float(rtt), "no reply" if status in (11010, 11003, 11002) else f"icmp status {status}"
    except Exception:                                       # any surprise in the API: let ping.exe do it
        return None


def ping(host: str, timeout_s: float = 1.0) -> tuple[int, float, str]:
    """One ICMP echo. Windows: iphlpapi, then ping.exe; elsewhere the system ping, then a datagram socket.
    Returns (ok, ms, detail). ping output is parsed as bytes: the only language-independent token is 'TTL='."""
    r = _icmp_windows(host, timeout_s)
    if r is not None:
        return r
    win = platform.system().lower().startswith("win")
    cmd = ["ping", "-n", "1", "-w", str(int(timeout_s * 1000)), host] if win else ["ping", "-c", "1", "-W", str(max(1, int(timeout_s))), host]
    t0 = time.perf_counter()
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout_s + 2).stdout.lower()
    except FileNotFoundError:
        return _icmp_echo(host, timeout_s)                  # no ping binary (a bare container): try a datagram ICMP socket
    except Exception as exc:
        return 0, round((time.perf_counter() - t0) * 1000, 1), f"ping failed: {exc}"
    if b"ttl=" not in out:                                  # Windows prints "unreachable" replies with exit code 0
        return 0, round((time.perf_counter() - t0) * 1000, 1), "no reply"
    m = re.search(rb"[=<]\s*(\d+(?:[.,]\d+)?)\s*ms", out)
    return 1, float(m.group(1).replace(b",", b".")) if m else round((time.perf_counter() - t0) * 1000, 1), ""


_MAC_RE = re.compile(rb"((?:[0-9a-f]{2}[-:]){5}[0-9a-f]{2})")


def arp_lookup(host: str) -> str | None:
    """The MAC in the PC's ARP table for host: '' when there is no entry, None when there is no arp command."""
    win = platform.system().lower().startswith("win")
    cmd = ["arp", "-a", host] if win else ["ip", "neigh", "show", host]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=5).stdout.lower()
    except FileNotFoundError:
        if win:
            return None
        try:
            out = subprocess.run(["arp", "-n", host], capture_output=True, timeout=5).stdout.lower()
        except Exception:
            return None
    except Exception:
        return ""
    macs = [m.decode().replace("-", ":") for m in _MAC_RE.findall(out)]
    macs = [m for m in macs if m not in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00")]
    return macs[0] if macs else ""


def _icmp_echo(host: str, timeout_s: float) -> tuple[int, float, str]:
    """ICMP echo through an unprivileged datagram socket (Linux with ping_group_range open). No raw socket."""
    import struct as _struct
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except (OSError, AttributeError) as exc:
        return 0, 0.0, f"ping unavailable: {exc}"
    try:
        sock.settimeout(timeout_s)
        payload = _struct.pack(">BBHHH", 8, 0, 0, 0, 1) + b"plantcollector"
        csum = 0
        data = payload if len(payload) % 2 == 0 else payload + b"\0"
        for i in range(0, len(data), 2):
            csum += (data[i] << 8) + data[i + 1]
        csum = (csum >> 16) + (csum & 0xFFFF)
        csum = (~(csum + (csum >> 16))) & 0xFFFF
        payload = payload[:2] + _struct.pack(">H", csum) + payload[4:]
        t0 = time.perf_counter()
        sock.sendto(payload, (host, 0))
        sock.recvfrom(1024)
        return 1, round((time.perf_counter() - t0) * 1000, 2), ""
    except socket.timeout:
        return 0, round(timeout_s * 1000, 1), "no reply"
    except OSError as exc:
        return 0, 0.0, f"ping unavailable: {exc}"
    finally:
        sock.close()


def probe(targets: list[tuple[str, int, int]], address: int = 0, timeout: float = 3.0) -> list[dict]:
    """For each (host, port, unit): open a TCP connection, send one read of one holding register, report.
    An exception reply (illegal address, illegal function) still proves a Modbus server answers."""
    from pymodbus.client import ModbusTcpClient
    rows = []
    for host, port, unit in targets:
        row = {"host": host, "port": port, "unit": unit, "tcp": "", "modbus": "", "ms": None}
        t0 = time.perf_counter()
        try:
            s = socket.create_connection((host, port), timeout=timeout)
            s.close()
            row["tcp"] = "open"
        except OSError as exc:
            row["tcp"] = "timeout" if isinstance(exc, socket.timeout) else "closed"
            row["modbus"] = f"no connection ({exc})"
            row["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            rows.append(row)
            continue
        c = ModbusTcpClient(host=host, port=port, timeout=timeout)
        try:
            c.connect()
            t1 = time.perf_counter()
            rr = c.read_holding_registers(address=address, count=1, device_id=unit)
            row["ms"] = round((time.perf_counter() - t1) * 1000, 1)
            if rr is None:
                row["modbus"] = "no reply"
            elif rr.isError():
                row["modbus"] = f"answers (exception: {rr})"
            else:
                row["modbus"] = f"answers, register {address} = {rr.registers[0]}"
        except Exception as exc:
            row["modbus"] = f"no reply ({exc})"
            row["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        finally:
            try:
                c.close()
            except Exception:
                pass
        rows.append(row)
    return rows
