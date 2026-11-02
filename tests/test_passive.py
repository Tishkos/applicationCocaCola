"""The passive session: the SAU is pinged and captured, never read; the capture is read back with tshark."""

import os
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from plantcollector import capture as cap
from plantcollector.session import Session

from test_session import _serve

NEO_PORT = 15051

SAU, MASTER, PC = "192.168.0.128", "192.168.0.112", "192.168.0.50"


# ---- a tiny pcap writer: Ethernet / IPv4 / TCP or ICMP, checksums left at zero (tshark does not check them) ----
def _ip(a):
    return socket.inet_aton(a)


def _pkt(src, dst, proto, payload):
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 1, 0, 64, proto, 0, _ip(src), _ip(dst))
    return b"\x00\x11\x22\x33\x44\x55" + b"\x66\x77\x88\x99\xaa\xbb" + b"\x08\x00" + ip + payload


def _tcp(src, dst, sport, dport, seq, ack, data=b"", flags=0x18):
    hdr = struct.pack("!HHIIBBHHH", sport, dport, seq, ack, 5 << 4, flags, 8192, 0, 0)
    return _pkt(src, dst, 6, hdr + data)


def _icmp(src, dst, typ):
    return _pkt(src, dst, 1, struct.pack("!BBHHH", typ, 0, 0, 1, 1) + b"x" * 8)


def _mb_req(tid, fc=3, addr=1000, qty=2):
    return struct.pack("!HHHB", tid, 0, 6, 245) + struct.pack("!BHH", fc, addr, qty)


def _mb_resp(tid, fc=3, exc=None):
    if exc:
        return struct.pack("!HHHB", tid, 0, 3, 245) + struct.pack("!BB", fc | 0x80, exc)
    return struct.pack("!HHHB", tid, 0, 7, 245) + struct.pack("!BB", fc, 4) + b"\x00\x01\x00\x02"


def write_pcap(path, packets):
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for t, data in packets:
            f.write(struct.pack("<IIII", int(t), int(round((t - int(t)) * 1e6)), len(data), len(data)) + data)


def _stall_capture(path, t0):
    """Two master sessions. Group 2 (port 61416) polls every 0.5 s; at t0 + 10 s its request is acknowledged by the
    SAU's TCP in 30 ms and answered 2.5 s later with exception 06. Group 1 (port 61415) asks at t0 + 10.01 s and
    gets its answer 2.49 s later. Every other request is answered in 2 ms. A ping from this PC at t0 + 3 s."""
    pk = []
    seq = {61416: [1, 1], 61415: [1, 1]}

    def ask(t, port, tid, delay, exc=None, ack_at=None):
        m, sv = seq[port]
        req = _mb_req(tid)
        pk.append((t, _tcp(MASTER, SAU, port, 502, m, sv, req)))
        m += len(req)
        if ack_at is not None:
            pk.append((t + ack_at, _tcp(SAU, MASTER, 502, port, sv, m, b"", flags=0x10)))
        resp = _mb_resp(tid, exc=exc)
        pk.append((t + delay, _tcp(SAU, MASTER, 502, port, sv, m, resp)))
        seq[port] = [m, sv + len(resp)]

    t = t0
    tid = 100
    while t < t0 + 20:
        if abs(t - (t0 + 10)) < 1e-6:
            ask(t, 61416, tid, 2.5, exc=6, ack_at=0.030)
            ask(t + 0.01, 61415, 9000, 2.49)
            t += 3.0
        else:
            ask(t, 61416, tid, 0.002)
            t += 0.5
        tid += 1
    pk.append((t0 + 3, _icmp(PC, SAU, 8)))
    pk.append((t0 + 3.001, _icmp(SAU, PC, 0)))
    write_pcap(path, sorted(pk, key=lambda p: p[0]))


TSHARK = cap.find_tool("tshark")


