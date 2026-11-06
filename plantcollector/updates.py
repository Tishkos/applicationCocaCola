"""The installed version and the auto-updater's state, for the dashboard (Settings > About).

The updater itself is scripts/updater.ps1, a Windows scheduled task (PlantCollectorUpdater) that the
Setup registers. It writes its state to <ProgramData>/CCI Plant Monitoring/update/status.json; the
collector only reads that file, and "Check for updates now" only drops a flag file there and starts
the task. Nothing here downloads or installs anything.

Folders when installed by the Setup:
  <data dir>        = C:/ProgramData/CCI Plant Monitoring/data     (the collector's --data)
  <data dir>/../update                                             (the updater's folder)
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone

from . import __version__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPDATER_TASK = "PlantCollectorUpdater"

# shown in Settings > Updates for the installed version: one short line per version
WHATS_NEW = {
    "1.0.3": "The updater also takes new versions from the setup-download branch.",
    "1.0.4": "This version arrived through the automatic updater: updates work on this PC.",
    "1.0.6": "When the Neo reports 0 grid import while MAIN 1–3 carry power, grid import and site load come from the three incomers.",
    "1.0.7": "A Power analyzers tab: the two meters behind the gateways at 192.168.141.1 and .3, read every 15 s. The gensets have animated cards and a generator bus panel.",
    "1.0.8": "Updates download first and then install with one click, in a new update window that says when it is done. Each finished month of readings moves to a file of its own, and the window always shows the newest page.",
}


def installed_version() -> str:
    """The version the Setup wrote (version.txt in the program folder), else the package's own."""
    try:
        with open(os.path.join(ROOT, "version.txt"), encoding="utf-8") as f:
            v = f.readline().strip()
        if v:
            return v
    except OSError:
        pass
    return __version__


def installed_by_setup() -> bool:
    return os.path.exists(os.path.join(ROOT, "version.txt")) and os.path.exists(os.path.join(ROOT, "scripts", "updater.ps1"))


def update_dir(data_dir: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(data_dir)), "update")


def status(data_dir: str) -> dict:
    out = {"version": installed_version(), "installed": installed_by_setup(), "state": "not installed by the Setup",
           "whats_new": WHATS_NEW.get(installed_version())}
    if not out["installed"]:
        return out
    out["state"] = "never checked"
    try:
        with open(os.path.join(update_dir(data_dir), "status.json"), encoding="utf-8-sig") as f:
            st = json.load(f)
        if isinstance(st, dict):
            out.update({k: st.get(k) for k in ("state", "message", "latest", "last_check", "last_install", "auto", "window",
                                               "available")})
            out["version"] = installed_version()
    except (OSError, ValueError):
        pass
    out["check_requested"] = os.path.exists(os.path.join(update_dir(data_dir), "now.flag"))
    # "downloading" / "installing" left behind (the updater was stopped before it could report back): the version
    # that runs now says what happened
    if out.get("state") == "ready" and out.get("latest") and out["latest"] == out["version"]:
        out.update(state="up to date", message=f"Version {out['version']} is installed.", available=None)
    if out.get("state") in ("downloading", "installing"):
        if out.get("latest") and out["latest"] == out["version"]:
            out.update(state="up to date", message=f"Version {out['version']} is installed.", available=None)
        elif _older_than(out.get("last_check"), 70 * 60):    # the updater task is stopped by Windows after 1 h
            out.update(state="error", message="The updater did not report back (see update\\updater.log); "
                                               "'Check for updates' asks it again.")
    return out


def _older_than(stamp, seconds: float) -> bool:
    """The updater writes the PC's local time as "yyyy-MM-dd HH:mm"."""
    try:
        t = datetime.strptime(str(stamp), "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return False
    return (datetime.now() - t).total_seconds() > seconds


def request_check(data_dir: str, install: bool = False, download: bool = False) -> dict:
    """Ask the updater to check GitHub now; with download=True also to download and check a newer release (it then
    reports "ready" and the collector keeps running); with install=True to install it at once (outside its night
    window: the collector stops for about a minute while the Setup runs; a release already downloaded is used)."""
    if not installed_by_setup():
        raise RuntimeError("this copy was not installed by the Setup; it has no updater")
    d = update_dir(data_dir)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "now.flag"), "w", encoding="utf-8") as f:
        f.write(("install" if install else "download" if download else "check") + " " + datetime.now(timezone.utc).isoformat())
    started = False
    if os.name == "nt":
        try:
            r = subprocess.run(["schtasks", "/Run", "/TN", UPDATER_TASK], capture_output=True, timeout=20,
                               creationflags=0x08000000)
            started = r.returncode == 0
        except Exception:
            started = False
    return {"ok": True, "started": started, "status": status(data_dir)}
