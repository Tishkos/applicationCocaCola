"""
Passive evidence of the SAU's timing, at no cost to the SAU: nothing here sends a packet.

Wireshark's own capture program (dumpcap) writes what the PC's network card sees for the SAU's address into a
ring of files (one per hour by default, the oldest overwritten), and around every comm fail the master logs the
files are copied to data/evidence/<date>/ so the ring never overwrites them. Wireshark's tshark then turns them
into transactions: for every Modbus request to the SAU, when the SAU's TCP acknowledged it and when the Modbus
answer came, or the exception it carried. That is the proof that the request reached the SAU and its own
software answered late.

The card sees the master <-> SAU traffic only when the switch (or the MikroTik) mirrors the SAU's port to the PC.
Without a mirror it sees this PC's own traffic to the SAU (pings, an open web page) and broadcasts; the report
says which case it is.

    dumpcap / tshark: from the Wireshark installation (C:\\Program Files\\Wireshark), found by themselves.
"""

from __future__ import annotations

import collections
import glob
import os
import platform
import re
import shutil
import socket
import statistics
import subprocess
from datetime import datetime, timedelta, timezone

from . import sitetime

_WIN = platform.system() == "Windows"
MODBUS_PORT = 502          # the SAU's Modbus TCP port; the demo uses another one, decoded as Modbus all the same


def decode_as() -> list[str]:
    """tshark decodes Modbus/TCP on 502 by itself; any other port has to be named."""
    return [] if MODBUS_PORT == 502 else ["-d", f"tcp.port=={MODBUS_PORT},mbtcp"]
_NAME = re.compile(r"_(\d{5})_(\d{14})\.pcapng?$|_(\d{14})_(\d{5})\.pcapng?$")


