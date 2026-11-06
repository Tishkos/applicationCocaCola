"""The two power meters behind the serial gateways (1.0.7): decoding, the kept connection, the added devices.

The fake gateway below behaves like the "GATEWAY TR" meter modems seen at the plant on 2026-11-06: it serves one
connection at a time, ignores any other connection while one is served, ignores new connections for a while after
the served one closed, and drops a connection that stays idle too long. The registers are the ones read from the
meter behind 192.168.141.1 that night.
"""

import os
import socket
import struct
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from plantcollector import views  # noqa: E402
from plantcollector.adapters import modbus_tcp  # noqa: E402
from plantcollector.adapters.modbus_tcp import _decode  # noqa: E402
from plantcollector.core import Collector  # noqa: E402
from plantcollector.registry import Quality, added_devices, load_registry  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# meter behind 192.168.141.1 (unit 17), 2026-11-06 02:35 plant time; everything else reads 0x8000
METER_1 = {
    1099: 40, 1100: 37, 1101: 40, 1102: 10, 1103: 0x8000, 1104: 39,
    1119: 418, 1120: 416, 1121: 415, 1122: 416, 1123: 241, 1124: 241, 1125: 240, 1126: 0, 1127: 240,
    1139: 9, 1140: 9, 1141: 9, 1142: 27, 1143: 2, 1144: 2, 1145: 2, 1146: 5, 1147: 10, 1148: 9, 1149: 9, 1150: 28,
    1159: 985, 1160: 975, 1161: 981, 1162: 981, 1179: 5081,
    1699: 9513, 1700: 2235, 1701: 29, 1702: 0, 1703: 771, 1704: 4974, 1705: 2, 1706: 0,
}


class FakeGateway:
    def __init__(self, regs: dict, unit: int = 17, block_s: float = 3.0, idle_s: float = 30.0):
        self.regs, self.unit, self.block_s, self.idle_s = dict(regs), unit, block_s, idle_s
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.lock = threading.Lock()
        self.serving = None                      # the connection being served
        self.blocked_until = 0.0
        self.accepted = 0
        self.answered = 0
        self.ignored = 0
        self.client_closes = 0                   # served connections the client closed
        self.idle_drops = 0                      # served connections this gateway dropped for being idle
        self.stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self.stop = True
        try:
            self.sock.close()
        except OSError:
            pass

    def _accept(self):
        while not self.stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with self.lock:
                self.accepted += 1
                serve = self.serving is None and time.monotonic() >= self.blocked_until
                if serve:
                    self.serving = conn
            threading.Thread(target=self._session, args=(conn, serve), daemon=True).start()

    def _session(self, conn, serve):
        conn.settimeout(0.2)
        buf, last = b"", time.monotonic()
        try:
            while not self.stop:
                if serve and time.monotonic() - last > self.idle_s:
                    with self.lock:
                        self.idle_drops += 1
                    return                       # dropped by the gateway: no block afterwards
                try:
                    chunk = conn.recv(512)
                except socket.timeout:
                    continue
                except OSError:
                    chunk = b""
                if not chunk:
                    if serve:
                        with self.lock:
                            self.client_closes += 1
                            self.blocked_until = time.monotonic() + self.block_s
                    return
                buf += chunk
                while len(buf) >= 12:
                    tid, _proto, length, unit, fc, start, count = struct.unpack(">HHHBBHH", buf[:12])
                    buf = buf[6 + length:]
                    if not serve or unit != self.unit or fc not in (3, 4) or count > 125:
                        with self.lock:
                            self.ignored += 1
                        continue                 # like the real gateway: no answer at all
                    last = time.monotonic()
                    vals = [self.regs.get(a, 0x8000) for a in range(start, start + count)]
                    pdu = struct.pack(">BB", fc, 2 * count) + b"".join(struct.pack(">H", v) for v in vals)
                    conn.sendall(struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu)
                    with self.lock:
                        self.answered += 1
        finally:
            with self.lock:
                if self.serving is conn:
                    self.serving = None
            try:
                conn.close()
            except OSError:
                pass


def _config(tmp_path, gw: FakeGateway, extra_devices: str = "", meters: str = "meters: {every_s: 10, store_every_s: 300}"):
    cfg = tmp_path / "site.plant.yaml"
    cfg.write_text(f"""
site: {{site_id: t, poll_interval_s: 600}}
{meters}
devices:
  - {{id: analyzer-1, profile: pm800_meter.yaml, adapter: modbus_tcp,
      connection: {{host: 127.0.0.1, port: {gw.port}, unit_id: 17, timeout: 1, max_block: 16, keep_open: true}}}}
{extra_devices}
""")
    return str(cfg)


