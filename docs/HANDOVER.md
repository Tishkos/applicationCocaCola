# Handover notes - read this first

For whoever continues this work, a person or a tool. Everything learned, decided and still open, in one
place, as of 15 October 2026. Facts carry their source; anything marked *model* or *inferred* is not a fact.

## 1. The job

Erbil Coca-Cola hybrid plant (CCI, Iraq). A ComAp InteliNeo 5500 master runs five gensets (IG4 200) and
three mains incomers (InteliMains 210 G2) and curtails a 3.2 MW Sineng PV plant through a Sineng SAU-100
gateway. Two UPS of 350 kVA exist on paper. The plant burns diesel while curtailing solar; the contract is to
measure, understand, and then propose and document the configuration changes that cut fuel, with the plant
writing every change itself.

This repository is the read-only data collector for the plant PC, the Modbus server for the DT Turkey / CCI
monitoring platform, the interpreter and model of the master's PLC program, and the folder of proposed
changes. The sister repository `qs-ai-hybird` is not to be touched.

## 2. Rules that stay in force

- Read-only on every plant device. No code path writes; `tests` check that. Nothing is restarted, no setpoint
  touched, nothing changed on live equipment without the plant's written approval. In InteliConfig never
  **Write**, **Program** or **OK and Restart**; the plant writes any change itself on a copy of the archive,
  with the archive of 14 October as rollback.
- No credentials in files or chat. Controller, site and SAU passwords stay on site. No bypassing of
  licensing, passwords or the encrypted parts of archives. No experiments against the real controller.
- Repository hygiene: no mention of assistants, tools' temp paths or the sister project's name in files or
  commit messages; commits as `Tishko <sabirtiishko@gmail.com>` with no attribution lines.
- Documents and emails: plain language, short, black headings, no names of the previous integrator or his
  company. Statuses "Ready" / "Pending commissioning". Internal pending items are not disclosed to CCI.
  Nothing is claimed that is not verified; every number says where it comes from.
- WhatsApp drafts in the owner's style: lowercase except the first letter, commas instead of full stops,
  no numbered lists, no dashes or quotes, short.

## 3. The plant as known (sources: archive of 14 Oct, Modbus export, history 29 Sep - 14 Oct, site session)

- Network 192.168.0.0/24 (ComAp side). Plant PC `192.168.0.200` (Siemens IPC, Windows 10 IoT, internet
  over the CCI Wi-Fi 172.17.76.x). Master `.112` (Modbus unit 1, CAN address 9). Gensets `.105 .106 .113
  .114 .115`. Mains `.107 .111 .110`. SAU-100 `.128` (ETH0; ETH1 192.168.1.123), ten SP-320K-H inverters
  on RS485 behind it (unit ids 1-10 assumed), the array as unit 245. A sixth target 192.168.0.1 in the
  master's connection table has no definition (*inferred*: leftover).
- Running nominal 3,150 kW with three sets, 4,150 kW with four (history). Gen P Min 25 % (setpoint).
  Load about 2 MW on 14 Oct, 570-680 kW at night on the grid. Gensets run by hand since 1 October
  (history: no set in power management) - reason unknown.
