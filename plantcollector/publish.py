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

What a bad tag shows in its value registers is `bad_value` in the config: the last good
value (default), zero, or NaN. The quality register tells the truth either way.
"""

from __future__ import annotations

import asyncio
import logging
import math
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

MAP_VERSION = 1
VALUES_START = 100
QUALITY_START = 1000
BLOCK_SIZE = 2000
HEADER = {"last_read_time": 0, "minutes_since_read": 2, "good_tags": 3, "total_tags": 4, "map_version": 5, "heartbeat": 6}


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
        """Rebuild the register image from the collector's latest readings."""
        img = [0] * BLOCK_SIZE
        now = self.c.now()
        last = self.c.last_poll_at
        good = 0
        for m in self.map:
            r = self.c.store.latest(m["device"]).get(m["point"]) if m["device"] in self.c.registry.devices else None
            q = r.quality if r is not None else Quality.BAD
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
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
                "reads": self.reads, "rejected_writes": self.rejected_writes, "bad_value": self.bad_value}
