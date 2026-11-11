"""The analyzer block of the Turkey feed (map version 2): a fixed slot per analyzer number after the solar tags,
served from the meter reader's latest values; the solar registers stay exactly as in map version 1."""
import io
import os
import struct
import sys
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(__file__))

from test_meters import METER_1, FakeGateway, _config, _wait  # noqa: E402

from plantcollector import publish as pb  # noqa: E402
from plantcollector.api import create_app  # noqa: E402
from plantcollector.core import Collector  # noqa: E402
from plantcollector.store import Reading  # noqa: E402


def f32(regs):
    return struct.unpack(">f", struct.pack(">HH", *regs))[0]


def test_slots_follow_the_analyzer_number_and_the_solar_map_is_untouched(tmp_path):
    gw = FakeGateway({17: METER_1, 2: {**METER_1, 1142: 55, 1699: 0, 1700: 0, 1701: 1234, 1702: 0}}, unit={17, 2})
    try:
        c = Collector(_config(tmp_path, gw), str(tmp_path / "d"), initial_poll=False)
        try:
            solar_before = [dict(m) for m in pb.build_map(pb.load_tag_catalog())]
            assert len(solar_before) == 305 and max(m["address"] + m["registers"] for m in solar_before) <= pb.QUALITY_START
            assert all(m["quality_address"] < pb.AN_VALUES_START for m in solar_before)
            # the plant's analyzer-1 is slot 1; an added one takes its own number's slot, whatever is in between
            c._update_settings(lambda d: d.update({"analyzer_last": 6}))
            did = c.add_analyzer("127.0.0.1", gw.port, 2, "Blower")["id"]
            assert did == "analyzer-7"
            c.set_register_name(did, "p_total_kw", "Blower kW")
            amap = pb.analyzer_map(c)
            by = {m["name"]: m for m in amap}
            assert by["ANALYZER_1.P_TOTAL_KW"]["address"] == pb.AN_VALUES_START and by["ANALYZER_1.P_TOTAL_KW"]["quality_address"] == pb.AN_QUALITY_START
            assert by["ANALYZER_7.P_TOTAL_KW"]["address"] == pb.AN_VALUES_START + 6 * pb.AN_SLOT_REGISTERS
            assert by["ANALYZER_7.E_IN_KWH"]["address"] == pb.AN_VALUES_START + 6 * pb.AN_SLOT_REGISTERS + 30 and by["ANALYZER_7.E_IN_KWH"]["type"] == "uint32"
            assert by["ANALYZER_7.E_IN_KWH"]["quality_address"] == pb.AN_QUALITY_START + 6 * pb.AN_SLOT_QUALITY + 15
            assert by["ANALYZER_7.P_TOTAL_KW"]["register_name"] == "Blower kW" and by["ANALYZER_7.P_TOTAL_KW"]["analyzer_name"] == "Blower"
            assert {m["slot"] for m in amap} == {1, 7} and len(amap) == 32
            assert pb.build_map(pb.load_tag_catalog()) == solar_before                       # nothing moved
            # removing one empties its slot; the other keeps its registers
            c.remove_analyzer(did)
            assert {m["slot"] for m in pb.analyzer_map(c)} == {1}
            assert pb.analyzer_slot("analyzer-48") == 48 and pb.analyzer_slot("analyzer-49") is None and pb.analyzer_slot("inv-01") is None
        finally:
            c.close()
    finally:
        gw.close()