- Export cap 1,300 kW per closed mains feeder (PLC setpoint). On 1 Oct the plant exported on three feeders.
- The master's PLC program: 11 sheets, 88 blocks, exported as `docs/site/neo_plc_2026-10-14/`. It is
  executed exactly by `tools/neo_plc.py` and reproduces history values (1,545.3 kW request on 1 Oct,
  "Gen Nom" 2,940 on 14 Oct). Summary: sheet 1 site sums and MCB/GCB logic; 2 UPS math (Running nominal
  Gensets = Running Nominal Of All - PV Actual P, Gen load %, shedding comparators 35/70/80 %); 3 PV and
  UPS breaker commands with fail latches; 4 PVCB status; 5-6 composers and validators; 7 grid request
  (site load + feeders x 1,300 kW, capped at the SAU's max available); 8 single-breaker cap 2,200 kW;
  9 Max Available x curtailment %, grid/gen/manual selection; 10 step protection (actual + 100 kW), PV comm
  fail = ECU2 OR ECU3 OR negative request -> request 0 -> ramp 25 kW/s up, 1,500 down -> ECU write;
  overproduction warning; 11 reactive request forced to 0.
- The master talks to the SAU over Modbus TCP in two sessions ("PV logger group 1/2", ECU 2 and ECU 3,
  timeout 3,000 ms). Group 1 writes AP Set as a 32-bit value at 2100-2101 every 1 s; group 2 writes AP Set
  at 2101-2102 every 10 s (and RP Set 2102 vs 2103, PF 2105, mode 1007 vs 1008, coil 4002 vs 4003). The
  two write lists are one register apart; with 32-bit values they overlap. Either the SAU has a second
  register set at +1 or one definition is off by one. **Open, measured by the trace, not to be discussed
  outside until measured.** The SAU allows five Modbus TCP links in all.
- Comm fails: 133 "PV Logger group 1/2 Comm Fail" warnings in the history 29 Sep - 14 Oct, up to 33 a day
  (9 Oct); on 14 Oct 20 between 08:52 and 09:58 in 5 bursts, mostly both sessions within seconds. Tabulated:
  29 episodes (gap > 2 min); inside a burst a session's fails repeat every 33 s (median, min 7 s), which is a
  cycle, not unrelated stalls; at the first fail of 23 of 29 episodes the request is still > 0 (the bit rises
  before the request collapses); `pv_max_kw` (group 2's Max Available) is already invalid at 99 of 133 fail
  instants; 4 of 133 fell on the grid; one-session fails exist (5 Oct, long group-2-only runs). The previous
  integrator's "the SAU fails to respond for 6-9 s" is timeout-times-retries arithmetic, not a measurement;
  the one measured stall was 2.5 s and the flag rose at about 2.6 s.
- Climb-back after the PV goes to zero: about 300 kW/min, measured on 14 Oct after two PV breaker closes
  (0 -> 685 kW in about 2.5 min). After a comm-fail zero it may be faster; the per-fail loss is between a dip
  and about five minutes and is **not measured** yet.
- The UPS shedding alarms in the history are an artefact: on PV drops the master's PV term goes stale and
  "Running nominal Gensets" collapses for seconds, so Gen load % jumps above the shed thresholds. Whether the
  UPS and their breakers are wired at all is **unverified**; the history shows UPS breaker fail warnings.
- Reactive control is dead by design (sheet 11). Grid export is allowed.
- The OFF/AUTO toggles on 14 Oct were energy-meter calibration by the plant, not alarm clearing.
- The master's clock is one hour behind local time (plant engineer, 10 Oct). History hours in this file are
  controller time; add one hour for local. Record the offset on every session (HMI clock vs PC clock).

### What the vendor group chat adds (export of the "Remote monitoring" group, Sept 2025 - 15 Oct 2026)

Read on 17 Oct. Roles only, no numbers or credentials from the chat go into this repository.

- The drops are old. First explicit report 12 Jun 2026 ("sudden production drops, ComAp error on screen");
  the solar engineer says the problem began about April-May 2026 and got worse with heat; "20 times a day"
  in July; a Saturday in late September (26 Sep) had an hour or two of timeouts from 09:00. The plant (CCI)
  says the gensets have already needed major maintenance because of the steps and that **even one drop a
  day is not acceptable**; they also say the **plant load is stable, there is no rush current** (so the
  model's load-event parameter is contested by the plant; the trace's one-minute load falls decide).
- The SAU is the box that stalls, with direct evidence, all from the chat:
  - 31 Aug: ping to .128 times out in bursts; a MikroTik router (RB4011) between the SAU and the controller
    logs the SAU IP flapping up/down with **no Ethernet link-down event**; with the SAU powered off the network
    is clean. If a stall is under about 5 min it recovers alone, longer ones need a SAU restart.
  - 5-6 Sep: downloading the SAU's "soft runtime log" from its web page makes the SAU stop answering
    Modbus TCP for seconds; the ComAp shows "PV logger group 1,2 comm fail" and the solar fell 809 -> 400 kW.
    Reproduced with Modbus Poll (timeout, Err=8, about 3 s) on a click of download. **Never download or
    export logs from the SAU while the sun is up.**
  - 6 Sep 09:37-09:51: SAU unreachable 13 min; the SAU's own `SauGuard` watchdog log shows the main
    application restarted at 09:36:55 and 09:49:56 (SIGTERM, crash and restart); `ModbusTcpServer.cpp` log
    shows the TCP server dropping sockets (read returned <= 0) and clients reconnecting (connids 101-110).
    The MikroTik also logged SAU down on 7 Sep at 19:33 and 19:34, after sunset: the stalls do not need sun.
  - 8-9 Sep: Sineng captured packets at the SAU itself (mirror to ETH1). The integrator found in the same
    file: master -> SAU request, ACKed at once, **answer 2.5 s later with Modbus exception 06 "Server Device
    Busy"**; a second request answered 2.5 s later; a request on the other port (ETH1) answered 2.7 s later;
    both ports recover together. Sineng's reading of the same file: "no interruption, not the SAU".
  - 10 Sep: SAU CPU load page: 1-min load 121 % (5-min 115 %, 15-min 89 %), half an hour later 36 %.
    10 Oct: SAU status page shows an internal message queue of 22,487 (localhost) at the moment it stops
    answering. 10 Oct: a Wireshark capture shows TCP retransmissions to .128 at the fail.
  - RS485 side: communication success rate per inverter 80-85 % in June, 35 % with gensets and 65 % on the
    grid on 20 Jun; Sineng says it must be above 95 %. Two devices with **no protocol model assigned**
    (ID 200 "PDC#2 UPS" on RS485-7 - a UPS in the PV station's own cabinet, presumably not one of the plant's
    350 kVA units - and ID 201 "PDC#1 dehumidifier" on RS485-3) are polled forever and time out every cycle;
    if the dehumidifier really is dead the cabinet climate is uncontrolled in a hot station; a weather station that is not installed is configured too; PID box (101/102) and RMU (202)
    also sit on the SAU. The inverters' RS485 is wired five in parallel per channel, shield grounded at one
    end, termination inside the devices (Sineng), communication board SP225KHI4 A02. Sineng's own line is
    "the RS485 quality is the problem, the SAU is fine".
  - Both SAU Ethernet ports were cabled to the same switch (ETH0 .128, ETH1 192.168.1.123). Two network
    cards of one Linux box on one switch can by themselves cause intermittent unreachability (ARP answered
    on the wrong port). **Hypothesis, cheap to check by looking, not by unplugging.**
  - New media converters were fitted on 24 Sep and caused an IP conflict until 26 Sep 14:00; the solar
    engineer blames the 26 Sep burst on it, the plant says the burst started at 09:00 before any work. The
    history after 29 Sep still has 133 fails, so the conflict is not the whole story.
  - Sineng changed the SAU's IP to 192.168.20.128 during a remote test on 6 Sep (cutting the master off) to
    show "no power change without the EMS"; it was put back. Sineng has remote access to a site PC
    (Sunlogin/AweSun and AnyDesk) and the SAU's debug tool; the site was told not to use high-privilege SAU
    accounts. Sineng: "SAU-100 cannot be changed"; the nameplate says SAU-300, protocol SAU300U211.
  - Sineng statements to verify, not to repeat: "communication loss cannot change power, the inverters
    transmit at full power when there is no communication" (what the SAU does when the master's writes stop
    is exactly the open question behind the v4 stage 2); the SAU has two "valid delay" settings of 3,000 ms
    for inverter and box-transformer power data; the master's PV logger modules have Pause Before Request
    0 ms and Response Timeout 3,000 ms (screenshot in the chat, 10 Oct). On 11 Oct Sineng said they no
    longer need a capture "for reproducing the issue" and did not say why.
- A second way the solar drops, without any comm fail: inverter grid-protection trips. In June-July the
  800 V bus rose to 860 V at high production (phase imbalance 820/840 V) and inverters tripped on high and
  low voltage; on 25 Jul Sineng widened the limits remotely to 800 V +/-15 % and 50 Hz +/-1.5 Hz (48.5-51.5)
  and refused 47-52 Hz. On gensets a large load step moves frequency; a dip below 48.5 Hz would trip the
  inverters and dump the load on the sets. Some inverter alarms are not shown on the web page, only in the
  debug tool. Not measured by us yet; the trace shows PV drops without a fail bit if it happens.
- Older open items from the chat: the PID box has been off since autumn 2025 because with it on all inverters
  trip "PV fuse" at sunrise (earth terminals were opened in Jan 2026, floating-voltage checks, insulation
  >= 17 MOhm); one inverter lost a month of production statistics (Jul); the transformer breather/oil cup
  maintenance; night no-load loss about 90 kWh toward the transformers; the SAU's clock was off.
- Documents that exist in the chat and should be collected: a single-line diagram (`power sld salah.pdf`),
  the MV station SLD (`ITS-6500-MV-20 ... SLD`), `Si-Box Diesel microgrid configuration Guide.pdf` (Sineng's
  own diesel-hybrid guide), the DTSU666 meter guide, SAU300 and SAU200 manuals, SP-350K-H1 installation
  manual and datasheet, `Problem SAU 300.pdf`, the SAU log files `SauGuard.txt`, `SmartCMSHU121.txt`,
  `Device data service.txt`, the capture `20260908SAU-TCP.pcapng`, inverter alarm histories (CSV/XLS/zip),
  the daily report PDFs from 28 Sep.
- People in the chat by role: the solar site engineer (operates the SAU, writes the reports), the sponsor,
  two plant (CCI) engineers, the previous integrator, Sineng after-sales, SAU support, sales and management.

### What the files from the chat show (read 17 Oct; analysis tool `tools/pcap_modbus.py`)

- **The 8 Sep capture (105 s, 09:08:08-09:09:52 plant time; a capture on the Sineng engineer's laptop on
  ETH1's subnet with the master's traffic mirrored into it) shows one stall and, more important, how the master
  reacts to it.** The master's group 1 (one TCP session) does every 1 s: read 1000 x6, write FC6 1007 = 0,
  write FC16 2100 x2 (AP Set, 0.1 kW), write FC16 2102 x2 (RP Set = 0), write coil 4002 ON. Group 2 (second
  session) every 2 s: seven reads (1000 x99, 1017, 1019, 1021, 1187, 1189, 1195) and **no writes at all** in
  105 s: the 2100/2101 overlap is not a live issue (the ECU 3 write items are most likely not wired).
  **Every FC6 write to 1007 is refused with exception 06 (104 of 104, in 1 ms)**; it raises no fail by itself.
  The stall: at 09:08:30.05 the SAU's application went silent for 2.5 s (its kernel ACKed within 34 ms; the
  HTTP server and the engineer's own Modbus Poll on ETH1 waited too) while it **prepared a 5 MB log export**
  (GET .../downloadRunTimeUrl at 29.25) that then streamed 32-33 s. Outside that event the SAU answered 1,278
  transactions, median 1.2 ms, p99 13 ms. Verified frame by frame (`tools/pcap_modbus.py`, scratch dump):
  1. **The master did not wait 3,000 ms.** At 31.970 / 31.973, 1.92 s after the unanswered requests, both
     sessions sent their next item at the same instant with the old request still open: one scheduler event in
     the master, not two timers. Whatever "Response timeout 3,000 ms" governs, it did not govern this.
  2. **The master pairs replies by arrival order, not by transaction id.** The late reply to tid 634 arrived at
     32.564; 6 ms later the master sent tid 636 although tid 635's reply only came at 32.575. From then on each
     reply was consumed as the answer to the following request (an exception frame as the answer to the AP read,
     a read frame as the answer to a write, the 99-register block as the answer to a 2-register read) until an
     unsolicited reply arrived while the master was idle (32.732). This is why history rows show PV = 0.0 at the
     instant of a fail with a request of 800-1,400 kW: the AP read was "answered" by a stale exception frame.
  3. **The zero was declared when the stall ended, not when a timeout ran out.** Setpoint 2,583.6 at 32.570,
     1,233.6 at 33.571, 0.0 at 34.573 (the 1,500 kW/s down-ramp): the ramp's input went to 0 at about 32.67,
     100 ms after the mispaired frames, 2.6 s after the abandoned request. The flag cleared about 2 s later. For
     a stall of this kind the zero always reaches the SAU, because the link is back when the flag rises; the PV
     fell 2,484 -> 153 kW in 14 s and climbed back at about 12 kW/s (ramp 25 kW/s capped at actual + 100 kW).
  Consequences: "raise the timeout 3 -> 5 s" rests on an untested assumption about what the setting does and
  would not have helped here; **the hold (v3) with any N above about 3 s rides through this event completely**;
  the one-session idea (no lockstep cross-trigger, fewer transactions, one link less on the SAU) is the only
  root-cause setting with a mechanism behind it; deleting the refused 1007 write shortens the cycle and removes
  the frame that gets mispaired first. The exact rule (what the master abandons at ~1.9 s, how an exception
  reply counts, how many bad transactions raise the bit) must be read from the ECU module help in InteliConfig
  on the archive copy, offline, before anything is proposed. Also in the capture: the SAU's ETH1 ARPs for
  192.168.1.50 every 1.3 s (a configured, absent host); the laptop on ETH1's subnet received 192.168.0.x
  broadcasts, so the two subnets shared one switch domain that day; 192.168.0.129 is an Intel NIC sending
  Windows-style LLDP and ARPing the genset controllers: an unknown PC, not the router (inventory gap).
- **Cross-check with tshark 4.2.2 (Wireshark's decoder, 17 Oct): every fact above holds.** Unit 245 on all master
  frames; group 1 (port 61415) does the writes, group 2 (61416) reads only; FC6 1007 refused with exception 06 in
  104 of 104; GET /deviceLog/downloadRunTimeUrl at 09:08:29.25, then requests tid 634/13846 at 30.050 unanswered
  until 32.564 (2.51 s), the engineer's own poll on ETH1 (tid 1910, 29.835) also answered only at 32.564, and the
  master's next items at 31.971/31.973, 1.92 s after the abandoned requests.
- **`SauGuard.txt` shows no watchdog restart of the application.** It holds 10 cold starts of the guard
  (box reboots: 18 Jun, 22 Jul, 30 Jul x2, 13 Aug x2, 19 Aug x2, 31 Aug, 1 Sep) and three SIGTERM stop/start
  pairs (22 Jun 14:02; **6 Sep 09:36:28 and 09:49:33**). The two on 6 Sep match, to the minute, the Sineng
  engineer changing the SAU's IP to 192.168.20.128 and back during his remote test (chat 08:41-09:00 Budapest
  = 09:41-10:00 plant), and the router's "down 09:37:48 - up 09:51:05". The "absolute proof of crashes" sent to
  Sineng on 7 Sep rests on this file; **our side must not repeat it.** The stalls are real anyway (capture).
- `Device data service.txt` (6 Sep): inverters are SAU devices 3-12 (slave 1-10), firmware SP320KHU212 /
  111A / 111B; the SAU re-sends an inverter's slave address by serial number (FC71) when its comm is abnormal.
  Devices 200 and 201 go "disconnected" 22-30 s after every start. On 6 Sep the five inverters of one RS485
  channel dropped together 14:08-14:37 and the other five 14:55-15:21 (26-28 min each, all five within 40 s):
  a channel- or breaker-level event, not single wires. Which group sits behind PV CB1 / CB2 matters here.
- `data_change_history_record` (SAU, 24 May - 20 Jun): 9,764 changes; inverter comm-status drops of all ten
  at once are night events (AC lost, DSP restarts on return); daytime single-inverter RS485 drops were rare
  (about ten in four weeks, 5-30 s). The volume is grid-protection flags: "AC voltage abnormal" 2,047 daytime
  rises in 28 days, "AC volt high", "AC frequency low/high"; 29 May alone 577. Winding 1 inverters flag
  twice as often as winding 2.
- Inverter history exports (6 inverters, 18 Jun - 22 Jul): per inverter about 1,000 records in 3-4 weeks,
  top codes 261 frequency low, 260 voltage abnormal, 258 overvoltage, 262 frequency high, 257 undervoltage,
  264/265 LVRT/HVRT, **517 DSP restart 38-64 per inverter**, 507 hardware DC-bus overvoltage 20-26 per inverter;
  heavy days 1 Jul (408), 9 Jul (477), 13 Jul (373). This was on the grid (no gensets that month). So in
  June-July the inverters' own grid-protection trips were a large, separate cause of solar drops; the limits
  were widened on 25 Jul. Not seen by the master as a comm fail.
- `Si-Box Diesel microgrid configuration Guide` (Sineng, A00): the SAU / Si-Logger has its own **"Diesel
  microgrid" active-power control mode** (next to Disallow, Derating prohibited, Remote scheduling, Local, DI,
  Anti-reflux): with a grid meter and a diesel meter on its RS485 (DTSU666 among the supported) it keeps the
  gensets above a "minimum load power of diesel" setting by itself. Its anti-reflux logic derates the PV on
  **meter communication abnormal after a 30 s confirmation, to a configurable percentage of rated, not to
  zero** (parameters 67194-67198), with decline/climb factors. Needs Si-Box software "Diesel Micro Network
  0.5.42+" and inverter software that "supports diesel engine safety regulations" (after-sales upgrade). The
  plant is today in Remote scheduling (the master writes). This is a second, local layer worth studying for
  the one-genset stage; it is not a quick fix and needs meters the SAU can read.
