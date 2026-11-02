"""
Change 01 - PV logger communication tolerance. Builds the modified PLC sheets for InteliConfig from the
originals of 2026-10-14, in the same "PLC sheet export" format, so on the plant PC it is: create the user
setpoint, import the sheets into a copy of the archive, compare, save as a new archive.

  python tools/neo_plc_change01.py          writes docs/changes/change-01_comm_fail_tolerance/

What it changes (see README.md in the output folder):
  sheet 10 PV req step protection a lost session holds the last request instead of forcing it to 0;
                                 a session lost for 5 minutes raises "PV comm lost"
  sheet 3  PV Breaker controll   (optional, stage 2) "PV comm lost" opens the two PV breakers
Everything else is untouched; the program behaves exactly as today while the link is healthy.
"""

from __future__ import annotations

import copy
import os
import shutil
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from neo_plc import _text, describe, load_program  # noqa: E402

SRC = os.path.join(ROOT, "docs", "site", "neo_plc_2026-10-14")
OUT = os.path.join(ROOT, "docs", "changes", "change-01_comm_fail_tolerance")

# --- new objects (free in the program of 14 Oct: PLC-BOUT 13 bits 2..7, PLC-AOUT 39.., user setpoint 65514)
SP_HOLD = 65514                 # new user setpoint "PV comm lost delay" (s, 1 decimal) - created in InteliConfig first
B_COMM_LOST = (10436, 2)        # PV comm fail held longer than 5 minutes (PLC-BOUT 13 bit 2, free on 14 Oct)
LOST_S = 300.0


def read(name: str) -> ET.Element:
    return ET.fromstring(_text(os.path.join(SRC, name)))


def write(root: ET.Element, path: str) -> None:
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    text = '<?xml version="1.0" encoding="utf-16"?>\r\n' + body.replace("\n", "\r\n") + "\r\n"
    with open(path, "wb") as f:
        f.write(text.encode("utf-16"))      # BOM + UTF-16 LE, as InteliConfig writes it


# --- small builders in the export's own vocabulary
def pin_in(name="", comobj=-1, bit=0, *, negated=False, const=None, ctype=-1, cat="Unknown", hidden=False) -> ET.Element:
    e = ET.Element("Input")
    e.set("Name", name)
    e.set("BitIndex", str(bit))
    if negated:
        e.set("IsNegated", "true")
    e.set("ComObject", str(comobj))
    if const is not None:
        e.set("IsConst", "true")
    e.set("ValueOfConstant", str(const if const is not None else 0))
    e.set("ConnectedFunctionType", str(ctype))
    e.set("IsHidden", "true" if hidden else "false")
    e.set("ComObjCategory", cat)
    return e


def pin_out(name, comobj, bit=0, *, cat, res="", dim="", negated=False) -> ET.Element:
    e = ET.Element("Output")
    e.set("Name", name)
    e.set("BitIndex", str(bit))
    if negated:
        e.set("IsNegated", "true")
    e.set("ValueIndex", "0")
    e.set("ComObject", str(comobj))
    e.set("ConnectedFunctionType", "0")
    e.set("ComObjCategory", cat)
    e.set("ResolutionIndex", res)
    e.set("Dimension", dim)
    return e


def block(type_id: int, x: int, y: int, inputs: list, outputs: list, props: list) -> ET.Element:
    b = ET.Element("FunctionBlockExport")
    b.set("Identification", str(type_id))
    pos = ET.SubElement(b, "Position")
    pos.set("X", str(x))
    pos.set("Y", str(y))
    ins = ET.SubElement(b, "Inputs")
    for i in inputs:
        ins.append(i)
    outs = ET.SubElement(b, "Outputs")
    for o in outputs:
        outs.append(o)
    pr = ET.SubElement(b, "Properties")
    for ident, value, vtype in props:
        p = ET.SubElement(pr, "Property")
        p.set("Identification", str(ident))
        p.set("Value", value)
        p.set("ValueType", vtype)
        p.set("ValueIdentification", "-1")
    return b


def bin_in(name, key, negated=False, ctype=0) -> ET.Element:
    return pin_in(name, key[0], key[1], negated=negated, ctype=ctype, cat="BinValue")


def ana_in(name, comobj, ctype=0) -> ET.Element:
    return pin_in(name, comobj, 0, ctype=ctype, cat="AnaValue")


def setpoint_in(name, comobj) -> ET.Element:
    return pin_in(name, comobj, 0, ctype=2, cat="AllSetpoint")


def const_in(value) -> ET.Element:
    return pin_in(const=value)


def hidden_in() -> ET.Element:
    return pin_in(hidden=True)


def or_block(x, y, name, key, *ins) -> ET.Element:
    inputs = list(ins) + [hidden_in()] * (8 - len(ins))
    return block(1, x, y, inputs, [pin_out(name, key[0], key[1], cat="BinValue")], [(0, "1", "UInt")])


def delay_block(x, y, name, key, src: ET.Element, up: ET.Element) -> ET.Element:
    return block(33, x, y, [src, up, const_in(0), pin_in()],
                 [pin_out(name, key[0], key[1], cat="BinValue")], [(6, "False", "Boolean"), (7, "0", "UInt")])


