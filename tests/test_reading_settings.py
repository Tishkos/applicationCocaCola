"""Settings > Reading the devices: the master's live rate (or off) and each other device's rate, applied at once,
remembered in settings.json, and never faster than the site read for the other devices."""

import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
from test_live import NEO_UNIT, hosts, plant  # noqa: E402,F401  (hosts is the fake plant fixture)

from plantcollector.api import create_app  # noqa: E402
from plantcollector.core import Collector  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _wait(cond, s=10.0):
    t0 = time.time()
    while time.time() - t0 < s:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _saved(c):
    with open(c.settings_path) as f:
        return json.load(f)


def test_rows_and_options(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    c.start_live()
    try:
        r = TestClient(create_app(c)).get("/api/v1/local/settings").json()["reading"]
        assert r["live"]["available"] and r["live"]["devices"] == ["neo-01"] and r["live"]["every_s"] == 1
        assert [o["value"] for o in r["live"]["options"]] == [0, 5, 10, 30, 60, 120, 300, 600]
        rows = {g["key"]: g for g in r["groups"]}
        sau_row = rows[f"127.0.0.1:{sau.port}"]
        assert sau_row["devices"] == ["inv-01", "pv-array"] and sau_row["name"].startswith("Solar")
        main_row = rows[f"127.0.0.1:{mains.port}"]
        assert main_row["devices"] == ["main-01"] and main_row["name"] == "MAIN 1 (IM210 G2)"
        assert [o["value"] for o in main_row["options"]] == [0, 1800, 3600, 7200]     # never faster than the 10-min site read
        assert "neo-01" not in [d for g in r["groups"] for d in g["devices"]]          # read live: set above
    finally:
        c.close()


def test_live_rate_is_applied_and_remembered(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    c.start_live()
    api = TestClient(create_app(c))
    try:
        assert _wait(lambda: c.live.cycles >= 1)
        r = api.post("/api/v1/local/settings", json={"live_every_s": 30})
        assert r.status_code == 200 and r.json()["reading"]["live"]["every_s"] == 30
        assert c.live.every_s == 30 and c.live.store_every_s >= 60 and c.live.chart_minutes() == 60
        assert c.live.history.maxlen == 60 * 60 // 30 + 5
        assert c.live.guard_window_s == 15 + 30           # a fail is seen only at the next live read
        assert _saved(c)["live_every_s"] == 30
        assert api.get("/api/v1/local/live").json()["minutes"] == 60
        # a fresh start reads the saved rate
        c.close()
        c2 = plant(tmp_path, neo, sau, mains)
        assert c2.live_cfg()["every_s"] == 30
        c2.start_live()
        assert c2.live.every_s == 30
        c2.close()
    finally:
        c.close()


def test_live_off_reads_the_master_at_the_site_read(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    c.start_live()
    api = TestClient(create_app(c))
    try:
        assert _wait(lambda: c.live.cycles >= 1)
        thread = c.live.thread
        assert api.post("/api/v1/local/settings", json={"live_every_s": 0}).status_code == 200
        assert c.live is None and not thread.is_alive()
        assert _saved(c)["live_every_s"] == 0
        n0 = neo.total_requests()
        c.poll()
        assert neo.total_requests() > n0                        # the site read reads the master itself now
        assert api.get("/api/v1/local/summary").status_code == 200
        assert api.get("/api/v1/local/live").json()["running"] is False
        # and on again
        assert api.post("/api/v1/local/settings", json={"live_every_s": 60}).status_code == 200
        assert c.live is not None and c.live.every_s == 60
    finally:
        c.close()


def test_device_rate_is_applied_remembered_and_undoes_the_guard(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    api = TestClient(create_app(c))
    key = f"127.0.0.1:{mains.port}"
    c.slow_down("main-01", 3600, "test: the guard slowed it")
    row = {g["key"]: g for g in api.get("/api/v1/local/settings").json()["reading"]["groups"]}[key]
    assert row["every_s"] == 3600 and row["slowed"] is True
    # back to every site read: the guard's slow-down is undone and remembered
    r = api.post("/api/v1/local/settings", json={"device_every": {key: 0}})
    assert r.status_code == 200
    assert "main-01" not in c.every_override and c.device_every_s("main-01") == 600
    assert "main-01" not in _saved(c)["slowed_devices"] and _saved(c)["device_every_s"]["main-01"] == 0
    # the whole SAU row at once
    sau_key = f"127.0.0.1:{sau.port}"
    assert api.post("/api/v1/local/settings", json={"device_every": {sau_key: 1800}}).status_code == 200
    assert c.device_every_s("inv-01") == 1800 and c.device_every_s("pv-array") == 1800
    c.close()
    c2 = plant(tmp_path, neo, sau, mains)
    assert c2.device_every_s("inv-01") == 1800 and c2.device_every_s("main-01") == 600
    # a later slow-down by the guard wins over the earlier choice
    c2.slow_down("main-01", 3600, "test")
    assert "main-01" not in c2.device_choice and "main-01" not in _saved(c2)["device_every_s"]
    c2.close()


def test_slowed_device_is_skipped_until_due(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains, live=False)
    try:
        c.set_device_every(["main-01"], 3600)
        c.poll()
        n = mains.total_requests()
        c.poll()
        assert mains.total_requests() == n                      # not due yet
    finally:
        c.close()


def test_bad_requests(tmp_path, hosts):
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    c.start_live()
    api = TestClient(create_app(c))
    try:
        assert api.post("/api/v1/local/settings", json={}).status_code == 400
        assert api.post("/api/v1/local/settings", json={"live_every_s": 3}).status_code == 400
        assert api.post("/api/v1/local/settings", json={"device_every": {"nope": 0}}).status_code == 400
        assert api.post("/api/v1/local/settings", json={"device_every": {f"127.0.0.1:{mains.port}": 900}}).status_code == 400
        with pytest.raises(ValueError):
            c.set_device_every(["neo-01"], 3600)          # read live
        assert api.post("/api/v1/local/settings", json={"poll_interval_s": 1800}).status_code == 200   # still works alone
    finally:
        c.close()


def test_demo_is_locked(tmp_path):
    c = Collector(os.path.join(ROOT, "config", "site.yaml"), str(tmp_path / "d"), seed_hours=0, fresh=True)
    api = TestClient(create_app(c))
    r = api.get("/api/v1/local/settings").json()["reading"]
    assert r["simulated"] is True and r["live"]["available"] is False
    assert api.post("/api/v1/local/settings", json={"live_every_s": 60}).status_code == 400


def test_slow_master_switches_the_guard_off(tmp_path, hosts):
    """Read every 10 min, the master's fail is seen up to 10 min late: it must never be blamed on our reads."""
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    c.start_live()
    try:
        c.set_live_every(600)
        assert c.live.every_s == 600 and not c.live.guard_active()
        assert c.live.guard_window_s == 15 + 60
        c.set_live_every(60)
        assert c.live.guard_active()
    finally:
        c.close()


def test_the_sau_is_read_only_at_the_site_read(tmp_path, hosts):
    """However fast the master is read live, and whatever is changed in Settings, the SAU gateway is asked only
    at the site read: once per device behind it, nothing in between; set slower, not at all until due."""
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    c.start_live()
    api = TestClient(create_app(c))
    try:
        assert api.post("/api/v1/local/settings", json={"live_every_s": 5}).status_code == 200
        assert _wait(lambda: c.live.cycles >= 2, 15)
        assert sau.total_requests() == 0                     # the live reader never touches the SAU
        c.poll()
        per_read = sau.total_requests()
        assert 0 < per_read <= 2 * 4                         # inv-01 and pv-array: a few blocks each, no retries
        time.sleep(1.5)
        assert sau.total_requests() == per_read              # nothing between site reads
        n_neo = neo.total_requests()
        c.poll()                                             # the site read leaves the live master to the live reader
        assert sau.total_requests() == 2 * per_read and neo.total_requests() - n_neo <= 6
        sau_key = f"127.0.0.1:{sau.port}"
        assert api.post("/api/v1/local/settings", json={"device_every": {sau_key: 3600}}).status_code == 200
        c.poll()
        assert sau.total_requests() == 2 * per_read          # slowed: not due yet, not asked at all
        assert api.post("/api/v1/local/settings", json={"device_every": {sau_key: 0}}).status_code == 200
        c.poll()
        assert sau.total_requests() == 3 * per_read          # back to every site read
    finally:
        c.close()


def test_turkey_feed_does_not_follow_the_settings(tmp_path, hosts):
    """Changing the master's live rate (or switching it off) does not move the Turkey feed: its values are those of
    the last site read."""
    neo, sau, mains = hosts
    c = plant(tmp_path, neo, sau, mains)
    c.start_live()
    api = TestClient(create_app(c))
    try:
        c.poll()
        before = api.get("/api/v1/tags/Psum3").json()["value"]
        neo.set(NEO_UNIT, 1040, 777)                          # PV Actual P changes on the master
        assert api.post("/api/v1/local/settings", json={"live_every_s": 5}).status_code == 200
        assert _wait(lambda: c.store.value("neo-01", "pv_prod_kw") == 777, 15)       # the dashboard has it live
        assert api.get("/api/v1/tags/Psum3").json()["value"] == before     # Turkey: still the site read's value
        assert api.post("/api/v1/local/settings", json={"live_every_s": 0}).status_code == 200
        assert api.get("/api/v1/tags/Psum3").json()["value"] == before
        c.poll()
        assert api.get("/api/v1/tags/Psum3").json()["value"] == 777        # changes at the next site read only
    finally:
        c.close()


def test_one_collector_lock_is_a_no_op_off_windows():
    from plantcollector.cli import one_collector
    assert one_collector() is True and one_collector() is True
