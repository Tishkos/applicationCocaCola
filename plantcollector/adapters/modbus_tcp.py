"""
Modbus TCP reader. Read-only: only read_holding_registers / read_input_registers exist here.

Points are grouped into contiguous blocks (max 100 registers) so one device costs a
handful of requests per poll, not one per point. That is what keeps the load on the
SAU and the InteliNeo small.

Connections are shared per host:port and closed after every poll (close_all): the SAU
gateway allows only 5 Modbus TCP links in total, and the InteliNeo already uses some of
them. Ten inverters behind one SAU therefore cost one connection, and none between polls.

point.raw: {address, kind: uint16|int16|uint32|int32|float32, scale, fn: holding|input|coil|discrete,
            word_order: big|little,
            bit: <n>        one bit of a status word (0 = least significant)
            mask: <int>     true when any of these bits is set
            invert: true    negate a bit/mask boolean
            derived: comm_error              true when the device answered nothing
            derived: sum, of: [point, ...]   sum of sibling points (all must be good)}
connection.address_offset: added to every address before the request (0 by default; -1 if a
            device's table turns out to be 1-based).
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from datetime import datetime, timezone

from pymodbus.client import ModbusTcpClient

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)     # we report failures ourselves, per point
log = logging.getLogger(__name__)

from ..registry import Device, PointDefinition, Quality
from ..store import Reading
from . import Adapter, reading

MAX_BLOCK = 100

_POOL: dict[tuple[str, int], ModbusTcpClient] = {}
_POOL_LOCK = threading.RLock()
_CONN_LOCKS: dict[tuple[str, int], threading.RLock] = {}     # one request at a time per connection, not per process


def shared_client(host: str, port: int, timeout: float) -> ModbusTcpClient:
    with _POOL_LOCK:
        key = (host, port)
        if key not in _POOL:
            # retries=0: one request, one answer or one timeout. The library's default re-sends a timed-out
            # request three times, which quadruples our load on a stalled gateway at the worst moment and
            # hides the stall's real length.
            _POOL[key] = ModbusTcpClient(host=host, port=port, timeout=timeout, retries=0)
            _CONN_LOCKS[key] = threading.RLock()
        return _POOL[key]


def connection_lock(host: str, port: int) -> threading.RLock:
    with _POOL_LOCK:
        return _CONN_LOCKS.setdefault((host, port), threading.RLock())


def close_all() -> None:
    """Drop every connection; called by the collector after each poll."""
    with _POOL_LOCK:
        for c in _POOL.values():
            try:
                c.close()
            except Exception:
                pass


def _decode(regs: list[int], kind: str, scale: float, word_order: str) -> float | int:
    if kind in ("uint16", "int16"):
        raw = regs[0]
        if kind == "int16" and raw >= 0x8000:
            raw -= 0x10000
        return raw * scale
    hi, lo = (regs[0], regs[1]) if word_order == "big" else (regs[1], regs[0])
    packed = struct.pack(">HH", hi, lo)
    fmt = {"uint32": ">I", "int32": ">i", "float32": ">f"}[kind]
    return struct.unpack(fmt, packed)[0] * scale


def _size(kind: str) -> int:
    return 2 if "32" in kind else 1


class ModbusTcpAdapter(Adapter):
    name = "modbus_tcp"

    def __init__(self, device: Device):
        super().__init__(device)
        c = device.connection
        self.unit_id = int(c.get("unit_id", 1))
        self.address_offset = int(c.get("address_offset", 0))
        self._client = shared_client(c["host"], int(c.get("port", 502)), float(c.get("timeout", 3)))
        self._lock = connection_lock(c["host"], int(c.get("port", 502)))
        self._blocks = self._plan_blocks(device.points)
        self.last_ok_blocks = 0                 # statistics of the last read(), for the trace
        self.last_failed_blocks = 0
        self.last_ms = 0.0
        self.last_wait_ms = 0.0

    @staticmethod
    def _plan_blocks(points: list[PointDefinition]) -> list[tuple[str, int, int, list[PointDefinition]]]:
        """Group mapped points by function code into contiguous blocks."""
        mapped = [p for p in points if "address" in p.raw]
        blocks: list[tuple[str, int, int, list[PointDefinition]]] = []
        for fn in ("holding", "input", "coil", "discrete"):
            pts = sorted((p for p in mapped if p.raw.get("fn", "holding") == fn), key=lambda p: int(p.raw["address"]))
            cur: list[PointDefinition] = []
            start = end = None
            for p in pts:
                a = int(p.raw["address"])
                n = _size(p.raw.get("kind", "uint16"))
                if cur and (a + n - start > MAX_BLOCK):
                    blocks.append((fn, start, end - start, cur))
                    cur, start, end = [], None, None
                if start is None:
                    start = a
                cur.append(p)
                end = max(end or 0, a + n)
            if cur:
                blocks.append((fn, start, end - start, cur))
        return blocks

    @staticmethod
    def _split_runs(fn: str, pts: list[PointDefinition]) -> list[tuple[str, int, int, list[PointDefinition]]]:
        """The points of one block as runs of registers with no gap between them (overlaps join a run)."""
        runs: list[tuple[str, int, int, list[PointDefinition]]] = []
        cur: list[PointDefinition] = []
        start = end = None
        for p in sorted(pts, key=lambda p: int(p.raw["address"])):
            a = int(p.raw["address"])
            n = _size(p.raw.get("kind", "uint16"))
            if cur and a > end:
                runs.append((fn, start, end - start, cur))
                cur, start, end = [], None, None
            if start is None:
                start = a
            cur.append(p)
            end = max(end or 0, a + n)
        if cur:
            runs.append((fn, start, end - start, cur))
        return runs

    def connect(self) -> bool:
        return self._client.connect()

    def close(self) -> None:
        with self._lock:
            self._client.close()

    def read(self) -> dict[str, Reading]:
        t0 = time.perf_counter()
        with self._lock:                    # one request at a time on this connection; other hosts are not held up
            self.last_wait_ms = round((time.perf_counter() - t0) * 1000, 1)   # time spent behind another request
            return self._read()

    def watch_default(self) -> tuple[str, int, int] | None:
        """Where the stall catcher reads by default: the setpoint words at 2100-2101 when the profile maps them
        (shows the second the master's value lands), otherwise the first mapped register."""
        if not self._blocks:
            return None
        for fn, start, count, _pts in self._blocks:
            if fn == "holding" and start <= 2100 < start + count:
                return "holding", 2100, 2
        fn, start, _count, _pts = self._blocks[0]
        return fn, start, 1

    def ping_read(self, target: tuple[str, int, int] | None = None) -> tuple[str, float, list[int] | None, datetime]:
        """One small read on the shared connection: the stall catcher. target = (fn, address, count).
        Returns (status, ms, values, time of the request): status is 'ok', 'exception <n>', 'timeout' or
        'error <text>'. The request time is taken inside the connection lock, so a row never carries the time
        the thread started waiting for the lock."""
        target = target or self.watch_default()
        if target is None:
            return "error no register mapping", 0.0, None, datetime.now(timezone.utc)
        fn, address, count = target
        call = {"input": self._client.read_input_registers, "coil": self._client.read_coils,
                "discrete": self._client.read_discrete_inputs}.get(fn, self._client.read_holding_registers)
        with self._lock:
            t_req = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            try:
                if not self._client.connected:
                    self._client.connect()
                rr = call(address=address + self.address_offset, count=count, device_id=self.unit_id)
                ms = round((time.perf_counter() - t0) * 1000, 1)
                if rr is None:
                    return "timeout", ms, None, t_req
                if rr.isError():
                    code = getattr(rr, "exception_code", None)
                    return f"exception {code if code is not None else rr}", ms, None, t_req
                regs = rr.bits[:count] if fn in ("coil", "discrete") else rr.registers[:count]
                return "ok", ms, [int(v) for v in regs], t_req
            except Exception as exc:
                ms = round((time.perf_counter() - t0) * 1000, 1)
                text = str(exc).lower()
                return ("timeout" if "timeout" in text or "timed out" in text or "no response" in text else f"error {exc}"), ms, None, t_req

    def _read(self) -> dict[str, Reading]:
        t0 = time.perf_counter()
        try:
            return self._read_blocks()
        finally:
            self.last_ms = round((time.perf_counter() - t0) * 1000, 1)

    def _read_blocks(self) -> dict[str, Reading]:
        out: dict[str, Reading] = {}
        now = datetime.now(timezone.utc)
        derived = []
        for p in self.device.points:
            if "address" not in p.raw:
                if p.raw.get("derived") in ("comm_error", "sum"):
                    derived.append(p)
                else:
                    out[p.id] = reading(p, None, Quality.BAD, "no register mapping", now)
        ok_blocks = failed_blocks = 0
        queue = list(self._blocks)
        while queue:
            block = queue.pop(0)
            fn, start, count, pts = block
            call = {"input": self._client.read_input_registers, "coil": self._client.read_coils,
                    "discrete": self._client.read_discrete_inputs}.get(fn, self._client.read_holding_registers)
            try:
                if not self._client.connected:
                    self._client.connect()
                rr = call(address=start + self.address_offset, count=count, device_id=self.unit_id)
                if rr is None or rr.isError():
                    code = getattr(rr, "exception_code", None)
                    runs = self._split_runs(fn, pts)
                    if code in (2, 3, 4) and len(runs) > 1:
                        # the device refuses a block that spans registers it does not have (ComAp answers 4 when
                        # a request crosses "Not Used" registers): read the contiguous runs instead, from now on
                        i = self._blocks.index(block) if block in self._blocks else len(self._blocks)
                        self._blocks[i:i + 1] = runs
                        queue[0:0] = runs
                        log.info("%s: block %d+%d refused (exception %s); reading it as %d contiguous blocks from now on",
                                 self.device.id, start, count, code, len(runs))
                        continue
                    raise RuntimeError(str(rr))
                regs = [int(b) for b in rr.bits[:count]] if fn in ("coil", "discrete") else rr.registers   # a coil is a 0/1 "register"
                ok_blocks += 1
            except Exception as exc:
                failed_blocks += 1
                for p in pts:
                    out[p.id] = reading(p, None, Quality.BAD, f"modbus: {exc}", now)
                continue
            for p in pts:
                raw = p.raw
                off = int(raw["address"]) - start
                kind = raw.get("kind", "uint16")
                try:
                    chunk = regs[off:off + _size(kind)]
                    if "bit" in raw:
                        val = bool((int(chunk[0]) >> int(raw["bit"])) & 1)
                    elif "mask" in raw:
                        val = bool(int(chunk[0]) & int(raw["mask"]))
                    else:
                        val = _decode(chunk, kind, float(raw.get("scale", 1.0)), raw.get("word_order", "big"))
                        if p.type == "boolean":
                            val = bool(val)
                    if raw.get("invert") and isinstance(val, bool):
                        val = not val
                    out[p.id] = reading(p, val, ts=now)
                except Exception as exc:
                    out[p.id] = reading(p, None, Quality.BAD, f"decode: {exc}", now)
        self.last_ok_blocks, self.last_failed_blocks = ok_blocks, failed_blocks
        for p in derived:
            if p.raw.get("derived") == "comm_error":   # the device answered nothing this poll
                if not self._blocks:                    # nothing mapped yet: nothing was asked, so unknown
                    out[p.id] = reading(p, None, Quality.BAD, "no register mapping", now)
                else:
                    out[p.id] = reading(p, failed_blocks > 0 and ok_blocks == 0, ts=now)
            else:                                       # sum of sibling points, good only when all are good
                parts = [out.get(pid) for pid in p.raw.get("of", [])]
                if parts and all(r is not None and r.quality == Quality.GOOD and r.value is not None for r in parts):
                    out[p.id] = reading(p, round(sum(float(r.value) for r in parts), 3), ts=now)
                else:
                    missing = [pid for pid, r in zip(p.raw.get("of", []), parts) if r is None or r.quality != Quality.GOOD]
                    out[p.id] = reading(p, None, Quality.BAD, f"sum needs {', '.join(missing) or 'of: [...]'}", now)
        return out
