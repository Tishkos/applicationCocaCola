"""
Demo model of the REAL plant (config/site.plant.yaml): the InteliNeo site master with 5 gensets, the Sineng SAU
with its inverters, and the InteliMains incomers. Point ids are those of the real profiles, so every panel of the
dashboard, the exports and the Turkey tag list work in the demo exactly as on site.

The day follows the Neo history of 29 Sep - 14 Oct 2026 (docs/site/neo_history_*.csv): the gensets take the plant
early in the morning when the government grid goes, the grid comes back in the afternoon, PV is curtailed to the
PV request while on gensets, the SAU stalls now and then (Wrn PV Logger group 1/2 Comm Fail) and an inverter trips
once in a while. Used by the collector (config/site.demo.yaml, `simulation: {model: real_plant}`) and by
tools/grafana_demo_data.py. It never touches a device.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    TZ = ZoneInfo("Asia/Baghdad")
except Exception:                                     # no tz database: Erbil has no daylight saving
    TZ = timezone(timedelta(hours=3), "Asia/Baghdad")

GEN_NOM_KW = 1088.0          # one genset; 5 together = 4352 kW (Neo history maximum)
STRINGS = 12


class RealPlantSim:
    def __init__(self, devices: dict, now: datetime | None = None, seed: int = 7, emit_all_events: bool = False):
        self.now = now or datetime.now(timezone.utc)
        self.rng = random.Random(seed)
        self.emit_all = emit_all_events          # True: also grid / comms / alarm events (no collector to make them)
        self.comms_down: set[str] = set()
        kinds = {d.id: d for d in devices.values()}
        self.master = next((d.id for d in kinds.values() if d.kind == "genset" and d.point("g1_running")), None)
        self.n_gens = sum(1 for p in kinds[self.master].points if p.id.endswith("_running") and p.id[1:-8].isdigit()) if self.master else 0
        self.inverters = [d.id for d in kinds.values() if d.kind == "pv" and d.point("ac_power_kw") and not d.point("inverters_total")]
        self.arrays = [d.id for d in kinds.values() if d.kind == "pv" and d.point("inverters_total")]
        self.mains = [d.id for d in kinds.values() if d.kind == "grid"]
        self.analyzers = [d.id for d in kinds.values() if d.kind == "analyzer"]       # the power meters (own feeders)
        self.an_kwh = [148_200.0 + 61_500 * i for i in range(len(self.analyzers))]
        self.inv_rated = {i: float(kinds[i].limits.get("rated_kw", 320)) * 1.1 for i in self.inverters}    # 320 kW nominal, 352 kW max
        self.pv_rated = sum(self.inv_rated.values()) or 3520.0
        n = len(self.inverters)
        self.gen_on = [False] * self.n_gens
        self.gen_hours = [5210.0, 5185.0, 5242.0, 5170.0, 5196.0, 5200.0, 5190.0, 5180.0][: self.n_gens]
        self.gen_starts = [812, 790, 845, 610, 702, 700, 700, 700][: self.n_gens]
        self.pv_total_kwh = [612_000.0 + 9_000 * i for i in range(n)]
        self.pv_today = [0.0] * n
        self.soiling = [1.0] * n
        self.pcc_kwh, self.mains_kwh = 4_812_000.0, 2_215_000.0
        self.day = None
        self.grid_off_h = (6.0, 15.5)
        self.sau_down_until: datetime | None = None
        self.inv_down: dict[int, datetime] = {}
        self.forced_alarm_until: datetime | None = None
        self.cloud, self.heartbeat = 1.0, 0
        self.grid_was_on: bool | None = None
        self.events: list[tuple] = []           # (ts, kind, device, severity, code, message)
        self.data: dict[str, dict[str, float | None]] = {}
        self.step(0)

    # ------------------------------------------------------------------ events
    def _ev(self, kind, msg, dev=None, sev="info", code=None, collector_makes_it=False):
        if collector_makes_it and not self.emit_all:
            return
        self.events.append((self.now, kind, dev, sev, code, msg))

    def drain_events(self) -> list[tuple]:
        out, self.events = self.events, []
        return out

    def values_for(self, device_id: str) -> dict:
        return self.data.get(device_id, {})

    def inject(self, scenario: str, device_id: str | None = None):
        if scenario == "genset_alarm":
            self.forced_alarm_until = self.now + timedelta(hours=1)
        elif scenario == "genset_alarm_clear":
            self.forced_alarm_until = None
        elif scenario == "comms_loss" and device_id:
            self.comms_down.add(device_id)
        elif scenario == "comms_restore" and device_id:
            self.comms_down.discard(device_id)
        elif scenario == "sau_stall":
            self.sau_down_until = self.now + timedelta(minutes=10)

    # ------------------------------------------------------------------ one step of simulated time
    def step(self, seconds: float) -> None:
        r = self.rng
        self.now += timedelta(seconds=seconds)
        dt_s = max(seconds, 1.0)
        local = self.now.astimezone(TZ)
        if self.day != local.date():
            self.day = local.date()
            self.grid_off_h = (r.uniform(5.8, 9.8), r.uniform(13.5, 18.3))
            self.pv_today = [0.0] * len(self.inverters)
            self.soiling = [r.uniform(0.93, 1.0) for _ in self.inverters]
        h = local.hour + local.minute / 60 + local.second / 3600

        # site load: bottling plant, two shifts
        base = 900 + 950 * math.exp(-((h - 12.5) / 4.6) ** 2) + 120 * math.sin(h / 24 * 2 * math.pi * 3)
        load = max(500.0, base * r.uniform(0.97, 1.03))
        # sun (Erbil, autumn): about 06:20 - 17:00
        sun = max(0.0, math.sin(math.pi * (h - 6.3) / (17.0 - 6.3))) if 6.3 < h < 17.0 else 0.0
        self.cloud = min(1.0, max(0.55, self.cloud + r.uniform(-0.04, 0.04)))
        irr = 820 * sun ** 1.2 * self.cloud
        pv_avail = self.pv_rated * 0.92 * irr / 1000

        # government grid: off in the morning / midday, back in the afternoon
        grid_on = not (self.grid_off_h[0] <= h < self.grid_off_h[1])
        if self.grid_was_on is not None and grid_on != self.grid_was_on:
            if grid_on:
                self._ev("grid_restored", f"{self.mains[0] if self.mains else 'grid'}: grid back", self.mains[0] if self.mains else None, "info", "GRID_OK", True)
                self._ev("neo", f"{self.master}: Mains/Bus healthy - transfer back to mains", self.master, "info", "MAINS_RET")
            else:
                self._ev("grid_lost", f"{self.mains[0] if self.mains else 'grid'}: grid not available", self.mains[0] if self.mains else None, "warning", "GRID_OFF", True)
                self._ev("neo", f"{self.master}: Alarm Mains Fail - gensets take the load", self.master, "warning", "MAINS_FAIL")
        self.grid_was_on = grid_on

        # SAU stalls: a few per day, 2..12 min (the PV logger comm fail)
        gw = self.arrays + self.inverters
        if self.sau_down_until and self.now >= self.sau_down_until:
            self.sau_down_until = None
            self.comms_down.difference_update(gw)
            for d in self.arrays:
                self._ev("comms_restored", f"{d}: data restored", d, "info", "COMMS_OK", True)
        if not self.sau_down_until and seconds > 0 and r.random() < dt_s / (6 * 3600):
            self.sau_down_until = self.now + timedelta(minutes=r.uniform(2, 12))
            grp = r.choice([1, 2])
            for d in self.arrays:
                self._ev("comms_lost", f"{d}: no valid data (timeout)", d, "error", "COMMS_LOST", True)
            self._ev("neo", f"{self.master}: Wrn PV Logger group {grp} Comm Fail", self.master, "warning", "PV_LOGGER")
        if self.sau_down_until:
            self.comms_down.update(gw)
        sau_ok = self.sau_down_until is None

        # an inverter trips now and then
        for i, until in list(self.inv_down.items()):
            if self.now >= until:
                del self.inv_down[i]
                self._ev("neo", f"{self.inverters[i]}: back on the grid", self.inverters[i], "info", "INV_OK")
        if self.inverters and sun > 0.1 and seconds > 0 and r.random() < dt_s / (30 * 3600):
            i = r.randrange(len(self.inverters))
            if i not in self.inv_down:
                self.inv_down[i] = self.now + timedelta(minutes=r.uniform(10, 60))
                self._ev("neo", f"{self.inverters[i]}: tripped (grid over-voltage)", self.inverters[i], "warning", "INV_TRIP")
        online = [i for i in range(len(self.inverters)) if i not in self.inv_down]
        frac_on = len(online) / max(1, len(self.inverters))

        # dispatch (what the Neo does)
        if grid_on:
            pv_req = pv_avail
            pv = pv_avail * frac_on
            gens_needed, mains = 0, load - pv
        else:
            need = max(load - pv_avail * 0.9, 0)
            gens_needed = min(self.n_gens, max(2, math.ceil(need / (GEN_NOM_KW * 0.8)) if need > 0 else 2))
            pv_req = max(0.0, load - gens_needed * GEN_NOM_KW * 0.30)          # keep the gensets above ~30 %
            pv = min(pv_avail * frac_on, pv_req)
            mains = 0.0
        if not sau_ok and not grid_on:
            pv = min(pv, pv_req * 0.4)                                         # the Neo opens the PV path

        # running gensets stay on; on a change start the one with the fewest hours / stop the one with the most
        want = {g for g in range(self.n_gens) if self.gen_on[g]}
        while len(want) < gens_needed:
            want.add(min((g for g in range(self.n_gens) if g not in want), key=lambda g: self.gen_hours[g]))
        while len(want) > gens_needed:
            want.discard(max(want, key=lambda g: self.gen_hours[g]))
        for g in range(self.n_gens):
            on = g in want
            if on and not self.gen_on[g]:
                self.gen_starts[g] += 1
                self._ev("neo", f"{self.master}: Gen {g + 1} started, GCB closed", self.master, "info", "GEN_START")
            if not on and self.gen_on[g]:
                self._ev("neo", f"{self.master}: Gen {g + 1} stopped", self.master, "info", "GEN_STOP")
            self.gen_on[g] = on
            if on:
                self.gen_hours[g] += seconds / 3600
        n_run = sum(self.gen_on)
        gens_kw = max(0.0, load - pv - mains) if n_run else 0.0
        nominal = n_run * GEN_NOM_KW
        self.pcc_kwh += load * seconds / 3600
        self.mains_kwh += max(mains, 0) * seconds / 3600
        self.heartbeat ^= 1
        f = 50.0 + (r.uniform(-0.05, 0.05) if grid_on else r.uniform(-0.15, 0.12))
        v = 231 + (r.uniform(-3, 3) if grid_on else r.uniform(-6, 5))
        forced = self.forced_alarm_until is not None and self.now < self.forced_alarm_until
        alarm = (not sau_ok) or bool(self.inv_down) or forced
        D: dict[str, dict] = {}

        if self.master:
            neo = {
                "power_kw": gens_kw, "gensets_q_kvar": gens_kw * 0.32, "gensets_pf": 0.95 if n_run else 0.0,
                "mains_power_kw": mains, "mains_q_kvar": mains * 0.28, "mains_pf": 0.96 if grid_on else 0.0,
                "mains_current_l1_a": max(mains, 0) / (math.sqrt(3) * 0.4 * 0.96) / 3,
                "pv_prod_kw": pv if sau_ok else D.get("_pv_seen", pv), "pv_q_kvar": pv * 0.05, "pv_s_kva": pv * 1.01, "pv_pf": 0.99 if pv > 1 else 0.0,
                "pv_curtailment_pct": 100.0 if grid_on else min(100.0, 100 * pv_req / self.pv_rated),
                "ren_power_kw": pv, "total_p_kw": load, "load_kw": load,
                "bus_frequency_hz": f, "bus_voltage_v": v, "bus_voltage_l1_v": v, "bus_voltage_l2_v": v + r.uniform(-1, 1),
                "bus_voltage_l3_v": v + r.uniform(-1, 1), "bus_voltage_l12_v": v * 1.732, "bus_voltage_l23_v": v * 1.732,
                "bus_voltage_l31_v": v * 1.732, "bus_voltage_rel_pct": v / 230 * 100, "engine_priority": 1,
                "actual_reserve_kw": nominal - gens_kw, "start_reserve_kw": 300.0, "stop_reserve_kw": 900.0,
                "running_power_kw": gens_kw, "running_nominal_kw": nominal, "battery_v": 27.1 + r.uniform(-0.2, 0.2),
                "pvcb_closed": 1, "pvcb_open": 0, "pv2cb_closed": 1, "ups1_feedback": 1, "ups2_feedback": 1,
                "remote_off": 0, "remote_test": 0, "sd_override": 0, "estop": 0,
                "pv_cb1_on_cmd": 0, "pv_cb2_on_cmd": 0, "ups1_on_cmd": 0, "pv_cb1_off_cmd": 0, "pv_cb2_off_cmd": 0,
                "ups1_off_cmd": 0, "ups2_on_cmd": 0, "ups2_off_cmd": 0,
                "pcc_kwh": self.pcc_kwh, "mains_kwh": self.mains_kwh,
                "running_hours": sum(self.gen_hours), "num_starts": float(sum(self.gen_starts)),
                "controller_mode": 2, "breaker_state": 3 if grid_on else 2, "eth_status": 3,
                "can_present_mask": float((1 << (self.n_gens + 4)) - 1),
                "running": 1 if n_run else 0, "alarm": 1 if alarm else 0, "mcb_status": 1 if grid_on else 0,
                "mains_available": 1 if grid_on else 0, "pvcb_status": 1, "pv_run_request": 1 if sun > 0 else 0,
                "curtailment_enable": 0 if grid_on else 1, "system_ready": 1, "peripheral_comm_fail": 0 if sau_ok else 1,
                "common_warning": 1 if alarm else 0, "common_shutdown": 0, "alarm_active": 1 if alarm else 0,
                "common_pvcb_open": 0, "common_mains_protection": 0, "not_in_auto": 0, "mode_auto": 1,
                "heartbeat": self.heartbeat, "bus_healthy": 1, "deadbus": 0, "alarm_mains_fail": 0 if grid_on else 1,
                "alarm_mains_frequency": 0, "alarm_mains_voltage": 0, "pv_operable": 1 if sau_ok else 0,
                "ecu1_comm_fail": 0, "ecu2_comm_fail": 0 if sau_ok else 1, "ecu3_comm_fail": 0, "ecu4_comm_fail": 0,
                "ecu5_comm_fail": 0, "pv_logger_comm_fail": 0 if sau_ok else 1,
            }
            for g in range(self.n_gens):
                neo[f"g{g + 1}_running"] = 1 if self.gen_on[g] else 0
            if not sau_ok:
                neo["pv_prod_kw"] = getattr(self, "_pv_seen", pv)
            D[self.master] = neo
        if sau_ok:
            self._pv_seen = pv

        share = pv / max(1, len(online))
        avg_soil = sum(self.soiling[j] for j in online) / max(1, len(online))
        for i, did in enumerate(self.inverters):
            up = i in online and sun > 0.02
            p = share * self.soiling[i] / avg_soil if up else 0.0
            self.pv_today[i] += p * seconds / 3600
            self.pv_total_kwh[i] += p * seconds / 3600
            eff = 98.3 + r.uniform(-0.3, 0.2) if p > 5 else 0.0
            dc = p / (eff / 100) if eff else 0.0
            vac = 800 + r.uniform(-6, 6)
            iac = p * 1000 / (math.sqrt(3) * vac) if p else 0.0
            pts = {
                "ac_power_kw": p, "reactive_kvar": p * 0.04, "power_factor": 0.99 if p else 0.0,
                "voltage_l1_v": vac, "voltage_l2_v": vac + r.uniform(-2, 2), "voltage_l3_v": vac + r.uniform(-2, 2),
                "frequency_hz": f, "current_l1_a": iac, "current_l2_a": iac * 1.01, "current_l3_a": iac * 0.99,
                "dc_power_kw": dc, "efficiency_pct": eff, "cabin_temp_c": 24 + 22 * (p / 352) + r.uniform(-0.8, 0.8),
                "energy_today_kwh": self.pv_today[i], "energy_total_kwh": self.pv_total_kwh[i],
                "grid_connected": 1 if up else 0, "running_with_alarm": 1 if i in self.inv_down else 0,
                "derated": 1 if (not grid_on and p > 0) else 0, "standby": 1 if (sun <= 0.02 and i in online and 6 < local.hour < 18) else 0,
                "dormant": 1 if sun <= 0.02 else 0, "fault": 1 if i in self.inv_down else 0, "warning": 1 if i in self.inv_down else 0,
            }
            for s_ in range(STRINGS):
                pts[f"string_current_{s_ + 1}_a"] = (dc * 1000 / 1150 / STRINGS) * r.uniform(0.95, 1.04) if dc else 0.0
                pts[f"string_voltage_{s_ + 1}_v"] = (1080 + r.uniform(-25, 25)) if dc else (40.0 if sun > 0 else 0.0)
            D[did] = pts
        for did in self.arrays:
            D[did] = {
                "ac_power_kw": pv, "reactive_kvar": pv * 0.04, "power_factor": 0.99 if pv else 0.0,
                "energy_today_kwh": sum(self.pv_today), "energy_total_kwh": sum(self.pv_total_kwh),
                "inverters_total": len(self.inverters), "inverters_online": len(self.inverters) if sun > 0 else 0,
                "inverters_working": len(online) if sun > 0.02 else 0, "max_power_kw": self.pv_rated,
                "max_reactive_kvar": 1056.0, "min_reactive_kvar": -1056.0, "available_kw": pv_avail,
                "available_reactive_kvar": 1056.0, "min_reactive_available_kvar": -1056.0,
                "inverters_stopped": len(self.inv_down), "inverters_standby": 0 if sun > 0.02 else len(self.inverters),
                "inverters_faulty": len(self.inv_down), "inverters_alarm": len(self.inv_down),
                "rated_capacity_kva": self.pv_rated, "frequency_hz": f,
                "set_2100": 0, "set_2101": 0, "set_2102": 0, "set_2103": 0, "set_2104": 0, "set_2105": 0, "set_2106": 0,
                "mode_1007": 0, "mode_1008": 0,
            }
        split = [0.38, 0.27, 0.35, 0.2, 0.2]
        for m, did in enumerate(self.mains):
            pm, lm = mains * split[m % 5] * r.uniform(0.97, 1.03), load * split[m % 5] * r.uniform(0.97, 1.03)
            D[did] = {"power_kw": pm, "mains_import_q_kvar": pm * 0.28, "mains_pf": 0.96 if grid_on else 0.0,
                      "load_p_kw": lm, "load_q_kvar": lm * 0.3, "load_pf": 0.95,
                      "battery_v": 27.0 + r.uniform(-0.2, 0.2), "available": 1 if grid_on else 0}
        # the power meters: two feeders of the plant (a small one and a bigger one), on the same 400 V bus
        for k, did in enumerate(self.analyzers):
            share = (0.024, 0.085, 0.05)[k % 3]
            p = max(0.0, load * share * (1 + 0.18 * math.sin(h / 24 * 2 * math.pi * (2 + k))) * r.uniform(0.96, 1.04))
            pf = min(0.99, max(0.8, 0.93 + 0.03 * math.sin(h / 3 + k) + r.uniform(-0.01, 0.01)))
            s_ = p / pf
            q = math.sqrt(max(0.0, s_ * s_ - p * p))
            vll = 401 + 3 * math.sin(h / 5) + r.uniform(-1.2, 1.2)
            vs = [vll + r.uniform(-1.5, 1.5) for _ in range(3)]
            ia = s_ * 1000 / (math.sqrt(3) * vll)
            imb = (1.0 + r.uniform(0.01, 0.04), 1.0 - r.uniform(0.0, 0.03), 1.0 + r.uniform(-0.02, 0.02))
            cur = [ia * x for x in imb]
            self.an_kwh[k] += p * seconds / 3600
            D[did] = {"p_total_kw": round(p), "p_l1_kw": round(p * imb[0] / 3), "p_l2_kw": round(p * imb[1] / 3), "p_l3_kw": round(p * imb[2] / 3),
                      "q_total_kvar": round(q), "s_total_kva": round(s_), "pf_total": round(pf, 3),
                      "i_l1_a": round(cur[0]), "i_l2_a": round(cur[1]), "i_l3_a": round(cur[2]), "i_avg_a": round(sum(cur) / 3),
                      "i_n_a": round(ia * 0.04), "v_l1l2_v": round(vs[0]), "v_l2l3_v": round(vs[1]), "v_l3l1_v": round(vs[2]),
                      "v_ll_avg_v": round(sum(vs) / 3), "v_l1n_v": round(vs[0] / math.sqrt(3)), "v_l2n_v": round(vs[1] / math.sqrt(3)),
                      "v_l3n_v": round(vs[2] / math.sqrt(3)), "v_ln_avg_v": round(sum(vs) / 3 / math.sqrt(3)), "f_hz": round(f, 2),
                      "e_in_kwh": round(self.an_kwh[k], 3), "e_out_kwh": 0.0, "ct_primary_a": (450, 1000, 600)[k % 3]}
        self.data = D
