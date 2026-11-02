"""
Reads a packet capture (pcap / pcapng, e.g. from Wireshark on the plant PC or a MikroTik sniffer stream) and
turns the Modbus TCP traffic into the numbers that matter for the SAU question:

  - every client connection to port 502: transactions, latency median / p95 / max, exception replies
  - the slow transactions (> 0.5 s) and the exceptions, with the time, function code, address
  - the master's AP setpoint writes (FC16 at 2100, 0.1 kW) next to the PV power it reads (FC3 at 1000), as a
    time series: this shows the request collapsing after a stall, and the ramp back
  - the request mix per client (function, start address, quantity) = the real polling pattern of each session
  - who else talks to the SAU (web page, downloads), per second around each stall

    python tools/pcap_modbus.py capture.pcapng [--tz 3] [--sau 192.168.0.128] [--master 192.168.0.112]

Needs scapy (pip install scapy). Read-only, works on a file, touches no device.
"""

from __future__ import annotations

import argparse
import collections
import statistics
import struct
from datetime import datetime, timedelta, timezone


def read_packets(path):
    from scapy.all import IP, TCP, PcapNgReader, PcapReader  # noqa: WPS433

    try:
        rd = PcapNgReader(path)
    except Exception:  # noqa: BLE001
        rd = PcapReader(path)
    for pkt in rd:
        if IP in pkt and TCP in pkt:
            ip, tcp = pkt[IP], pkt[TCP]
            yield float(pkt.time), ip.src, ip.dst, int(tcp.sport), int(tcp.dport), str(tcp.flags), bytes(tcp.payload), len(bytes(pkt))


def mbap_frames(payload: bytes):
    off = 0
    while off + 8 <= len(payload):
        tid, pid, ln, uid = struct.unpack(">HHHB", payload[off:off + 7])
        if pid != 0 or ln < 2 or off + 6 + ln > len(payload):
            return
        fc = payload[off + 7]
        yield tid, uid, fc, payload[off + 8:off + 6 + ln]
        off += 6 + ln