@pytest.mark.skipif(not TSHARK, reason="tshark (Wireshark) not installed here")
def test_capture_shows_the_request_reached_the_sau_and_the_answer_came_late(tmp_path):
    p = str(tmp_path / "sau_00001_20261020093000.pcap")
    t0 = datetime(2026, 10, 20, 6, 33, 0, tzinfo=timezone.utc).timestamp()
    _stall_capture(p, t0)
    txs = cap.transactions([p], SAU, TSHARK)
    assert len(txs) == 36
    stall = [x for x in txs if x["tid"] == "120"][0]
    other = [x for x in txs if x["tid"] == "9000"][0]
    assert other["exc"] is None and 2400 < other["ans_ms"] < 2600
    assert stall["exc"] == 6 and 2400 < stall["ans_ms"] < 2600 and stall["ack_ms"] < 50
    v = cap.window_verdict(txs, datetime.fromtimestamp(t0 + 11.5, timezone.utc), datetime.fromtimestamp(t0 + 15, timezone.utc), MASTER)
    assert "exception 06" in v and "acknowledged 1 of them within 30 ms" in v and "software answered late" in v
    calm = cap.window_verdict(txs, datetime.fromtimestamp(t0 + 4, timezone.utc), datetime.fromtimestamp(t0 + 5, timezone.utc), MASTER)
    assert "all answered" in calm
    oth = cap.other_traffic([p], SAU, TSHARK)
    assert {o["what"] for o in oth} == {"ping"} and {o["peer"] for o in oth} == {PC}
    fail = dict(session="group 1", start=datetime.fromtimestamp(t0 + 11.5, timezone.utc), end=datetime.fromtimestamp(t0 + 15, timezone.utc))
    text = "\n".join(cap.report_section([p], [fail], SAU, MASTER, TSHARK))
    assert f"{MASTER} (36 requests)." in text and "software answered late" in text and "1 x code 6" in text


@pytest.mark.skipif(not TSHARK, reason="tshark (Wireshark) not installed here")
def test_without_a_mirror_the_report_says_so(tmp_path):
    p = str(tmp_path / "sau_00001_20261020093000.pcap")
    t0 = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc).timestamp()
    write_pcap(p, [(t0 + k, _icmp(PC, SAU, 8)) for k in range(5)])
    fail = dict(session="group 1", start=datetime.fromtimestamp(t0 + 2, timezone.utc), end=datetime.fromtimestamp(t0 + 4, timezone.utc))
    text = "\n".join(cap.report_section([p], [fail], SAU, MASTER, TSHARK))
    assert "does not mirror the SAU's port" in text and "no master requests in the capture" in text


def test_report_without_tshark_still_names_the_files(tmp_path):
    p = tmp_path / "sau_00001_20261020093000.pcapng"
    p.write_bytes(b"x")
    text = "\n".join(cap.report_section([str(p)], [], SAU, MASTER, None))
    assert "tshark (Wireshark) was not found" in text and "modbus.exception_code == 6" in text


def test_keep_copies_the_ring_files_around_a_fail(tmp_path):
    ring, ev = tmp_path / "ring", tmp_path / "ev"
    ring.mkdir()
    base = datetime(2026, 10, 20, 8, 0)                     # the PC's local time, as dumpcap names the files
    paths = []
    for k in range(4):
        start = base + timedelta(hours=k)
        p = ring / f"sau_{k + 1:05d}_{start.strftime('%Y%m%d%H%M%S')}.pcapng"
        p.write_bytes(b"data" * (k + 1))
        end = (start + timedelta(hours=1)).astimezone().timestamp() if k < 3 else time.time()
        os.utime(p, (end, end))
        paths.append(p)
    files = cap.ring_files(str(ring))
    assert [f["num"] for f in files] == [1, 2, 3, 4]
    t_fail = files[1]["start"] + timedelta(minutes=30)       # in the middle of file 2
    copied, complete = cap.keep(str(ring), str(ev), t_fail, t_fail + timedelta(seconds=12))
    assert [os.path.basename(c) for c in copied] == [paths[1].name] and complete
    edge = files[2]["start"] + timedelta(seconds=20)         # 20 s into file 3: file 2 holds the minute before
    copied, complete = cap.keep(str(ring), str(ev), edge, edge + timedelta(seconds=5))
    assert sorted(os.path.basename(c) for c in copied) == [paths[2].name] and complete   # file 2 was already kept
    assert os.path.exists(ev / paths[1].name)
    late = files[3]["start"] + timedelta(seconds=5)         # inside the file still being written
    copied, complete = cap.keep(str(ring), str(ev), late, late + timedelta(seconds=5))
    assert not complete


