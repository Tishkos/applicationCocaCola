"""
Modbus TCP server for the DT Turkey platform.

Publishes the tags of profiles/tag_catalog.yaml as holding registers (FC03; FC04 mirrors
them), refreshed from the collector's latest readings after every site read. The register
block is read-only: a write request is answered with a Modbus exception and changes nothing.

Register map (0-based protocol addresses, unit id from the config):

  0-1     uint32   unix time (UTC seconds) of the last site read
  2       uint16   minutes since the last site read
  3       uint16   tags with quality good
  4       uint16   total tags
  5       uint16   register map version
  6       uint16   heartbeat, +1 at every site read

  100...  values, in catalogue order:
            float   float32, 2 registers, high word first (ABCD), engineering units
            boolean uint16, 1 register, 0 / 1
  1000+i  uint16   quality of tag i: 1 good, 2 stale, 0 bad / no data yet

  Analyzer block (map version 2): one fixed slot per analyzer number, so a meter added or
  removed on the dashboard never moves another one's registers.
  7       uint16   analyzers published (slots in use)
  8       uint16   analyzer slots (48)
  2000+32*(n-1)+o   the values of analyzer n (n = 1..48), o = the point's offset (ANALYZER_POINTS):
                    float32 (2 registers, high word first), except the energy counter, uint32 kWh
  4000+16*(n-1)+j   quality of point j of analyzer n; every register of an empty slot is 0

What a bad tag shows in its value registers is `bad_value` in the config: the last good
value (default), zero, or NaN. The quality register tells the truth either way.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import struct
import threading
import time
from datetime import datetime, timezone

from pymodbus.constants import ExcCodes
from pymodbus.simulator import DataType, SimData, SimDevice

from .core import Collector
from .registry import Quality
from .views import load_tag_catalog

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)

MAP_VERSION = 2
VALUES_START = 100
QUALITY_START = 1000
BLOCK_SIZE = 5000
HEADER = {"last_read_time": 0, "minutes_since_read": 2, "good_tags": 3, "total_tags": 4, "map_version": 5, "heartbeat": 6,
          "analyzers_published": 7, "analyzer_slots": 8}

# the analyzer block: a fixed slot per analyzer number (analyzer-7 -> slot 7, whether or not 3..6 exist)
AN_VALUES_START = 2000
AN_SLOT_REGISTERS = 32
AN_QUALITY_START = 4000
AN_SLOT_QUALITY = 16
AN_SLOTS = 48
# (tag, point id, type, unit, offset in the slot)
ANALYZER_POINTS = [
    ("P_TOTAL_KW", "p_total_kw", "float32", "kW", 0), ("Q_TOTAL_KVAR", "q_total_kvar", "float32", "kvar", 2),
    ("S_TOTAL_KVA", "s_total_kva", "float32", "kVA", 4), ("PF_TOTAL", "pf_total", "float32", "", 6),
    ("I_L1_A", "i_l1_a", "float32", "A", 8), ("I_L2_A", "i_l2_a", "float32", "A", 10), ("I_L3_A", "i_l3_a", "float32", "A", 12),
    ("I_N_A", "i_n_a", "float32", "A", 14), ("V_L1L2_V", "v_l1l2_v", "float32", "V", 16), ("V_L2L3_V", "v_l2l3_v", "float32", "V", 18),
    ("V_L3L1_V", "v_l3l1_v", "float32", "V", 20), ("V_L1N_V", "v_l1n_v", "float32", "V", 22), ("V_L2N_V", "v_l2n_v", "float32", "V", 24),
    ("V_L3N_V", "v_l3n_v", "float32", "V", 26), ("F_HZ", "f_hz", "float32", "Hz", 28), ("E_IN_KWH", "e_in_kwh", "uint32", "kWh", 30),
]
assert len(ANALYZER_POINTS) <= AN_SLOT_QUALITY and max(o for *_, o in ANALYZER_POINTS) + 2 <= AN_SLOT_REGISTERS
assert AN_VALUES_START + AN_SLOTS * AN_SLOT_REGISTERS <= AN_QUALITY_START and AN_QUALITY_START + AN_SLOTS * AN_SLOT_QUALITY <= BLOCK_SIZE


def build_map(catalog: list[dict]) -> list[dict]:
    """Assign every catalogue tag its registers. Deterministic: catalogue order decides."""
    out, addr = [], VALUES_START
    for i, t in enumerate(catalog):
        is_bool = t.get("type") == "boolean"
        out.append({"index": i, "name": t["name"], "device": t["device"], "point": t["point"], "unit": t.get("unit", ""),
                    "type": "boolean" if is_bool else "float32", "address": addr, "registers": 1 if is_bool else 2,
                    "quality_address": QUALITY_START + i})
        addr += 1 if is_bool else 2
    if addr > QUALITY_START:
        raise ValueError(f"too many tags for the value block ({addr} > {QUALITY_START})")
    return out


def analyzer_slot(did: str) -> int | None:
    """analyzer-7 -> 7; None for anything else or a number past the last slot."""
    m = re.match(r"^analyzer-(\d+)$", did or "")
    n = int(m.group(1)) if m else 0
    return n if 1 <= n <= AN_SLOTS else None


def analyzer_map(c: Collector) -> list[dict]:
    """The analyzer block's tags for the analyzers the collector reads now (the plant's and those added on the
    dashboard, minus the removed ones), each at its own slot. Same shape as build_map()'s entries, plus the
    analyzer's name and the register's own name from the Analyzers tab."""
    names = getattr(c, "register_names", {}) or {}
    removed = getattr(c, "removed", set())
    out = []
    for d in sorted(c.registry.by_kind("analyzer"), key=lambda d: analyzer_slot(d.id) or 0):
        n = analyzer_slot(d.id)
        if n is None or d.id in removed:
            continue
        pts = {p.id for p in d.points}
        for j, (tag, pid, typ, unit, off) in enumerate(ANALYZER_POINTS):
            if pid not in pts:
                continue
            out.append({"slot": n, "name": f"ANALYZER_{n}.{tag}", "device": d.id, "point": pid, "unit": unit, "type": typ,
                        "address": AN_VALUES_START + AN_SLOT_REGISTERS * (n - 1) + off, "registers": 2,
                        "quality_address": AN_QUALITY_START + AN_SLOT_QUALITY * (n - 1) + j,
                        "analyzer_name": d.name if d.name and d.name != d.id else "", "register_name": names.get(d.id, {}).get(pid, "")})
    return out


def _u32(v: float) -> tuple[int, int]:
    x = max(0, min(0xFFFFFFFF, int(round(v))))
    return (x >> 16) & 0xFFFF, x & 0xFFFF


def turkey_quality(c: Collector, r, now: datetime | None = None) -> Quality:
    """Quality as docs/TURKEY_MODBUS.md defines it: good, stale when older than two read intervals, bad."""
    if r is None:
        return Quality.BAD
    if r.quality == Quality.GOOD:
        now = now or c.now()
        if (now - r.source_ts).total_seconds() > 2 * float(c.poll_interval_s) + 60:
            return Quality.STALE
    return r.quality


def _f32(v: float) -> tuple[int, int]:
    hi, lo = struct.unpack(">HH", struct.pack(">f", float(v)))
    return hi, lo


class ModbusPublisher:
    def __init__(self, collector: Collector, *, bind: str = "0.0.0.0", port: int = 502, unit_id: int = 1,
                 bad_value: str = "last", catalog: list[dict] | None = None):
        self.c = collector
        self.bind, self.port, self.unit_id = bind, int(port), int(unit_id)
        self.bad_value = bad_value if bad_value in ("last", "zero", "nan") else "last"
        self.catalog = catalog if catalog is not None else load_tag_catalog()
        self.map = build_map(self.catalog)
        self.image = [0] * BLOCK_SIZE
        self._last_good: dict[int, float] = {}
        self._last_good_an: dict[str, float] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._thread: threading.Thread | None = None
        self.started_at: datetime | None = None
        self.last_refresh: datetime | None = None
        self.reads = 0
        self.rejected_writes = 0
        self.heartbeat = 0
        self._hb_poll: datetime | None = None    # the site read the heartbeat last counted
        self.refresh()

    @classmethod
    def from_config(cls, collector: Collector) -> "ModbusPublisher | None":
        cfg = (collector.registry.extra.get("publish") or {}).get("modbus_tcp") or {}
        if not cfg.get("enabled"):
            return None
        return cls(collector, bind=str(cfg.get("bind", "0.0.0.0")), port=int(cfg.get("port", 502)),
                   unit_id=int(cfg.get("unit_id", 1)), bad_value=str(cfg.get("bad_value", "last")))

    # --- the register image ---------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the register image from the values of the last site read (never from the live reader's
        newer values: the Turkey feed changes once per site read, every 10 minutes)."""
        img = [0] * BLOCK_SIZE
        now = self.c.now()
        last = self.c.last_poll_at
        good = 0
        latest = {d: self.c.turkey_latest(d) for d in {m["device"] for m in self.map} if d in self.c.registry.devices}
        for m in self.map:
            r = latest.get(m["device"], {}).get(m["point"]) if m["device"] in latest else None
            q = turkey_quality(self.c, r, now)
            val = r.value if r is not None and q != Quality.BAD else None
            if val is not None and isinstance(val, (int, float, bool)):
                self._last_good[m["index"]] = float(val)
                if q == Quality.GOOD:
                    good += 1
            else:
                val = {"last": self._last_good.get(m["index"], 0.0), "zero": 0.0, "nan": math.nan}[self.bad_value] if m["type"] == "float32" \
                    else self._last_good.get(m["index"], 0.0) if self.bad_value == "last" else 0.0
            if m["type"] == "boolean":
                img[m["address"]] = 1 if val else 0
            else:
                img[m["address"]], img[m["address"] + 1] = _f32(val)
            img[m["quality_address"]] = {Quality.GOOD: 1, Quality.STALE: 2, Quality.BAD: 0}[q] if r is not None else 0
        ts = int(last.timestamp()) if last else 0
        img[0], img[1] = (ts >> 16) & 0xFFFF, ts & 0xFFFF
        img[2] = min(65535, max(0, int((now - last).total_seconds() // 60))) if last else 65535
        img[3], img[4], img[5] = good, len(self.map), MAP_VERSION
        # the analyzer block: the meter reader's latest values (the meters are read every 15 s anyway; nothing is
        # asked of a device for the feed), a fixed slot per analyzer number
        amap = analyzer_map(self.c)
        for m in amap:
            r = self.c.store.latest(m["device"]).get(m["point"])
            q = turkey_quality(self.c, r, now)
            val = r.value if r is not None and q != Quality.BAD and isinstance(r.value, (int, float, bool)) else None
            key = f"{m['device']}.{m['point']}"
            if val is not None:
                self._last_good_an[key] = float(val)
            else:
                val = {"last": self._last_good_an.get(key, 0.0), "zero": 0.0, "nan": math.nan}[self.bad_value] if m["type"] == "float32" \
                    else self._last_good_an.get(key, 0.0) if self.bad_value == "last" else 0.0
            a = m["address"]
            img[a], img[a + 1] = _u32(val if val == val else 0.0) if m["type"] == "uint32" else _f32(val)
            img[m["quality_address"]] = {Quality.GOOD: 1, Quality.STALE: 2, Quality.BAD: 0}[q] if r is not None else 0
        img[7], img[8] = len({m["slot"] for m in amap}), AN_SLOTS
        if last is not None and last != self._hb_poll:   # +1 per site read, not per refresh
            self._hb_poll = last
            self.heartbeat = (self.heartbeat + 1) & 0xFFFF
        img[6] = self.heartbeat
        img = [v & 0xFFFF for v in img]           # a value outside 0..65535 would make the server answer nothing
        with self._lock:
            self.image = img
        self.last_refresh = datetime.now(timezone.utc)

    async def _action(self, function_code, start_address, address, count, current_registers, set_values):
        if set_values is not None or function_code not in (3, 4):
            self.rejected_writes += 1
            return ExcCodes.ILLEGAL_FUNCTION
        with self._lock:
            current_registers[address - start_address:address - start_address + count] = self.image[address:address + count]
        self.reads += 1
        return None

    # --- the server ------------------------------------------------------------------------

    def start(self) -> None:
        from pymodbus.server import ModbusTcpServer

        device = SimDevice(id=self.unit_id, simdata=[SimData(address=0, count=BLOCK_SIZE, values=0, datatype=DataType.REGISTERS, readonly=True)],
                           action=self._action)
        others = SimDevice(id=0, simdata=[SimData(address=0, count=1, datatype=DataType.INVALID)])   # any other unit id: exception, not silence
        ready = threading.Event()
        failure: list[BaseException] = []

        async def serve():
            try:
                self._server = ModbusTcpServer([device, others], address=(self.bind, self.port))
                ready.set()
                await self._server.serve_forever()
            except BaseException as exc:          # noqa: BLE001 - reported to the starter
                failure.append(exc)
                ready.set()

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=lambda: self._loop.run_until_complete(serve()), daemon=True, name="modbus-publish")
        self._thread.start()
        ready.wait(10)
        time.sleep(0.3)
        if failure:
            raise RuntimeError(f"Modbus publisher could not start on {self.bind}:{self.port}: {failure[0]}")
        self.started_at = datetime.now(timezone.utc)
        self.c.after_poll.append(self.refresh)
        # a site read that finished while the server was starting would otherwise be missed until the next one
        self.refresh()

        def tick():                                # keeps 'minutes since the last read' and the stale flags current
            while self._thread is not None and self._thread.is_alive():
                time.sleep(60)
                try:
                    self.refresh()
                except Exception:
                    pass
        threading.Thread(target=tick, daemon=True, name="modbus-publish-tick").start()

    def stop(self) -> None:
        if self.refresh in self.c.after_poll:
            self.c.after_poll.remove(self.refresh)
        if self._server and self._loop:
            fut = asyncio.run_coroutine_threadsafe(self._server.shutdown(), self._loop)
            try:
                fut.result(5)
            except Exception:
                pass

    def status(self) -> dict:
        return {"enabled": True, "running": self.started_at is not None, "bind": self.bind, "port": self.port,
                "unit_id": self.unit_id, "tags": len(self.map), "map_version": MAP_VERSION,
                "analyzers": len({m["slot"] for m in analyzer_map(self.c)}), "analyzer_slots": AN_SLOTS,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
                "reads": self.reads, "rejected_writes": self.rejected_writes, "bad_value": self.bad_value}
