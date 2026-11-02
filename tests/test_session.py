"""The one-click session and the trace report, against fake controllers and synthetic files."""

import asyncio
import glob
import os
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import ModbusTcpServer

from plantcollector import report
from plantcollector.session import Session

NEO_PORT, SAU_PORT = 15041, 15042


def _serve(port, regs, units=(1,)):
    stores = {u: ModbusDeviceContext(hr=ModbusSequentialDataBlock(1, regs), ir=ModbusSequentialDataBlock(1, regs)) for u in units}
    ctx = ModbusServerContext(devices=stores, single=False)
    holder = {}

    async def serve():
        holder["srv"] = ModbusTcpServer(ctx, address=("127.0.0.1", port))
        await holder["srv"].serve_forever()

    loop = asyncio.new_event_loop()
    threading.Thread(target=lambda: loop.run_until_complete(serve()), daemon=True).start()
    time.sleep(1.0)
    holder["loop"] = loop
    return holder


@pytest.fixture(scope="module")
def plant():
    neo = [0] * 1400
    neo[1040] = 1000
    neo[1068], neo[1069] = 0, 790
    neo[1130], neo[1131] = 0, 3150
    sau = [0] * 2200
    sau[1000], sau[1001] = 0, 9985
    sau[2101] = 10912
    n = _serve(NEO_PORT, neo)
    s = _serve(SAU_PORT, sau, units=(245,))
    yield n, s
    for h in (n, s):
        h["loop"].call_soon_threadsafe(lambda h=h: asyncio.ensure_future(h["srv"].shutdown()))


def _config(tmp_path):
    cfg = tmp_path / "site.yaml"
    cfg.write_text(f"""site: {{site_id: t, name: test, poll_interval_s: 3600}}
devices:
  - {{id: neo-01, profile: intelineo_master.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: {NEO_PORT}, unit_id: 1, timeout: 1}}}}
  - {{id: pv-array, profile: pv_array_sau.yaml, adapter: modbus_tcp, connection: {{host: 127.0.0.1, port: {SAU_PORT}, unit_id: 245, timeout: 1}}}}
probe:
  hosts: [127.0.0.1]
""")
    return str(cfg)


def test_session_runs_the_steps_reports_and_bundles(plant, tmp_path):
    data = tmp_path / "data"
    s = Session(_config(tmp_path), str(data), hours=0.002, phase1_min=0.05, sau_s=0.5, long_run=False, bundles_dir=str(tmp_path / "bundles"))
    zp = s.run()
    assert os.path.exists(zp)
    names = zipfile.ZipFile(zp).namelist()
    assert "probe.txt" in names and "check.txt" in names and any(n.startswith("trace/neo-01_") for n in names) \
        and any(n.startswith("trace/pv-array_") for n in names) and any(n.startswith("trace/report_") for n in names)
    log = open(s.log_path, encoding="utf-8").read()
    assert "phase 1, master only" in log and "phase 2" in log and "session done" in log
    assert "probe:" in log and "check:" in log
    rep = open(glob.glob(str(data / "trace" / "report_*.md"))[0], encoding="utf-8").read()
    assert "Comm fails" in rep and "None declared" in rep and "Response time" in rep and "set_2101" in rep
    assert "stall catcher" in rep and "Pings:" in rep and "watch read of pv-array every 1 s" in log
    assert any(n.startswith("trace/watch_") for n in names)
    assert not s.sau_dropped


def test_sau_guard_pauses_then_resumes_when_fails_go_on_without_us(plant, tmp_path, monkeypatch):
    data = tmp_path / "data"
    s = Session(_config(tmp_path), str(data), hours=0.004, phase1_min=0.03, sau_s=0.5, long_run=False,
                bundles_dir=str(tmp_path / "bundles"), guard_window_s=3.0)
    calls = {"n": 0}

    def fake_count(since, until=None):          # phase 1: 0 fails; first window: 6 fails (pause); while paused: 4 (resume)
        calls["n"] += 1
        return 0 if calls["n"] == 1 else 6 if calls["n"] == 2 else 4
    monkeypatch.setattr(s, "fail_count", fake_count)
    monkeypatch.setattr(s, "step_probe", lambda: "")
    monkeypatch.setattr(s, "step_check", lambda: "")
    s.run()
    assert s.sau_dropped
    log = open(s.log_path, encoding="utf-8").read()
    assert "SAU reads paused for one window" in log and "SAU reads resumed" in log