def ana_switch(x, y, name, comobj, in1, in2, sw, dim="kW") -> ET.Element:
    return block(5, x, y, [in1, in2, sw], [pin_out(name, comobj, cat="AnaValue", res="1", dim=dim)], [])


def sheet_io(root, kind, comobj, bit, index, type_, external=None) -> None:
    sec = root.find("PlcProgramExport").find("PlcSheetInputs" if kind == "in" else "PlcSheetOutputs")
    e = ET.SubElement(sec, "SheetInput" if kind == "in" else "SheetOutput")
    e.set("BitIndex", str(bit))
    e.set("Index", str(index))
    e.set("Type", type_)
    if kind == "in":
        e.set("ComObjectNumber", str(comobj))
    else:
        e.set("ComObject", str(comobj))
        ext = ET.SubElement(e, "ExternalOutput")
        for i in external or []:
            ET.SubElement(ext, "int").text = str(i)


def comobj_entry(root, name, number, *, dim="", res="0", bit="", setpoint=False) -> None:
    sec = root.find("CommunicationObjectsExport")
    e = ET.SubElement(sec, "ComObjectExport")
    e.set("Name", name)
    e.set("Dimension", dim)
    e.set("IsUserSetpoint", "true" if setpoint else "false")
    e.set("Resolution", res)
    e.set("ComObjectNumber", str(number))
    e.set("Type", "Setpoint" if setpoint else "Value")
    e.set("BitIndex", str(bit))


def blocks_of(root) -> ET.Element:
    return root.find("PlcProgramExport").find("FunctionBlocks")


# --------------------------------------------------------------------------------------------- the change
# The first draft held the SAU values with two extra Analog Switch blocks; InteliConfig refused the import
# ("exceeded PLC resources: Analog Switch" - the program already uses 7). This version adds no switch at all:
# the existing switch that forces the request to 0 on a comm fail is turned into a hold by feeding it its own
# output, and the only new block is the 300 s "PV comm lost" delay.

def sheet10(with_setpoint: bool) -> ET.Element:
    root = read("sheet10_PV_req_step_protection.xml")
    fb = blocks_of(root)
    blocks = list(fb)
    comm_fail = blocks[5]                      # "PV comm fail" = ECU 2 Comm Fail OR ECU 3 Comm Fail OR PV negative request
    assert comm_fail.get("Identification") == "1" and comm_fail.find("Outputs")[0].get("ComObject") == "10432"
    ins = comm_fail.find("Inputs")
    assert ins[2].get("ComObject") == "10433" and ins[2].get("BitIndex") == "6"
    # a request of 0 must stay 0 (it is no longer forced through the switch), so the "negative request" leaves the OR
    for k, v in (("Name", ""), ("ComObject", "-1"), ("BitIndex", "0"), ("ConnectedFunctionType", "-1"),
                 ("IsHidden", "true"), ("ComObjCategory", "Unknown")):
        ins[2].set(k, v)
    hold = next(b for b in blocks if b.get("Identification") == "5" and b.find("Outputs")[0].get("ComObject") == "21279")
    hin = hold.find("Inputs")
    assert hin[1].get("IsConst") == "true" and hin[1].get("ValueOfConstant") == "0"
    hin.remove(hin[1])                          # constant 0 -> the switch's own output: hold the last request
    hin.insert(1, ana_in("PV req prot comm fail", 21279))
    # 300 s without the logger -> "PV comm lost" (for a warning, and for sheet 3 in stage 2)
    up = setpoint_in("PV comm lost delay", SP_HOLD) if with_setpoint else const_in(int(LOST_S * 10))
    fb.insert(4, delay_block(4, 50, "PV comm lost", B_COMM_LOST, bin_in("PV comm fail", (10432, 1)), up))
    if with_setpoint:
        sheet_io(root, "in", SP_HOLD, 0, 17, "Analog")
        comobj_entry(root, "PV comm lost delay", SP_HOLD, dim="s", res="1", setpoint=True)
    sheet_io(root, "out", B_COMM_LOST[0], B_COMM_LOST[1], 19, "Binary")
    root.find("Sheet").set("Height", "1260")
    return root


def sheet03() -> ET.Element:
    root = read("sheet03_PV_breaker_control.xml")
    fb = blocks_of(root)
    cb1, cb2 = list(fb)[0], list(fb)[1]
    for b in (cb1, cb2):
        ins = b.find("Inputs")
        slot = next(i for i in ins if i.get("IsHidden") == "true")
        new = bin_in("PV comm lost", B_COMM_LOST, negated=True)
        ins.insert(list(ins).index(slot), new)
        ins.remove(slot)
    sheet_io(root, "in", B_COMM_LOST[0], B_COMM_LOST[1], 28, "Binary")
    comobj_entry(root, "PLC-BOUT 13", B_COMM_LOST[0], bit=B_COMM_LOST[1])
    return root


B_ALLOW_INVALID = (10436, 3)    # invalid flag of the multiplier (unused)
A_ALLOWED = 21286               # "PV allowed": 1 while the logger answers or was lost less than the hold time, else 0
A_ALLOWED_2 = 21287             # second output of the composer (unused)
A_REQ_FINAL = 21288             # request after the hold time: held request x PV allowed
HOLD_S = 60.0


