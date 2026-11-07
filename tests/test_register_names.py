"""The plant's own names for the analyzers' registers (settings.json register_names): only the name shown changes."""
import json
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from plantcollector.api import create_app
from plantcollector.core import Collector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "site.demo.yaml")


@pytest.fixture
def c(tmp_path):
    c = Collector(CFG, str(tmp_path), initial_poll=False, fresh=True)
    yield c
    c.close()


def reg(api, did, pid):
    return next(r for r in api.get("/api/v1/local/registers").json()[did] if r["id"] == pid)


def test_every_register_of_the_analyzers_is_listed(c):
    api = TestClient(create_app(c))
    regs = api.get("/api/v1/local/registers").json()
    assert set(regs) == {"analyzer-1", "analyzer-2"}
    r = reg(api, "analyzer-1", "p_total_kw")
    assert r == {"id": "p_total_kw", "address": 1142, "manual": 1143, "words": 1, "kind": "int16", "scale": None, "unit": "kW",
                 "description": "Active power, total (+ into the load)", "name": ""}
    e = reg(api, "analyzer-1", "e_in_kwh")
    assert e["words"] == 4 and e["kind"] == "mod10x4"
    assert all("address" in x for x in regs["analyzer-1"]) and "comm_error" not in {x["id"] for x in regs["analyzer-1"]}
    assert [x["address"] for x in regs["analyzer-1"]] == sorted(x["address"] for x in regs["analyzer-1"])


def test_name_a_register_and_back(c, tmp_path):
    api = TestClient(create_app(c))
    r = api.post("/api/v1/local/settings", json={"register_names": {"analyzer-1": {"p_total_kw": "  Blower   power "}}})
    assert r.status_code == 200
    assert reg(api, "analyzer-1", "p_total_kw")["name"] == "Blower power"
    assert reg(api, "analyzer-2", "p_total_kw")["name"] == ""                    # each meter on its own
    saved = json.load(open(os.path.join(str(tmp_path), "settings.json")))
    assert saved["register_names"] == {"analyzer-1": {"p_total_kw": "Blower power"}}
    c2 = Collector(CFG, str(tmp_path), initial_poll=False)                        # a restart keeps it
    try:
        assert c2.register_list("analyzer-1")[[x["id"] for x in c2.register_list("analyzer-1")].index("p_total_kw")]["name"] == "Blower power"
    finally:
        c2.close()
    assert api.post("/api/v1/local/settings", json={"register_names": {"analyzer-1": {"p_total_kw": ""}}}).status_code == 200
    assert reg(api, "analyzer-1", "p_total_kw")["name"] == ""
    assert json.load(open(os.path.join(str(tmp_path), "settings.json")))["register_names"] == {}
    ev = c.store.events(since=datetime(2000, 1, 1, tzinfo=timezone.utc))
    assert any(e["message"] == "analyzer-1 register 1142 (p_total_kw) named 'Blower power'" for e in ev)


def test_refused(c):
    api = TestClient(create_app(c))
    for body in ({"neo-01": {"power_kw": "x"}}, {"ghost": {"p_total_kw": "x"}}, {"analyzer-1": {"comm_error": "x"}},
                 {"analyzer-1": {"nope": "x"}}, {"analyzer-1": {"p_total_kw": "x" * 41}}, {"analyzer-1": {"p_total_kw": "<b>"}},
                 {"analyzer-1": {"p_total_kw": 5}}, {"analyzer-1": "x"}):
        assert api.post("/api/v1/local/settings", json={"register_names": body}).status_code == 400, body
    assert c.register_names == {}


def test_what_is_read_stays_the_same(c):
    before = [(p.id, dict(p.raw)) for p in c.registry.devices["analyzer-1"].points]
    c.set_register_name("analyzer-1", "i_l1_a", "Blower L1")
    assert [(p.id, dict(p.raw)) for p in c.registry.devices["analyzer-1"].points] == before
    assert c.registry.devices["analyzer-1"].point("i_l1_a").description == "Current L1"


def test_bad_names_in_the_settings_file_are_ignored(tmp_path):
    with open(os.path.join(str(tmp_path), "settings.json"), "w") as f:
        json.dump({"register_names": {"analyzer-1": {"p_total_kw": "Blower", "comm_error": "x", "nope": "y", "i_l1_a": 7},
                                      "neo-01": {"power_kw": "x"}, "analyzer-2": "z"}}, f)
    c = Collector(CFG, str(tmp_path), initial_poll=False)
    try:
        assert c.register_names == {"analyzer-1": {"p_total_kw": "Blower"}}
    finally:
        c.close()