def find_tool(name: str) -> str | None:
    """dumpcap or tshark: on the PATH, else in the usual Wireshark folders."""
    p = shutil.which(name) or shutil.which(name + ".exe")
    if p:
        return p
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            for sub in ("Wireshark", os.path.join("Programs", "Wireshark")):
                cand = os.path.join(base, sub, name + ".exe")
                if os.path.exists(cand):
                    return cand
    if _WIN:                                                  # installed somewhere else: Wireshark records where
        try:
            import winreg
            for hive, key, value in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wireshark", "InstallDir"),
                                     (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Wireshark.exe", "Path"),
                                     (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Wireshark", "InstallDir")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        d = winreg.QueryValueEx(k, value)[0]
                    cand = os.path.join(d, name + ".exe")
                    if os.path.exists(cand):
                        return cand
                except OSError:
                    continue
        except ImportError:
            pass
    return None


def local_ip_for(host: str) -> str | None:
    """The PC's own address on the way to host. A UDP 'connect' only picks the route; no packet leaves."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _run(cmd: list[str], timeout: float = 20.0) -> str:
    try:
        kw = {"creationflags": 0x08000000} if _WIN else {}             # CREATE_NO_WINDOW: no console flashing up
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
        return (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    except Exception:
        return ""


def interface_alias(ip: str) -> str | None:
    """The adapter that holds this address: its Windows name ('Ethernet 3'), or the Linux device ('eth0')."""
    if _WIN:
        out = _run(["powershell", "-NoProfile", "-Command",
                    f"(Get-NetIPAddress -IPAddress {ip} -ErrorAction SilentlyContinue).InterfaceAlias"]).strip()
        return out.splitlines()[0].strip() if out else None
    for line in _run(["ip", "-o", "-4", "addr", "show"]).splitlines():
        parts = line.split()
        if len(parts) > 3 and parts[3].split("/")[0] == ip:
            return parts[1]
    return None


def pick_interface(dumpcap: str, host: str, wanted: str | None = None) -> tuple[str | None, str]:
    """The dumpcap interface to capture on, and why. 'wanted' (a name or number from dumpcap -D) wins; otherwise the
    adapter that holds the PC's address towards the host, matched against dumpcap's own list."""
    listing = _run([dumpcap, "-D"])
    rows = []
    for line in listing.splitlines():
        m = re.match(r"\s*(\d+)\.\s+(\S+)(?:\s+\((.*)\))?", line)
        if m:
            rows.append((m.group(1), m.group(2), m.group(3) or ""))
    if wanted:
        for num, dev, label in rows:
            if wanted in (num, dev, label):
                return num, f"{label or dev} (given)"
        return wanted, f"{wanted} (given, not in dumpcap's list; trying anyway)"
    ip = local_ip_for(host)
    if not ip:
        return None, f"no route from this PC to {host}"
    alias = interface_alias(ip)
    if not alias:
        return None, f"the PC's address towards {host} is {ip}, but no adapter name was found for it"
    for num, dev, label in rows:
        if alias in (label, dev):
            return num, f"{alias} ({ip})"
    return None, f"adapter {alias} ({ip}) is not in dumpcap's list (is Npcap installed with Wireshark?)"


def loopback_interface(dumpcap: str) -> str | None:
    """The loopback capture adapter (Npcap's 'Adapter for loopback traffic capture', or lo): for the demo only."""
    for line in _run([dumpcap, "-D"]).splitlines():
        m = re.match(r"\s*(\d+)\.\s+(\S+)(?:\s+\((.*)\))?", line)
        if m and (re.search(r"loopback", m.group(3) or "", re.I) or m.group(2) in ("lo", r"\Device\NPF_Loopback")):
            return m.group(1)
    return None


def list_interfaces(dumpcap: str) -> list[tuple[str, str]]:
    """(number, label) of every interface dumpcap offers, loopback and event tracing left out."""
    out = []
    for line in _run([dumpcap, "-D"]).splitlines():
        m = re.match(r"\s*(\d+)\.\s+(\S+)(?:\s+\((.*)\))?", line)
        if m:
            label = m.group(3) or m.group(2)
            if not re.search(r"loopback|etw|event tracing|bluetooth|^lo$|^any$", label, re.I):
                out.append((m.group(1), label))
    return out


def self_test(dumpcap: str, iface: str, host: str, master: str | None, work_dir: str, pinger,
              seconds: int = 30, tshark: str | None = None) -> dict:
    """A short capture on one interface while the host is pinged five times: does this card see the SAU?
    The capture is kept as a Wireshark file (selftest_<time>_<adapter>.pcapng in work_dir) whatever the result.
    Returns ok (5 packets or more with the host), packets, mirror (the master's Modbus requests to the host were
    in it too: True / False / None when tshark is missing) and the file."""
    os.makedirs(work_dir, exist_ok=True)
    stamp = sitetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(work_dir, f"selftest_{stamp}_{re.sub(r'[^0-9A-Za-z]+', '_', str(iface)).strip('_')}.pcapng")
    kw = {"creationflags": 0x08000000} if _WIN else {}
    try:
        proc = subprocess.Popen([dumpcap, "-i", str(iface), "-f", f"host {host}", "-a", f"duration:{int(seconds)}", "-w", path],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    except OSError as exc:
        return dict(ok=False, packets=0, mirror=None, path=path, detail=f"dumpcap did not start: {exc}")
    import time as _t
    _t.sleep(1.5)                                            # dumpcap needs a moment before it listens
    for _ in range(5):
        try:
            pinger(host)
        except Exception:
            pass
        _t.sleep(0.8)
    try:
        _, err = proc.communicate(timeout=seconds + 20)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, err = proc.communicate()
    text = (err or b"").decode("utf-8", "replace")
    m = re.search(r"Packets captured:\s*(\d+)", text)
    packets = int(m.group(1)) if m else 0
    mirror = None
    if tshark and os.path.exists(path) and master:
        rows = _tshark_rows(tshark, path, f"ip.src=={master} && ip.dst=={host} && tcp.dstport=={MODBUS_PORT}", ["frame.time_epoch"])
        mirror = bool(rows)
    detail = text.strip().splitlines()[-1] if text.strip() else ""
    return dict(ok=packets >= 5, packets=packets, mirror=mirror, path=path, detail=detail)


class Capture:
    """dumpcap in the background, writing a ring of files for one host. Restarted by the session if it dies."""

    def __init__(self, dumpcap: str, interface: str, host: str, ring_dir: str, file_s: int = 3600,
                 file_mb: int = 100, files: int = 48, log=print):
        self.dumpcap, self.interface, self.host, self.ring_dir = dumpcap, interface, host, ring_dir
        self.file_s, self.file_mb, self.files, self.log = file_s, file_mb, files, log
        self.proc: subprocess.Popen | None = None
        self.starts = 0
        os.makedirs(ring_dir, exist_ok=True)

    def command(self) -> list[str]:
        return [self.dumpcap, "-i", str(self.interface), "-f", f"host {self.host}",
                "-b", f"duration:{int(self.file_s)}", "-b", f"filesize:{int(self.file_mb) * 1000}",
                "-b", f"files:{int(self.files)}", "-w", os.path.join(self.ring_dir, "sau.pcapng"), "-q"]

    def start(self) -> bool:
        kw = {"creationflags": 0x08000000} if _WIN else {}
        err = open(os.path.join(self.ring_dir, "dumpcap.log"), "a", encoding="utf-8")
        try:
            self.proc = subprocess.Popen(self.command(), stdout=err, stderr=err, **kw)
        except OSError as exc:
            self.log(f"capture: dumpcap did not start: {exc}")
            return False
        finally:
            err.close()
        self.starts += 1
        try:
            with open(os.path.join(self.ring_dir, "dumpcap.pid"), "w") as f:
                f.write(str(self.proc.pid))
        except OSError:
            pass
        return True

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ---- the ring and the evidence -------------------------------------------------------------------------
def ring_files(ring_dir: str) -> list[dict]:
    """The ring's files, oldest first: path, number, start (from the name, the PC's local time) and end (last write)."""
    out = []
    for p in glob.glob(os.path.join(ring_dir, "*.pcapng")) + glob.glob(os.path.join(ring_dir, "*.pcap")):
        m = _NAME.search(os.path.basename(p))
        if not m:
            continue
        num, stamp = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        start = datetime.strptime(stamp, "%Y%m%d%H%M%S").astimezone().astimezone(timezone.utc)
        end = datetime.fromtimestamp(os.path.getmtime(p), timezone.utc)
        out.append(dict(path=p, num=int(num), start=start, end=max(end, start)))
    return sorted(out, key=lambda f: f["num"])


def keep(ring_dir: str, evidence_dir: str, start: datetime, end: datetime, margin_s: float = 60.0) -> tuple[list[str], bool]:
    """Copy the ring files that cover [start - margin, end + margin] to evidence_dir. Returns (copied, complete):
    complete is False while the newest of those files is still being written (copy again later)."""
    files = ring_files(ring_dir)
    lo, hi = start - timedelta(seconds=margin_s), end + timedelta(seconds=margin_s)
    hit = [f for f in files if f["start"] <= hi and f["end"] >= lo]
    newest = files[-1]["path"] if files else None
    os.makedirs(evidence_dir, exist_ok=True)
    copied = []
    for f in hit:
        dst = os.path.join(evidence_dir, os.path.basename(f["path"]))
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(f["path"]):
            shutil.copy2(f["path"], dst)
            copied.append(dst)
    complete = bool(hit) and all(f["path"] != newest for f in hit)
    return copied, complete


# ---- reading the evidence ----------------------------------------------------------------------------------
_TX_FIELDS = ["frame.time_epoch", "ip.src", "ip.dst", "tcp.srcport", "tcp.dstport", "tcp.stream", "tcp.len",
              "mbtcp.trans_id", "modbus.func_code", "modbus.exception_code", "tcp.seq", "tcp.ack"]
_OTHER_FIELDS = ["frame.time_epoch", "ip.src", "ip.dst", "ip.proto", "tcp.srcport", "tcp.dstport", "udp.dstport", "icmp.type"]


def _tshark_rows(tshark: str, path: str, display: str, fields: list[str]) -> list[list[str]]:
    cmd = [tshark, "-n", *decode_as(), "-r", path, "-Y", display, "-T", "fields", "-E", "separator=\t", "-E", "occurrence=a",
           "-E", "aggregator=|"]
    for f in fields:
        cmd += ["-e", f]
    out = _run(cmd, timeout=600)
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == len(fields) and parts[0][:1].isdigit():
            rows.append(parts)
    return rows


def transactions(paths: list[str], sau: str, tshark: str) -> list[dict]:
    """Every Modbus request to the SAU in the files: t (request), client, port, tid, fc, ack_ms (first packet back
    from the SAU on that connection, a bare TCP ACK counts), ans_ms (the Modbus answer with that transaction id),
    exc (exception code or None). A request never answered in the file has ans_ms None. ack_ms is the first packet
    from the SAU on that connection whose TCP acknowledgement number covers the request: the SAU's network stack
    had it, whatever its Modbus program did next."""
    txs: list[dict] = []
    for i, path in enumerate(sorted(paths)):
        rows = _tshark_rows(tshark, path, f"ip.addr=={sau} && tcp.port=={MODBUS_PORT}", _TX_FIELDS)
        pending: dict[tuple, dict[str, dict]] = collections.defaultdict(dict)
        last_t = float(rows[-1][0]) if rows else 0.0
        for r in rows:
            t = float(r[0])
            src, dst, sport, dport, stream = r[1], r[2], r[3], r[4], (i, r[5])
            tids = [x for x in r[7].split("|") if x]
            fcs = [x for x in r[8].split("|") if x]
            excs = [x for x in r[9].split("|") if x]
            seq = int(r[10]) if r[10].isdigit() else None
            ackn = int(r[11]) if r[11].isdigit() else None
            if dst == sau and dport == str(MODBUS_PORT):
                nxt = seq + int(r[6]) if seq is not None and r[6].isdigit() else None
                for k, tid in enumerate(tids):
                    if tid not in pending[stream]:                       # a retransmission keeps the first time
                        tx = dict(t=t, client=src, port=sport, tid=tid, fc=int(fcs[k]) if k < len(fcs) and fcs[k].isdigit() else None,
                                  ack_ms=None, ans_ms=None, exc=None, nxt=nxt)
                        pending[stream][tid] = tx
                        txs.append(tx)
            elif src == sau and sport == str(MODBUS_PORT):
                for tx in pending[stream].values():
                    covers = ackn is None or tx["nxt"] is None or ackn >= tx["nxt"]
                    if tx["ack_ms"] is None and t >= tx["t"] and covers:
                        tx["ack_ms"] = (t - tx["t"]) * 1000
                for k, tid in enumerate(tids):
                    tx = pending[stream].pop(tid, None)
                    if tx is not None:
                        tx["ans_ms"] = (t - tx["t"]) * 1000
                        if excs:
                            e = excs[min(k, len(excs) - 1)]
                            tx["exc"] = int(e) if e.isdigit() else None
        for waiting in pending.values():                                 # the file ends before the answer could come
            for tx in waiting.values():
                tx["cut"] = last_t - tx["t"] < 5.0
    return sorted(txs, key=lambda x: x["t"])


def other_traffic(paths: list[str], sau: str, tshark: str) -> list[dict]:
    """Everything else to or from the SAU: ping, web page, other ports. t, peer, what."""
    out = []
    for path in sorted(paths):
        for r in _tshark_rows(tshark, path, f"ip.addr=={sau} && !(tcp.port=={MODBUS_PORT})", _OTHER_FIELDS):
            t, src, dst, proto = float(r[0]), r[1], r[2], r[3]
            peer = dst if src == sau else src
            if proto == "1":
                what = "ping" if r[7] in ("0", "8") else "icmp"
            elif proto == "6":
                port = r[5] if src != sau else r[4]
                what = {"80": "web page (80)", "8080": "web (8080)", "22": "ssh (22)", "21": "ftp (21)", "2404": "iec 104 (2404)"}.get(port, f"tcp {port}")
            elif proto == "17":
                what = f"udp {r[6]}"
            else:
                what = f"ip proto {proto}"
            out.append(dict(t=t, peer=peer, what=what))
    return out


def _utc(t: float) -> datetime:
    return datetime.fromtimestamp(t, timezone.utc)


def window_verdict(txs: list[dict], start: datetime, end: datetime | None, master: str, before_s: float = 12.0) -> str:
    """What the capture shows for the master's requests in the seconds before and during one comm fail."""
    lo = (start - timedelta(seconds=before_s)).timestamp()
    hi = ((end or start + timedelta(seconds=30)) + timedelta(seconds=2)).timestamp()
    win = [x for x in txs if lo <= x["t"] <= hi and x["client"] == master]
    if not win:
        return "no master requests in the capture at that time (no mirror of the SAU's port, or the capture was not running)"
    late = [x for x in win if x["ans_ms"] is not None and x["ans_ms"] >= 1000]
    never = [x for x in win if x["ans_ms"] is None and not x.get("cut")]
    busy = [x for x in win if x["exc"] == 6 and (x["ans_ms"] or 0) >= 500]
    bad = late + never + [x for x in busy if x not in late]
    early = [x for x in bad if x["ack_ms"] is not None and (x["ans_ms"] is None or x["ans_ms"] - x["ack_ms"] >= 200)]
    acked = [x for x in bad if x["ack_ms"] is not None]
    worst = max([x["ans_ms"] for x in win if x["ans_ms"] is not None] or [0])
    if not (late or never or busy):
        return (f"{len(win)} master requests, all answered, slowest {worst:.0f} ms: the SAU was not late on the wire; "
                "look at the master's own module")
    first = min(late + never + busy, key=lambda x: x["t"])
    parts = [f"{len(win)} master requests; from {sitetime.local(_utc(first['t'])).strftime('%H:%M:%S.%f')[:-3]} ({sitetime.label()})"]
    if late:
        parts.append(f"{len(late)} answered after 1 s or more (slowest {worst / 1000:.2f} s)")
    if busy:
        parts.append(f"{len(busy)} answered late with exception 06 Server Device Busy")
    if never:
        parts.append(f"{len(never)} never answered")
    if early:
        parts.append(f"the SAU's TCP acknowledged {len(early)} of them within {max(x['ack_ms'] for x in early):.0f} ms, long before "
                     "the Modbus answer: the requests reached the SAU and its own software answered late")
    elif acked:
        parts.append("the SAU's TCP acknowledged them only together with the late answer (no separate early acknowledgement in the file)")
    else:
        parts.append("no TCP acknowledgement from the SAU for those requests either: the box or the path did not answer")
    return "; ".join(parts)


def report_section(evidence_paths: list[str], fails: list[dict], sau: str, master: str, tshark: str | None) -> list[str]:
    """Markdown lines: the capture's own numbers and one verdict per comm fail."""
    L = ["## From the capture (passive: Wireshark's files, nothing sent)"]
    if not evidence_paths:
        L += ["No capture files kept for this day (no comm fail while the capture ran, or no capture).", ""]
        return L
    if not tshark:
        L += [f"{len(evidence_paths)} capture files kept, but tshark (Wireshark) was not found to read them. "
              "Open them in Wireshark with the filter modbus.exception_code == 6, or send them.", ""]
        return L
    txs = transactions(evidence_paths, sau, tshark)
    oth = other_traffic(evidence_paths, sau, tshark)
    L.append(f"Files: {len(evidence_paths)} ({', '.join(os.path.basename(p) for p in sorted(evidence_paths)[:6])}"
             + (", ..." if len(evidence_paths) > 6 else "") + ").")
    clients = collections.Counter(x["client"] for x in txs)
    if not txs:
        L.append("No Modbus requests to the SAU in these files: the switch does not mirror the SAU's port to this PC, "
                 "so only this PC's own traffic and broadcasts are in them.")
    else:
        mirror = master in clients
        L.append("Modbus clients of the SAU in the files: " + ", ".join(f"{c} ({n} requests)" for c, n in clients.most_common())
                 + ("." if mirror else ". The master is not among them: no mirror of the SAU's port."))
        ans = [x["ans_ms"] for x in txs if x["ans_ms"] is not None]
        if ans:
            L.append(f"Answer time: median {statistics.median(ans):.0f} ms, max {max(ans):.0f} ms; "
                     f"1 s or slower: {sum(1 for a in ans if a >= 1000)}; never answered: {sum(1 for x in txs if x['ans_ms'] is None and not x.get('cut'))}; "
                     f"exceptions: " + (", ".join(f"{k} x code {c}" for c, k in collections.Counter(x['exc'] for x in txs if x['exc']).most_common()) or "none") + ".")
        fast_exc = sum(1 for x in txs if x["exc"] and (x["ans_ms"] or 0) < 500)
        if fast_exc:
            L.append(f"{fast_exc} exceptions came back at once (under 0.5 s): a refused request, not a busy SAU; they are not counted as late.")
    if oth:
        peers = collections.Counter((o["peer"], o["what"]) for o in oth)
        L.append("Other traffic with the SAU: " + ", ".join(f"{p} {w} ({n})" for (p, w), n in peers.most_common(8)) + ".")
    if fails:
        L.append("")
        L.append("Per master comm fail (the 12 s before it and during it):")
        for f in fails[:40]:
            L.append(f"- {sitetime.hms(f['start'])} {f['session']}: {window_verdict(txs, f['start'], f['end'], master)}")
            others = [o for o in oth if f["start"].timestamp() - 12 <= o["t"] <= (f["end"] or f["start"] + timedelta(seconds=30)).timestamp()]
            if others:
                L.append("  also at that time: " + ", ".join(f"{p} {w} ({n})" for (p, w), n in collections.Counter((o['peer'], o['what']) for o in others).most_common(4)))
    L.append("")
    return L


def stop_stale(ring_dir: str, log=print) -> None:
    """A dumpcap left over from an earlier session would fight the new one for the ring: stop it (only if the pid
    in our own pid file still belongs to a dumpcap)."""
    pid_file = os.path.join(ring_dir, "dumpcap.pid")
    try:
        pid = int(open(pid_file).read().strip())
    except (OSError, ValueError):
        return
    if _WIN:
        out = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"])
        if "dumpcap" in out.lower():
            _run(["taskkill", "/PID", str(pid), "/F"])
            log(f"capture: stopped the dumpcap left from an earlier session (pid {pid})")
    else:
        try:
            with open(f"/proc/{pid}/comm") as f:
                if "dumpcap" in f.read():
                    os.kill(pid, 15)
                    log(f"capture: stopped the dumpcap left from an earlier session (pid {pid})")
        except OSError:
            pass
    try:
        os.remove(pid_file)
    except OSError:
        pass