def _write_day(tmp_path, date="2026-10-16"):
    d = tmp_path / "trace"
    d.mkdir()
    t0 = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with open(d / f"events_{date}.csv", "w") as f:
        f.write("ts,device,point,old,new\n")
        for a, b, p in ((60, 72, "ecu2_comm_fail"), (62, 72, "ecu3_comm_fail"), (400, 430, "ecu2_comm_fail")):
            f.write(f"{iso(t0 + timedelta(seconds=a))},neo-01,{p},0,1\n{iso(t0 + timedelta(seconds=b))},neo-01,{p},1,0\n")
    with open(d / f"neo-01_{date}.csv", "w") as f:
        f.write("ts,ms,ok_blocks,failed_blocks,pv_prod_kw,power_kw,mains_power_kw,pv_curtailment_pct\n")
        for s in range(600):
            fail = 60 <= s < 72 or 400 <= s < 430
            load_drop = 300 if 200 <= s < 260 else 0
            pv = "" if fail else (400 if 300 <= s < 340 else 1000)          # a 600 kW solar drop at 300 s the gensets take
            gen = 790 - load_drop + (600 if 300 <= s < 340 else 0)
            f.write(f"{iso(t0 + timedelta(seconds=s))},5,3,0,{pv},{gen},0,{31 if not fail else 5}\n")
    with open(d / f"pv-array_{date}.csv", "w") as f:
        f.write("ts,ms,ok_blocks,failed_blocks,ac_power_kw,inverters_online,set_2100,set_2101,set_2102,set_2103\n")
        for s in range(0, 600, 5):
            fail = 60 <= s < 72
            online = 9 if 40 <= s < 80 or 380 <= s < 420 else 10
            f.write(f"{iso(t0 + timedelta(seconds=s))},{900 if fail else 40},{0 if fail else 3},{3 if fail else 0},998.5,{online},0,{10912 if s % 10 else 0},{0 if s % 10 else 10912},0\n")
    with open(d / f"connect_{date}.csv", "w") as f:
        f.write("ts,host,ok,ms,error\n")
        for s in range(0, 600, 60):
            f.write(f"{iso(t0 + timedelta(seconds=s))},127.0.0.1:502,{0 if s == 60 else 1},3,{'timed out' if s == 60 else ''}\n")
    with open(d / f"watch_{date}.csv", "w") as f:            # fail 1 (60-72 s): SAU silent to us, ping fine; fail 3 (400-430): SAU answers us
        f.write("ts,device,kind,ok,ms,detail\n")
        for s in range(600):
            silent = 58 <= s < 70
            f.write(f"{iso(t0 + timedelta(seconds=s))},pv-array,read,{0 if silent else 1},{3000 if silent else 4},{'timeout' if silent else 5}\n")
            f.write(f"{iso(t0 + timedelta(seconds=s))},pv-array,ping,1,1,\n")
    return str(d)


def test_report_reads_fails_sau_words_regulator_and_load_steps(tmp_path):
    d = _write_day(tmp_path)
    ev = report._read(os.path.join(d, "events_2026-10-16.csv"))
    fails = report.fails_from_events(ev)
    assert [(f["session"], f["seconds"]) for f in fails] == [("group 1", 12.0), ("group 2", 10.0), ("group 1", 30.0)]
    assert len(report.episodes(fails)) == 2
    text = report.summarize(d, "2026-10-16")
    assert "3 fails: group 1 2, group 2 1; 2 episodes" in text
    assert "Both sessions within 5 s of each other: 1 of 2" in text
    assert "The SAU answered nobody during the fails" in text or "unanswered" in text
    assert "Connection tests: 10" in text and "failed 1" in text
    assert "set_2101" in text and "group 2 layout" in text
    assert "curtailment % before/during/after 31.0 / 5.0 / 31.0" in text
    assert "Largest fall within one minute: 300 kW" in text
    assert "Solar drops" in text and "1 drops: 1 x unexplained" in text and "the gensets took it" in text
    assert "the count fell 2 times" in text and "in the 30 s before them: 3 of 3" in text
    assert "Stall episodes" in text and "master fail at the same time: yes" in text
    assert "2 x SAU alive (ping ok) but its application answered nobody" in text
    assert "1 x SAU answered us normally" in text


def test_report_on_an_empty_day(tmp_path):
    text = report.summarize(str(tmp_path), "2026-01-01")
    assert "Rows: master 0" in text and "None declared" in text
