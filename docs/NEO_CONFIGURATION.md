# How the InteliNeo configuration works

From the controller archive of 2026-10-14 (`InteliNeo 5500_INEO 5500_20261014_01.aig4`, firmware
1.2.0.43, application "Standard HC"), the Values/Setpoints exports of the same day and the controller's
own Modbus table. `tools/neo_archive.py` extracts the readable parts of an archive; the extracted text
is in `docs/site/neo_archive_2026-10-14/`.

Confidence marks: **[fact]** read directly from the files; **[inferred]** follows from names, live
values and history together; **[open]** needs the PLC editor or the site.

## 1. What the controller is in this plant

The InteliNeo 5500 is the **hybrid master**: it does not control a genset or a breaker of its own
(BESS "Not Installed"). It sits on the CAN bus with the five genset controllers (IG4 200, addresses
1-5) and the three mains controllers (InteliMains 210 G2, addresses 6-8) as address 9 — one more than
the firmware's limit of 8, hence the permanent alarm **Max Num CU on CAN Exceeded** [fact].

Its job, as configured, is the **PV side** of the plant [fact]:

| Function | How |
|---|---|
| Read the PV plant | Modbus TCP master to the SAU-100 (192.168.0.128:502), two "PV Logger" definitions, 1-2 s refresh |
| Limit the PV plant | writes the active-power setpoint ("AP Set", registers 2100/2101) and reactive settings to the SAU — this is the curtailment |
| Switch the PV in and out | binary outputs **PV CB1 ON/OFF, PV CB2 ON/OFF** with feedbacks **PV1/PV2 feedback** (two PV breakers, one per transformer winding) |
| Control two UPS breakers | binary outputs **UPS1 ON/OFF, UPS2 ON/OFF** with feedbacks **UPS1/UPS2 feedback** |
| Read the mains incomers | Modbus TCP master to MAIN 1-3 (192.168.0.107 / .111 / .110): mains import P/Q/PF, load P/Q/PF, battery, MCB feedback (coil 0) |
| Measure the bus | its own voltage/frequency inputs: "Mains/Bus" 417 V, 50.01 Hz |
| Power management | enabled (kW mode, start reserve 300 kW, stop reserve 400 kW, min run power 210 kW) — but no genset was "in PM" on 14 Oct [fact], see §5 |

## 2. The devices it talks to — and their registers [fact]

These definitions are the plant's own register maps, taken from the archive. They are now
`profiles/pv_array_sau.yaml` and `profiles/im210_mains.yaml`.

**SAU-100 virtual array** (one unit for the whole PV plant; the Neo's "PV Logger group 1" and
"group 2" both point at 192.168.0.128:502):

| Register | Value | Scale |
|---|---|---|
| 1000 | AP active power | ×0.1 kW, int32 |
| 1002 | RP reactive power | ×0.1 kVAr |
| 1004 | power factor | ×0.001 |
| 1009 | daily yield | ×0.1 kWh — reads 635,283 on 14 Oct: **not plausible, verify** |
| 1011 | total yield | ×1 kWh — reads 3,010: **verify** |
| 1017 / 1019 / 1021 | inverters total / online / working | |
| 1063 / 1065 / 1067 | maximum active / reactive / min reactive power | ×0.1 (3,520 kW = 10 × 352) |
| **1093** | **maximum active power available** — what the sun allows right now | ×0.1 kW |
| 1095 / 1097 | reactive available / min reactive available | ×0.1 |
| 1187 / 1189 / 1193 / 1195 | inverters stopped / standby / faulty / alarm | |
| 2100-2101 (write) | AP Set — the PV power limit sent by the Neo | ×0.1 kW |
| 2102-2103, 2105, 2018, 2020, 1007/1008, coils 4002/4003 (write) | reactive / PF / percentage setpoints, mode, inverter ON/OFF | |

Register 1093 is the key number for curtailment analysis: *available − actual = PV thrown away*.

