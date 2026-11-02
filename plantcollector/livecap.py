"""
Live reading of the SAU's traffic while it is being captured, so the session knows what happens as it happens.

Wireshark's tshark listens on the same adapter as the ring capture (it only listens, nothing is sent) and prints
one line per packet with the SAU. From those lines this module keeps, for every client of the SAU:

  - every Modbus request and its answer: how long the SAU took, whether its TCP acknowledged the request at once
    (the request reached the box), the exception code if any (06 = Server Device Busy), requests never answered;
  - connection resets and retransmissions;
  - everything else with the SAU: web page requests (and log downloads), ping, other ports. The SAU page's own
    /file/downloadByUrl calls only fetch its configuration files (seen on site, 22 Oct) and are not log downloads.

With the SAU's switch port mirrored to this PC the master's requests are in it; without a mirror only this PC's
own traffic is, and `mirror` says so. The ring files on disk stay the evidence; this is the live view and the
material for the diagnosis of each comm fail.
"""

from __future__ import annotations

import collections
import statistics
import subprocess
import threading
import time

from . import capture as _cap
from .capture import _WIN

FIELDS = ["frame.time_epoch", "ip.src", "ip.dst", "ip.proto", "tcp.srcport", "tcp.dstport", "tcp.stream", "tcp.len",
          "tcp.seq", "tcp.ack", "tcp.flags.reset", "tcp.analysis.retransmission", "mbtcp.trans_id", "modbus.func_code",
          "modbus.exception_code", "icmp.type", "udp.dstport", "http.request.uri"]
LATE_MS = 1000          # an answer this late counts as late (the master gives up at about 1.9-3 s)
NO_ANSWER_S = 4.0       # a request still open after this long counts as not answered
KEEP_S = 3 * 3600       # transactions kept in memory for the diagnosis of past fails



def is_log_download(uri: str) -> bool:
    """A real log or record export, not the page fetching its own configuration files through /file/downloadByUrl."""
    u = (uri or "").lower()
    return "download" in u and "/file/downloadbyurl" not in u and ("log" in u or "record" in u or "export" in u)