def comp4_block(x, y, name, comobj, comobj2, *ins) -> ET.Element:
    inputs = list(ins) + [hidden_in()] * (16 - len(ins))
    return block(53, x, y, inputs, [pin_out(name, comobj, cat="AnaValue", res="0", dim="-"),
                                    pin_out("", comobj2, cat="AnaValue", res="0", dim="-")], [])


def axbc_block(x, y, name, comobj, flag, a, b) -> ET.Element:
    return block(51, x, y, [a, b, const_in(1), const_in(0)],
                 [pin_out(name, comobj, cat="AnaValue", res="1", dim="kW"), pin_out("", flag[0], flag[1], cat="BinValue")],
                 [(22, "0", "UInt"), (37, "0", "UInt"), (23, "False", "Boolean"), (38, "False", "Boolean"), (36, "0", "UInt")])


def sheet10_timed(hold_s: float = HOLD_S) -> ET.Element:
    """Hold through short fails only: after `hold_s` without the logger the request is forced to 0, exactly as the
    program does today for every fail. Blocks with free budget only (Delay, Comp 4, AxB/C)."""
    root = sheet10(False)
    fb = blocks_of(root)
    delay = next(b for b in fb if b.get("Identification") == "33" and b.find("Outputs")[0].get("Name") == "PV comm lost")
    delay.find("Inputs")[1].set("ValueOfConstant", str(int(hold_s * 10)))
    # PV allowed = NOT PV comm lost, as a number (1/0); PV req final = held request x PV allowed
    fb.append(comp4_block(43, 16, "PV allowed", A_ALLOWED, A_ALLOWED_2, bin_in("PV comm lost", B_COMM_LOST, negated=True)))
    fb.append(axbc_block(43, 22, "PV req final", A_REQ_FINAL, B_ALLOW_INVALID,
                         ana_in("PV req prot comm fail", 21279), ana_in("PV allowed", A_ALLOWED)))
    ramp = next(b for b in fb if b.get("Identification") == "61")
    rin = ramp.find("Inputs")[0]
    assert rin.get("ComObject") == "21279"
    rin.set("Name", "PV req final")
    rin.set("ComObject", str(A_REQ_FINAL))
    root.find("Sheet").set("Width", "1800")
    return root


# --------------------------------------------------------------------------------------------- v4: the hold with a guard
# Built after an independent review of the first v4 draft (17 Oct). Free objects on 14 Oct: PLC-BOUT 13 bits 2..7
# and PLC-BOUT 7 bit 0 (seven bits, all used here); PLC-AOUT from 21286 (the export shows 21248..21285 in use by the
# plant). No Analog Switch (none left). Block order: every sheet of the plant lists its blocks in (X, Y) order, so
# the new blocks are placed where that order is also the order they must run in; nothing of the plant moves.
B_COMM_LOST4 = (10436, 2)       # hold time over (as v3)
B_TWO_SETS = (10436, 3)         # two or more gensets loaded (floor above 200 kW)
B_SPENT = (10436, 4)            # the guard has acted CAP_S seconds in a row: it stops for the rest of the fail
B_ABOVE_FLOOR = (10436, 5)      # gensets above their floor (with a 50 kW margin to switch back)
B_GUARD = (10436, 6)            # guard active
B_HOLD_ENDED = (10436, 7)       # hold time over OR fewer than two sets: back to today's 0
B_GUARDED_INV = (10430, 0)      # invalid flag of "PV req guarded"
A_FLOOR_A = 21286               # floor of the loaded sets among Gen 1..3 (kW)
A_FLOOR_B = 21287               # floor of the loaded sets among Gen 4..5 (kW)
A_REQ_FINAL4 = 21288            # the request into the ramp (same object as v3's "PV req final")
A_FLOOR = 21289                 # gensets floor (kW)
A_FLOOR_ON = 21290              # floor + margin (kW)
A_FACTOR = 21291                # 100 normally, 100 - step while the guard acts, 0 on the grid during a fail (%)
A_GUARDED = 21292               # what was sent to the SAU, times the factor: the value the hold keeps
B_AP_INVALID = (10432, 7)       # sheet 6: AP validator invalid flag (exists)
B_MAX_INVALID = (10433, 0)      # sheet 6: Max Available validator invalid flag (exists)
B_NEG_REQ = (10433, 6)          # sheet 10: "PV negative request" (request <= 0), exists
B_ECU2 = (11900, 1)             # ECU 2 = PV logger group 1, the session that writes the setpoint (1 s, archive)

# REAL (IG4 archives of 17 Oct) for Gen 1, 3, 4, 5; Gen 2 is password protected and unread: ASSUMED 1,000 kW
GEN_NOMINAL_KW = (1000, 1000, 700, 1000, 1150)
FLOOR_PCT = 15                  # the guard acts below 15 % of the loaded nominal (Gen P Min is 25 %, reverse power -10 %)
FLOOR_MARGIN_KW = 50            # and stops once the gensets are 50 kW above it again
STEP_PCT = 4                    # while it acts, the held value is multiplied by 0.96 every second cycle (about 20 % per s)
CAP_S = 4.0                     # the guard acts at most 4 s in a row (lowers by about half at most), then rests 60 s
CAP_REST_S = 60.0


