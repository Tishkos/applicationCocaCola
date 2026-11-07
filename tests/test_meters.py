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
    def __init__(self, regs: dict, unit: int = 17, block_s: float = 3.0, idle_s: float = 30.0, strict: bool = False):
        self.regs, self.unit, self.block_s, self.idle_s = dict(regs), unit, block_s, idle_s
        self.strict = strict                     # a connection it ignored, when closed, starts its quiet time again
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
                    if serve or self.strict:
                        with self.lock:
                            self.client_closes += 1 if serve else 0
                            self.blocked_until = time.monotonic() + self.block_s
                    return
                buf += chunk
                while len(buf) >= 12:
                    tid, _proto, length, unit, fc, start, count = struct.unpack(">HHHBBHH", buf[:12])
                    buf = buf[6 + length:]
                    if not serve or unit not in (self.unit if isinstance(self.unit, (set, frozenset)) else {self.unit}) \
                            or fc not in (3, 4) or count > 125:
                        with self.lock:
                            self.ignored += 1
                        continue                 # like the real gateway: no answer at all
                    last = time.monotonic()
                    regs = self.regs.get(unit, self.regs) if isinstance(self.unit, (set, frozenset)) else self.regs
                    vals = [regs.get(a, 0x8000) for a in range(start, start + count)]
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
        real = [d for d in ("analyzer-1", "analyzer-2") if d in reg.devices and reg.devices[d].adapter == "modbus_tcp"]
        assert (len(real) == 2) == want and (want or not real), name
    demo = load_registry(os.path.join(ROOT, "config", "site.demo.yaml"))           # the demo models them instead
    assert [demo.devices[d].adapter for d in ("analyzer-1", "analyzer-2")] == ["simulator", "simulator"]
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
    """Accepted while the gateway still ignored new connections, it is never served: after first_answer_s without an
    answer the reader closes it, leaves the gateway alone for rest_s, and the next connection is served. Like the
    plant's gateways (strict), closing the ignored connection starts the gateway's quiet time again: opened at once,
    the new connection would be ignored too, and so would every one after it."""
    gw = FakeGateway(METER_1, block_s=1.0, strict=True)
    gw.blocked_until = time.monotonic() + 1.0
    c = Collector(_config(tmp_path, gw), str(tmp_path / "d"), initial_poll=False)
    try:
        from plantcollector.meters import MeterReader
        c.meters = MeterReader(c, {"every_s": 10, "first_answer_s": 0.8, "rest_s": 1.6})
        c.meters.cycle()                          # accepted during the quiet time: never served
        assert gw.accepted == 1 and gw.answered == 0
        time.sleep(1.0)
        c.meters.cycle()                          # still no answer, longer than first_answer_s: closed, the gateway rests
        assert gw.accepted == 1
        c.meters.cycle()                          # resting: no new connection yet
        assert gw.accepted == 1
        assert "takes a new connection in" in c.store._latest["analyzer-1"]["p_total_kw"].error
        time.sleep(1.8)
        c.meters.cycle()                          # after the rest: a new connection, and it is served
        assert gw.accepted == 2
        assert _values(c)["p_total_kw"] == (27, Quality.GOOD)
        assert any(e[0] == "meters" for e in c.store._db.execute("SELECT kind FROM events WHERE code='REOPEN'").fetchall())
    finally:
        c.close()
        gw.close()


