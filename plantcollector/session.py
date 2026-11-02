"""
One click, the whole measuring session, with the care a person would take, and then it keeps going.

    python -m plantcollector.cli --config config/site.plant.yaml --data data session --hours 2

Steps, logged to data/session_<date>.log and printed:
  1. probe: who answers on Modbus TCP (one read of one register per controller)
  2. check: one poll of every device through the collector, every point printed, values to compare with the screens
  3. trace, master only, for the first phase (default 30 min): the master's comm-fail rate is measured without us
     touching the SAU at all
  4. trace, master every 1 s and the SAU every 5 s, plus the stall catcher (a ping of the SAU and of the master
     every second, and one small read of the SAU on the connection the trace already holds every second), for
     the rest of the session. The guard: every 10 min the comm-fail count is checked; three or more fails in a
     window pause our reads of the SAU (the pings go on, the master is read as before) for one window - if the
     fails go on without us, we were not the cause and the reads resume; if they stop, the reads stay paused and
     the fact is logged. Nothing reconnects: one trace, one connection, for the whole phase
  5. at the end of the session: a report (data/trace/report_<date>.md) and a bundle
     (bundles/plant_<date>_<HHMM>.zip with the trace files, the report, the probe and check output, the log)
  6. then the long run: master every 1 s (it costs the SAU nothing and gives the fail lengths), SAU every 30 s,
     the watch read every 3 s, pings every 1 s, the same guard, until the window is closed; every day at
     --zip-hour (Erbil time, default 16:00) today so far and the whole day before are reported and bundled

Everything is read-only; the trace, the probe and the collector have no write function.

Passive mode (the default of SESSION.bat): the SAU is never read and never connected to by us, so the session adds
no load to it and can run for weeks:
  - the master every 1 s (its comm-fail bits give every fail to the second),
  - a ping of the SAU and of the master every 1 s (the box alive or not; an ICMP echo is answered by the SAU's
    network stack, not by its Modbus program), the MAC behind each address every 10 s,
  - Wireshark's dumpcap, when installed, writing a ring of files of the SAU's traffic (one per hour, two days kept);
    around every comm fail the files are copied to data/evidence/<date>/ so the ring never overwrites them, and the
    daily report reads them with tshark: per fail, whether the SAU's TCP acknowledged the master's requests and how
    late its Modbus answers came, or with which exception. With the SAU's switch port mirrored to the PC this is the
    proof; without a mirror the files hold only this PC's own traffic and the report says so,
  - before the ring starts, a self-test: 30 s of capture while the SAU is pinged five times, on the adapter towards
    the SAU first and then on the others, until one sees the SAU; the test capture is kept as a Wireshark file in
    data/evidence/selftest/ and goes with the first bundle; the log says whether the master's traffic (the mirror)
    was in it,
  - a report and a bundle after the first hour, then every day at --zip-hour; the probe skips the SAU, no check.
"""

from __future__ import annotations

import glob
import io
import os
import threading
import time
import zipfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

from . import capture as cap
from . import sitetime
from .registry import load_registry
from .report import fails_from_events, summarize, _read
from .trace import Trace, probe