**InteliMains 210 G2** (each of MAIN 1-3): 1001 Mains Import P (int16), 1006 Mains Import Q,
1014 Mains PF ×0.01, 1046 LAOD P (int32), 1048 LAOD Q, 1050 LAOD PF ×0.01, 1051 battery ×0.1 V,
coil 0 MCB feedback.

The connection table also holds a sixth target, 192.168.0.1:502, with no definition attached [fact]
— probably a leftover entry (the old router address), not a device [inferred].

## 3. The PLC program [fact — the eleven sheets exported from InteliConfig on 14 Oct]

The sheet exports (`docs/site/neo_plc_2026-10-14/*.xml`) contain every block, its inputs, outputs,
properties and wiring. `tools/neo_plc.py` lists them block by block (`program.md` in the same folder) and
executes them, so the behaviour model runs the plant's own logic. In plain words, in execution order:

1. **IM210 data acquisition** — site totals from the three InteliMains: *Mains Import P site* = IM1 + IM2 +
   IM3, *Load P site* = LAOD P of IM1 + IM2 + IM3 (same for Q; PF is a combination of the three). The
   history of 1 Oct 08:19 shows the three units at −399 / −370 / −341 kW while exporting — three separate
   feeders, so the sums are right (the earlier "triple counting" worry is withdrawn). *MCB #n* = MCB
   feedback of unit n AND "unit n present on CAN"; *Any MCB close* = OR of the three; *Any GCB close* = OR
   of Gen Loaded 1…5; **GroupLink = NOT Any MCB close** (groups linked whenever no mains breaker is closed,
   i.e. on gensets — the daily "2 + 1 / 2 − 1" in the history); *GCB and MCB close* = both.
   The setpoint "Reactive power scheduling mode" is converted and written to the SAU.
2. **UPS breakers math** — *Running nominal Gensets* = "Running Nominal Power Of All" − *PV Actual P*;
   *Gen load* = Gensets P ÷ Running nominal Gensets × 100 (interpolation 0→0, nominal→100 %); *Gen Load* =
   that value when any GCB is closed, 1 % otherwise. **UPS 1 CB command** is a hysteresis comparator on
   *Gen Load* with ON = "UPS 1 Load Shedd OFF" 35 % and OFF = "UPS 1 Load Shedd ON" 70 % — with ON below
   OFF the comparator works inverted: the command is 1 (UPS breaker closed) below 35 % and drops to 0
   (shed) above 70 %. UPS 2 the same with 80 %.
