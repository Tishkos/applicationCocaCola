"""
The live page of SESSION.bat: what the session sees, second by second, in a browser on the plant PC.

    http://localhost:8070      opened by itself when the session starts

Read-only like everything else: the page shows the session's own state (the master's comm-fail bits, the pings,
the capture, the evidence kept, the console log). The only action is "Make report now", which writes today's
report and zip on this PC. Nothing here talks to the SAU or the master.
"""

from __future__ import annotations

import collections
import glob
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from . import sitetime

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")

# the master's values the page shows (ids from profiles/intelineo_master.yaml)
MASTER_POINTS = ("pv_prod_kw", "power_kw", "load_kw", "mains_power_kw", "bus_frequency_hz", "pv_curtailment_pct",
                 "running_nominal_kw", "mode_auto", "ecu2_comm_fail", "ecu3_comm_fail", "pv_logger_comm_fail",
                 "mains_available", "common_warning", "alarm_active")


class Monitor:
    """Live state and the console log, shared by the session's threads and the web page."""

    def __init__(self, session):
        self.s = session
        self.lock = threading.RLock()
        self.started = datetime.now(timezone.utc)
        self.log_lines: collections.deque = collections.deque(maxlen=2000)
        self.n = 0
        self.master = {"ok": None, "ms": None, "ts": None, "values": {}}
        self.pings: dict[str, dict] = {}
        self.ping_hist: dict[str, collections.deque] = {}
        self.series: collections.deque = collections.deque(maxlen=720)     # one point every 5 s = the last hour
        self._last_series = 0.0
        self.groups = {"group 1": {"fail": False, "since": None}, "group 2": {"fail": False, "since": None}}
        self.fails: collections.deque = collections.deque(maxlen=500)
        self.selftest: dict = {}
        self.url = None
        self.ping_log: collections.deque = collections.deque(maxlen=3 * 3600 * 2)   # (epoch, device, ok), 3 h of both
        self.read_log: collections.deque = collections.deque(maxlen=3 * 3600)       # (epoch, ok) of our master reads

    # ---- the console log -----------------------------------------------------------------------
    def shown(self, text):
        """The demo shows the plant's addresses instead of its loopback ones; on site this changes nothing."""
        for real, show in (getattr(self.s, "display_ips", None) or {}).items():
            text = text.replace(real, show) if isinstance(text, str) else text
        return text

    def add(self, level: str, text: str, ts: datetime | None = None) -> None:
        ts = ts or datetime.now(timezone.utc)
        text = self.shown(text)
        with self.lock:
            self.n += 1
            self.log_lines.append({"n": self.n, "t": sitetime.hms(ts), "level": level, "text": text})

    def log_after(self, after: int) -> list[dict]:
        with self.lock:
            return [line for line in self.log_lines if line["n"] > after]

    # ---- what the trace reports ------------------------------------------------------------------
    def on_read(self, dev_id, ts, out, ms, ok):
        if dev_id != "neo-01":
            return
        vals = {}
        for pid in MASTER_POINTS:
            r = out.get(pid)
            if r is not None and r.value is not None and getattr(r.quality, "value", r.quality) == "good":
                vals[pid] = r.value
        with self.lock:
            self.read_log.append((ts.timestamp(), bool(ok)))
            self.master.update(ok=bool(ok), ms=ms, ts=ts)
            if ok:
                self.master["values"].update(vals)
            now = time.monotonic()
            if now - self._last_series >= 5:
                self._last_series = now
                sau = self.pings.get("pv-array", {})
                self.series.append({"t": int(ts.timestamp() * 1000), "pv": vals.get("pv_prod_kw"), "gen": vals.get("power_kw"),
                                    "load": vals.get("load_kw"), "sau_ms": sau.get("ms") if sau.get("ok") else None,
                                    "fail": bool(self.groups["group 1"]["fail"] or self.groups["group 2"]["fail"])})

    def on_ping(self, dev_id, ts, ok, ms):
        with self.lock:
            p = self.pings.setdefault(dev_id, {"ok": None, "ms": None, "ts": None, "down_since": None})
            if ok:
                p["down_since"] = None
            elif p["ok"] is not False:
                p["down_since"] = ts
            p.update(ok=ok, ms=ms if ok else None, ts=ts)
            h = self.ping_hist.setdefault(dev_id, collections.deque(maxlen=3600))
            h.append(1 if ok else 0)
            self.ping_log.append((ts.timestamp(), dev_id, bool(ok)))

    def fail_started(self, group: str, ts: datetime, sau_ping_ok):
        with self.lock:
            self.groups[group] = {"fail": True, "since": ts}
            self.fails.appendleft({"group": group, "start": ts, "end": None, "seconds": None, "sau_ping": sau_ping_ok, "evidence": [],
                                   "tag": None, "why": None})

    def fail_ended(self, group: str, ts: datetime) -> float | None:
        with self.lock:
            since = self.groups[group]["since"]
            self.groups[group] = {"fail": False, "since": ts}
            for f in self.fails:
                if f["group"] == group and f["end"] is None:
                    f["end"] = ts
                    f["seconds"] = (ts - f["start"]).total_seconds()
                    return f["seconds"]
        return (ts - since).total_seconds() if since else None

    def set_diagnosis(self, group: str, start: datetime, d: dict) -> None:
        with self.lock:
            for f in self.fails:
                if f["group"] == group and f["start"] == start:
                    f["tag"], f["why"] = self.shown(d.get("tag")), self.shown(d.get("text"))

    def evidence_for(self, start: datetime, files: list[str]) -> None:
        with self.lock:
            for f in self.fails:
                if f["start"] == start:
                    f["evidence"] = sorted(set(f["evidence"]) | {os.path.basename(p) for p in files})

    # ---- the page's one call --------------------------------------------------------------------
    def state(self) -> dict:
        s = self.s
        now = datetime.now(timezone.utc)
        today = sitetime.day(now)
        with self.lock:
            pings = {}
            for dev, p in self.pings.items():
                h = self.ping_hist.get(dev) or []
                pings[dev] = {"ok": p["ok"], "ms": p["ms"], "age_s": _age(p["ts"], now),
                              "down_s": _age(p["down_since"], now) if p.get("down_since") else None,
                              "loss_pct": round(100 * (len(h) - sum(h)) / len(h), 2) if h else None, "samples": len(h)}
            fails = [{"group": f["group"], "start": sitetime.hms(f["start"]), "date": sitetime.day(f["start"]),
                      "end": sitetime.hms(f["end"]) if f["end"] else None, "seconds": f["seconds"],
                      "sau_ping": f["sau_ping"], "evidence": f["evidence"], "tag": f["tag"], "why": f["why"]} for f in list(self.fails)[:60]]
            groups = {g: {"fail": v["fail"], "since": sitetime.hms(v["since"]) if v["since"] else None,
                          "for_s": _age(v["since"], now) if v["fail"] and v["since"] else None} for g, v in self.groups.items()}
            master = {"ok": self.master["ok"], "ms": self.master["ms"], "age_s": _age(self.master["ts"], now),
                      "values": dict(self.master["values"])}
            series = list(self.series)
            fails_today = sum(1 for f in self.fails if sitetime.day(f["start"]) == today)
        cap = s.cap
        live = getattr(s, "live", None)
        mirror = live.mirror() if live is not None else self.selftest.get("mirror")
        traffic = live.stats(60) if live is not None else []
        for r in traffic:
            r["peer"], r["label"] = self.shown(r["peer"]), self.shown(r["label"])
        latency = live.master_latency(3600) if live is not None else []
        ring = sorted(glob.glob(os.path.join(s.ring_dir, "*.pcapng")))
        ev = sorted(glob.glob(os.path.join(s.evidence_dir, "*", "*.pcapng")))
        return {
            "demo": bool(getattr(s, "demo", False)),
            "now": sitetime.local(now).strftime("%Y-%m-%d %H:%M:%S"), "zone": sitetime.label(), "tz": sitetime.zone_name(),
            "uptime_s": int((now - self.started).total_seconds()),
            "hosts": {k: self.shown(v) for k, v in {"master": s.master_host, "sau": s.sau_host, "pc": _pc_address(s.sau_host)}.items()},
            "master": master, "pings": pings, "groups": groups,
            "fails": fails, "fails_today": fails_today, "fails_total": len(self.fails),
            "capture": {"running": bool(cap and cap.alive()), "configured": cap is not None,
                        "interface": self.selftest.get("label"), "mirror": mirror,
                        "live": bool(live and live.alive()), "packets": live.packets if live is not None else None,
                        "ring_files": len(ring), "ring_mb": round(sum(_size(p) for p in ring) / 1e6, 1),
                        "evidence_files": len(ev), "evidence_mb": round(sum(_size(p) for p in ev) / 1e6, 1),
                        "selftest_file": os.path.basename(self.selftest.get("file") or "") or None},
            "daily_report": f"{s.zip_hour:02d}:00",
            "series": series, "traffic": traffic, "latency": latency,
            "summary": _summary(groups, master, pings, mirror, bool(cap and cap.alive()), fails, traffic),
        }

    def evidence_list(self) -> list[dict]:
        out = []
        for p in sorted(glob.glob(os.path.join(self.s.evidence_dir, "**", "*.pcapng"), recursive=True), reverse=True):
            rel = os.path.relpath(p, self.s.evidence_dir).replace("\\", "/")
            out.append({"path": rel, "mb": round(_size(p) / 1e6, 2),
                        "time": sitetime.local(datetime.fromtimestamp(os.path.getmtime(p), timezone.utc)).strftime("%Y-%m-%d %H:%M")})
        return out[:200]