- SAU diagnostics page (Device Monitor > Communication diagnostic > Network Connections): the two master
  sessions at :502 showed send queues of 21 and 207 bytes = exactly one group-1 reply (21 B) and one group-2
  99-register reply (207 B). **That is normal**: the master acknowledges on a 500 ms clock (ACKs at x.227 /
  x.727 in the capture, median 96 ms after the data), so one reply sits in the queue about a quarter of the
  time. Only a *growing* queue means loss; the 22,487 of 10 Oct was on a 127.0.0.1 socket (one SAU process not
  reading another's output: an internal stall, not the network). Loss in the SAU -> master direction stays a
  hypothesis until the "Network interface" counters (errors, dropped, collisions, duplex) and the router/switch
  port counters say so. Two failure modes are in the evidence and must not be mixed: the application silent
  with the kernel alive (8 Sep, 5-6 Sep) and the box not answering pings (31 Aug, 7 Sep 19:33, 10 Oct), which
  is kernel or path. Listening ports seen: 21, 22, 111, 502, 1888, 2404 (IEC 104), 8080.

### What the exports of 17 Oct show (master, Gen 1/3/4/5, Main 1-3; Gen 2 .106 is password-protected, skipped)

- **No PV logger comm fail on 15, 16 or 17 Oct** (master history to 17 Oct 12:07), after 19 on the 13th and 20 on
  the 14th. The master's ECU definitions are byte-identical to 14 Oct and no setpoint changed: nothing on the
  master explains it. Whatever changed is on the SAU, the network, or in how people used the SAU page. Ask
  nobody yet; the trace and the next exports will show whether it holds.
- **Genset protections (Gen 1/3/4/5, identical): Reverse Power 10 % for 5.0 s, Overload Wrn 105 % / BOC 120 %
  after 5 s, isochronous load sharing, load ramp 10 s, all four in MAN** (Power Management off). The lab's
  assumed 5 %/5 s is replaced by 10 %/5 s: with sets held at Gen P Min 25 %, a load step down (or a solar
  return) of more than about 35 % of the running nominal sustained 5 s trips them.
- **Nominals: Gen 1 (.105) 1,000 kW, Gen 3 (.113) 700 kW, Gen 4 (.114) 1,000 kW, Gen 5 (.115) 1,150 kW**;
  Gen 2 unknown (probably 1,000). Priorities 5/3/2/1. Governor frequency gain differs (8/10/8/5 %) and Gen 1's
  excitation-loss setting differs (40 % 7 s against 30 % 5 s): the four sets are not tuned alike.
- **The gensets carry all the plant's reactive power**: about 1,000-1,130 kVAr at 400-470 kW, PF 0.34-0.39
  lagging (master and genset values, 17 Oct). The solar gives none (PV Q forced to 0 in the PLC, PV PF request
  1.000). The inverters could supply it; that would unload the sets' excitation and is a candidate improvement.
- **15 Oct 14:26-14:28, Gen 1 could not be paralleled**: manual start, sync, GCB closed, loaded to ~460 kW, and
  within 8 s it absorbed 625 kVAr (PF 0.60 leading) -> BOC Excitation Loss; reset, the same again (-571 kVAr);
  then its engine ran down to an underspeed shutdown. The running sets were carrying ~1,100 kVAr and pushed
  reactive into the incoming set instead of sharing it: a VAr-sharing / AVR problem on Gen 1 or in the group.
  The bus stayed up (no blackout in the master history).
- **Every evening at the transfer back to the grid (~18:00) breakers trip in a cascade**: "Other CB Trip" on all
  three mains and "OtherGCBTrip" on the sets, and on 14 Oct Gen 3 BOC under-frequency/under-voltage and Gen 5
  Sd Generator L2 Overvoltage at 18:07. The reverse-synchronisation back to the grid is rough.
- **"Max Num CU on CAN Exceeded"** first appeared on 1 Oct 08:06, the same morning as a series of changes over
  Ethernet: controller mode OFF, Gen P Min 20 -> 15 -> 25 %, Min Run Power 50 -> 210 kW. It has stayed active.
  1 Oct is also when the gensets went to manual. The controller added or replaced that day is the suspect
  (Gen 2, the only one with a password, is a candidate).
- The master commands UPS1 OFF and UPS2 OFF (binary outputs 6 and 8) with both UPS feedbacks at 1; why is in
  the PLC sheets 2-3 (the archive copy of 17 Oct).
- SAU (two short page visits, 17 Oct 15:05-15:12 SAU time, about 3-4 min behind local): CPU 71/61/66 %, 49 MB
  RAM free; Modbus links in use 3 of 5 (master x2, internal 127.0.0.1 x1); the web page open permanently on
  192.168.0.50 (two connections) and 192.168.1.50 (on eth1), besides ours; two default routes with eth1 ->
  192.168.1.1 first; "4G dialing status: abnormal"; serial counters since the last start: COM1 and COM2 (the two
  inverter chains) 4.40 M requests each with 588,902 / 588,910 failed (13.4 %, identical: systematic, not wiring;
  broadcasts counted as failures is the leading explanation), COM3 43.8 % and COM7 12.6 % failed (the two
  devices without a model), COM4-6 practically perfect. All ten inverters online, "comm status" orange on all.

- **UPS, from the master's history (bit order verified against the 17 Oct screen: BOUT string left to right =
  outputs 1..8)**: the program sheds the UPS input breakers when Gen Load exceeds 70 % (UPS1) / 80 % (UPS2) and
  closes them below 35 % after 10 s (sheets 2-3, PV setpoints). **Since 3 Oct 13:13 the master commands UPS1 OFF
  and UPS2 OFF in every row, while both UPS feedback inputs read 1 in every row**; at 13:13:38 that day it logged
  "UPS1 fail to open" and "UPS2 fail to open". On 1 Oct 08:01-08:19 a burst of UPS1/UPS2 fail to close / fail
  to open / other fail (the same morning as the CAN alarm, the setpoint changes and the switch to manual). So
  the UPS relief does not work today: the breakers do not follow the command or the feedback does not report
  them. "UPS Shedding" alarms (about one a day) come at the evening transfer and at solar drops. A one-genset
  plan needs working UPS breakers and feedback, corrected UPS math and the hold first.

