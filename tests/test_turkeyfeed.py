"""The Turkey feed's switch in site.plant.yaml (plantcollector/turkeyfeed.py), as the Setup's checkbox uses it."""
import os

import yaml

from plantcollector import turkeyfeed
from plantcollector.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANT = os.path.join(ROOT, "config", "site.plant.yaml")


def test_the_plant_config_switches_on_and_off_with_nothing_else_changed():
    text = open(PLANT, encoding="utf-8").read()
    assert turkeyfeed.is_enabled(text) is False
    on = turkeyfeed.set_enabled(text, True)
    assert turkeyfeed.is_enabled(on) is True
    assert yaml.safe_load(on)["publish"]["modbus_tcp"]["enabled"] is True
    # one line differs, and only its value
    a, b = text.splitlines(), on.splitlines()
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    assert len(a) == len(b) and len(diff) == 1
    assert diff[0][0].strip() == "enabled: false" and diff[0][1].strip() == "enabled: true"
    assert turkeyfeed.set_enabled(on, False) == text
    before, after = yaml.safe_load(text), yaml.safe_load(on)
    before["publish"]["modbus_tcp"].pop("enabled"); after["publish"]["modbus_tcp"].pop("enabled")
    assert before == after


def test_comment_and_crlf_are_kept():
    text = "site:\r\n  name: x\r\npublish:\r\n  modbus_tcp:\r\n    enabled: false   # off until the VPN\r\n    port: 502\r\n"
    on = turkeyfeed.set_enabled(text, True)
    assert on == "site:\r\n  name: x\r\npublish:\r\n  modbus_tcp:\r\n    enabled: true   # off until the VPN\r\n    port: 502\r\n"


def test_missing_pieces_are_added():
    none = "site:\n  name: x\n"
    t = turkeyfeed.set_enabled(none, True)
    d = yaml.safe_load(t)["publish"]["modbus_tcp"]
    assert d["enabled"] is True and d["port"] == 502 and d["bind"] == "0.0.0.0" and d["unit_id"] == 1
    assert t.startswith("site:\n  name: x\n")
    no_switch = "publish:\n  modbus_tcp:\n    port: 1502\n"
    t = turkeyfeed.set_enabled(no_switch, True)
    assert yaml.safe_load(t)["publish"]["modbus_tcp"] == {"enabled": True, "port": 1502}
    no_tcp = "publish:\n  other: 1\n"
    t = turkeyfeed.set_enabled(no_tcp, False)
    assert yaml.safe_load(t)["publish"]["modbus_tcp"]["enabled"] is False and yaml.safe_load(t)["publish"]["other"] == 1


def test_an_enabled_key_elsewhere_is_not_the_switch():
    text = "devices:\n  - id: a\n    enabled: false\npublish:\n  modbus_tcp:\n    enabled: false\n"
    on = turkeyfeed.set_enabled(text, True)
    assert on == "devices:\n  - id: a\n    enabled: false\npublish:\n  modbus_tcp:\n    enabled: true\n"


def test_cli_turkey_on_off_status(tmp_path, capsys):
    cfg = tmp_path / "site.plant.yaml"
    cfg.write_text(open(PLANT, encoding="utf-8").read(), encoding="utf-8")
    assert main(["--config", str(cfg), "turkey", "status"]) == 1
    assert main(["--config", str(cfg), "turkey", "on"]) == 0
    assert "on" in capsys.readouterr().out
    assert turkeyfeed.state(str(cfg)) is True
    assert main(["--config", str(cfg), "turkey", "status"]) == 0
    assert main(["--config", str(cfg), "turkey", "on"]) == 0          # again: no change, still fine
    assert "already" in capsys.readouterr().out
    assert main(["--config", str(cfg), "turkey", "off"]) == 0
    assert turkeyfeed.state(str(cfg)) is False
    assert main(["--config", str(tmp_path / "missing.yaml"), "turkey", "on"]) == 2
