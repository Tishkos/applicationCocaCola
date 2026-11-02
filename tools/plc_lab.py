"""
PLC lab: the plant and the master's PLC program running live in the browser, with every block of every
sheet shown with its live pin values, like the controller's own PLC monitor, but on a model you can poke.

    python tools/plc_lab.py                                  -> http://localhost:8090
    python tools/plc_lab.py --speed 10 --hour 8.8
    python tools/plc_lab.py --replay data/trace/neo-01_2026-10-16.csv [--replay-sau data/trace/pv-array_2026-10-16.csv]

Nothing here touches the plant.

What is exact: the PLC program (the exported sheets, executed block by block at the controller's 100 ms cycle),
its setpoints, the ECU refresh and timeout figures from the archive, the ramp constants, the load anchors and
the grid hours from the history, the sun curve for mid-October in Erbil.
What is modelled (marked ASSUMED in plant_model.py and adjustable on the page): how fast the inverters follow
a setpoint, the controller's curtailment regulator, what the SAU does with a write while it is not answering,
load noise, cloud edges. Replay mode removes most of that: the PLC then runs on the values the trace recorded
on the plant, and the page shows the recorded setpoint next to the one the program computes.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
import sys
import threading
import time
from collections import deque
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from neo_plc import Plc, load_program, plant_inputs  # noqa: E402
import plant_model as pm  # noqa: E402

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
except ImportError:                                     # the model part works without the web part
    FastAPI = Request = HTMLResponse = JSONResponse = None

CHANGE_DIR = os.path.join(ROOT, "docs", "changes", "change-01_comm_fail_tolerance")
PROGRAMS = {
    "today": (pm.program, {}),
    "change-01 v2 (hold until the link is back)": (lambda: load_program(os.path.join(CHANGE_DIR, "program_v2")), {65514: 300.0}),
    "change-01 v3 (hold 60 s, then 0)": (lambda: load_program(os.path.join(CHANGE_DIR, "program_v3")), {}),
}
HISTORY_S = 900
PLC_CYCLE_S = 0.1            # REAL: the controller's PLC cycle
ECU_REFRESH_S = 1.0          # REAL: the PV logger group 1 session refreshes its values every 1,000 ms (archive)
SESSION2_WRITE_S = 10.0      # REAL: group 2 writes its setpoints every 10,000 ms or on change (archive)

# knobs: the figures the trace will measure; until then these are the archive values and the model's assumptions
DEFAULT_PARAMS = dict(
    timeout_s=3.0,           # REAL: Modbus timeout of the PV logger sessions (archive)
    retries=2,               # ASSUMED: lost replies before the module declares the comm fail (the integrator: 6-9 s)
    inverter_tau_s=10.0,     # ASSUMED: first-order response of the inverters to a setpoint
    inverter_ramp_kw_s=5.0,  # CALIBRATED on 14 Oct: the PV climbs about 300 kW/min after a breaker close (0 = no limit); the trace will say
    curtail_rate=0.03,       # CALIBRATED: the controller's curtailment regulator, %/s per % of error
    gen_p_min_pct=25.0,      # REAL: Gen P Min
    load_noise=1.0,          # ASSUMED: slow random load variation, % of load
    cloud_edge_s=20.0,       # ASSUMED: a cloud edge takes this long to pass
    writes_through_silence=0,  # HYPOTHESIS: 1 = a write still lands in the SAU while it gives no replies
    rp_trip_pct=5.0,         # ASSUMED: IG4 reverse power protection, % of running nominal (the real setting is unread)
    rp_delay_s=5.0,          # ASSUMED: its delay
    load_events=1,           # ASSUMED: random production load steps on/off (chillers, compressors, lines)
    load_event_minutes=10.0, # ASSUMED: one such step every N minutes on average
    load_event_kw=400.0,     # ASSUMED: largest step, kW (sizes uniform up to this, either direction)
    ups_kw_each=300.0,       # ASSUMED: the load each UPS feeds, kW (what the plant has not yet told us; 0 = 5 % of the load)
    overload_trip_pct=110.0, # ASSUMED: genset overload protection, % of running nominal (IG4 unread)
    overload_delay_s=10.0,   # ASSUMED: its delay
    genset_start_s=30.0,     # ASSUMED: crank, warm-up and synchronisation before a started set takes load
)


# Scenarios: a program, how the gensets are run, and the model knobs. The first is the plant as it runs today.
SCENARIOS = [
    dict(name="Default: today's program, 3 gensets by hand", program="today", gensets=3, auto_pm=False,
         note="The plant as recorded on 14 October: gensets started by hand, Gen P Min 25 %, any comm fail forces the PV request to 0."),
    dict(name="Change 01 v3: hold 60 s, then 0", program="change-01 v3 (hold 60 s, then 0)", gensets=3, auto_pm=False,
         note="Sheet 10 keeps the last request for 60 s after a comm fail is declared, then 0 exactly as today. Nothing else changes."),
    dict(name="Change 01 v2: hold until the link is back, PV breakers open after 5 min", program="change-01 v2 (hold until the link is back)", gensets=3, auto_pm=False,
         note="Sheet 10 holds as long as the fail lasts; after 5 minutes without the logger sheet 3 opens the PV breakers (stage 2)."),
    dict(name="Power management AUTO, settings as they are", program="today", gensets=None, auto_pm=True,
         note="The master starts and stops gensets itself with the reserves and delays from the archive; today's program."),
    dict(name="AUTO + Gen P Min 20 %", program="today", gensets=None, auto_pm=True, params=dict(gen_p_min_pct=20.0),
         note="As above with the gensets allowed down to 20 % instead of 25 %: more solar, closer to the engine's floor."),
    dict(name="AUTO + change 01 v3 + UPS fixes", program="change-01 v3 (hold 60 s, then 0)", gensets=None, auto_pm=True,
         ups_math_fix=True, ups_shed_delay_s=10.0,
         note="Power management on, the comm-fail hold, the UPS sheet using the real genset nominal and a 10 s shedding delay (the model's S3)."),
    dict(name="Two gensets in the sun", program="change-01 v3 (hold 60 s, then 0)", gensets=2, auto_pm=False,
         note="Two sets by hand through the day with the comm-fail hold: the first real fuel step. Watch gen load % on clouds and fails."),
    dict(name="AUTO, two gensets minimum", program="change-01 v3 (hold 60 s, then 0)", gensets=None, auto_pm=True, min_gensets=2,
         note="Power management never below two sets (the model's S6): the safe version of the step above."),
    dict(name="One genset by day, the UPS as buffer, next set on reserve", program="change-01 v3 (hold 60 s, then 0)", gensets=1, auto_pm=True,
         min_gensets=1, ups_math_fix=True,
         note="One set carries the floor, the solar the rest. On a surge the set takes it; on a solar loss the PLC sheds the UPS to battery at 70/80 % gen load while power management starts the next set. Needs the UPS really connected and the comm-fail zero gone."),
    dict(name="Grid day with export", program="today", gensets=0, auto_pm=False, grid=True,
         note="On the grid: no gensets, the PV request is the SAU maximum capped by the export limit of 1,300 kW per feeder."),
]
SCENARIO_BY_NAME = {s["name"]: s for s in SCENARIOS}


class LivePlant:
    """plant_model.simulate(), unrolled into one-second ticks (ten PLC cycles each) with injectable events."""

    def __init__(self, program_name: str = "today", start_hour: float = 10.0, manual_gensets: int = 3, replay=None):
        self.lock = threading.RLock()
        self.replay = replay                               # ReplaySource or None
        self.start_hour = start_hour
        self.speed = 1.0
        self.paused = False
        self.scenario = SCENARIOS[0]["name"]
        self.program_name = program_name
        self.manual_gensets = manual_gensets
        self.auto_pm = False
        self.min_gensets = 1
        self.ups_math_fix = False
        self.ups_shed_delay_s = 0.0
        self.grid_override = None
        self.params = dict(DEFAULT_PARAMS)
        self._init_state()

    def _init_state(self):
        """Everything back to zero: a fresh PLC, a fresh plant, empty history and counters; the knobs stay."""
        self.sc = pm.Scenario("lab", manual_gensets=self.manual_gensets)
        self.day = pm.Day("lab day", clearness=1.0)
        self.t = self.replay.t0 if self.replay else self.start_hour * 3600.0
        self.plc = None
        self._new_plc(self.program_name)
        self.rnd = random.Random(14)
        self.running = self.manual_gensets or 0
        self.pv_actual = 0.0
        self.sau_value = 0.0                               # what the SAU would answer right now
        self.sau_reading = 0.0                             # the value the controller holds (refreshed every 1 s while answered)
        self.received_setpoint = 0.0                       # the last setpoint that reached the SAU
        self.curtail = 30.0
        self.pv_cb = [1, 1]
        self.ups_closed = [1, 1]                           # in normal operation both UPS breakers are closed
        self.fail_since = None
        self.last_transfer = -1e9
        self.warn_timer = 0.0
        self.load_offset = 0.0
        self.load_noise_kw = 0.0
        self.clouds = []                                   # (start, end, depth)
        self.silent_until = [-1.0, -1.0]                   # the SAU gives no replies on session i until t
        self.last_reply = [self.t, self.t]
        self.comm_flag = [0, 0]
        self.off_until = -1.0
        self.fault_reset_pending = False
        self.start_timer = self.stop_timer = 0
        self.starting_until = None
        self.ups_shed_timer = [0.0, 0.0]
        self.load_events_list = []                         # (start, end, kw): production steps, random or injected
        self.rp_timer = 0.0
        self.ol_timer = 0.0
        self.tripped_until = -1.0
        self.schedule = []                                 # (t, what, value) from the 14 October replay
        self.totals = dict(seconds=0, pv_used_kwh=0.0, pv_available_kwh=0.0, gen_kwh=0.0, fuel_l=0.0, comm_fails=0,
                           zero_setpoints=0, warnings=0, ups_sheds=0, min_gen_load_pct=None, max_gen_load_pct=0.0,
                           rp_seconds=0, rp_trips=0, blackout_s=0, overload_seconds=0, overload_trips=0)
        self._prev_comm_any = False
        # a steady start: the regulator already sits where it keeps the gensets at Gen P Min
        if not self.replay:
            load0 = pm.load_profile_kw(int(self.t / 60))
            avail0 = pm.pv_available_kw(self.t / 60, self.day, [])
            run_nom0 = 0.0 if self.on_grid() else sum(pm.GEN_NOMS_KW[:max(self.running, 1)])
            wanted = max(0.0, min(avail0, load0 - self.params["gen_p_min_pct"] / 100 * run_nom0)) if run_nom0 else min(avail0, load0)
            self.curtail = 0.0 if self.on_grid() else max(0.0, min(100.0, wanted / pm.SAU_MAX_AVAILABLE_KW * 100))
            self.pv_actual = self.sau_value = self.sau_reading = self.received_setpoint = wanted
        self.hist = {k: deque(maxlen=HISTORY_S) for k in ("t", "load", "pv_available", "pv_actual", "setpoint", "received", "request",
                                                            "gen_kw", "gen_load_pct", "comm", "silent", "recorded_setpoint", "rp_limit", "tripped", "mains")}
        self.events = deque(maxlen=200)
        self._last_bits = {}
        self.ticks = 0
        self.snapshot = {}
        self._warm_up()

    # ---- program -------------------------------------------------------------------------------
    def _new_plc(self, name: str):
        loader, setpoints = PROGRAMS[name]
        self.plc = Plc(loader(), setpoints, breaker_timeout_s=15)
        self.program_name = name

    def set_program(self, name: str):
        with self.lock:
            self._new_plc(name)
            self._warm_up()
            self.log(f"program: {name}")

    def _warm_up(self):
        for _ in range(30):
            self._tick(warm=True)

    def apply_scenario(self, name: str):
        sc = SCENARIO_BY_NAME[name]
        with self.lock:
            self.scenario = name
            self.program_name = sc["program"]
            self.manual_gensets = sc["gensets"] if sc["gensets"] is not None else 3
            self.auto_pm = bool(sc.get("auto_pm"))
            self.min_gensets = int(sc.get("min_gensets", 1))
            self.ups_math_fix = bool(sc.get("ups_math_fix"))
            self.ups_shed_delay_s = float(sc.get("ups_shed_delay_s", 0.0))
            self.grid_override = True if sc.get("grid") else None
            self.params = dict(DEFAULT_PARAMS)
            self.params.update(sc.get("params", {}))
            self._init_state()
            self.log(f"scenario: {name}")

    def reset(self):
        with self.lock:
            self._init_state()
            self.log("reset: plant and program restarted from zero")

    # ---- helpers ---------------------------------------------------------------------------------
    def log(self, text: str):
        self.events.appendleft((self._clock(), text))

    def _clock(self) -> str:
        s = int(self.t) % 86400
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"

    def on_grid(self) -> bool:
        if self.grid_override is not None:
            return self.grid_override
        return pm.grid_on(self.t / 60, self.day)

    def _cloud_factor(self) -> float:
        f = 1.0
        edge = max(1.0, self.params["cloud_edge_s"])
        for (a, b, depth) in self.clouds:
            if a - edge <= self.t <= b + edge:
                if self.t < a:
                    d = depth * (self.t - (a - edge)) / edge
                elif self.t > b:
                    d = depth * ((b + edge) - self.t) / edge
                else:
                    d = depth
                f *= (1 - d)
        return f

    def _sessions(self, dt: float):
        """The two PV logger sessions: a reply every second while the SAU answers; the comm fail flag of a session
        rises after timeout x (retries + 1) seconds without a reply and clears at the first reply."""
        declare_after = self.params["timeout_s"] * (self.params["retries"] + 1)
        for i in range(2):
            silent = self.t < self.silent_until[i]
            if not silent:
                self.last_reply[i] = self.t
            flag = int(self.t - self.last_reply[i] >= declare_after)
            self.comm_flag[i] = flag
        return [self.t < s for s in self.silent_until]

    # ---- one second ----------------------------------------------------------------------------------
    def _tick(self, warm: bool = False):
        for (ts, what, value) in [s for s in self.schedule if s[0] <= self.t]:
            self.schedule.remove((ts, what, value))
            self.inject(what, value, scheduled=True)
        sc, plc, P = self.sc, self.plc, self.params
        dt = 1.0
        if self.replay:
            self._tick_replay(warm)
            return
        minute = self.t / 60
        if P["load_noise"] > 0:
            self.load_noise_kw += self.rnd.gauss(0, 3) - 0.02 * self.load_noise_kw
        base_load = pm.load_profile_kw(int(minute))
        if P["load_events"] and not warm and self.rnd.random() < dt / max(60.0, 60 * P["load_event_minutes"]):
            kw = self.rnd.uniform(0.25, 1.0) * P["load_event_kw"] * self.rnd.choice((-1, 1))
            self.load_events_list.append((self.t, self.t + self.rnd.uniform(60, 600), kw))
            self.log(f"production step {kw:+.0f} kW")
        self.load_events_list = [e for e in self.load_events_list if e[1] + 30 > self.t]
        load = base_load + self.load_offset + self.load_noise_kw * P["load_noise"] + self._load_events_kw()
        avail = pm.pv_available_kw(minute, self.day, []) * self._cloud_factor()
        on_grid = self.on_grid()
        if self.grid_override is None and on_grid != pm.grid_on((self.t - dt) / 60, self.day):
            self.last_transfer = self.t
        if on_grid:
            self.running = 0
            run_nom = 0.0
        else:
            if self.running == 0:
                self.running = max(self.min_gensets, self.manual_gensets or 3)
            run_nom = sum(pm.GEN_NOMS_KW[:self.running])
        pvcb_close = int(not (self.t < self.off_until or self.t - self.last_transfer < 120))
        silent = self._sessions(dt)
        comm = list(self.comm_flag)
        comm_any = bool(comm[0] or comm[1])
        fault_reset = 0
        if self.fault_reset_pending or (self.fail_since is not None and self.t - self.fail_since >= 120):
            fault_reset, self.fail_since, self.fault_reset_pending = 1, None, False
        pv_live = bool(pvcb_close and any(self.pv_cb))
        if not pv_live:
            self.pv_actual = 0.0
        # the controller's copy of the SAU values refreshes once a second while session 1 answers; stale otherwise
        if not silent[0]:
            self.sau_reading = self.sau_value
        neo_pv_term = self.sau_reading if (pv_live or self.ups_math_fix) else 0.0
        ups_each = P["ups_kw_each"] if P["ups_kw_each"] > 0 else sc.ups_critical_share * load
        shed_load = sum(ups_each for i in range(2) if not self.ups_closed[i])
        load_on_bus = load - shed_load
        tripped = self.t < self.tripped_until
        if tripped:                                           # the gensets tripped on reverse power: the plant is dark
            self.pv_actual = 0.0
            gen_kw = 0.0
        else:
            gen_kw = 0.0 if on_grid else load_on_bus - self.pv_actual     # negative = reverse power into the gensets
        mains_each = (load_on_bus - self.pv_actual) / 3 if on_grid else 0.0
        if not on_grid and run_nom and gen_kw < -P["rp_trip_pct"] / 100 * run_nom:
            self.rp_timer += dt
            if not warm:
                self.totals["rp_seconds"] += dt
            if self.rp_timer >= P["rp_delay_s"] and not tripped and not warm:
                self.tripped_until = self.t + 180
                if not warm:
                    self.totals["rp_trips"] += 1
                self.log(f"REVERSE POWER TRIP: gensets at {gen_kw:.0f} kW for {P['rp_delay_s']:g} s, plant dark for 3 min")
                self.rp_timer = 0.0
        else:
            self.rp_timer = 0.0
        if not on_grid and run_nom and gen_kw > P["overload_trip_pct"] / 100 * run_nom and not tripped:
            self.ol_timer += dt
            if not warm:
                self.totals["overload_seconds"] += dt
            if self.ol_timer >= P["overload_delay_s"] and not warm:
                self.tripped_until = self.t + 180
                if not warm:
                    self.totals["overload_trips"] += 1
                self.log(f"OVERLOAD TRIP: gensets at {gen_kw:.0f} kW of {run_nom:.0f} for {P['overload_delay_s']:g} s, plant dark for 3 min")
                self.ol_timer = 0.0
        else:
            self.ol_timer = 0.0
        if tripped and not warm:
            self.totals["blackout_s"] += dt
        inputs = plant_inputs(pv_kw=self.sau_reading, max_avail_kw=pm.SAU_MAX_AVAILABLE_KW, curtailment_pct=self.curtail,
                              gensets_kw=gen_kw, running_nominal_kw=run_nom, mains_import_kw=(mains_each,) * 3,
                              on_grid=on_grid, gens_loaded=self.running, pv_cb=tuple(self.pv_cb), ups_fb=tuple(self.ups_closed),
                              comm_fail=(comm[0], comm[1]), pvcb_close=pvcb_close, fault_reset=fault_reset,
                              pv_valid=not comm_any, neo_pv_sample_kw=neo_pv_term)
        for _ in range(int(round(dt / PLC_CYCLE_S))):          # ten PLC cycles per second, inputs refreshed once a second
            plc.step(inputs, PLC_CYCLE_S)
        setpoint = plc.get(21267) or 0.0
        request = plc.get(21265) or 0.0
        cb_cmd = [plc.get(10425, 2) or 0, plc.get(10426, 2) or 0]
        ups_cmd = [plc.get(10427, 2) or 0, plc.get(10434, 2) or 0]
        self.warn_timer = self.warn_timer + dt if plc.get(10433, 2) else 0.0
        if any(plc.get(n, b) for n, b in ((10425, 7), (10426, 0), (10426, 7), (10427, 0), (10427, 7), (10428, 0), (10434, 7), (10435, 0))):
            self.fail_since = self.fail_since if self.fail_since is not None else self.t
        self.pv_cb = cb_cmd
        for i in range(2):                                   # the UPS breakers follow the commands, shedding after the optional delay
            if ups_cmd[i]:
                self.ups_shed_timer[i] = 0.0
                self.ups_closed[i] = 1
            else:
                self.ups_shed_timer[i] += dt
                if self.ups_shed_timer[i] >= self.ups_shed_delay_s:
                    if self.ups_closed[i] and not warm:
                        self.totals["ups_sheds"] += 1
                    self.ups_closed[i] = 0
        # the setpoint reaches the SAU through session 1 every second, through session 2 every 10 s, only while
        # that session gets replies (unless the hypothesis switch says writes land anyway)
        through = P["writes_through_silence"] or (not silent[0]) or (not silent[1] and int(self.t) % int(SESSION2_WRITE_S) == 0)
        if through:
            self.received_setpoint = setpoint
        # the inverters: first-order response to the setpoint they received, with an optional rate limit
        if pvcb_close and any(self.pv_cb):
            target = min(avail, self.received_setpoint)
            step = (target - self.pv_actual) * (1 - math.exp(-dt / max(0.5, P["inverter_tau_s"])))
            lim = P["inverter_ramp_kw_s"] * dt
            if lim > 0:
                step = max(-lim, min(lim, step))
            self.pv_actual += step
        else:
            self.pv_actual = 0.0
        self.sau_value = self.pv_actual
        if not on_grid and run_nom:
            gen_load_now = max(0.0, load_on_bus - self.pv_actual) / run_nom * 100
            if pv_live and not comm_any:                     # the regulator does not wind up while the PV is off or unseen
                self.curtail = max(0.0, min(100.0, self.curtail + P["curtail_rate"] * (gen_load_now - P["gen_p_min_pct"]) * dt))
            else:                                            # (14 Oct: after a breaker close the request restarts low, 09:29:21)
                self.curtail = min(self.curtail, max(0.0, self.pv_actual) / pm.SAU_MAX_AVAILABLE_KW * 100)
        elif on_grid:
            self.curtail = 0.0
        if self.auto_pm and not on_grid:
            self._power_management(run_nom, gen_kw, dt)
        if warm:
            return
        self.t += dt
        self.ticks += 1
        gl = 100 * gen_kw / run_nom if run_nom else 0.0
        self._totals(dt, avail, gen_kw, run_nom, gl, comm)
        self._record(load, avail, setpoint, request, gen_kw, gl, comm_any, any(silent), None, run_nom, mains_each * 3 if on_grid else None)
        self.snapshot = dict(clock=self._clock(), load=round(load), pv_available=round(avail), pv_actual=round(self.pv_actual),
                             setpoint=round(setpoint, 1), received=round(self.received_setpoint, 1), request=round(request, 1),
                             gen_kw=round(gen_kw), run_nom=round(run_nom), gen_load_pct=round(gl, 1), gensets=self.running,
                             on_grid=on_grid, curtail=round(self.curtail, 1), pv_cb=self.pv_cb, ups=self.ups_closed, comm=comm,
                             silent=[int(s) for s in silent], pvcb_close=pvcb_close, warning=int(bool(plc.get(10433, 2))),
                             latch=int(bool(plc.get(10424, 4))), replay=False, tripped=int(tripped),
                             mains_kw=round(mains_each * 3) if on_grid else None,
                             rp_limit_kw=round(-P["rp_trip_pct"] / 100 * run_nom) if run_nom else None,
                             load_events=[(round(kw), round(max(0.0, b - self.t))) for (a, b, kw) in self.load_events_list if a <= self.t <= b])
        self.snapshot["totals"] = self._totals_view()
        self._edges(comm)

    def _totals_view(self):
        T = self.totals
        return dict(hours=round(T["seconds"] / 3600, 2), pv_used_kwh=round(T["pv_used_kwh"]), pv_available_kwh=round(T["pv_available_kwh"]),
                    pv_curtailed_kwh=round(max(0.0, T["pv_available_kwh"] - T["pv_used_kwh"])), gen_kwh=round(T["gen_kwh"]),
                    fuel_l=round(T["fuel_l"]), comm_fails=T["comm_fails"], zero_setpoints=T["zero_setpoints"], warnings=T["warnings"],
                    ups_sheds=T["ups_sheds"], min_gen_load_pct=None if T["min_gen_load_pct"] is None else round(T["min_gen_load_pct"], 1),
                    max_gen_load_pct=round(T["max_gen_load_pct"], 1), rp_seconds=round(T["rp_seconds"]), rp_trips=T["rp_trips"],
                    blackout_min=round(T["blackout_s"] / 60, 1), overload_seconds=round(T["overload_seconds"]), overload_trips=T["overload_trips"])

    def _load_events_kw(self) -> float:
        total = 0.0
        for (a, b, kw) in self.load_events_list:
            if a <= self.t <= b:
                ramp = 15.0
                f = min(1.0, (self.t - a) / ramp, max(0.0, (b - self.t) / ramp) if b - self.t < ramp else 1.0)
                total += kw * f
        return total

    def _totals(self, dt, avail, gen_kw, run_nom, gl, comm):
        T = self.totals
        T["seconds"] += dt
        T["pv_used_kwh"] += self.pv_actual * dt / 3600
        T["pv_available_kwh"] += avail * dt / 3600
        T["gen_kwh"] += gen_kw * dt / 3600
        if run_nom and self.running:
            for i in range(self.running):                     # load shared in proportion to nominal
                share = pm.GEN_NOMS_KW[i] / run_nom
                T["fuel_l"] += pm.fuel_l_per_min(gen_kw * share, pm.GEN_NOMS_KW[i]) * dt / 60
            T["min_gen_load_pct"] = gl if T["min_gen_load_pct"] is None else min(T["min_gen_load_pct"], gl)
            T["max_gen_load_pct"] = max(T["max_gen_load_pct"], gl)
        if self.warn_timer == 15:                             # the "AHI" alarm has its own 15 s delay
            T["warnings"] += 1
        if any(comm) and not self._prev_comm_any:
            T["comm_fails"] += 1
        self._prev_comm_any = bool(any(comm))
        sp = self.hist["setpoint"]
        if len(sp) > 1 and sp[-1] == 0 and sp[-2] > 0:
            T["zero_setpoints"] += 1

    _prev_comm_any = False

    def _power_management(self, run_nom, gen_kw, dt):
        """The master's power management as the archive sets it (start/stop reserves and delays, REAL) plus the
        time a started set needs before it carries load (ASSUMED genset_start_s)."""
        sc = self.sc
        if self.starting_until is not None:
            if self.t >= self.starting_until:
                self.running += 1; self.starting_until = None; self.log(f"power management: genset {self.running} on the bus")
            return
        reserve = sc.start_reserve_kw + sc.solar_coverage * self.pv_actual
        if run_nom - gen_kw < reserve and self.running < pm.GENSETS:
            self.start_timer += dt
            if self.start_timer >= sc.next_start_delay_s:
                self.starting_until = self.t + self.params["genset_start_s"]; self.start_timer = 0
                self.log(f"power management: start request for genset {self.running + 1}, on the bus in {self.params['genset_start_s']:g} s")
        else:
            self.start_timer = 0
        if self.running > self.min_gensets and sum(pm.GEN_NOMS_KW[:self.running - 1]) - gen_kw >= sc.stop_reserve_kw + sc.solar_coverage * self.pv_actual \
                and gen_kw >= sc.min_run_power_kw:
            self.stop_timer += dt
            if self.stop_timer >= sc.next_stop_delay_s:
                self.log(f"power management: genset {self.running} stopped"); self.running -= 1; self.stop_timer = 0
        else:
            self.stop_timer = 0

    def _record(self, load, avail, setpoint, request, gen_kw, gl, comm_any, silent_any, recorded_setpoint, run_nom=0.0, mains_kw=None):
        rp = -self.params["rp_trip_pct"] / 100 * run_nom if run_nom else None
        self.hist["mains"].append(None if mains_kw is None else round(mains_kw))
        for k, v in (("t", self._clock()), ("load", round(load)), ("pv_available", round(avail)), ("pv_actual", round(self.pv_actual)),
                     ("setpoint", round(setpoint)), ("received", round(self.received_setpoint)), ("request", round(request)),
                     ("gen_kw", round(gen_kw)), ("gen_load_pct", round(gl, 1)), ("comm", int(comm_any)), ("silent", int(silent_any)),
                     ("recorded_setpoint", recorded_setpoint), ("rp_limit", None if rp is None else round(rp)),
                     ("tripped", int(self.t < self.tripped_until))):
            self.hist[k].append(v)

    def _edges(self, comm):
        plc = self.plc
        for name, key in (("PV logger group 1 comm fail", ("comm", 0)), ("PV logger group 2 comm fail", ("comm", 1)),
                          ("PV prod > PV req warning", (10433, 2)), ("PV CB1 ON command", (10425, 2)), ("UPS1 ON command", (10427, 2)),
                          ("UPS2 ON command", (10434, 2)), ("PV comm lost", (10436, 2))):
            v = comm[key[1]] if key[0] == "comm" else int(bool(plc.get(*key)))
            old = self._last_bits.get(name)
            if old is not None and old != v:
                self.log(f"{name}: {'on' if v else 'off'}")
            self._last_bits[name] = v
        sp = self.hist["setpoint"]
        if len(sp) > 1 and sp[-1] == 0 and sp[-2] > 0:
            self.log("PV setpoint forced to 0")

    # ---- replay of a recorded trace --------------------------------------------------------------------
    def _tick_replay(self, warm: bool):
        row = self.replay.row_at(self.t)
        if row is None:
            self.paused = True
            return
        g = self.replay.get
        pv = g(row, "pv_prod_kw", 0.0)
        gen_kw = g(row, "power_kw", 0.0)
        run_nom = g(row, "running_nominal_kw", 0.0)
        curtail = g(row, "pv_curtailment_pct", 0.0)
        mains = g(row, "mains_power_kw", 0.0)
        comm = [int(g(row, "ecu2_comm_fail", 0)), int(g(row, "ecu3_comm_fail", 0))]
        on_grid = bool(g(row, "mcb_status", 0))
        pv_cb = (int(g(row, "pvcb_closed", 1)), int(g(row, "pv2cb_closed", 1)))
        ups = (int(g(row, "ups1_feedback", 0)), int(g(row, "ups2_feedback", 0)))
        gens = sum(int(g(row, f"g{i}_running", 0)) for i in range(1, 6))
        inputs = plant_inputs(pv_kw=pv, max_avail_kw=pm.SAU_MAX_AVAILABLE_KW, curtailment_pct=curtail, gensets_kw=gen_kw,
                              running_nominal_kw=run_nom, mains_import_kw=(mains / 3,) * 3, on_grid=on_grid, gens_loaded=gens,
                              pv_cb=pv_cb, ups_fb=ups, comm_fail=(comm[0], comm[1]), pvcb_close=int(g(row, "pv_run_request", 1)),
                              pv_valid=not (comm[0] or comm[1]), neo_pv_sample_kw=None)
        for _ in range(int(round(1.0 / PLC_CYCLE_S))):
            self.plc.step(inputs, PLC_CYCLE_S)
        setpoint = self.plc.get(21267) or 0.0
        request = self.plc.get(21265) or 0.0
        self.pv_actual = pv
        self.received_setpoint = setpoint
        self.running = gens
        self.curtail = curtail
        recorded = self.replay.recorded_setpoint(self.t)
        if warm:
            return
        self.t += 1.0
        self.ticks += 1
        gl = 100 * gen_kw / run_nom if run_nom else 0.0
        load = gen_kw + pv + max(0.0, mains)
        self._record(load, pv, setpoint, request, gen_kw, gl, bool(comm[0] or comm[1]), False, recorded, run_nom, mains if on_grid else None)
        self.snapshot = dict(clock=self._clock(), load=round(load), pv_available=None, pv_actual=round(pv), setpoint=round(setpoint, 1),
                             received=recorded, request=round(request, 1), gen_kw=round(gen_kw), run_nom=round(run_nom),
                             gen_load_pct=round(gl, 1), gensets=gens, on_grid=on_grid, curtail=round(curtail, 1), pv_cb=list(pv_cb),
                             ups=list(ups), comm=comm, silent=[0, 0], pvcb_close=1, warning=int(bool(self.plc.get(10433, 2))), mains_kw=round(mains) if on_grid else None,
                             latch=int(bool(self.plc.get(10424, 4))), replay=True, replay_file=self.replay.name, totals=self._totals_view())
        self._edges(comm)

    # ---- the thread ---------------------------------------------------------------------------------
    def run(self, stop: threading.Event):
        while not stop.is_set():
            if self.paused:
                time.sleep(0.1)
                continue
            t0 = time.perf_counter()
            with self.lock:
                self._tick()
            wait = 1.0 / self.speed - (time.perf_counter() - t0)
            if wait > 0:
                time.sleep(wait)

    # ---- injections ------------------------------------------------------------------------------------
    def inject(self, what: str, value: float = 0, scheduled: bool = False):
        with self.lock:
            if what in ("comm1", "comm2", "comm12"):          # the SAU stops answering that session for N seconds
                n = value or 10
                if what != "comm2":
                    self.silent_until[0] = self.t + n
                if what != "comm1":
                    self.silent_until[1] = self.t + n
            elif what == "cloud":
                self.clouds.append((self.t, self.t + (value or 120), 0.5))
                self.clouds = [c for c in self.clouds if c[1] + 60 > self.t]
            elif what == "load":
                self.load_offset += value
            elif what == "load_event":                       # a production step of value kW for 3 minutes
                self.load_events_list.append((self.t, self.t + 180, value))
            elif what in ("integrator_both", "integrator_one"):   # his example: 1 MW of solar, link lost, load drops at the same time
                n = 60.0
                self.silent_until[0] = self.t + n
                if what == "integrator_both":
                    self.silent_until[1] = self.t + n
                self.load_events_list.append((self.t + 12, self.t + 12 + 180, -(value or 800)))
            elif what == "off":
                self.off_until = self.t + (value or 60)
            elif what == "grid":
                self.grid_override = not self.on_grid()
            elif what == "fault_reset":
                self.fault_reset_pending = True
            elif what == "speed":
                self.speed = max(0.2, min(120.0, value))
            elif what == "pause":
                self.paused = not self.paused
            elif what == "auto_pm":
                self.auto_pm = not self.auto_pm
            elif what == "gensets":
                self.running = int(value); self.manual_gensets = int(value); self.sc.manual_gensets = int(value)
            elif what == "reset":
                self._init_state()
            elif what == "time":
                self.t = float(value) * 3600
                self.last_reply = [self.t, self.t]
            elif what == "replay14":
                self.t = 8 * 3600.0
                self.last_reply = [self.t, self.t]
                self.schedule = []
                for (m0, minutes, kind) in pm.INTERRUPTIONS_14_OCT:
                    if kind == "off":
                        self.schedule.append((m0 * 60.0, "off", minutes * 60.0))
                    else:                                   # a burst: the SAU silent for 10 s every 30 s, as the history reads
                        for k in range(minutes * 2):
                            self.schedule.append((m0 * 60.0 + k * 30.0, "comm12", 10.0))
                self.log("14 October: the recorded toggles and comm-fail bursts are scheduled from 08:00")
            elif what.startswith("param:"):
                key = what[6:]
                if key in self.params:
                    self.params[key] = float(value)
            if not scheduled:
                self.log(f"{what} {value:g}" if value else what)

    # ---- the sheet view ---------------------------------------------------------------------------------
    def sheet_view(self, index: int) -> dict:
        prog, plc = self.plc.prog, self.plc
        sheet = next((s for s in prog.sheets if s.index == index), prog.sheets[0])
        produced = {}
        blocks = []
        for b in sheet.blocks:
            ins = [p for p in b.inputs if p.connected or p.raw_const is not None]
            outs = [p for p in b.outputs if p.connected]
            bid = f"b{b.order}"
            for j, o in enumerate(outs):
                produced[o.key] = (bid, j)
            blocks.append(dict(id=bid, type=b.type_name, x=b.x, y=b.y,
                               inputs=[dict(name=prog.name_of(p) if p.connected else (p.name or "const"), value=_fmt(plc._get(p)),
                                            neg=p.negated, const=p.raw_const is not None, key=_key(p)) for p in ins],
                               outputs=[dict(name=prog.name_of(p), value=_fmt(plc.values.get(p.key)), key=_key(p)) for p in outs]))
        wires = []
        for b, blk in zip(sheet.blocks, blocks):
            for i, p in enumerate([q for q in b.inputs if q.connected or q.raw_const is not None]):
                if p.connected and p.key in produced:
                    src, j = produced[p.key]
                    wires.append(dict(frm=src, out=j, to=blk["id"], inp=i))
        return dict(index=sheet.index, name=sheet.name, blocks=blocks, wires=wires,
                    sheets=[dict(index=s.index, name=s.name) for s in prog.sheets])


class ReplaySource:
    """A trace file of the master (plantcollector trace, neo-01_<date>.csv) and optionally of the SAU array
    (pv-array_<date>.csv, for the recorded setpoint words). Rows are looked up by second of day."""

    def __init__(self, neo_csv: str, sau_csv: str | None = None):
        self.name = os.path.basename(neo_csv)
        self.rows = {}
        with open(neo_csv, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.rows[self._sec(r["ts"])] = r
        if not self.rows:
            raise ValueError(f"no rows in {neo_csv}")
        self.t0 = float(min(self.rows))
        self.sau = {}
        if sau_csv:
            with open(sau_csv, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    self.sau[self._sec(r["ts"])] = r

    @staticmethod
    def _sec(ts: str) -> int:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return d.hour * 3600 + d.minute * 60 + d.second

    def row_at(self, t: float):
        s = int(t) % 86400
        for k in (s, s - 1, s + 1, s - 2):
            if k in self.rows:
                return self.rows[k]
        return None

    @staticmethod
    def get(row, key, default):
        v = row.get(key, "")
        if v in ("", None):
            return default
        try:
            return float(v)
        except ValueError:
            return default

    def recorded_setpoint(self, t: float):
        """AP Set as the group 1 session writes it: 32-bit at 2100-2101, x0.1 kW."""
        s = int(t) % 86400
        r = self.sau.get(s) or self.sau.get(s - 1)
        if not r:
            return None
        try:
            return round(((int(float(r["set_2100"])) << 16) | int(float(r["set_2101"]))) * 0.1, 1)
        except (KeyError, ValueError):
            return None


def _fmt(v):
    if v is None:
        return "####"
    if isinstance(v, float):
        return f"{v:.1f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:.0f}"
    return str(v)


def _key(p):
    return f"{p.comobj}.{p.bit}" if p.is_binary else str(p.comobj)


# ---- web ------------------------------------------------------------------------------------------------
PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>PLC lab</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{--bg:#eef0ea;--panel:#ffffff;--ink:#1b2027;--muted:#6b7480;--rule:#d9dcd6;--soft:#f6f7f3;
 --load:#1b2027;--avail:#a7adb5;--solar:#2f8f46;--set:#2457c5;--rec:#7b3fa0;--gen:#c03a2b;--grid:#0f7f8c;--amber:#b8860b;--ok:#2f8f46;
 --sans:'IBM Plex Sans','Segoe UI',system-ui,sans-serif;--mono:'IBM Plex Mono',Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 var(--sans)}
header{display:flex;align-items:center;gap:18px;padding:10px 20px;background:var(--panel);border-bottom:1px solid var(--rule);position:sticky;top:0;z-index:5}
header h1{font-size:17px;font-weight:600;margin:0;letter-spacing:.2px}
header .sc{color:var(--muted);font-size:13px;max-width:420px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pill{display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:500;background:var(--soft);border:1px solid var(--rule)}
.pill.ok{color:var(--ok);border-color:#bfe0c6;background:#eef8f0}.pill.silent{color:var(--amber);border-color:#ead9a6;background:#fbf5e3}
.pill.fail{color:#fff;background:var(--amber);border-color:var(--amber)}.pill.trip{color:#fff;background:var(--gen);border-color:var(--gen)}
.clock{font:500 20px var(--mono);margin-left:auto}
.spd button{min-width:40px}
.wrap{display:grid;grid-template-columns:330px 1fr;gap:14px;padding:14px 20px}
@media(max-width:1100px){.wrap{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:6px}
.card h2{font-size:12px;font-weight:600;color:var(--muted);margin:0;padding:10px 14px 6px;letter-spacing:.3px}
.controls .group{padding:4px 14px 12px;border-bottom:1px solid var(--rule)}
.controls .group:last-child{border-bottom:0}
.controls .row{display:flex;flex-wrap:wrap;gap:6px}
button,select,input{font:13px var(--sans);padding:5px 10px;border:1px solid var(--rule);background:var(--soft);color:var(--ink);border-radius:4px;cursor:pointer}
button:hover{border-color:#b9bfb8;background:#fff}
button.on{background:var(--set);color:#fff;border-color:var(--set)}
button.danger{border-color:#e4b1aa;background:#fbefed;color:#8a2a1f}
button.wide{width:100%;text-align:left}
select{width:100%;background:#fff}
input{width:70px;cursor:text;background:#fff;font-family:var(--mono)}
.knob{display:grid;grid-template-columns:1fr 76px;align-items:center;gap:6px;padding:3px 0;font-size:12px;color:var(--muted)}
.knob small{display:block;color:#9aa1aa;font-size:11px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:8px;margin-bottom:14px}
.kpi{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:8px 10px}
.kpi span{display:block;font-size:11px;color:var(--muted)}
.kpi strong{font:500 20px var(--mono);display:block;margin-top:2px}
.kpi strong i{font:12px var(--sans);color:var(--muted);font-style:normal;margin-left:3px}
.kpi.solar strong{color:var(--solar)}.kpi.set strong{color:var(--set)}.kpi.gen strong{color:var(--gen)}.kpi.grid strong{color:var(--grid)}
.chartcard{padding:0 0 8px;margin-bottom:14px;position:relative}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;padding:8px 14px 4px;font-size:12px;color:var(--muted)}
.legend span{cursor:pointer;display:inline-flex;align-items:center;gap:6px;user-select:none}
.legend span.off{opacity:.35}
.legend i{display:inline-block;width:18px;height:0;border-top:3px solid currentColor}
.legend i.dash{border-top-style:dashed}
.legend b{display:inline-block;width:14px;height:12px;border-radius:2px}
#chart{width:100%;height:260px;display:block;cursor:crosshair}
#tip{position:absolute;pointer-events:none;display:none;background:#1b2027;color:#fff;font:500 13px var(--sans);padding:7px 11px;border-radius:5px;line-height:1.45;box-shadow:0 4px 14px rgba(0,0,0,.25);white-space:nowrap}
#tip b{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px}
#tip .t{font:12px var(--mono);color:#c9ced6}
.note{padding:6px 14px 2px;color:var(--muted);font-size:13px}
.totals{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0;padding:4px 8px 10px}
.totals div{padding:6px 8px}
.totals span{display:block;font-size:11px;color:var(--muted)}
.totals strong{font:500 16px var(--mono)}
.sheetcard{margin-top:14px}
.tabs{display:flex;flex-wrap:wrap;gap:4px;padding:6px 14px;border-bottom:1px solid var(--rule);align-items:center}
.tabs button{font-size:12px;padding:3px 8px}
.tabs button.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.tabs .zoom{margin-left:auto;display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)}
.sheetwrap{display:grid;grid-template-columns:1fr 290px}
@media(max-width:1100px){.sheetwrap{grid-template-columns:1fr}}
#sheet{overflow:auto;min-height:560px;max-height:75vh;background:#fcfcfa}
#sheet svg{transform-origin:0 0}
svg text{font:11px var(--sans)}
.blk{fill:#fff;stroke:#2a313a;stroke-width:1}
.blkhdr{fill:#eef0ea}
.pin1{fill:var(--set);font-weight:600}.pin0{fill:#9aa1aa}.pinv{fill:var(--ink);font-family:var(--mono)}
.wire{fill:none;stroke:var(--set);stroke-width:1.3;opacity:.65}
.wire:hover{opacity:1;stroke-width:2.4}
.hdr{font-weight:600}
.events{border-left:1px solid var(--rule);padding:8px 12px;font-size:12px;overflow:auto;max-height:75vh}
.events div{padding:3px 0;border-bottom:1px dotted var(--rule)}
.events time{font-family:var(--mono);color:var(--muted);margin-right:6px}
.events .bad{color:var(--gen);font-weight:600}.events .warn{color:var(--amber)}
.replay{color:var(--amber);font-weight:500}
details summary{cursor:pointer;color:var(--muted);font-size:12px;padding:4px 0}
</style></head><body>
<header><h1>PLC lab</h1><span class="sc" id="scname"></span><span class="pill" id="status">–</span><span class="replay" id="replay"></span>
<span class="clock" id="clock">--:--:--</span>
<span class="spd"><button onclick="ctl('pause')" id="pause">pause</button><button onclick="ctl('speed',1)" id="s1">1x</button><button onclick="ctl('speed',10)" id="s10">10x</button><button onclick="ctl('speed',60)" id="s60">60x</button></span>
</header>
<div class="wrap">
<aside class="card controls">
 <div class="group"><h2>Scenario</h2><select id="scenario"></select>
  <div class="row" style="margin-top:6px"><button class="wide" onclick="ctl('reset')">Reset: start again from zero</button></div>
  <div class="knob" style="margin-top:6px"><span>program</span></div><select id="program"></select></div>
 <div class="group"><h2>Link to the SAU</h2><div class="row">
  <button onclick="ctl('comm12',10)">silent 10 s</button><button onclick="ctl('comm12',30)">30 s</button><button onclick="ctl('comm12',90)">90 s</button><button onclick="ctl('comm12',400)">400 s</button>
  <button onclick="ctl('comm1',30)">group 1 only, 30 s</button><button onclick="ctl('comm2',30)">group 2 only, 30 s</button></div>
  <h2 style="padding-left:0">The integrator's example</h2><div class="row">
  <button class="danger" onclick="ctl('integrator_both',800)">link lost, load drops 800 kW</button>
  <button class="danger" onclick="ctl('integrator_one',800)">same, one session alive</button>
  <button class="danger" onclick="ctl('integrator_both',1200)">link lost, load drops 1,200 kW</button></div></div>
 <div class="group"><h2>Plant</h2><div class="row">
  <button onclick="ctl('cloud',120)">cloud 50 %, 2 min</button><button onclick="ctl('cloud',600)">cloud 10 min</button>
  <button onclick="ctl('load_event',300)">line starts +300 kW</button><button onclick="ctl('load_event',-300)">line stops -300 kW</button>
  <button onclick="ctl('load',300)">base load +300</button><button onclick="ctl('load',-300)">-300</button>
  <button onclick="ctl('gensets',1)">1 set</button><button onclick="ctl('gensets',2)">2 sets</button><button onclick="ctl('gensets',3)">3 sets</button><button onclick="ctl('gensets',4)">4 sets</button>
  <button onclick="ctl('auto_pm')" id="auto_pm">power management AUTO</button><button onclick="ctl('grid')" id="gridbtn">switch to the grid</button></div>
  <div class="note" style="padding:6px 0 0">The grid is there from 18:05 to 06:12 as in the history; by day the plant runs on gensets. The button forces a transfer.</div></div>
 <div class="group"><h2>Operator</h2><div class="row">
  <button onclick="ctl('off',60)">OFF toggle 60 s</button><button onclick="ctl('fault_reset')">fault reset</button>
  <button onclick="ctl('time',8.8)">go to 08:48</button><button onclick="ctl('time',12)">12:00</button><button onclick="ctl('time',16.5)">16:30</button><button onclick="ctl('time',18.0)">18:00 (grid at 18:05)</button>
  <button onclick="ctl('replay14')">replay 14 October from 08:00</button></div></div>
 <div class="group"><h2>Settings</h2><details open><summary>what is measured, what is assumed</summary><div id="params"></div></details></div>
</aside>
<main>
 <div class="kpis" id="kpis"></div>
 <div class="card chartcard">
  <div class="legend" id="legend"></div>
  <canvas id="chart"></canvas><div id="tip"></div>
  <div class="note" id="note"></div>
 </div>
 <div class="card"><h2>Since the start</h2><div class="totals" id="totals"></div></div>
 <div class="card sheetcard"><div class="tabs" id="tabs"></div>
  <div class="sheetwrap"><div id="sheet"></div><div class="events" id="events"></div></div></div>
</main></div>
<script>
let sheet=10, state=null, zoom=1, hover=null;
const SERIES=[['load','load','--load',1,[]],['pv_available','solar available','--avail',1,[]],['pv_actual','solar actual','--solar',2.2,[]],
 ['setpoint','setpoint computed by the program','--set',1.4,[]],['received','setpoint the SAU holds','--set',1.2,[5,4]],['recorded_setpoint','setpoint recorded on site','--rec',1.6,[]],
 ['gen_kw','gensets','--gen',1.8,[]],['rp_limit','reverse power trip level (assumed)','--gen',1,[2,4]],['mains','grid, import + / export −','--grid',1.8,[]]];
const hidden=new Set(['rp_limit'===''?'':'']);
const PARAMS=[['timeout_s','Modbus timeout, s','archive'],['retries','retries before the comm fail','assumed, 6-9 s in all'],['inverter_tau_s','inverter response, s','assumed'],
 ['inverter_ramp_kw_s','SAU ramp limit, kW/s (0 = none)','calibrated 14 Oct, climbs'],['curtail_rate','regulator, %/s per % of error','calibrated 14 Oct'],['gen_p_min_pct','Gen P Min, %','setpoint'],
 ['rp_trip_pct','reverse power trip, % of nominal','assumed, IG4 unread'],['rp_delay_s','reverse power delay, s','assumed'],
 ['load_events','random production steps (0/1)','assumed'],['load_event_minutes','one step every N minutes','assumed'],['load_event_kw','largest step, kW','assumed'],
 ['genset_start_s','genset start to load, s','assumed'],['ups_kw_each','load each UPS feeds, kW','assumed, plant to confirm'],['overload_trip_pct','genset overload trip, % of nominal','assumed, IG4 unread'],['overload_delay_s','overload delay, s','assumed'],
 ['load_noise','load noise x','assumed'],['cloud_edge_s','cloud edge, s','assumed'],['writes_through_silence','writes land while silent (0/1)','hypothesis']];
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v.replace('--','--')).trim();
async function ctl(what,value){await fetch('/api/control',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({what,value:value||0})});}
async function poll(){ try{const r=await fetch('/api/state?sheet='+sheet); state=await r.json(); render();}catch(e){} setTimeout(poll,300); }
function kpi(label,val,unit,cls){return `<div class="kpi ${cls||''}"><span>${label}</span><strong>${val===null||val===undefined?'–':val}<i>${unit||''}</i></strong></div>`;}
function render(){
  const s=state.plant; if(!s.clock) return;
  document.getElementById('clock').textContent=s.clock;
  document.getElementById('scname').textContent=state.scenario;
  const st=document.getElementById('status'); let cls='ok',txt=(s.on_grid?'on the grid':'on gensets')+', link OK';
  if(s.silent&&(s.silent[0]||s.silent[1])){cls='silent';txt='SAU silent '+(s.silent[0]?'g1 ':'')+(s.silent[1]?'g2':'');}
  if(s.comm[0]||s.comm[1]){cls='fail';txt='COMM FAIL '+(s.comm[0]?'g1 ':'')+(s.comm[1]?'g2':'');}
  if(s.tripped){cls='trip';txt='GENSETS TRIPPED, plant dark';}
  st.className='pill '+cls; st.textContent=txt;
  document.getElementById('pause').textContent=state.paused?'play':'pause';
  for(const k of [1,10,60]) document.getElementById('s'+k).className=(state.speed==k)?'on':'';
  document.getElementById('auto_pm').className=state.auto_pm?'on':'';
  document.getElementById('gridbtn').textContent=s.on_grid?'switch to gensets':'switch to the grid';
  document.getElementById('replay').textContent=s.replay?('replay of '+s.replay_file):'';
  document.getElementById('kpis').innerHTML=kpi('load',s.load,'kW')+kpi('solar available',s.pv_available,'kW')+kpi('solar actual',s.pv_actual,'kW','solar')+
    kpi('setpoint computed',s.setpoint,'kW','set')+kpi('setpoint at the SAU',s.received,'kW','set')+kpi('gensets',s.gen_kw,'kW','gen')+kpi('gen load',s.gen_load_pct,'%','gen')+
    kpi('sets / nominal',s.gensets+' / '+s.run_nom,'kW')+kpi(s.on_grid?'grid, import + / export −':'grid',s.on_grid?s.mains_kw:'off, MCBs open',s.on_grid?'kW':'','grid')+kpi('curtailment',s.curtail,'%')+kpi('PV breakers / UPS',s.pv_cb.join('')+' / '+s.ups.join(''),'')+
    kpi('reverse power trip at',s.rp_limit_kw,'kW')+kpi('warning',s.warning?'PV prod > req':'none','');
  const sc=document.getElementById('scenario');
  if(sc.options.length==0){for(const x of state.scenarios){const o=document.createElement('option');o.value=x.name;o.textContent=x.name;sc.appendChild(o);} sc.onchange=()=>fetch('/api/scenario',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:sc.value})});}
  if(sc.value!=state.scenario) sc.value=state.scenario;
  const sel=document.getElementById('program');
  if(sel.options.length==0){for(const p of state.programs){const o=document.createElement('option');o.value=p;o.textContent=p;sel.appendChild(o);} sel.onchange=()=>fetch('/api/program',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:sel.value})});}
  if(sel.value!=state.program) sel.value=state.program;
  const note=(state.scenarios.find(x=>x.name==state.scenario)||{}).note||'';
  const ev=(s.load_events||[]).map(e=>`production step ${e[0]>0?'+':''}${e[0]} kW, ${e[1]} s left`).join(' · ');
  document.getElementById('note').textContent=note+(ev?'   ·   '+ev:'');
  params(state.params); legend(); chart(state.hist); totals(s.totals); tabs(state.sheet.sheets); drawSheet(state.sheet);
  document.getElementById('events').innerHTML=state.events.map(e=>{const c=/TRIP/.test(e[1])?'bad':(/comm fail: on|forced to 0/.test(e[1])?'warn':'');return `<div class="${c}"><time>${e[0]}</time>${esc(e[1])}</div>`;}).join('');
}
function legend(){
  const l=document.getElementById('legend'); if(l.children.length) return;
  for(const [k,name,col,w,dash] of SERIES){const sp=document.createElement('span');sp.innerHTML=`<i class="${dash.length?'dash':''}" style="color:${css(col)}"></i>${name}`;sp.onclick=()=>{hidden.has(k)?hidden.delete(k):hidden.add(k);sp.className=hidden.has(k)?'off':'';};l.appendChild(sp);}
  const b=document.createElement('span');b.innerHTML=`<b style="background:rgba(184,134,11,.18)"></b>SAU silent`;l.appendChild(b);
  const b2=document.createElement('span');b2.innerHTML=`<b style="background:rgba(184,134,11,.4)"></b>comm fail declared`;l.appendChild(b2);
  const b3=document.createElement('span');b3.innerHTML=`<b style="background:rgba(192,58,43,.25)"></b>plant dark`;l.appendChild(b3);
}
let geo=null;
function chart(h){
  const c=document.getElementById('chart'); const dpr=devicePixelRatio||1; const W=c.width=c.clientWidth*dpr, H=c.height=260*dpr; const g=c.getContext('2d');
  g.clearRect(0,0,W,H); const n=h.load.length; if(n<2) return;
  const L=54*dpr, R=10*dpr, T=10*dpr, B=22*dpr;
  let hi=2200, lo=0;
  for(const [k] of SERIES){ if(hidden.has(k)) continue; for(const v of h[k]){ if(v===null||v===undefined) continue; if(v>hi) hi=v; if(v<lo) lo=v; } }
  lo=Math.min(lo,-200); hi*=1.04;
  const x=i=>L+i/(900-1)*(W-L-R), y=v=>T+(hi-v)/(hi-lo)*(H-T-B);
  geo={x,y,n,L,R,T,B,W,H,dpr};
  for(let i=0;i<n;i++){ const w=Math.max(1,(W-L-R)/900);
    if(h.silent[i]){g.fillStyle='rgba(184,134,11,.16)'; g.fillRect(x(i),T,w,H-T-B);} if(h.comm[i]){g.fillStyle='rgba(184,134,11,.26)'; g.fillRect(x(i),T,w,H-T-B);}
    if(h.tripped&&h.tripped[i]){g.fillStyle='rgba(192,58,43,.22)'; g.fillRect(x(i),T,w,H-T-B);} }
  g.strokeStyle='#e3e6e0'; g.lineWidth=1*dpr; g.fillStyle='#8a929c'; g.font=(11*dpr)+'px IBM Plex Mono, monospace'; g.textAlign='right';
  const step=hi-lo>3000?1000:500;
  for(let v=Math.ceil(lo/step)*step; v<=hi; v+=step){ g.beginPath(); g.moveTo(L,y(v)); g.lineTo(W-R,y(v)); g.stroke(); g.fillText(v,L-6*dpr,y(v)+4*dpr); }
  g.strokeStyle='#b9bfb8'; g.beginPath(); g.moveTo(L,y(0)); g.lineTo(W-R,y(0)); g.stroke();
  g.textAlign='center'; for(let i=0;i<n;i+=180){ g.fillText(h.t[i],x(i),H-6*dpr); }
  const hk=hover?hover.k:null;
  for(const [k,name,col,w,dash] of SERIES){ if(hidden.has(k)) continue; const arr=h[k]; if(!arr) continue;
    g.beginPath(); g.setLineDash(dash.map(d=>d*dpr)); g.strokeStyle=css(col); g.globalAlpha=(hk&&hk!==k)?0.25:1; g.lineWidth=(hk===k?w+1.6:w)*dpr; let pen=false;
    for(let i=0;i<n;i++){const v=arr[i]; if(v===null||v===undefined){pen=false;continue;} pen?g.lineTo(x(i),y(v)):g.moveTo(x(i),y(v)); pen=true;} g.stroke(); g.setLineDash([]); g.globalAlpha=1; }
  if(hover){ const i=hover.i; const v=h[hover.k][i]; if(v!==null&&v!==undefined){ const col=SERIES.find(s=>s[0]===hover.k)[2]; g.fillStyle='#fff'; g.beginPath(); g.arc(x(i),y(v),6*dpr,0,7); g.fill(); g.fillStyle=css(col); g.beginPath(); g.arc(x(i),y(v),4*dpr,0,7); g.fill(); } }
}
const cv=document.getElementById('chart'), tip=document.getElementById('tip');
cv.addEventListener('mousemove',e=>{ if(!geo||!state) return; const r=cv.getBoundingClientRect(); const px=(e.clientX-r.left)*geo.dpr, py=(e.clientY-r.top)*geo.dpr;
  const i=Math.round((px-geo.L)/(geo.W-geo.L-geo.R)*(900-1)); if(i<0||i>=geo.n){hover=null;tip.style.display='none';return;}
  const h=state.hist; let best=null, bestd=14*geo.dpr;                       // the line under the pointer, if one is within 14 px
  for(const [k,name,col] of SERIES){ if(hidden.has(k)) continue; const v=h[k][i]; if(v===null||v===undefined) continue; const d=Math.abs(geo.y(v)-py); if(d<bestd){bestd=d;best=[k,name,col,v];} }
  if(!best){hover=null;tip.style.display='none';return;}
  hover={i,k:best[0]};
  let html=`<div><b style="background:${best[2]==='--load'?'#e6e8e3':css(best[2])}"></b>${best[1]}</div><div class="t">${best[3]} kW at ${h.t[i]}`+(best[0]==='gen_kw'?` (gen load ${h.gen_load_pct[i]} %)`:'')+`</div>`;
  if(h.silent[i]) html+=`<div style="color:#e6c15a">SAU silent${h.comm[i]?', comm fail declared':''}</div>`; if(h.tripped&&h.tripped[i]) html+=`<div style="color:#ff8a7a">plant dark</div>`;
  tip.innerHTML=html; tip.style.display='block'; const card=cv.parentElement.getBoundingClientRect(); let tx=e.clientX-card.left+14, ty=e.clientY-card.top-34; if(tx+240>card.width) tx-=260; tip.style.left=tx+'px'; tip.style.top=ty+'px'; });
cv.addEventListener('mouseleave',()=>{hover=null;tip.style.display='none';});
function totals(T){ if(!T) return; const d=document.getElementById('totals');
  const items=[['since the start',T.hours+' h'],['solar used',T.pv_used_kwh+' kWh'],['solar available',T.pv_available_kwh+' kWh'],['solar thrown away',T.pv_curtailed_kwh+' kWh'],
    ['gensets',T.gen_kwh+' kWh'],['fuel (model)',T.fuel_l+' l'],['comm fails declared',T.comm_fails],['setpoint forced to 0',T.zero_setpoints],['overproduction warnings',T.warnings],
    ['UPS sheds',T.ups_sheds],['gen load min / max',(T.min_gen_load_pct===null?'–':T.min_gen_load_pct)+' / '+T.max_gen_load_pct+' %'],['seconds in reverse power',T.rp_seconds],
    ['reverse power trips',T.rp_trips],['seconds in overload',T.overload_seconds],['overload trips',T.overload_trips],['plant dark',T.blackout_min+' min']];
  d.innerHTML=items.map(([k,v])=>`<div><span>${k}</span><strong>${v}</strong></div>`).join(''); }
function params(p){ const d=document.getElementById('params'); if(d.children.length) return;
  for(const [k,label,src] of PARAMS){const l=document.createElement('label');l.className='knob';l.innerHTML=`<span>${label}<small>${src}</small></span>`;const i=document.createElement('input');i.value=p[k];i.id='p_'+k;i.onchange=()=>ctl('param:'+k,parseFloat(i.value));l.appendChild(i);d.appendChild(l);} }
function tabs(list){ const t=document.getElementById('tabs'); if(t.children.length){for(const b of t.querySelectorAll('button[data-i]')) b.className=(+b.dataset.i==sheet)?'on':''; return;}
  for(const s of list){const b=document.createElement('button');b.dataset.i=s.index;b.textContent=s.index+' '+s.name;b.onclick=()=>{sheet=s.index;};t.appendChild(b);}
  const z=document.createElement('div');z.className='zoom';z.innerHTML='zoom <input type="range" min="50" max="150" value="100" style="width:110px;padding:0">';z.querySelector('input').oninput=e=>{zoom=e.target.value/100;document.querySelector('#sheet svg').style.transform=`scale(${zoom})`;};t.appendChild(z); }
function drawSheet(sh){
  const CX=34, RY=16, BW=170, M=240, MY=20; let maxx=0,maxy=0; const pos={};
  for(const b of sh.blocks){ const rows=Math.max(b.inputs.length,b.outputs.length,1); const h=RY*(rows+1)+6; const px=M+(b.x-4)*CX, py=MY+b.y*RY; pos[b.id]={x:px,y:py,h:h,b:b}; maxx=Math.max(maxx,px+BW+260); maxy=Math.max(maxy,py+h+10); }
  let out=`<svg width="${maxx}" height="${maxy}" xmlns="http://www.w3.org/2000/svg" style="transform:scale(${zoom})">`;
  for(const w of sh.wires){ const a=pos[w.frm], b=pos[w.to]; if(!a||!b) continue; const o=a.b.outputs[w.out]; const x1=a.x+BW, y1=a.y+RY*(w.out+1)+10, x2=b.x, y2=b.y+RY*(w.inp+1)+10; const dx=Math.max(30,(x2-x1)/2);
    out+=`<path class="wire" d="M${x1},${y1} C${x1+dx},${y1} ${x2-dx},${y2} ${x2},${y2}"><title>${esc(o.name)} = ${esc(o.value)}  (${o.key})</title></path>`; }
  for(const id in pos){ const p=pos[id], b=p.b;
    out+=`<rect class="blk" x="${p.x}" y="${p.y}" width="${BW}" height="${p.h}" rx="3"/><rect class="blkhdr" x="${p.x+1}" y="${p.y+1}" width="${BW-2}" height="15"/><text class="hdr" x="${p.x+6}" y="${p.y+12}">${esc(b.type)}</text>`;
    b.inputs.forEach((i,k)=>{ const y=p.y+RY*(k+1)+14; const cls=i.value==='1'?'pin1':(i.value==='0'?'pin0':'pinv');
      out+=`<text class="${cls}" x="${p.x+6}" y="${y}"><title>${esc(i.name)} (${i.key})</title>${esc(i.value)}</text><text x="${p.x-4}" y="${y}" text-anchor="end" fill="#6b7480"><title>${esc(i.name)} (${i.key})</title>${i.neg?'NOT ':''}${esc(i.name)}</text>`; });
    b.outputs.forEach((o,k)=>{ const y=p.y+RY*(k+1)+14; const cls=o.value==='1'?'pin1':(o.value==='0'?'pin0':'pinv');
      out+=`<text class="${cls}" x="${p.x+BW-6}" y="${y}" text-anchor="end"><title>${esc(o.name)} (${o.key})</title>${esc(o.value)}</text><text x="${p.x+BW+4}" y="${y}" fill="#6b7480"><title>${esc(o.name)} (${o.key})</title>${esc(o.name)}</text>`; });
  }
  out+='</svg>'; document.getElementById('sheet').innerHTML=out;
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
poll();
</script></body></html>"""


