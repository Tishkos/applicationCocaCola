"""The installed version and the updater's requests (Settings > Updates). The updater itself is a PowerShell
script run by Windows; here only what the collector does: report status.json, drop the request flag, refuse
requests from other PCs and from a copy that was not installed by the Setup."""

import json
import os

import pytest
from fastapi.testclient import TestClient

from plantcollector import updates
from plantcollector.api import create_app
from plantcollector.core import Collector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "site.yaml")


@pytest.fixture(scope="module")
def collector(tmp_path_factory):
    base = tmp_path_factory.mktemp("pd")
    return Collector(CFG, str(base / "data"), seed_hours=0, fresh=True)


@pytest.fixture()
def setup_copy(tmp_path, monkeypatch):
    """A program folder as the Setup leaves it: version.txt and scripts/updater.ps1."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "updater.ps1").write_text("# updater\n")
    (tmp_path / "version.txt").write_text("1.2.3\r\n")
    monkeypatch.setattr(updates, "ROOT", str(tmp_path))
    return tmp_path


H = {"X-CCI-Request": "update"}           # the header the dashboard's Updates panel sends


def test_not_installed_by_the_setup(collector):
    api = TestClient(create_app(collector), headers=H)
    st = api.get("/api/v1/local/version").json()
    assert st["installed"] is False and st["version"] == updates.installed_version()
    assert api.post("/api/v1/local/update/check").status_code == 409
    assert api.post("/api/v1/local/update/install").status_code == 409
    assert api.get("/api/v1/local/settings").json()["update"]["installed"] is False


def test_status_from_the_updater(collector, setup_copy):
    api = TestClient(create_app(collector), headers=H)
    st = api.get("/api/v1/local/version").json()
    assert st["installed"] is True and st["version"] == "1.2.3" and st["state"] == "never checked"
    d = updates.update_dir(collector.data_dir)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "status.json"), "w", encoding="utf-8-sig") as f:      # PowerShell 5 writes a BOM
        json.dump({"state": "update available", "message": "Version 1.3.0 is available", "latest": "1.3.0",
                   "available": "1.3.0", "last_check": "2026-11-03 01:30", "auto": True, "window": "01:00-05:00",
                   "installed": "9.9.9"}, f)
    st = api.get("/api/v1/local/settings").json()
    assert st["version"] == "1.2.3"
    u = st["update"]
    assert u["state"] == "update available" and u["available"] == "1.3.0" and u["version"] == "1.2.3"
    assert u["check_requested"] is False


def test_check_and_install_requests(collector, setup_copy):
    api = TestClient(create_app(collector), headers=H)
    flag = os.path.join(updates.update_dir(collector.data_dir), "now.flag")
    r = api.post("/api/v1/local/update/check")
    assert r.status_code == 200 and r.json()["status"]["check_requested"] is True
    assert open(flag, encoding="utf-8").read().startswith("check ")
    r = api.post("/api/v1/local/update/install")
    assert r.status_code == 200
    assert open(flag, encoding="utf-8").read().startswith("install ")
    os.remove(flag)


def test_only_from_this_pc(collector, setup_copy):
    other = TestClient(create_app(collector), client=("192.168.0.20", 50000), headers=H)
    assert other.post("/api/v1/local/update/check").status_code == 403
    assert other.post("/api/v1/local/update/install").status_code == 403
    assert other.get("/api/v1/local/version").status_code == 200        # reading the state is fine
    local = TestClient(create_app(collector), client=("127.0.0.1", 50000), headers=H)
    assert local.post("/api/v1/local/update/check").status_code == 200


def test_not_from_another_web_site(collector, setup_copy):
    """A page of another web site open in a browser on this PC cannot start an install."""
    plain = TestClient(create_app(collector), client=("127.0.0.1", 50000))
    assert plain.post("/api/v1/local/update/install").status_code == 403                 # no dashboard header
    api = TestClient(create_app(collector), client=("127.0.0.1", 50000), headers=H)
    assert api.post("/api/v1/local/update/install", headers={"Origin": "https://evil.example"}).status_code == 403
    assert api.post("/api/v1/local/update/install", headers={"Host": "evil.example:8080"}).status_code == 403
    assert api.post("/api/v1/local/update/check", headers={"Origin": "http://localhost:8080"}).status_code == 200


def test_broken_status_file(collector, setup_copy):
    d = updates.update_dir(collector.data_dir)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "status.json"), "w") as f:
        f.write("{half")
    st = updates.status(collector.data_dir)
    assert st["installed"] is True and st["state"] == "never checked"
