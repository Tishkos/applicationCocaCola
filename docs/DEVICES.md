# What the device manuals say (and what it means for the collector)

From the Sineng SAU-200 / SAU-300 user manuals and the ComAp InteliConfig global guide.
The SAU-100 on site shows the same web application and protocol set as the SAU-200.

## Sineng SAU gateway

| Fact | Where | Consequence for us |
|---|---|---|
| Modbus TCP slave on port 502, **max 5 links** (connections) | SAU-200 §4.1, SAU-300 §4.1 | The collector uses **one** connection per gateway and closes it after every poll. Nothing else should hold connections open. The InteliNeo already uses some of the five. |
| "Supports data exchange between Modbus TCP and RTU"; "communication point table configurable" | §4.1, §2 table 2-2 | The registers the SAU answers on port 502 are the SAU's **configured point table**, not automatically the inverter's native map. The table is set in SAU-WEB (Setting). That table, or Sineng's document for it, is the "SAU point table" we need. |
| IEC 60870-5-104 slave on 2404 (max 10), IEC 61850-MMS on 102, NTP | §4.1 | Other services on the box; not used by us. The Neo and the collector use 502 only. |
| Modbus RTU host on 4 (SAU-200) / 6+4 (SAU-300) RS485 ports, **≤ 12 devices per bus**, unique IDs per bus | §4.2 | The ten inverters sit on one or two RS485 buses with addresses 1…n; those addresses are the unit ids the SAU forwards. |
| Default IPs: Eth0 192.168.20.128, **Eth1 192.168.1.123** | SAU-200 §3.2 | Explains the second address seen in the capture: 192.168.0.128 is Eth0 (changed on site), 192.168.1.123 is Eth1 at its default. |
| "Virtualizes multiple inverters of the same array into a single adjustable power device" | §5.2 | The device the InteliNeo talks to as **unit 245** is this virtual array device: totals in, power limit out. Per-inverter data is behind the individual unit ids. |
| SAU-WEB manages the box; separate document "sau-web operation manual.pdf" | SAU-200 ch. 4 | The Dashboard / Device Monitor / Setting / Logs pages are SAU-WEB. Ask Sineng for that manual too. |
| USB export of logs, statistics and records; "Enjoy Solar" cloud (LINK1 LED) | §2 table 2-2, §3.3 | The SAU may already report to Sineng's cloud; the LINK1 LED on the box tells. |
| Troubleshooting: "check if the point table configuration matches the connected device / data points, data formats" | ch. 6 / ch. 4 | A wrong point table is Sineng's own first suspect for communication errors — relevant to the PV-logger comm-fail history. |

## ComAp InteliNeo / InteliConfig

| Fact | Where | Consequence for us |
|---|---|---|
| Modbus registers are a **user-defined table in the controller configuration**: Controller configuration → Others → MODBUS. Three groups: binary objects 0–999, values 1000–2999, setpoints 3000–3999; one register = 2 bytes, bigger objects take consecutive registers. | InteliConfig guide §5.2.8 "MODBUS", image 5.19 | This table *is* `profiles\intelineo_master.yaml`. It is site-specific, so it has to be read from this controller. |
| The MODBUS window has **Export MODBUS definition to file**; the Tools ribbon has **Generate Cfg image (MODBUS)** | §5.2.8, §4.2.2, §6.3 | Either gives the whole list in one file: register, object, name, type. That is the export to do on the plant PC. |
| Opening the Controller configuration window online **needs the controller password** (Others → Access → Enter password) | §5.5.1 note, §5.4 | The person with the controller password opens it; we only read and export. Nothing is written to the controller unless **OK and Restart** is pressed — never press it. |
| Export to CSV of Setpoints / Values | §6.3.1 | Handy for a snapshot of all live values with names, to check our readings against. |
| Communication plug-in modules (CM-Ethernet: "TCP Modbus or SNMP") appear as a Setpoints group | §5.2.6 | The Modbus TCP server settings of the Neo (port 502, device address) are in Setpoints → the CM-Ethernet / Modbus group. |

## Facts from site screenshots (SAU device list, IP scan, InteliNeo control window)

