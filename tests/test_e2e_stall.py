"""End to end: a fake master and a fake SAU; the SAU's application freezes 2.5 s (the kernel still accepts), the
master raises both comm-fail bits for 6 s. The real trace with the stall catcher, then the real report, must keep
the master's 1-s cadence, time the stall right, never write, and call it a stall inside the SAU."""

import csv
import glob
import os
import socket
import struct
import threading
import time

from plantcollector import report
from plantcollector.registry import load_registry
from plantcollector.trace import Trace


class FakeServer:
    def __init__(self, port):
        self.regs = {}
        self.frozen_until = 0.0
        self.requests = 0
        self.writes = 0
        self.s = socket.socket(); self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind(("127.0.0.1", port)); self.s.listen(8)
        threading.Thread(target=self.accept, daemon=True).start()
    def accept(self):
        while True:
            c, _ = self.s.accept()
            threading.Thread(target=self.handle, args=(c,), daemon=True).start()
    def handle(self, c):
        buf = b""
        try:
            while True:
                d = c.recv(4096)
                if not d: return
                buf += d
                while len(buf) >= 7:
                    tid, pid, ln, uid = struct.unpack(">HHHB", buf[:7])
                    if len(buf) < 6 + ln: break
                    pdu, buf = buf[7:6 + ln], buf[6 + ln:]
                    self.requests += 1
                    fc = pdu[0]
                    while time.monotonic() < self.frozen_until:      # the application stalls; the kernel still ACKs
                        time.sleep(0.01)
                    if fc in (3, 4):
                        a, q = struct.unpack(">HH", pdu[1:5])
                        vals = [self.regs.get(a + i, 0) for i in range(q)]
                        body = bytes([fc, 2 * q]) + b"".join(struct.pack(">H", v) for v in vals)
                    else:
                        self.writes += 1
                        body = bytes([fc | 0x80, 1])
                    c.sendall(struct.pack(">HHHB", tid, 0, len(body) + 1, uid) + body)
        except OSError:
            return


def test_frozen_sau_is_caught_and_called_a_stall_inside_the_sau(tmp_path):
    neo, sau = FakeServer(15081), FakeServer(15082)
    neo.regs[1040] = 2400
    sau.regs[1001], sau.regs[2101] = 24000, 25000
    reg = load_registry("config/site.plant.yaml")
    reg.devices["neo-01"].connection.update({"host": "127.0.0.1", "port": 15081, "timeout": 3})
    reg.devices["pv-array"].connection.update({"host": "127.0.0.1", "port": 15082, "timeout": 5})
    tr = Trace(reg, str(tmp_path), {"neo-01": 1.0, "pv-array": 5.0}, connect_test_s=None, watch={"pv-array": 1.0}, ping_s=1.0, log=lambda m: None)

    def scenario():
        time.sleep(5)
        sau.frozen_until = time.monotonic() + 2.5
        time.sleep(2.6)
        neo.regs[1268], neo.regs[1040], sau.regs[2101] = 0b110, 0, 0
        time.sleep(5)
        neo.regs[1268], neo.regs[1040] = 0, 300
    threading.Thread(target=scenario, daemon=True).start()
    tr.run(duration_s=17)

    assert neo.writes == 0 and sau.writes == 0
    rows = list(csv.DictReader(open(glob.glob(str(tmp_path / "neo-01_*.csv"))[0])))
    gaps = [(report._ts(b["ts"]) - report._ts(a["ts"])).total_seconds() for a, b in zip(rows, rows[1:])]
    assert max(gaps) < 1.3                                    # the master is never held up by the SAU
    reads = [r for r in csv.DictReader(open(glob.glob(str(tmp_path / "watch_*.csv"))[0])) if r["kind"] == "read"]
    sau_rows = list(csv.DictReader(open(glob.glob(str(tmp_path / "pv-array_*.csv"))[0])))
    slow = [float(r["ms"]) for r in reads + sau_rows if float(r["ms"]) >= 2000]   # whichever request met the stall
    assert len(slow) == 1 and 2300 <= slow[0] <= 2700
    assert sau.requests / 17 < 2.0                            # our load on the SAU stays small
    date = os.path.basename(glob.glob(str(tmp_path / "neo-01_*.csv"))[0])[7:17]
    text = report.summarize(str(tmp_path), date)
    assert "2 fails" in text and "answered nobody for 2.5 s: a stall inside the SAU" in text, text
    assert "comm fail -> zero setpoint" in text
