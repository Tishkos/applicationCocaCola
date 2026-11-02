"""
Collector core: registry + adapters + store + a few health checks + daily totals.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from .adapters import build_adapter
from .adapters.simulator import SimPlant
from .registry import Quality, load_registry
from .store import TelemetryStore


POLL_MIN_S = 600                                    # never read the plant more often than this (10 min)
POLL_OPTIONS_S = (600, 1800, 3600, 7200)            # what the dashboard offers: 10 min, 30 min, 1 h, 2 h


class Collector:
    def __init__(self, config_path: str, data_dir: str = "./data", *, seed_hours: float = 0.0, fresh: bool = False,
                 initial_poll: bool = True):
        self.config_path = config_path
        self.registry = load_registry(config_path)
        self.simulated = all(d.adapter == "simulator" for d in self.registry.devices.values())
        self.plant: SimPlant | None = None
        if self.simulated:
            if (self.registry.extra.get("simulation") or {}).get("model") == "real_plant":
                from .sim_realplant import RealPlantSim            # the real plant's devices (config/site.demo.yaml)
                self.plant = RealPlantSim(self.registry.devices)
            else:
                prof = self.registry.extra.get("site_load", {}).get("profile_kw") or [300] * 24
                self.plant = SimPlant(self.registry.devices, prof)
            if seed_hours > 0:                       # start the demo clock in the past, then catch up
                self.plant.now -= timedelta(hours=seed_hours)
        os.makedirs(data_dir, exist_ok=True)
        db = os.path.join(data_dir, "demo.sqlite" if self.simulated else "telemetry.sqlite")
        if fresh and self.simulated and os.path.exists(db):      # demo data is regenerated; real data is never deleted
            for ext in ("", "-wal", "-shm"):
                try:
                    os.remove(db + ext)
                except FileNotFoundError:
                    pass
        self.db_path = db
        self.store = TelemetryStore(db)
        # polling rate: the config value, overridden by what was saved from the dashboard (real plant only)
        self.settings_path = os.path.join(data_dir, "settings.json")
        self.settings_source = "config"
        if not self.simulated:
            saved = self._load_settings()
            if saved.get("poll_interval_s") in POLL_OPTIONS_S:
                self.registry.site.poll_interval_s = float(saved["poll_interval_s"])
                self.settings_source = "settings.json"
        self.adapters = {d.id: build_adapter(d, plant=self.plant) for d in self.registry.devices.values()}
        # adapters connect on their first read (a connect to an unreachable device blocks for its timeout)
        self._comms_lost: set[str] = set()
        self._alarm_on: set[str] = set()
        self._grid_off: set[str] = set()
        self._poll_lock = threading.RLock()
        self.after_poll: list = []               # callbacks run after every poll (the Modbus publisher refreshes here)
        self.started_at = datetime.now(timezone.utc)
        self.polls = 0
        self.last_poll_at: datetime | None = None
        if self.simulated and seed_hours > 0:
            self.poll()
            self._seed_demo(seed_hours)
        elif initial_poll:
            self.poll()                      # `run` skips this and polls from its own thread, so the
                                             # dashboard is up at once even when devices time out

    def _seed_demo(self, hours: float) -> None:
        """Generate demo history with a couple of scripted events so every panel has content."""
        assert self.plant is not None
        gens = [d.id for d in self.registry.by_kind("genset")]
        script = []                                   # (hours before now, scenario, device)
        if len(gens) > 1 and hours >= 12:
            script += [(min(hours - 1, 30), "comms_loss", gens[1]), (min(hours - 1, 30) - 0.4, "comms_restore", gens[1])]
        if gens and hours >= 6:
            script += [(min(hours - 0.5, 9), "genset_alarm", gens[0]), (min(hours - 0.5, 9) - 1.5, "genset_alarm_clear", gens[0])]
        script.sort(key=lambda s: -s[0])
        left = hours
        for at, scenario, dev in script:
            if left > at:
                self.advance((left - at) * 3600, step=300)
                left = at
            self.plant.inject(scenario, dev)
        if left > 0:
            self.advance(left * 3600, step=300)

    def now(self) -> datetime:
        return self.plant.now if self.plant is not None else datetime.now(timezone.utc)

    # --- settings ---------------------------------------------------------------------

    @property
    def poll_interval_s(self) -> float:
        return float(self.registry.site.poll_interval_s)

    def _load_settings(self) -> dict:
        try:
            with open(self.settings_path) as f:
                return json.load(f) or {}
        except (FileNotFoundError, ValueError):
            return {}

    def set_poll_interval(self, seconds: int) -> None:
        """Change the polling rate at runtime and remember it. Only the fixed options are allowed;
        never below POLL_MIN_S, to keep the load on the SAU gateway small."""
        seconds = int(seconds)
        if seconds not in POLL_OPTIONS_S:
            raise ValueError(f"poll interval must be one of {sorted(POLL_OPTIONS_S)} seconds")
        if self.simulated:
            raise ValueError("the demo refreshes every 30 s; this setting applies to the real plant config")
        self.registry.site.poll_interval_s = float(seconds)
        data = self._load_settings()
        data["poll_interval_s"] = seconds
        data["changed_at"] = datetime.now(timezone.utc).isoformat()
        tmp = self.settings_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.settings_path)
        self.settings_source = "settings.json"
        self.store.event("settings", f"poll interval set to {seconds} s", severity="info", code="SETTINGS")

    def close(self) -> None:
        for a in self.adapters.values():
            try:
                a.close()
            except Exception:
                pass

    # --- polling ----------------------------------------------------------------------

    def poll(self) -> None:
        """Read every device once. Safe to call from the API thread while the loop runs."""
        with self._poll_lock:
            self._poll()

    def _poll(self) -> None:
        for did, ad in self.adapters.items():
            try:
                rows = ad.read()
            except Exception as exc:
                d = self.registry.devices[did]
                from .adapters import reading
                rows = {p.id: reading(p, None, Quality.BAD, f"read failed: {exc}") for p in d.points}
            self.store.ingest(did, rows)
            self._health(did, rows)
        try:                                     # free the gateway's connection slots between polls
            from .adapters.modbus_tcp import close_all
            close_all()
        except Exception:
            pass
        self.polls += 1
        self.last_poll_at = datetime.now(timezone.utc)
        for fn in list(self.after_poll):
            try:
                fn()
            except Exception as exc:
                print(f"after_poll callback failed: {exc}")

    def advance(self, seconds: float, step: float = 60.0) -> None:
        """Simulator only: move simulated time forward, polling every `step` seconds."""
        assert self.plant is not None
        left = seconds
        with self.store.bulk():
            while left > 0:
                s = min(step, left)
                self.plant.step(s)
                for ts, kind, dev, sev, code, msg in getattr(self.plant, "drain_events", list)():   # the model's controller messages
                    self.store.event(kind, msg, device_id=dev, severity=sev, code=code, ts=ts)
                left -= s
                self.poll()

    def _health(self, did: str, rows) -> None:
        """Minimal event log: comms lost/restored and controller alarm bits."""
        ts = self.now()
        dev = self.registry.devices[did]
        measured = [r for pid, r in rows.items() if not (dev.point(pid) and dev.point(pid).raw.get("derived"))]
        asked = [r for r in measured if not (r.error or "").startswith("no register mapping")]   # unmapped points were never requested
        all_bad = bool(asked) and all(r.quality == Quality.BAD for r in asked)
        if all_bad and did not in self._comms_lost:
            self._comms_lost.add(did)
            err = next((r.error for r in asked if r.error), "")
            self.store.event("comms_lost", f"{did}: no valid data ({err})", device_id=did, severity="error", code="COMMS_LOST", ts=ts)
        elif not all_bad and did in self._comms_lost:
            self._comms_lost.discard(did)
            self.store.event("comms_restored", f"{did}: data restored", device_id=did, severity="info", code="COMMS_OK", ts=ts)
        av = rows.get("available") if dev.kind == "grid" else rows.get("mains_available")
        if av is not None and av.quality == Quality.GOOD:
            key = f"{did}:grid"
            if not av.value and key not in self._grid_off:
                self._grid_off.add(key)
                self.store.event("grid_lost", f"{did}: grid not available", device_id=did, severity="warning", code="GRID_OFF", ts=ts)
            elif av.value and key in self._grid_off:
                self._grid_off.discard(key)
                self.store.event("grid_restored", f"{did}: grid back", device_id=did, severity="info", code="GRID_OK", ts=ts)
        a = rows.get("alarm_active")
        if a is not None and a.quality == Quality.GOOD:
            if a.value and did not in self._alarm_on:
                self._alarm_on.add(did)
                code = rows.get("alarm_code")
                self.store.event("alarm", f"{did}: alarm active" + (f" (code {code.value})" if code and code.value else ""),
                                 device_id=did, severity="error", code="ALARM", ts=ts)
            elif not a.value and did in self._alarm_on:
                self._alarm_on.discard(did)
                self.store.event("alarm_cleared", f"{did}: alarm cleared", device_id=did, severity="info", code="ALARM_OK", ts=ts)

    def active_issues(self) -> list[dict]:
        out = [{"code": "COMMS_LOST", "device": d, "message": "no valid data"} for d in sorted(self._comms_lost)]
        out += [{"code": "ALARM", "device": d, "message": "controller alarm active"} for d in sorted(self._alarm_on)]
        out += [{"code": "GRID_OFF", "device": k.split(":")[0], "message": "grid not available"} for k in sorted(self._grid_off)]
        return out

    # --- daily totals ----------------------------------------------------------------------

    def daily_totals(self, day: datetime | None = None) -> dict:
        day = (day or self.now()).astimezone(timezone.utc)
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        reg, st = self.registry, self.store
        load = sum(st.integrate_kwh(m.id, "load_kw", start, end) for m in reg.by_kind("meter"))
        if not reg.by_kind("meter"):              # no site meter (the real plant): the master's site load
            src = next((d for d in reg.devices.values() if d.point("load_kw")), None)
            load = st.integrate_kwh(src.id, "load_kw", start, end) if src else 0.0
        # solar: the master's aggregated reading when there is one (as the dashboard), else the array/gateway
        # totals, else the PV devices one by one - never inverters and their gateway together
        master_pv = next((d for d in reg.devices.values() if d.point("pv_prod_kw")), None)
        pvs = reg.by_kind("pv")
        arrays = [p for p in pvs if p.point("inverters_total")] or pvs
        pv = (st.integrate_kwh(master_pv.id, "pv_prod_kw", start, end) if master_pv else
              sum(st.integrate_kwh(p.id, "power_kw" if p.point("power_kw") else "ac_power_kw", start, end) for p in arrays))
        pv_avail = sum(st.integrate_kwh(p.id, "available_kw", start, end) for p in reg.by_kind("pv"))
        gens = {}
        gen_kwh = fuel = 0.0
        for g in reg.by_kind("genset"):
            kwh = st.integrate_kwh(g.id, "power_kw", start, end)
            rows = st.history(g.id, "fuel_rate_lph", start, end)
            litres = sum((v0 + v1) / 2 * (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
                         for (t0, v0), (t1, v1) in zip(rows, rows[1:]) if v0 is not None and v1 is not None)
            run = st.history(g.id, "running", start, end)
            hours = sum((datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
                        for (t0, v0), (t1, _v1) in zip(run, run[1:]) if v0)
            gens[g.id] = {"energy_kwh": round(kwh, 1), "fuel_l": round(litres, 1), "run_hours": round(hours, 2),
                          "avg_load_kw": round(kwh / hours, 1) if hours > 0.05 else 0.0,
                          "l_per_kwh": round(litres / kwh, 3) if kwh > 1 else None}
            gen_kwh += kwh
            fuel += litres
        grid_kwh = sum(st.integrate_kwh(g.id, "power_kw", start, end) for g in reg.by_kind("grid"))
        if not reg.by_kind("grid"):
            grid_kwh = sum(st.integrate_kwh(d.id, "mains_power_kw", start, end) for d in reg.devices.values() if d.point("mains_power_kw"))
        grid_hours = 0.0
        for d in reg.devices.values():               # several devices see the same grid: the longest, not the sum
            pid = "available" if d.kind == "grid" else ("mains_available" if d.point("mains_available") else None)
            if pid:
                rows = st.history(d.id, pid, start, end)
                grid_hours = max(grid_hours, sum((datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
                                                 for (t0, v0), (t1, _v1) in zip(rows, rows[1:]) if v0))
        bess_dis = bess_chg = 0.0
        for b in reg.by_kind("bess"):
            rows = st.history(b.id, "power_kw", start, end)
            for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
                if v0 is None or v1 is None:
                    continue
                e = (v0 + v1) / 2 * (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
                bess_dis += max(e, 0); bess_chg += max(-e, 0)
        # what the gensets would have burned without solar: everything the grid did not supply
        counter_l = max(0.0, load - grid_kwh) * 0.30            # ~0.30 L/kWh at typical load
        metered = any(g.point("fuel_rate_lph") for g in reg.by_kind("genset"))
        burnt_l = fuel if metered else gen_kwh * 0.30            # no fuel metering (the real plant): the same estimate
        return {
            "date": start.date().isoformat(), "site": reg.site.name,
            "totals": {
                "load_kwh": round(load, 1), "pv_kwh": round(pv, 1), "pv_available_kwh": round(pv_avail, 1),
                "pv_curtailed_kwh": round(max(0.0, pv_avail - pv), 1),
                "pv_share_pct": round(100 * pv / load, 1) if load else None,
                "bess_discharged_kwh": round(bess_dis, 1), "bess_charged_kwh": round(bess_chg, 1),
                "genset_kwh": round(gen_kwh, 1), "fuel_l": round(fuel, 1),
                "fuel_cost": round(fuel * reg.site.fuel_price_per_l, 2),
                "fuel_saved_vs_no_solar_l": round(max(0.0, counter_l - burnt_l), 1), "fuel_metered": metered,
                "grid_kwh": round(grid_kwh, 1), "grid_hours": round(grid_hours, 1),
                "grid_share_pct": round(100 * grid_kwh / load, 1) if load else None,
            },
            "gensets": gens,
            "events": [e for e in st.events(start, limit=300) if e["ts"] < end.isoformat()],
        }


def run_loop(c: Collector, interval: float | None = None, stop=None) -> None:
    """Poll whenever the last read is older than the current interval. The interval is read
    every second, so a change from the dashboard takes effect at once; a manual "read now"
    counts as a read and pushes the next scheduled one."""
    fixed = float(interval) if interval else None
    while not (stop and stop.is_set()):
        every = fixed if fixed else c.poll_interval_s
        last = c.last_poll_at.timestamp() if c.last_poll_at else 0.0
        if time.time() - last >= every:
            try:
                if c.simulated:
                    c.advance(every, step=every)
                else:
                    c.poll()
            except Exception as exc:
                print(f"collector error: {exc}")
        time.sleep(1.0)