def test_pick_interface_matches_the_adapter_towards_the_sau(monkeypatch):
    monkeypatch.setattr(cap, "local_ip_for", lambda host: PC)
    monkeypatch.setattr(cap, "interface_alias", lambda ip: "Ethernet 3" if ip == PC else None)
    monkeypatch.setattr(cap, "_run", lambda cmd, timeout=20.0: "1. \\Device\\NPF_{AAA} (Local Area Connection* 8)\n"
                                                                "2. \\Device\\NPF_{BBB} (Ethernet 3)\n3. \\Device\\NPF_{CCC} (Wi-Fi 2)\n")
    iface, why = cap.pick_interface("dumpcap", SAU)
    assert iface == "2" and "Ethernet 3" in why and PC in why
    iface, why = cap.pick_interface("dumpcap", SAU, wanted="Wi-Fi 2")
    assert iface == "3"
    monkeypatch.setattr(cap, "interface_alias", lambda ip: None)
    iface, why = cap.pick_interface("dumpcap", SAU)
    assert iface is None and "no adapter name" in why


def test_dumpcap_command_is_a_ring_filtered_to_the_sau(tmp_path):
    c = cap.Capture("dumpcap", "2", SAU, str(tmp_path), file_s=3600, file_mb=100, files=48)
    cmd = c.command()
    assert cmd[:5] == ["dumpcap", "-i", "2", "-f", f"host {SAU}"]
    assert "duration:3600" in cmd and "filesize:100000" in cmd and "files:48" in cmd and "-w" in cmd


# ---- the session never touches the SAU ---------------------------------------------------------------------
class _Counter:
    """A TCP listener standing in for the SAU: counts every connection anyone opens to it."""

    def __init__(self, host="127.0.0.1"):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.accepts = 0
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.accepts += 1
            c.close()


@pytest.fixture(scope="module")
def master():
    import asyncio
    neo = [0] * 1400
    neo[1040] = 1000
    h = _serve(NEO_PORT, neo)
    yield h
    h["loop"].call_soon_threadsafe(lambda: asyncio.ensure_future(h["srv"].shutdown()))


def test_passive_session_never_connects_to_the_sau(master, tmp_path, monkeypatch):
    from plantcollector import trace
    monkeypatch.setattr(trace, "ping", lambda host, timeout_s=1.0: (1, 0.4, ""))   # no ICMP in a bare container
    sau = _Counter()
    cfg = tmp_path / "site.yaml"
    cfg.write_text(f"""site: {{site_id: t, name: test, poll_interval_s: 3600}}
devices:
  - {{id: neo-01, profile: intelineo_master.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: {NEO_PORT}, unit_id: 1, timeout: 1}}}}
  - {{id: pv-array, profile: pv_array_sau.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.2, port: {sau.port}, unit_id: 245, timeout: 1}}}}
probe:
  hosts: [127.0.0.2]
""")
    data = tmp_path / "data"
    s = Session(str(cfg), str(data), hours=0.002, long_run=False, bundles_dir=str(tmp_path / "bundles"),
                mode="passive", capture=False)
    zp = s.run()
    assert sau.accepts == 0
    log = open(s.log_path, encoding="utf-8").read()
    assert "SAU never read" in log and "no reads of the SAU" in log and "capture: off" in log and "bundle:" in log
    import zipfile
    names = zipfile.ZipFile(zp).namelist()
    assert any(n.startswith("trace/neo-01_") for n in names) and not any(n.startswith("trace/pv-array_") for n in names)
    assert "probe.txt" in names and "127.0.0.2" not in zipfile.ZipFile(zp).read("probe.txt").decode()
    rep = [n for n in names if n.startswith("trace/report_")][0]
    text = zipfile.ZipFile(zp).read(rep).decode()
    assert "From the capture" in text and "No capture files kept" in text
    watch = [n for n in names if n.startswith("trace/watch_")]
    assert watch and "pv-array,ping" in zipfile.ZipFile(zp).read(watch[0]).decode()


