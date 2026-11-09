# Plant Data Collector — Erbil Coca-Cola hybrid plant

New to this work? Read `docs\HANDOVER.md` first: what is known, what is assumed, the rules, and what comes next.

One folder, one click. Reads the plant (ComAp InteliNeo, Sineng inverters through the
SAU gateway, the mains incomer, the meter) once an hour, keeps every value on the plant PC,
shows a dashboard, and exports Excel / CSV — including the tag list DT Turkey asked for.

It only **reads**. There is no code path in this program that writes to any device.

```
START.bat            demo plant (simulated data) — try the dashboard and the exports now
START-PLANT.bat      the real plant (config\site.plant.yaml), read-only polling
CHECK-PLANT.bat      one poll of the real plant, every point printed — the commissioning tool
STOP.bat             stop the collector
UNINSTALL.bat        remove it from Windows startup (the data stays)
OPEN-DASHBOARD.bat   http://localhost:8080
RUN-IN-WINDOW.bat    run in a console window instead of as a Windows task (shows every message; stops when closed)
PROBE-PLANT.bat      which controllers answer on Modbus TCP at all (one read of one register each)
PLC-LAB.bat          the plant and the master's PLC program live in the browser (model only, touches nothing): http://localhost:8090
TRACE-PLANT.bat      fast read-only trace to data\trace: 1 s master, 5 s SAU, response times, event log (Ctrl+C stops)
SESSION.bat          catches every SAU stall with no load on the SAU: master every second, SAU only pinged, Wireshark capture kept around every comm fail, report and zip in bundles\
SESSION-AUTOSTART.bat      (Run as administrator, once) opens SESSION.bat by itself at every Windows logon; a second window stops at once
SESSION-AUTOSTART-OFF.bat  removes that again
```

The dashboard (`plantcollector\web\index.html`) is "CCI Plant Monitoring": a Grafana-style dark panel grid with
collapsible rows, a time-range picker, live tiles with sparklines, gauges, the power flow, state timelines (grid,
gensets, which genset ran when, device links), the master controller's signals, the inverter fleet, the mains
incomers, light/dark, larger text for wall screens and a TV mode. It builds itself from the devices in the config, so
the demo and the real plant each get the panels that fit. The previous design is kept as `index.classic.html`.
Also on the dashboard: **alarm alerts** (banner, bell, sound, optional Windows notification, Acknowledge, 7-day alarm
history; acknowledgements are kept on that computer), **Ctrl+K** search, full-screen panel view, and a **language
switch** - English, العربية, کوردی, Türkçe (texts in `plantcollector\web\static\i18n.js`; the Kurdish should be
checked by a native speaker). **Day report**: `http://localhost:8080/report` - one printable A4 page for any day
(energy by source, savings, grid outages, genset hours, inverter ranking, power quality, alarms); Print / Save as PDF.

If port 8080 is taken by another program, the dashboard takes the next free port and writes its
address to `data\dashboard.url`; the START script and `OPEN-DASHBOARD.bat` read it from there.
The Modbus port for Turkey is never moved (it is what they were told); if it cannot start, the
collector runs without it, says so on the Settings page, and retries every minute.

The first start installs Python (if missing) and the packages, so it needs internet once.
It then registers the collector as a Windows task that **starts with Windows and restarts
itself if it fails**: shutting the PC down is fine, everything collected so far is in
`data\telemetry.sqlite` and collection continues at the next boot. The click asks for
administrator rights once (for the Windows task); if that is refused it still runs, in the
window, until the window is closed.

## Install with the Setup (one wizard, standalone: service, window, updater)

`CCI-Plant-Monitoring-Setup-<version>.exe` (from the repository's GitHub Releases, or built with
`installer/build.sh`) installs everything in one wizard, as administrator. It is **standalone**: it carries its
own Python 3.13 and every package, so the PC needs nothing else (no Python, no internet, no other program):