def test_after_a_restart_the_reader_waits_for_the_gateway(tmp_path):
    """An update restarts the program: the old connection closes with it, and the gateway ignores a new one for a few
    minutes. The new reader waits until rest_s after the old connection was last open, so its first connection is
    served (1.0.9 opened one at once, then every few minutes another one, and every one fell in the quiet time)."""
    gw = FakeGateway(METER_1, block_s=1.0, strict=True)
    cfg = _config(tmp_path, gw, meters="meters: {every_s: 10, rest_s: 1.5}")
    c = Collector(cfg, str(tmp_path / "d"), initial_poll=False)
    try:
        assert c.start_meters() and _wait(lambda: c.meters.cycles >= 1)
        assert _values(c)["p_total_kw"] == (27, Quality.GOOD)
    finally:
        c.close()                                 # writes when the meter connection was last open
    from plantcollector.adapters.modbus_tcp import close_all
    close_all()                                   # the process ends: its connections close
    assert _wait(lambda: gw.client_closes == 1)
    c2 = Collector(cfg, str(tmp_path / "d"), initial_poll=False)
    try:
        assert c2.start_meters()
        assert c2.meters.status()["waiting_s"]    # it waits before connecting
        time.sleep(0.5)
        assert gw.accepted == 1                   # nothing opened during the gateway's quiet time
        assert _wait(lambda: c2.meters.cycles >= 2, s=8)
        assert _values(c2)["p_total_kw"] == (27, Quality.GOOD)
        assert gw.accepted == 2 and gw.ignored == 0
        assert c2.store._db.execute("SELECT COUNT(*) FROM events WHERE code='WAIT'").fetchone()[0] == 1
    finally:
        c2.close()
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
        monkeypatch.setattr(meters, "on_its_network", lambda host, *a, **k: False)
        c.meters = meters.MeterReader(c, {"every_s": 10})
        c.meters.cycle()
        assert gw.accepted == 0
        v, q = _values(c)["p_total_kw"]
        assert v is None and q == Quality.BAD and "not from its plant network" in c.store.latest("analyzer-1")["p_total_kw"].error
        assert c.meters.status()["routes"]["analyzer-1"]["ok"] is False
    finally:
        c.close()
        gw.close()


def test_the_meters_are_asked_through_the_plant_adapter(monkeypatch):
    """The plant PC reaches 192.168.141.x through its plant network adapter (a netmask wider than /24), with no
    address of its own in 192.168.141.x: the meters are asked. Through another adapter (the Wi-Fi) they are not."""
    from plantcollector import meters
    routes = {"192.168.141.3": "192.168.0.50", "192.168.0.10": "192.168.0.50", "10.0.0.9": "10.1.1.5"}
    monkeypatch.setattr(meters, "route_source", lambda host, port=502: routes.get(host))
    assert meters.on_its_network("192.168.141.3", {"192.168.0.50"})            # same adapter as the plant's devices
    assert not meters.on_its_network("192.168.141.3", {"10.1.1.5"})            # another adapter
    assert meters.on_its_network("192.168.141.3", set(), any_route=True)
    assert not meters.on_its_network("192.168.9.9", {"192.168.0.50"})          # no route at all
    routes["192.168.141.3"] = "192.168.141.20"
    assert meters.on_its_network("192.168.141.3", set())                       # an address in its own /24


def test_the_plant_adapter_is_found_from_the_other_devices(tmp_path, monkeypatch):
    gw = FakeGateway(METER_1)
    c = Collector(_config(tmp_path, gw), str(tmp_path / "d"), initial_poll=False)
    try:
        from plantcollector import meters
        c.meters = meters.MeterReader(c, {"every_s": 10})
        c.meters.cycle()
        assert c.meters.status()["routes"]["analyzer-1"] == {"from": "127.0.0.1", "ok": True}
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


def test_the_page_is_asked_again_after_an_update(tmp_path, gateway):
    from fastapi.testclient import TestClient
    from plantcollector.api import create_app
    c = Collector(_config(tmp_path, gateway), str(tmp_path / "d"), initial_poll=False, read_kept=False)
    try:
        api = TestClient(create_app(c))
        for path in ("/", "/static/i18n.js"):
            r = api.get(path)
            assert r.status_code == 200 and r.headers.get("cache-control") == "no-cache", path
        assert "no-cache" not in (api.get("/api/v1/health").headers.get("cache-control") or "")
    finally:
        c.close()