def delay2_block(x, y, name, key, src, up_s, down_s) -> ET.Element:
    return block(33, x, y, [src, const_in(int(round(up_s * 10))), const_in(int(round(down_s * 10))), pin_in()],
                 [pin_out(name, key[0], key[1], cat="BinValue")], [(6, "False", "Boolean"), (7, "0", "UInt")])


def andor_block(x, y, name, key, is_or, *ins) -> ET.Element:
    inputs = list(ins) + [hidden_in()] * (8 - len(ins))
    return block(1, x, y, inputs, [pin_out(name, key[0], key[1], cat="BinValue")], [(0, "1" if is_or else "0", "UInt")])


def mux_block(x, y, name, comobj, selects: list, data: list, res="0", dim="kW") -> ET.Element:
    """Mux: three select pins (first = least significant), eight data pins (constants or values); unused selects
    stay unconnected, unused data pins hidden, as in the plant's own sheets 8 and 9."""
    sel = list(selects) + [pin_in() for _ in range(3 - len(selects))]
    dat = [d if isinstance(d, ET.Element) else const_in(d) for d in data] + [hidden_in() for _ in range(8 - len(data))]
    return block(45, x, y, sel + dat, [pin_out(name, comobj, cat="AnaValue", res=res, dim=dim)], [])


def math_block(x, y, name, comobj, op, *ins, res="0", dim="kW") -> ET.Element:
    inputs = list(ins) + [hidden_in()] * (8 - len(ins))
    return block(17, x, y, inputs, [pin_out(name, comobj, cat="AnaValue", res=res, dim=dim)], [(12, str(op), "UInt")])


def hyst_block(x, y, name, key, x_in, on_in, off_in) -> ET.Element:
    return block(3, x, y, [x_in, on_in, off_in], [pin_out(name, key[0], key[1], cat="BinValue")], [])


def axbcd_block(x, y, name, comobj, flag, flag_name, a, b, c, d, res="1", dim="kW") -> ET.Element:
    return block(51, x, y, [a, b, c, d],
                 [pin_out(name, comobj, cat="AnaValue", res=res, dim=dim), pin_out(flag_name, flag[0], flag[1], cat="BinValue")],
                 [(22, "0", "UInt"), (37, "0", "UInt"), (23, "False", "Boolean"), (38, "False", "Boolean"), (36, "0", "UInt")])


def _floor_table(noms, pct) -> list:
    """Mux data: input k = floor of the sets whose select bits are set in k."""
    return [int(sum(n for i, n in enumerate(noms) if k >> i & 1) * pct / 100) for k in range(2 ** len(noms))]


def two_sets_threshold(noms, pct) -> int:
    """A floor above this means two or more sets: halfway between the largest single-set floor and the smallest
    two-set floor (with today's nominals 172 and 255 -> 213)."""
    single = max(int(n * pct / 100) for n in noms)
    pair = min(int((a + b) * pct / 100) for i, a in enumerate(noms) for b in noms[i + 1:])
    assert pair > single, "a pair of sets cannot be told from one set by the floor"
    return (single + pair) // 2


def sheet06_v4() -> ET.Element:
    """Sheet 6 unchanged except that its two existing invalid flags are offered to the other sheets."""
    root = read("sheet06_Validation.xml")
    sheet_io(root, "out", B_AP_INVALID[0], B_AP_INVALID[1], 8, "Binary")
    sheet_io(root, "out", B_MAX_INVALID[0], B_MAX_INVALID[1], 9, "Binary")
    return root


# names of the objects the v4 sheet reads, for its declarations
_NAMES4 = {
    (10196, 0): "Gen Loaded 16", (10196, 1): "Gen Loaded 16", (10196, 2): "Gen Loaded 16", (10196, 3): "Gen Loaded 16",
    (10196, 4): "Gen Loaded 16", 20183: "Gensets P",
}


def _declare(root: ET.Element) -> None:
    """Declare what the sheet reads the way the plant's sheets do: a SheetInput for every object produced outside
    the sheet, a ComObjectExport for every object read; output pins without a name get the object's name."""
    prog = root.find("PlcProgramExport")
    blocks = list(prog.find("FunctionBlocks"))
    produced, fmt = set(), {20183: ("0", "kW")}
    for b in blocks:
        for o in b.find("Outputs"):
            n, bit = int(o.get("ComObject")), int(o.get("BitIndex"))
            produced.add((n, bit) if o.get("ComObjCategory") == "BinValue" else (n, None))
            if o.get("ComObjCategory") != "BinValue":
                fmt[n] = (o.get("ResolutionIndex") or "0", o.get("Dimension") or "")
            if not o.get("Name"):
                o.set("Name", f"PLC-BOUT {n - 10423}.{bit + 1}" if o.get("ComObjCategory") == "BinValue" else f"#{n}")
    declared_in = {(int(e.get("ComObjectNumber")), int(e.get("BitIndex"))) for e in prog.find("PlcSheetInputs")}
    co = root.find("CommunicationObjectsExport")
    listed = {(int(e.get("ComObjectNumber")), e.get("BitIndex")) for e in co}
    used_idx = {int(e.get("Index")) for sec in ("PlcSheetInputs", "PlcSheetOutputs") for e in prog.find(sec)}
    nxt = max(used_idx) + 1
    for b in blocks:
        for i in b.find("Inputs"):
            n = int(i.get("ComObject"))
            if n < 0:
                continue
            binary = i.get("ComObjCategory") == "BinValue"
            bit = int(i.get("BitIndex")) if binary else 0
            key = (n, bit) if binary else (n, None)
            if key not in produced and (n, bit) not in declared_in:
                sheet_io(root, "in", n, bit, nxt, "Binary" if binary else "Analog")
                declared_in.add((n, bit))
                nxt += 1
            lkey = (n, str(bit) if binary else "")
            if lkey not in listed:
                setpoint = i.get("ComObjCategory") == "AllSetpoint"
                name = _NAMES4.get((n, bit) if binary else n) or i.get("Name") or f"#{n}"
                res, dim = ("0", "") if binary else fmt.get(n, ("0", ""))
                comobj_entry(root, name, n, bit=str(bit) if binary else "", setpoint=setpoint, res=res, dim=dim)
                listed.add(lkey)