@pytest.fixture()
def gateway():
    gw = FakeGateway(METER_1)
    yield gw
    gw.close()


def _wait(cond, s=10.0):
    t0 = time.time()
    while time.time() - t0 < s:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _values(c, did="analyzer-1"):
    return {pid: (r.value, r.quality) for pid, r in c.store.latest(did).items()}


# ------------------------------------------------------------------ decoding

def test_mod10_counter():
    assert _decode([9513, 2235, 29, 0], "mod10x4", 0.001, "big") == pytest.approx(2922359.513)
    assert _decode([2190, 2733, 22, 0], "mod10x4", 0.001, "big") == pytest.approx(2227332.19)
    with pytest.raises(ValueError):
        _decode([10000, 0, 0, 0], "mod10x4", 1, "big")


def test_profile_blocks_fit_the_gateway():
    reg = load_registry(os.path.join(ROOT, "config", "site.plant.yaml"))
    from plantcollector.adapters.modbus_tcp import ModbusTcpAdapter
    ad = ModbusTcpAdapter(reg.devices["analyzer-1"])
    assert ad.max_block == 16
    assert all(count <= 16 for _fn, _start, count, _pts in ad._blocks)
    assert len(ad._blocks) <= 8                   # a handful of requests a minute per meter


# ------------------------------------------------------------------ the kept connection

def test_values_on_one_kept_connection(tmp_path, gateway):
    c = Collector(_config(tmp_path, gateway), str(tmp_path / "d"), initial_poll=False)
    try:
        assert c.start_meters()
        assert _wait(lambda: c.meters.cycles >= 1)
        v = _values(c)
        assert v["p_total_kw"] == (27, Quality.GOOD)
        assert v["i_l1_a"][0] == 40 and v["i_n_a"][0] == 10 and v["i_avg_a"][0] == 39
        assert v["v_ll_avg_v"][0] == 416 and v["v_ln_avg_v"][0] == 240
        assert v["pf_total"][0] == pytest.approx(0.981)
        assert v["f_hz"][0] == pytest.approx(50.81)
        assert v["e_in_kwh"][0] == pytest.approx(2922359.513)
        assert v["e_out_kwh"][0] == pytest.approx(249740.771)
        assert v["comm_error"][0] is False
        # the site read leaves the meter to its reader and does not close the kept connection
        c.poll()
        c.meters.cycle()
        assert gateway.accepted == 1 and gateway.client_closes == 0 and gateway.ignored == 0
        assert _values(c)["p_total_kw"] == (27, Quality.GOOD)
        assert c.device_every_s("analyzer-1") == 10
        # the cards, and Settings > Reading the devices has no rate to choose for it
        s = views.summary(c)
        card = next(d for d in s["devices"] if d["id"] == "analyzer-1")
        assert card["kind"] == "analyzer" and card["comms"] == "ok"
        assert s["meters"]["running"] and s["meters"]["devices"] == ["analyzer-1"]
        assert all("analyzer-1" not in g["devices"] for g in views.reading_settings(c)["groups"])
    finally:
        c.close()


def test_stores_every_few_minutes_not_every_read(tmp_path, gateway):
    c = Collector(_config(tmp_path, gateway), str(tmp_path / "d"), initial_poll=False)
    try:
        from plantcollector.meters import MeterReader
        c.meters = MeterReader(c, {"every_s": 10, "store_every_s": 300})
        for _ in range(3):
            c.meters.cycle()
        rows = c.store._db.execute("SELECT COUNT(*) FROM readings WHERE device_id='analyzer-1' AND point_id='p_total_kw'").fetchone()[0]
        assert rows == 1                          # stored once; the latest value is still every read's
        assert c.meters.cycles == 3
    finally:
        c.close()


def test_a_dropped_connection_comes_back(tmp_path):
    gw = FakeGateway(METER_1, idle_s=0.5)
    c = Collector(_config(tmp_path, gw), str(tmp_path / "d"), initial_poll=False)
    try:
        from plantcollector.meters import MeterReader
        c.meters = MeterReader(c, {"every_s": 10})
        c.meters.cycle()
        assert _values(c)["p_total_kw"] == (27, Quality.GOOD)
        time.sleep(1.0)                           # the gateway drops the idle connection
        assert _wait(lambda: gw.idle_drops == 1, 3)
        for _ in range(3):
            c.meters.cycle()
            if _values(c)["comm_error"][0] is False and all(q == Quality.GOOD for pid, (v, q) in _values(c).items() if pid in ("p_total_kw", "f_hz")):
                break
        assert _values(c)["p_total_kw"] == (27, Quality.GOOD)
        assert gw.client_closes == 0              # this program never closed a served connection itself
    finally:
        c.close()
        gw.close()


