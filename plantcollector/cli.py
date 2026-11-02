"""
plantcollector command line.

  python -m plantcollector.cli --config config/site.yaml run           collector + dashboard
  python -m plantcollector.cli --config config/site.plant.yaml check   one poll, print every point
  python -m plantcollector.cli --config ... export --hours 24 --out data.xlsx
  python -m plantcollector.cli --config ... report --date 2026-10-12 --out report.xlsx
  python -m plantcollector.cli --config config/site.plant.yaml trace          1 s / 2 s trace to data/trace
  python -m plantcollector.cli --config config/site.plant.yaml probe          who answers on Modbus TCP
  python -m plantcollector.cli --config config/site.plant.yaml session        the whole measuring session, one click
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone

from .core import Collector, run_loop


def _collector(a, *, seed: float = 0.0, fresh: bool = False, initial_poll: bool = True) -> Collector:
    return Collector(a.config, a.data, seed_hours=seed, fresh=fresh, initial_poll=initial_poll)


def _log_to_file(path: str) -> None:
    """Background runs have no console: send output to a small rotating log file."""
    import logging
    from logging.handlers import RotatingFileHandler
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    h = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)

    class _Stream:
        def write(self, m):
            m = m.rstrip()
            if m:
                logging.getLogger("collector").info(m)
        def flush(self): pass
    sys.stdout = sys.stderr = _Stream()


def _port_free(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sk.bind(("" if host == "0.0.0.0" else host, port))
            return True
        except OSError:
            return False


def _choose_port(host: str, wanted: int, wait_s: int = 15, tries: int = 10) -> int:
    """Wait a little for a previous instance to release the wanted port; if something else
    holds it, step to the next free one. The chosen address is written to data/dashboard.url."""
    for i in range(wait_s):
        if _port_free(host, wanted):
            return wanted
        if i == 0:
            print(f"[collector] port {wanted} is busy, waiting for it to free up...")
        time.sleep(1)
    for p in range(wanted + 1, wanted + 1 + tries):
        if _port_free(host, p):
            print(f"[collector] port {wanted} is taken by another program; using port {p} instead")
            return p
    print(f"[collector] ports {wanted}-{wanted + tries} all busy; trying {wanted} anyway")
    return wanted


def cmd_run(a) -> int:
    import uvicorn
    from .api import create_app

    if a.log:
        _log_to_file(a.log)
    try:
        c = _collector(a, seed=a.seed_hours if not a.no_seed else 0.0, fresh=True, initial_poll=False)
    except Exception as exc:
        print(f"[collector] cannot start: {exc}")
        raise
    forced = a.interval                       # --interval on the command line wins over the dashboard setting
    if a.interval is None:
        a.interval = float(c.registry.site.poll_interval_s)
    if c.simulated:
        print(f"[collector] DEMO mode: simulated plant, {a.seed_hours:.0f} h of history generated")
    else:
        n = len(c.registry.devices)
        reqs = sum(len(getattr(ad, "_blocks", [])) or 1 for ad in c.adapters.values())
        print(f"[collector] READ-ONLY polling of {n} devices every {a.interval:.0f} s "
              f"({reqs} Modbus requests per cycle); data in {c.db_path}")
    stop = threading.Event()
    t = threading.Thread(target=run_loop, args=(c, forced, stop), daemon=True, name="poller")
    t.start()
    # Modbus publisher for the Turkey platform: its port must stay what we told them, so if it
    # cannot start (port taken, VPN address not up yet) it is retried every minute, not moved.
    publisher = None
    from .publish import ModbusPublisher
    c.publisher_error = None

    def start_publisher() -> bool:
        nonlocal publisher
        try:
            pub = ModbusPublisher.from_config(c)
            if pub is None:
                return True
            pub.start()
            publisher = pub
            c.publisher = pub
            c.publisher_error = None
            print(f"[collector] Modbus TCP publisher for the Turkey platform: {pub.bind}:{pub.port} unit {pub.unit_id}, {len(pub.map)} tags, read-only")
            return True
        except Exception as exc:
            c.publisher_error = str(exc)
            print(f"[collector] Modbus publisher not started ({exc}); retrying every minute")
            return False

    c.publisher = None
    if not start_publisher():
        def retry():
            while not stop.is_set() and publisher is None:
                stop.wait(60)
                if not stop.is_set():
                    start_publisher()
        threading.Thread(target=retry, daemon=True, name="publisher-retry").start()

    a.port = _choose_port(a.host, a.port)
    url = f"http://localhost:{a.port}"
    try:
        with open(os.path.join(a.data, "dashboard.url"), "w") as f:
            f.write(url + "\n")
    except OSError:
        pass
    print(f"[collector] dashboard: {url}   api: {url}/api/docs   database: {c.db_path}")
    if a.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(create_app(c), host=a.host, port=a.port, log_level="warning", log_config=None)
    finally:
        stop.set()
        if publisher:
            publisher.stop()
        c.close()
    return 0


def cmd_check(a) -> int:
    """One poll of every device, printed as a table. The commissioning tool for the plant PC."""
    c = _collector(a)
    bad = 0
    for d in c.registry.devices.values():
        rows = c.store.latest(d.id)
        conn = d.connection
        where = f"{conn.get('host', '')}:{conn.get('port', '')} unit {conn.get('unit_id', '')}" if d.adapter == "modbus_tcp" else d.adapter
        print(f"\n== {d.id}  ({d.kind}, {d.model})  {where}")
        for p in d.points:
            r = rows.get(p.id)
            if r is None:
                print(f"   {p.id:<28} --        (not read)")
                continue
            q = r.quality.value
            if q != "good":
                bad += 1
            val = "" if r.value is None else (f"{r.value:.3f}" if isinstance(r.value, float) else str(r.value))
            addr = p.raw.get("address", "")
            print(f"   {p.id:<28} {val:>12} {p.unit:<6} {q:<6} addr={addr!s:<6} {r.error or ''}")
    print(f"\n{sum(len(d.points) for d in c.registry.devices.values()) - bad} good, {bad} not good")
    c.close()
    return 0 if bad == 0 else 1


def _range(a, c: Collector):
    until = c.now()
    since = until - timedelta(hours=a.hours)
    if a.frm:
        since = datetime.fromisoformat(a.frm).replace(tzinfo=timezone.utc)
    if a.to:
        until = datetime.fromisoformat(a.to).replace(tzinfo=timezone.utc)
    return since, until


def cmd_export(a) -> int:
    from . import export
    c = _collector(a)
    since, until = _range(a, c)
    headers, rows = export.wide_table(c, since, until, a.step)
    out = a.out or f"export_{since.strftime('%Y%m%d%H%M')}_{until.strftime('%Y%m%d%H%M')}.{a.format}"
    data = export.to_csv(headers, rows) if a.format == "csv" else export.to_xlsx(c, headers, rows, since, until, a.step)
    with open(out, "wb") as f:
        f.write(data)
    print(f"wrote {out}: {len(rows)} rows x {len(headers)} columns")
    return 0


def cmd_report(a) -> int:
    from . import export
    c = _collector(a)
    day = datetime.fromisoformat(a.date).replace(tzinfo=timezone.utc) if a.date else c.now()
    out = a.out or f"daily_{day.strftime('%Y-%m-%d')}.xlsx"
    with open(out, "wb") as f:
        f.write(export.daily_report_xlsx(c, day))
    print(f"wrote {out}")
    return 0


def cmd_trace(a) -> int:
    """Fast read-only trace of a few devices to files in data/trace (see trace.py)."""
    from .registry import load_registry
    from .trace import Trace
    reg = load_registry(a.config)
    every: dict[str, float] = {}
    for item in a.every or []:
        dev, _, sec = item.partition("=")
        every[dev.strip()] = float(sec or 1)
    if not every:
        for dev, sec in (("neo-01", 1.0), ("pv-array", 5.0)):      # gentle on the SAU by default; --every pv-array=2 when the fail count allows
            if dev in reg.devices:
                every[dev] = sec
    if not every:
        print("nothing to trace: give --every <device>=<seconds>")
        return 2
    out = a.out or os.path.join(a.data, "trace")
    watch: dict[str, float] = {}
    for item in a.watch or []:
        dev, _, sec = item.partition("=")
        watch[dev.strip()] = float(sec or 1)
    if watch:
        for dev in watch:
            every.setdefault(dev, 5.0)
    tr = Trace(reg, out, every, connect_test_s=(a.connect_test or None), watch=watch)
    try:
        tr.run(duration_s=a.hours * 3600 if a.hours else None)
    except KeyboardInterrupt:
        tr.close()
        print("\n[trace] stopped")
    print("[trace] " + ", ".join(f"{d}: {tr.rows[d]} rows, {tr.failed[d]} failed reads" for d in every)
          + f", {tr.events_logged} events" + (f", watch {tr.watch_rows} rows, {tr.stalls} stalls" if watch else "") + f", files in {out}")
    return 0


def cmd_probe(a) -> int:
    """Which hosts answer on Modbus TCP at all. One read of one register per target, nothing else."""
    from .registry import load_registry
    from .trace import probe
    reg = load_registry(a.config)
    targets: list[tuple[str, int, int]] = []
    for item in a.targets or []:
        parts = item.split(":")
        host = parts[0]
        port = int(parts[1]) if len(parts) > 1 and parts[1] else 502
        unit = int(parts[2]) if len(parts) > 2 and parts[2] else 1
        targets.append((host, port, unit))
    if not targets:
        for d in reg.devices.values():
            c = d.connection
            if d.adapter == "modbus_tcp" and "host" in c:
                t = (c["host"], int(c.get("port", 502)), int(c.get("unit_id", 1)))
                if t not in targets:
                    targets.append(t)
        for h in (reg.extra.get("probe") or {}).get("hosts", []) or []:
            t = (str(h), 502, 1)
            if t not in targets:
                targets.append(t)
    rows = probe(targets, address=a.address, timeout=a.timeout)
    print(f"{'host':<22}{'unit':>5}  {'tcp':<10}{'ms':>8}  modbus")
    for r in rows:
        ms = "" if r["ms"] is None else f"{r['ms']:.0f}"
        print(f"{r['host'] + ':' + str(r['port']):<22}{r['unit']:>5}  {r['tcp'][:9]:<10}{ms:>8}  {r['modbus']}")
    return 0


def cmd_session(a) -> int:
    """The whole measuring session, one click (see session.py)."""
    from .session import Session
    s = Session(a.config, a.data, hours=a.hours, phase1_min=a.phase1_min, sau_s=a.sau_s, long_run=not a.no_long, zip_hour=a.zip_hour, watch_s=a.watch_s,
                bundles_dir=a.bundles, mode=a.mode, capture=not a.no_capture, capture_if=a.capture_if,
                ring_files=a.ring_files, ring_file_s=int(a.ring_hours * 3600))
    s.monitor_on = not a.no_monitor
    s.open_browser = not a.no_browser
    try:
        s.run()
    except KeyboardInterrupt:
        s.stop.set()
        print("\n[session] stopped")
    return 0


def cmd_session_demo(a) -> int:
    """DEMO-SESSION.bat: the SAU watch on a simulated plant (see demo_watch.py)."""
    from .demo_watch import run
    return run(os.path.dirname(os.path.abspath(a.data)), open_browser=not a.no_browser)


def cmd_bundle(a) -> int:
    """Report and zip one day's trace files (what the session does by itself every day)."""
    from .session import Session
    s = Session(a.config, a.data, bundles_dir=a.bundles, long_run=False)
    from . import sitetime
    date = a.date or sitetime.day(datetime.now(timezone.utc))
    print(s.step_report_and_bundle(date=date))
    return 0


