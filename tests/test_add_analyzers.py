"""Analyzers added from the dashboard (1.0.17): a meter of the plant's analyzers' type, by its gateway's IP address
and its Modbus address. Read by the meter reader; one behind a gateway already read shares its one connection."""
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from test_meters import METER_1, FakeGateway, _config, _values, _wait  # noqa: E402

from plantcollector import views  # noqa: E402
from plantcollector.api import create_app  # noqa: E402
from plantcollector.core import Collector  # noqa: E402
from plantcollector.registry import Quality  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METER_2 = {**METER_1, 1142: 55, 1099: 80}           # a second meter behind the same gateway


@pytest.fixture()
def gw2():
    gw = FakeGateway({17: METER_1, 2: METER_2}, unit={17, 2})
    yield gw
    gw.close()


def cards(c):
    return {d["id"]: d for d in views.summary(c)["devices"] if d["kind"] == "analyzer"}


def test_add_one_behind_the_same_gateway_shares_its_connection(tmp_path, gw2):
    c = Collector(_config(tmp_path, gw2), str(tmp_path / "d"), initial_poll=False)
    try:
        assert c.start_meters() and _wait(lambda: c.meters.cycles >= 1)
        api = TestClient(create_app(c))
        r = api.post("/api/v1/local/analyzers", json={"host": "127.0.0.1", "port": gw2.port, "unit_id": 2, "name": "Blower"})
        assert r.status_code == 200, r.text
        did = r.json()["id"]
        assert did == "analyzer-3"                                     # after the plant's two, never an id used before
        assert _wait(lambda: _values(c, did).get("p_total_kw", (None,))[0] == 55)
        assert _values(c)["p_total_kw"] == (27, Quality.GOOD)           # the first meter is still read
        assert gw2.accepted == 1 and gw2.ignored == 0                  # one connection for both meters
        card = cards(c)[did]
        assert card["added"] and card["name"] == "Blower" and card["host"] == "127.0.0.1" and card["unit_id"] == 2
        assert not cards(c)["analyzer-1"]["added"]
        assert did in TestClient(create_app(c)).get("/api/v1/local/registers").json()
        saved = json.load(open(tmp_path / "d" / "settings.json"))
        assert saved["added_analyzers"] == [{"id": did, "host": "127.0.0.1", "port": gw2.port, "unit_id": 2, "name": "Blower"}]
        c.poll()                                                       # the site read leaves it to the meter reader
        assert gw2.accepted == 1
    finally:
        c.close()
    # a restart reads it again
    c = Collector(_config(tmp_path, gw2), str(tmp_path / "d"), initial_poll=False)
    try:
        assert "analyzer-3" in c.registry.devices and "analyzer-3" in c.user_added
        assert c.start_meters() and "analyzer-3" in c.meters.device_ids
    finally:
        c.close()


def test_remove_an_added_one(tmp_path, gw2):
    c = Collector(_config(tmp_path, gw2), str(tmp_path / "d"), initial_poll=False)
    try:
        assert c.start_meters() and _wait(lambda: c.meters.cycles >= 1)
        api = TestClient(create_app(c))
        did = api.post("/api/v1/local/analyzers", json={"host": "127.0.0.1", "port": gw2.port, "unit_id": 2}).json()["id"]
        assert _wait(lambda: _values(c, did).get("p_total_kw", (None,))[0] == 55)
        assert api.delete("/api/v1/local/analyzers/analyzer-1").status_code == 400          # the plant's own stay
        assert api.delete(f"/api/v1/local/analyzers/{did}").status_code == 200
        assert did not in cards(c) and did not in c.meters.device_ids
        assert did not in api.get("/api/v1/local/registers").json()
        n = c.meters.cycles
        assert _wait(lambda: c.meters.cycles >= n + 1)
        assert _values(c)["p_total_kw"] == (27, Quality.GOOD) and gw2.accepted == 1     # the other meter's connection stays
        assert json.load(open(tmp_path / "d" / "settings.json"))["added_analyzers"] == []
        assert c.store.latest(did)                                     # its readings stay
        # added again: a new id, its old readings are not mixed in
        again = api.post("/api/v1/local/analyzers", json={"host": "127.0.0.1", "port": gw2.port, "unit_id": 2}).json()["id"]
        assert again == "analyzer-4"
    finally:
        c.close()
    c = Collector(_config(tmp_path, gw2), str(tmp_path / "d"), initial_poll=False)
    try:
        assert "analyzer-3" not in c.registry.devices and "analyzer-4" in c.registry.devices
    finally:
        c.close()