def test_without_the_reader_the_site_read_still_reads_it(tmp_path, gateway):
    """One-off reads (CHECK-PLANT): no meter reader, so the site read reads the meter and closes afterwards."""
    c = Collector(_config(tmp_path, gateway), str(tmp_path / "d"), initial_poll=False)
    try:
        c.poll()
        assert _values(c)["p_total_kw"] == (27, Quality.GOOD)
    finally:
        c.close()


def test_no_value_code_is_not_a_number(tmp_path):
    regs = dict(METER_1)
    regs[1162] = 0x8000                           # no load: the meter has no power factor
    gw = FakeGateway(regs)
    c = Collector(_config(tmp_path, gw), str(tmp_path / "d"), initial_poll=False)
    try:
        c.poll()
        v, q = _values(c)["pf_total"]
        assert v is None and q == Quality.BAD
        assert _values(c)["p_total_kw"] == (27, Quality.GOOD)
        assert views.device_comms(c, "analyzer-1") == "ok"
    finally:
        c.close()
        gw.close()


# ------------------------------------------------------------------ settings and totals

def _cfg(**over):
    base = {"site": {"site_id": "erbil-cc-01"},
            "devices": [{"id": "neo-01", "adapter": "modbus_tcp", "connection": {"host": "192.168.0.112", "unit_id": 1}}]}
    base.update(over)
    return base


def test_added_devices_reach_an_installed_plant_config():
    more, blocks = added_devices(_cfg())
    assert [e["id"] for e in more] == ["analyzer-1", "analyzer-2"]
    assert 10 <= blocks["meters"]["every_s"] <= 240
    # already there, under its id or under another id with the same host and unit: not added again
    have = _cfg(devices=_cfg()["devices"] + [{"id": "my-meter", "adapter": "modbus_tcp", "connection": {"host": "192.168.141.1", "unit_id": 17}}])
    assert [e["id"] for e in added_devices(have)[0]] == ["analyzer-2"]
    assert [e["id"] for e in added_devices(_cfg(skip_added_devices=["analyzer-2"]))[0]] == ["analyzer-1"]
    assert "meters" not in added_devices(_cfg(meters={"every_s": 30}))[1]
    # not for another site, and not for the demo
    assert added_devices(_cfg(site={"site_id": "elsewhere"})) == ([], {})
    demo = _cfg(devices=[{"id": "neo-01", "adapter": "simulator"}])
    assert added_devices(demo) == ([], {})


def test_the_plant_configs_get_the_meters_the_demos_do_not():
    for name, want in (("site.plant.yaml", True), ("site.eth1.yaml", False), ("site.demo.yaml", False), ("site.yaml", False)):
        reg = load_registry(os.path.join(ROOT, "config", name))
        assert ("analyzer-1" in reg.devices and "analyzer-2" in reg.devices) == want, name
    reg = load_registry(os.path.join(ROOT, "config", "site.plant.yaml"))
    a1, a2 = reg.devices["analyzer-1"].connection, reg.devices["analyzer-2"].connection
    assert (a1["host"], a1["unit_id"], a2["host"], a2["unit_id"]) == ("192.168.141.1", 17, "192.168.141.3", 1)
    assert a1["keep_open"] and a2["keep_open"]


def test_meters_never_count_into_the_site_totals():
    reg = load_registry(os.path.join(ROOT, "config", "site.plant.yaml"))
    assert reg.by_kind("meter") == []             # the site load stays the master's
    specs = views.series_specs(reg)
    # the meters' own trends are keyed by the meter; nothing site-wide takes them in
    assert all(k.startswith("analyzer-") for k, (_a, prs) in specs.items() if any(d.startswith("analyzer") for d, _p in prs))
    for key in ("load_kw", "frequency_hz", "voltage_v", "mains_kw", "genset_kw", "pv_kw"):
        assert all(not d.startswith("analyzer") for d, _p in specs.get(key, ("", []))[1]), key