def cmd_report_trace(a) -> int:
    from .report import summarize
    from .registry import load_registry
    from . import sitetime
    load_registry(a.config)                                  # sets the plant's time zone
    date = a.date or sitetime.day(datetime.now(timezone.utc))
    print(summarize(a.trace_dir or os.path.join(a.data, "trace"), date))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="plantcollector", description="read-only plant data collector")
    ap.add_argument("--config", default=os.path.join("config", "site.yaml"))
    ap.add_argument("--data", default="data", help="folder for the database")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="collector + dashboard")
    r.add_argument("--host", default="0.0.0.0")
    r.add_argument("--port", type=int, default=8080)
    r.add_argument("--interval", type=float, default=None, help="poll interval in seconds (default: site.poll_interval_s from the config)")
    r.add_argument("--open", action="store_true", help="open the dashboard in the browser")
    r.add_argument("--seed-hours", type=float, default=72.0, help="demo only: hours of history to generate")
    r.add_argument("--no-seed", action="store_true")
    r.add_argument("--log", help="write output to this file instead of the console")
    r.set_defaults(fn=cmd_run)

    k = sub.add_parser("check", help="poll once and print every point")
    k.set_defaults(fn=cmd_check)

    e = sub.add_parser("export", help="write the wide table to csv/xlsx")
    e.add_argument("--hours", type=float, default=24)
    e.add_argument("--from", dest="frm")
    e.add_argument("--to")
    e.add_argument("--step", type=int, default=300)
    e.add_argument("--format", choices=["csv", "xlsx"], default="xlsx")
    e.add_argument("--out")
    e.set_defaults(fn=cmd_export)

    p = sub.add_parser("report", help="daily report workbook")
    p.add_argument("--date")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_report)

    t = sub.add_parser("trace", help="fast read-only trace of a few devices to files (event studies)")
    t.add_argument("--every", action="append", metavar="DEVICE=SECONDS",
                   help="device id and read interval, repeatable (default: neo-01=1 pv-array=5)")
    t.add_argument("--hours", type=float, default=None, help="stop after this many hours (default: until Ctrl+C)")
    t.add_argument("--connect-test", type=float, default=60.0, help="seconds between fresh-connection tests (0 = off)")
    t.add_argument("--watch", action="append", metavar="DEVICE=SECONDS",
                   help="stall catcher: ping the host and read one register on the device's connection this often (e.g. pv-array=1)")
    t.add_argument("--out", help="folder for the files (default: <data>/trace)")
    t.set_defaults(fn=cmd_trace)

    ss = sub.add_parser("session", help="the whole measuring session, one click: probe, check, trace in phases, report, bundle, then the long run")
    ss.add_argument("--hours", type=float, default=2.0)
    ss.add_argument("--phase1-min", type=float, default=30.0, help="minutes of master-only trace before the SAU is polled")
    ss.add_argument("--sau-s", type=float, default=5.0, help="SAU read interval in the session")
    ss.add_argument("--watch-s", type=float, default=1.0, help="stall catcher interval on the SAU in phase 2 and the long run (0 = off)")
    ss.add_argument("--zip-hour", type=int, default=16, help="hour (Erbil time) of the daily report and bundle in the long run")
    ss.add_argument("--no-long", action="store_true", help="stop after the session instead of continuing with the long run")
    ss.add_argument("--bundles", help="folder for the zips (default: bundles/ next to data/)")
    ss.add_argument("--mode", choices=["passive", "active"], default="passive",
                    help="passive (default): the SAU is only pinged and captured, never read - no load on it; active: the old phases with SAU reads")
    ss.add_argument("--no-capture", action="store_true", help="passive: do not start Wireshark's dumpcap")
    ss.add_argument("--capture-if", help="passive: the capture interface (a number or name from 'dumpcap -D'); default: the adapter towards the SAU")
    ss.add_argument("--ring-files", type=int, default=48, help="passive: capture files kept in the ring")
    ss.add_argument("--ring-hours", type=float, default=1.0, help="passive: hours per capture file")
    ss.add_argument("--no-monitor", action="store_true", help="passive: no live page")
    ss.add_argument("--no-browser", action="store_true", help="passive: start the live page but do not open a browser")
    ss.set_defaults(fn=cmd_session)

    sd = sub.add_parser("session-demo", help="the SAU watch on a simulated plant: comm fails, a busy SAU, lost pings - nothing real is touched")
    sd.add_argument("--no-browser", action="store_true")
    sd.set_defaults(fn=cmd_session_demo)

    bd = sub.add_parser("bundle", help="report and zip one day's trace files")
    bd.add_argument("--date", help="YYYY-MM-DD (default: today, UTC)")
    bd.add_argument("--bundles")
    bd.set_defaults(fn=cmd_bundle)

    rt = sub.add_parser("report-trace", help="print the report of one day's trace files")
    rt.add_argument("--date")
    rt.add_argument("--trace-dir")
    rt.set_defaults(fn=cmd_report_trace)

    q = sub.add_parser("probe", help="which hosts answer on Modbus TCP (one read of one register each)")
    q.add_argument("--targets", action="extend", nargs="+", metavar="HOST[:PORT[:UNIT]]",
                   help="repeatable; default: every Modbus device in the config plus probe.hosts")
    q.add_argument("--address", type=int, default=0, help="holding register to read (default 0)")
    q.add_argument("--timeout", type=float, default=3.0)
    q.set_defaults(fn=cmd_probe)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
