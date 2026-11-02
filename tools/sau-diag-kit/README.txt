SAU / InteliNeo Modbus diagnostic kit  (Windows)
================================================

What it does
------------
Records, for as long as you let it run (recommended: a full day):
  1. every Modbus packet between the InteliNeo (192.168.0.112) and the SAU (192.168.0.128)
  2. a continuous ping log to the SAU with timestamps
  3. a Modbus response-time test from this PC to the SAU, one request per second
  4. a copy of the SAU "Network Connections" diagnostic page every 60 seconds

Nothing here changes any setting on any device. It only listens and records.

Install once (5 minutes)
------------------------
  1. Wireshark  https://www.wireshark.org/download.html
     - during install, say YES to installing Npcap (needed for capture)
  2. Python 3   https://www.python.org/downloads/windows/
     - tick "Add python.exe to PATH" on the first installer screen
  3. Open PowerShell as Administrator and run:
        pip install pymodbus requests

Where to put the PC
-------------------
The packet capture (item 1) can only see controller<->SAU traffic if this PC sits
where that traffic passes:
  - best: a MIRROR / SPAN port on the switch, mirroring the SAU's port, or
  - a small network tap between the SAU and the switch.
If the PC is simply plugged into any free port, items 2-4 still work and are
still very useful, but item 1 will only show this PC's own traffic.
Tell us which case applies when you send the results.

Run
---
  1. Right-click  run_diag.ps1  ->  "Run with PowerShell"  (as Administrator)
     If Windows blocks it:  open PowerShell as Administrator and run
        Set-ExecutionPolicy -Scope Process Bypass
        .\run_diag.ps1
  2. The first time it asks you to pick the network interface (the LAN one, not Wi-Fi).
  3. Leave it running. Everything is written to  C:\sau_diag\<date>\
  4. To stop: press Ctrl+C in the PowerShell window, or run  stop_diag.ps1

Send us
-------
Zip the folder  C:\sau_diag\<date>  and send it, together with:
  - a screenshot of the InteliNeo PV logger / Modbus client settings
    (Pause Before Request, Response Timeout, comm fail delay)
  - the SAU firmware version (About page)
  - whether the PC was on a mirror port / tap, or a normal port

Note on the SAU page sampling (item 4)
--------------------------------------
The script fetches the connections page by URL. If the SAU needs a login, the
page will come back as the login screen instead. In that case, open the page
in Chrome, press F12 -> Network tab, reload, click the request that returns the
table, copy its URL and the "Cookie" header, and paste them into  config.ps1 .