class Session:
    def __init__(self, config: str, data_dir: str, hours: float = 2.0, phase1_min: float = 30.0, sau_s: float = 5.0,
                 long_run: bool = True, zip_hour: int = 16, bundles_dir: str | None = None, log=print, clock=None,
                 watch_s: float = 1.0, guard_window_s: float = 600.0, mode: str = "active",
                 capture: bool = True, capture_if: str | None = None, ring_files: int = 48, ring_file_s: int = 3600):
        self.config, self.data_dir = config, data_dir
        self.hours, self.phase1_min, self.sau_s = hours, phase1_min, sau_s
        self.watch_s = watch_s
        self.guard_window_s = guard_window_s
        self.long_run, self.zip_hour = long_run, zip_hour
        self.trace_dir = os.path.join(data_dir, "trace")
        self.bundles_dir = bundles_dir or os.path.join(os.path.dirname(os.path.abspath(data_dir)), "bundles")
        os.makedirs(self.trace_dir, exist_ok=True)
        os.makedirs(self.bundles_dir, exist_ok=True)
        self.registry = load_registry(config)
        self._print = log
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.log_path = os.path.join(data_dir, f"session_{sitetime.day(self.clock())}.log")
        self.notes: list[str] = []
        self.sau_dropped = False
        self.stop = threading.Event()
        self.mode = mode
        self.capture_on, self.capture_if = capture, capture_if
        self.ring_files, self.ring_file_s = ring_files, ring_file_s
        self.ring_dir = os.path.join(data_dir, "capture")
        self.evidence_dir = os.path.join(data_dir, "evidence")
        self.cap: cap.Capture | None = None
        self.tshark = cap.find_tool("tshark")
        self._kept: dict[tuple, bool] = {}                  # (session, start) -> evidence complete
        self._announced: set[str] = set()                   # evidence files already named in the log
        self._incidents: list[dict] = []                    # other things whose capture is kept: late SAU answers, lost pings
        self.selftest_s = 30
        self.live = None                                     # livecap.LiveCapture: the capture read as it happens
        self.live_on = True
        self.open_browser = True
        self.demo = False                                    # DEMO-SESSION.bat: a simulated plant
        self.evidence_margin_s = 60.0                       # capture kept around a fail: this much before and after
        self.keeper_s = 30.0                                # how often the keeper looks for new fails
        self._selftest_files: list[str] = []                # the self-test captures, kept and sent with the first bundle
        self._last_cap_start = 0.0
        self._cap_down = False
        sau = self.registry.devices.get("pv-array")
        neo = self.registry.devices.get("neo-01")
        self.monitor = None                                  # the live page (monitor.py), passive mode only
        self.monitor_on = False
        self._colour = False
        self.sau_host = sau.connection.get("host") if sau else None
        self.master_host = neo.connection.get("host") if neo else None

    # ---- helpers -----------------------------------------------------------------------------------
    _COLOURS = {"alert": "\033[91m", "warn": "\033[93m", "ok": "\033[92m", "info": ""}
    _MARKS = {"alert": "[!!]", "warn": "[! ]", "ok": "[ok]", "info": "    "}

    def log(self, text: str, level: str = "info") -> None:
        """One line to the window, the session log file and the live page. level: info, ok, warn, alert."""
        line = f"{sitetime.hms(self.clock())} {text}"
        self.notes.append(line)
        if self._colour and level != "info":
            self._print(f"{self._COLOURS[level]}{sitetime.hms(self.clock())} {self._MARKS[level]} {text}\033[0m")
        elif self._colour:
            self._print(f"{sitetime.hms(self.clock())}      {text}")
        else:
            self._print(line)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{sitetime.hms(self.clock())} {self._MARKS[level].strip() or '-'} {text}\n")
        except OSError:
            pass
        if self.monitor is not None:
            self.monitor.add(level, text)

    def _capture(self, fn, *a, **k) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(*a, **k)
        return buf.getvalue()

    def fail_count(self, since: datetime, until: datetime | None = None) -> int:
        until = until or self.clock()
        ev = []
        for d in sorted({sitetime.day(since), sitetime.day(until)}):   # a window across Erbil midnight
            ev += _read(os.path.join(self.trace_dir, f"events_{d}.csv"))
        fails = fails_from_events(ev)
        return sum(1 for f in fails if since <= f["start"] <= until)

    # ---- steps ---------------------------------------------------------------------------------------
    def step_probe(self, skip_hosts: set | None = None) -> str:
        targets = []
        seen_hosts = set()
        skip_hosts = skip_hosts or set()
        for d in self.registry.devices.values():            # one probe per host:port (the ten inverter unit ids
            c = d.connection                                 # behind the SAU would be eleven connections for nothing)
            if d.adapter == "modbus_tcp" and "host" in c and c["host"] not in skip_hosts:
                key = (c["host"], int(c.get("port", 502)))
                if key in seen_hosts:
                    continue
                seen_hosts.add(key)
                targets.append((c["host"], key[1], int(c.get("unit_id", 1))))
        for h in (self.registry.extra.get("probe") or {}).get("hosts", []) or []:
            if str(h) not in skip_hosts and (str(h), 502, 1) not in targets:
                targets.append((str(h), 502, 1))
        rows = probe(targets, timeout=3.0)
        text = "\n".join(f"{r['host']}:{r['port']} unit {r['unit']}: tcp {r['tcp']}, {r['modbus']}" for r in rows)
        answering = sum(1 for r in rows if r["modbus"].startswith("answers"))
        self.log(f"probe: {answering} of {len(rows)} targets answer on Modbus")
        return text

    def step_check(self) -> str:
        from .cli import cmd_check

        class A:
            pass
        a = A()
        a.config, a.data = self.config, self.data_dir
        text = self._capture(cmd_check, a)
        last = [ln for ln in text.splitlines() if "good" in ln][-1:] or ["?"]
        self.log(f"check: {last[0].strip()}")
        return text

    def _trace(self, every: dict, seconds: float, label: str, connect_test_s: float = 300.0,
               watch_s: float | None = None) -> Trace:
        """One trace for the whole phase, in a background thread, with the guard checked every window."""
        watch_s = self.watch_s if watch_s is None else watch_s
        watch = {"pv-array": watch_s} if watch_s and "pv-array" in every else {}
        tr = Trace(self.registry, self.trace_dir, every, connect_test_s=connect_test_s, log=self.log,
                   watch=watch, ping_s=1.0 if watch or len(every) > 1 else None)
        self.log(f"{label}: " + ", ".join(f"{d} every {s:g} s" for d, s in every.items())
                 + (f", watch read of pv-array every {watch_s:g} s, pings every 1 s" if watch else "") + f", {seconds / 60:.0f} min")
        th = threading.Thread(target=tr.run, kwargs=dict(duration_s=seconds, stop=self.stop, status_every_s=1e9), daemon=True)
        t_start = self.clock()
        th.start()
        if "pv-array" in every:
            self._guard(tr, seconds, t_start)
        th.join()
        self.log(f"{label} done: " + ", ".join(f"{d} {tr.rows[d]} rows, {tr.failed[d]} unanswered" for d in every) + f", {tr.events_logged} events"
                 + (f", watch {tr.watch_rows} rows, {tr.stalls} stalls" if watch else ""))
        return tr

    def _guard(self, tr: Trace, seconds: float, t_start) -> None:
        """Every window: 3 or more comm fails -> pause our SAU reads for one window and see whether the fails go
        on without us. They do: resume (we were not the cause). They stop: stay paused, say so."""
        window = min(self.guard_window_s, max(seconds / 2, 1.0))
        deadline = time.monotonic() + seconds
        t_win = t_start
        while time.monotonic() < deadline - 1 and not self.stop.is_set():
            if not self.stop.wait(min(window, max(deadline - time.monotonic(), 0.1))):
                pass
            if time.monotonic() >= deadline or self.stop.is_set():
                break
            now = self.clock()
            n = self.fail_count(t_win, now)
            if tr.paused("pv-array"):
                if n >= 2:
                    tr.resume("pv-array")
                    self.log(f"guard: {n} comm fails while our SAU reads were paused - the fails do not need us; SAU reads resumed")
                else:
                    self.log(f"guard: {n} comm fails while paused - the burst stopped without us; SAU reads stay paused (pings and the master go on). Tell the engineer.")
                    self.sau_dropped = True
            elif n >= 3:
                tr.pause("pv-array")
                self.sau_dropped = True
                self.log(f"guard: {n} comm fails in {window / 60:.0f} min while we read the SAU: SAU reads paused for one window to see whether the fails go on without us (pings and the master continue)")
            else:
                self.log(f"guard: {n} comm fails in the last {window / 60:.0f} min")
            t_win = now

    def step_trace(self) -> None:
        total = self.hours * 3600
        phase1 = min(self.phase1_min * 60, total)
        t0 = self.clock()
        if phase1 > 0:
            self._trace({"neo-01": 1.0}, phase1, "phase 1, master only")
            self.log(f"phase 1: {self.fail_count(t0)} comm fails in {phase1 / 60:.0f} min without the SAU read")
        remaining = total - phase1
        if remaining > 0 and not self.stop.is_set():
            self._trace({"neo-01": 1.0, "pv-array": self.sau_s}, remaining, "phase 2")

    def step_report_and_bundle(self, date: str | None = None, extra_texts: dict | None = None) -> str:
        date = date or sitetime.day(self.clock())
        report = summarize(self.trace_dir, date)
        diag = []
        dpath = os.path.join(self.trace_dir, f"diagnosis_{date}.csv")
        if os.path.exists(dpath):
            import csv
            with open(dpath, newline="", encoding="utf-8") as f:
                diag = list(csv.DictReader(f))
        if diag:
            report += "\n## Why each comm fail happened (worked out a few seconds after it ended)\n\n"
            for r in diag:
                report += f"- {r['start'][11:19]} {r['group']}, {r['seconds']} s: **{r['tag']}**. {r['why']}\n"
            report += "\n"
        ev_files = sorted(glob.glob(os.path.join(self.evidence_dir, date, "*.pcap*")))
        if self.mode == "passive" or ev_files:
            try:
                fails = fails_from_events(_read(os.path.join(self.trace_dir, f"events_{date}.csv")))
                report += "\n" + "\n".join(cap.report_section(ev_files, fails, self.sau_host or "", self.master_host or "", self.tshark)) + "\n"
            except Exception as exc:                        # a bad capture file must not cost the report
                report += f"\n## From the capture\n\nThe capture files could not be read: {exc}\n"
        if self.notes:
            report += "\n## Session log\n\n" + "\n".join(self.notes) + "\n"
        rp = os.path.join(self.trace_dir, f"report_{date}.md")
        with open(rp, "w", encoding="utf-8") as f:
            f.write(report)
        stamp = sitetime.local(self.clock()).strftime("%Y-%m-%d_%H%M")
        zp = os.path.join(self.bundles_dir, f"plant_{date}_{stamp[-4:]}.zip")
        with zipfile.ZipFile(zp + ".part", "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(os.listdir(self.trace_dir)):
                if date in name:
                    z.write(os.path.join(self.trace_dir, name), f"trace/{name}")
            for name, text in (extra_texts or {}).items():
                z.writestr(name, text)
            budget = 300e6                                  # the evidence goes along while the zip stays mailable
            for p in self._selftest_files:
                if os.path.exists(p) and os.path.getsize(p) <= budget:
                    z.write(p, f"evidence/selftest/{os.path.basename(p)}")
                    budget -= os.path.getsize(p)
            self._selftest_files = []                       # once, with the first bundle
            for p in ev_files:
                if os.path.getsize(p) <= budget:
                    z.write(p, f"evidence/{os.path.basename(p)}")
                    budget -= os.path.getsize(p)
            if os.path.exists(self.log_path):
                z.write(self.log_path, os.path.basename(self.log_path))
        os.replace(zp + ".part", zp)                        # never a half-written zip
        self.log(f"bundle: {zp} ({os.path.getsize(zp) / 1e6:.1f} MB), report: {rp}")
        return zp

    # ---- the whole thing --------------------------------------------------------------------------------
    def _until_zip(self, now: datetime) -> float:
        """Seconds until the next daily report, at zip_hour o'clock plant time (Erbil)."""
        loc = sitetime.local(now)
        nxt = loc.replace(hour=self.zip_hour, minute=0, second=0, microsecond=0)
        if nxt <= loc:
            nxt += timedelta(days=1)
        return (nxt - loc).total_seconds()

    def _single_instance(self) -> bool:
        """One session per data folder: a second window (a double click, the autostart and a click by hand) would
        write the same files twice and start a second capture. The lock is released when the process ends."""
        path = os.path.join(self.data_dir, "session.lock")
        f = open(path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self._lock_file = f
        return True

    def run(self) -> str:
        if not self._single_instance():
            self.log("another session is already running on this PC (same data folder); this window does nothing and can be closed")
            return ""
        if self.mode == "passive":
            return self.run_passive()
        self.log(f"session start: {self.hours:g} h, phase 1 {self.phase1_min:g} min master only, then SAU every {self.sau_s:g} s")
        extra = {}
        try:
            extra["probe.txt"] = self.step_probe()
        except Exception as exc:
            self.log(f"probe failed: {exc}")
        try:
            extra["check.txt"] = self.step_check()
        except Exception as exc:
            self.log(f"check failed: {exc}")
        try:
            self.step_trace()
        except KeyboardInterrupt:                           # Ctrl+C = end now, still report and bundle
            self.stop.set()
            self.log("stopped by hand; reporting and bundling what there is")
        zp = self.step_report_and_bundle(extra_texts=extra)
        self.log("session done. Copy the bundle to the stick or send it; the files stay here too.")
        if self.long_run and not self.stop.is_set():
            self.long()
        return zp

    def long(self) -> None:
        """After the session: the master at 1 s (free for the SAU), the SAU every 30 s, the watch read every 3 s,
        pings every 1 s, the same guard; a report and a bundle every day at zip_hour."""
        every = {"neo-01": 1.0, "pv-array": 30.0}
        self.log("long run: " + ", ".join(f"{d} every {s:g} s" for d, s in every.items()) + f", watch read every 3 s, pings every 1 s; a bundle every day at {self.zip_hour:02d}:00 {sitetime.label()}. Close the window to stop.")
        while not self.stop.is_set():
            now = self.clock()
            self._trace(every, self._until_zip(now), "long run", connect_test_s=600.0, watch_s=3.0)
            if self.stop.is_set():
                break
            yesterday = sitetime.day(self.clock() - timedelta(hours=self.zip_hour + 1))
            self.notes = []
            try:
                self.step_report_and_bundle(date=yesterday)
            except Exception as exc:                        # a bad file must not end the long run
                self.log(f"daily report failed: {exc}; the trace goes on")

    # ---- passive: nothing to the SAU but pings ----------------------------------------------------------
    def _start_capture(self) -> None:
        if not self.capture_on or not self.sau_host:
            self.log("capture: off")
            return
        dumpcap = cap.find_tool("dumpcap")
        if not dumpcap:
            self.log("capture: Wireshark (dumpcap) not found on this PC; the master and the pings run without it", "warn")
            return
        cap.stop_stale(self.ring_dir, log=self.log)
        iface, why = cap.pick_interface(dumpcap, self.sau_host, self.capture_if)
        # the self-test: a short capture while the SAU is pinged five times. The adapter towards the SAU first, then
        # every other one, until one sees the SAU. Only that one is used for the ring.
        candidates = ([(iface, why)] if iface else []) + [(n, label) for n, label in cap.list_interfaces(dumpcap) if n != iface]
        if self.capture_if:
            candidates = candidates[:1]
        chosen = None
        for n, label in candidates[:8]:
            res = cap.self_test(dumpcap, n, self.sau_host, self.master_host, os.path.join(self.evidence_dir, "selftest"),
                                self._pinger, seconds=self.selftest_s, tshark=self.tshark)
            if os.path.exists(res["path"]):
                self._selftest_files.append(res["path"])
            if res["ok"]:
                mirror = ("yes: the master's requests to the SAU are in it" if res["mirror"] else
                          "not yet: only this PC's own traffic with the SAU (mirror the SAU's switch port to this PC for the Modbus timing)"
                          if res["mirror"] is False else "unknown (tshark not found)")
                self.log(f"capture test PASSED on {label}: {res['packets']} packets with {self.sau_host} in {self.selftest_s} s while pinging it. "
                         f"Mirror: {mirror}. Test file: {res['path']}", "ok")
                if self.monitor is not None:
                    self.monitor.selftest = {"label": label, "file": res["path"], "packets": res["packets"],
                                             "mirror": "yes" if res["mirror"] else "no" if res["mirror"] is False else "unknown"}
                chosen = (n, label)
                break
            self.log(f"capture test on {label}: {res['packets']} packets with {self.sau_host} - not this adapter" + (f" ({res['detail']})" if res["packets"] == 0 and res["detail"] else ""), "warn")
        if not chosen:
            self.log("capture test FAILED on every adapter: no capture. The master and the pings go on. "
                     "Check that Wireshark/Npcap is installed and this PC is on the plant network, then start again.", "alert")
            return
        self.cap = cap.Capture(dumpcap, chosen[0], self.sau_host, self.ring_dir, file_s=self.ring_file_s,
                               files=self.ring_files, log=self.log)
        if self.tshark and self.live_on:
            from .livecap import LiveCapture
            self.live = LiveCapture(self.tshark, chosen[0], self.sau_host, self.master_host, cap.local_ip_for(self.sau_host), log=self.log)
            self.live.on_episode = lambda t0, t1, text: self.add_incident(
                "the SAU answering the master late", datetime.fromtimestamp(t0, timezone.utc), datetime.fromtimestamp(t1, timezone.utc), text)
            self.live.start()
            self.log("live traffic view on: every Modbus answer of the SAU, its delays, Busy replies, resets and other visitors are followed as they happen")
        if self.cap.start():
            self._last_cap_start = time.monotonic()
            self.log(f"capture: monitoring on {chosen[1]}, filter host {self.sau_host}, a new file every {self.ring_file_s / 3600:g} h, "
                     f"{self.ring_files} kept, in {self.ring_dir}" + ("" if self.tshark else "; tshark not found, the files will be kept but not read"))

    def _pinger(self, host: str) -> None:
        from . import trace
        trace.ping(host)

    def keep_evidence(self, now: datetime | None = None, margin_s: float | None = None) -> int:
        """Copy the ring files around every comm fail of today and yesterday into evidence/<date>/. Returns the
        number of fails whose evidence is complete."""
        now = now or self.clock()
        margin_s = self.evidence_margin_s if margin_s is None else margin_s
        if not os.path.isdir(self.ring_dir):
            return 0
        ev = []
        for d in sorted({sitetime.day(now - timedelta(days=1)), sitetime.day(now)}):
            ev += _read(os.path.join(self.trace_dir, f"events_{d}.csv"))
        done = 0
        for f in fails_from_events(ev):
            key = (f["session"], f["start"])
            if self._kept.get(key):
                done += 1
                continue
            if f["end"] is None and now - f["start"] < timedelta(minutes=10):
                continue                                    # still going: kept when it ends (or after 10 min)
            end = f["end"] or now
            if f["end"] is not None and now < end + timedelta(seconds=margin_s + 5):
                continue                                    # wait for the minute after the fail
            copied, complete = cap.keep(self.ring_dir, os.path.join(self.evidence_dir, sitetime.day(f["start"])),
                                        f["start"], end, margin_s)
            new = [c for c in copied if c not in self._announced]
            if new:
                self._announced.update(new)
                self.log(f"evidence: {', '.join(os.path.basename(c) for c in new)} kept for the {f['session']} fail at "
                         f"{sitetime.hms(f['start'])}", "ok")
            if copied and self.monitor is not None:
                self.monitor.evidence_for(f["start"], copied)
            if complete or now - end > timedelta(hours=2):
                self._kept[key] = True
                done += 1
        return done

    def add_incident(self, kind: str, start: datetime, end: datetime, text: str = "") -> None:
        """Anything else worth its capture: the SAU answering late without a declared fail, the SAU not answering ping."""
        self._incidents.append({"kind": kind, "start": start, "end": end, "text": text, "done": False})

    def keep_incidents(self, now: datetime | None = None) -> None:
        now = now or self.clock()
        margin = timedelta(seconds=self.evidence_margin_s + 5)
        for inc in self._incidents:
            if inc["done"] or now < inc["end"] + margin:
                continue
            copied, complete = cap.keep(self.ring_dir, os.path.join(self.evidence_dir, sitetime.day(inc["start"])),
                                        inc["start"], inc["end"], self.evidence_margin_s)
            new = [c for c in copied if c not in self._announced]
            if new:
                self._announced.update(new)
                self.log(f"evidence: {', '.join(os.path.basename(c) for c in new)} kept for {inc['kind']} at {sitetime.hms(inc['start'])}", "ok")
            if complete or now - inc["end"] > timedelta(hours=2):
                inc["done"] = True
        self._incidents = [i for i in self._incidents if not i["done"] or now - i["end"] < timedelta(hours=3)]

    def _keeper(self) -> None:
        while not self.stop.wait(self.keeper_s):
            try:
                if self.cap is not None and not self.cap.alive():
                    if not self._cap_down:
                        self._cap_down = True
                        self.log("capture: Wireshark's capture stopped; starting it again", "alert")
                    if time.monotonic() - self._last_cap_start > 30:      # not faster than every 30 s if it keeps dying
                        self._last_cap_start = time.monotonic()
                        if self.cap.start():
                            time.sleep(2)
                            if self.cap.alive():
                                self._cap_down = False
                                self.log("capture: recording again", "ok")
                self.keep_evidence()
                if os.path.isdir(self.ring_dir):
                    self.keep_incidents()
            except Exception as exc:                        # the keeper must outlive one bad file
                self.log(f"evidence: {exc}", "warn")

    def _passive_trace(self, seconds: float, label: str) -> Trace:
        tr = Trace(self.registry, self.trace_dir, {"neo-01": 1.0}, connect_test_s=600.0, log=self.log, ping_s=1.0,
                   ping_also=["pv-array"] if "pv-array" in self.registry.devices else [], listener=_Watch(self))
        self.log(f"{label}: master every 1 s, pings of the master and the SAU every 1 s, no reads of the SAU"
                 + ("" if self.long_run else f", {seconds / 60:.0f} min"))
        th = threading.Thread(target=tr.run, kwargs=dict(duration_s=seconds, stop=self.stop, status_every_s=1e9), daemon=True)
        th.start()
        while th.is_alive():
            th.join(0.5)                                     # short joins: Ctrl+C reaches the main thread at once
        return tr

    def run_passive(self) -> str:
        """Runs until the window is closed. Every comm fail is logged as it starts and ends, its capture is kept,
        and every day at zip_hour (Erbil) the day before is reported and zipped. With long_run=False (tests,
        a one-off check) it runs self.hours, reports and stops."""
        _console_setup()
        self._colour = _colour_ok()
        self.log("QS Collector, SAU watch: the master every second, the master and the SAU pinged every second, the SAU never read (no load on it)", "ok")
        if self.monitor_on:
            from . import monitor as mon
            self.monitor = mon.Monitor(self)
            try:
                url, _srv = mon.serve(self.monitor, open_browser=self.open_browser)
                self.log(f"live page: {url} (opens by itself; any browser on this PC)", "ok")
            except Exception as exc:
                self.monitor = None
                self.log(f"live page not started ({exc}); the session goes on in this window", "warn")
        extra = {}
        try:
            extra["probe.txt"] = self.step_probe(skip_hosts={self.sau_host} if self.sau_host else None)
        except Exception as exc:
            self.log(f"probe failed: {exc}", "warn")
        self._start_capture()
        keeper = threading.Thread(target=self._keeper, daemon=True, name="evidence")
        keeper.start()
        zp = ""
        try:
            if not self.long_run:                           # a fixed run: self.hours, then one report
                try:
                    self._passive_trace(self.hours * 3600, "run")
                except KeyboardInterrupt:
                    self.stop.set()
                self.keep_evidence(now=self.clock() + timedelta(seconds=120))
                return self.step_report_and_bundle(extra_texts=extra)
            self.log(f"running until this window is closed; a report and a zip every day at {self.zip_hour:02d}:00 ({sitetime.label()}), "
                     "or any time with 'Make report now' on the live page")
            while not self.stop.is_set():
                self._passive_trace(self._until_zip(self.clock()), "watching")
                if self.stop.is_set():
                    break
                self.notes = []
                now = self.clock()
                for date in (sitetime.day(now - timedelta(days=1)), sitetime.day(now)):     # the whole day before, then today so far
                    try:
                        self.keep_evidence(now=now + timedelta(seconds=120))
                        zp = self.step_report_and_bundle(date=date)
                        self.log(f"daily report for {date} written: {zp}", "ok")
                    except Exception as exc:
                        self.log(f"daily report for {date} failed: {exc}; the session goes on", "warn")
        except KeyboardInterrupt:
            self.stop.set()
            self.log("stopped by hand", "warn")
        finally:
            self.stop.set()
            if self.live is not None:
                self.live.close()
            if self.cap is not None:
                self.cap.stop()
        return zp


class _Watch:
    """The trace's listener in passive mode: turns what changes into console lines and feeds the live page."""

    GROUPS = {"ecu2_comm_fail": "group 1", "ecu3_comm_fail": "group 2"}
    NAMES = {"neo-01": "the master", "pv-array": "the SAU"}

    def __init__(self, session: "Session"):
        self.s = session
        self.m = session.monitor
        self._down: dict[str, datetime] = {}

    def on_read(self, dev_id, ts, out, ms, ok):
        if self.m is not None:
            self.m.on_read(dev_id, ts, out, ms, ok)

    def on_ping(self, dev_id, ts, ok, ms):
        if self.m is not None:
            self.m.on_ping(dev_id, ts, ok, ms)

    def _sau_ping_ok(self):
        return self._ping_ok("pv-array")

    def _ping_ok(self, dev):
        if self.m is None:
            return None
        p = self.m.pings.get(dev)
        return None if not p else p.get("ok")

    def _diagnose(self, group, start, end):
        from .diagnose import diagnose
        try:
            with self.m.lock:
                pings, reads = list(self.m.ping_log), list(self.m.read_log)
            d = diagnose(start.timestamp(), end.timestamp(), pings, self.s.live, reads,
                         master=self.s.master_host, me=self.s.live.me if self.s.live else None)
            self.m.set_diagnosis(group, start, d)
            path = os.path.join(self.s.trace_dir, f"diagnosis_{sitetime.day(start)}.csv")
            new = not os.path.exists(path)
            import csv
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["start", "end", "group", "seconds", "tag", "why"])
                w.writerow([sitetime.local(start).isoformat(timespec="seconds"), sitetime.local(end).isoformat(timespec="seconds"), group,
                            round((end - start).total_seconds()), d["tag"], d["text"]])
            self.s.log(f"why the {group} fail at {sitetime.hms(start)}: {d['tag']}. {d['text']}", "warn")
        except Exception as exc:
            self.s.log(f"diagnosis of the {group} fail failed: {exc}", "warn")

    def on_change(self, ts, dev_id, point, old, new):
        s, name = self.s, self.NAMES.get(dev_id, dev_id)
        if dev_id == "neo-01" and point in self.GROUPS:
            g = self.GROUPS[point]
            if new:
                ping, mping = self._sau_ping_ok(), self._ping_ok("neo-01")
                if self.m is not None:
                    self.m.fail_started(g, ts, ping)
                s.log(f"COMM FAIL {g}: the master lost the SAU (PV logger {g})"
                      + ("; the master itself does not answer ping right now (restarting, or its network), so this is the master's side"
                         if mping is False and ping is not False else
                         "; the SAU still answers ping, so the box is up" if ping else
                         "; the SAU does not answer ping either" if ping is False else ""), "alert")
            else:
                secs = self.m.fail_ended(g, ts) if self.m is not None else None
                s.log(f"comm fail {g} ended" + (f" after {secs:.0f} s" if secs is not None else "")
                      + "; its capture is kept within the next two minutes", "ok")
                if self.m is not None and secs is not None:
                    start = ts - timedelta(seconds=secs)
                    tm = threading.Timer(6.0, self._diagnose, args=(g, start, ts))      # let late answers arrive first
                    tm.daemon = True
                    tm.start()
        elif point == "ping_ok":
            if new:
                s.log(f"{name} answers ping again", "ok")
                down = self._down.pop(dev_id, None)
                if dev_id == "pv-array" and down is not None:
                    s.add_incident("the SAU not answering ping", down, ts)
            else:
                s.log(f"{name} does not answer ping", "warn")
                self._down[dev_id] = ts
        elif point == "reachable" and dev_id == "neo-01":
            s.log("the master answers our reads again" if new else "the master does not answer our reads", "ok" if new else "alert")
        elif point == "mac":
            s.log(f"the MAC address behind {name} changed from {old} to {new}: two devices may answer for one address", "alert")
        elif dev_id == "neo-01" and point == "mode_auto":
            s.log(f"the master is {'in AUTO' if new else 'no longer in AUTO'}", "info" if new else "warn")
        elif dev_id == "neo-01" and point == "mains_available":
            s.log("grid back" if new else "grid lost", "info" if new else "warn")


def _console_setup() -> None:
    """Windows console: no QuickEdit (a click in the window would pause everything until Enter), colours on."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h_in = k.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h_in, ctypes.byref(mode)):
            k.SetConsoleMode(h_in, (mode.value & ~0x0040) | 0x0080)      # QuickEdit off, extended flags on
        h_out = k.GetStdHandle(-11)
        if k.GetConsoleMode(h_out, ctypes.byref(mode)):
            k.SetConsoleMode(h_out, mode.value | 0x0004)                  # ANSI colours
    except Exception:
        pass


def _colour_ok() -> bool:
    import sys
    return bool(getattr(sys.stdout, "isatty", lambda: False)())