def test_the_block_is_served_over_modbus_with_the_latest_meter_values(tmp_path):
    from pymodbus.client import ModbusTcpClient
    gw = FakeGateway({17: METER_1, 2: {**METER_1, 1142: 55}}, unit={17, 2})
    try:
        c = Collector(_config(tmp_path, gw), str(tmp_path / "d"), initial_poll=False)
        pub = pb.ModbusPublisher(c, bind="127.0.0.1", port=15041, unit_id=1)
        try:
            did = c.add_analyzer("127.0.0.1", gw.port, 2, "Blower")
            assert c.start_meters() and _wait(lambda: c.meters.cycles >= 1)
            assert _wait(lambda: c.store.value("analyzer-3", "p_total_kw") == 55)
            ts = datetime.now(timezone.utc)
            c.store.ingest("analyzer-1", {"e_in_kwh": Reading("e_in_kwh", 2925749.388, "kWh", ts)})
            pub.start()
            pub.refresh()
            cl = ModbusTcpClient("127.0.0.1", port=15041)
            assert cl.connect()
            hdr = cl.read_holding_registers(address=0, count=9, device_id=1).registers
            assert hdr[4] == 305 and hdr[5] == 2 and hdr[7] == 2 and hdr[8] == pb.AN_SLOTS
            a1 = pb.AN_VALUES_START
            assert round(f32(cl.read_holding_registers(address=a1, count=2, device_id=1).registers), 3) == 27.0        # METER_1's p_total
            e = cl.read_holding_registers(address=a1 + 30, count=2, device_id=1).registers
            assert ((e[0] << 16) | e[1]) == 2925749                                                                     # whole kWh, uint32
            q1 = cl.read_holding_registers(address=pb.AN_QUALITY_START, count=16, device_id=1).registers
            assert q1[0] == 1 and q1[15] == 1
            a3 = pb.AN_VALUES_START + 2 * pb.AN_SLOT_REGISTERS
            assert round(f32(cl.read_holding_registers(address=a3, count=2, device_id=1).registers), 3) == 55.0
            # slot 2 is empty on this test plant: all zero, quality 0
            a2 = pb.AN_VALUES_START + pb.AN_SLOT_REGISTERS
            assert cl.read_holding_registers(address=a2, count=32, device_id=1).registers == [0] * 32
            assert cl.read_holding_registers(address=pb.AN_QUALITY_START + 16, count=16, device_id=1).registers == [0] * 16
            # the block is read-only too, and the last register of the image is readable
            assert cl.write_register(address=a1, value=1, device_id=1).isError()
            assert not cl.read_holding_registers(address=pb.BLOCK_SIZE - 2, count=2, device_id=1).isError()
            cl.close()
            st = pub.status()
            assert st["map_version"] == 2 and st["analyzers"] == 2 and st["tags"] == 305
        finally:
            pub.stop()
            c.close()
    finally:
        gw.close()


def test_the_api_lists_the_block_and_gives_the_tag_file(tmp_path):
    gw = FakeGateway({17: METER_1}, unit={17})
    try:
        c = Collector(_config(tmp_path, gw), str(tmp_path / "d"), initial_poll=False)
        try:
            c.set_register_name("analyzer-1", "e_in_kwh", "Main feeder")
            api = TestClient(create_app(c))
            r = api.get("/api/v1/tags/registers").json()
            assert r["map_version"] == 2 and len(r["tags"]) == 305
            an = r["analyzers"]
            assert an["values_start"] == 2000 and an["slots"] == 48 and len(an["points"]) == 16
            assert [t["name"] for t in an["tags"]][:2] == ["ANALYZER_1.P_TOTAL_KW", "ANALYZER_1.Q_TOTAL_KVAR"]
            j = api.get("/api/v1/tags/analyzers").json()
            e = next(t for t in j["tags"] if t["name"] == "ANALYZER_1.E_IN_KWH")
            assert e["register_name"] == "Main feeder" and e["quality"] == "no data" and e["address"] == 2030
            x = api.get("/api/v1/tags/analyzers.xlsx")
            assert x.status_code == 200 and x.headers["content-disposition"].endswith('"analyzer-tags.xlsx"')
            wb = load_workbook(io.BytesIO(x.content))
            ws = wb["Analyzer tags"]
            rows = list(ws.iter_rows(min_row=2, values_only=True))
            assert len(rows) == 16 and rows[0][0] == "ANALYZER_1.P_TOTAL_KW" and rows[0][4] == 2000 and rows[0][5] == 42001
            assert "How to read" in wb.sheetnames
            csv = api.get("/api/v1/tags/analyzers.csv").text
            assert "ANALYZER_1.E_IN_KWH" in csv and "Main feeder" in csv
        finally:
            c.close()
    finally:
        gw.close()
