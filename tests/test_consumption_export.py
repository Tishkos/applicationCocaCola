"""1.0.22: the export by device with the plant's register names as headers, the plant load consumption tab, and
the backup/restore of the dashboard's own setup."""
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(__file__))

from test_meters import METER_1, FakeGateway, _config, _wait  # noqa: E402

from plantcollector import export, views  # noqa: E402
from plantcollector.api import create_app  # noqa: E402
from plantcollector.core import Collector  # noqa: E402
from plantcollector.store import Reading  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "config", "site.demo.yaml")


@pytest.fixture()
def demo(tmp_path):
    c = Collector(DEMO, str(tmp_path / "d"), initial_poll=False, fresh=True)
    c.poll()
    try:
        yield c
    finally:
        c.close()


def test_export_only_the_chosen_devices_with_register_names(demo):
    c = demo
    now = c.now()
    c.set_register_name("analyzer-1", "p_total_kw", "Blower")
    headers, rows = export.wide_table(c, now - timedelta(hours=1), now, 300, ["analyzer-1"])
    assert headers[:2] == ["time_utc", "time_local (Asia/Baghdad)"]
    assert all(h.startswith("analyzer-1") for h in headers[2:]) and headers[2:]
    assert "analyzer-1: Blower [kW]" in headers and not any(h.startswith("analyzer-1.p_total_kw") for h in headers)
    assert rows and len(rows[0]) == len(headers)
    everything, _ = export.wide_table(c, now - timedelta(hours=1), now, 300)
    assert len(everything) > len(headers) and "analyzer-1: Blower [kW]" in everything
    with pytest.raises(ValueError, match="unknown device: nope"):
        export.wide_table(c, now - timedelta(hours=1), now, 300, ["analyzer-1", "nope"])
    api = TestClient(create_app(c))
    r = api.get("/api/v1/local/export.xlsx?hours=1&step=300&devices=analyzer-1,analyzer-2")
    assert r.status_code == 200 and "2devices" in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    assert [c.value for c in wb["Data"][1]][2].startswith("analyzer-")
    assert wb["Summary"]["A4"].value == "devices: analyzer-1, analyzer-2 (2)"
    assert {row[1] for row in wb["Points"].iter_rows(min_row=2, values_only=True)} == {"analyzer-1", "analyzer-2"}
    assert api.get("/api/v1/local/export.csv?hours=1&devices=nope").status_code == 400
    r = api.get("/api/v1/local/export.csv?hours=1&devices=analyzer-1")
    assert r.status_code == 200 and "analyzer-1: Blower [kW]" in r.text.splitlines()[0] and "_analyzer-1_" in r.headers["content-disposition"]


