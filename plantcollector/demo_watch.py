"""
DEMO-SESSION.bat: the SAU watch on a simulated plant, to see the live page, the console, the diagnosis, the evidence
and the reports at work without touching the real plant. Nothing is sent to any real device.

On this PC only (loopback addresses), every 5 minutes, round and round:
  0:45  a comm fail on group 1: the SAU answers the master 2.5 s late with exception 06 (busy)
  2:00  a comm fail on both groups; the SAU also stops answering ping for 3 s
  3:10  someone downloads a log from the SAU's web page
  3:20  a comm fail although the SAU answers on time (the master's own side)
  4:15  the SAU answers 1.5 s late for a few seconds without a declared fail
and all the time: the master polling the SAU, a browser polling the SAU's web page.

Files go to data-demo\\ and bundles-demo\\, never into the real data\\ folder. With Wireshark installed the demo
captures on Npcap's loopback adapter, so the capture, the evidence and the diagnosis work exactly as on site.
"""

from __future__ import annotations

import math
import os
import random
import socket
import struct
import threading
import time

MASTER, SAU = "127.0.0.3", "127.0.0.2"
CYCLE = 300.0
FAIL_BUSY = (45, 57)          # group 1, the SAU busy
FAIL_BOTH = (120, 127)        # both groups, ping lost 121-124
DOWNLOAD_AT = 190
FAIL_MASTER = (200, 206)      # the SAU answers on time
LATE_ONLY = (255, 262)        # late answers, no declared fail