def _age(ts, now) -> int | None:
    return None if ts is None else max(0, int((now - ts).total_seconds()))


def _size(p) -> int:
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def _pc_address(host) -> str | None:
    if not host:
        return None
    from .capture import local_ip_for
    return local_ip_for(host)


# ---- the web server ----------------------------------------------------------------------------------
def create_app(mon: Monitor):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="SAU watch", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=os.path.join(WEB, "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def page():
        with open(os.path.join(WEB, "monitor.html"), encoding="utf-8") as f:
            return f.read()

    @app.get("/api/state")
    def state():
        return JSONResponse(mon.state())

    @app.get("/api/log")
    def log(after: int = 0):
        return {"lines": mon.log_after(after)}

    @app.get("/api/evidence")
    def evidence():
        return {"files": mon.evidence_list()}

    @app.get("/api/evidence/file")
    def evidence_file(path: str):
        base = os.path.realpath(mon.s.evidence_dir)
        full = os.path.realpath(os.path.join(base, path))
        if not full.startswith(base + os.sep) or not full.endswith(".pcapng") or not os.path.isfile(full):
            raise HTTPException(404)
        return FileResponse(full, filename=os.path.basename(full), media_type="application/octet-stream")

    @app.post("/api/evidence/reveal")
    def evidence_reveal(path: str = ""):
        """Open File Explorer on the evidence folder, with the file selected (this PC only: the page listens on localhost)."""
        base = os.path.realpath(mon.s.evidence_dir)
        os.makedirs(base, exist_ok=True)
        full = os.path.realpath(os.path.join(base, path)) if path else base
        if not (full == base or full.startswith(base + os.sep)) or not os.path.exists(full):
            raise HTTPException(404)
        import subprocess
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", full] if os.path.isfile(full) else ["explorer", full])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(full) if os.path.isfile(full) else full])
        except OSError as exc:
            raise HTTPException(500, str(exc))
        return {"opened": full}

    @app.post("/api/report")
    def report_now():
        try:
            zp = mon.s.step_report_and_bundle()
        except Exception as exc:
            mon.add("alert", f"report failed: {exc}")
            raise HTTPException(500, str(exc))
        return {"zip": zp}

    @app.get("/api/report/latest", response_class=PlainTextResponse)
    def report_latest():
        files = sorted(glob.glob(os.path.join(mon.s.trace_dir, "report_*.md")), key=os.path.getmtime)
        if not files:
            return "No report yet. Press 'Make report now'."
        with open(files[-1], encoding="utf-8") as f:
            return f.read()

    return app


def serve(mon: Monitor, host: str = "127.0.0.1", port: int = 8070, open_browser: bool = True):
    """Start the page in a background thread on the first free port from 'port'. Returns the URL."""
    import socket
    import uvicorn
    for p in range(port, port + 20):
        with socket.socket() as sk:
            try:
                sk.bind((host, p))
            except OSError:
                continue
        port = p
        break
    config = uvicorn.Config(create_app(mon), host=host, port=port, log_level="warning", log_config=None)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True, name="monitor-web").start()
    url = f"http://localhost:{port}"
    mon.url = url
    if open_browser:
        import webbrowser
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    return url, server