- the program in `C:\Program Files\CCI Plant Monitoring`: `CCI Plant Monitoring.exe` (the window),
  `CCI Plant Monitoring Service.exe` (the collector), `runtime\` (the program's own Python and packages);
- **the background service**: the Windows task `PlantDataCollector` runs `CCI Plant Monitoring Service.exe`
  as SYSTEM at start-up, and starts it again within 5 minutes if it ever stops. It runs whether the window is
  open or not, and whether anybody is signed in;
- **the window** "CCI Plant Monitoring" in the Start Menu (and on the Desktop): the dashboard as an
  application, no browser and no address. Closing it does not stop anything. It needs the Microsoft Edge
  WebView2 Runtime (part of Windows 11 and of an up-to-date Windows 10); without it the window says so and
  opens the dashboard in Edge or Chrome instead, and the service is not affected;
- **the updater**: the Windows task `PlantCollectorUpdater` asks GitHub for the newest version every
  4 hours: a published Release, or the Setup on the branch `setup-download` (named in its `SHA256SUMS.txt`),
  whichever is newer. Plain web requests: no GitHub program, account or sign-in on the PC, only internet. With automatic updates on, it installs it at night (01:00-05:00, the PC's clock), after
  checking the file's SHA-256. If the new version does not answer within 3 minutes, it puts the previous
  one back and does not try that release again. Settings > Updates shows the state, with
  "Check for updates" and "Install now" (only in the window on that PC);
- optional: the firewall rules for Turkey's Modbus (TCP 502) and the dashboard (TCP 8080).
- **"Install now"** closes the window and opens a small update window (downloading, installing, starting); when the
  new version answers, CCI Plant Monitoring opens again by itself. Night updates run without any window.
- **One collector only:** the collector holds a machine-wide lock (`Global\CCI.PlantMonitoring.collector`); a second
  one, however it is started, ends at once, so no device is ever read twice.
- **Full screen** (the top-bar button, F, a panel's own button, TV mode) fills the screen with the program window;
  Esc leaves. **Settings** shows one "Save settings" bar with what is about to change, and Discard.

**The database** is SQLite, built into the program (no database server to install or look after):
`C:\ProgramData\CCI Plant Monitoring\data\telemetry.sqlite`, in WAL mode, so a power cut does not damage it.
Every reading of every site read (all devices, every 10 minutes) is stored, and nothing is ever deleted.
The live reading of the master (every minute by default) is stored once a minute, every state change at once, and
every value at least every 5 minutes. With the plant config (15 devices, 688 points) that is roughly
100,000 site rows and up to 130,000 live rows a day, about 30-35 MB a day.
Each finished month (UTC) moves to a file of its own, `data\archive\readings-YYYY-MM.sqlite` (since 1.0.8), in the
background and a few thousand rows at a time; every chart, export and day report reads the months it covers by
itself. Each month file records the last row of the main file it holds, so a reading written late (a PC clock that
was wrong) is added to its month later and never hidden or lost, and the copies leave the main file one hour after
the move. Back up the whole `data` folder, `archive` included. (Grafana reads only `telemetry.sqlite`.)

Settings and history live in `C:\ProgramData\CCI Plant Monitoring` (`config\`, `data\`, `update\`,
`install.log`); an update never overwrites them. Only SYSTEM and administrators may change that folder (users
may read it), because the service and the updater run as SYSTEM from what is in it. While the Setup replaces
the files, both exes refuse to start ("being updated"). If an earlier copy of this folder ran as the Windows
task, the Setup finds it and brings its `config\site.plant.yaml`, database and `settings.json` over
(the first time only). The wizard can also be pointed at such a folder.

Uninstall: Settings > Apps > CCI Plant Monitoring. It removes the program (with its Python), both tasks,
the processes, the firewall rules and the shortcuts. The history and settings stay unless "Collected data
and settings" is ticked (silent: `uninstall.exe /S /DELETEDATA`).

Silent install: `Setup.exe /S [/AUTOUPDATE=1|0] [/FIREWALL=1|0] [/DESKTOP=1|0] [/TURKEY=1|0] [/STARTWINDOW=1|0]
[/IMPORT=<folder>] [/SOURCE=<this PC's plant IP>]`; the updater runs `/S /UPDATE` (the earlier choices).
`/TURKEY` is the wizard's "Turkey's Modbus feed" box: it switches `publish.modbus_tcp.enabled` in the settings
(through the program's own `turkey on|off` command, which changes that one value and nothing else); `/STARTWINDOW`
is the "Open the window when a user signs in" box (a shortcut in the all-users Startup folder). Left out, both stay
as they are.

Building: `installer/build.sh 1.2.3` (Linux; needs Python 3.13 with pip, zstd, nsis, gcc-mingw-w64-x86-64).
`installer/build_runtime.py` makes `build/stage`: the Windows Python from conda-forge (exactly the files and
SHA-256s in `installer/runtime.lock`), the packages from `wheels\` (offline), the program, the two exes
(`installer/launcher/`: they start the program's own `runtime\python313.dll` in-process, so Windows shows
their names and icon) and the `.pyc` files. NSIS then packs it.

A new version for the updater: put the Setup and its `SHA256SUMS.txt` on the branch `setup-download` (the
previous Setup removed), or publish a release: `git tag v1.2.3 && git push origin v1.2.3`. The workflow `.github/workflows/release.yml`
runs the tests, builds the Setup and publishes it with `SHA256SUMS.txt`. The Setup is not code-signed,
so Windows SmartScreen asks once on a hand install ("More info" > "Run anyway"); the updater is not
affected.

## Desktop application (own icon, one double-click, no browser)

`INSTALL-APP.bat` puts a **Plant Collector** icon on the Desktop and in the Start Menu. The icon
opens the dashboard as a real program window (`app\desktop.py`: a native window on Edge WebView2,
title "Plant Collector", the collector's icon, no address bar, no tabs, no "localhost" on screen).
If the collector is not running yet it is started first - the real plant, read-only, exactly like
`START-PLANT.bat` - and the window shows the dashboard as soon as it answers.

```
INSTALL-APP.bat       create the Desktop + Start Menu shortcut (once) and open the app
PLANT-COLLECTOR.bat   the same as the icon, from the folder
UNINSTALL-APP.bat     remove the shortcuts again (the collector and the data stay)
```

The native window needs the `pywebview` package: it is in `requirements.txt` and its wheels are in
`wheels\`, so the next `START-PLANT.bat` / first click installs it with everything else (no
internet needed). Until it is installed, the icon opens the same dashboard in an Edge/Chrome app-mode
window instead (also frameless; the origin shows for a moment at start). `app\launch.ps1 -Demo` opens
the demo plant. Files: `app\desktop.py`, `app\launch.ps1`, `app\Install-DesktopShortcut.ps1`,
`app\PlantCollector.ico` / `.png`. The logo is one artwork, `app\PlantCollector-master.png` (red
rounded square, the letters C C with a lightning bolt as the I, a gauge with its needle on top);
`python tools\make_icon.py` derives every size from it: the `.ico` (256 … 16 px) for the window,
taskbar and shortcut, the 256 px `.png` for the start-up page of the window, and for the dashboard
`static\app-icon.png` (512 px, shown at 128 px at the top of the left rail) and `static\favicon.png`
(the browser-tab icon; a dot is drawn in its corner when the collector is offline or alarms are active).
A new render of the logo on white goes in with `python tools\make_icon.py --cut render.png`.

How it behaves:

- **One window.** Clicking the icon again while the window is open (or still opening) brings that
  window to the front; nothing else is started. Two guards: `launch.ps1` holds a mutex until its
  window is up, `desktop.py` holds one for as long as the window exists.
- **Its own taskbar identity.** The window registers under the application name
  `CCI.PlantCollector` (AppUserModelID) and the shortcut is tagged with the same name, so the
  taskbar shows the Plant Collector icon - not Python's - and "Pin to taskbar" pins the real
  shortcut. After `INSTALL-APP.bat`, if the taskbar still shows an old icon for a moment, that is
  Windows' icon cache: sign out and in, or restart Explorer.
- **Quick start.** The launcher tests the window packages only once (`.venv\.desktop-ok` remembers
  it), the window opens at once, and when the collector is not running yet the window starts it
  behind a "Starting the collector…" page - first by simply starting the registered Windows task
  `PlantDataCollector` (a few seconds, no prompt), and only if that is not possible the long way
  through `scripts\run.ps1` (administrator prompt, as `START-PLANT.bat`).

## What you get

- **Dashboard** at `http://localhost:8080` (also from any PC on the plant LAN at `http://<pc-ip>:8080`):
  live power flow (grid / solar / gensets → load), today's solar share, fuel, grid hours,
  trends over 6 h … 30 days, every device with its live values and data quality, event log
  (grid lost/back, device not answering, controller alarms), all points with their register.
- **Export**: pick a window and a step → **Excel** (Data + Summary + Points sheets) or **CSV**.
  One click for the **daily report** workbook and for the **DT Turkey tag list with live values**.
- **API** (documented at `http://localhost:8080/api/docs`): `/api/v1/tags` serves the 305
  Turkey tags with value, quality and timestamp; `/api/v1/local/series`, `/export.csv`, … serve the rest.
- **Modbus TCP server for the CCI / DT Turkey platform**: the same 305 tags at fixed holding registers
  (`docs\TURKEY_MODBUS.md`, also at `/api/v1/tags/registers`), refreshed after every site read, read-only.
  Their platform polls this PC over the VPN; the field devices are never polled from outside.
  Switched on with the "Turkey's Modbus feed" box of the Setup, which sets `publish.modbus_tcp.enabled: true` in
  `config\site.plant.yaml` (by hand, from the program folder: `runtime\python.exe -m plantcollector.cli --config
  "C:\ProgramData\CCI Plant Monitoring\config\site.plant.yaml" turkey on|off|status`, then restart the service); the filled-in tag
  lists for Turkey are in `docs\turkey\` (regenerate with `python tools\turkey_register_map.py --host <vpn-ip>`).
- **Data** in `data\telemetry.sqlite`: every reading with timestamp and quality (good / stale / bad).
  A bad read is stored as bad, never as a zero.
- **Grafana — CCI Plant Monitoring** (optional, needs Docker Desktop): `grafana\docker-compose.yml` →
  `http://localhost:3000`. One dashboard for the whole plant on the real device ids: power flow and source
  mix, the 5 gensets (breaker, CAN presence, loading, reserves), the InteliNeo master (mode, breaker state,
  healthy/alarm signals, bus f/V), the SAU and the 10 Sineng inverters (fleet table, string currents),
  the 3 InteliMains incomers, energy per day, device links and the event log. The Grafana image is built
  from `grafana\Dockerfile`: CC logo and "CCI Plant Monitoring" instead of Grafana's, SQLite plugin inside.
  `GRAFANA-DEMO.bat` runs it on simulated real-plant data (`tools\grafana_demo_data.py`), `GRAFANA-STOP.bat`
  stops it. The dashboard is generated: edit `tools\grafana_dashboard.py`, run it, Grafana reloads it.
  Not yet for the real plant: `data	elemetry.sqlite` is in WAL mode, which Grafana in Docker cannot read
  safely through the bind mount - it needs a read-only copy the way the demo makes one.

## Two configs

| File | Use |
|---|---|
| `config\site.demo.yaml` | **Demo** (START.bat, RUN-IN-WINDOW.bat). The real plant's devices and point ids, fed by a model of the plant (`plantcollector\sim_realplant.py`): grid outages, genset rotation, PV curtailment, SAU stalls, inverter trips. No hardware, no network traffic. |
| `config\site.yaml` | The older, simpler demo plant (grid + solar + 5 gensets + meter); the tests use it. |
| `config\site.eth1.yaml` | **Not in use since 21 Oct 2026: ETH1 is unplugged on Sineng's advice, the SAU is only at 192.168.0.128.** Kept in case ETH1 is ever cabled again. **A PC on the SAU's second port (ETH1, 192.168.1.x).** Inverters and PV array through 192.168.1.123 only; the master and the mains are not reachable from there. Copy it over `site.plant.yaml` on that PC. SESSION.bat needs the master: run it on the ComAp PC. |
| `config\site.plant.yaml` | **Real plant.** Read-only Modbus polling of the InteliNeo (192.168.0.112, unit 1 - mapped from the controller's own Modbus export) and the ten inverters through the SAU (192.168.0.128 - registers pending its point table), **once an hour** (`poll_interval_s: 3600`). |

### To go live

1. **Plant PC on the ComAp network**: the port with the ComAp switch cable gets a fixed address
   `192.168.0.200`, mask `255.255.255.0`, no gateway (done on 14 Oct on the USB dongle; at
   installation move the cable to a built-in LAN port and set the same address there).
2. `CHECK-PLANT.bat`: the InteliNeo block must come back `good` with `bus_frequency_hz` ≈ 50 and
   `power_kw` equal to the kW on the controller screen (if the values look shifted by one register, set
   `address_offset: -1` on `neo-01`). The inverters show `registers pending` until step 3.
3. **SAU Modbus TCP point table** (SAU-WEB → Setting with the advanced login, or Sineng support) →
   add `address` / `kind` / `scale` next to each `sau_id` in `profiles\inverter_sineng.yaml`, run
   `CHECK-PLANT.bat` again, compare with the SAU's Device Monitor page.
4. `START-PLANT.bat`. Done. The Turkey platform reads the Modbus server (`docs\TURKEY_MODBUS.md`)
   over the VPN, or `http://<pc-ip>:8080/api/v1/tags` / the exported file.

Changing the poll rate: the dashboard's **Settings** page (10 min / 30 min / 1 h / 2 h, never more often, saved in `data\settings.json`), or `poll_interval_s` in `config\site.plant.yaml`. "Read devices now" in the top bar reads everything once, at any time.

### Live dashboard, gentle on the devices (`live:` in `config\site.plant.yaml`)

| What | How often | Why |
|---|---|---|
| **Site read** - every device, then the **Turkey feed** (Modbus registers, `/api/v1/tags`, the tag files) | every 10 min | what Turkey was told; their values change only here |
| **InteliNeo master** | every 1 min (Settings: 5 s ... 10 min, or off), one kept connection, 3 requests | it already knows gensets P (CAN), mains import P, PV actual P, breakers, mode, which genset is on the bus: the dashboard is live without one extra request to the SAU or the InteliMains |
| SAU + 10 inverters, pv-array | at the site read (10 min), 0.2 s between devices (Settings: or 30 min / 1 h / 2 h) | no load on the SAU |
| InteliMains MAIN 1-3 | at the site read (10 min) (Settings: or 30 min / 1 h / 2 h, each on its own) | see the guard below |

**Settings > Reading the devices** sets all of these from the dashboard: the master's live rate (5 s, 10 s, 30 s,
1 min, 2 min, 5 min, 10 min, or off: then the master is read at the site read like the others), and for every other device or
gateway "with every site read" or less often. Applied at once, kept in `data\settings.json` (`live_every_s`,
`device_every_s`) after restarts and updates. Devices behind one address (the inverters and the PV array behind the SAU
logger) are one row. Choosing "with every site read" also undoes an automatic slow-down by the guard. With the master
read every 30 s or slower, the live chart shows the last hour instead of 15 minutes. Read slower than once a minute, the guard
is off: the master's comm fails are still logged, but nothing is slowed down automatically (a fail seen minutes
late cannot be matched to one of our reads). Turkey's feed is built at the site
read either way; a device read less often than the site read shows its Turkey values as stale between its reads.

- The live reader never refreshes the Turkey feed. It never asks the master more than `max_requests_per_s` (2): if the
  controller makes us read in smaller blocks, the live rate slows down by itself. When the master does not answer it
  backs off (2, 4, 8 ... 60 s). After a failed request the connection is opened fresh, so a late answer is never taken
  for the answer to the next request.
- The database gets the master's numbers once a minute and its on/off signals and states when they change (every value
  at least every 5 min): about 100,000 rows a day instead of 4 million.
