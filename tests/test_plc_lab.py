"""The live plant behind the PLC lab: ticks, injections, program switch, sheet view."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import plc_lab  # noqa: E402


def run(p, seconds):
    for _ in range(seconds):
        p._tick()


def test_today_zeroes_on_a_comm_fail_and_the_timed_variant_holds():
    p = plc_lab.LivePlant(start_hour=10.0)
    run(p, 60)
    before = p.snapshot["setpoint"]
    assert before > 300 and p.snapshot["gensets"] == 3 and not p.snapshot["on_grid"]
    p.inject("comm12", 20)                      # the SAU gives no replies for 20 s
    run(p, 5)
    assert p.snapshot["silent"] == [1, 1] and p.snapshot["comm"] == [0, 0]       # not declared yet: timeout x retries
    run(p, 5)
    assert p.snapshot["comm"] == [1, 1] and p.snapshot["setpoint"] == 0            # declared after 9 s: request 0
    assert p.snapshot["received"] == before                                        # but nothing reaches a silent SAU
    run(p, 15)
    assert p.snapshot["comm"] == [0, 0] and 0 < p.snapshot["setpoint"] < before     # link back: climbing from 0
    assert p.snapshot["received"] == p.snapshot["setpoint"]                        # and now it reaches the SAU
    assert any("PV setpoint forced to 0" in e[1] for e in p.events)
    p.set_program("change-01 v3 (hold 60 s, then 0)")
    run(p, 120)
    held = p.snapshot["setpoint"]
    p.inject("comm12", 40)
    run(p, 20)
    s20 = p.snapshot["setpoint"]
    run(p, 15)
    assert abs(s20 - held) < 150 and p.snapshot["setpoint"] == s20 and s20 > 0     # held through a 35 s fail (frozen at the value of the moment the fail was declared)
    p.inject("comm12", 120)
    run(p, 100)
    assert p.snapshot["setpoint"] == 0                                              # declared at 9 s, held 60 s, then 0 as today


def test_sheet_view_lists_blocks_pins_values_and_wires():
    p = plc_lab.LivePlant(start_hour=12.0)
    run(p, 5)
    v = p.sheet_view(10)
    assert v["name"].startswith("PV req step") and len(v["blocks"]) == 16 and len(v["wires"]) > 10
    ramp = next(b for b in v["blocks"] if b["type"] == "Ramp")
    assert ramp["inputs"][1]["value"] == "25" and ramp["inputs"][2]["value"] == "1500"   # the ramp constants, scaled
    assert ramp["outputs"][0]["name"] == "PV req out ramp"
    assert all(len(s) == 2 for s in [(b["x"], b["y"]) for b in v["blocks"]])
    assert [s["index"] for s in v["sheets"]] == list(range(1, 12))


def test_injections_change_the_plant():
    p = plc_lab.LivePlant(start_hour=11.0)
    p.params["load_noise"] = 0
    run(p, 30)
    load = p.snapshot["load"]
    p.inject("load", 300)
    run(p, 2)
    assert p.snapshot["load"] == load + 300
    p.inject("off", 30)
    run(p, 5)
    assert p.snapshot["pvcb_close"] == 0 and p.snapshot["pv_actual"] == 0
    p.inject("grid")
    run(p, 2)
    assert p.snapshot["on_grid"] and p.snapshot["gensets"] == 0
    p.inject("param:writes_through_silence", 1)
    assert p.params["writes_through_silence"] == 1


def test_one_silent_session_lets_the_zero_through_the_other():
    p = plc_lab.LivePlant(start_hour=11.0)
    run(p, 60)
    p.inject("comm1", 40)
    run(p, 10)
    assert p.snapshot["comm"] == [1, 0] and p.snapshot["setpoint"] == 0
    run(p, 10)
    assert p.snapshot["received"] == 0                                             # session 2 writes every 10 s


def test_replay_runs_the_program_on_recorded_values(tmp_path):
    neo = tmp_path / "neo-01_2026-10-16.csv"
    sau = tmp_path / "pv-array_2026-10-16.csv"
    cols = ["ts", "ms", "ok_blocks", "failed_blocks", "pv_prod_kw", "power_kw", "running_nominal_kw", "pv_curtailment_pct", "mains_power_kw",
            "ecu2_comm_fail", "ecu3_comm_fail", "mcb_status", "pvcb_closed", "pv2cb_closed", "ups1_feedback", "ups2_feedback",
            "g1_running", "g2_running", "g3_running", "g4_running", "g5_running", "pv_run_request"]
    with open(neo, "w") as f:
        f.write(",".join(cols) + "\n")
        for s in range(180):
            fail = 1 if 60 <= s < 70 else 0
            f.write(f"2026-10-16T09:00:{0:02d}Z".replace(":00Z", f":{s % 60:02d}Z").replace("09:00", f"09:{s // 60:02d}") +
                    f",5,3,0,{1000 if not fail else ''},790,3150,31,0,{fail},{fail},0,1,1,1,1,1,1,1,0,0,1\n")
    with open(sau, "w") as f:
        f.write("ts,ms,ok_blocks,failed_blocks,set_2100,set_2101\n")
        for s in range(180):
            v = 0 if 60 <= s < 75 else 10912
            f.write(f"2026-10-16T09:{s // 60:02d}:{s % 60:02d}Z,4,3,0,0,{v}\n")
    src = plc_lab.ReplaySource(str(neo), str(sau))
    p = plc_lab.LivePlant(replay=src)
    assert p.snapshot == {} and p.t == 9 * 3600
    run(p, 55)
    assert p.snapshot["replay"] and abs(p.snapshot["setpoint"] - 1091.2) < 0.1 and p.snapshot["received"] == 1091.2
    run(p, 10)
    assert p.snapshot["comm"] == [1, 1] and p.snapshot["setpoint"] == 0 and p.snapshot["received"] == 0
    run(p, 40)
    assert p.snapshot["comm"] == [0, 0] and 0 < p.snapshot["setpoint"] < 1091.2
    run(p, 100)
    assert p.paused                                                                 # end of the file


def test_scenarios_reset_and_counters():
    p = plc_lab.LivePlant(start_hour=10.0)
    run(p, 120)
    T = p.snapshot["totals"]
    assert T["hours"] == 0.03 and T["pv_used_kwh"] > 0 and T["fuel_l"] > 0 and 20 < T["min_gen_load_pct"] <= 30
    p.apply_scenario("Two gensets in the sun")
    assert p.snapshot == {} and p.program_name.startswith("change-01 v3") and p.manual_gensets == 2
    run(p, 60)
    assert p.snapshot["gensets"] == 2 and p.snapshot["totals"]["hours"] == 0.02
    p.apply_scenario("AUTO + change 01 v3 + UPS fixes")
    assert p.auto_pm and p.ups_math_fix and p.ups_shed_delay_s == 10
    p.apply_scenario("Grid day with export")
    run(p, 30)
    assert p.snapshot["on_grid"] and p.snapshot["gensets"] == 0 and p.snapshot["request"] == 3520
    p.apply_scenario(plc_lab.SCENARIOS[0]["name"])
    run(p, 10)
    p.inject("load", 500)
    p.reset()
    assert p.snapshot == {} and p.load_offset == 0 and p.totals["seconds"] == 0 and p.t == 10 * 3600
    assert [x["name"] for x in plc_lab.SCENARIOS][1].startswith("Change 01 v3")


def test_integrators_example_and_the_assumed_reverse_power_trip():
    """His example: 1 MW of solar, the link lost, the load drops at the same time. With both sessions silent the zero
    cannot reach the SAU in either program, so the exposure is the same; with one session alive today's program
    delivers the zero through the other session within 10 s and the hold does not."""
    def case(scenario, what, kw):
        p = plc_lab.LivePlant(start_hour=11.0)
        p.apply_scenario(scenario)
        p.params["load_events"] = 0
        p.params["load_noise"] = 0
        run(p, 120)
        p.inject(what, kw)
        lows = []
        for _ in range(90):
            p._tick()
            lows.append(p.snapshot["gen_kw"])
        return min(lows), p.snapshot["totals"]["rp_trips"]
    today, v3 = plc_lab.SCENARIOS[0]["name"], plc_lab.SCENARIOS[1]["name"]
    assert case(today, "integrator_both", 1200)[1] == 1 and case(v3, "integrator_both", 1200)[1] == 1   # both trip: the zero cannot get through
    low_today, trips_today = case(today, "integrator_one", 800)
    low_v3, trips_v3 = case(v3, "integrator_one", 800)
    assert trips_today == 0 and trips_v3 == 0                                            # below the assumed trip level either way
    assert low_today > low_v3 + 30                                                       # the zero arrives through session 2 (the SAU ramps down at 5 kW/s), the hold keeps the solar up


def test_random_production_steps_appear_and_pass():
    p = plc_lab.LivePlant(start_hour=11.0)
    p.params["load_event_minutes"] = 0.5
    run(p, 600)
    assert any("production step" in e[1] for e in p.events)
    assert p.snapshot["load"] > 0


def test_one_genset_with_the_ups_as_buffer():
    """One set carries the floor. A surge is fine; a total solar loss is survived only if the UPS shed enough load
    while the next set starts; a load drop during a held setpoint pushes one set into reverse power."""
    one = next(x["name"] for x in plc_lab.SCENARIOS if x["name"].startswith("One genset"))

    def trial(what, value, **pp):
        p = plc_lab.LivePlant(start_hour=12.0)
        p.apply_scenario(one)
        p.params["load_events"] = 0
        p.params["load_noise"] = 0
        p.params.update(pp)
        run(p, 180)
        assert p.snapshot["gensets"] == 1 and abs(p.snapshot["gen_load_pct"] - 25) < 1
        p.inject(what, value)
        peak = 0
        for _ in range(300):
            p._tick()
            peak = max(peak, p.snapshot["gen_load_pct"])
        T = p.snapshot["totals"]
        return peak, T["overload_trips"], T["rp_trips"], T["ups_sheds"], p.snapshot["gensets"]

    peak, ol, rp, sheds, sets = trial("load_event", 400)
    assert peak < 70 and ol == 0 and rp == 0
    peak, ol, rp, sheds, sets = trial("off", 60)                     # total loss, UPS 300 kW each (assumed)
    assert ol == 0 and sheds == 2 and sets == 2                      # the UPS go to battery, the second set comes
    peak, ol, rp, sheds, sets = trial("off", 60, ups_kw_each=100)    # small UPS: the single set is overloaded and trips
    assert ol >= 1
    peak, ol, rp, sheds, sets = trial("integrator_one", 500)         # held setpoint + load drop on one set
    assert rp >= 1