| Fact | Consequence |
|---|---|
| Inverters: **Sineng SP-xxxK-HU, 12-string version**, 10 units; RS485 addresses 1–5 on COM1, 6–10 on COM2 | unit ids 1…10 confirmed; 12 string currents, not 17 |
| RS485 success rate per inverter **53–77 %** (5/10/30/60-minute windows) | the SAU is retrying inverters all day: a plausible root of the slow Modbus TCP answers and the Neo's "PV logger comm fail". Site item: RS485 wiring, termination, baud/parity, point table |
| On the SAU as well: ABB PowerValue 11 UPS (COM7:2, not answering), Acrel PZ96L-E4 meter (COM4:1) and Sfere PD194E meter (COM4:2) = Meter1 generator feeder / Meter2 grid feeder, Acrel AM5SE-T relay (COM5:1), ARTU100 RTU (COM5:2), Sineng AgilePID (COM7:3), two CSQ devices (COM3:2 not answering, COM3:3 ok) | generator and grid power also exist on the SAU; a second source for mains/genset values |
| Address 1 is used on COM1 (inverter), COM4 (meter) and COM7 (the SAU itself) | the SAU's Modbus TCP side is **not** a plain pass-through by unit id; the TCP point table / forwarding configuration (SAU-WEB → Setting) decides which TCP unit id and registers hold what |
| Network: MikroTik router .1; nine ComAp devices (.105 .107 .110 .111 .112 .113 .114 .115 .167); SAU .128 (ZHIYUAN hardware); a PC DESKTOP-K287K9O .166 (not the plant PC used now); "SOLAR" Lenovo laptop .171 | .112 = InteliNeo (InteliConfig connects there); .171 is probably the licensed InteliSCADA laptop |
| InteliNeo 5500 SN 240842DA, firmware 1.2.0.43, application "Standard HC"; 05:28: gensets 1,859 kW, mains 0, PV 0, bus 416 V / 49.98 Hz | plant peak load is around 1.9 MW; the demo is scaled to this |
| Binary inputs PV1/PV2 feedback, UPS1/UPS2 feedback; binary outputs PV CB1/CB2 ON/OFF, **UPS1/UPS2 ON/OFF** | the UPS control outputs already exist in the configuration — starting point for the UPS logic work |
| Alarms: Wrn PV Logger group 1 and 2 Comm Fail, **Max Num CU on CAN Exceeded** (red), Wrn Production Mode | the 8-controller firmware limit is a live alarm, not only a report finding |
| SAU dashboard: installed capacity 3,200 kW, 10/10 inverters online, 2/2 meters | PV rated 3.2 MW |

## Facts from the remote session of 2026-10-14 (InteliConfig export, SAU device export, plant PC)