class LiveCapture:
    def __init__(self, tshark: str, interface: str, sau: str, master: str | None, me: str | None, log=None):
        self.tshark, self.interface, self.sau, self.master, self.me = tshark, interface, sau, master, me
        self.log = log or (lambda text, level="info": None)
        self.lock = threading.RLock()
        self.txs: collections.deque = collections.deque()          # finished or given-up transactions
        self.pending: dict[tuple, dict] = {}                       # (stream, tid) -> request
        self.other: collections.deque = collections.deque()        # (t, peer, what, detail)
        self.resets: collections.deque = collections.deque()       # (t, client)
        self.retrans: collections.deque = collections.deque()      # (t, src)
        self.peers_seen: dict[tuple, float] = {}                   # (peer, what) -> last time
        self._dl_said: dict[str, float] = {}                       # peer -> time its last log download was reported
        self.last_master_seen = 0.0
        self.packets = 0
        self.proc: subprocess.Popen | None = None
        self.stop = threading.Event()
        self.started = 0.0
        self.episode = None                                        # an open "SAU late" episode
        self.restarts = 0
        self.on_episode = None                                     # callback(start, end, text) when a late episode closes

    # ---- the process ------------------------------------------------------------------------------------
    def command(self) -> list[str]:
        cmd = [self.tshark, "-l", "-n", *_cap.decode_as(), "-i", str(self.interface), "-f", f"host {self.sau}",
               "-T", "fields", "-E", "separator=\t", "-E", "occurrence=a", "-E", "aggregator=|"]
        for f in FIELDS:
            cmd += ["-e", f]
        return cmd

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="livecap").start()
        threading.Thread(target=self._sweep, daemon=True, name="livecap-sweep").start()

    def _run(self) -> None:
        kw = {"creationflags": 0x08000000} if _WIN else {}
        while not self.stop.is_set():
            try:
                self.proc = subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             text=True, encoding="utf-8", errors="replace", bufsize=1, **kw)
            except OSError as exc:
                self.log(f"live traffic view: tshark did not start ({exc}); the ring capture goes on", "warn")
                return
            self.started = time.time()
            for line in self.proc.stdout:
                if self.stop.is_set():
                    break
                try:
                    self.feed(line.rstrip("\n").split("\t"))
                except Exception:
                    pass
                if time.time() - self.started > 3600:              # tshark grows in memory on long runs: renew hourly
                    break
            self._kill()
            if not self.stop.is_set():
                self.restarts += 1
                time.sleep(1)

    def _kill(self) -> None:
        p = self.proc
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    def close(self) -> None:
        self.stop.set()
        self._kill()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ---- one packet ---------------------------------------------------------------------------------------
    def feed(self, r: list[str]) -> None:
        if len(r) < len(FIELDS) or not r[0][:1].isdigit():
            return
        t = float(r[0])
        # an ICMP error carries the packet it answers: tshark then lists both headers ("a|b"); the outer one counts
        src, dst, proto = r[1].split("|")[0], r[2].split("|")[0], r[3].split("|")[0]
        if not proto or not (src or dst):                    # ARP and other frames without an IP header
            return
        with self.lock:
            self.packets += 1
            if self.master and (src == self.master or dst == self.master):
                self.last_master_seen = t
            port = str(_cap.MODBUS_PORT)
            if proto == "6" and port in (r[4], r[5]):
                self._modbus(t, r)
                return
            peer = dst if src == self.sau else src
            if proto == "1":
                what, detail = "ping", r[15]
            elif proto == "6":
                port = r[5] if src != self.sau else r[4]
                what = {"80": "web page", "8080": "web page", "22": "ssh", "21": "ftp", "2404": "IEC 104"}.get(port, f"tcp {port}")
                detail = r[17]
                if detail and src != self.sau and is_log_download(detail) and t - self._dl_said.get(peer, 0) > 60:
                    self._dl_said[peer] = t                  # once per download burst, not once per request
                    self.log(f"{self.name(peer)} is downloading a log from the SAU's web page ({detail}), extra work for the SAU", "alert")
            elif proto == "17":
                what, detail = f"udp {r[16]}", ""
            else:
                what, detail = f"ip {proto}", ""
            self.other.append((t, peer, what, detail))
            key = (peer, what)
            if key not in self.peers_seen and what not in ("ping",) and peer != self.me:
                self.log(f"{self.name(peer)} started talking to the SAU ({what})", "info")
            self.peers_seen[key] = t

    def _modbus(self, t: float, r: list[str]) -> None:
        src, dst, sport, dport = r[1], r[2], r[4], r[5]
        stream = r[6]
        tids = [x for x in r[12].split("|") if x]
        if r[10] in ("1", "True"):
            self.resets.append((t, dst if src == self.sau else src))
        if r[11]:
            self.retrans.append((t, src))
        port = str(_cap.MODBUS_PORT)
        if dst == self.sau and dport == port:                      # a request
            seq = int(r[8]) if r[8].isdigit() else None
            nxt = seq + int(r[7]) if seq is not None and r[7].isdigit() else None
            fcs = [x for x in r[13].split("|") if x]
            for k, tid in enumerate(tids):
                key = (stream, tid)
                if key not in self.pending:
                    self.pending[key] = {"t": t, "client": src, "tid": tid, "fc": int(fcs[k]) if k < len(fcs) and fcs[k].isdigit() else None,
                                         "ack_ms": None, "ans_ms": None, "exc": None, "nxt": nxt}
        elif src == self.sau and sport == port:                    # from the SAU: an ACK and/or answers
            ackn = int(r[9]) if r[9].isdigit() else None
            for (st, _tid), tx in self.pending.items():
                if st == stream and tx["ack_ms"] is None and t >= tx["t"] and (ackn is None or tx["nxt"] is None or ackn >= tx["nxt"]):
                    tx["ack_ms"] = (t - tx["t"]) * 1000
            excs = [x for x in r[14].split("|") if x]
            for k, tid in enumerate(tids):
                tx = self.pending.pop((stream, tid), None)
                if tx is None:
                    continue
                tx["ans_ms"] = (t - tx["t"]) * 1000
                if excs:
                    e = excs[min(k, len(excs) - 1)]
                    tx["exc"] = int(e) if e.isdigit() else None
                self._done(tx, t)

    def _done(self, tx: dict, now: float) -> None:
        self.txs.append(tx)
        late = (tx["ans_ms"] is None) or tx["ans_ms"] >= LATE_MS or (tx["exc"] == 6 and tx["ans_ms"] >= 500)
        if late and tx["client"] == self.master:
            self._episode(tx, now)
        while self.txs and now - self.txs[0]["t"] > KEEP_S:
            self.txs.popleft()
        for dq in (self.other, self.resets, self.retrans):
            while dq and now - dq[0][0] > KEEP_S:
                dq.popleft()

    def _episode(self, tx: dict, now: float) -> None:
        ep = self.episode
        if ep and now - ep["last"] < 8:
            ep["last"] = now
            ep["n"] += 1
            ep["worst"] = max(ep["worst"], tx["ans_ms"] or NO_ANSWER_S * 1000)
            ep["busy"] += tx["exc"] == 6
            ep["none"] += tx["ans_ms"] is None
            return
        self.episode = {"start": tx["t"], "last": now, "n": 1, "worst": tx["ans_ms"] or NO_ANSWER_S * 1000,
                        "busy": int(tx["exc"] == 6), "none": int(tx["ans_ms"] is None), "ack": tx["ack_ms"]}
        if tx["ans_ms"] is None:
            what = "did not answer a request from the master"
        else:
            what = f"answered the master {tx['ans_ms'] / 1000:.2f} s late" + (" with exception 06 (Server Device Busy)" if tx["exc"] == 6 else "")
        ack = (f"; its TCP acknowledged the request in {tx['ack_ms']:.0f} ms, so the request reached the SAU and its program was late"
               if tx["ack_ms"] is not None and (tx["ans_ms"] is None or tx["ans_ms"] - tx["ack_ms"] >= 200) else "")
        self.log(f"the SAU {what}{ack}", "alert")

    def _sweep(self) -> None:
        while not self.stop.wait(1.0):
            now = time.time()
            with self.lock:
                for key, tx in list(self.pending.items()):
                    if now - tx["t"] > NO_ANSWER_S:
                        del self.pending[key]
                        self._done(tx, now)
                ep = self.episode
                if ep and now - ep["last"] > 8:
                    self.episode = None
                    parts = [f"{ep['n']} late answer(s)", f"worst {ep['worst'] / 1000:.2f} s"]
                    if ep["busy"]:
                        parts.append(f"{ep['busy']} x exception 06 Busy")
                    if ep["none"]:
                        parts.append(f"{ep['none']} never answered")
                    self.log(f"the SAU answers the master on time again ({', '.join(parts)})", "ok")
                    if self.on_episode is not None:
                        try:
                            self.on_episode(ep["start"], ep["last"], ", ".join(parts))
                        except Exception:
                            pass

    # ---- what the page and the diagnosis read ------------------------------------------------------------------
    def name(self, ip: str) -> str:
        if ip == self.master:
            return f"the master ({ip})"
        if ip == self.me:
            return f"this PC ({ip})"
        return ip

    def mirror(self) -> str:
        """'yes' when the master's traffic with the SAU was seen in the last minute."""
        if not self.alive() and not self.packets:
            return "unknown"
        return "yes" if time.time() - self.last_master_seen < 60 else "no"

    def window(self, t0: float, t1: float) -> dict:
        """Everything the capture saw between t0 and t1 (epoch seconds), per client, for the diagnosis."""
        with self.lock:
            txs = [x for x in list(self.txs) + list(self.pending.values()) if t0 <= x["t"] <= t1]
            other = [o for o in self.other if t0 <= o[0] <= t1]
            resets = [x for x in self.resets if t0 <= x[0] <= t1]
            retrans = [x for x in self.retrans if t0 <= x[0] <= t1]
        return {"txs": txs, "other": other, "resets": resets, "retrans": retrans}

    def stats(self, seconds: float = 60.0) -> list[dict]:
        """Per client and kind over the last `seconds`: the 'Traffic with the SAU' table."""
        now = time.time()
        w = self.window(now - seconds, now)
        rows = []
        by = collections.defaultdict(list)
        for tx in w["txs"]:
            by[tx["client"]].append(tx)
        for client, txs in sorted(by.items()):
            ans = [x["ans_ms"] for x in txs if x["ans_ms"] is not None]
            rows.append({"peer": client, "label": self.name(client), "what": "Modbus",
                         "count": len(txs), "per_min": round(len(txs) * 60 / seconds),
                         "median_ms": round(statistics.median(ans), 1) if ans else None, "max_ms": round(max(ans), 1) if ans else None,
                         "late": sum(1 for a in ans if a >= LATE_MS), "busy": sum(1 for x in txs if x["exc"] == 6 and (x["ans_ms"] or 0) >= 500),
                         "refused": sum(1 for x in txs if x["exc"] and (x["ans_ms"] or 0) < 500),
                         "none": sum(1 for x in txs if x["ans_ms"] is None and now - x["t"] > NO_ANSWER_S)})
        # requests, not packets: a web page request has its URI, a ping request is ICMP type 8; other kinds count packets
        oc = collections.Counter((o[1], o[2]) for o in w["other"]
                                 if (o[2] == "web page" and o[3]) or (o[2] == "ping" and o[3] == "8") or o[2] not in ("web page", "ping"))
        for (peer, what), n in oc.most_common(12):
            rows.append({"peer": peer, "label": self.name(peer), "what": what, "count": n, "per_min": round(n * 60 / seconds),
                         "median_ms": None, "max_ms": None, "late": 0, "busy": 0, "refused": 0, "none": 0})
        return rows

    def master_latency(self, seconds: float = 3600, step: float = 5.0) -> list[dict]:
        """The slowest answer to the master in every `step` seconds: the live latency chart."""
        now = time.time()
        w = self.window(now - seconds, now)
        buckets: dict[int, float] = {}
        for x in w["txs"]:
            if x["client"] != self.master:
                continue
            b = int(x["t"] // step)
            v = x["ans_ms"] if x["ans_ms"] is not None else NO_ANSWER_S * 1000
            buckets[b] = max(buckets.get(b, 0), v)
        return [{"t": int(b * step * 1000), "ms": round(v, 1)} for b, v in sorted(buckets.items())]