class Plant:
    def __init__(self):
        self.t0 = time.time()
        self.regs = [0] * 1400
        self.stop = threading.Event()
        self.ports: dict[str, int] = {}

    def el(self) -> float:
        return (time.time() - self.t0) % CYCLE

    def within(self, w, pad=0.0) -> bool:
        return w[0] - pad <= self.el() < w[1]

    # ---- the servers -------------------------------------------------------------------------------------
    def _server(self, host: str, port: int, handler) -> int:
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(16)

        def accept():
            while not self.stop.is_set():
                try:
                    c, _ = srv.accept()
                except OSError:
                    return
                threading.Thread(target=handler, args=(c,), daemon=True).start()
        threading.Thread(target=accept, daemon=True).start()
        return srv.getsockname()[1]

    def _modbus(self, regs_fn, delay_fn):
        def handle(c):
            with c:
                while not self.stop.is_set():
                    try:
                        h = c.recv(7)
                        if len(h) < 7:
                            return
                        tid, _, ln, unit = struct.unpack("!HHHB", h)
                        pdu = c.recv(ln - 1)
                        fc, a, q = struct.unpack("!BHH", pdu[:5])
                        d, exc = delay_fn()
                        if d:
                            time.sleep(d)
                        if exc:
                            body = struct.pack("!BB", fc | 0x80, exc)
                        else:
                            vals = regs_fn(a, q)
                            body = struct.pack("!BB", fc, 2 * q) + struct.pack(f"!{q}H", *vals)
                        c.sendall(struct.pack("!HHHB", tid, 0, len(body) + 1, unit) + body)
                    except OSError:
                        return
        return handle

    def _web(self, c):
        with c:
            try:
                c.recv(4000)
                body = b'{"ok":1,"values":[' + b",".join(str(random.randint(0, 9999)).encode() for _ in range(300)) + b"]}"
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode()
                          + b"\r\n\r\n" + body)
            except OSError:
                pass

    def _sau_delay(self):
        if self.within(FAIL_BUSY, 1) or self.within(FAIL_BOTH, 1):
            return (2.5, 6) if random.random() < 0.6 else (2.4, 0)
        if self.within(LATE_ONLY):
            return (1.5, 0)
        return (random.uniform(0.004, 0.03), 0)

    def start(self) -> dict:
        self.ports["master"] = self._server(MASTER, 0, self._modbus(lambda a, q: self.regs[a:a + q], lambda: (0, 0)))
        self.ports["sau"] = self._server(SAU, 0, self._modbus(lambda a, q: [random.randint(0, 9000) for _ in range(q)], self._sau_delay))
        for wp in (80, 8080, 0):                              # the SAU's web page on 80 when free, like the real one
            try:
                self.ports["web"] = self._server(SAU, wp, self._web)
                break
            except OSError:
                continue
        for lp, period in ((0, 0.5), (0, 1.0)):
            threading.Thread(target=self._master_client, args=(period,), daemon=True).start()
        threading.Thread(target=self._browser, daemon=True).start()
        threading.Thread(target=self._values, daemon=True).start()
        return self.ports

    # ---- the clients -------------------------------------------------------------------------------------
    def _master_client(self, period: float):
        while not self.stop.is_set():
            try:
                s = socket.socket()
                s.bind((MASTER, 0))
                s.connect((SAU, self.ports["sau"]))
                s.settimeout(5)
                tid = 0
                while not self.stop.is_set():
                    tid = (tid + 1) & 0xFFFF
                    s.sendall(struct.pack("!HHHBBHH", tid, 0, 6, 245, 3, 1000, 20))
                    s.recv(512)
                    time.sleep(period)
            except OSError:
                time.sleep(1)

    def _browser(self):
        downloaded = -1
        while not self.stop.is_set():
            cycle = int((time.time() - self.t0) // CYCLE)
            uri = "/server/operatingParameters"
            if self.el() >= DOWNLOAD_AT and downloaded != cycle:
                uri, downloaded = "/deviceLog/downloadRunTimeUrl", cycle
            try:
                s = socket.create_connection((SAU, self.ports["web"]), timeout=2)
                s.sendall(f"POST {uri} HTTP/1.1\r\nHost: sau\r\nContent-Length: 2\r\n\r\n{{}}".encode())
                s.recv(20000)
                s.close()
            except OSError:
                pass
            time.sleep(1.2)

    def _values(self):
        def i32(a, v):
            v = int(v) & 0xFFFFFFFF
            self.regs[a], self.regs[a + 1] = v >> 16, v & 0xFFFF
        while not self.stop.is_set():
            t = time.time() - self.t0
            pv = 700 + 60 * math.sin(t / 25) + random.uniform(-8, 8)
            gen = 1310 + 40 * math.sin(t / 40) + random.uniform(-5, 5)
            fails = [w for w in (FAIL_BUSY, FAIL_BOTH, FAIL_MASTER) if self.within(w)]
            if fails:
                pv = max(0, pv - 520 * min(1, (self.el() - fails[0][0]) / 3))
                gen += 480
            self.regs[1040] = int(pv) & 0xFFFF
            i32(1068, gen)
            self.regs[1085] = 0
            i32(1074, 50000 + random.randint(-30, 30))
            self.regs[1046] = 9962
            i32(1130, 3857)
            self.regs[1261] = 0x4000
            self.regs[1268] = 0b110 if self.within(FAIL_BOTH) else 0b10 if (self.within(FAIL_BUSY) or self.within(FAIL_MASTER)) else 0
            time.sleep(0.25)

    def ping(self, host: str, timeout_s: float = 1.0):
        """The demo's ping: simulated, no traffic to the simulated devices; the SAU 'drops' it during the second fail."""
        if host == SAU and 121 <= self.el() < 124:
            time.sleep(0.2)
            return 0, 1000.0, "timeout (demo)"
        return 1, round(random.uniform(0.4, 1.6), 2), ""


def run(root: str, open_browser: bool = True) -> int:
    from . import capture as cap
    from . import trace
    from .session import Session
    plant = Plant()
    try:
        ports = plant.start()
    except OSError as exc:
        print(f"[demo] cannot start the simulated plant on {SAU} / {MASTER}: {exc}")
        return 2
    trace.ping = plant.ping
    cap.MODBUS_PORT = ports["sau"]
    data = os.path.join(root, "data-demo")
    os.makedirs(data, exist_ok=True)
    cfg = os.path.join(data, "site.demo.yaml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(f"""# written by DEMO-SESSION.bat: a simulated plant on this PC's loopback addresses
site: {{site_id: demo, name: "Demo plant (simulated)", poll_interval_s: 3600, timezone: Asia/Baghdad}}
devices:
  - {{id: neo-01, profile: intelineo_master.yaml, adapter: modbus_tcp, connection: {{host: {MASTER}, port: {ports['master']}, unit_id: 1, timeout: 1}}}}
  - {{id: pv-array, profile: pv_array_sau.yaml, adapter: modbus_tcp, connection: {{host: {SAU}, port: {ports['sau']}, unit_id: 245, timeout: 1}}}}
""")
    dumpcap = cap.find_tool("dumpcap")
    lo = cap.loopback_interface(dumpcap) if dumpcap else None
    s = Session(cfg, data, long_run=True, mode="passive", capture=bool(lo), capture_if=lo, ring_file_s=300,
                bundles_dir=os.path.join(root, "bundles-demo"))
    s.monitor_on, s.open_browser, s.demo = True, open_browser, True
    s.display_ips = {MASTER: "192.168.0.112", SAU: "192.168.0.128", "127.0.0.1": "192.168.0.50"}   # shown as on site
    s.selftest_s, s.evidence_margin_s, s.keeper_s = 8, 20.0, 5.0
    s._pinger = lambda host: socket.create_connection((host, ports["web"]), timeout=1).close()
    print("[demo] DEMO: a simulated plant on this PC; nothing is sent to the real master or SAU. Files in data-demo\\.")
    if dumpcap and not lo:
        print("[demo] Wireshark found but no loopback adapter (install Npcap with loopback support): the demo runs without capture")
    try:
        s.run()
    except KeyboardInterrupt:
        s.stop.set()
    finally:
        plant.stop.set()
    return 0