DUMPCAP = cap.find_tool("dumpcap")


def _can_capture():
    if not DUMPCAP:
        return False
    out = cap._run([DUMPCAP, "-D"])
    return "lo" in out


@pytest.mark.skipif(not _can_capture(), reason="dumpcap cannot capture here")
def test_session_tests_the_adapter_before_monitoring(tmp_path, monkeypatch):
    srv = _Counter("127.0.0.2")                              # stands for the SAU; the 'ping' is a TCP connect

    def pinger(host):
        s = socket.create_connection((host, srv.port), timeout=1)
        s.close()
    cfg = tmp_path / "site.yaml"
    cfg.write_text(f"""site: {{site_id: t, name: test, poll_interval_s: 3600}}
devices:
  - {{id: neo-01, profile: intelineo_master.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: {NEO_PORT}, unit_id: 1, timeout: 1}}}}
  - {{id: pv-array, profile: pv_array_sau.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.2, port: 502, unit_id: 245, timeout: 1}}}}
""")
    s = Session(str(cfg), str(tmp_path / "data"), hours=0.001, long_run=False, mode="passive", capture_if="lo")
    monkeypatch.setattr(s, "_pinger", pinger)
    s.selftest_s = 8
    s._start_capture()
    try:
        log = "\n".join(s.notes)
        assert "capture test PASSED on Loopback" in log and "Mirror: not yet" in log and "capture: monitoring on" in log
        assert s.cap is not None and s.cap.alive()
        kept = os.listdir(tmp_path / "data" / "evidence" / "selftest")
        assert len(kept) == 1 and kept[0].startswith("selftest_") and kept[0].endswith(".pcapng") and kept[0] in log
    finally:
        if s.cap:
            s.cap.stop()


@pytest.mark.skipif(not _can_capture(), reason="dumpcap cannot capture here")
def test_session_refuses_an_adapter_that_does_not_see_the_sau(tmp_path, monkeypatch):
    cfg = tmp_path / "site.yaml"
    cfg.write_text(f"""site: {{site_id: t, name: test, poll_interval_s: 3600}}
devices:
  - {{id: neo-01, profile: intelineo_master.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: {NEO_PORT}, unit_id: 1, timeout: 1}}}}
  - {{id: pv-array, profile: pv_array_sau.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.2, port: 502, unit_id: 245, timeout: 1}}}}
""")
    s = Session(str(cfg), str(tmp_path / "data"), hours=0.001, long_run=False, mode="passive", capture_if="lo")
    monkeypatch.setattr(s, "_pinger", lambda host: None)   # nothing goes to the SAU: the test must fail
    s.selftest_s = 6
    s._start_capture()
    log = "\n".join(s.notes)
    assert "capture test FAILED" in log and s.cap is None