## 4. What the model says (tools/plant_model.py; *model*, calibrated only on 14 Oct)

Fuel from a Willans line (ASSUMED 3,500-4,500 l/day absolute), curtailment regulator ASSUMED (0.03 %/s per
% error, fitted to 8 overproduction warnings), PV response 10 s. Relative results for 14 Oct: today about
4,130 l; two gensets in the sun -26 to -28 %; power management AUTO -32 to -45 %; one genset alone is only
safe without total-PV-loss events; a 2 x 350 kVA UPS buffer covers clouds, not a total loss. Change 01 alone
is worth about 140 kWh / 45 l a day today, about 350 kWh / 100 l with power management on. Treat all of
these as directions, not numbers, until the collector has weeks of data.

## 5. Change 01 - PV logger comm-fail tolerance (docs/changes/change-01_comm_fail_tolerance/)

**17 Oct: v4 (hold + guard) built and tested offline; it replaces v2/v3** (README of the change folder, section
v4). Sheet 10 v4 plus sheet 6 v4 (declarations only). The fail also rises on an invalid AP or Max Available; the
hold keeps the ramp output (what was sent); the guard lowers the held value while Gensets P is below 15 % of the
loaded nominal, only while ECU 2 (the writing session) is fine, at most 4 s in a row; on the grid, with one set
or 10 s after the fail is declared it gives today's 0 (hold shortened from 60 s to 10 s on 17 Oct); a negative request still gives 0. 13 blocks, the seven free PLC bits, AOUT
21286-21292, no Analog Switch, plant block order kept. Closed-loop model `tools/change01_sim.py` (report
`v4_simulation.md`), 19 tests in `tests/test_change01_v4.py`, two independent review rounds, all findings closed
or documented. Largest survivable load drop with one session lost, Gen 4 + 5 at 2,000 kW: v3 700, v4 1,200,
today 1,400 kW (today pays a ~1.2 MW step at every fail). Not imported, not on any controller. Before loading:
the import check on an archive copy, the recorder checks and the kill criteria below.