| Fact | Consequence |
|---|---|
| **Plant PC** DESKTOP-91MOKR0 = Siemens industrial PC, Windows 10 IoT Enterprise 19045. Internet via CCI corporate Wi-Fi (cci.tr, 172.17.76.x); ComAp network reached through a Realtek **USB ethernet dongle** ("Ethernet 2"); the two built-in ports LAN [P1]/[P2] are unused; "Ethernet" is a Siemens SIMOSIM *virtual* adapter (always "up", goes nowhere); an OpenVPN TAP adapter exists | PC set to **192.168.0.200/24, no gateway** on the dongle (the old scan's .166 belonged to a different PC). Move the cable to P1/P2 at installation. Port 502 free, Python not installed |
| ComAp network 192.168.0.0/24 has no DHCP; a second network 192.168.200.0/24 with a DHCP router (.1) sits on the same switch | fixed addresses only; the old MikroTik .1 was not seen |
| Controllers (InteliConfig detection): Gen 1-5 **IG4 200** at .105 .106 .113 .114 .115 (SN 242535AC, 241635D3, 241635B6, 2416350D, 2416355C); MAIN 1-3 **IM210G2** at .107 .111 .110 (SN 24264E2B, 24264E58, 24264ED4); **InteliNeo 5500** .112 SN 240842DA, CAN/terminal address 9, direct connection port 23, AirGate enabled ("Waiting to connect" - no internet there) | CAN16 bits 1-9 = Gen 1-5, MAIN 1-3, Neo: nine controllers, hence "Max Num CU on CAN Exceeded" |
| Neo **Modbus server enabled, unit id 1, port 502**; its table = Values 1000-1548 + Binaries 0-453, **no setpoints** exposed; answers unit 1, not unit 9 | `profiles/intelineo_master.yaml` is now the real map; nothing is writable over Modbus even by mistake |
| Not in the Neo Modbus table: PV logger values (SAU totals), the InteliMains readings (LAOD P ≈ 1,47 MW each on 14 Oct), PLC values (Load P site), Statistics (PV kWh, Gen kWh, fuel), PV 1-8 arrays | site load is derived (gensets + mains + PV); richer totals need the plant to extend the Modbus list |
| Neo setpoints of interest: Mains/PV/Gensets installed, BESS not installed; Curtailment enabled, Gen P Min 25 %; Max Export 1 transf 1300 kW; MAX P req 1 PV breaker 2200 kW; UPS 1/2 load-shedding thresholds 70/35 % and 80/35 %; Fuel genset consumption 237.3 g/kWh; Solar Coverage Ratio 20 %; time zone GMT+2, NTP pool.ntp.org | limits for the dashboard and for the later UPS/PV logic work |
| Neo on 14 Oct 17:28 plant time: gensets 1,482 kW / 830 kVAr (4 of 5 loaded: Gen 1,2,3,5), mains 0, PV 0 (night), bus 417 V 50.01 Hz, curtailment output 95.9 %, mode AUTO, PV1/PV2/UPS1/UPS2 feedback = 1, UPS1 OFF / UPS2 OFF outputs = 1, alarms "Max Num CU on CAN Exceeded", "Wrn Production Mode" | demo scale (~1.5-1.9 MW evening load) confirmed |
| Inverters: **Sineng SP-320K-H**, 320 kW / 320 kVA, 12 strings (inner model SPxxxKHUx12), AC 800 V (line ≈ 834 V), named "winding 1 INV#1-5" / "winding 2 INV#1-5"; SAU-100 software SmartCMSHU121B 0.7.3, ETH0 192.168.0.128 gw .1, ETH1 192.168.1.123; Platform Access empty (no cloud) | 3.2 MW PV; two transformer windings = PV logger groups 1 and 2 |
| SAU Modbus TCP: unit 1 answers (exception 2 at register 0 → its table starts elsewhere), **unit 245 answers data at register 0** (virtual array); the *general* web login shows no protocol / point-table pages | the SAU point table is the one open item for the inverter tags - needs the advanced login or Sineng |
| SAU meters: M1 "electric energy meter" (generator feeder, Acrel), M3 (grid feeder, Sfere) - M3 no data; no irradiance / temperature sensor; AgilePID device present; a relay/RMU device exported under the name "SAU-300" | no PV-side meter and no weather data for the Turkey tags |
| SAU web operation log: PV dispatch limit set to 3,500 kW on 2026-06-17 (super user), 4,000 kW in Sept 2025 | the SAU-side cap is above the plant's 3.2 MW - not limiting |
| SAU realtime log: "AC volt low" events on all inverters at 13:21 on 14 Oct, AgilePID grid overvoltage flags | genset-only operation with voltage dips; worth correlating with the Neo history later |
| **Indications (not verified) that the gensets are run outside automatic power management**, 14 Oct: Running Nominal Power *In PM* = 0 while 3,851 kW of gensets run; 4 gensets loaded for 1,482 kW (~38 %) where PM with a 400 kW stop reserve would keep two; User Buttons 1-4 latched on and a user-defined "Wrn Production Mode" warning, whose meaning is in the PLC program (not read). A site contact also said the gensets are started manually | to be confirmed on the IG4 200 panels (MAN/AUTO) or by the site before it goes into any report; if true it is a large fuel item |

| **Controller archive read (2026-10-14)**: the Neo's Modbus-master definitions give the SAU virtual-array registers (1000 AP, 1093 max available, 1009/1011 yields, inverter counts; writes at 2100+) and the InteliMains registers (1001 import P, 1046 load P, coil 0 MCB); connection table .128 ×2, .107, .111, .110 (+ a leftover .1); PLC sheet names; 750 history records incl. daily group link/unlink at sunrise/sunset, OFF→AUTO mode toggles, Gen P Min trials on 1 Oct | see docs/NEO_CONFIGURATION.md; new profiles pv_array_sau.yaml and im210_mains.yaml |