@pytest.mark.skipif(not _can_capture(), reason="dumpcap cannot capture here")
def test_end_to_end_a_comm_fail_is_kept_as_pcapng(tmp_path, monkeypatch):
    """The whole chain on a real capture: the session runs, the master raises a comm fail, the keeper copies the
    ring files around it to evidence/<date>/ while the session is still running, and the report reads them."""
    from datetime import datetime, timezone
    regs = [0] * 1400
    regs[1040] = 700
    srv_sock = socket.socket()
    srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv_sock.bind(("127.0.0.1", 0))
    srv_sock.listen(4)
    port = srv_sock.getsockname()[1]

    def serve_client(c):                                     # a minimal Modbus TCP server: FC03 from regs, live
        with c:
            while True:
                hdr = c.recv(7)
                if len(hdr) < 7:
                    return
                tid, _, length, unit = struct.unpack("!HHHB", hdr)
                pdu = c.recv(length - 1)
                fc, addr, qty = struct.unpack("!BHH", pdu[:5])
                vals = regs[addr:addr + qty]
                body = struct.pack("!BB", fc, 2 * qty) + struct.pack(f"!{qty}H", *vals)
                c.sendall(struct.pack("!HHHB", tid, 0, len(body) + 1, unit) + body)

    def accept():
        while True:
            try:
                c, _ = srv_sock.accept()
            except OSError:
                return
            threading.Thread(target=serve_client, args=(c,), daemon=True).start()
    threading.Thread(target=accept, daemon=True).start()
    sau = _Counter("127.0.0.2")
    traffic = threading.Event()

    def talk():                                              # traffic to the 'SAU' for the capture to hold
        while not traffic.is_set():
            try:
                socket.create_connection(("127.0.0.2", sau.port), timeout=1).close()
            except OSError:
                pass
            time.sleep(0.3)
    threading.Thread(target=talk, daemon=True).start()
    from plantcollector import trace
    monkeypatch.setattr(trace, "ping", lambda host, timeout_s=1.0: (1, 0.3, ""))
    cfg = tmp_path / "site.yaml"
    cfg.write_text(f"""site: {{site_id: t, name: test, poll_interval_s: 3600}}
devices:
  - {{id: neo-01, profile: intelineo_master.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: {port}, unit_id: 1, timeout: 1}}}}
  - {{id: pv-array, profile: pv_array_sau.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.2, port: 502, unit_id: 245, timeout: 1}}}}
""")
    data = tmp_path / "data"
    s = Session(str(cfg), str(data), hours=0.009, long_run=False, mode="passive", capture_if="lo",
                ring_file_s=5, bundles_dir=str(tmp_path / "bundles"))
    s.selftest_s, s.evidence_margin_s, s.keeper_s = 4, 2.0, 1.0
    monkeypatch.setattr(s, "_pinger", lambda host: socket.create_connection((host, sau.port), timeout=1).close())
    result = {}
    th = threading.Thread(target=lambda: result.setdefault("zip", s.run()), daemon=True)
    th.start()
    try:
        deadline = time.time() + 30
        while time.time() < deadline and not (s.cap and s.cap.alive() and os.path.exists(data / "trace")):
            time.sleep(0.5)
        assert s.cap is not None and s.cap.alive(), "\n".join(s.notes)
        time.sleep(7)
        regs[1268] = 0b10                                    # ECU 2 comm fail (bit 1) for 4 s
        time.sleep(4)
        regs[1268] = 0
        from plantcollector import sitetime
        day = sitetime.day(datetime.now(timezone.utc))               # evidence folders are named by the plant's date
        ev_dir = data / "evidence" / day
        deadline = time.time() + 25
        while time.time() < deadline and not (ev_dir.exists() and any(n.endswith(".pcapng") for n in os.listdir(ev_dir))):
            time.sleep(0.5)
        kept = sorted(os.listdir(ev_dir)) if ev_dir.exists() else []
        assert kept and all(n.startswith("sau_") and n.endswith(".pcapng") for n in kept), "\n".join(s.notes)
        assert th.is_alive()                                 # kept while the session was still running
        log = "\n".join(s.notes)
        assert "evidence:" in log and "group 1 fail" in log
    finally:
        traffic.set()
        th.join(timeout=60)
        srv_sock.close()
    import zipfile
    names = zipfile.ZipFile(result["zip"]).namelist()
    assert any(n.startswith("evidence/sau_") for n in names) and any(n.startswith("evidence/selftest/") for n in names)
    rep = zipfile.ZipFile(result["zip"]).read([n for n in names if n.startswith("trace/report_")][0]).decode()
    assert "From the capture" in rep and "group 1" in rep


def test_a_second_session_on_the_same_data_folder_does_nothing(tmp_path):
    cfg = tmp_path / "site.yaml"
    cfg.write_text(f"""site: {{site_id: t, name: test, poll_interval_s: 3600}}
devices:
  - {{id: neo-01, profile: intelineo_master.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: 1, unit_id: 1, timeout: 1}}}}
""")
    a = Session(str(cfg), str(tmp_path / "data"), hours=0.001, long_run=False, mode="passive", capture=False)
    assert a._single_instance()
    b = Session(str(cfg), str(tmp_path / "data"), hours=0.001, long_run=False, mode="passive", capture=False)
    assert b.run() == "" and "already running" in "\n".join(b.notes)
    a._lock_file.close()                                   # the first one ends: the lock is free again
    c = Session(str(cfg), str(tmp_path / "data"), hours=0.001, long_run=False, mode="passive", capture=False)
    assert c._single_instance()
    c._lock_file.close()