Why: any comm fail on either session forces the PV request to 0 at once; when the link is back it climbs
from 0 again. What: sheet 10 keeps everything and holds the last request instead (Ana Switch fed by its own
output; the negative-request input removed from the OR), plus a "PV comm lost" delay. Variants: v2 (hold until
the link returns, 300 s delay as constant or setpoint 65514), v3 timed (hold at most 60 s, then 0 exactly as
today) built after the integrator's objection. Sheet 3 v2 (optional, stage 2): open the PV breakers after
5 min without the logger; it hangs on the "PV comm lost" flag, which in v3 is the 60 s delay, so `program_v3`
carries the plant's own sheet 3 and stage 2 with v3 needs a separate 300 s delay first. Built in InteliConfig's own export format by `tools/neo_plc_change01.py`, tested in
`tests/test_change01.py` (identical with a healthy link; short fail ridden through; long fail safe; a real
zero request still reaches the SAU). The PLC has no Analog Switch left (7 of 7); the first import was refused
for that and rebuilt. v2 was imported on a copy of the archive on site; v3 is not yet imported. Request PDF
`change-01_request.pdf` (two pages, plain language) exists; it says "about five minutes" which is the upper
bound. **Status: parked until the collector has two weeks of data; then back with measured hold time and
margin.** Reverse power: the held value is at most 100 kW above the inverters' output at that moment (step
protection), the gensets sit around 25 % (about 788 kW with three sets), so reverse power inside a 60 s
hold needs a load drop of roughly 700 kW within that minute; load steps are not measured; IG4 reverse-power
settings are unread. Never claim the IG4 protection "covers" it. The lab shows the honest picture: with both
sessions silent the zero cannot reach the SAU in either program, so the exposure in that window is the same today
as with the hold; with one session alive today's program delivers the zero through the other session within 10 s
and the hold does not, which is the hold's real cost. A possible v4: hold, but lower the held value by the amount
the gensets fall below Gen P Min (genset kW comes over CAN, not from the SAU), which answers the example directly.
A four-role design review of v3/v4 (modifier, questioner, risk officer, evidence lead) is in
`docs/changes/change-01_comm_fail_tolerance/design_review_v4.md`: the revised v4 (floor from Gen Loaded bits x
per-set constants, never from Running Nominal Of All; hold driven by the comm-fail bits OR the validator invalid
flags; ceiling only during a fail and on gensets; release 1 kW/s), the two kill criteria with register numbers
(#14364 through a fail; the 2100/2101 read-back through one-session fails), and the order: root cause first, then
v3 with N from data and three sets, v4 only after the measurements, no one-set operation and no automatic breaker
opening before the UPS, IG4 and SAU figures. The lab's one-genset scenario (model, assumed UPS size, overload setting and start time) says: one set survives
surges and clouds, survives a total solar loss only if the UPS shed about 600 kW within seconds while the next
set starts, and goes into reverse power when a held setpoint meets a load drop or when the sun returns after a
cloud with the regulator wound up; so one-set operation needs the v4 guard as a PV ceiling (never request more
than load minus the genset floor), not plain v3, plus the real UPS and IG4 figures.

## 6. Tools in this repository

| What | How |
|---|---|
| Hourly collector + dashboard + Turkey Modbus server | `START-PLANT.bat` (first `CHECK-PLANT.bat`) |
| Who answers on Modbus | `PROBE-PLANT.bat` (`plantcollector.cli probe`) |
| The measuring session, one click (1 h: 15 min master only, then the SAU): probe, check, master-only trace, then the SAU with a fail-rate guard and the stall catcher, report, zip, long run with a daily zip | `SESSION.bat` (`plantcollector.cli session`) -> `data\trace\`, `bundles\` |
| Fast read-only trace: 1 s master, 5 s SAU, setpoint read-back, response times, event log; each device in its own thread so a slow SAU never delays the master's rows | `TRACE-PLANT.bat` (`plantcollector.cli trace`) -> `data\trace\` |
| The stall catcher: every second one ping of the SAU and one read of one register on the connection the trace already holds (no extra Modbus link); `watch_<date>.csv`; "answers_us" and "ping_ok" edges in the event log | `trace --watch pv-array=1`; on in `session` by default (`--watch-s 0` turns it off) |
| A day of trace files -> the numbers (fails, SAU latency, setpoint words, regulator through fails, load steps) and **a verdict per master comm fail**: SAU unreachable / alive but silent / slow / answered us normally | `plantcollector.cli report-trace --date` / `bundle --date` |
| A packet capture (Wireshark on the PC, or the router's sniffer streamed to it) -> Modbus latency per session, exception replies, slow transactions, the setpoint and PV series, traffic around each stall | `tools/pcap_modbus.py capture.pcapng` (needs scapy) |
| Read the master's archive (plain parts) | `tools/neo_archive.py` |
| List and execute the PLC program | `tools/neo_plc.py` (`--out`, `--demo`) |
| Run a day of the plant | `tools/plant_model.py` (scenarios S0-S6, days) |
| The PLC lab: plant and program live in the browser, every sheet with live pin values, scenarios, the integrator's example, an assumed reverse-power trip, random production steps; `--replay` runs the program on a recorded trace | `PLC-LAB.bat` (`tools/plc_lab.py`) -> http://localhost:8090 |
| Build the change-01 sheets | `tools/neo_plc_change01.py` |
| The request PDF | `tools/change01_note.py` |
| Plant drawing | `tools/plant_diagram.py` -> `docs/plant_overview.svg` |
| History export to CSV | `tools/neo_history.py` |
| Tests (38) | `python -m pytest -q` |

Docs: `NEO_CONFIGURATION.md` (how the configuration works), `SIMULATION.md` (exact vs assumed, scenarios),
`INFO_NEEDED.md` (open items), `SAFETY.md`, `DATA_FLOW.md`, `TURKEY_MODBUS.md`, `DEVICES.md`,
`site/visit_checklist.md` (the read-only export steps), `changes/` (every proposed change with its files,
tests, screenshots and rollback).

## 7. The staged plan for the contract

0. Know: archives, tables and histories of all nine controllers, the SAU settings and point table, the
   single-line diagram, the UPS wiring, the fuel log.
1. See: the collector on the plant PC, the trace for event studies, a daily report, alerts, remote access.
2. Change 01 with measured numbers, written by the plant on a copy, rollback ready.
3. Two gensets in the sun (power management on, settings as measured).
4. One genset plus the UPS as buffer (change 02: UPS math fix, shed delay, bridge) once the UPS are real.
5. Hand-over: documentation, training, the model recalibrated on measured data.

## 8. Open questions (who answers)

- Why the gensets run by hand since 1 Oct; the two mode changes on normal days (plant).
- UPS: in service, what they feed, wiring of the master's commands and feedbacks, make, model, ports (plant).
- SAU: point table (2100/2101), max client connections, firmware, RS485 counters, watchdog or fallback when
  the master goes silent, ramp rates (SAU web page, installer or Sineng). From the chat: is ETH1 still cabled
  to the same switch; what the two 3,000 ms "valid delay" settings do; can the phantom devices (200, 201,
  weather station) be removed from the device list and who may do it; did Sineng R&D find anything on 11 Oct;
  how the master's PV logger module treats a Modbus exception reply (06 Busy) - as a failed request at once,
  and how many failed requests make the fail bit (archive / InteliConfig, not a guess).
- Inverters: trip windows after 25 Jul (800 V +/-15 %, 48.5-51.5 Hz) against the genset frequency and 800 V
  bus excursions on load steps; alarms visible only in the debug tool (solar engineer, SAU history export
  after sunset).
- IG4: nominal ratings, reverse-power threshold and delay, Modbus servers on or off (archives, probe).
- Which inverter group sits behind PV CB1 and PV CB2; where the SAU meters M1 and M3 measure.
- Network: switch, cable path, Wi-Fi links, 192.168.0.1; remote access CCI allows.
- Plant: production hours, biggest load steps, fuel log, single-line diagram.

## 9. Next steps

1. Remote session with the plant engineer: PROBE, CHECK, START, TRACE on the plant PC; archives, Modbus
   tables and histories of the eight other controllers; SAU screenshots; UPS and genset photos; the questions.
2. Build the IG4 and IM210 profiles from their exported tables; a report tool over the trace files (comm
   fails per session with durations, response-time percentiles, setpoint word pattern, mode changes, UPS
   feedbacks, load steps); then the daily report and alerts.
3. After two weeks of data: Change 01 with numbers; recalibrate the model; the offer for stages 3-5.

## 10. Lessons already paid for

Overclaims cost trust with the plant: "covered by the IG4 reverse-power protection" (unverified, and the trip
is the feared outcome), "five to thirty times a day", "the toggles were to clear alarms" (they were meter
calibration), "the zero never reaches the SAU" (only when both sessions are down). State the source of every
number, mark the rest as estimate, and measure before proposing.