def _summary(groups, master, pings, mirror, capturing, fails, traffic) -> str:
    """The page's one-paragraph answer to 'what is happening now', in plain words."""
    sau = pings.get("pv-array", {})
    g = [k for k, v in groups.items() if v["fail"]]
    parts = []
    if g:
        longest = max(groups[k]["for_s"] or 0 for k in g)
        parts.append(f"Comm fail now on {' and '.join(g)} for {longest} s: the master has lost the SAU.")
        if sau.get("ok"):
            parts.append("The SAU still answers ping, so the box is up and its Modbus program is late.")
        elif sau.get("ok") is False:
            parts.append("The SAU does not answer ping either, so the box or the network to it is down.")
    elif master.get("age_s") is None or master["age_s"] > 10:
        parts.append("Waiting for the master: our reads get no answer yet.")
    else:
        parts.append("The master is talking to the SAU normally.")
        if sau.get("ok"):
            parts.append("The SAU answers ping in " + ("under 1 ms." if sau["ms"] < 1 else f"{sau['ms']:.1f} ms."))
        elif sau.get("ok") is False:
            parts.append(f"The SAU has not answered ping for {sau.get('down_s') or 0} s.")
    m = next((r for r in traffic if r["what"] == "Modbus" and r["label"].startswith("the master")), None)
    if m and m["count"]:
        parts.append(f"On the wire the SAU answered the master's last {m['count']} requests in {m['median_ms']:.0f} ms typically, "
                     f"{m['max_ms']:.0f} ms at worst" + (f", {m['busy']} of them Busy" if m["busy"] else "") + ".")
    if not capturing:
        parts.append("Wireshark's capture is not recording.")
    elif mirror != "yes":
        parts.append("The capture records this PC's own traffic with the SAU; the master's traffic will be in it once the SAU's switch port is mirrored to this PC.")
    if fails and not g:
        f = fails[0]
        parts.append(f"Last comm fail at {f['start']} ({f['group']}, {f['seconds']:.0f} s)" + (f": {f['tag']}." if f.get("tag") else ".")
                     if f.get("seconds") is not None else f"Last comm fail at {f['start']} ({f['group']}).")
    return " ".join(parts)