def test_refused(tmp_path, gw2):
    extra = """  - {id: neo-01, profile: intelineo_master.yaml, adapter: modbus_tcp, connection: {host: 192.168.0.112, port: 502, unit_id: 1}}
probe:
  hosts: [192.168.0.105]"""
    c = Collector(_config(tmp_path, gw2, extra_devices=extra), str(tmp_path / "d"), initial_poll=False)
    try:
        api = TestClient(create_app(c))
        for body, word in (({"host": "192.168.0.112", "unit_id": 1}, "neo-01"), ({"host": "192.168.0.105", "unit_id": 1}, "genset"),
                           ({"host": "127.0.0.1", "port": gw2.port, "unit_id": 17}, "already"), ({"host": "nope", "unit_id": 1}, "IP"),
                           ({"host": "0.0.0.0", "unit_id": 1}, "not a device"), ({"host": "10.0.0.9", "unit_id": 0}, "1 to 247"),
                           ({"host": "10.0.0.9", "unit_id": 248}, "1 to 247"), ({"host": "10.0.0.9", "port": 0, "unit_id": 1}, "port"),
                           ({"host": "10.0.0.9", "unit_id": 1, "name": "<x>"}, "<"), ({"host": "10.0.0.9", "unit_id": "a"}, "numbers")):
            r = api.post("/api/v1/local/analyzers", json=body)
            assert r.status_code == 400 and word in r.json()["detail"], (body, r.text)
        assert not c.user_added
    finally:
        c.close()


def test_not_in_the_demo(tmp_path):
    c = Collector(os.path.join(ROOT, "config", "site.demo.yaml"), str(tmp_path), initial_poll=False, fresh=True)
    try:
        r = TestClient(create_app(c)).post("/api/v1/local/analyzers", json={"host": "10.0.0.9", "unit_id": 1})
        assert r.status_code == 400 and "demo" in r.json()["detail"]
    finally:
        c.close()


def test_the_turkey_map_does_not_change(tmp_path, gw2):
    from plantcollector.publish import ModbusPublisher
    c = Collector(_config(tmp_path, gw2), str(tmp_path / "d"), initial_poll=False)
    try:
        before = ModbusPublisher(c, port=0).map
        c.add_analyzer("127.0.0.1", gw2.port, 2, "Blower")
        assert ModbusPublisher(c, port=0).map == before
        assert all(not m["device"].startswith("analyzer-") for m in before)
    finally:
        c.close()


def test_bad_entries_in_the_settings_file_are_left_out(tmp_path, gw2):
    os.makedirs(tmp_path / "d", exist_ok=True)
    with open(tmp_path / "d" / "settings.json", "w") as f:
        json.dump({"added_analyzers": [{"id": "analyzer-5", "host": "10.0.0.9", "port": 502, "unit_id": 3},
                                       {"id": "analyzer-6", "host": "bad", "unit_id": 3},
                                       {"id": "analyzer-1", "host": "10.0.0.8", "unit_id": 3},      # the plant's own id
                                       {"id": "neo-9", "host": "10.0.0.7", "unit_id": 3}, "junk"]}, f)
    c = Collector(_config(tmp_path, gw2), str(tmp_path / "d"), initial_poll=False)
    try:
        assert c.user_added == {"analyzer-5"}
        assert c.registry.devices["analyzer-1"].connection["unit_id"] == 17
    finally:
        c.close()
