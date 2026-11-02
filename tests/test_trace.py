"""The trace and the probe against fake controllers: files, response times, event log, read-only."""

import asyncio
import csv
import glob
import os
import threading
import time

import pytest
from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import ModbusTcpServer

from plantcollector.registry import load_registry
from plantcollector.trace import Trace, probe

NEO_PORT, SAU_PORT = 15031, 15032


def _serve(port, regs, units=(1,)):
    stores = {u: ModbusDeviceContext(hr=ModbusSequentialDataBlock(1, regs), ir=ModbusSequentialDataBlock(1, regs)) for u in units}
    ctx = ModbusServerContext(devices=stores, single=False)
    holder = {"store": stores[units[0]]}

    async def serve():
        holder["srv"] = ModbusTcpServer(ctx, address=("127.0.0.1", port))
        await holder["srv"].serve_forever()

    loop = asyncio.new_event_loop()
    threading.Thread(target=lambda: loop.run_until_complete(serve()), daemon=True).start()
    time.sleep(1.0)
    holder["loop"], holder["regs"] = loop, regs
    return holder


@pytest.fixture(scope="module")
def plant():
    neo = [0] * 1400
    neo[1040] = 1000                                   # PV Actual P
    neo[1268] = 0                                      # no comm fail
    sau = [0] * 2200
    sau[1000], sau[1001] = 0, 9985                     # AP 998.5 kW
    sau[2100], sau[2101] = 0, 11000                    # AP Set 1,100.0 kW as the group 1 session writes it
    n = _serve(NEO_PORT, neo)
    s = _serve(SAU_PORT, sau, units=(245,))
    yield n, s
    for h in (n, s):
        h["loop"].call_soon_threadsafe(lambda h=h: asyncio.ensure_future(h["srv"].shutdown()))


def _registry():
    reg = load_registry("config/site.plant.yaml")
    reg.devices["neo-01"].connection.update({"host": "127.0.0.1", "port": NEO_PORT, "timeout": 1})
    reg.devices["pv-array"].connection.update({"host": "127.0.0.1", "port": SAU_PORT, "timeout": 1})
    return reg


def test_trace_writes_rows_and_connection_tests(plant, tmp_path):
    reg = _registry()
    tr = Trace(reg, str(tmp_path), {"neo-01": 0.2, "pv-array": 0.2}, connect_test_s=0.5)
    for _ in range(3):
        tr.read_once("neo-01"); tr.read_once("pv-array")
    tr.connect_test()
    tr.close()
    neo_rows = list(csv.DictReader(open(glob.glob(str(tmp_path / "neo-01_*.csv"))[0])))
    assert len(neo_rows) == 3 and all(int(r["ok_blocks"]) == 3 and 0 < float(r["ms"]) < 1000 for r in neo_rows)
    assert neo_rows[0]["pv_prod_kw"] == "1000"
    sau_rows = list(csv.DictReader(open(glob.glob(str(tmp_path / "pv-array_*.csv"))[0])))
    assert sau_rows[0]["set_2101"] == "11000" and sau_rows[0]["set_2100"] == "0"
    assert sau_rows[0]["ac_power_kw"] == "998.5"
    con = list(csv.DictReader(open(glob.glob(str(tmp_path / "connect_*.csv"))[0])))
    assert {c["host"] for c in con} == {f"127.0.0.1:{NEO_PORT}", f"127.0.0.1:{SAU_PORT}"} and all(c["ok"] == "1" for c in con)
    assert not glob.glob(str(tmp_path / "events_*.csv"))             # nothing changed: no event file


class _Stub:
    """An adapter whose readings the test dictates, to exercise the event log."""

    def __init__(self, device, values):
        from plantcollector.adapters import reading
        self.device, self.values, self._reading = device, values, reading
        self.last_ok_blocks, self.last_failed_blocks, self.last_ms = 3, 0, 1.0

    def read(self):
        from plantcollector.registry import Quality
        out = {}
        for p in self.device.points:
            if p.id in self.values:
                out[p.id] = self._reading(p, self.values[p.id])
            else:
                out[p.id] = self._reading(p, None, Quality.BAD, "not in this test")
        return out

    def close(self):
        pass


def test_event_log_records_bits_marked_words_and_reachability(tmp_path):
    reg = _registry()
    tr = Trace(reg, str(tmp_path), {"neo-01": 1, "pv-array": 1}, connect_test_s=None)
    neo = _Stub(reg.devices["neo-01"], {"pv_prod_kw": 1000.0, "ecu2_comm_fail": False, "ecu3_comm_fail": False})
    sau = _Stub(reg.devices["pv-array"], {"ac_power_kw": 998.5, "set_2100": 0, "set_2101": 11000, "set_2102": 0})
    tr.adapters = {"neo-01": neo, "pv-array": sau}
    tr.read_once("neo-01"); tr.read_once("pv-array")
    neo.values["ecu2_comm_fail"] = True                 # a comm fail appears
    neo.values["pv_prod_kw"] = 0.0                    # a number without 'edge' changes: not an event
    sau.values["set_2101"], sau.values["set_2102"] = 0, 11000   # the group 2 session's write lands one word further
    sau.values["ac_power_kw"] = 990.0
    tr.read_once("neo-01"); tr.read_once("pv-array")
    neo.values["ecu2_comm_fail"] = False
    sau.last_ok_blocks = 0                              # the SAU stops answering
    tr.read_once("neo-01"); tr.read_once("pv-array")
    tr.close()
    ev = list(csv.DictReader(open(glob.glob(str(tmp_path / "events_*.csv"))[0])))
    changed = [(e["device"], e["point"], e["old"], e["new"]) for e in ev]
    assert ("neo-01", "ecu2_comm_fail", "0", "1") in changed and ("neo-01", "ecu2_comm_fail", "1", "0") in changed
    assert ("pv-array", "set_2101", "11000", "0") in changed and ("pv-array", "set_2102", "0", "11000") in changed
    assert ("pv-array", "reachable", "1", "0") in changed
    assert not any(e["point"] in ("pv_prod_kw", "ac_power_kw") for e in ev)
    assert all(e["ts"].endswith("Z") for e in ev)