- **The master's own comm-fail bits** (ECU 1-5: MAIN 1-3 and the two PV logger groups) are shown live in "Devices the
  master reads" and every fail and its end goes to Alarms & events, with "N s after this program read <device>" when it
  came right after one of our reads. A device in `back_off` (the InteliMains) that the master loses right after our read
  twice is read only hourly from then on (`slowed_devices` in `data\settings.json`; Settings > Reading the devices undoes it).
- New on the dashboard: "Last 15 minutes · live" (or "Last hour · live") under the single-line diagram, "Devices the
  master reads" in the master row, and how often each device is read (row titles and device cards). The overview, the
  diagram, the gensets and the master refresh at the live rate while the master is read live (every 3 to 10 s).
- `live: {enabled: false}` puts everything back at the site read, as before.
- A PC with Wi-Fi or a 4G router in the same 192.168.0.x range as the plant cable: set `network: {source_address:
  <the PC's cable address>}`, or Windows sends the plant's traffic out through the Wi-Fi and every device looks dead.

## QS Collector, SAU watch: SESSION.bat (passive: no load on the SAU)

Runs until its window is closed (and starts again by itself if it ever crashes). A live page opens in the
browser at `http://localhost:8070`: what is happening now in plain words, the master, the SAU and this PC with
their links, the plant values from the master, the last hour, every comm fail with **why it happened** (SAU busy,
SAU slow, SAU unreachable, no answer, or the master's own side, with what else was talking to the SAU at that
moment), the traffic with the SAU per client (requests per minute, typical and worst answer, late, Busy, no
answer), the console and the evidence files. Closing the browser stops nothing. Every issue is also written in the
window as it happens, in colour. All times are Erbil time.

