"""A fake plant for the tests: Modbus TCP servers on 127.0.0.1 that answer from images the test can change,
and count every request and every connection (to check how much load the collector puts on a device)."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

from pymodbus.simulator import DataType, SimData, SimDevice


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeHost:
    """One IP:port with one or more unit ids, each a 2200-register image (registers and coils share it)."""

    SIZE = 2200

    def __init__(self, units: list[int], port: int | None = None):
        self.port = port or free_port()
        self.images = {u: [0] * self.SIZE for u in units}
        self.requests = {u: 0 for u in units}
        self.connects = 0
        self.open_now = 0
        self.max_open = 0
        self.silent = False                       # True: accept requests but never answer
        self.refuse: dict[tuple[int, int], int] = {}   # (unit, start address) -> Modbus exception code to answer
        self.flap: set[tuple[int, int]] = set()        # (unit, start address) answered "busy" every other request
        self._flip = 0
        self._loop = None
        self._server = None

    def set(self, unit: int, address: int, *values: int) -> None:
        for i, v in enumerate(values):
            self.images[unit][address + i] = int(v) & 0xFFFF

    def set32(self, unit: int, address: int, value: int) -> None:
        v = int(value) & 0xFFFFFFFF
        self.set(unit, address, v >> 16, v & 0xFFFF)

    def total_requests(self) -> int:
        return sum(self.requests.values())

    def _action(self, unit):
        async def action(function_code, start_address, address, count, current_registers, set_values):
            self.requests[unit] += 1
            if self.silent:
                await asyncio.sleep(30)
            if set_values is not None:
                from pymodbus.constants import ExcCodes
                return ExcCodes.ILLEGAL_FUNCTION
            from pymodbus.constants import ExcCodes
            code = self.refuse.get((unit, address))
            if code:
                return ExcCodes(code)
            if (unit, address) in self.flap:
                self._flip += 1
                if self._flip % 2:
                    return ExcCodes.DEVICE_BUSY
            img = self.images[unit]
            current_registers[address - start_address:address - start_address + count] = img[address:address + count]
            return None
        return action

    def _on_connect(self, connected: bool) -> None:
        if connected:
            self.connects += 1
            self.open_now += 1
            self.max_open = max(self.max_open, self.open_now)
        else:
            self.open_now = max(0, self.open_now - 1)

    def start(self) -> "FakeHost":
        from pymodbus.server import ModbusTcpServer
        devices = [SimDevice(id=u, simdata=[SimData(address=0, count=self.SIZE, values=0, datatype=DataType.REGISTERS)],
                             action=self._action(u)) for u in self.images]
        ready = threading.Event()

        async def serve():
            self._server = ModbusTcpServer(devices, address=("127.0.0.1", self.port), trace_connect=self._on_connect)
            ready.set()
            await self._server.serve_forever()

        self._loop = asyncio.new_event_loop()
        threading.Thread(target=lambda: self._loop.run_until_complete(serve()), daemon=True).start()
        ready.wait(5)
        time.sleep(0.4)
        return self

    def stop(self) -> None:
        if self._server and self._loop:
            fut = asyncio.run_coroutine_threadsafe(self._server.shutdown(), self._loop)
            try:
                fut.result(5)
            except Exception:
                pass