def test_the_window_empties_its_cache_once_per_version(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("desktop_app", os.path.join(ROOT, "app", "desktop.py"))
    desk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(desk)
    prog = tmp_path / "prog"
    prog.mkdir()
    (prog / "version.txt").write_text("1.0.8\n")
    monkeypatch.setattr(desk, "ROOT", str(prog))
    store = tmp_path / "appwindow"
    for d in ("EBWebView/Default/Cache/Cache_Data", "EBWebView/Default/Code Cache/js", "EBWebView/Default/Local Storage/leveldb"):
        (store / d).mkdir(parents=True)
    (store / "EBWebView/Default/Local Storage/leveldb/000003.log").write_text("pc.lang=ar")
    assert desk.fresh_cache(str(store)) is True
    assert not (store / "EBWebView/Default/Cache").exists() and not (store / "EBWebView/Default/Code Cache").exists()
    assert (store / "EBWebView/Default/Local Storage/leveldb/000003.log").exists()      # language and theme stay
    (store / "EBWebView/Default/Cache").mkdir()
    assert desk.fresh_cache(str(store)) is False                                        # once per version
    (prog / "version.txt").write_text("1.0.9\n")
    assert desk.fresh_cache(str(store)) is True


def test_read_devices_now_reads_the_meters_on_their_kept_connection(tmp_path, gateway):
    """The one "Read devices now" button: the site read and a cycle of the meter reader at once, in the reader's
    own thread (one connection to the gateway, as always)."""
    c = Collector(_config(tmp_path, gateway, meters="meters: {every_s: 240, store_every_s: 300}"), str(tmp_path / "d"),
                  initial_poll=False)
    try:
        assert c.start_meters()
        assert _wait(lambda: c.meters.cycles >= 1)
        time.sleep(3.2)                               # a click soon after a read waits for nothing
        n = c.meters.cycles
        gateway.regs[1142] = 31
        t0 = time.time()
        done = c.read_everything_now(wait_s=8)
        assert done == {"meters": True} and time.time() - t0 < 6
        assert c.meters.cycles == n + 1 and _values(c)["p_total_kw"] == (31, Quality.GOOD)
        assert gateway.accepted == 1                  # still the one kept connection
        assert c.read_everything_now(wait_s=8) == {}  # clicked again at once: no extra read of the meters
        from fastapi.testclient import TestClient
        from plantcollector.api import create_app
        client = TestClient(create_app(c))
        r = client.post("/api/v1/local/poll").json()
        assert r["ok"] and "readers" in r
    finally:
        c.close()


def test_a_meter_connection_is_opened_from_the_pcs_own_address_in_its_network(tmp_path, monkeypatch):
    """The plant PC has 192.168.141.50 on its plant cable (with four more addresses), and Windows sent the requests
    for 192.168.141.3 out through the Wi-Fi and the router, where they never connected: a meter connection is
    opened from the PC's one address in the meter's network."""
    from plantcollector.adapters import modbus_tcp
    monkeypatch.setattr(modbus_tcp, "own_addresses", lambda: {"192.168.200.253", "192.168.141.50", "192.168.101.50",
                                                               "192.168.0.129", "192.168.0.50", "192.168.0.100"})
    assert modbus_tcp.own_address_in("192.168.141.3") == "192.168.141.50"
    assert modbus_tcp.own_address_in("192.168.0.20") is None            # three addresses there: Windows chooses
    assert modbus_tcp.own_address_in("10.0.0.1") is None
    from plantcollector.registry import Device
    dev = Device(id="analyzer-9", name="x", kind="analyzer", vendor="v", model="m", adapter="modbus_tcp",
                 connection={"host": "192.168.141.3", "port": 502, "unit_id": 1, "keep_open": True}, points=[])
    ad = modbus_tcp.ModbusTcpAdapter(dev)
    assert ad.pick_source() == "192.168.141.50"
    assert ad._client.comm_params.source_address == ("192.168.141.50", 0)
    dev2 = Device(id="analyzer-8", name="x", kind="analyzer", vendor="v", model="m", adapter="modbus_tcp",
                  connection={"host": "192.168.141.4", "port": 502, "unit_id": 1, "keep_open": True, "source_address": "192.168.141.60"}, points=[])
    assert modbus_tcp.ModbusTcpAdapter(dev2).pick_source() == "192.168.141.60"