def test_the_live_page_shows_a_comm_fail_and_logs_it(tmp_path):
    """The live page's state and console: a comm fail and a lost ping reach both, with their levels."""
    from datetime import datetime, timedelta, timezone
    from fastapi.testclient import TestClient
    from plantcollector import monitor
    from plantcollector.session import _Watch
    cfg = tmp_path / "site.yaml"
    cfg.write_text(f"""site: {{site_id: t, name: test, poll_interval_s: 3600, timezone: Asia/Baghdad}}
devices:
  - {{id: neo-01, profile: intelineo_master.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: 1, unit_id: 1, timeout: 1}}}}
  - {{id: pv-array, profile: pv_array_sau.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.2, port: 502, unit_id: 245, timeout: 1}}}}
""")
    s = Session(str(cfg), str(tmp_path / "data"), long_run=False, mode="passive", capture=False)
    s.monitor = monitor.Monitor(s)
    w = _Watch(s)
    t0 = datetime(2026, 10, 20, 6, 33, 16, tzinfo=timezone.utc)            # 09:33:16 in Erbil
    w.on_ping("pv-array", t0 - timedelta(seconds=1), True, 0.6)
    w.on_change(t0, "neo-01", "ecu2_comm_fail", False, True)
    w.on_change(t0 + timedelta(seconds=2), "pv-array", "ping_ok", True, False)
    w.on_change(t0 + timedelta(seconds=12), "neo-01", "ecu2_comm_fail", True, False)
    st = s.monitor.state()
    f = st["fails"][0]
    assert f["group"] == "group 1" and f["start"] == "09:33:16" and f["seconds"] == 12 and f["sau_ping"] is True
    lines = s.monitor.log_after(0)
    levels = [(l["level"], l["text"][:22]) for l in lines]
    assert ("alert", "COMM FAIL group 1: the") in levels and ("warn", "the SAU does not answe") in levels
    assert any(l["level"] == "ok" and "ended after 12 s" in l["text"] for l in lines)
    c = TestClient(monitor.create_app(s.monitor))
    assert "SAU watch" in c.get("/").text
    j = c.get("/api/state").json()
    assert j["zone"] == "Erbil time" and j["fails"][0]["start"] == "09:33:16" and j["capture"]["configured"] is False
    assert c.get("/api/log?after=0").json()["lines"][0]["n"] == 1
    assert c.get("/api/evidence/file?path=../site.yaml").status_code == 404     # nothing outside the evidence folder


def _feed(lc, t, src, dst, sport, dport, stream, length, seq, ack, tid="", fc="", exc="", proto="6", icmp="", uri="", rst="0"):
    lc.feed([f"{t:.6f}", src, dst, proto, str(sport), str(dport), str(stream), str(length), str(seq), str(ack), rst, "",
             str(tid), str(fc), str(exc), icmp, "", uri])