def test_trace_loop_runs_for_its_duration_and_marks_a_dead_device(plant, tmp_path):
    reg = _registry()
    reg.devices["main-01"].connection.update({"host": "127.0.0.1", "port": 15999, "timeout": 0.3})
    tr = Trace(reg, str(tmp_path), {"neo-01": 0.1, "main-01": 0.3}, connect_test_s=None)
    tr.run(duration_s=1.0)
    assert tr.rows["neo-01"] >= 5 and tr.failed["neo-01"] == 0
    assert tr.rows["main-01"] >= 2 and tr.failed["main-01"] == tr.rows["main-01"]
    main_rows = list(csv.DictReader(open(glob.glob(str(tmp_path / "main-01_*.csv"))[0])))
    assert all(r["ok_blocks"] == "0" and r["mains_import_kw"] == "" for r in main_rows if "mains_import_kw" in r)


def test_probe_reports_who_answers(plant):
    rows = probe([("127.0.0.1", NEO_PORT, 1), ("127.0.0.1", SAU_PORT, 245), ("127.0.0.1", SAU_PORT, 7), ("127.0.0.1", 15999, 1)], timeout=1)
    assert rows[0]["tcp"] == "open" and rows[0]["modbus"].startswith("answers")
    assert rows[1]["tcp"] == "open" and rows[1]["modbus"].startswith("answers")
    assert rows[2]["tcp"] == "open"                                   # unit 7 does not exist: still a Modbus answer or a timeout, never a crash
    assert rows[3]["tcp"] in ("closed", "timeout") and rows[3]["modbus"].startswith("no connection")


def test_nothing_in_the_trace_writes():
    import inspect
    import plantcollector.trace as t
    src = inspect.getsource(t)
    assert "write_register" not in src and "write_coil" not in src and "write_registers" not in src


def test_watch_pings_and_reads_one_register_on_the_same_connection(plant, tmp_path):
    reg = _registry()
    tr = Trace(reg, str(tmp_path), {"neo-01": 0.3, "pv-array": 1.0}, connect_test_s=None, watch={"pv-array": 0.2})
    tr.run(duration_s=1.5)
    rows = list(csv.DictReader(open(glob.glob(str(tmp_path / "watch_*.csv"))[0])))
    reads = [r for r in rows if r["kind"] == "read"]
    pings = [r for r in rows if r["kind"] == "ping"]
    assert len(reads) >= 4 and all(r["ok"] == "1" for r in reads) and all(float(r["ms"]) < 1000 for r in reads)
    assert reads[0]["detail"] == "0;11000"               # the setpoint words 2100-2101 as the fake SAU holds them
    assert (len(pings) >= 4 and all(r["ok"] == "1" for r in pings)) or (not pings and tr._ping_off)   # no ping in a bare container
    assert tr.watch_rows == len(rows) and tr.stalls == 0
    # the master's rows kept their cadence while the SAU was watched
    neo = list(csv.DictReader(open(glob.glob(str(tmp_path / "neo-01_*.csv"))[0])))
    assert len(neo) >= 4


def test_watch_marks_a_silent_sau(tmp_path):
    reg = _registry()
    reg.devices["pv-array"].connection.update({"port": 15039, "timeout": 0.3})   # nobody listens here
    tr = Trace(reg, str(tmp_path), {"pv-array": 1.0}, connect_test_s=None, watch={"pv-array": 0.2})
    tr.run(duration_s=1.2)
    rows = [r for r in csv.DictReader(open(glob.glob(str(tmp_path / "watch_*.csv"))[0])) if r["kind"] == "read"]
    assert rows and all(r["ok"] == "0" for r in rows) and tr.stalls == len(rows)
    assert all(r["detail"].startswith(("timeout", "error")) for r in rows)


def test_ping_parses_windows_output_as_bytes(monkeypatch):
    """A Turkish Windows prints the reply in the OEM code page; only 'TTL=' is language independent."""
    import subprocess as sp
    from plantcollector import trace as tr
    line = "Yanıt 192.168.0.128: bayt=32 süre<1ms TTL=64\r\n".encode("cp857")

    class R:
        stdout = line
    monkeypatch.setattr(tr, "_icmp_windows", lambda host, t: None)
    monkeypatch.setattr(sp, "run", lambda *a, **k: R())
    ok, ms, detail = tr.ping("192.168.0.128")
    assert ok == 1 and ms == 1.0 and detail == ""
    R.stdout = "Hedef ana bilgisayara ulaşılamıyor.\r\n".encode("cp857")
    ok, ms, detail = tr.ping("192.168.0.128")
    assert ok == 0 and detail == "no reply"
