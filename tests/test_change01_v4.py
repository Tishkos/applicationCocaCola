"""Change 01 v4 (hold + guard): the sheets build within the PLC's free resources and in the plant's block order,
the program is identical to today's while the link is healthy, and in closed loop it rides through pauses, survives
the AP race, keeps the gensets out of reverse power on load drops a plain hold does not survive, and falls back to
today's 0 on the grid, with one genset and after the hold time."""

import os
import random
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from neo_plc import Plc, load_program, plant_inputs  # noqa: E402
import change01_sim as sim  # noqa: E402

OUT = os.path.join(ROOT, "docs", "changes", "change-01_comm_fail_tolerance")
V4 = os.path.join(OUT, "program_v4")
KEYS = [21265, 21269, 21267, 21263, (10435, 6), (10435, 7), (10425, 2), (10426, 2), (10433, 2), 21249]


def settle(plc, inputs, seconds, dt=0.1):
    for _ in range(int(round(seconds / dt))):
        plc.step(inputs, dt)
    return plc


def snap(plc):
    return [plc.get(*k) if isinstance(k, tuple) else plc.get(k) for k in KEYS]


def test_v4_builds_within_free_resources_and_in_plant_order():
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "neo_plc_change01.py")], check=True, capture_output=True)
    for f in ("sheet10_PV_req_step_protection_v4_guard.xml", "sheet06_Validation_v4.xml", "program_v4.md"):
        assert os.path.exists(os.path.join(OUT, f))
    today, v4 = load_program(), load_program(V4)
    assert len(v4.blocks) == len(today.blocks) + 13
    assert sum(b.type_id == 5 for b in v4.blocks) == 7                  # no Analog Switch added (none left)
    outs = [o.key for b in v4.blocks for o in b.outputs if o.connected]
    assert len(outs) == len(set(outs))                                   # every output on its own object
    old = {o.key for b in today.blocks for o in b.outputs if o.connected}
    new = set(outs) - old
    assert {k for k in new if isinstance(k, tuple)} <= {(10436, b) for b in range(2, 8)} | {(10430, 0)}
    assert {k for k in new if not isinstance(k, tuple)} <= set(range(21286, 21293))
    for s in v4.sheets:                                                  # listed order = position order, as the plant's
        pos = [(b.x, b.y) for b in s.blocks]
        assert pos == sorted(pos), s.name
    for n in (21288, 21292):
        assert v4.resolution[n] == 1
    ramp = next(b for b in v4.blocks if b.type_id == 61 and b.sheet_index == 10)
    assert ramp.inputs[1].const == 25 and ramp.inputs[2].const == 1500   # the ramp keeps its 25 / 1500 kW/s


def test_floor_and_two_sets_follow_the_loaded_sets():
    plc = Plc(load_program(V4))
    for sets, floor in (((0, 3, 4), 150 + 150 + 172), ((3, 4), 322), ((2,), 105), ((0, 1, 2, 3, 4), 727), ((), 0), ((4,), 172)):
        inp = plant_inputs(pv_kw=1000, curtailment_pct=31.0, gensets_kw=790, running_nominal_kw=3150)
        for i in range(5):
            inp[(10196, i)] = int(i in sets)
        settle(plc, inp, 0.3)
        assert plc.get(21289) == floor
        assert plc.get(10436, 3) == int(len(sets) >= 2)


def test_identical_to_today_while_the_link_is_healthy():
    cases = [dict(pv_kw=1000, curtailment_pct=31.0, gensets_kw=790, running_nominal_kw=3150),
             dict(pv_kw=1000, curtailment_pct=60.0, gensets_kw=790, running_nominal_kw=3150),
             dict(pv_kw=1000, curtailment_pct=31.0, gensets_kw=200, running_nominal_kw=3150),   # gensets low, link fine
             dict(pv_kw=1445, curtailment_pct=0, gensets_kw=0, running_nominal_kw=0, gens_loaded=0, on_grid=True,
                  mains_import_kw=(-399, -370, -341)),
             dict(pv_kw=1000, curtailment_pct=0.0, gensets_kw=790, running_nominal_kw=3150),
             dict(pv_kw=1000, curtailment_pct=31.0, gensets_kw=790, running_nominal_kw=3150, manual_pv=1)]
    for sp in ({}, {65527: -100.0}):                                     # a negative manual request: today sends 0
        a, b = Plc(load_program(), sp), Plc(load_program(V4), sp)
        for c in cases:
            settle(a, plant_inputs(**c), 120)
            settle(b, plant_inputs(**c), 120)
            assert snap(a) == snap(b)
            assert b.get(10436, 6) == 0                                   # the guard sleeps
            assert (b.get(21267) or 0) >= 0


