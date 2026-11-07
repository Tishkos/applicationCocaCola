"""The plant's own names for its analyzers (settings.json device_names): only the name shown changes."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from plantcollector.api import create_app
from plantcollector.core import Collector
from plantcollector import views

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "site.demo.yaml")


@pytest.fixture
def c(tmp_path):
    c = Collector(CFG, str(tmp_path), initial_poll=False, fresh=True)
    yield c
    c.close()


def names(c):
    return {d["id"]: (d["name"], d["renamed"]) for d in views.summary(c)["devices"] if d["kind"] == "analyzer"}


def test_rename_an_analyzer_and_back(c, tmp_path):
    assert names(c)["analyzer-1"] == ("", False)
    api = TestClient(create_app(c))
    r = api.post("/api/v1/local/settings", json={"device_names": {"analyzer-1": "  Blower   001 "}})
    assert r.status_code == 200 and r.json()["device_names"] == {"analyzer-1": "Blower 001"}
    assert names(c)["analyzer-1"] == ("Blower 001", True)
    saved = json.load(open(os.path.join(str(tmp_path), "settings.json")))
    assert saved["device_names"] == {"analyzer-1": "Blower 001"}
    # a restart keeps it
    c2 = Collector(CFG, str(tmp_path), initial_poll=False)
    try:
        assert c2.name_of("analyzer-1") == "Blower 001"
    finally:
        c2.close()
    # empty = the default name again
    r = api.post("/api/v1/local/settings", json={"device_names": {"analyzer-1": ""}})
    assert r.status_code == 200 and r.json()["device_names"] == {}
    assert names(c)["analyzer-1"] == ("", False)
    assert "analyzer-1" not in json.load(open(os.path.join(str(tmp_path), "settings.json")))["device_names"]


def test_only_analyzers_and_sensible_names(c):
    api = TestClient(create_app(c))
    for body in ({"neo-01": "Master"}, {"nope": "x"}, {"analyzer-1": "x" * 41}, {"analyzer-1": "<b>"}, {"analyzer-1": 5}):
        r = api.post("/api/v1/local/settings", json={"device_names": body})
        assert r.status_code == 400, body
    assert c.device_names == {}


def test_the_id_and_the_readings_stay(c):
    c.set_device_name("analyzer-2", "Compressor")
    assert "analyzer-2" in c.registry.devices and c.registry.devices["analyzer-2"].name == ""
    ev = c.store.events(since=__import__("datetime").datetime(2000, 1, 1, tzinfo=__import__("datetime").timezone.utc))
    assert any(e["message"] == "analyzer-2 named 'Compressor'" for e in ev)


def test_settings_file_with_bad_names_is_ignored(tmp_path):
    with open(os.path.join(str(tmp_path), "settings.json"), "w") as f:
        json.dump({"device_names": {"analyzer-1": "Blower", "neo-01": "x", "ghost": "y", "analyzer-2": 7}}, f)
    c = Collector(CFG, str(tmp_path), initial_poll=False)
    try:
        assert c.device_names == {"analyzer-1": "Blower"}
    finally:
        c.close()