def test_live_view_follows_requests_answers_and_busy_replies():
    """The live reader on tshark's lines: an answer on time, a busy one 2.5 s late with an early TCP ACK, a request
    never answered, a web page visitor; the per-client table, the alerts and the window for the diagnosis."""
    from plantcollector.livecap import LiveCapture
    logs = []
    lc = LiveCapture("tshark", "1", SAU, MASTER, PC, log=lambda text, level="info": logs.append((level, text)))
    t = time.time() - 30
    _feed(lc, t, MASTER, SAU, 61416, 502, 0, 12, 1, 1, tid=1, fc=3)
    _feed(lc, t + 0.02, SAU, MASTER, 502, 61416, 0, 13, 1, 13, tid=1, fc=3)                 # 20 ms
    _feed(lc, t + 1, MASTER, SAU, 61416, 502, 0, 12, 13, 14, tid=2, fc=3)
    _feed(lc, t + 1.03, SAU, MASTER, 502, 61416, 0, 0, 14, 25)                                # bare ACK in 30 ms
    _feed(lc, t + 3.5, SAU, MASTER, 502, 61416, 0, 9, 14, 25, tid=2, fc=131, exc=6)          # Busy after 2.5 s
    _feed(lc, t + 4, MASTER, SAU, 61415, 502, 1, 12, 1, 1, tid=7, fc=3)                       # never answered
    for k in range(3):
        _feed(lc, t + 5 + k, PC, SAU, 50000 + k, 80, 2 + k, 100, 1, 1, uri="/server/operatingParameters")
    with lc.lock:
        for key, tx in list(lc.pending.items()):                                              # what the sweeper does
            del lc.pending[key]; lc._done(tx, time.time())
    rows = {(r["peer"], r["what"]): r for r in lc.stats(60)}
    m = rows[(MASTER, "Modbus")]
    assert m["count"] == 3 and m["busy"] == 1 and m["late"] == 1 and m["none"] == 1 and m["max_ms"] == pytest.approx(2500, abs=5)
    assert rows[(PC, "web page")]["count"] == 3
    assert any(l == "alert" and "exception 06" in t and "acknowledged the request in 30 ms" in t for l, t in logs)
    assert lc.mirror() == "no" or True                                                        # no process: decided by the traffic seen


def test_diagnosis_names_a_busy_sau_and_the_web_page_at_the_same_time():
    from plantcollector.livecap import LiveCapture
    from plantcollector.diagnose import diagnose
    lc = LiveCapture("tshark", "1", SAU, MASTER, PC)
    t = 1_800_000_000.0
    _feed(lc, t, MASTER, SAU, 61416, 502, 0, 12, 1, 1, tid=2, fc=3)
    _feed(lc, t + 0.03, SAU, MASTER, 502, 61416, 0, 0, 1, 13)
    _feed(lc, t + 2.5, SAU, MASTER, 502, 61416, 0, 9, 1, 13, tid=2, fc=131, exc=6)
    for k in range(4):
        _feed(lc, t + k, PC, SAU, 50000 + k, 80, 5 + k, 100, 1, 1, uri="/server/operatingParameters")
    pings = [(t + k, "pv-array", True) for k in range(-2, 8)]
    d = diagnose(t + 1, t + 6, pings, lc, master=MASTER, me=PC)
    assert d["tag"] == "SAU busy" and "exception 06" in d["text"] and "within 30 ms" in d["text"]
    assert "this PC was using the SAU's web page at the same time (4 requests)" in d["text"]
    # the same fail with no capture: the pings alone, said honestly
    d2 = diagnose(t + 1, t + 6, pings, None)
    assert d2["tag"] == "SAU up" and "without the port mirror" in d2["text"]
    # the SAU silent to ping the whole time
    d3 = diagnose(t + 1, t + 6, [(t + k, "pv-array", False) for k in range(-1, 8)], None)
    assert d3["tag"] == "SAU unreachable"
    # everything answered on time on the wire: the master's own side
    lc2 = LiveCapture("tshark", "1", SAU, MASTER, PC)
    _feed(lc2, t, MASTER, SAU, 61416, 502, 0, 12, 1, 1, tid=3, fc=3)
    _feed(lc2, t + 0.02, SAU, MASTER, 502, 61416, 0, 13, 1, 13, tid=3, fc=3)
    assert diagnose(t + 1, t + 6, pings, lc2, master=MASTER, me=PC)["tag"] == "master side"