def analyse(path: str, tz_hours: float = 3.0, sau: str | None = None, master: str | None = None, slow_s: float = 0.5):
    tz = timezone(timedelta(hours=tz_hours))
    ts = lambda t: datetime.fromtimestamp(t, tz).strftime("%H:%M:%S.%f")[:-3]  # noqa: E731
    pending: dict = {}
    done: list = []
    reqs = collections.Counter()
    per_sec = collections.defaultdict(collections.Counter)
    t0 = t1 = None
    for t, src, dst, sport, dport, flags, payload, size in read_packets(path):
        t0 = t if t0 is None else t0
        t1 = t
        sec = datetime.fromtimestamp(t, tz).strftime("%H:%M:%S")
        if dport == 502:
            kind = f"modbus req {src}"
        elif sport == 502:
            kind = f"modbus rsp to {dst}"
        elif dport in (80, 8080):
            kind = f"web req {src}"
        elif sport in (80, 8080):
            kind = f"web data to {dst}"
        else:
            kind = "other"
        per_sec[sec][kind] += size
        if not payload or 502 not in (sport, dport):
            continue
        for tid, uid, fc, body in mbap_frames(payload):
            if dport == 502:
                addr = qty = None
                if fc in (1, 2, 3, 4, 5, 6, 15, 16) and len(body) >= 4:
                    addr, qty = struct.unpack(">HH", body[:4])
                pending[(src, sport, dst, tid)] = (t, fc, addr, qty, body)
                reqs[(src, sport, dst, fc, addr, qty)] += 1
            else:
                key = (dst, dport, src, tid)
                if key in pending:
                    tr, fcq, addr, qty, bodyq = pending.pop(key)
                    exc = body[0] if fc & 0x80 else None
                    done.append(dict(t=tr, client=dst, port=dport, server=src, fc=fcq, addr=addr, qty=qty, lat=t - tr, exc=exc, req=bodyq, rsp=body))
    if t0 is None:
        print("no TCP packets")
        return
    print(f"capture {datetime.fromtimestamp(t0, tz)} -> {datetime.fromtimestamp(t1, tz)} (UTC{tz_hours:+g}), {len(done)} Modbus transactions, {len(pending)} unanswered")

    print("\nRequest mix per client session (count, client:port -> server, fc, start, qty):")
    for k, v in sorted(reqs.items(), key=lambda kv: (kv[0][0], kv[0][1], -kv[1])):
        print(f"  {v:5d}  {k[0]}:{k[1]} -> {k[2]}  fc{k[3]} @{k[4]} x{k[5]}")

    by_conn = collections.defaultdict(list)
    for d in done:
        by_conn[(d["client"], d["port"], d["server"])].append(d)
    slow_times = []
    for k, rows in sorted(by_conn.items()):
        lat = sorted(r["lat"] for r in rows)
        excs = [r for r in rows if r["exc"] is not None]
        print(f"\n{k[0]}:{k[1]} -> {k[2]}: {len(rows)} transactions, latency median {statistics.median(lat)*1000:.1f} ms, "
              f"p95 {lat[int(0.95 * (len(lat) - 1))]*1000:.0f} ms, max {lat[-1]*1000:.0f} ms, exception replies {len(excs)}")
        exc_kinds = collections.Counter((r["fc"], r["addr"], r["exc"]) for r in excs)
        for (fc, addr, exc), n in exc_kinds.most_common(5):
            print(f"   exception {exc} on fc{fc} @{addr}: {n} times" + (" (every request: the SAU refuses this item)" if n >= 0.9 * sum(1 for r in rows if r['fc'] == fc and r['addr'] == addr) else ""))
        for r in sorted((r for r in rows if r["lat"] > slow_s), key=lambda r: r["t"]):
            print(f"   slow {ts(r['t'])} fc{r['fc']} @{r['addr']} x{r['qty']}: {r['lat']*1000:.0f} ms" + (f" exception {r['exc']}" if r["exc"] is not None else ""))
            slow_times.append(r["t"])
    for key, (tr, fc, addr, qty, body) in list(pending.items())[:10]:
        print(f"   unanswered {ts(tr)} {key[0]}:{key[1]} fc{fc} @{addr}")

    if master and sau:
        print(f"\nAP setpoint written by {master} (fc16 @2100, x0.1 kW) and PV power read (fc3 @1000):")
        series = []
        for d in sorted(done, key=lambda d: d["t"]):
            if d["client"] != master or d["server"] != sau:
                continue
            if d["fc"] == 16 and d["addr"] == 2100 and len(d["req"]) >= 9:
                hi, lo = struct.unpack(">HH", d["req"][5:9])
                series.append((d["t"], "SET ", (hi << 16 | lo) / 10.0))
            elif d["fc"] == 3 and d["addr"] == 1000 and d["exc"] is None and len(d["rsp"]) >= 5:
                hi, lo = struct.unpack(">HH", d["rsp"][1:5])
                v = hi << 16 | lo
                v = v - (1 << 32) if v & 0x80000000 else v
                series.append((d["t"], "READ", v / 10.0))
        last = None
        shown = 0
        for t, kind, v in series:
            near_stall = any(abs(t - s) < 45 for s in slow_times)
            changed = last is None or abs(v - last) > 20
            if near_stall or changed or shown < 6:
                print(f"  {ts(t)} {kind} {v:8.1f} kW")
                shown += 1
            last = v if kind == "SET " else last
        if not series:
            print("  none in this capture")

    if slow_times:
        print("\nTraffic per second around the stalls (bytes per kind):")
        for s in slow_times[:5]:
            lo, hi = datetime.fromtimestamp(s - 6, tz), datetime.fromtimestamp(s + 6, tz)
            for sec in sorted(per_sec):
                if lo.strftime("%H:%M:%S") <= sec <= hi.strftime("%H:%M:%S"):
                    print("  ", sec, dict(sorted(per_sec[sec].items())))
            print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pcap")
    ap.add_argument("--tz", type=float, default=3.0, help="hours offset for printed times (plant: 3)")
    ap.add_argument("--sau", default="192.168.0.128")
    ap.add_argument("--master", default="192.168.0.112")
    ap.add_argument("--slow", type=float, default=0.5, help="seconds; transactions slower than this are listed")
    a = ap.parse_args()
    analyse(a.pcap, a.tz, a.sau, a.master, a.slow)
