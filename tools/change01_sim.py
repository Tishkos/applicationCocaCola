"""
Change 01, closed loop: the master's PLC program (today, v3 or v4) running at its 100 ms cycle against a small
plant: the two PV logger sessions, the setpoint write, the inverters, the gensets and their reverse-power trip.
It answers one question per scenario: what happens to the gensets and to the solar when the logger link breaks.

    python tools/change01_sim.py                 report on screen
    python tools/change01_sim.py --out FILE.md   and written to a file

What is REAL here: the PLC program (the exported sheets), the session behaviour of 8 Sep (the master gives up
after about 1.9 s, the fail is declared when the SAU answers again and clears about 2 s later), group 1 writing
the setpoint every second and group 2 writing nothing, the inverters' fall rate after a zero (8 Sep: 2,484 ->
153 kW in 14 s, about 166 kW/s), the genset nominals and reverse power 10 % for 5 s (IG4 archives, 17 Oct),
Gen P Min 25 %.
What is ASSUMED: Gen 2's nominal (1,000 kW), the inverters' 1 s reaction and 25 kW/s rise, which ECU values
belong to which session (AP to group 1, Max Available to group 2), a 3 s declare time for a long silence, the
controller's curtailment regulator (integral, frozen while the logger values are invalid). Nothing here touches
the plant.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from neo_plc import Plc, load_program, plant_inputs  # noqa: E402

CHANGE = os.path.join(ROOT, "docs", "changes", "change-01_comm_fail_tolerance")
PROGRAM_DIRS = {"today": None, "v3": os.path.join(CHANGE, "program_v3"), "v4": os.path.join(CHANGE, "program_v4")}

GEN_NOMINAL_KW = (1000, 1000, 700, 1000, 1150)   # Gen 1..5; Gen 2 ASSUMED
RP_PCT, RP_DELAY_S = 10.0, 5.0                     # REAL: IG4 reverse power
GEN_P_MIN = 25.0                                   # REAL
INV_DOWN_KW_S = 166.0                              # MEASURED 8 Sep
INV_UP_KW_S = 25.0                                 # ASSUMED (the PLC ramp is 25 kW/s anyway)
INV_DELAY_S = 1.0                                  # ASSUMED
ABANDON_S = 1.9                                    # MEASURED 8 Sep: the master gives up after about 1.9 s
DECLARE_LONG_S = 3.0                               # ASSUMED: a silence that goes on is declared after 3 s
CLEAR_S = 2.0                                      # MEASURED 8 Sep: the flag clears about 2 s after the SAU is back
DECLARE_LAG_S = 0.1                                # MEASURED 8 Sep: the zero followed the mispaired frames by about 0.1 s
MAX_AVAIL_KW = 3520.0
DT = 0.1

_cache: dict = {}


def program(name: str):
    if name not in _cache:
        d = PROGRAM_DIRS[name]
        _cache[name] = load_program(d) if d else load_program()
    return _cache[name]


@dataclass
class Scenario:
    name: str
    gensets: tuple = (0, 3, 4)            # running sets, indices into GEN_NOMINAL_KW (Gen 1, 4, 5)
    load_kw: float = 2000.0
    avail_kw: float = 3000.0
    silent: list = field(default_factory=list)      # (start, end, sessions) the SAU gives no replies on these sessions
    load_steps: list = field(default_factory=list)  # (t, delta kW)
    clouds: list = field(default_factory=list)      # (start, end, available kW meanwhile)
    ap_zero: list = field(default_factory=list)     # (start, end): AP reads a valid 0 (a mispaired frame)
    ap_invalid: list = field(default_factory=list)  # (start, end): AP invalid without any comm fail bit
    on_grid: bool = False
    mcb: tuple = (1, 1, 1)                          # mains breakers closed (on the grid)
    seconds: float = 120.0


@dataclass
class Result:
    min_gen_kw: float
    min_gen_pct: float
    rp_seconds: float
    tripped: bool
    max_step_kw: float
    export_over_s: float                            # seconds the export was above 1,300 kW per closed feeder
    max_export_kw: float
    trace: list


def _in(t, spans):
    return any(a <= t < b for (a, b, *_) in spans)


def run(prog_name: str, sc: Scenario) -> Result:
    plc = Plc(program(prog_name), breaker_timeout_s=15)
    noms = [GEN_NOMINAL_KW[i] for i in sc.gensets]
    run_nom = 0.0 if sc.on_grid else float(sum(noms))
    load = sc.load_kw
    cap = 1300.0 * sum(sc.mcb)
    p0 = min(sc.avail_kw, load + cap) if sc.on_grid else max(0.0, load - GEN_P_MIN / 100 * run_nom)
    pv = p0
    curtail = 0.0 if sc.on_grid else p0 / MAX_AVAIL_KW * 100
    received = p0
    pending = []                                   # (t_effective, setpoint) on the way to the inverters
    ap_reading, max_reading = pv, MAX_AVAIL_KW
    flags = [0, 0]
    silent_since = [None, None]
    clear_at = [None, None]
    declare_at = [None, None]
    rp_t, rp_seconds, tripped = 0.0, 0.0, False
    min_gen, max_step = 1e9, 0.0
    export_over, max_export = 0.0, 0.0
    gen_hist = []
    trace = []
    t = -60.0                                      # one minute to settle before the scenario clock starts
    while t < sc.seconds:
        # load and sun
        L = load + sum(d for (ts, d) in sc.load_steps if t >= ts)
        avail = sc.avail_kw
        for (a, b, kw) in sc.clouds:
            if a <= t < b:
                avail = kw
        # sessions: silent spans per session (0 = group 1, 1 = group 2)
        for i in range(2):
            silent = any(a <= t < b and i in s for (a, b, s) in sc.silent)
            if silent:
                if silent_since[i] is None:
                    silent_since[i] = t
                if t - silent_since[i] >= DECLARE_LONG_S:
                    flags[i] = 1
                clear_at[i] = None
            else:
                if silent_since[i] is not None:
                    if t - silent_since[i] >= ABANDON_S and not flags[i]:
                        declare_at[i] = t + DECLARE_LAG_S   # declared just after the SAU answers again (8 Sep)
                    if flags[i]:
                        clear_at[i] = t + CLEAR_S
                    silent_since[i] = None
                if declare_at[i] is not None and t >= declare_at[i]:
                    flags[i], declare_at[i], clear_at[i] = 1, None, t + CLEAR_S
                if clear_at[i] is not None and t >= clear_at[i]:
                    flags[i], clear_at[i] = 0, None
        # ASSUMED: while a session is silent the controller keeps its last values; they turn invalid when the fail
        # is declared and stay invalid until the flag clears
        g1_ok, g2_ok = not flags[0], not flags[1]
        g1_fresh = g1_ok and silent_since[0] is None and declare_at[0] is None
        g2_fresh = g2_ok and silent_since[1] is None and declare_at[1] is None
        # what the controller reads (refreshed every second while the session answers)
        if abs(t * 10 % 10) < 1e-6 or abs(t * 10 % 10 - 10) < 1e-6:
            if g1_fresh:
                ap_reading = pv
            if g2_fresh:
                max_reading = MAX_AVAIL_KW
        ap_val = ap_reading if g1_ok else None
        if _in(t, sc.ap_zero):
            ap_val = 0.0
        if _in(t, sc.ap_invalid):
            ap_val = None
        max_val = max_reading if g2_ok else None
        gen = 0.0 if sc.on_grid else L - pv
        inputs = plant_inputs(pv_kw=pv, max_avail_kw=MAX_AVAIL_KW, curtailment_pct=curtail, gensets_kw=gen,
                              running_nominal_kw=run_nom, on_grid=sc.on_grid, gens_loaded=0,
                              comm_fail=(flags[0], flags[1]),
                              mains_import_kw=((L - pv) / 3,) * 3 if sc.on_grid else (0, 0, 0))
        inputs[19433] = ap_val
        inputs[19424] = max_val
        for i in range(5):
            inputs[(10196, i)] = int(i in sc.gensets and not sc.on_grid)
        if sc.on_grid:
            for k, n in enumerate((10169, 10170, 10752)):
                inputs[(n, 0)] = int(sc.mcb[k])
        plc.step(inputs, DT)
        setpoint = plc.get(21267) or 0.0
        # group 1 writes the setpoint every second while it gets replies; group 2 writes nothing (8 Sep)
        if abs(t * 10 % 10) < 1e-6 or abs(t * 10 % 10 - 10) < 1e-6:
            if silent_since[0] is None:
                pending.append((t + INV_DELAY_S, setpoint))
        while pending and pending[0][0] <= t:
            received = pending.pop(0)[1]
        target = min(avail, max(0.0, received))
        if target > pv:
            pv = min(target, pv + INV_UP_KW_S * DT)
        else:
            pv = max(target, pv - INV_DOWN_KW_S * DT)
        # the curtailment regulator of the controller: integral on gen load, frozen while the values are invalid
        if not sc.on_grid and run_nom and ap_val is not None and max_val is not None and not flags[0] and not flags[1]:
            err = (L - pv) / run_nom * 100 - GEN_P_MIN
            if not (err > 0 and setpoint < (plc.get(21265) or 0) - 50):   # no winding up while the ramp climbs
                curtail = max(0.0, min(100.0, curtail + 0.03 * err * DT))
        # gensets and their protection
        if t >= 0:
            if not sc.on_grid:
                min_gen = min(min_gen, gen)
                gen_hist.append((t, gen))
                while gen_hist and gen_hist[0][0] < t - 15:
                    gen_hist.pop(0)
                max_step = max(max_step, gen - min(g for (_, g) in gen_hist))
                if gen < -RP_PCT / 100 * run_nom:
                    rp_t += DT
                    rp_seconds += DT
                    if rp_t >= RP_DELAY_S:
                        tripped = True
                else:
                    rp_t = 0.0
            if sc.on_grid:
                max_export = max(max_export, pv - L)
                if pv - L > cap + 1:
                    export_over += DT
            if abs(t * 10 % 10) < 1e-6:
                trace.append((round(t), round(L), round(pv), round(gen), round(setpoint), flags[0], flags[1]))
        t = round(t + DT, 1)
    return Result(min_gen_kw=min_gen if not sc.on_grid else 0.0,
                  min_gen_pct=(min_gen / run_nom * 100) if run_nom else 0.0,
                  rp_seconds=round(rp_seconds, 1), tripped=tripped, max_step_kw=max_step,
                  export_over_s=round(export_over, 1), max_export_kw=max_export, trace=trace)


def pv_energy(prog_name: str, sc: Scenario) -> float:
    """PV energy delivered in the scenario window, kWh."""
    r = run(prog_name, sc)
    return sum(row[2] for row in r.trace) / 3600


# --------------------------------------------------------------------------------------------- scenarios
def scenarios() -> list[tuple[str, Scenario, str]]:
    return [
        ("8 Sep pause", Scenario("8 Sep pause", silent=[(10, 12.5, (0, 1))]),
         "The SAU silent 2.5 s on both sessions, load 2,000 kW, three gensets (Gen 1, 4, 5)."),
        ("8 Sep pause, AP read as 0", Scenario("8 Sep pause, AP read as 0", silent=[(10, 12.5, (0, 1))], ap_zero=[(12.5, 12.8)]),
         "As above, and for 0.3 s after the SAU is back the AP read returns 0 (the mispaired frame) before the fail bit."),
        ("group 2 lost 10 s", Scenario("group 2 lost 10 s", silent=[(10, 20, (1,))]),
         "Only the reading session fails for 10 s; group 1 keeps writing. No load change."),
        ("group 2 lost, cloud and sun back", Scenario("group 2 lost, cloud", silent=[(10, 20, (1,))], clouds=[(14, 19, 500.0)]),
         "Group 2 lost 10 s; a cloud takes the sun down to 500 kW for 5 s and passes."),
        ("all lost 30 s", Scenario("all lost 30 s", silent=[(10, 40, (0, 1))]),
         "Both sessions silent 30 s: longer than v4's 10 s hold (v3: 60 s)."),
        ("AP invalid 8 s, no fail bit", Scenario("AP invalid", ap_invalid=[(10, 18)]),
         "The AP value invalid for 8 s while both ECU fail bits stay 0."),
        ("on the grid, group 2 lost, load drop", Scenario("grid", on_grid=True, mcb=(1, 0, 0), silent=[(10, 20, (1,))],
                                                          load_kw=1500, avail_kw=3000, load_steps=[(15, -1000)]),
         "On the grid through MCB 1 only (export limit 1,300 kW), exporting at the limit; group 2 lost 10 s and the "
         "load drops 1,000 kW at 15 s. A hold would push the export over the limit; v4 does what today does."),
    ]


def load_drop(kw: float, sets=(0, 3, 4), sessions=(1,), at=15.0, load=2000.0) -> Scenario:
    return Scenario(f"drop {kw:.0f}", gensets=sets, load_kw=load, silent=[(10, 20, sessions)], load_steps=[(at, -kw)])


def sweep(sets, sessions=(1,), load=2000.0, step=100, top=1800) -> dict:
    out = {}
    for p in ("today", "v3", "v4"):
        limit = None
        for kw in range(0, top + step, step):
            r = run(p, load_drop(kw, sets, sessions, load=load))
            if r.tripped:
                break
            limit = kw
        out[p] = limit
    return out


def report() -> str:
    L = ["# Change 01 v4 (the guard): closed-loop test", "",
         "The master's PLC program run at 100 ms against a small plant model (tools/change01_sim.py). "
         "Reverse power trips the gensets at -10 % of the running nominal for 5 s (IG4, 17 Oct).", "",
         "## Scenarios", "",
         "| Scenario | Program | Lowest genset load | Reverse power | Trip | Biggest step on the gensets (15 s) | Export over limit | PV energy in window |",
         "|---|---|---|---|---|---|---|---|"]
    for title, sc, _ in scenarios():
        for p in ("today", "v3", "v4"):
            r = run(p, sc)
            e = sum(row[2] for row in r.trace) / 3600
            L.append(f"| {title} | {p} | {r.min_gen_kw:.0f} kW ({r.min_gen_pct:.0f} %) | {r.rp_seconds:.1f} s | "
                     f"{'YES' if r.tripped else 'no'} | {r.max_step_kw:.0f} kW | {r.export_over_s:.0f} s | {e:.1f} kWh |")
    L += ["", "Scenario notes:", ""]
    L += [f"- **{t}**: {n}" for t, _, n in scenarios()]
    L += ["", "## Load drop while one session is lost (the hold's real exposure)", "",
          "Group 2 lost from 10 s to 20 s, group 1 still writing; the load drops at 15 s. The figure is the largest "
          "drop (100 kW steps) the gensets survive without a reverse-power trip.", "",
          "| Gensets running | Load | today | v3 (plain hold) | v4 (hold + guard) |", "|---|---|---|---|---|"]
    for sets, label in (((3, 4), "Gen 4 + Gen 5 (2,150 kW)"), ((0, 3, 4), "Gen 1 + 4 + 5 (3,150 kW)")):
        for load in (1500.0, 2000.0):
            s = sweep(sets, load=load)
            fmt = lambda v: "none" if v is None else (f">= {v} kW" if v >= 1800 else f"{v} kW")
            L.append(f"| {label} | {load:.0f} kW | {fmt(s['today'])} | {fmt(s['v3'])} | {fmt(s['v4'])} |")
    L += ["", "With group 1 lost (no writes reach the SAU) every program is equally blind: whatever the PLC computes "
          "cannot reach the inverters until the link is back.", ""]
    s = sweep((0, 3, 4), sessions=(0,))
    L.append(f"Group 1 lost, three sets, load 2,000 kW: today {s['today']} kW, v3 {s['v3']} kW, v4 {s['v4']} kW.")
    L += ["", "## What limits the guard", "",
          "The guard lowers the request faster than the inverters can follow, so the limit is how fast the inverters "
          "come down (166 kW/s measured on 8 Sep) and how long a write takes to act (1 s assumed). Largest survivable "
          "drop for v4, Gen 4 + Gen 5, load 2,000 kW:", "", "| Inverter fall rate | v4 |", "|---|---|"]
    global INV_DOWN_KW_S
    keep = INV_DOWN_KW_S
    try:
        for rate in (100.0, 166.0, 300.0):
            INV_DOWN_KW_S = rate
            lim = None
            for kw in range(500, 2100, 100):
                if run("v4", load_drop(kw, (3, 4))).tripped:
                    break
                lim = kw
            L.append(f"| {rate:.0f} kW/s | {lim} kW |")
    finally:
        INV_DOWN_KW_S = keep
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out")
    a = ap.parse_args()
    text = report()
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
