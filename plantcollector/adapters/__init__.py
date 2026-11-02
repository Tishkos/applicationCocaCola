"""Adapters read devices. There is no write anywhere in this package."""

from __future__ import annotations

from datetime import datetime, timezone

from ..registry import Device, PointDefinition, Quality
from ..store import Reading


class Adapter:
    name = "base"

    def __init__(self, device: Device):
        self.device = device

    def connect(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def read(self) -> dict[str, Reading]:
        raise NotImplementedError


def reading(p: PointDefinition, value, quality: Quality = Quality.GOOD, error: str | None = None, ts: datetime | None = None) -> Reading:
    return Reading(point_id=p.id, value=value, unit=p.unit, source_ts=ts or datetime.now(timezone.utc), quality=quality, error=error)


def build_adapter(device: Device, *, plant=None) -> Adapter:
    if device.adapter == "simulator":
        from .simulator import SimulatorAdapter
        if plant is None:
            raise ValueError("simulator adapter needs a SimPlant")
        return SimulatorAdapter(device, plant)
    if device.adapter == "modbus_tcp":
        from .modbus_tcp import ModbusTcpAdapter
        return ModbusTcpAdapter(device)
    if device.adapter == "inteliscada":
        from .inteliscada import InteliScadaAdapter
        return InteliScadaAdapter(device)
    raise ValueError(f"unknown adapter '{device.adapter}' for {device.id}")