def create_app(plant: LivePlant):
    app = FastAPI(title="PLC lab")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE

    @app.get("/api/state")
    def state(sheet: int = 10):
        with plant.lock:
            return JSONResponse(dict(plant=plant.snapshot, paused=plant.paused, speed=plant.speed, program=plant.program_name,
                                     programs=list(PROGRAMS), auto_pm=plant.auto_pm, params=plant.params,
                                     scenario=plant.scenario, scenarios=[dict(name=x["name"], note=x["note"]) for x in SCENARIOS],
                                     hist={k: list(v) for k, v in plant.hist.items()}, events=list(plant.events),
                                     sheet=plant.sheet_view(sheet)))

    @app.post("/api/control")
    async def control(req: Request):
        body = await req.json()
        plant.inject(str(body.get("what", "")), float(body.get("value") or 0))
        return {"ok": True}

    @app.post("/api/scenario")
    async def scenario(req: Request):
        body = await req.json()
        name = body.get("name")
        if name in SCENARIO_BY_NAME:
            plant.apply_scenario(name)
        return {"ok": name in SCENARIO_BY_NAME}

    @app.post("/api/program")
    async def program(req: Request):
        body = await req.json()
        name = body.get("name")
        if name in PROGRAMS:
            plant.set_program(name)
        return {"ok": name in PROGRAMS}

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description="the plant and its PLC program, live in the browser")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--hour", type=float, default=10.0, help="start time of day")
    ap.add_argument("--replay", help="a trace file of the master (data/trace/neo-01_<date>.csv): the PLC runs on the recorded values")
    ap.add_argument("--replay-sau", help="the SAU array trace of the same day, for the recorded setpoint")
    a = ap.parse_args()
    import uvicorn
    replay = ReplaySource(a.replay, a.replay_sau) if a.replay else None
    plant = LivePlant(start_hour=a.hour, replay=replay)
    plant.speed = a.speed
    stop = threading.Event()
    threading.Thread(target=plant.run, args=(stop,), daemon=True).start()
    print(f"PLC lab: http://localhost:{a.port}   (Ctrl+C stops)")
    try:
        uvicorn.run(create_app(plant), host="127.0.0.1", port=a.port, log_level="warning")
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