def test_consumption_from_the_meter_counter_and_from_kw(demo):
    c = demo
    t1 = c.now() - timedelta(minutes=2)                 # the demo's own poll (just now) stays outside the window
    t0 = t1 - timedelta(hours=2)
    # analyzer-1: a counter read at both ends of the window; analyzer-2: no counter, 40 kW flat for two hours
    c.store.ingest("analyzer-1", {"e_in_kwh": Reading("e_in_kwh", 12000.0, "kWh", t0 - timedelta(minutes=5))})
    c.store.ingest("analyzer-1", {"e_in_kwh": Reading("e_in_kwh", 12150.5, "kWh", t1 - timedelta(minutes=1))})
    for k in range(0, 121, 10):
        c.store.ingest("analyzer-2", {"p_total_kw": Reading("p_total_kw", 40.0, "kW", t0 + timedelta(minutes=k))})
    c.set_register_name("analyzer-1", "e_in_kwh", "Blower feeder")
    d = views.consumption(c, t0, t1)
    rows = {r["id"]: r for r in d["rows"]}
    a1, a2 = rows["analyzer-1"], rows["analyzer-2"]
    assert a1["method"] == "counter" and a1["kwh"] == 150.5 and a1["start_kwh"] == 12000.0 and a1["counter_kwh"] == 12150.5
    assert a1["register"] == "Blower feeder" and a1["note"] == ""
    assert a2["method"] == "from kW" and a2["kwh"] == 80.0 and "integrated" in a2["note"]
    assert d["metered_total_kwh"] == 230.5 and d["counters"] == 1
    assert a1["share_pct"] == round(100 * 150.5 / 230.5, 1)
    # a counter that went backwards: not believed; a first reading inside the window: noted
    c.store.ingest("analyzer-1", {"e_in_kwh": Reading("e_in_kwh", 5.0, "kWh", t1 + timedelta(seconds=30))})
    d2 = views.consumption(c, t0, t1 + timedelta(seconds=60))
    r1 = {r["id"]: r for r in d2["rows"]}["analyzer-1"]
    assert r1["method"] == "from kW" or r1["kwh"] is None
    assert "backwards" in r1["note"]
    api = TestClient(create_app(c))
    j = api.get("/api/v1/local/consumption", params={"from": t0.isoformat(), "to": t1.isoformat()}).json()
    assert {r["id"]: r["kwh"] for r in j["rows"]} == {"analyzer-1": 150.5, "analyzer-2": 80.0}
    x = api.get("/api/v1/local/consumption.xlsx", params={"from": t0.isoformat(), "to": t1.isoformat()})
    assert x.status_code == 200 and "_consumption_" in x.headers["content-disposition"]
    ws = load_workbook(io.BytesIO(x.content))["Consumption"]
    body = {row[0]: row for row in ws.iter_rows(min_row=6, values_only=True)}
    assert body["Blower feeder"][9] == 150.5 and body["Blower feeder"][11] == "counter"
    assert body["all meters"][9] == 230.5
    csv = api.get("/api/v1/local/consumption.csv", params={"from": t0.isoformat(), "to": t1.isoformat()}).text
    assert csv.lstrip("\ufeff").splitlines()[0].startswith("feeder,device,name,register name,kW now") and "Blower feeder" in csv


def test_backup_and_restore_carry_the_setup_to_another_pc(tmp_path):
    gw = FakeGateway({17: METER_1, 2: {**METER_1, 1142: 55}}, unit={17, 2})
    try:
        (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
        a = Collector(_config(tmp_path / "a", gw), str(tmp_path / "a" / "d"), initial_poll=False)
        try:
            did = a.add_analyzer("127.0.0.1", gw.port, 2, "Blower")["id"]
            a.set_register_name(did, "p_total_kw", "Blower kW")
            a.set_register_name("analyzer-1", "e_in_kwh", "Main feeder")
            bk = a.settings_backup()
        finally:
            a.close()
        assert bk["format"] == "cci-plant-monitoring-settings" and bk["version"] == 1
        assert bk["added_analyzers"] == [{"id": did, "host": "127.0.0.1", "port": gw.port, "unit_id": 2, "name": "Blower"}]
        assert bk["register_names"] == {did: {"p_total_kw": "Blower kW"}, "analyzer-1": {"e_in_kwh": "Main feeder"}}
        text = json.dumps(bk)                                    # the file as downloaded
        b = Collector(_config(tmp_path / "b", gw), str(tmp_path / "b" / "d"), initial_poll=False)
        try:
            api = TestClient(create_app(b))
            r = api.get("/api/v1/local/settings/backup")
            assert r.status_code == 200 and r.headers["content-disposition"].startswith('attachment; filename="cci-plant-monitoring-settings_')
            rep = api.post("/api/v1/local/settings/restore", json=json.loads(text))
            assert rep.status_code == 200, rep.text
            rep = rep.json()
            assert [x["id"] for x in rep["analyzers_added"]] == ["analyzer-3"] and rep["names_set"] == 2 and not rep["analyzers_failed"]
            assert b.register_names == {"analyzer-3": {"p_total_kw": "Blower kW"}, "analyzer-1": {"e_in_kwh": "Main feeder"}}
            assert "analyzer-3" in b.user_added
            # once more: nothing added twice, names simply the same
            rep2 = api.post("/api/v1/local/settings/restore", json=json.loads(text)).json()
            assert not rep2["analyzers_added"] and [x["id"] for x in rep2["analyzers_present"]] == ["analyzer-3"] and rep2["names_set"] == 0
            assert api.post("/api/v1/local/settings/restore", json={"hello": 1}).status_code == 400
        finally:
            b.close()
    finally:
        gw.close()
