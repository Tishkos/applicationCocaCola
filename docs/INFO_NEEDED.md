# Still needed from the site

Updated after the remote session of 2026-10-14. The InteliNeo side is mapped; what is left is
the inverter register map and a few confirmations on the first live read.

## 1. ComAp InteliNeo 5500 (site master) - DONE, verify on first live read
- [x] Modbus register list (Generate Cfg image MODBUS, 2026-10-14) → `profiles\intelineo_master.yaml`
- [x] Modbus unit id = **1** (Setpoints: Modbus Server Address 1; CAN address 9 is not it)
- [ ] First `CHECK-PLANT.bat`: `bus_frequency_hz` ≈ 50, `bus_voltage_v` ≈ 241, `power_kw` = the kW on the
      controller screen. If the voltages look shifted by one register the table is 1-based →
      `address_offset: -1` on `neo-01` in `config\site.plant.yaml`.
- [ ] `pvcb_closed` = 1 while the PV breaker is closed (bit order of Binary Inputs); `g1..g5_running`
      match the gensets that are actually loaded (Gen Loaded 16 bit order)
- [x] ECU order of the Neo's Modbus-master devices: archive confirms IM1, PV logger 1, PV logger 2, IM2, IM3
      (profile: pv_logger_comm_fail = ECU 2 | ECU 3)
- [ ] If the plant agrees to extend the controller's Modbus list later: PV logger values (Daily yield,
      Max Active Power Available, inverter counts), the three InteliMains readings, PLC "Load P site",
      the Statistics group (PV / Gen kWh, fuel). Com. objects are listed in the profile header.

## 2. Sineng inverters through the SAU-100 - the one open item for the Turkey tags
- [x] Model **SP-320K-H**, 320 kW, 12 strings, 10 units (SAU export) ; unit ids 1-10 assumed = RS485 addresses
- [x] Signal list with the SAU's data IDs → `profiles\inverter_sineng.yaml` (`sau_id` per point)
- [ ] **SAU Modbus TCP point table** = which register holds which data ID for which unit id.
      Sources, any one of: SAU-WEB → Setting with the *advanced* or *super* login (the *general*
      login shows no protocol pages), Sineng support ("SAU-100 Modbus TCP point table / northbound
      protocol" for SmartCMSHU121B 0.7.3), or a register scan from the plant PC (the PowerShell dump
      script of 14 Oct, `modbus_dump.csv`) matched against the live values in Device Monitor.
      → `raw.address / kind / scale` next to each `sau_id`.
- [ ] **The two PV-logger sessions write the same datapoints one register apart** (archive of 14 Oct):
      group 1 writes "AP Set" as a 32-bit value at 2100-2101 every second, group 2 writes "AP Set" at
      2101-2102 every 10 s (and RP Set 2102 vs 2103, PF 2105, mode 1007 vs 1008, coil 4002 vs 4003).
      With 32-bit values the two writes overlap. Either the SAU really has a second register set at +1,
      or one definition is off by one. The trace reads 2100-2106 back word by word every second: if the
      words move with the 1 s / 10 s pattern, the layout and its effect on the setpoint are visible.
      To settle with the SAU point table. Not to be discussed outside until measured.
- [x] The SAU's virtual array device: register map taken from the Neo's own definition in the archive →
      `profiles\pv_array_sau.yaml` (unit 245 assumed from the live test; CHECK compares ac_power_kw with the
      Neo's PV Actual P). Daily/total yield scaling to verify against the inverters' counters.
- [x] Pyranometer / panel temperature: **no such sensor on site** (SAU device list) - the two
      `pv-env` tags stay unmapped unless a sensor is added

## 3. Mains and meter
- [x] Mains power / availability come from the Neo (Mains Import P, MCB Status) - `mains_available`
      to be confirmed the first time the grid is on
- [x] No PV-side LV meter on the SAU (M1 = generator feeder Acrel PZ96L-E4, M3 = grid feeder Sfere
      PD194E, no data). The Turkey "pv-meter" tags: Psum3 / Qsum3 / Voltage L1-3 N come from the
      Neo's PV aggregate and bus; currents and energies unmapped until a meter or the SAU totals exist

## 4. Plant PC - DONE except the installation visit
- [x] DESKTOP-91MOKR0, Siemens IPC, Windows 10 IoT Enterprise 19045, stays on; ComAp network on
      "Ethernet 2" (USB dongle) at **192.168.0.200**, internet on the CCI Wi-Fi; port 502 free;
      Python not installed (START-PLANT.bat installs it from python.org)
- [ ] At installation: move the ComAp cable from the USB dongle to a built-in LAN port (P1/P2) and give
      that port 192.168.0.200/24, no gateway; set the Siemens virtual "Ethernet" adapter back to automatic
- [ ] A short written OK from plant management for read-only data collection
- [ ] VPN for CCI: the PC is already on CCI's corporate Wi-Fi (172.17.76.x, cci.tr) and has an OpenVPN
      TAP adapter - ask CCI IT whether their platform can reach it there; then `publish.modbus_tcp.bind`

## 5. Nice to have
- [ ] Local diesel price → `fuel_price_per_l`
- [x] The three InteliMains: registers from the Neo's definition → `profiles\im210_mains.yaml`, enabled in the
      plant config (unit id 1 assumed - CHECK)
- [ ] Per-genset detail (fuel level, hours, alarms): Modbus export of one IG4 200 the same way as the Neo's,
      then the commented genset devices in `config\site.plant.yaml`
- [x] PLC program: all eleven sheets exported from InteliConfig (docs\site\neo_plc_2026-10-14, read by
      tools\neo_plc.py). User Buttons 1-4 enable PV CB1/CB2/UPS1/UPS2, Button 10 = manual PV request.
- [ ] Still to confirm in the PLC editor: the select order of the Mux in sheet 8 (which single PV breaker
      gets the 2,200 kW cap), the Math operation code 4 (site PF), the breaker-block timeout; and what
      Forced Value 1 does at the transfers (not in the sheets - a controller function)
- [ ] SAU advanced login also gives the Setup Wizard pages (RS485 settings - the 53-77 % success rate)
