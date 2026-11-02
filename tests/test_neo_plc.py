"""The controller's PLC program, read from the sheet exports and executed."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from neo_plc import Plc, describe, load_program, plant_inputs  # noqa: E402


def settle(plc, inputs, seconds=120, dt=0.1):
    for _ in range(int(seconds / dt)):
        plc.step(inputs, dt)
    return plc


def test_program_loads_completely():
    prog = load_program()
    assert [s.index for s in prog.sheets] == list(range(1, 12))
    assert len(prog.blocks) == 88
    assert prog.setpoints[65525] == "PV req step protetion"
    text = describe(prog)
    assert "PV req out ramp" in text and "ECU write: PV active power setpoint" in text
    assert all(s in text for s in ("UPS breakers math", "PV req step protection", "PV Q request"))


def test_grid_request_is_the_sau_maximum_and_step_protected():
    # 1 Oct 08:19 in the history: on the grid, PV 1,445 kW, three feeders exporting, recorded request 1,545.3 kW
    plc = settle(Plc(load_program()), plant_inputs(pv_kw=1445, curtailment_pct=0, gensets_kw=0, running_nominal_kw=0,
                                                   gens_loaded=0, on_grid=True, mains_import_kw=(-399, -370, -341)))
    assert plc.get(21265) == 3520                 # PV req (kW) = Max Active Power Available
    assert abs(plc.get(21269) - 1545) < 0.01      # PV req protected = actual + 100
    assert abs(plc.get(21267) - 1545) < 0.01      # setpoint written to the SAU
    assert plc.get(21249) == 0                    # reactive power request is always 0


def test_genset_request_follows_the_curtailment_percentage():
    plc = settle(Plc(load_program()), plant_inputs(pv_kw=1000, curtailment_pct=31.0, gensets_kw=790, running_nominal_kw=3150))
    assert abs(plc.get(21265) - 3520 * 0.31) < 0.01
    assert abs(plc.get(21267) - 1091.2) < 0.01
    assert abs(plc.get(21263) - 790 / 3150 * 100) < 0.01       # Gen Load %
    assert plc.get(10435, 6) == 1 and plc.get(10435, 7) == 1     # UPS commands: breakers closed
    assert plc.get(10425, 2) == 1 and plc.get(10426, 2) == 1     # PV CB1/CB2 ON


def test_comm_fail_forces_zero_then_ramps_25_kw_per_s():
    plc = settle(Plc(load_program()), plant_inputs(pv_kw=1000, curtailment_pct=60.0, gensets_kw=790, running_nominal_kw=3150))
    assert abs(plc.get(21267) - 1100) < 0.01      # step protection: never more than actual + 100
    settle(plc, plant_inputs(pv_kw=1000, curtailment_pct=60.0, gensets_kw=790, running_nominal_kw=3150, comm_fail=(1, 0)), 1)
    assert plc.get(21267) == 0                    # one session lost for one second -> setpoint 0
    settle(plc, plant_inputs(pv_kw=1000, curtailment_pct=60.0, gensets_kw=790, running_nominal_kw=3150), 10)
    assert 240 <= plc.get(21267) <= 250           # back: 25 kW/s


def test_ups_shedding_from_the_stale_pv_value():
    # 14 Oct 12:25: PV breakers opened at 1,041 kW, gensets pick up 1,829 kW, the controller's PV term is already 0
    plc = settle(Plc(load_program()), plant_inputs(pv_kw=1041, curtailment_pct=29.6, gensets_kw=788, running_nominal_kw=3150))
    assert plc.get(10435, 6) == 1
    settle(plc, plant_inputs(pv_kw=1041, curtailment_pct=29.6, gensets_kw=1829, running_nominal_kw=3150,
                             neo_pv_sample_kw=0, pvcb_close=0), 1)
    assert abs(plc.get(21284) - 2109) < 0.01      # Running nominal Gensets = 3,150 - 1,041
    assert plc.get(21263) > 80                    # Gen Load inflated
    assert plc.get(10435, 6) == 0 and plc.get(10435, 7) == 0
    settle(plc, plant_inputs(pv_kw=210, curtailment_pct=29.6, gensets_kw=1829, running_nominal_kw=3150,
                             neo_pv_sample_kw=0, pvcb_close=0), 10)
    assert abs(plc.get(21284) - 2940) < 0.01      # "Gen Nom" 2,940 in the history row of 12:25:29
    assert plc.get(10435, 6) == 0                 # hysteresis: stays shed above 35 %
    assert plc.get(10425, 2) == 0                 # PV breakers commanded open


def test_overproduction_warning_and_latch():
    plc = Plc(load_program())
    settle(plc, plant_inputs(pv_kw=1000, curtailment_pct=20.0, gensets_kw=800, running_nominal_kw=3150), 30)
    assert plc.get(10433, 2) == 1                 # PV 1,000 > request 704 + 75: warning bit at once (alarm delay 15 s is outside the PLC)
    assert plc.get(10424, 4) == 0                 # the latch waits for the 60 s delay
    settle(plc, plant_inputs(pv_kw=1000, curtailment_pct=20.0, gensets_kw=800, running_nominal_kw=3150), 40)
    assert plc.get(10424, 4) == 1                 # "PV prod > PV req BRKR open" latched (not wired to any function)
    settle(plc, plant_inputs(pv_kw=1000, curtailment_pct=20.0, gensets_kw=800, running_nominal_kw=3150, fault_reset=1), 1)
    assert plc.get(10424, 4) == 0


def test_plant_model_runs_the_program():
    import plant_model as pm
    r = pm.simulate(pm.SCENARIOS[0], pm.DAYS[0])
    assert 5000 < r.pv_used_kwh < 9000
    assert 3 <= r.overproduction_warnings <= 20
    assert r.overload_minutes == 0
    assert r.ups_shed_events >= 1                 # the 12:25 toggle sheds the UPS in the model too