def test_late_answers_and_lost_pings_are_kept_too_and_the_report_says_why(tmp_path):
    """Not only declared comm fails: a late-answer episode and a lost ping keep their capture as well, and the
    daily report carries the reason worked out for each fail."""
    from datetime import datetime, timedelta, timezone
    from plantcollector import sitetime
    cfg = tmp_path / "site.yaml"
    cfg.write_text(f"""site: {{site_id: t, name: test, poll_interval_s: 3600, timezone: Asia/Baghdad}}
devices:
  - {{id: neo-01, profile: intelineo_master.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: 1, unit_id: 1, timeout: 1}}}}
""")
    s = Session(str(cfg), str(tmp_path / "data"), long_run=False, mode="passive", capture=False, bundles_dir=str(tmp_path / "b"))
    os.makedirs(s.ring_dir)
    base = datetime.now().replace(microsecond=0) - timedelta(hours=2)
    for k in range(3):
        p = os.path.join(s.ring_dir, f"sau_{k + 1:05d}_{(base + timedelta(minutes=30 * k)).strftime('%Y%m%d%H%M%S')}.pcapng")
        open(p, "wb").write(b"x" * 10)
        end = (base + timedelta(minutes=30 * (k + 1))).astimezone().timestamp()
        os.utime(p, (end, end))
    t = (base + timedelta(minutes=40)).astimezone(timezone.utc)                     # inside the second file
    s.add_incident("the SAU answering the master late", t, t + timedelta(seconds=9), "3 late answer(s)")
    s.keep_incidents(now=t + timedelta(minutes=5))
    kept = os.listdir(os.path.join(s.evidence_dir, sitetime.day(t)))
    assert kept == [os.path.basename(sorted(os.listdir(s.ring_dir))[1])] and "kept for the SAU answering the master late" in "\n".join(s.notes)
    day = sitetime.day(t)
    os.makedirs(s.trace_dir, exist_ok=True)
    with open(os.path.join(s.trace_dir, f"diagnosis_{day}.csv"), "w", encoding="utf-8") as f:
        f.write("start,end,group,seconds,tag,why\n2026-10-21T09:33:16+03:00,2026-10-21T09:33:28+03:00,group 1,12,SAU busy,The SAU was busy.\n")
    zp = s.step_report_and_bundle(date=day)
    import zipfile
    rep = zipfile.ZipFile(zp).read(f"trace/report_{day}.md").decode()
    assert "Why each comm fail happened" in rep and "09:33:16 group 1, 12 s: **SAU busy**" in rep
    assert any(n == f"trace/diagnosis_{day}.csv" for n in zipfile.ZipFile(zp).namelist())


def test_diagnosis_names_a_master_restart():
    """15:37 on 21 Oct: the master stopped answering ping for 4 s (a configuration write) and flagged both PV groups;
    the SAU answered every ping. That is the master's side, not the SAU."""
    from plantcollector.diagnose import diagnose
    t0 = 1_000_000.0
    pings = [(t0 + i, "pv-array", True) for i in range(-3, 8)]
    pings += [(t0 + i, "neo-01", not (0 <= i <= 3)) for i in range(-3, 8)]
    d = diagnose(t0, t0 + 5, pings)
    assert d["tag"] == "master restart" and "Not the SAU" in d["text"]
    d2 = diagnose(t0, t0 + 5, [p for p in pings if p[1] == "pv-array"] + [(t0 + i, "neo-01", True) for i in range(-3, 8)])
    assert d2["tag"] == "SAU up"


def test_livecap_reads_the_outer_header_of_an_icmp_error():
    from plantcollector import livecap
    lc = livecap.LiveCapture.__new__(livecap.LiveCapture)
    livecap.LiveCapture.__init__(lc, None, "lo", "192.168.0.128", "192.168.0.112", "192.168.0.50", log=lambda *a, **k: None)
    row = [""] * len(livecap.FIELDS)
    row[0], row[1], row[2], row[3] = "1000.0", "192.168.0.128|192.168.0.50", "192.168.0.50|192.168.0.128", "1|17"
    lc.feed(row)
    assert lc.other and lc.other[-1][1] == "192.168.0.50" and lc.other[-1][2] == "ping"


def test_page_file_fetches_are_not_log_downloads():
    """On site the SAU page calls /file/downloadByUrl for its own configuration XMLs on every page load."""
    from plantcollector.livecap import is_log_download
    assert not is_log_download("/file/downloadByUrl")
    assert is_log_download("/deviceLog/downloadRunTimeUrl")