HOLD_V4_S = 10.0                # v4 holds at most 10 s after the fail is declared, then 0 as today (covers pauses up to
                                # about 10 s; the measured one was 2.5 s)


def sheet10_v4(hold_s: float = HOLD_V4_S, floor_pct: int = FLOOR_PCT, step_pct: int = STEP_PCT,
               noms: tuple = GEN_NOMINAL_KW, cap_s: float = CAP_S) -> ET.Element:
    """The hold with a guard. Healthy link: identical to today, output by output. During a fail it holds what was
    sent to the SAU; on gensets it lowers that value while the gensets sit below their floor (only while the writing
    session works); on the grid, with fewer than two sets, or after `hold_s`, it does what today's program does: 0."""
    root = sheet10(False)                       # v2 (const): the switch holds; "PV comm lost" Delay; no negative-request OR
    fb = blocks_of(root)
    blocks = list(fb)
    lost = next(b for b in blocks if b.get("Identification") == "33" and b.find("Outputs")[0].get("Name") == "PV comm lost")
    lost.find("Inputs")[1].set("ValueOfConstant", str(int(hold_s * 10)))
    # 1. the fail: ECU 2 OR ECU 3 OR AP invalid OR Max Available invalid (the ECU bit already stays about 2 s after
    #    the SAU answers again, so no extra stretch)
    fail_or = next(b for b in blocks if b.get("Identification") == "1" and b.find("Outputs")[0].get("ComObject") == "10432"
                   and b.find("Outputs")[0].get("BitIndex") == "1")
    ins = fail_or.find("Inputs")
    assert ins[2].get("IsHidden") == "true" and ins[3].get("IsHidden") == "true"
    for k, (nm, key) in ((2, ("AP(active Power) validate invalid", B_AP_INVALID)),
                         (3, ("Max Active Power Available invalid", B_MAX_INVALID))):
        ins.remove(ins[k])
        ins.insert(k, bin_in(nm, key))
    # 2. the hold keeps "PV req guarded" (computed last cycle from the ramp output: what was sent, at most one ramp
    #    step of 150 kW away from the value before a corrupted cycle)
    hold = next(b for b in fb if b.get("Identification") == "5" and b.find("Outputs")[0].get("ComObject") == "21279")
    hin = hold.find("Inputs")
    assert hin[1].get("ComObject") == "21279"
    hin.remove(hin[1])
    hin.insert(1, ana_in("PV req guarded", A_GUARDED))
    ramp = next(b for b in fb if b.get("Identification") == "61")
    rin = ramp.find("Inputs")[0]
    assert rin.get("ComObject") == "21279"
    rin.set("Name", "PV req final")
    rin.set("ComObject", str(A_REQ_FINAL4))
    # 3. the new blocks, after the plant's last column (X 36), in the order they run
    held = lambda: ana_in("PV req prot comm fail", 21279)
    two = two_sets_threshold(noms, floor_pct)
    new = [
        mux_block(43, 30, "Gen floor 1-3", A_FLOOR_A,
                  [bin_in("Gen Loaded 1", (10196, 0)), bin_in("Gen Loaded 2", (10196, 1)), bin_in("Gen Loaded 3", (10196, 2))],
                  _floor_table(noms[:3], floor_pct)),
        mux_block(43, 46, "Gen floor 4-5", A_FLOOR_B,
                  [bin_in("Gen Loaded 4", (10196, 3)), bin_in("Gen Loaded 5", (10196, 4))],
                  _floor_table(noms[3:], floor_pct)),
        math_block(52, 30, "Gen floor", A_FLOOR, 0, ana_in("Gen floor 1-3", A_FLOOR_A), ana_in("Gen floor 4-5", A_FLOOR_B)),
        math_block(52, 40, "Gen floor + margin", A_FLOOR_ON, 0, ana_in("Gen floor", A_FLOOR), const_in(FLOOR_MARGIN_KW)),
        hyst_block(60, 30, "Gensets above floor", B_ABOVE_FLOOR, ana_in("Gensets P", 20183),
                   ana_in("Gen floor + margin", A_FLOOR_ON), ana_in("Gen floor", A_FLOOR)),
        hyst_block(60, 38, "Two sets loaded", B_TWO_SETS, ana_in("Gen floor", A_FLOOR), const_in(two), const_in(two)),
        andor_block(68, 30, "PV guard active", B_GUARD, False, bin_in("PV comm fail", (10432, 1)),
                    bin_in("Any MCB close", (10424, 6), negated=True), bin_in("Gensets above floor", B_ABOVE_FLOOR, negated=True),
                    bin_in("ECU 2 Comm Fail", B_ECU2, negated=True, ctype=1), bin_in("PV guard spent", B_SPENT, negated=True)),
        delay2_block(68, 40, "PV guard spent", B_SPENT, bin_in("PV guard active", B_GUARD), cap_s, CAP_REST_S),
        andor_block(68, 48, "PV hold ended", B_HOLD_ENDED, True, bin_in("PV comm lost", B_COMM_LOST4),
                    bin_in("Two sets loaded", B_TWO_SETS, negated=True)),
        mux_block(76, 30, "PV guard factor", A_FACTOR,
                  [bin_in("PV guard active", B_GUARD), bin_in("Any MCB close", (10424, 6)), bin_in("PV comm fail", (10432, 1))],
                  [100, 100, 100, 100, 100, 100 - step_pct, 0, 0], dim="%"),
        axbcd_block(84, 30, "PV req guarded", A_GUARDED, B_GUARDED_INV, "PV req guarded invalid",
                    ana_in("PV req out ramp", 21267), ana_in("PV guard factor", A_FACTOR), const_in(100), const_in(0)),
        mux_block(92, 16, "PV req final", A_REQ_FINAL4,
                  [bin_in("PV hold ended", B_HOLD_ENDED), bin_in("PV negative request", B_NEG_REQ), bin_in("PV comm fail", (10432, 1))],
                  [held(), held(), 0, 0, held(), 0, held(), 0], res="1"),
    ]
    for b in new:
        fb.append(b)
    _declare(root)
    root.find("Sheet").set("Width", "2600")
    root.find("Sheet").set("Height", "1400")
    return root


