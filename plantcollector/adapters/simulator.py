"""
Demo data source: a small plant model (gensets, PV, battery, meter) with its own
simple operating rule, so the dashboard and the exports can be shown before the
real registers are available. Not used with the real plant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..registry import Device, Quality
from ..store import Reading
from . import Adapter, reading


@dataclass
class Gen:
    running: bool = False
    power_kw: float = 0.0
    run_hours: float = 1200.0
    fuel_level_pct: float = 80.0
    fuel_rate_lph: float = 0.0
    coolant_temp_c: float = 40.0
    oil_pressure_bar: float = 0.0
    battery_v: float = 25.6
    mode: str = "AUT"
    alarm_active: bool = False
    alarm_code: int = 0
    power_target_kw: float = 0.0
    started_at: datetime | None = None
    tank_l: float = 2000.0


@dataclass
class PV:
    power_kw: float = 0.0
    available_kw: float = 0.0
    energy_today_kwh: float = 0.0
    energy_total_kwh: float = 950_000.0
    irradiance_wm2: float = 0.0
    inverters_online: int = 8
    status: int = 1
    power_limit_pct: float = 100.0
    soiling: float = 1.0


@dataclass
class Bess:
    soc_pct: float = 55.0
    power_kw: float = 0.0
    power_target_kw: float = 0.0
    soh_pct: float = 97.0
    max_charge_kw: float = 300.0
    max_discharge_kw: float = 300.0
    cell_temp_max_c: float = 28.0
    status: int = 1
    alarm_active: bool = False


@dataclass
class Meter:
    load_kw: float = 0.0
    frequency_hz: float = 50.0
    voltage_v: float = 400.0


@dataclass
class Grid:
    power_kw: float = 0.0
    available: bool = True
    voltage_v: float = 400.0
    frequency_hz: float = 50.0
    energy_import_kwh: float = 412_000.0
    forced_off: bool = False


@dataclass
class SimPlant:
    devices: dict[str, Device]
    load_profile_kw: list[float]
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    gens: dict[str, Gen] = field(default_factory=dict)
    pvs: dict[str, PV] = field(default_factory=dict)
    bess: dict[str, Bess] = field(default_factory=dict)
    meters: dict[str, Meter] = field(default_factory=dict)
    grids: dict[str, Grid] = field(default_factory=dict)
    comms_down: set[str] = field(default_factory=set)

    def __post_init__(self):
        for d in self.devices.values():
            {"genset": self.gens, "pv": self.pvs, "bess": self.bess, "meter": self.meters, "grid": self.grids}.get(d.kind, {})[d.id] = \
                {"genset": Gen, "pv": PV, "bess": Bess, "meter": Meter, "grid": Grid}.get(d.kind, Meter)()
        self.step(0)

    def _grid_schedule(self, t: datetime) -> bool:
        """Government electricity in Erbil comes in blocks. Demo pattern: roughly 2/3 of the day."""
        h = (t.hour + t.minute / 60.0 + 3) % 24
        day = (t + timedelta(hours=3)).timetuple().tm_yday
        off_blocks = [(2 + (day % 3), 4 + (day % 3)), (11 + (day % 2), 13 + (day % 2)), (18, 20.5)]
        return not any(a <= h < b for a, b in off_blocks)

    def _sun(self, t: datetime) -> float:
        h = (t.hour + t.minute / 60.0 + 3) % 24          # plant local time ~ UTC+3
        return 0.0 if h < 6 or h > 18 else max(0.0, math.sin(math.pi * (h - 6) / 12.0))

    def _load(self, t: datetime) -> float:
        h = (t.hour + 3) % 24
        a, b = self.load_profile_kw[h], self.load_profile_kw[(h + 1) % 24]
        return a + (b - a) * (t.minute / 60.0)

    def step(self, seconds: float) -> None:
        dt_h = seconds / 3600.0
        self.now += timedelta(seconds=seconds)
        load = self._load(self.now)
        sun = self._sun(self.now)
        local_day = (self.now + timedelta(hours=3)).date()
        new_day = local_day != getattr(self, "_day", local_day)
        self._day = local_day

        pv_total = 0.0
        for did, p in self.pvs.items():
            rated = float(self.devices[did].limits.get("rated_kw", 800))
            p.irradiance_wm2 = round(1000 * sun, 1)
            p.available_kw = round(rated * sun * p.soiling * p.inverters_online / 8.0, 1)
            p.power_kw = round(min(p.available_kw, rated * p.power_limit_pct / 100.0), 1)
            if new_day:
                p.energy_today_kwh = 0.0
            pv_total += p.power_kw

        # operating rule: solar first, then the government grid when it is present, then the
        # battery (if any), then gensets. Gensets stop when the grid comes back and carries the load.
        deficit = load - pv_total
        grid_kw = 0.0
        for did, gr in self.grids.items():
            gr.available = self._grid_schedule(self.now) and not gr.forced_off
            lim = float(self.devices[did].limits.get("import_limit_kw", 1000))
            gr.power_kw = round(min(lim, max(0.0, deficit)), 1) if gr.available else 0.0
            gr.voltage_v = round(400.0 - 6 * (gr.power_kw / lim), 1) if gr.available else 0.0
            gr.frequency_hz = 50.0 if gr.available else 0.0
            gr.energy_import_kwh = round(gr.energy_import_kwh + gr.power_kw * dt_h, 3)
            grid_kw += gr.power_kw
            deficit -= gr.power_kw
        bess_kw = 0.0
        for b in self.bess.values():
            if deficit > 0 and b.soc_pct > 30:
                bess_kw += min(deficit, b.max_discharge_kw)
            elif deficit < 0 and b.soc_pct < 95:
                bess_kw -= min(-deficit, b.max_charge_kw)
        remaining = deficit - bess_kw
        running = [g for g in self.gens.values() if g.running]
        if remaining > 20 and not running:
            g = next(iter(self.gens.values())); g.running, g.started_at = True, self.now
        elif remaining > 420 and len(running) < len(self.gens):
            for g in self.gens.values():
                if not g.running:
                    g.running, g.started_at = True, self.now; break
        elif remaining < -150 and running:
            g = running[-1]
            if g.started_at and (self.now - g.started_at).total_seconds() > 1800:
                g.running = False
        elif remaining <= 20 and running and grid_kw > 0:      # grid is back: gensets off after min run
            for g in list(running):
                if g.started_at and (self.now - g.started_at).total_seconds() > 1800:
                    g.running = False
        running = [g for g in self.gens.values() if g.running]
        gen_total = 0.0
        if running:
            share = max(0.0, remaining) / len(running)
            for did, g in self.gens.items():
                lim = self.devices[did].limits
                if g.running:
                    tgt = min(max(share, float(lim.get("min_load_kw", 150))), float(lim.get("max_load_kw", 450)))
                    ramp = float(lim.get("ramp_kw_per_min", 100)) * seconds / 60.0
                    g.power_kw = round(min(tgt, g.power_kw + ramp) if tgt > g.power_kw else max(tgt, g.power_kw - ramp), 1)
                    frac = g.power_kw / float(lim.get("rated_kw", 500))
                    g.fuel_rate_lph = round(g.power_kw * (0.26 + 0.16 * max(0.0, 1 - frac)), 2)
                    g.tank_l -= g.fuel_rate_lph * dt_h
                    if g.tank_l < 375:                     # fuel delivery at 15 %
                        g.tank_l = 2375.0
                    g.fuel_level_pct = round(100 * g.tank_l / 2500, 1)
                    g.run_hours += dt_h
                    g.coolant_temp_c = min(92.0, g.coolant_temp_c + 0.8 * seconds / 60)
                    g.oil_pressure_bar = 4.2
                    g.power_target_kw = tgt
                    gen_total += g.power_kw
        for g in self.gens.values():
            if not g.running:
                g.power_kw, g.fuel_rate_lph, g.oil_pressure_bar = 0.0, 0.0, 0.0
                g.coolant_temp_c = max(30.0, g.coolant_temp_c - 0.3 * seconds / 60)

        # the battery takes whatever is left (droop), within its limits
        imbalance = load - pv_total - grid_kw - gen_total
        for did, b in self.bess.items():
            cap = float(self.devices[did].limits.get("capacity_kwh", 1000))
            kw = max(-b.max_charge_kw, min(b.max_discharge_kw, imbalance))
            if kw > 0 and b.soc_pct <= 10:
                kw = 0.0
            if kw < 0 and b.soc_pct >= 95:
                kw = 0.0
            b.power_kw = round(kw, 1)
            b.power_target_kw = b.power_kw
            b.soc_pct = round(min(100.0, max(0.0, b.soc_pct - 100 * kw * dt_h / (cap * 0.96))), 2)
            imbalance -= kw
        if imbalance < 0:  # surplus: PV curtails itself
            for p in self.pvs.values():
                cut = min(p.power_kw, -imbalance); p.power_kw = round(p.power_kw - cut, 1); imbalance += cut
        for p in self.pvs.values():
            p.energy_today_kwh = round(p.energy_today_kwh + p.power_kw * dt_h, 3)
            p.energy_total_kwh = round(p.energy_total_kwh + p.power_kw * dt_h, 3)
        for m in self.meters.values():
            m.load_kw = round(load, 1)
            m.frequency_hz = round(50.0 - min(1.5, max(0.0, imbalance) / 100.0), 2)
            m.voltage_v = round(400.0 - 8 * min(1.5, max(0.0, imbalance) / 100.0), 1)

    def inject(self, scenario: str, device_id: str | None = None):
        if scenario == "pv_soiling":
            for p in self.pvs.values(): p.soiling = 0.72
        elif scenario == "inverter_offline":
            for p in self.pvs.values(): p.inverters_online = 6
        elif scenario == "genset_alarm" and device_id:
            self.gens[device_id].alarm_active, self.gens[device_id].alarm_code = True, 1041
        elif scenario == "genset_alarm_clear" and device_id:
            self.gens[device_id].alarm_active, self.gens[device_id].alarm_code = False, 0
        elif scenario == "grid_off":
            for gr in self.grids.values(): gr.forced_off = True
        elif scenario == "grid_on":
            for gr in self.grids.values(): gr.forced_off = False
        elif scenario == "comms_loss" and device_id:
            self.comms_down.add(device_id)
        elif scenario == "comms_restore" and device_id:
            self.comms_down.discard(device_id)


class SimulatorAdapter(Adapter):
    name = "simulator"

    def __init__(self, device: Device, plant: SimPlant):
        super().__init__(device)
        self.plant = plant

    def read(self) -> dict[str, Reading]:
        d = self.device
        if d.id in self.plant.comms_down:       # silent device: like a real one, only the collector's comm_error is set
            return {p.id: reading(p, True, ts=self.plant.now) if p.raw.get("derived") == "comm_error"
                    else reading(p, None, Quality.BAD, "comms timeout", self.plant.now) for p in d.points}
        if hasattr(self.plant, "values_for"):   # the real-plant model (sim_realplant.py): values by point id
            vals, out = self.plant.values_for(d.id), {}
            for p in d.points:
                v = False if p.raw.get("derived") == "comm_error" else vals.get(p.id)
                if v is None:
                    continue                    # not modelled (e.g. monthly energy): no row, as an unread point
                out[p.id] = reading(p, bool(v) if p.type == "boolean" else v, ts=self.plant.now)
            return out
        st = {"genset": self.plant.gens, "pv": self.plant.pvs, "bess": self.plant.bess, "meter": self.plant.meters,
              "grid": self.plant.grids}.get(d.kind, {}).get(d.id)
        return {p.id: reading(p, getattr(st, p.id, None), ts=self.plant.now) for p in d.points}
