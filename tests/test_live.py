"""The live reader (master every few seconds, dashboard only) against a fake plant:
the Turkey feed changes only at the site read, the load on every device, the database, the master's
comm-fail bits and the guard that slows a device down, the back-off when the master is gone."""

import json
import struct
import sys
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pymodbus.client import ModbusTcpClient

sys.path.insert(0, os.path.dirname(__file__))
from fakeplant import FakeHost, free_port  # noqa: E402

from plantcollector.api import create_app  # noqa: E402
from plantcollector.core import Collector  # noqa: E402
from plantcollector.publish import ModbusPublisher  # noqa: E402

NEO_UNIT = 1


def plant(tmp_path, neo, sau, mains, *, live=True, extra=""):
    cfg = tmp_path / "site.plant.yaml"
    neo_port = neo.port if neo else free_port()
    cfg.write_text(f"""
site: {{site_id: t, poll_interval_s: 600}}
live:
  enabled: {str(live).lower()}
  devices: [neo-01]
  every_s: 1
  max_requests_per_s: 3
  store_every_s: 60
  links:
    ecu1_comm_fail: [MAIN 1 (IM210 G2), main-01]
    ecu2_comm_fail: [PV logger group 1, pv-array]
  back_off: [main-01]
{extra}
devices:
  - {{id: neo-01, profile: intelineo_master.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: {neo_port}, unit_id: 1, timeout: 1}}}}
  - {{id: inv-01, profile: inverter_sineng.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: {sau.port}, unit_id: 3, timeout: 1}}}}
  - {{id: pv-array, profile: pv_array_sau.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: {sau.port}, unit_id: 245, timeout: 1}}}}
  - {{id: main-01, profile: im210_mains.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: {mains.port}, unit_id: 1, timeout: 1}}}}
""")
    return Collector(str(cfg), str(tmp_path / "data"), initial_poll=False)


@pytest.fixture()
def hosts():
    neo = FakeHost([NEO_UNIT]).start()
    sau = FakeHost([3, 245]).start()
    mains = FakeHost([1]).start()
    neo.set(NEO_UNIT, 1040, 500)          # PV Actual P 500 kW  (Turkey tag Psum3)
    neo.set32(NEO_UNIT, 1068, 1200)       # Gensets P 1200 kW
    neo.set(NEO_UNIT, 1085, 300)          # Mains Import P 300 kW
    neo.set(NEO_UNIT, 1227, 0b00011)      # Gen 1 and Gen 2 loaded
    mains.set(1, 1001, 310)
    yield neo, sau, mains
    for h in (neo, sau, mains):
        h.stop()


def wait_cycles(c, n, timeout=15):
    start = c.live.cycles
    t0 = time.time()
    while c.live.cycles < start + n and time.time() - t0 < timeout:
        time.sleep(0.05)
    assert c.live.cycles >= start + n, "the live reader did not run"


def turkey_tag(cl, pub, name):
    m = next(t for t in pub.map if t["name"] == name)
    regs = cl.read_holding_registers(address=m["address"], count=2, device_id=1).registers
    q = cl.read_holding_registers(address=m["quality_address"], count=1, device_id=1).registers[0]
    return round(struct.unpack(">f", struct.pack(">HH", *regs))[0], 3), q


def heartbeat(cl):
    return cl.read_holding_registers(address=0, count=7, device_id=1).registers[6]


