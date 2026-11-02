"""
Demo data for the Grafana dashboard, shaped like the REAL plant (config/site.plant.yaml):
neo-01 (InteliNeo master), inv-01..inv-10 (Sineng SP-320K-H behind the SAU), pv-array (the SAU),
main-01..main-03 (InteliMains 210). Same tables, point ids, quality values and timestamp format as
the collector writes, so the dashboard that shows this also shows data\\telemetry.sqlite.

The day follows what the Neo history of 29 Sep - 14 Oct 2026 shows (docs/site/neo_history_*.csv):
the gensets take the plant early in the morning, the government grid comes back in the afternoon,
PV is curtailed to the PV request while on gensets, the SAU drops out now and then
(Wrn PV Logger group 1/2 Comm Fail).

It never touches a device. Writes data/grafana/plant-demo.sqlite (a plain rollback-journal copy,
replaced atomically, so Grafana in Docker can read it through the bind mount) every 30 s.

    python tools/grafana_demo_data.py            # 7 days of history, then live every 30 s
    python tools/grafana_demo_data.py --once     # history only, then exit
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "grafana")
OUT = os.path.join(OUT_DIR, "plant-demo.sqlite")
WORK = os.path.join(OUT_DIR, "plant-demo.work.sqlite")
TZ = ZoneInfo("Asia/Baghdad")

HISTORY_DAYS = 7
HISTORY_STEP_S = 600
LIVE_STEP_S = 30

GEN_NOM_KW = 1088.0          # 5 gensets, 4352 kW together (Neo history max)
PV_RATED_KW = 3520.0         # 10 x 352 kW
INVERTERS = 10
STRINGS = 12


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat()


class Plant:
    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)
        self.gen_on = [False] * 5
        self.gen_hours = [5210.0, 5185.0, 5242.0, 5170.0, 5196.0]
        self.gen_starts = [812, 790, 845, 610, 702]
        self.pv_total_kwh = [612_000.0 + 9_000 * i for i in range(INVERTERS)]
        self.pv_today = [0.0] * INVERTERS
        self.pcc_kwh = 4_812_000.0
        self.mains_kwh = 2_215_000.0
        self.day = None
        self.grid_off_h = (6.0, 15.5)      # set per day
        self.sau_down_until: datetime | None = None
        self.inv_down: dict[int, datetime] = {}
        self.cloud = 1.0
        self.prev = {}
        self.events: list[tuple] = []
        self.heartbeat = 0

    def new_day(self, local: datetime):
        r = self.rng
        self.day = local.date()
        self.grid_off_h = (r.uniform(5.8, 9.8), r.uniform(13.5, 18.3))
        self.pv_today = [0.0] * INVERTERS
        self.soiling = [r.uniform(0.93, 1.0) for _ in range(INVERTERS)]

    def ev(self, t, kind, msg, dev=None, sev="info", code=None):
        self.events.append((iso(t), kind, dev, sev, code, msg))

    # ---------------------------------------------------------------- one poll
    def step(self, t: datetime, dt_s: float) -> dict[str, dict[str, tuple]]:
        r = self.rng
        local = t.astimezone(TZ)
        if self.day != local.date():
            self.new_day(local)
        h = local.hour + local.minute / 60 + local.second / 3600

        # site load: bottling plant, two shifts
        base = 900 + 950 * math.exp(-((h - 12.5) / 4.6) ** 2) + 120 * math.sin(h / 24 * 2 * math.pi * 3)
        load = max(500.0, base * r.uniform(0.96, 1.04))

        # sun (Erbil, early November): sunrise ~06:20, sunset ~17:00
        sun = max(0.0, math.sin(math.pi * (h - 6.3) / (17.0 - 6.3))) if 6.3 < h < 17.0 else 0.0
        self.cloud += r.uniform(-0.05, 0.05)
        self.cloud = min(1.0, max(0.55, self.cloud))
        irr = 820 * sun ** 1.2 * self.cloud
        pv_avail = PV_RATED_KW * 0.92 * (irr / 1000)

        # government grid off in the morning/midday window, back in the afternoon
        grid_on = not (self.grid_off_h[0] <= h < self.grid_off_h[1])
        if self.prev.get("grid_on", grid_on) != grid_on:
            if grid_on:
                self.ev(t, "grid_restored", "main-01: grid back", "main-01", "info", "GRID_OK")
                self.ev(t, "neo", "neo-01: Mains/Bus healthy, transfer to mains", "neo-01", "info", "MAINS_RET")
            else:
                self.ev(t, "grid_lost", "main-01: grid not available", "main-01", "warning", "GRID_OFF")
                self.ev(t, "neo", "neo-01: Alarm Mains Fail, gensets take the load", "neo-01", "warning", "MAINS_FAIL")
        self.prev["grid_on"] = grid_on

        # SAU stalls (the PV logger comm fail the session hunts): a few per day, 2..12 min
        if self.sau_down_until and t >= self.sau_down_until:
            self.sau_down_until = None
            self.ev(t, "comms_restored", "pv-array: data restored", "pv-array", "info", "COMMS_OK")
        if not self.sau_down_until and r.random() < dt_s / (6 * 3600):
            self.sau_down_until = t + timedelta(minutes=r.uniform(2, 12))
            grp = r.choice([1, 2])
            self.ev(t, "comms_lost", "pv-array: no valid data (timeout)", "pv-array", "error", "COMMS_LOST")
            self.ev(t, "neo", f"neo-01: Wrn PV Logger group {grp} Comm Fail", "neo-01", "warning", "PV_LOGGER")
        sau_ok = self.sau_down_until is None

        # an inverter trips now and then
        for i, until in list(self.inv_down.items()):
            if t >= until:
                del self.inv_down[i]
                self.ev(t, "alarm_cleared", f"inv-{i + 1:02d}: back on the grid", f"inv-{i + 1:02d}", "info", "ALARM_OK")
        if sun > 0.1 and r.random() < dt_s / (30 * 3600):
            i = r.randrange(INVERTERS)
            if i not in self.inv_down:
                self.inv_down[i] = t + timedelta(minutes=r.uniform(10, 60))
                self.ev(t, "alarm", f"inv-{i + 1:02d}: alarm active (grid over-voltage)", f"inv-{i + 1:02d}", "warning", "ALARM")

        # dispatch
        if grid_on:
            pv_req = pv_avail                                  # no curtailment on the grid
            pv = pv_avail * (len([1 for i in range(INVERTERS) if i not in self.inv_down]) / INVERTERS)
            gens_needed = 0
            mains = load - pv
        else:
            # gensets keep >= ~30 % load; PV limited to the PV request
            need = max(load - pv_avail * 0.9, 0)
            gens_needed = min(5, max(2, math.ceil(need / (GEN_NOM_KW * 0.8)) if need > 0 else 2))
            gen_min = gens_needed * GEN_NOM_KW * 0.30
            pv_req = max(0.0, load - gen_min)
            pv = min(pv_avail * (len([1 for i in range(INVERTERS) if i not in self.inv_down]) / INVERTERS), pv_req)
            mains = 0.0
        if not sau_ok and not grid_on:
            pv = min(pv, pv_req * 0.4)                         # Neo opens the PV path while the SAU is silent

        # running gensets stay on; when the count changes, start the one with the fewest hours
        # or stop the one with the most (the master's running-hours balancing)
        want = {g for g in range(5) if self.gen_on[g]}
        while len(want) < gens_needed:
            want.add(min((g for g in range(5) if g not in want), key=lambda g: self.gen_hours[g]))
        while len(want) > gens_needed:
            want.discard(max(want, key=lambda g: self.gen_hours[g]))
        for g in range(5):
            on = g in want
            if on and not self.gen_on[g]:
                self.gen_starts[g] += 1
                self.ev(t, "neo", f"neo-01: Gen {g + 1} started, GCB closed", "neo-01", "info", "GEN_START")
            if not on and self.gen_on[g]:
                self.ev(t, "neo", f"neo-01: Gen {g + 1} stopped", "neo-01", "info", "GEN_STOP")
            self.gen_on[g] = on
            if on:
                self.gen_hours[g] += dt_s / 3600
        n_run = sum(self.gen_on)
        gens_kw = max(0.0, load - pv - mains) if n_run else 0.0
        running_nom = n_run * GEN_NOM_KW

        self.pcc_kwh += load * dt_s / 3600
        self.mains_kwh += max(mains, 0) * dt_s / 3600
        self.heartbeat ^= 1
        f = 50.0 + (r.uniform(-0.05, 0.05) if grid_on else r.uniform(-0.15, 0.12))
        v = 231 + (r.uniform(-3, 3) if grid_on else r.uniform(-6, 5))
        g = lambda: 1.0                                       # 'good'

        out: dict[str, dict[str, tuple]] = {}
        alarm = (not sau_ok) or bool(self.inv_down)
        breaker = 3 if grid_on else 2                         # MainsOper / IslOper
        neo = {
            "power_kw": gens_kw, "gensets_q_kvar": gens_kw * 0.32, "gensets_pf": 0.95 if n_run else 0,
            "mains_power_kw": mains, "mains_q_kvar": mains * 0.28, "mains_pf": 0.96 if grid_on else 0,
            "mains_current_l1_a": max(mains, 0) / (math.sqrt(3) * 0.4 * 0.96) / 3 * 1.0,
            "pv_prod_kw": pv if sau_ok else self.prev.get("pv_seen", 0.0),
            "pv_q_kvar": pv * 0.05, "pv_s_kva": pv * 1.01, "pv_pf": 0.99 if pv > 1 else 0,
            "pv_curtailment_pct": 100.0 if grid_on else min(100.0, 100 * pv_req / PV_RATED_KW),
            "ren_power_kw": pv, "total_p_kw": load, "load_kw": load,
            "bus_frequency_hz": f, "bus_voltage_v": v, "bus_voltage_l1_v": v, "bus_voltage_l2_v": v + r.uniform(-1, 1),
            "bus_voltage_l3_v": v + r.uniform(-1, 1), "bus_voltage_l12_v": v * 1.732, "bus_voltage_l23_v": v * 1.732,
            "bus_voltage_l31_v": v * 1.732, "bus_voltage_rel_pct": v / 230 * 100,
            "engine_priority": 1, "actual_reserve_kw": running_nom - gens_kw,
            "start_reserve_kw": 300.0, "stop_reserve_kw": 900.0,
            "running_power_kw": gens_kw, "running_nominal_kw": running_nom, "battery_v": 27.1 + r.uniform(-0.2, 0.2),
            "pvcb_closed": 1.0, "pvcb_open": 0.0, "pv2cb_closed": 1.0, "ups1_feedback": 1.0, "ups2_feedback": 1.0,
            "remote_off": 0.0, "remote_test": 0.0, "sd_override": 0.0, "estop": 0.0,
            "pcc_kwh": self.pcc_kwh, "mains_kwh": self.mains_kwh,
            "running_hours": sum(self.gen_hours), "num_starts": float(sum(self.gen_starts)),
            "controller_mode": 2.0, "breaker_state": float(breaker), "eth_status": 3.0,
            "can_present_mask": float(0b111111111),
            "running": 1.0 if n_run else 0.0, "alarm": 1.0 if alarm else 0.0,
            "mcb_status": 1.0 if grid_on else 0.0, "mains_available": 1.0 if grid_on else 0.0,
            "pvcb_status": 1.0, "pv_run_request": 1.0 if sun > 0 else 0.0, "curtailment_enable": 0.0 if grid_on else 1.0,
            "system_ready": 1.0, "peripheral_comm_fail": 0.0 if sau_ok else 1.0,
            "common_warning": 1.0 if alarm else 0.0, "common_shutdown": 0.0, "alarm_active": 1.0 if alarm else 0.0,
            "common_pvcb_open": 0.0, "common_mains_protection": 0.0, "not_in_auto": 0.0, "mode_auto": 1.0,
            "heartbeat": float(self.heartbeat), "bus_healthy": 1.0, "deadbus": 0.0,
            "alarm_mains_fail": 0.0 if grid_on else 1.0, "alarm_mains_frequency": 0.0, "alarm_mains_voltage": 0.0,
            "pv_operable": 1.0 if sau_ok else 0.0,
            "ecu1_comm_fail": 0.0, "ecu2_comm_fail": 0.0 if sau_ok else 1.0, "ecu3_comm_fail": 0.0,
            "ecu4_comm_fail": 0.0, "ecu5_comm_fail": 0.0, "pv_logger_comm_fail": 0.0 if sau_ok else 1.0, "comm_error": 0.0,
        }
        for gi in range(5):
            neo[f"g{gi + 1}_running"] = 1.0 if self.gen_on[gi] else 0.0
        out["neo-01"] = {k: (float(val), "good") for k, val in neo.items()}
        if sau_ok:
            self.prev["pv_seen"] = pv

        # inverters (through the SAU: silent with it)
        online = [i for i in range(INVERTERS) if i not in self.inv_down]
        share = pv / max(1, len(online))
        for i in range(INVERTERS):
            did = f"inv-{i + 1:02d}"
            if not sau_ok:
                out[did] = {"comm_error": (1.0, "good"), "ac_power_kw": (None, "bad")}
                continue
            up = i in online and sun > 0.02
            p = share * self.soiling[i] / (sum(self.soiling[j] for j in online) / max(1, len(online))) if up else 0.0
            self.pv_today[i] += p * dt_s / 3600
            self.pv_total_kwh[i] += p * dt_s / 3600
            temp = 24 + 22 * (p / 352) + r.uniform(-0.8, 0.8)
            eff = 98.3 + r.uniform(-0.3, 0.2) if p > 5 else 0.0
            dc = p / (eff / 100) if eff else 0.0
            vac = 800 + r.uniform(-6, 6)
            iac = p * 1000 / (math.sqrt(3) * vac) if p else 0.0
            pts = {
                "ac_power_kw": p, "reactive_kvar": p * 0.04, "power_factor": 0.99 if p else 0.0,
                "voltage_l1_v": vac, "voltage_l2_v": vac + r.uniform(-2, 2), "voltage_l3_v": vac + r.uniform(-2, 2),
                "frequency_hz": f, "current_l1_a": iac, "current_l2_a": iac * 1.01, "current_l3_a": iac * 0.99,
                "dc_power_kw": dc, "efficiency_pct": eff, "cabin_temp_c": temp,
                "energy_today_kwh": self.pv_today[i], "energy_total_kwh": self.pv_total_kwh[i],
                "comm_error": 0.0, "grid_connected": 1.0 if up else 0.0,
                "running_with_alarm": 1.0 if i in self.inv_down else 0.0, "derated": 1.0 if (not grid_on and p > 0) else 0.0,
                "standby": 1.0 if (sun <= 0.02 and i in online and 6 < local.hour < 18) else 0.0,
                "dormant": 1.0 if sun <= 0.02 else 0.0, "fault": 1.0 if i in self.inv_down else 0.0,
                "warning": 1.0 if i in self.inv_down else 0.0,
            }
            for s in range(STRINGS):
                pts[f"string_current_{s + 1}_a"] = (dc * 1000 / 1150 / STRINGS) * r.uniform(0.95, 1.04) if dc else 0.0
            out[did] = {k: (float(val), "good") for k, val in pts.items()}

        # the SAU itself
        if sau_ok:
            stopped = INVERTERS - len(online)
            sau = {
                "ac_power_kw": pv, "reactive_kvar": pv * 0.04, "power_factor": 0.99 if pv else 0.0,
                "energy_today_kwh": sum(self.pv_today), "energy_total_kwh": sum(self.pv_total_kwh),
                "inverters_total": INVERTERS, "inverters_online": INVERTERS if sun > 0 else 0,
                "inverters_working": len(online) if sun > 0.02 else 0, "max_power_kw": PV_RATED_KW,
                "max_reactive_kvar": 1056.0, "min_reactive_kvar": -1056.0, "available_kw": pv_avail,
                "available_reactive_kvar": 1056.0, "min_reactive_available_kvar": -1056.0,
                "inverters_stopped": stopped, "inverters_standby": 0 if sun > 0.02 else INVERTERS,
                "inverters_faulty": len(self.inv_down), "inverters_alarm": len(self.inv_down),
                "rated_capacity_kva": PV_RATED_KW, "frequency_hz": f, "comm_error": 0.0,
            }
            out["pv-array"] = {k: (float(val), "good") for k, val in sau.items()}
        else:
            out["pv-array"] = {"comm_error": (1.0, "good"), "ac_power_kw": (None, "bad")}

        # mains incomers: the load splits over three transformers
        split = [0.38, 0.27, 0.35]
        for m in range(3):
            pm = mains * split[m] * r.uniform(0.97, 1.03)
            lm = load * split[m] * r.uniform(0.97, 1.03)
            out[f"main-{m + 1:02d}"] = {k: (float(val), "good") for k, val in {
                "power_kw": pm, "mains_import_q_kvar": pm * 0.28, "mains_pf": 0.96 if grid_on else 0.0,
                "load_p_kw": lm, "load_q_kvar": lm * 0.3, "load_pf": 0.95,
                "battery_v": 27.0 + r.uniform(-0.2, 0.2), "available": 1.0 if grid_on else 0.0, "comm_error": 0.0,
            }.items()}
        return out


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS readings (ts TEXT NOT NULL, device_id TEXT NOT NULL, point_id TEXT NOT NULL,
       value REAL, text_value TEXT, quality TEXT NOT NULL, error TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_readings ON readings(device_id, point_id, ts)",
    "CREATE INDEX IF NOT EXISTS ix_readings_ts ON readings(ts)",
    """CREATE TABLE IF NOT EXISTS events (ts TEXT NOT NULL, kind TEXT NOT NULL, device_id TEXT, severity TEXT,
       code TEXT, message TEXT)""",
]


def write(db, plant: Plant, t: datetime, data):
    ts = iso(t)
    rows = [(ts, did, pid, val, None, q, None if q == "good" else "timeout")
            for did, pts in data.items() for pid, (val, q) in pts.items()]
    db.executemany("INSERT INTO readings VALUES (?,?,?,?,?,?,?)", rows)
    if plant.events:
        db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)", plant.events)
        plant.events.clear()


def publish(db):
    db.commit()
    tmp = OUT + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    d = sqlite3.connect(tmp)
    db.backup(d)
    d.execute("PRAGMA journal_mode=DELETE")
    d.close()
    for _ in range(10):                      # Docker may hold the old file for a moment
        try:
            os.replace(tmp, OUT)
            return
        except PermissionError:
            time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="write the history and exit")
    ap.add_argument("--days", type=int, default=HISTORY_DAYS)
    a = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(WORK):
        os.remove(WORK)
    db = sqlite3.connect(WORK)
    for s in SCHEMA:
        db.execute(s)
    plant = Plant()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    t = now - timedelta(days=a.days)
    t = t - timedelta(seconds=t.timestamp() % HISTORY_STEP_S)
    n = 0
    while t <= now:
        write(db, plant, t, plant.step(t, HISTORY_STEP_S))
        t += timedelta(seconds=HISTORY_STEP_S)
        n += 1
    plant.ev(now, "settings", "demo data generator started (simulated real plant)", None, "info", "DEMO")
    publish(db)
    print(f"[grafana-demo] {a.days} days of history ({n} polls) -> {OUT}", flush=True)
    if a.once:
        return
    print(f"[grafana-demo] live: a new poll every {LIVE_STEP_S} s (Ctrl+C stops)", flush=True)
    last = now
    while True:
        time.sleep(LIVE_STEP_S)
        t = datetime.now(timezone.utc).replace(microsecond=0)
        write(db, plant, t, plant.step(t, (t - last).total_seconds()))
        last = t
        # keep the demo file small: drop what is older than the history window
        db.execute("DELETE FROM readings WHERE ts < ?", (iso(t - timedelta(days=a.days)),))
        publish(db)


if __name__ == "__main__":
    main()
