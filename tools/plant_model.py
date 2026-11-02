"""
Behaviour model of the Erbil hybrid plant: what the controller decides over a day, under today's settings and under
proposed modifications. Pure Python, nothing talks to a device.

  python tools/plant_model.py                 prints the comparison table, writes docs/simulation/*.csv + .png
  python tools/plant_model.py --seed 3

What is exact and what is not (details in docs/SIMULATION.md):
  EXACT   - the PLC program of the InteliNeo 5500 (11 sheets exported from InteliConfig on 2026-10-14) is executed
            block by block by tools/neo_plc.py: PV request on grid / on gensets, step protection, comm-fail handling,
            ramp, overproduction warning, UPS shedding commands, PV/UPS breaker commands, with the plant's setpoints
  REAL    - archive of 2026-10-14: Gen P Min 25 %, start/stop reserve 300/400 kW, min run power 210 kW, solar
            coverage 20 %, curtailment gain 5 / 15 s, fuel 237.3 g/kWh, SAU "max available" 3,520 kW
  REAL    - controller history 29 Sep - 14 Oct: grid from ~18:05 to ~06:12 (night load 570-680 kW), gensets by day,
            3 gensets (3,150 kW) at 21-33 %, the PV interruptions of 14 Oct (operator OFF/AUTO toggles, PV-logger
            comm fails), ~6 MWh/day PV
  ASSUMED - the controller's built-in curtailment regulator (structure; its rate is calibrated so the request runs
            ~200 kW under the PV at the warnings, as recorded), the SAU/inverter response time, daytime load between
            the recorded points, clear-sky PV shape, genset fuel curve shape, UPS load share and autonomy
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
import sys
from dataclasses import dataclass, field, replace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from neo_plc import Plc, load_program, plant_inputs  # noqa: E402

# ----------------------------------------------------------------------------- plant (REAL unless marked)
GEN_NOMS_KW = [1150, 1000, 1000, 1000, 700]   # from the "Gen Nom" steps in the history (nameplates to confirm)
GENSETS = len(GEN_NOMS_KW)
PV_RATED_KW = 3200
SAU_MAX_AVAILABLE_KW = 3520      # REAL: the SAU reports a constant "Maximum Active Power Available" of 3,520 kW
FUEL_L_PER_KWH_FULL = 237.3 / 1000 / 0.84      # 237.3 g/kWh (Neo setpoint), diesel 0.84 kg/l -> 0.283 l/kWh
FUEL_IDLE_FRACTION = 0.10        # ASSUMED Willans line: l/h = nom * (0.10*full + (1-0.10)*full*load) -> 0.37 l/kWh at 25 %
STEP_S = 60                      # model step (load, sun, power management)
SUB_S = 10                       # PLC / PV plant step inside a model step
PROGRAM = None


def program():
    global PROGRAM
    if PROGRAM is None:
        PROGRAM = load_program()
    return PROGRAM


@dataclass
class Scenario:
    name: str
    manual_gensets: int | None = None   # None = automatic power management; n = n gensets kept running
    manual_extra_from_h: float | None = None   # manual operation: one more genset from this hour
    min_gensets: int = 1                # PROPOSED: power management never runs fewer than this ("minimal running power")
    gen_p_min: float = 0.25             # REAL 25 %
    start_reserve_kw: float = 300       # REAL
    stop_reserve_kw: float = 400        # REAL
    min_run_power_kw: float = 210       # REAL
    solar_coverage: float = 0.20        # REAL dynamic spinning reserve: 20 % of PV output kept as reserve
    next_start_delay_s: int = 20        # REAL
    next_stop_delay_s: int = 180        # REAL
    curtail_rate: float = 0.03          # CALIBRATED on 14 Oct: 8-13 overproduction warnings a day like the history
    pv_response_s: float = 10           # ASSUMED SAU/inverter first-order response to a setpoint
    plc_setpoints: dict = field(default_factory=dict)   # PLC user setpoint overrides {comobj: value}
    program_dir: str | None = None      # a modified program (folder of sheet exports) instead of the plant's
    comm_hold_s: float = 0              # PROPOSED: Modbus timeout/retries so that fails shorter than this are not declared
    ups_math_fix: bool = False          # PROPOSED: UPS sheet uses the genset nominal directly (no stale-PV artefact)
    ups_shed_delay_s: float = 0         # PROPOSED: delay before a UPS shedding command acts
    ups_bridge: bool = False            # PROPOSED: UPS carries its load on battery for a few minutes after a PV drop
    ups_bridge_minutes: int = 8
    ups_critical_share: float = 0.05    # ASSUMED each UPS carries 5 % of the plant load
    ups_kw: tuple | None = None         # or a fixed load per UPS in kW (e.g. (350, 350)) instead of the share
    ups_autonomy_min: float = 30        # ASSUMED battery autonomy at full critical load


@dataclass
class Day:
    name: str
    clearness: float = 1.0              # 1 = clear sky
    cloud_events: int = 0               # number of cloud passages
    grid_hours: tuple = ((0, 6.2), (18.08, 24))   # REAL typical: grid from ~18:05 to ~06:12 (history)
    interruptions: tuple = ()           # (start_minute, minutes, kind): "off" = operator OFF->AUTO, "comm" = PV logger comm fail
    seed: int = 1


# PV interruptions of 14 Oct 2026 from the controller history (REAL): operator OFF->AUTO toggles (PVCB opened) and
# "PV Logger group 1/2 Comm Fail" bursts
INTERRUPTIONS_14_OCT = ((8 * 60 + 5, 9, "off"), (8 * 60 + 52, 8, "comm"), (9 * 60 + 17, 2, "comm"), (9 * 60 + 20, 1, "off"),
                        (9 * 60 + 23, 4, "off"), (9 * 60 + 37, 2, "off"), (9 * 60 + 58, 2, "comm"), (12 * 60 + 25, 1, "off"))


@dataclass
class Result:
    scenario: str
    day: str
    engine_hours: float = 0
    fuel_l: float = 0
    pv_available_kwh: float = 0
    pv_used_kwh: float = 0
    pv_curtailed_kwh: float = 0
    genset_kwh: float = 0
    grid_kwh: float = 0
    load_kwh: float = 0
    avg_gen_load_pct: float = 0
    overload_minutes: int = 0           # minutes containing seconds with gensets above 100 % (a genset start takes 20-40 s)
    max_gen_load_pct: float = 0
    overproduction_warnings: int = 0    # PLC "PV prod > PV req Warning" rising edges
    ups_shed_minutes: int = 0           # minutes with a UPS shed (breaker opened by the PLC or on battery)
    ups_shed_events: int = 0            # PLC UPS shedding commands
    starts: int = 0
    max_gen_load_pct: float = 0
    overload_seconds: int = 0           # sub-step seconds with the gensets above 100 %
    timeline: dict = field(default_factory=dict)


def load_profile_kw(minute: int) -> float:
    """Night 570-680 kW on the grid (REAL, mains import at the transfers); daytime points from the history
    (genset load % x running nominal + PV): 1,590 kW 07:16, 1,850 kW ~10:30, 1,830 kW 12:25, 1,640 kW 13:06,
    1,482 kW 17:28 (REAL points, ASSUMED in between)."""
    anchors = [(0, 650), (6.1, 650), (6.3, 1450), (7.3, 1590), (9, 1650), (10.5, 1850), (12.4, 1830), (13.1, 1640),
               (15, 1520), (17.5, 1480), (18.0, 1300), (18.2, 650), (24, 650)]
    h = minute / 60
    for (h0, p0), (h1, p1) in zip(anchors, anchors[1:]):
        if h0 <= h <= h1:
            return p0 + (p1 - p0) * (h - h0) / (h1 - h0)
    return 650


def pv_available_kw(minute: float, day: Day, clouds: list) -> float:
    """Clear-sky bell for Erbil mid-October (sunrise ~06:05, sunset ~17:45, peak ~78 % of rated), times clouds."""
    h = minute / 60
    rise, set_ = 6.08, 17.75
    if h <= rise or h >= set_:
        return 0.0
    x = (h - rise) / (set_ - rise)
    bell = math.sin(math.pi * x) ** 1.3
    p = PV_RATED_KW * 0.78 * bell * day.clearness
    for (t0, t1, depth) in clouds:
        if t0 <= minute <= t1:
            p *= (1 - depth)
    return p


def make_clouds(day: Day, rnd: random.Random) -> list:
    out = []
    for _ in range(day.cloud_events):
        t0 = rnd.randint(7 * 60, 16 * 60)
        out.append((t0, t0 + rnd.randint(5, 20), rnd.uniform(0.4, 0.7)))
    return out


def grid_on(minute: float, day: Day) -> bool:
    return any(a * 60 <= minute < b * 60 for a, b in day.grid_hours)


def fuel_l_per_min(p_kw: float, nom_kw: float) -> float:
    if nom_kw <= 0:
        return 0.0
    load = max(0.0, min(1.0, p_kw / nom_kw))
    l_per_h = nom_kw * FUEL_L_PER_KWH_FULL * (FUEL_IDLE_FRACTION + (1 - FUEL_IDLE_FRACTION) * load)
    return l_per_h / 60


def simulate(sc: Scenario, day: Day) -> Result:
    rnd = random.Random(day.seed)
    clouds = make_clouds(day, rnd)
    r = Result(sc.name, day.name)
    plc = Plc(load_program(sc.program_dir) if sc.program_dir else program(), sc.plc_setpoints, breaker_timeout_s=15)
    nom_of = lambda n: sum(GEN_NOMS_KW[:n])        # gensets start in the order of the list
    running = sc.manual_gensets if sc.manual_gensets is not None else 2
    start_timer = stop_timer = 0
    pv_actual = 0.0                 # what the inverters produce
    sau_reading = 0.0               # "PV Actual P" as the controller reads it over Modbus (one PLC step late)
    curtail = 0.0                   # controller curtailment output, %
    pv_cb = [1, 1]                  # PV breaker feedbacks (follow the PLC commands)
    ups_closed = [0, 0]             # UPS breaker feedbacks (the PLC closes them after its 10 s delay)
    ups_battery = [sc.ups_autonomy_min, sc.ups_autonomy_min]
    ups_shed_timer = [0.0, 0.0]
    fail_since = None               # a latched breaker fail, reset by the "operator" after two minutes
    last_transfer = -1e9
    warn_timer = 0
    bridge_until = -1
    pv_prev_actual = 0.0
    gen_load_sum = 0.0
    gen_kw = 0.0
    tl = {k: [] for k in ("load", "pv_available", "pv_used", "pv_request", "gensets", "gen_load_pct", "grid", "ups_shed")}

    for m in range(24 * 60):
        load = load_profile_kw(m)
        avail = pv_available_kw(m, day, clouds)
        on_grid = grid_on(m, day)
        if on_grid != grid_on(m - 1, day):
            last_transfer = m
        interruption = next((kind for t0, n, kind in day.interruptions if t0 <= m < t0 + n), None)

        # --- gensets: manual operation or the controller's power management (minute level)
        if on_grid:
            running = 0
            run_nom = 0.0
        else:
            if running == 0:
                running = sc.manual_gensets if sc.manual_gensets is not None else 3   # morning transfer: 3 gensets
            if sc.manual_gensets is not None:
                running = sc.manual_gensets + (1 if sc.manual_extra_from_h is not None and m >= sc.manual_extra_from_h * 60 else 0)
            run_nom = nom_of(running)

        # --- UPS batteries and the proposed bridge
        if sc.ups_bridge and pv_actual < pv_prev_actual - 250 and m > 0:   # a PV drop of more than 250 kW within a minute
            bridge_until = m + sc.ups_bridge_minutes

        shed_any = False
        ups_shed_now = 0
        overload_in_minute = False
        for s in range(STEP_S // SUB_S):
            t = m + s * SUB_S / 60
            # controller-side PV logic: PVCB close/open is off for 2 minutes around a transfer and during an OFF toggle
            pvcb_close = int(not (interruption == "off" or t - last_transfer < 2))
            # a PV-logger comm-fail burst as recorded: a fail of ~10 s every 30 s during the interruption; declared
            # to the PLC only when it lasts longer than the proposed timeout/retry window (comm_hold_s)
            comm_flag = int(interruption == "comm" and s % 3 == 0 and sc.comm_hold_s < SUB_S)
            fault_reset = 0
            if fail_since is not None and t - fail_since >= 2:
                fault_reset, fail_since = 1, None
            pv_live = bool(pvcb_close and any(pv_cb))
            if not pv_live:
                pv_actual = 0.0                                   # breakers open: the inverters trip at once
            # the controller counts the PV in "Running Nominal Power Of All" only while its PVCB feedback is closed;
            # the SAU value it subtracts ("PV Actual P") is one Modbus refresh old
            neo_pv_term = sau_reading if pv_live else 0.0
            if sc.ups_math_fix:
                neo_pv_term = sau_reading      # the PLC then subtracts the same value -> genset nominal exactly
            shed_load = sum((sc.ups_kw[i] if sc.ups_kw else sc.ups_critical_share * load) for i in range(2) if not ups_closed[i])
            load_on_bus = load - shed_load
            gen_kw = 0.0 if on_grid else max(0.0, load_on_bus - pv_actual)
            mains_each = (load_on_bus - pv_actual) / 3 if on_grid else 0.0
            if run_nom:
                r.max_gen_load_pct = max(r.max_gen_load_pct, 100 * gen_kw / run_nom)
                if gen_kw > run_nom:
                    r.overload_seconds += SUB_S
            inputs = plant_inputs(pv_kw=sau_reading, max_avail_kw=SAU_MAX_AVAILABLE_KW, curtailment_pct=curtail,
                                  gensets_kw=gen_kw, running_nominal_kw=run_nom, mains_import_kw=(mains_each,) * 3,
                                  on_grid=on_grid, gens_loaded=running, pv_cb=tuple(pv_cb), ups_fb=tuple(ups_closed),
                                  comm_fail=(comm_flag, comm_flag), pvcb_close=pvcb_close, fault_reset=fault_reset,
                                  pv_valid=not comm_flag, neo_pv_sample_kw=neo_pv_term)
            if m == 0 and s == 0:
                for _ in range(3):                                # warm-up: delays and breakers settle
                    plc.step(inputs, SUB_S)
                    ups_closed = [plc.get(10427, 2) or 0, plc.get(10434, 2) or 0]
                    inputs = plant_inputs(pv_kw=sau_reading, max_avail_kw=SAU_MAX_AVAILABLE_KW, curtailment_pct=curtail,
                                          gensets_kw=gen_kw, running_nominal_kw=run_nom, mains_import_kw=(mains_each,) * 3,
                                          on_grid=on_grid, gens_loaded=running, pv_cb=tuple(pv_cb), ups_fb=tuple(ups_closed),
                                          comm_fail=(0, 0), pvcb_close=pvcb_close, fault_reset=0, neo_pv_sample_kw=neo_pv_term)
            plc.step(inputs, SUB_S)
            setpoint = plc.get(21267) or 0.0                       # PV req out ramp -> SAU active power setpoint
            cb_cmd = [plc.get(10425, 2) or 0, plc.get(10426, 2) or 0]   # PV CB1 ON / PV CB2 ON
            ups_cmd = [plc.get(10427, 2) or 0, plc.get(10434, 2) or 0]  # UPS1 ON / UPS2 ON
            warn_timer = warn_timer + SUB_S if plc.get(10433, 2) else 0
            if warn_timer == 2 * SUB_S:                            # the "AHI" alarm has its own 15 s delay
                r.overproduction_warnings += 1
            if any(plc.get(n, b) for n, b in ((10425, 7), (10426, 0), (10426, 7), (10427, 0), (10427, 7), (10428, 0), (10434, 7), (10435, 0))):
                fail_since = fail_since if fail_since is not None else t
            # breakers follow the commands (feedback for the next step)
            pv_cb = cb_cmd
            for i in range(2):
                if ups_cmd[i]:
                    ups_shed_timer[i] = 0.0
                    ups_closed[i] = 1
                else:
                    ups_shed_timer[i] += SUB_S
                    if ups_shed_timer[i] >= sc.ups_shed_delay_s:
                        if ups_closed[i]:
                            r.ups_shed_events += 1
                        ups_closed[i] = 0
            # the PV plant: inverters follow the setpoint with a first-order response while the breakers are closed
            if pvcb_close and any(pv_cb):
                target = min(avail, setpoint)
                pv_actual += (target - pv_actual) * (1 - math.exp(-SUB_S / sc.pv_response_s))
            else:
                pv_actual = 0.0
            sau_reading = pv_actual
            # the controller's curtailment regulator (ASSUMED structure): keeps the gensets at Gen P Min
            if not on_grid and run_nom:
                gen_load_now = max(0.0, load_on_bus - pv_actual) / run_nom * 100
                err = gen_load_now - sc.gen_p_min * 100
                curtail = max(0.0, min(100.0, curtail + sc.curtail_rate * err * SUB_S))
            elif on_grid:
                curtail = 0.0
            if not all(ups_closed):
                shed_any = True
                ups_shed_now = 1
            if run_nom:
                r.max_gen_load_pct = max(r.max_gen_load_pct, 100 * gen_kw / run_nom)
                if gen_kw > run_nom:
                    overload_in_minute = True

        # --- UPS on battery: drain while shed, recharge while closed; the bridge keeps UPS 1 on battery deliberately
        for i in range(2):
            if not ups_closed[i]:
                ups_battery[i] = max(0.0, ups_battery[i] - 1)
            else:
                ups_battery[i] = min(sc.ups_autonomy_min, ups_battery[i] + 0.2)
        if sc.ups_bridge and m < bridge_until and ups_battery[0] > 0:
            ups_closed[0] = 0
            shed_any = True
        if shed_any:
            r.ups_shed_minutes += 1

        # --- power management (automatic) at the minute level
        if not on_grid and sc.manual_gensets is None:
            reserve_needed = sc.start_reserve_kw + sc.solar_coverage * pv_actual
            if run_nom - gen_kw < reserve_needed and running < GENSETS:
                start_timer += STEP_S
                if start_timer >= sc.next_start_delay_s:
                    running += 1; r.starts += 1; start_timer = 0
            else:
                start_timer = 0
            if running > sc.min_gensets and nom_of(running - 1) - gen_kw >= sc.stop_reserve_kw + sc.solar_coverage * pv_actual \
                    and gen_kw >= sc.min_run_power_kw:
                stop_timer += STEP_S
                if stop_timer >= sc.next_stop_delay_s:
                    running -= 1; stop_timer = 0
            else:
                stop_timer = 0
            running = max(sc.min_gensets, running)
            run_nom = nom_of(running)

        gen_load_pct = gen_kw / run_nom if run_nom else 0.0
        if overload_in_minute:
            r.overload_minutes += 1
        grid_kw = load - sum((sc.ups_kw[i] if sc.ups_kw else sc.ups_critical_share * load) for i in range(2) if not ups_closed[i]) - pv_actual if on_grid else 0.0

        # --- bookkeeping
        dt_h = 1 / 60
        r.load_kwh += load * dt_h
        r.pv_available_kwh += avail * dt_h
        r.pv_used_kwh += pv_actual * dt_h
        r.genset_kwh += gen_kw * dt_h
        r.grid_kwh += max(0.0, grid_kw) * dt_h
        r.engine_hours += running * dt_h
        for i in range(running):                          # load shared in proportion to nominal (load sharing)
            share = GEN_NOMS_KW[i] / nom_of(running)
            r.fuel_l += fuel_l_per_min(gen_kw * share, GEN_NOMS_KW[i])
        gen_load_sum += gen_load_pct
        pv_prev_actual = pv_actual
        for k, v in (("load", load), ("pv_available", avail), ("pv_used", pv_actual), ("pv_request", plc.get(21267) or 0.0),
                     ("gensets", running), ("gen_load_pct", round(100 * gen_load_pct, 1)), ("grid", int(on_grid)),
                     ("ups_shed", ups_shed_now)):
            tl[k].append(round(v, 1))

    r.pv_curtailed_kwh = max(0.0, r.pv_available_kwh - r.pv_used_kwh)
    r.avg_gen_load_pct = 100 * gen_load_sum / (24 * 60)
    r.timeline = tl
    return r


FIXES = dict(comm_hold_s=90, ups_math_fix=True, ups_shed_delay_s=10)
SCENARIOS = [
    Scenario("S0 today: 3 gensets by hand, 4 from 14:00", manual_gensets=3, manual_extra_from_h=14),
    Scenario("S1 power management AUTO, settings as they are"),
    Scenario("S2 AUTO + Gen P Min 20 %", gen_p_min=0.20),
    Scenario("S3 AUTO + PLC fixes (comm hold, UPS math, shed delay)", **FIXES),
    Scenario("S4 AUTO + PLC fixes + Gen P Min 20 %", gen_p_min=0.20, **FIXES),
    Scenario("S5 AUTO + PLC fixes + UPS bridge, coverage 10 %", ups_bridge=True, solar_coverage=0.10, **FIXES),
    Scenario("S6 AUTO + PLC fixes, two gensets minimum", min_gensets=2, **FIXES),
]
DAYS = [
    Day("14 Oct as recorded (interruptions)", clearness=1.0, interruptions=INTERRUPTIONS_14_OCT),
    Day("clear day, no interruptions", clearness=1.0),
    Day("cloudy day", clearness=0.9, cloud_events=8, seed=7),
    Day("no grid at all", clearness=1.0, grid_hours=()),
    Day("clear day, full PV loss at 14:30 for 3 min", clearness=1.0, interruptions=((14 * 60 + 30, 3, "off"),)),
]


def table(results: list[Result]) -> str:
    head = (f"{'scenario':52} {'day':20} {'eng h':>6} {'fuel l':>7} {'PV used':>8} {'PV avail':>9} {'curtail':>8} "
            f"{'gen %':>6} {'ovl min':>7} {'warn':>5} {'UPS ev':>6} {'UPS min':>7}")
    lines = [head, "-" * len(head)]
    for r in results:
        lines.append(f"{r.scenario:52} {r.day:20} {r.engine_hours:6.1f} {r.fuel_l:7.0f} {r.pv_used_kwh:8.0f} {r.pv_available_kwh:9.0f} "
                     f"{100 * r.pv_curtailed_kwh / r.pv_available_kwh if r.pv_available_kwh else 0:7.0f}% {r.avg_gen_load_pct:6.0f} "
                     f"{r.overload_minutes:7d} {r.overproduction_warnings:5d} {r.ups_shed_events:6d} {r.ups_shed_minutes:7d}")
    return "\n".join(lines)


def write_outputs(results: list[Result], outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "scenarios.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "day", "engine_hours", "fuel_l", "pv_used_kwh", "pv_available_kwh", "pv_curtailed_kwh",
                    "genset_kwh", "grid_kwh", "load_kwh", "avg_gen_load_pct", "overload_minutes", "overproduction_warnings",
                    "ups_shed_events", "ups_shed_minutes", "starts"])
        for r in results:
            w.writerow([r.scenario, r.day, round(r.engine_hours, 2), round(r.fuel_l), round(r.pv_used_kwh), round(r.pv_available_kwh),
                        round(r.pv_curtailed_kwh), round(r.genset_kwh), round(r.grid_kwh), round(r.load_kwh), round(r.avg_gen_load_pct, 1),
                        r.overload_minutes, r.overproduction_warnings, r.ups_shed_events, r.ups_shed_minutes, r.starts])
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    day = DAYS[0].name
    picks = [r for r in results if r.day == day and r.scenario.startswith(("S0", "S1", "S3"))]
    if not picks:
        return
    t = [m / 60 for m in range(24 * 60)]
    fig, axes = plt.subplots(3, 1, figsize=(13, 9.5), sharex=True, gridspec_kw={"height_ratios": [3, 2, 1.4]})
    ink, blue, green, orange, purple, grey = "#1f2937", "#2563eb", "#15803d", "#b45309", "#7c3aed", "#9ca3af"
    ax = axes[0]
    ax.plot(t, picks[0].timeline["load"], color=ink, lw=1.6, label="plant load")
    ax.fill_between(t, picks[0].timeline["pv_available"], color=green, alpha=0.12, lw=0)
    ax.plot(t, picks[0].timeline["pv_available"], color=green, lw=1.2, ls="--", label="PV available (sun)")
    for r, c in zip(picks, (orange, blue, purple)):
        ax.plot(t, r.timeline["pv_used"], color=c, lw=1.8, label=f"PV used — {r.scenario}")
    ax.plot(t, picks[0].timeline["pv_request"], color=orange, lw=0.8, ls=":", label="PLC request to the SAU — S0")
    ax.set_ylabel("kW"); ax.set_ylim(0, 3400); ax.grid(alpha=0.25); ax.legend(loc="upper left", fontsize=9, ncol=2, frameon=False)
    ax.set_title(f"One simulated day ({day}) — the controller's PLC program run under each scenario", loc="left", fontsize=12, color=ink)
    ax = axes[1]
    for r, c in zip(picks, (orange, blue, purple)):
        ax.plot(t, r.timeline["gen_load_pct"], color=c, lw=1.6, label=r.scenario)
    ax.axhline(25, color=grey, lw=1, ls=":"); ax.text(23.9, 27, "Gen P Min 25 %", ha="right", fontsize=8, color=grey)
    ax.axhline(70, color=grey, lw=1, ls=":"); ax.text(23.9, 72, "UPS 1 shedding 70 %", ha="right", fontsize=8, color=grey)
    ax.axvspan(0, 6.2, color=blue, alpha=0.06, lw=0); ax.axvspan(18.08, 24, color=blue, alpha=0.06, lw=0)
    ax.text(0.2, 6, "on government grid", fontsize=8, color=blue); ax.text(18.3, 6, "on government grid", fontsize=8, color=blue)
    for r, c in zip(picks, (orange, blue, purple)):
        shed = [h for h, v in zip(t, r.timeline["ups_shed"]) if v]
        if shed:
            ax.scatter(shed, [100] * len(shed), s=6, color=c, marker="|")
    ax.text(23.9, 103, "UPS shed", ha="right", fontsize=8, color=grey)
    ax.set_ylabel("genset load %"); ax.set_ylim(0, 110); ax.grid(alpha=0.25); ax.legend(loc="upper left", fontsize=9, frameon=False)
    ax = axes[2]
    for r, c in zip(picks, (orange, blue, purple)):
        ax.step(t, r.timeline["gensets"], color=c, lw=1.6, where="post", label=r.scenario)
    ax.set_ylabel("gensets running"); ax.set_ylim(0, 5.5); ax.set_yticks(range(0, 6)); ax.grid(alpha=0.25)
    ax.set_xlabel("hour of day (plant time)"); ax.set_xlim(0, 24); ax.set_xticks(range(0, 25, 2))
    fig.text(0.01, 0.005, "The PLC program is the controller's own (sheet exports of 14 Oct). Grid hours, night load, genset steps and the "
             "interruptions are from the history; the curtailment regulator, daytime load shape, sun curve, fuel curve and UPS shares are assumptions.",
             fontsize=8, color=grey)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(os.path.join(outdir, "one_day.png"), dpi=150)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "simulation"))
    ap.add_argument("--seed", type=int, default=None, help="cloud pattern seed for the cloudy day")
    a = ap.parse_args()
    days = DAYS if a.seed is None else [replace(d, seed=a.seed) if d.cloud_events else d for d in DAYS]
    results = [simulate(sc, d) for d in days for sc in SCENARIOS]
    print(table(results))
    v = next(r for r in results if r.scenario.startswith("S0") and r.day == days[0].name)
    until = sum(v.timeline["pv_used"][: 13 * 60 + 20]) / 60
    print(f"\nvalidation 14 Oct: model PV until 13:20 {until:.0f} kWh vs 6,016 kWh on the inverter counters at the export (whole day {v.pv_used_kwh:.0f}); "
          f"overproduction warnings {v.overproduction_warnings} (history: 9 by 13:06); UPS shedding events {v.ups_shed_events} "
          f"(history: 2 at 12:25); engine hours {v.engine_hours:.0f} (3 gensets x ~12 h = 36 expected)")
    s0 = next(r for r in results if r.scenario.startswith("S0") and r.day == days[1].name)
    s1 = next(r for r in results if r.scenario.startswith("S1") and r.day == days[1].name)
    print(f"clear day, S0 -> S1: fuel {s0.fuel_l:.0f} -> {s1.fuel_l:.0f} l ({100 * (s1.fuel_l - s0.fuel_l) / s0.fuel_l:+.0f} %), "
          f"PV used {s0.pv_used_kwh:.0f} -> {s1.pv_used_kwh:.0f} kWh, engine hours {s0.engine_hours:.0f} -> {s1.engine_hours:.0f}")
    write_outputs(results, a.out)
    print(f"written: {a.out}/scenarios.csv, one_day.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
