# CCI Plant Monitoring - Setup download (test build 1.0.3)

**Download:** click `CCI-Plant-Monitoring-Setup-1.0.3.exe` above, then the **Download** button (the arrow, top right of the file view).

Try it on a spare Windows 10/11 PC first.

- Standalone: it carries its own Python and every package, so the PC needs nothing else.
- Windows asks once because the file is not signed: **More info > Run anyway**.
- It installs the background service (starts with Windows, keeps running when the window is closed), the
  **CCI Plant Monitoring** window (Start Menu and Desktop) and the updater.
- Settings and history: `C:\ProgramData\CCI Plant Monitoring`. An update keeps them.
- Uninstall: Settings > Apps > CCI Plant Monitoring. It removes the program, the service, the updater, the firewall
  rules and the shortcuts; the history stays unless "Collected data and settings" is ticked.
- If something goes wrong, send `C:\ProgramData\CCI Plant Monitoring\install.log` and
  `C:\ProgramData\CCI Plant Monitoring\data\collector.log`.

`SHA256SUMS.txt` holds the Setup's SHA-256. Built from branch `live-dashboard`, commit 4c8a2f9.
This branch holds only the download; the code is on `live-dashboard`.

The installed program's updater (1.0.3 and later) checks this branch too: a new Setup here, with its line in
`SHA256SUMS.txt`, is installed automatically at night or with "Install now" in Settings > Updates.