def test_source_address_only_on_its_own_network():
    try:
        modbus_tcp.set_source_address("192.168.0.200")
        far = modbus_tcp.shared_client("192.168.141.250", 15999, 1)
        near = modbus_tcp.shared_client("192.168.0.250", 15999, 1)
        assert far.comm_params.source_address in (None, ("", 0))
        assert near.comm_params.source_address == ("192.168.0.200", 0)
    finally:
        modbus_tcp.set_source_address(None)
        for key in (("192.168.141.250", 15999), ("192.168.0.250", 15999)):
            modbus_tcp._POOL.pop(key, None)


def test_a_gateway_that_ignores_the_connection_gets_one_request_a_cycle(tmp_path):
    gw = FakeGateway(METER_1, block_s=60)
    gw.blocked_until = time.monotonic() + 60      # as right after a restart: the old connection just closed
    c = Collector(_config(tmp_path, gw), str(tmp_path / "d"), initial_poll=False)
    try:
        from plantcollector.meters import MeterReader
        c.meters = MeterReader(c, {"every_s": 10})
        c.meters.cycle()
        assert gw.ignored == 1                    # the first block got no answer; the others waited for the next cycle
        v = _values(c)
        assert v["comm_error"][0] is True and v["p_total_kw"][1] == Quality.BAD
        assert "analyzer-1" not in c._comms_lost  # "no data" only after a few cycles in a row
    finally:
        c.close()
        gw.close()


def test_a_connection_that_is_never_served_is_opened_again(tmp_path):
    """Accepted while the gateway still ignored new connections, it is never served: after reopen_after_s without an
    answer the reader closes it, and the next connection (after the gateway's quiet time) is served."""
    gw = FakeGateway(METER_1, block_s=60)
    gw.blocked_until = time.monotonic() + 1.0
    c = Collector(_config(tmp_path, gw), str(tmp_path / "d"), initial_poll=False)
    try:
        from plantcollector.meters import MeterReader
        c.meters = MeterReader(c, {"every_s": 10})
        c.meters.reopen_after_s = 0.8
        c.meters.cycle()                          # accepted during the quiet time: never served
        assert gw.accepted == 1 and gw.answered == 0
        time.sleep(1.2)
        c.meters.cycle()                          # still no answer on it, and quiet for longer than reopen_after_s: closed
        c.meters.cycle()                          # a new connection, now served
        assert gw.accepted == 2
        assert _values(c)["p_total_kw"] == (27, Quality.GOOD)
        assert any(e[0] == "meters" for e in c.store._db.execute("SELECT kind FROM events WHERE code='REOPEN'").fetchall())
    finally:
        c.close()
        gw.close()


def test_one_off_tools_leave_the_kept_meters_to_the_service(tmp_path, gateway):
    c = Collector(_config(tmp_path, gateway), str(tmp_path / "d"), initial_poll=True, read_kept=False)
    try:
        assert gateway.accepted == 0              # check / export / report: no second connection to a meter gateway
        assert c.store.latest("analyzer-1") == {}
    finally:
        c.close()


def test_a_pc_without_an_address_in_the_meters_network_does_not_ask(tmp_path, monkeypatch):
    gw = FakeGateway(METER_1)
    c = Collector(_config(tmp_path, gw), str(tmp_path / "d"), initial_poll=False)
    try:
        from plantcollector import meters
        monkeypatch.setattr(meters, "on_its_network", lambda host: False)
        c.meters = meters.MeterReader(c, {"every_s": 10})
        c.meters.cycle()
        assert gw.accepted == 0
        v, q = _values(c)["p_total_kw"]
        assert v is None and q == Quality.BAD and "no address" in c.store.latest("analyzer-1")["p_total_kw"].error
    finally:
        c.close()
        gw.close()


def test_the_setup_ships_the_added_devices(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_runtime", os.path.join(ROOT, "installer", "build_runtime.py"))
    br = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(br)
    monkeypatch.setattr(br, "STAGE", str(tmp_path / "stage"))
    br.copy_program("9.9.9")
    stage = tmp_path / "stage"
    assert (stage / "config" / "added-devices.yaml").exists() and (stage / "profiles" / "pm800_meter.yaml").exists()
    assert added_devices(_cfg(), path=str(stage / "config" / "added-devices.yaml"))[0]


def test_skip_list_and_other_settings_files():
    assert [e["id"] for e in added_devices(_cfg(skip_added_devices="analyzer-2"))[0]] == ["analyzer-1"]
    assert added_devices(_cfg(), config_name="site.eth1.yaml") == ([], {})
