# Plant behaviour model — `tools/plant_model.py`

A model of what the controller decides over a day, so a modification can be tried on paper before anyone
touches the plant. The controller's PLC program is not reconstructed: the eleven sheets exported from
InteliConfig are executed block by block (`tools/neo_plc.py`, 10 s steps) with the plant's setpoints —
PV request on grid and on gensets, step protection, comm-fail handling, ramp, overproduction warning, UPS
shedding and breaker commands are the plant's own logic. Around it the model supplies what the PLC reads:
the load, the sun, the gensets (manual or the controller's power management), the controller's
curtailment regulator and the SAU/inverter response — the parts marked ASSUMED below.

Run: `python tools/plant_model.py` → table on screen, `docs/simulation/scenarios.csv`, `docs/simulation/one_day.png`.

## 1. What is real and what is assumed

| Item | Value | Source |
|---|---|---|
| **PLC program** | all 11 sheets, 88 blocks, executed as exported | EXACT — `docs/site/neo_plc_2026-10-14` |
| PLC setpoints | step 100 kW, PV prod > req 75 kW / 60 s, UPS 70-35 / 80-35 %, UPS delays 10 s, 1 breaker 2,200 kW, export 1,300 kW, X1/X2 0/100 % | REAL (archive) |
| SAU "Maximum Active Power Available" | 3,520 kW, constant | REAL (history, every row) |
| Gen P Min, curtailment gain / int | 25 %, 5 / 15 s | REAL setpoints |
| Start / stop reserve, min run power, delays | 300 / 400 / 210 kW, 20 / 180 s | REAL |
| Dynamic spinning reserve, solar coverage ratio | enabled, 20 % | REAL |
| Fuel at full load | 237.3 g/kWh → 0.283 l/kWh | REAL setpoint, diesel 0.84 kg/l |
| **Grid cycle** | on the government grid ~18:05 → ~06:12, gensets by day | REAL, history (every day) |
| **Night load** | 650 kW | REAL, mains import at the transfers (574-678 kW) |
| **Genset nominal steps** | 1,150 / 1,000 / 1,000 / 1,000 / 700 kW | from the history's "Gen Nom" values (nameplates to confirm) |
| **Today's operation** | 3 gensets by hand (3,150 kW), a 4th in the afternoon | REAL, history of 14 Oct |
| **PV interruptions of 14 Oct** | five OFF/AUTO toggles (PVCB opened), three comm-fail bursts | REAL, history, by type |
| Comm-fail burst shape | 10 s fail every 30 s during a burst | ASSUMED from the 2-33 lines per burst |
| Controller curtailment regulator | integrating on genset load − Gen P Min, 0.03 %/s per % | ASSUMED structure, rate CALIBRATED: 8 warnings on 14 Oct vs 9 recorded |
| SAU/inverter response to a setpoint | first order, 10 s | ASSUMED |
| Modbus refresh of "PV Actual P" | one PLC step (10 s) old | ASSUMED (real: 1-5 s) |
| Daytime load | 1,450-1,850 kW through recorded points (07:16, 10:30, 12:25, 13:06, 17:28) | REAL points, ASSUMED in between |
| PV available | clear-sky bell, sunrise 06:05, sunset 17:45, peak 78 % of rated (17 MWh/day) | ASSUMED (a pyranometer or the collector's daily curve will replace it) |
| Cloud passages | 8 drops of 40-70 % lasting 5-20 min (cloudy day) | ASSUMED |
| Fuel curve shape | 10 % of full-load fuel at idle, linear to full load (0.37 l/kWh at 25 %) | ASSUMED typical diesel |
| UPS critical load, autonomy | 5 % of plant load per UPS, 30 min | ASSUMED |

**Validation (14 Oct as recorded)**: 5,580 kWh of PV until 13:20 against 6,016 kWh on the inverters' own
counters at the time of the export (−7 %); 8 overproduction warnings against 9 recorded by 13:06; 3 UPS
shedding commands against 2 recorded (both at 12:25, reproduced by the same mechanism); 40 engine-hours
against ~36-40. Two exact checks of the PLC chain: the recorded request 1,545.3 kW on 1 Oct 08:19 =
1,445 + 100 (step protection), and "Gen Nom" 2,940 at 12:25:29 = 3,150 − 210 (UPS math).

## 2. Scenarios

- **S0 today** — three gensets by hand, a fourth from 14:00, everything else as configured.
- **S1** — automatic power management with the settings exactly as they are in the controller.
- **S2** — S1 + Gen P Min 20 % (the setting someone tried on 1 Oct).
- **S3** — S1 + the first PLC/communication change set: comm fails shorter than 90 s not declared
  (Modbus timeout/retries), UPS sheet using the genset nominal directly (no stale-PV artefact), 10 s
  delay before a UPS shedding command acts.
- **S4** — S3 + Gen P Min 20 %.
- **S5** — S3 + UPS bridge (after a PV drop of more than 250 kW/min, UPS 1 carries its loads on battery
  for 8 minutes) with the solar coverage ratio lowered from 20 % to 10 %.

Day types: 14 Oct as recorded (with its eight PV interruptions), a clear day without interruptions, a
cloudy day, and a day without any grid.

## 3. Results (per day; gensets run ~12 h because the night is on the grid)

| Scenario | 14 Oct as recorded | Clear day, no interruptions | Cloudy day | No grid at all |
|---|---|---|---|---|
| S0 today | 40 engine-h, **4,130 l**, PV 7.7 MWh (55 % curtailed), gensets at 14 % | 4,020 l, PV 8.1 MWh | 4,120 l, PV 7.7 MWh | 82 h, 7,280 l |
| S1 AUTO as configured | 21 h, **2,820 l (−32 %)**, PV 10.6 MWh (38 %), 11 UPS sheddings | 16 h, 2,210 l (−45 %), PV 12.6 MWh | 2,940 l (−29 %) | 28 h, 4,590 l (−37 %) |
| S2 + Gen P Min 20 % | 17 h, 2,390 l (−42 %), PV 11.9 MWh | 2,110 l (−48 %), PV 12.9 MWh | 2,490 l (−40 %) | 4,490 l (−38 %) |
| S3 + PLC/comm change set | 20 h, 2,720 l (−34 %), PV 11.0 MWh, 7 UPS sheddings | same as S1 | same as S1 | same as S1 |
| S4 = S3 + Gen P Min 20 % | 17 h, 2,290 l (−45 %), PV 12.3 MWh | 2,110 l | 2,490 l | 4,490 l |
| S5 = S3 + UPS bridge, coverage 10 % | 16 h, 2,370 l (−43 %) | 2,200 l | 2,550 l | 4,580 l |

Reading the table:

- The fuel lever is power management (S1): with the PV exactly at "load − 25 % of the running nominal",
  fewer running gensets means a lower floor and more PV. The figure is larger than the earlier estimate
  because the PLC holds no extra margin below that line — the ~200 kW gap seen at the warnings comes from
  the regulator's overshoot, which the model now reproduces as dynamics, not as a constant.
- With power management on, a PV loss drives one or two gensets above the 70/80 % thresholds and the UPS
  shedding does what it was built for; the sheddings disappear with the interruptions (S3 removes the
  comm-fail ones, the OFF/AUTO toggles are an operating habit).
- The UPS bridge (S5) buys little fuel on its own; the coverage ratio change does.
- Gen P Min 20 % is worth another 4-10 %, as on 1 Oct; the reason for the reversal that day is still to be asked.

## 4. Making it right: calibrate, validate, then predict

1. **Exact rules** — done for the PLC (sheet exports). Still to confirm in the editor: the Mux select
   order in sheet 8, the Math code 4, what Forced Value 1 does.
2. **Recorded inputs instead of assumptions** — the collector's log (PV actual, curtailment %, request,
   gensets P, running nominal, mains, breaker bits every few seconds) replaces the curtailment regulator,
   the SAU response and the daytime load shape; a few recorded days at that resolution calibrate the
   remaining constants in one pass.
3. **Validation days** — reproduce PV used, gensets running, warnings and UPS commands for 14 Oct and a
   few more days within ~5 %.
4. **Then the modifications** — each one a scenario; deltas against the validated baseline.

## 5. Adding a modification

- A **setpoint change** is a `Scenario(..., plc_setpoints={65521: 85})` (PLC user setpoints by number,
  see `program.md`) or a changed field for the controller's own settings (`gen_p_min`, reserves…).
- A **program change** is written twice: in the model, as a flag that changes what the PLC is fed or how
  its outputs are applied (`ups_math_fix`, `comm_hold_s`, `ups_shed_delay_s` are the first three), and in
  InteliConfig on a copy of the archive, as the blocks it stands for. The exported sheets of the modified
  archive can then be loaded into `tools/neo_plc.py` directly — the model runs the modified program
  itself, and the flag is retired.
- Keep the REAL/ASSUMED marking, add the scenario to `SCENARIOS`, run the tool; the CSV and the chart
  regenerate.
- The first one is done this way: `docs/changes/change-01_comm_fail_tolerance/` holds the modified sheets
  for InteliConfig (built by `tools/neo_plc_change01.py`), the whole modified program, and the model
  runs it with `Scenario(program_dir=...)`; `tests/test_change01.py` proves it identical with a healthy link. InteliConfig's import refused a first draft (no Analog Switch left in the PLC), so the change is built with the blocks that are free.