README = """# Change 01 - PV logger communication tolerance

Built from the sheet exports of 2026-10-14 by `tools/neo_plc_change01.py`. Same export format, so the way
in is: InteliConfig on a copy of the archive -> PLC editor -> import sheet -> compare -> save as a new archive.
Nothing here writes to the controller; the plant loads the new archive itself, at low load, with the
14 Oct archive at hand as the way back.

## v4: the hold with a guard (the version to use; built and tested offline on 17 Oct, not imported yet)

v4 replaces v2 and v3. Both of those let a negative request through to the SAU (they took the "negative request"
out of the fail OR), hold a value that can already be corrupted, and have no protection against a load drop
during the hold. The sections further down describe v2 and v3 and stay for the record.

Files: `sheet10_PV_req_step_protection_v4_guard.xml` and `sheet06_Validation_v4.xml` (**both** must be imported;
sheet 6 only declares its two existing invalid flags as sheet outputs, no block changes there), `program_v4/` the
whole program as the model runs it, `program_v4.md` its listing, `v4_simulation.md` the closed-loop results
(`tools/change01_sim.py`), tests in `tests/test_change01_v4.py`.

| | today | v4 |
|---|---|---|
| healthy link | - | identical to today, output by output (also with a negative manual request: 0) |
| what counts as a fail | ECU 2 OR ECU 3 comm fail OR negative request | ECU 2 OR ECU 3 OR AP invalid OR Max Available invalid (the validators of sheet 6); the negative request still gives 0 while healthy |
| during a fail, on two or more gensets | request forced to 0 | the value **actually sent** to the SAU is held (the ramp output: one corrupted cycle costs at most one ramp step of 150 kW) |
| the guard | - | while held, if Gensets P falls below 15 % of the nominal of the sets actually loaded (Gen Loaded bits x per-set nominal), the held value is lowered by 4 % every second cycle (about 15 % per second) until the gensets are 50 kW above that floor again; only while ECU 2 (the session that writes) has no fail; at most 4 s in a row per fail. Lowering only |
| on the grid, with one genset, or 10 s after the fail is declared | 0 | 0, as today |

Resources: 13 new blocks (no Analog Switch; Mux 2 -> 6, Comp Hyst +2, AND/OR +2, Math Fc +2, Delay +2,
AxB/C+D +1, and v3's Comp 4 / AxB are not used), the seven binary PLC outputs that were still free (PLC-BOUT 13
bits 2-7, PLC-BOUT 7 bit 0) and PLC-AOUT 21286-21292. All new blocks sit after the plant's last column, so the
order they are listed in is also the order of their positions, as in every plant sheet.

Per-set nominals in the two Mux tables: Gen 1 1,000, Gen 2 1,000 (ASSUMED: Gen 2 was not read, password), Gen 3
700, Gen 4 1,000, Gen 5 1,150 kW (IG4 archives 17 Oct). If a set is re-rated or swapped, rebuild with the new
figures. "Two sets loaded" means a floor above 213 kW (largest single-set floor 172, smallest pair 255).

What the closed-loop model shows (details in `v4_simulation.md`; plant model with the measured session timing,
writes through group 1 only, inverters falling 166 kW/s, reverse power 10 % / 5 s):
- a 2.5 s pause like 8 Sep: today about 1.1 MW onto the gensets; v4 nothing;
- the same pause with the AP read corrupted for 0.3 s before the fail bit: v3 about 1 MW, v4 150 kW;
- one session lost and a load drop: largest drop the gensets survive without a reverse-power trip, Gen 4 + 5 at
  2,000 kW load: v3 700 kW, v4 1,200 kW (today 1,400 kW, because it has already thrown the solar away - at the
  price of a ~1.2 MW step at every fail); three sets: v3 1,100, v4 1,500 (today 1,800+);
- the guard is faster than the inverters; its limit is how fast the inverters come down (166 kW/s on 8 Sep) and
  the write delay. On a big drop the reverse-power dip itself is instant; the guard ends it inside the 5 s delay;
- on the grid and with one genset v4 does exactly what today does;
- if the writing session goes silent and its flag comes late, the guard lowers blind for at most 4 s; the step
  when the link returns then stays within about 100 kW of today's.
Known small effects: the held value alternates by up to about 2.5 kW from cycle to cycle (the hold loop runs over
two cycles because the new blocks sit after the switch); the guard may lower somewhat more than needed (the write
every second and the inverters' delay), which costs solar, never safety.

Checks before it goes near the plant:
1. Import both sheets on a **copy** of the archive (never the live controller). InteliConfig must accept the block
   budget and the object numbers (only the Analog Switch limit is known).
2. In the editor: the two floor Mux tables read 0/150/150/300/105/255/255/405 and 0/150/172/322; "PV guard factor"
   reads 100/100/100/100/100/96/0/0; "PV req final" reads held/held/0/0/held/0/held/0; the switch's second input is
   "PV req guarded"; the ramp reads "PV req final" and still shows 25 and 1500 per s; "PV comm lost" shows 10 s up;
   "PV guard spent" shows 4 s up and 60 s down.
3. From the recorder: AP and Max Available are never invalid outside comm fails (otherwise the hold also starts
   then; still safe, it ends in 0 after 10 s as today); Gensets P stays valid during logger fails; the length of
   the real pauses (the 10 s maximum hold is set from the one measured pause of 2.5 s; 15 s would cover pauses up
   to about 15 s, change `HOLD_V4_S` and rebuild).
4. The PLC cycle is 100 ms (the guard's rate assumes it).
5. The kill criteria of the design review still stand: #14364 through a fail, and the read-back through
   one-session fails.
6. Compare with the 14 Oct archive: only sheets 6 and 10 differ; keep the 14 Oct archive as the way back.

## Why

One lost Modbus reply on either PV-logger session raises "ECU 2/3 Comm Fail"; sheet 10 switches the PV request
to 0 the same cycle. The 0 never reaches the SAU (the link is down) - it arrives when the link is back and takes
the PV to zero, ~5 minutes to climb back, gensets slammed, UPS shed. 5-33 such events a day in the history.

## What changes (sheet 10 only; no new switch blocks - the PLC has no Analog Switch left)

| | Before | After |
|---|---|---|
| `PV comm fail` | ECU 2 OR ECU 3 OR "negative request" | ECU 2 OR ECU 3 |
| `PV req prot comm fail` (the switch before the ramp) | request while healthy, **0** while PV comm fail | request while healthy, **the last request held** while PV comm fail (the switch reads its own output) |
| new `PV comm lost` | - | Delay of `PV comm fail`, 300 s (setpoint **PV comm lost delay** or a fixed 300 s) - for a warning, and for sheet 3 in stage 2 |
| sheet 3 (optional, stage 2) | - | PV CB1/CB2 command AND NOT `PV comm lost`: five minutes without the logger opens the PV breakers |

The "negative request" guard is not needed any more: a request of 0 now passes to the SAU as 0 (it was
only ever 0, never negative), and holding would otherwise keep an old value when the controller asks for 0.
With a healthy link the program behaves exactly as today (checked output by output with `tools/neo_plc.py`).

## Files

- `sheet10_PV_req_step_protection_v2.xml` - uses the new setpoint (create it first, step 1)
- `sheet10_PV_req_step_protection_v2_const.xml` - same with a fixed 300 s, no setpoint needed
- `sheet03_PV_breaker_control_v2.xml` - optional, only after the plant agrees to automatic breaker opening
- `program_v2/` - the whole program with the sheets replaced, as the model runs it; `program_v2.md` its listing
- `design_review_v4.md` - the four-role review of v3 and v4, the revised v4 and the measurements that come first
- `sheet10_PV_req_step_protection_v3_timed.xml` - the timed variant: hold the last request for 60 s, then 0 exactly
  as today, no setpoint needed; `program_v3/` is this sheet with the plant's own other sheets. Its "PV comm lost"
  flag is the 60 s delay, so the breaker-opening sheet 3 must not be combined with it as it is; stage 2 with v3
  needs its own 300 s delay.

## Steps on the plant PC (copy of the archive, never the live controller)

1. (setpoint version only) Setpoints -> User setpoints -> add **PV comm lost delay**: unit `s`, 1 decimal,
   range 0.0-3600.0, default **300.0**, group "PV setpoints". If InteliConfig gives it a number other than
   65514, replace `65514` in the sheet file (2 places) before importing - or use the `_const` file.
2. PLC editor -> sheet "PV req step protection" -> import the sheet 10 file. Check: one new Delay block
   `PV comm lost` in the first column; the `PV comm fail` OR reads only the two ECU bits; the switch
   `PV req prot comm fail` has its own output on its second input; the ramp and the SAU write are untouched.
3. Alarms: add a warning on `PV comm lost` ("PV logger lost 5 min").
4. Compile, compare with the 14 Oct archive (only sheet 10 differs), save as a new archive with the date.
5. Controller side (settings, no PLC): ECU modules "PV Logger group 1/2" - response timeout 3,000 -> 5,000 ms;
   one module instead of two once the SAU point table confirms that 2100/2101 is one setpoint.
   Check on the SAU whether it has its own "no command received -> power limit" fail-safe; if so it must be
   longer than a normal fail burst and shorter than 300 s.

## Test, with the plant's approval, at low load, the collector logging

- SAU Ethernet cable out for 30 s: PV must not move; one warning only. Three times.
- Cable out for 6 minutes: `PV comm lost` after 5 min (breakers open only if sheet 3 v2 is loaded), UPS not
  shed; cable back, fault reset, PV continues at the held value or the breakers re-close within the normal ramp.
- Afterwards: daily count of comm-fail warnings, OFF/AUTO toggles and UPS sheddings from the collector.

## Safety during the blind window

While a session is lost the inverters keep their last setpoint in both the old and the new program - the 0 of
the old program never reaches them. What changes is only the moment the link returns: today the PV is sent to
zero, now it continues. The load may fall during the blind window; the running gensets can lose 25 % of their
nominal before any reverse power (788 kW with three gensets, 290 kW with one), the controller's own
protections (bus, mains, anti-islanding, the gensets' reverse power) do not depend on the logger, and after
300 s the PV is taken off deliberately (stage 2) or at least reported. Set the delay shorter (120 s) when
one genset runs alone.

## Rollback

Load the 14 Oct archive; or in the editor put the constant 0 back on the switch's second input.
"""


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    write(sheet10(True), os.path.join(OUT, "sheet10_PV_req_step_protection_v2.xml"))
    write(sheet10(False), os.path.join(OUT, "sheet10_PV_req_step_protection_v2_const.xml"))
    write(sheet03(), os.path.join(OUT, "sheet03_PV_breaker_control_v2.xml"))
    write(sheet10_timed(), os.path.join(OUT, "sheet10_PV_req_step_protection_v3_timed.xml"))
    prog_dir = os.path.join(OUT, "program_v2")
    os.makedirs(prog_dir, exist_ok=True)
    for f in sorted(os.listdir(SRC)):
        if f.endswith(".xml"):
            shutil.copy(os.path.join(SRC, f), os.path.join(prog_dir, f))
    shutil.copy(os.path.join(OUT, "sheet10_PV_req_step_protection_v2.xml"), os.path.join(prog_dir, "sheet10_PV_req_step_protection.xml"))
    shutil.copy(os.path.join(OUT, "sheet03_PV_breaker_control_v2.xml"), os.path.join(prog_dir, "sheet03_PV_breaker_control.xml"))
    # v3 = the timed sheet 10 only, with the plant's own sheet 3: its "PV comm lost" is the 60 s delay, so the
    # breaker-opening sheet 3 (stage 2) must not hang on it; stage 2 with v3 needs its own 300 s delay first
    prog3_dir = os.path.join(OUT, "program_v3")
    os.makedirs(prog3_dir, exist_ok=True)
    for f in sorted(os.listdir(SRC)):
        if f.endswith(".xml"):
            shutil.copy(os.path.join(SRC, f), os.path.join(prog3_dir, f))
    shutil.copy(os.path.join(OUT, "sheet10_PV_req_step_protection_v3_timed.xml"), os.path.join(prog3_dir, "sheet10_PV_req_step_protection.xml"))
    # v4 = v3 + the guard: sheet 10 v4 and sheet 6 offering its two invalid flags, the plant's own other sheets
    write(sheet10_v4(), os.path.join(OUT, "sheet10_PV_req_step_protection_v4_guard.xml"))
    write(sheet06_v4(), os.path.join(OUT, "sheet06_Validation_v4.xml"))
    prog4_dir = os.path.join(OUT, "program_v4")
    os.makedirs(prog4_dir, exist_ok=True)
    for f in sorted(os.listdir(SRC)):
        if f.endswith(".xml"):
            shutil.copy(os.path.join(SRC, f), os.path.join(prog4_dir, f))
    shutil.copy(os.path.join(OUT, "sheet10_PV_req_step_protection_v4_guard.xml"), os.path.join(prog4_dir, "sheet10_PV_req_step_protection.xml"))
    shutil.copy(os.path.join(OUT, "sheet06_Validation_v4.xml"), os.path.join(prog4_dir, "sheet06_Validation.xml"))
    with open(os.path.join(OUT, "program_v4.md"), "w", encoding="utf-8") as f:
        f.write(describe(load_program(prog4_dir)).replace("sheet exports of 2026-10-14", "change 01 v4 applied to the sheet exports of 2026-10-14"))
    prog = load_program(prog_dir)
    with open(os.path.join(OUT, "program_v2.md"), "w", encoding="utf-8") as f:
        f.write(describe(prog).replace("sheet exports of 2026-10-14", "change 01 applied to the sheet exports of 2026-10-14"))
    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as f:
        f.write(README)
    print(f"wrote {OUT}: {len(prog.blocks)} blocks in {len(prog.sheets)} sheets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