def test_turkey_values_change_only_at_the_site_read(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    pub = ModbusPublisher(c, bind="127.0.0.1", port=free_port(), unit_id=1)
    pub.start()
    try:
        assert c.start_live()
        wait_cycles(c, 1)
        c.poll()                                              # site read -> Turkey feed
        cl = ModbusTcpClient("127.0.0.1", port=pub.port)
        assert cl.connect()
        api = TestClient(create_app(c))
        assert turkey_tag(cl, pub, "Psum3") == (500.0, 1)
        hb = heartbeat(cl)
        assert api.get("/api/v1/tags/Psum3").json()["value"] == 500

        neo.set(NEO_UNIT, 1040, 777)                          # the plant changes ...
        wait_cycles(c, 2)
        assert c.store.latest("neo-01")["pv_prod_kw"].value == 777          # ... the dashboard sees it at once
        assert api.get("/api/v1/local/summary").json()["kpi"]["pv_kw"] == 777
        pub.refresh()                                         # the publisher's one-minute tick
        assert turkey_tag(cl, pub, "Psum3") == (500.0, 1)     # ... Turkey does not, until the next site read
        assert heartbeat(cl) == hb
        assert api.get("/api/v1/tags/Psum3").json()["value"] == 500
        assert api.get("/api/v1/tags").json()["tags"][[t["name"] for t in pub.map].index("Psum3")]["value"] == 500

        c.poll()                                              # the next site read
        assert turkey_tag(cl, pub, "Psum3") == (777.0, 1)
        assert heartbeat(cl) == hb + 1
        assert api.get("/api/v1/tags/Psum3").json()["value"] == 777
        cl.close()
    finally:
        c.close()
        pub.stop()


def test_load_on_the_devices(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    try:
        c.start_live()
        wait_cycles(c, 1)
        r0, cyc0 = neo.total_requests(), c.live.cycles
        t0 = time.time()
        c.poll()                                              # site read while live: the master is not read again
        assert neo.total_requests() - r0 <= 3 * (c.live.cycles - cyc0 + 1)
        assert sau.total_requests() > 0 and mains.total_requests() > 0
        wait_cycles(c, 4)
        dt = time.time() - t0
        assert neo.connects == 1 and neo.max_open == 1        # one kept connection, never opened again
        per_cycle = c.live.requests_per_cycle()
        assert per_cycle == 3                                 # three block reads of the master per cycle
        assert (neo.total_requests() - r0) / dt <= 3.5        # never more than max_requests_per_s (3 here)
        time.sleep(0.3)
        assert sau.open_now == 0 and mains.open_now == 0      # the site read closes its connections
        assert neo.open_now == 1                              # the live one stays
    finally:
        c.close()


def test_the_live_rate_slows_down_when_a_cycle_needs_more_requests(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    try:
        ad = c.adapters["neo-01"]
        ad._blocks = [b for blk in ad._blocks for b in ad._split_runs(blk[0], blk[3])]   # as after an exception 4
        n = len(ad._blocks)
        assert n > 6
        c.start_live()
        wait_cycles(c, 1)
        assert c.live.every_s == pytest.approx(round(n / 3, 1))
    finally:
        c.close()


def test_live_values_are_stored_sparingly(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    try:
        c.start_live()
        wait_cycles(c, 4)
        rows = lambda pid: c.store._read("SELECT COUNT(*) FROM readings WHERE device_id='neo-01' AND point_id=?", (pid,))[0][0]
        assert rows("pv_prod_kw") == 1                        # numbers: once a minute (once at the start here)
        assert rows("g1_running") == 1
        c.live.state_gap_s = 0                                # (on/off signals: at most every 10 s on the plant)
        neo.set(NEO_UNIT, 1227, 0b00001)                      # Gen 2 leaves the bus: stored at once
        wait_cycles(c, 2)
        assert rows("g2_running") == 2 and rows("g1_running") == 1
        assert c.store.latest("neo-01")["g2_running"].value is False
    finally:
        c.close()


def test_master_link_fail_after_our_read_slows_that_device_down(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    try:
        c.start_live()
        wait_cycles(c, 1)
        events = lambda code: [e for e in c.store.events(datetime.now(timezone.utc) - timedelta(hours=1), 100) if e["code"] == code]

        neo.set(NEO_UNIT, 1268, 0b10)                         # PV logger group 1 fails, no read of ours before it
        wait_cycles(c, 2)
        fails = events("MASTER_LINK_FAIL")
        assert fails and "PV logger group 1 comm fail on the master" in fails[0]["message"]
        assert "after this program read" not in fails[0]["message"]
        neo.set(NEO_UNIT, 1268, 0)
        wait_cycles(c, 2)
        assert any("PV logger group 1 comm back" in e["message"] for e in events("MASTER_LINK_OK"))

        for n in (1, 2):                                      # twice: MAIN 1 lost right after our read of it
            c.poll()
            neo.set(NEO_UNIT, 1268, 0b1)
            wait_cycles(c, 2)
            neo.set(NEO_UNIT, 1268, 0)
            wait_cycles(c, 2)
        msgs = [e["message"] for e in events("MASTER_LINK_FAIL") if "MAIN 1" in e["message"]]
        assert len(msgs) == 2 and all("after this program read main-01" in m for m in msgs)
        assert c.every_override["main-01"] == 3600 and c.device_every_s("main-01") == 3600
        assert events("READ_SLOWED")
        saved = json.load(open(c.settings_path))
        assert saved["slowed_devices"] == {"main-01": 3600}
        before = mains.total_requests()
        c.poll()                                              # main-01 not due for an hour: not read
        assert mains.total_requests() == before
        c2 = plant(tmp_path, neo, sau, mains, live=False)     # remembered across a restart
        assert c2.every_override["main-01"] == 3600
    finally:
        c.close()


def test_master_gone_backs_off(tmp_path, hosts):
    _neo, sau, mains = hosts
    c = plant(tmp_path, None, sau, mains)                     # nothing listens on the master's port
    try:
        c.start_live()
        time.sleep(6.5)
        assert 2 <= c.live.cycles <= 4                        # 1 s, then 2 s, then 4 s ... not every second
        assert c.live.failures_in_row >= 2 and c.live.status()["backing_off"]
        assert "neo-01" in c._comms_lost
        assert c.live.running                                 # backing off on purpose is still running
    finally:
        c.close()


def test_turkey_quality_turns_stale_when_the_site_read_stops(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains, live=False)
    pub = ModbusPublisher(c, bind="127.0.0.1", port=free_port(), unit_id=1)
    pub.start()
    try:
        c.poll()
        cl = ModbusTcpClient("127.0.0.1", port=pub.port)
        assert cl.connect()
        assert turkey_tag(cl, pub, "Psum3")[1] == 1
        old = datetime.now(timezone.utc) - timedelta(seconds=2 * 600 + 61)
        for did in c.site_snapshot:
            for r in c.site_snapshot[did].values():
                r.source_ts = old
        pub.refresh()
        assert turkey_tag(cl, pub, "Psum3") == (500.0, 2)     # value kept, quality 2 = stale (docs/TURKEY_MODBUS.md)
        assert TestClient(create_app(c)).get("/api/v1/tags/Psum3").json()["quality"] == "stale"
        cl.close()
    finally:
        c.close()
        pub.stop()


def test_live_endpoint_and_summary(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    try:
        c.start_live()
        wait_cycles(c, 2)
        api = TestClient(create_app(c))
        lv = api.get("/api/v1/local/live").json()
        assert lv["running"] and lv["every_s"] == 1
        t, load, pv, gen, grid = lv["now"]
        assert (pv, gen, grid) == (500, 1200, 300) and load == 2000
        assert len(lv["history"]) >= 2
        assert set(lv["links"]) == {"ecu1_comm_fail", "ecu2_comm_fail"}
        s = api.get("/api/v1/local/summary").json()
        assert s["live"]["running"] and s["kpi"]["mains_kw"] == 300   # the master's Mains Import P, live
        dev = {d["id"]: d for d in s["devices"]}
        assert dev["neo-01"]["live"] and dev["neo-01"]["every_s"] == 1 and dev["inv-01"]["every_s"] == 600
    finally:
        c.close()


def test_source_address_is_used(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains, live=False, extra="network: {source_address: 127.0.0.1}")
    try:
        cl = c.adapters["neo-01"]._client
        assert getattr(getattr(cl, "comm_params", None), "source_address", None) == ("127.0.0.1", 0)
        c.poll()
        assert c.store.latest("neo-01")["pv_prod_kw"].value == 500
    finally:
        from plantcollector.adapters import modbus_tcp
        modbus_tcp.set_source_address(None)
        c.close()


# ---------------------------------------------------------------- from the independent review

def test_turkey_shows_nothing_from_the_live_reader_before_the_first_site_read(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    pub = ModbusPublisher(c, bind="127.0.0.1", port=free_port(), unit_id=1)
    pub.start()
    try:
        c.start_live()
        wait_cycles(c, 2)
        assert c.store.latest("neo-01")["pv_prod_kw"].value == 500       # the dashboard has it ...
        pub.refresh()                                                   # ... the one-minute tick before any site read
        cl = ModbusTcpClient("127.0.0.1", port=pub.port)
        assert cl.connect()
        assert turkey_tag(cl, pub, "Psum3")[1] == 0                     # ... Turkey does not: no data yet, as always
        assert TestClient(create_app(c)).get("/api/v1/tags/Psum3").json()["value"] is None
        c.poll()
        assert turkey_tag(cl, pub, "Psum3") == (500.0, 1)
        cl.close()
    finally:
        c.close()
        pub.stop()


def test_a_refused_block_costs_no_new_connection_and_no_back_off(tmp_path, hosts):
    neo, sau, mains = hosts
    neo.refuse[(NEO_UNIT, 1256)] = 2                  # the controller does not have one of the blocks (exception 2)
    c = plant(tmp_path, neo, sau, mains)
    try:
        c.start_live()
        wait_cycles(c, 5)
        assert neo.connects == 1                      # answered at once: the link is fine, no reconnect
        assert c.live.failures_in_row == 0 and not c.live.status()["backing_off"]
        assert c.store.latest("neo-01")["pv_prod_kw"].value == 500
    finally:
        c.close()


def test_busy_answers_back_off_without_reconnecting(tmp_path, hosts):
    neo, sau, mains = hosts
    neo.refuse[(NEO_UNIT, 1148)] = 6                  # "device busy" on one block, every time
    c = plant(tmp_path, neo, sau, mains)
    try:
        c.start_live()
        time.sleep(7)
        assert neo.connects == 1
        assert c.live.failures_in_row >= 2 and c.live.cycles <= 4     # 1 s, 2 s, 4 s ... not every second
        before = neo.total_requests()
        c.poll()                                      # while the live reader backs off, the site read reads the master
        assert neo.total_requests() > before
    finally:
        c.close()


def test_a_flapping_block_is_stored_at_most_once_a_minute_and_raises_no_event_storm(tmp_path, hosts):
    neo, sau, mains = hosts
    neo.flap.add((NEO_UNIT, 1148))                    # busy on every other request
    c = plant(tmp_path, neo, sau, mains, extra="  idle_store_s: 300")
    try:
        c.start_live()
        c.live.failures_in_row = 0
        time.sleep(8)
        rows = c.store._read("SELECT COUNT(*) FROM readings WHERE device_id='neo-01' AND point_id='battery_v'")[0][0]
        assert rows <= 2
        events = [e for e in c.store.events(datetime.now(timezone.utc) - timedelta(hours=1), 100) if e["code"] in ("COMMS_LOST", "COMMS_OK")]
        assert not events                             # part of the master answers: not "no data"
    finally:
        c.close()


def test_states_are_stored_when_they_change(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    try:
        c.start_live()
        sp = c.live.state_points["neo-01"]
        assert {"breaker_state", "controller_mode", "can_present_mask", "g1_running", "eth_status"} <= sp
        assert not {"pv_prod_kw", "pv_pf", "bus_frequency_hz", "load_kw"} & sp
        c.live.state_gap_s = 0
        neo.set(NEO_UNIT, 1192, 3)
        wait_cycles(c, 2)
        neo.set(NEO_UNIT, 1192, 7)
        wait_cycles(c, 2)
        neo.set(NEO_UNIT, 1192, 3)
        wait_cycles(c, 2)
        vals = [v for (v,) in c.store._read("SELECT value FROM readings WHERE device_id='neo-01' AND point_id='breaker_state' ORDER BY ts")]
        assert vals[-3:] == [3.0, 7.0, 3.0]
    finally:
        c.close()


def test_the_guard_does_not_blame_us_for_a_link_that_drops_on_its_own(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    try:
        c.start_live()
        wait_cycles(c, 1)
        for _ in range(4):                            # MAIN 1 drops four times with no read of ours before it
            neo.set(NEO_UNIT, 1268, 0b1); wait_cycles(c, 1)
            neo.set(NEO_UNIT, 1268, 0); wait_cycles(c, 1)
        for _ in range(2):                            # ... and twice right after our read: 2 of 6, by chance
            c.poll()
            neo.set(NEO_UNIT, 1268, 0b1); wait_cycles(c, 1)
            neo.set(NEO_UNIT, 1268, 0); wait_cycles(c, 1)
        assert "main-01" not in c.every_override
        mains.stop()                                  # our read fails (MAIN 1 gone): that read is not ours to blame
        c.read_times.pop("main-01", None)
        c.poll()
        assert "main-01" not in c.read_times
    finally:
        c.close()


def test_a_broken_settings_file_never_stops_the_collector(tmp_path, hosts):
    neo, sau, mains = hosts
    (tmp_path / "data").mkdir()
    for bad in ('{"slowed_devices": null}', '{"slowed_devices": {"main-01": "soon"}}', '{"slowed_devices": [1]}', '[]', '{'):
        (tmp_path / "data" / "settings.json").write_text(bad)
        c = plant(tmp_path, neo, sau, mains, live=False)
        assert "main-01" not in c.every_override
        c.close()