3. **PV Breaker controll** — *PV CB1 command* = User Button 1 AND no latched fail (fail to close / fail to
   open / other) AND **PVCB Close/Open** (the controller's own PV-breaker decision); PV CB2 the same with
   User Button 2. *UPS 1* = User Button 3 AND no latched fail AND *UPS 1 CB command* delayed by "UPS1 ON
   command delay" 10 s (delay on closing only; opening is immediate); UPS 2 the same with Button 4. Four
   breaker blocks turn the commands into the physical outputs **Binary output 1…8** (PV CB1 ON, PV CB2
   ON, UPS1 ON, PV CB1 OFF, PV CB2 OFF, UPS1 OFF, UPS2 ON, UPS2 OFF) and supervise the feedbacks; the
   twelve fail flags are RS latches cleared by **Fault reset**.
4. **(no name)** — *Any PV CB on* = PV CB1 status OR PV CB2 status OR UPS1 status; **PVCB function status**
   = Any PV CB on AND PVCB Close/Open, wired back into the controller as its PVCB feedback. So the moment
   the controller decides to open the PV breaker, its own feedback already reads "open".
5. **MCB & PV Breakers restriction power** — *PV feedback number* = PV1 feedback × 1 + PV2 feedback × 2;
   *MCB feedback numbers* = MCB1 × 1 + MCB2 × 2 + MCB3 × 4; both split back into bits for the sheets below.
6. **Validation** — *AP validate* = the SAU's active power if valid, else 0; *Max Active Power Available
   valid* = the SAU's "Maximum Active Power Available" (3,520 kW, a constant in the history) if valid, else 0.
7. **Grid || request** (on the mains) — *Site load no gen* = PV Actual P + Mains Import P site; *Max Export
   transformers* = MCB feedback numbers × "Max Export 1 transf" 1,300 kW (so 7 × 1,300 with all three
   MCBs closed — the composed number is 1 + 2 + 4, not a count); *Site Load with GEN* = Site load no gen −
   Gensets P; *PV req math* = that + Max Export transformers; **PV P req grid** = the smaller of PV req
   math and Max Active Power Available valid. In practice on the grid the request is simply the SAU
   maximum (3,520 kW) — PV runs flat out and exports (1 Oct 08:19: 1,445 kW PV, 1,110 kW exported).
8. **PV breakers restriction** — *1 breaker* = PV P req grid capped at "MAX P req 1 PV breaker" 2,200 kW;
   **PV req PV CB restriction** = 0 with no PV breaker closed, *1 breaker* with PV1 only, PV P req grid
   with PV2 only or both (the 2,200 kW cap is applied to one of the two single-breaker cases only — to
   be confirmed in the editor).
9. **PV request Mains/Gen** — **PV Output req gen kW** = Maximum Active Power Available × Curtailment
   Output ÷ 100 (interpolation 0 % → 0, 100 % → max available): the controller's own curtailment
   regulator (Gen P Min 25 %, gain 5, 15 s) produces a percentage and the PLC turns it into kW of the
   SAU maximum. **PV req (kW)** = PV req PV CB restriction on the mains, PV Output req gen kW on gensets,
   "MAN PV P req" when User Button 10 is on.
10. **PV req step protection** — *PV AP + step prot* = AP validate + "PV req step protetion" 100 kW;
    **PV req protected** = PV req (kW), but never more than PV AP + step prot (the request can only be
    100 kW above what the plant is producing — the ~300 kW/min recovery after any drop); **PV comm fail**
    = ECU 2 Comm Fail OR ECU 3 Comm Fail OR a request ≤ 0; *PV req prot comm fail* = 0 while PV comm fail,
    else PV req protected; **PV req out ramp** = ramp of that, 25 kW/s up, 1,500 kW/s down → written to
    the SAU as the active power setpoint. Protections: *PV Prod > PV req comp* = AP validate > PV req +
    75 kW → **PV prod > PV req Warning** at once (the "AHI" alarm adds its own 15 s delay; indication
    only); the same condition held 60 s, or *reverse power* (Load P site ≤ −10 kW on gensets) held 15 s,
    sets the latch *PV prod > PV req BRKR open* (cleared by Fault reset) — the latch is not wired to any
    controller function, so the PV breakers are not opened by it.
11. **PV Q request** — *Site Q* = PV Q + Mains Import Q site + Gensets Q; *Q min math* = Site Q − "PV Q min
    import" 1,000 kVAr; *Q function start* = Q min math > 0 AND PV Actual P > 100 kW AND Gensets Q ≥ 0;
    *Q min req* = Q min math while started, else 0 — and then **PV Q req = 0 unconditionally** (the final
    switch is driven by the constant "Logical 1"), ramped and written to the SAU. The reactive power
    control is switched off by design.

What this program explains in the history:

- **A PV-logger comm fail of a single second sets the SAU setpoint to 0** (sheet 10). The inverters
  follow within seconds, the gensets take the load, and when the session is back the setpoint ramps from
  0 at 25 kW/s but is held to "actual + 100 kW", so the plant climbs back at ~300 kW/min. Every "PV
  Logger group 1/2 Comm Fail" line is a production dip; 5-33 of them a day.
- **The UPS shedding alarms are a side effect of PV drops, not of genset overload.** "Running Nominal
  Power Of All" includes the PV while the controller's PVCB feedback is closed; sheet 4 drops that
  feedback the instant PVCB Close/Open goes off, while the SAU value the PLC subtracts is one Modbus
  refresh old. For a few seconds *Running nominal Gensets* = 3,150 − 1,041 = 2,109 kW while the gensets
  already carry 1,829 kW → *Gen Load* 87 % → both UPS commands drop. The history of 14 Oct 12:25 shows
  exactly this ("Gen Nom" 2,940 = 3,150 − 210 eleven seconds later, Gen Load 57 %, both "UPS Shedding").
  The hysteresis then keeps the UPS shed until the gensets fall below 35 %.
- The night-time "PV req 100 kW" in the history is the step protection with the PV at 0.
- On 1 Oct 08:19 the recorded request 1,545.3 kW = 1,445 (actual) + 100: the step chain reproduced to the kW.
- The reactive-power sheet, the single-breaker cap and the export limit are inactive in today's operation.

## 4. What the history log shows [fact], and what it means [inferred]

Two sources: the 750 records inside the archive (text reasons only) and the full InteliConfig history
export of the same period (`docs/site/neo_history_2026-09-29_2026-10-14.csv`, with the value snapshot of
every event; `tools/neo_history.py` makes these files from any History.xls).

**The daily cycle is a grid cycle, not a sun cycle.** Every evening at about 18:05 the plant transfers to
the **government grid**: gensets stop, `Groups Unlinked` ("2 - 1"), `Forced Value(1)` pulses, and `Other
CB Trip` records the PV breakers opening on the transfer (MCB 1 and 2 closed, 574 kW import). Every
morning (06:12 in mid-October, 08:00-10:30 earlier in the month — the grid's own schedule) the plant
transfers back to gensets with an **open transition**: the bus is dead for a few seconds (`Mains/Bus <f`,
all voltages 0), `BO PV Anti Islanding`, `PVCB Opened`, `Groups Linked` ("2 + 1"), then three gensets
(running nominal 3,150 kW) and `PVCB Closed` a few minutes later. Night load on the grid: 570-680 kW.
Some days have extra grid windows in the afternoon. So the gensets run about 12 hours a day — exactly the
solar hours — which is why curtailment hurts so much here.

**Daytime operation**: 3 gensets by hand most of the day (3,150 kW), a 4th at times (4,150 / 3,851 kW),
genset load 21-33 %. The curtailment rule reproduces the records exactly: at 12:25 on 14 Oct the load was
1,829 kW, the running nominal 3,151 kW, and the PV 1,041 kW = load − 25 % × running nominal. The PLC's
*request* usually sits ~240 kW below that (median of 91 warnings), so the inverters often produce more
than requested → `AHI PV prod > PV req Warning` (93 in 16 days; a warning only, the breakers stayed closed).

**Mode switching by hand**: `SetpointChange CON(8315)=0 → =2` = controller OFF then AUTO from
InteliConfig, up to **ten times a day** (14 Oct: 08:04, 09:19, 09:23, 09:37, 12:25). Each OFF opens the
PV breakers at once (0.3-1.0 MW dumped on the gensets), each AUTO re-closes them with a **voltage dip to
175-216 V** (−10 to −24 %), and on 12:25 the genset load jumped to 57 % and both UPS shed. The toggles
follow `PV Logger Comm Fail` bursts — the operators use OFF/AUTO as a "reset" for the PV communication.

**PV logger communication failures**: 2-33 per day (26 on 5 Oct, 33 on 9 Oct, 23 on 10 Oct, 20 on 14 Oct).
Each one makes the validation sheet force the PV request to 0; the PV recovers at ~300 kW/min. That is the
RS485/SAU problem seen from the controller's side.

**UPS shedding** on 8 of 15 days — always right after a PV loss (trip, toggle, comm fail), when the gensets
suddenly carry the whole load.

**Setpoint trials on 1 Oct** by an Ethernet client (`U=0,T=ETH CA1`): **Gen P Min** 25 → 20 → 15 → 25 %
within six minutes, and **Min Run Power** 210 → 50 → 210 kW. Somebody already tried lowering the genset
floor to let more PV in and went back. Before step 2 of the modification plan, ask what they saw.

**Fault resets** from Ethernet on 4 Oct 07:01 and 10 Oct 09:36. Two `Wrn Production Mode` records (1 Oct
08:07, a controller start with everything at zero) — a firmware message at power-up, not a plant alarm.

## 5. Settings that decide the plant's behaviour [fact]

| Group | Setting | Value | Effect |
|---|---|---|---|
| Curtailment | Curtailment | Enabled | the Neo limits PV to keep the gensets loaded |
| Curtailment | **Gen P Min** | **25 %** | gensets must keep ≥ 25 % of the *running* nominal power; PV takes the rest |
| Curtailment | Gain / Int | 5.00 / 15 s | controller speed |
| Power Management | Mode / reserves | kW, start 300 / stop 400 kW | would run two gensets for 1.5 MW |
| Power Management | Min Run Power | 210 kW | |
| Power Management | Dynamic Spinning Reserve, Solar Coverage Ratio | Enabled, 20 % | extra reserve for PV variability |
| Power Management | Priority / Auto Swap | 32 / Disabled | |
| PV setpoints | Max Export 1 transf | 1,300 kW | per-transformer limit towards the mains |
| PV setpoints | MAX P req 1 PV breaker | 2,200 kW | limit with one PV breaker |
| PV setpoints | UPS 1 / UPS 2 Load Shedd ON / OFF | 70/35 %, 80/35 % | UPS logic thresholds |
| PV setpoints | PV req step protection | 100 kW | ramp step |
| PV setpoints | PV Prod > PV req / del | 75 kW / 60 s | "inverters not following" watchdog |
| User buttons | 1-4 = MAN ON, 10 = MAN OFF | latched | their meaning is in the PLC [open] |
| Basic | Fuel genset consumption | 237.3 g/kWh | used for the fuel statistics |
| Ethernet | AirGate | Enabled, waiting to connect | no internet on the ComAp network |

On 14 Oct **no genset was in power management** (Running Nominal Power In PM = 0 while 3,851 kW were
running) [fact]. Together with four gensets at 38 % load and the manual OFF/AUTO switching, the gensets
look operated by hand [inferred] — to be confirmed on the IG4 panels (MAN/AUTO).

## 6. Protections defined by the plant [fact]

PV CB1/CB2 fail to close / fail to open / other fail; PV prod > PV req Warning; PV prod > PV req BRKR
open; Reverse power Warning; UPS1/UPS2 fail to close / open / other fail; UPS 1 Shedding; UPS 2
Shedding; PV Logger group 1/2 Comm Fail; Modbus TCP IM210 G2 Comm Fail.

"Wrn Production Mode" on the alarm list is a **firmware** message (it sits among the controller's
own UI texts), not a plant-defined protection — its meaning is a question for ComAp [open].

## 7. What is not readable, and how to get it

- The PLC program is now known from the sheet exports (§3). Still unknown: what Forced Value 1 does at
  the transfers, the alarm list that turns PLC bits into "AHI …" indications, and the screens.
- The per-inverter registers of the SAU (units 1-10) are not in the archive — the Neo only reads the
  array. Still the SAU point table or a register scan.
- The IM210 unit id is not in the definition (ComAp default 1 assumed).

## 8. What this means for the work

- The collector now reads the array (available vs actual PV = curtailment) and the three mains units
  with the plant's own register maps — no guessing, and the same registers the Neo polls every second.
- Step 2 of the plan (gensets back under power management) has a precedent: the 1 Oct trial. Find out
  why it was reverted before repeating it.
- Step 3 (UPS logic) has a concrete base: the UPS breakers are already automated as load shedding at
  70/80 % genset load (sheets 2 and 3). The first change there is a correction, not an extension: take
  *Running nominal Gensets* from the gensets' nominal power instead of "Running Nominal Of All − PV",
  and delay the shedding command a few seconds, so a PV drop no longer sheds the UPS. A "bridge on PV
  drop" would then be an extension of the same sheet, built in the PLC editor on a copy of this archive
  and tried in the model first.
- The comm-fail problem is a program behaviour as much as a network one: one lost session → setpoint 0.
  Besides the Modbus timeout/retry settings, sheet 10 could hold the last request for a short time
  instead of forcing 0 (a Delay on the comm-fail bits plus a hold on the validated values).