With Wireshark installed, tshark reads the capture as it happens (it only listens): with the SAU's switch port
mirrored to this PC it follows every request of the master and every answer of the SAU, so a busy SAU is named
the moment it answers late, even when the master does not declare a fail.

### Details

`SESSION.bat` is for us, to catch the SAU's stalls whenever they happen, for as long as it runs, without adding
anything to the SAU's load. `CHECK-PLANT.bat` / `START-PLANT.bat` are the collector for the Turkey platform; they
have nothing to do with this.

What it does, logged to `data\session_<date>.log`:

- the master (InteliNeo) every second: its PV logger comm-fail bits give every fail to the second, with its length;
- a ping of the SAU and of the master every second, and the MAC behind each address every 10 s. A ping is
  answered by the SAU's network stack, not by its Modbus program, so it costs the SAU nothing that matters and
  tells "box alive, program late" apart from "box or network gone";
- **the SAU is never read and never connected to** by this program (the probe skips it, there is no check);
- when Wireshark is installed on the PC: Wireshark's own `dumpcap` writes the SAU's traffic (filter
  `host 192.168.0.128`) into a ring in `data\capture\`, one file per hour, two days kept, on the adapter that leads
  to the SAU (found by itself; `--capture-if "Ethernet 3"` to force one). **Before it starts, a self-test**: 30 s of
  capture while the SAU is pinged five times; the adapter is used only if the SAU's packets are in it (otherwise the
  next adapter is tried), the log says "capture test PASSED on ..." and whether the master's traffic (the mirror) was
  seen, and the test capture is kept as `data\evidence\selftest\selftest_<time>_<adapter>.pcapng` (it goes with the
  first zip). Around every comm fail the files are
  copied to `data\evidence\<date>\` so the ring never overwrites them. Capturing only listens;
- every day at 16:00 Erbil time, for the whole day before and for today so far (or any time with "Make report now"): `data\trace\report_<date>.md` and a zip in `bundles\`
  with the files, the report and the kept capture files. The report has, per comm fail: whether the SAU answered
  pings, and from the capture whether the SAU's TCP acknowledged the master's requests at once and how late its
  Modbus answers came, or with which exception (06 Server Device Busy). That is the evidence for Sineng.

The capture sees the master's requests to the SAU only when the switch (or the MikroTik) **mirrors the SAU's port**
to the PC's port. Without a mirror it sees this PC's own traffic to the SAU and the report says so; the master's
fail times and the pings are still there. Do not run Wireshark's own window on the same capture at the same time
for days: it keeps everything in memory.

The older active session (it reads the SAU every 5 s with a guard) is still there for a short, supervised test:
`python -m plantcollector.cli --config config\site.plant.yaml --data data session --mode active --hours 1`.

## Event studies: the trace

The hourly collector is for the Turkey platform. For questions like "what happens in the ten seconds of a
PV logger comm fail", `TRACE-PLANT.bat` runs a separate, read-only trace in a console window: the master
every second, the SAU array every five seconds (the setpoint registers the master writes are read back word
by word), the response time of every read, a fresh-connection test every minute, and an event log of every
bit that changes. Files land in `data\trace\` (one set per day, CSV), nothing goes into the database.
Other intervals: `python -m plantcollector.cli --config config\site.plant.yaml --data data trace --every neo-01=1 --every pv-array=1`.
The SAU allows five Modbus TCP links in all and the master uses two, so one trace at a time.

## The PLC lab

`PLC-LAB.bat` (or `python tools\plc_lab.py`) runs the plant model one second at a time with the master's real
PLC program inside it and shows every sheet with live pin values, like the controller's own PLC monitor, on a
model you can poke: a comm fail on one or both sessions, a cloud, a load step, the operator's OFF toggle, a
grid transfer, a fault reset, two to four gensets, power management on or off, and the switch between today's
program and the change-01 variants. A strip chart shows the last 15 minutes. It never connects to the plant.

What is exact in it: the program at its 100 ms cycle, the setpoints, the session refresh and timeout figures from
the archive, the load anchors and grid hours from the history, the sun curve for the date. What is modelled, and
adjustable on the page: how fast the inverters follow a setpoint, the controller's curtailment regulator, whether a
write lands in the SAU while it gives no replies, load noise, cloud edges. A comm fail is injected as "the SAU stops
answering" and the flag rises the way the module declares it, after the timeout and the retries, so a two-second
hiccup never raises it. **Replay mode** removes the modelling: `python tools\plc_lab.py --replay data\trace\neo-01_<date>.csv
--replay-sau data\trace\pv-array_<date>.csv` runs the program on the values the trace recorded on the plant and draws
the recorded setpoint next to the computed one, with today's program or either change-01 variant.
Scenarios on the page (today by hand, change 01 v3, change 01 v2 with stage 2, power management AUTO, Gen P Min
20 %, AUTO with the PLC and UPS fixes, two gensets in the sun, AUTO with two sets minimum, a grid day) each start
from zero with a fresh program and plant; "reset" does the same for the current one. Counters since the start:
solar used, available and thrown away, genset kWh, fuel (model), comm fails, setpoints forced to 0, warnings,
UPS sheds, min and max gen load, seconds in reverse power, reverse power trips, minutes with the plant dark.

The previous integrator's example is a button: "link lost, load drops 800 kW" (and 1,200 kW, and the one-session
variant). The gensets' reverse power protection is in the model as an assumed 5 % for 5 s until the IG4 settings are
read; a trip opens the gensets and the plant goes dark for three minutes, so it counts. Random production steps
(chillers, lines) come and go on their own, size and rate adjustable, so the test is not hand-picked. Hovering a
line names it with its value at that second.

A ninth scenario tries the fuel idea directly: one genset by day with the UPS as the buffer and the next set on
reserve. The gensets got an assumed overload protection (110 % for 10 s), started sets need an assumed 30 s before
they take load, and each UPS feeds an assumed 300 kW; all three are fields on the page until the plant confirms
them. The SAU ramp is set to 5 kW/s, the climb measured on 14 October after breaker closes, and the model's
curtailment regulator no longer winds up while the PV is off or unseen (the history shows the request restarting
low after a breaker close).

## Folder map

```
START.bat, START-PLANT.bat, CHECK-PLANT.bat, STOP.bat, UNINSTALL.bat, OPEN-DASHBOARD.bat, RUN-IN-WINDOW.bat
scripts\run.ps1                     what the START scripts run (Python, packages, Windows task, browser)
scripts\stop.ps1, uninstall-service.ps1
config\site.demo.yaml               demo of the real plant (simulated)
config\site.yaml                    older simple demo plant (tests)
config\site.plant.yaml              real plant (read-only Modbus)
profiles\*.yaml                     device point maps (TODO = placeholder register)
profiles\tag_catalog.yaml           the 305 DT Turkey tag names mapped to our device/point ids
plantcollector\                     the program: adapters (modbus_tcp, inteliscada, simulator),
                                    store (SQLite), views, export (xlsx/csv), api (FastAPI), web (dashboard),
                                    publish (the read-only Modbus TCP server for Turkey)