def test_identical_to_today_in_random_healthy_transients():
    rnd = random.Random(7)
    a, b = Plc(load_program()), Plc(load_program(V4))
    for _ in range(150):
        c = dict(pv_kw=rnd.uniform(0, 2500), curtailment_pct=rnd.uniform(0, 80), gensets_kw=rnd.uniform(0, 2500),
                 running_nominal_kw=rnd.choice((2150, 3150)), manual_pv=int(rnd.random() < 0.1))
        inp = plant_inputs(**c)
        for _ in range(rnd.randint(1, 15)):
            a.step(inp, 0.1)
            b.step(inp, 0.1)
            assert a.get(21267) == b.get(21267)


def test_closed_loop_pause_is_ridden_through():
    sc = sim.scenarios()[0][1]
    today, v4 = sim.run("today", sc), sim.run("v4", sc)
    assert today.max_step_kw > 1000                                       # today: the 2.5 s pause throws ~1.1 MW on the gensets
    assert v4.max_step_kw < 50 and not v4.tripped


def test_closed_loop_ap_race():
    sc = sim.scenarios()[1][1]
    v3, v4 = sim.run("v3", sc), sim.run("v4", sc)
    assert v3.max_step_kw > 900                                           # v3 holds the corrupted request
    assert v4.max_step_kw < 200                                           # v4 holds what was sent: one ramp cycle at most


def test_closed_loop_load_drop_one_session_lost():
    for kw, sets in ((1300, (0, 3, 4)), (900, (3, 4))):
        v3, v4 = sim.run("v3", sim.load_drop(kw, sets)), sim.run("v4", sim.load_drop(kw, sets))
        assert v3.tripped                                                 # plain hold: reverse power trip
        assert not v4.tripped and v4.rp_seconds < 4                       # the dip is physics; the guard ends it inside the 5 s


def test_guard_sleeps_when_the_gensets_are_fine():
    for sc in (sim.scenarios()[2][1], sim.load_drop(200, sets=(0, 3, 4))):
        r = sim.run("v4", sc)
        held = [row[4] for row in r.trace if 14 <= row[0] <= 21]
        assert max(held) - min(held) < 2                                  # nothing lowered


def test_writer_blind_does_not_wind_the_hold_down():
    sc = sim.load_drop(600, sets=(0, 3, 4), sessions=(0,))                # group 1 (the writer) lost, load drop
    today, v3, v4 = (sim.run(p, sc) for p in ("today", "v3", "v4"))
    assert v4.max_step_kw < v3.max_step_kw + 150 and v4.max_step_kw < today.max_step_kw


def test_late_writer_flag_costs_no_more_than_today():
    # the writing session goes silent while the fail is already on, and its own flag only rises when it answers
    # again (the 8 Sep pattern applied to a long silence): the guard lowers blind, but at most 4 s of it, and the
    # 10 s hold ends before the writer is back
    keep = sim.DECLARE_LONG_S
    sim.DECLARE_LONG_S = 1e9
    try:
        sc = sim.Scenario("late flag", ap_invalid=[(10, 20)], silent=[(12, 20, (0,))], load_steps=[(13, -600)])
        today, v4 = sim.run("today", sc), sim.run("v4", sc)
    finally:
        sim.DECLARE_LONG_S = keep
    assert v4.max_step_kw < today.max_step_kw + 100 and not v4.tripped


def test_grid_falls_back_to_today():
    sc = sim.scenarios()[6][1]                                            # MCB 1 only, exporting at the limit, load drop
    today, v3, v4 = (sim.run(p, sc) for p in ("today", "v3", "v4"))
    assert v3.export_over_s > 10                                          # a hold on the grid pushes the export over the limit
    assert v4.export_over_s <= today.export_over_s + 1


def test_one_genset_falls_back_to_today():
    sc = sim.Scenario("one set", gensets=(3,), load_kw=1000, silent=[(10, 20, (1,))])
    today, v4 = sim.run("today", sc), sim.run("v4", sc)
    assert [r[4] for r in v4.trace if 14 <= r[0] <= 20] == [r[4] for r in today.trace if 14 <= r[0] <= 20]


def test_long_fail_still_goes_to_zero_at_the_inverters():
    sc = sim.Scenario("group 2 lost 75 s", silent=[(10, 85, (1,))])        # the writer works: the 0 reaches the SAU
    r = sim.run("v4", sc)
    assert all(row[4] == 1214 for row in r.trace if 14 <= row[0] <= 22)   # held: declared at 13 s, 10 s hold
    assert all(row[4] == 0 for row in r.trace if 25 <= row[0] <= 84)      # then 0, as today
    assert min(row[2] for row in r.trace if 25 <= row[0] <= 40) < 50       # and the inverters follow


def test_pauses_up_to_ten_seconds_are_ridden_through():
    for p, held in ((2.5, True), (8, True), (20, False)):
        r = sim.run("v4", sim.Scenario("pause", silent=[(10, 10 + p, (0, 1))]))
        assert (r.max_step_kw < 50) == held


def test_stuck_invalid_value_holds_instead_of_capping():
    sc = sim.scenarios()[5][1]
    today, v4 = sim.run("today", sc), sim.run("v4", sc)
    assert today.max_step_kw > 1000 and v4.max_step_kw < 50