docs\turkey\*.xlsx                  the two tag-list workbooks (Rev. 1.2) from DT Turkey
docs\DATA_FLOW.md                   how the data moves and how Turkey gets it
docs\TURKEY_MODBUS.md               the Modbus register map served to the Turkey platform
docs\DEVICES.md                     what the manuals and the site sessions say, and what it means for us
docs\site\                          exports taken on site (InteliNeo Modbus table, archive extract, PLC sheets, history of 2026-10-14)
docs\NEO_CONFIGURATION.md           how the InteliNeo configuration works (PLC program, devices, history, settings)
docs\SIMULATION.md                  the behaviour model: what is exact, what is assumed, scenarios and results
docs\plant_overview.svg             one-line and communication drawing of the plant, with what is still to verify
tools\neo_archive.py                reads the readable parts of a controller archive (.aig4), read-only
tools\neo_plc.py                    lists and executes the controller's PLC program from the sheet exports
tools\neo_plc_change01.py           builds the modified sheets of change 01 (docs\changes) in InteliConfig's export format
tools\plant_model.py                runs a day of the plant under today's settings and proposed changes
tools\plant_diagram.py              draws docs\plant_overview.svg
tools\plc_lab.py                    the PLC lab: the plant and the program live in the browser
tools\win_inspect.py                lists InteliConfig's windows and controls (first step of driving its read-only chores)
docs\HANDOVER.md                    read this first: everything known, assumed, decided and open
plantcollector\trace.py             the trace and the probe (read-only, own files)
plantcollector\session.py           the one-click session: steps, the SAU guard, daily report and zip
plantcollector\report.py            turns a day of trace files into the numbers (fails, SAU latency, words, regulator, load steps)
bundles\                            the zips the session makes, one per day
docs\site\visit_checklist.md        the read-only export steps for a site or remote session
tools\neo_history.py                turns a History export into CSV tables
tools\turkey_register_map.py        regenerates that map and fills the Turkey workbooks
docs\SAFETY.md                      why it cannot touch the plant
docs\INFO_NEEDED.md                 the open questions for the site
grafana\                            optional Grafana (Docker): CCI Plant Monitoring dashboard + branded image
tools\grafana_dashboard.py          builds grafana\dashboards\cci-plant-monitoring.json
tools\grafana_demo_data.py          simulated real-plant data for GRAFANA-DEMO.bat
tools\sau-diag-kit\                 read-only diagnostics for the SAU / InteliNeo link
tests\                              python -m pytest -q
```

## Command line (optional)

```
.venv\Scripts\python -m plantcollector.cli --config config\site.plant.yaml check
.venv\Scripts\python -m plantcollector.cli --config config\site.plant.yaml export --hours 168 --step 3600 --out week.xlsx
.venv\Scripts\python -m plantcollector.cli --config config\site.plant.yaml report --date 2026-10-12
```
