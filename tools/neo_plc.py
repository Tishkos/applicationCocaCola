"""
Reader and interpreter for the InteliNeo 5500 PLC program, from InteliConfig "PLC sheet export" XML files.

  python tools/neo_plc.py docs/site/neo_plc_2026-10-14                 readable listing of every sheet, block by block
  python tools/neo_plc.py docs/site/neo_plc_2026-10-14 --out FILE.md  write the listing
  python tools/neo_plc.py docs/site/neo_plc_2026-10-14 --demo         run the program on a few plant situations

The listing is what the controller executes; the interpreter (class Plc) runs the same blocks in the same order so
the behaviour model (tools/plant_model.py) uses the plant's own logic instead of a reconstruction.

Block semantics follow the ComAp PLC block descriptions (100 ms cycle, blocks executed in sheet order, then in the
order they are listed in the export). Where the export does not say what a property means, the reading used here is
marked ASSUMED in BLOCK_TYPES / the listing, so it can be checked in InteliConfig.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(ROOT, "docs", "site", "neo_plc_2026-10-14")

# block type id -> name, from the sheets themselves cross-checked with the ComAp PLC block list
BLOCK_TYPES = {
    1: "AND/OR", 3: "Comp Hyst", 5: "Ana Switch", 17: "Math Fc", 24: "Decomp 4", 33: "Delay", 39: "RS",
    45: "Mux", 51: "AxB/C+D", 52: "Convert", 53: "Comp 4", 54: "Validator", 58: "Breaker", 59: "Interpolation",
    61: "Ramp",
}
MATH_OPS = {0: "add", 1: "subtract", 2: "abs(subtract)", 3: "average", 4: "op4 (minimum or average)", 5: "maximum"}

# user setpoints of the PLC program, values from the controller archive of 2026-10-14 (Setpoints export)
SETPOINTS_2026_10_14 = {
    65515: 2200.0,   # MAX P req 1 PV breaker (kW)
    65516: 10.0,     # UPS2 ON command delay (s)
    65517: 10.0,     # UPS1 ON command delay (s)
    65518: 35.0,     # UPS 2 Load Shedd OFF (%)
    65519: 80.0,     # UPS 2 Load Shedd ON (%)
    65520: 35.0,     # UPS 1 Load Shedd OFF (%)
    65521: 70.0,     # UPS 1 Load Shedd ON (%)
    65522: 60.0,     # PV Prod > PV req del (s)
    65523: 75.0,     # PV Prod > PV req (kW)
    65524: 1000.0,   # PV Q min import (kVAr)
    65525: 100.0,    # PV req step protetion (kW)
    65526: 0.0,      # MAN PV Q req (kVAr)
    65527: 0.0,      # MAN PV P req (kW)
    65528: 100.0,    # PV interpol X2 (%)
    65529: 0.0,      # PV Interpol X1 (%)
    65532: 15.0,     # PV prod > PV req Warning AHI Del (s) - alarm delay, not used inside the sheets
    65534: 0.0,      # Reactive power scheduling mode
    65535: 1300.0,   # Max Export 1 transf (kW)
}

# controller functions behind the sheet outputs (ExternalOutput ids), from the sheet names and the archive
EXTERNAL_OUTPUTS = {
    200042: "ECU write: PV reactive power setpoint (SAU)",
    200044: "ECU write: PV active power setpoint (SAU)",
    200045: "ECU write: reactive power scheduling mode (SAU)",
    823900: "Binary output 1: PV CB1 ON", 823901: "Binary output 2: PV CB2 ON", 823902: "Binary output 3: UPS1 ON",
    823903: "Binary output 4: PV CB1 OFF", 823904: "Binary output 5: PV CB2 OFF", 823905: "Binary output 6: UPS1 OFF",
    823906: "Binary output 7: UPS2 ON", 823907: "Binary output 8: UPS2 OFF",
    1101: "controller binary input function (PVCB feedback, ASSUMED)",
}

# controller-side names for the sheet inputs that are not produced by a block (values the Neo provides)
EXTERNAL_INPUT_NAMES = {
    15594: "PV Actual P (controller)", 15595: "PV Actual Q (controller)", 20183: "Gensets P", 20182: "Gensets Q",
    10658: "Running Nominal Power Of All", 14364: "Curtailment Output (%)", 19433: "AP (SAU active power)",
    19424: "Maximum Active Power Available (SAU)", 19400: "Daily yield (SAU)",
    19419: "Mains Import P (IM1)", 19412: "Mains Import P (IM2)", 19405: "Mains Import P (IM3)",
    19418: "Mains Import Q (IM1)", 19411: "Mains Import Q (IM2)", 19404: "Mains Import Q (IM3)",
    19417: "Mains PF (IM1)", 19410: "Mains PF (IM2)", 19403: "Mains PF (IM3)",
    19436: "LAOD P (IM1)", 19415: "LAOD P (IM2)", 19408: "LAOD P (IM3)",
    19435: "LAOD Q (IM1)", 19414: "LAOD Q (IM2)", 19407: "LAOD Q (IM3)",
    19434: "LAOD PF (IM1)", 19413: "LAOD PF (IM2)", 19406: "LAOD PF (IM3)",
    10169: "MCB Feedback (IM1, ECU-BIN 1)", 10170: "MCB Feedback (IM2, ECU-BIN 2)", 10752: "MCB Feedback (IM3, ECU-BIN 3)",
    8546: "CAN16 (controller present bits)", 10196: "Gen Loaded 1..5", 8235: "Binary Inputs",
    20743: "User Buttons", 9145: "Log Bout 3 (bit 7 = PVCB Close/Open)", 9149: "Log Bout 7 (bit 13 = Logical 1)",
    11897: "Log Bout 10 (bit 0 = FltRes Button Echo)", 11900: "Log Bout 13 (bits 1,2 = ECU 2/3 Comm Fail)",
}


@dataclass
class Pin:
    name: str
    comobj: int
    bit: int
    negated: bool = False
    const: float | None = None      # engineering units (scaled), when the input is a constant
    raw_const: int | None = None
    category: str = ""
    hidden: bool = False
    connected_type: int = -1
    resolution: int | None = None
    dimension: str = ""

    @property
    def connected(self) -> bool:
        return self.comobj >= 0

    @property
    def key(self):
        return (self.comobj, self.bit) if self.is_binary else self.comobj

    @property
    def is_binary(self) -> bool:
        return self.category == "BinValue"


@dataclass
class Block:
    type_id: int
    sheet: str
    sheet_index: int
    order: int
    inputs: list[Pin]
    outputs: list[Pin]
    props: dict[int, str]
    x: int = 0
    y: int = 0

    @property
    def type_name(self) -> str:
        return BLOCK_TYPES.get(self.type_id, f"block type {self.type_id}")


@dataclass
class Sheet:
    name: str
    index: int
    file: str
    blocks: list[Block] = field(default_factory=list)
    external_outputs: dict = field(default_factory=dict)   # (comobj, bit|None) -> [ids]


@dataclass
class Program:
    sheets: list[Sheet]
    resolution: dict[int, int]          # comobj -> decimal places
    names: dict = field(default_factory=dict)       # (comobj, bit|None) -> name
    dimension: dict[int, str] = field(default_factory=dict)
    setpoints: dict[int, str] = field(default_factory=dict)

    @property
    def blocks(self) -> list[Block]:
        return [b for s in self.sheets for b in s.blocks]

    def produced(self) -> set:
        return {o.key for b in self.blocks for o in b.outputs if o.connected}

    def external_inputs(self) -> list[Pin]:
        prod = self.produced()
        seen, out = set(), []
        for b in self.blocks:
            for i in b.inputs:
                if i.connected and i.key not in prod and i.key not in seen:
                    seen.add(i.key)
                    out.append(i)
        return out

    def name_of(self, pin: Pin) -> str:
        key = (pin.comobj, pin.bit) if pin.is_binary else (pin.comobj, None)
        return self.names.get(key) or pin.name or f"#{pin.comobj}"


# --------------------------------------------------------------------------------------------- reading
def _text(path: str) -> str:
    raw = open(path, "rb").read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        txt = raw.decode("utf-16")
    else:
        txt = raw.decode("utf-8", errors="replace")
    return re.sub(r"^\s*<\?xml[^>]*\?>", "", txt, count=1)


def _int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def load_program(directory: str = DEFAULT_DIR) -> Program:
    files = sorted(glob.glob(os.path.join(directory, "*.xml")))
    if not files:
        raise FileNotFoundError(f"no PLC sheet exports in {directory}")
    sheets, resolution, names, dimension, setpoints = [], {}, {}, {}, {}
    generic, from_inputs = {}, {}
    for f in files:
        root = ET.fromstring(_text(f))
        sheet_el = root.find("Sheet")
        name = (sheet_el.attrib.get("Name") if sheet_el is not None else "") or "(no name)"
        idx = {_int(w.attrib.get("SheetIndex"), -1) for w in root.iter("WirePlcSheetExport")}
        idx.discard(-1)
        index = min(idx) if idx else _int(re.sub(r"\D", "", os.path.basename(f)[:7]) or 0)
        sheet = Sheet(name=name, index=index, file=os.path.basename(f))
        for co in root.iter("ComObjectExport"):
            n = _int(co.attrib.get("ComObjectNumber"), -1)
            bit = co.attrib.get("BitIndex", "")
            key = (n, _int(bit) if bit != "" else None)
            generic.setdefault(key, co.attrib.get("Name", ""))
            if bit == "":
                resolution[n] = _int(co.attrib.get("Resolution"), 0)
                dimension[n] = co.attrib.get("Dimension", "")
            if co.attrib.get("IsUserSetpoint") == "true":
                setpoints[n] = co.attrib.get("Name", "")
        for k, b in enumerate(root.iter("FunctionBlockExport")):
            pos = b.find("Position")
            ins, outs = [], []
            for i in b.iter("Input"):
                is_const = i.attrib.get("IsConst") == "true"
                pin = Pin(name=i.attrib.get("Name", ""), comobj=_int(i.attrib.get("ComObject"), -1),
                          bit=_int(i.attrib.get("BitIndex")), negated=i.attrib.get("IsNegated") == "true",
                          raw_const=_int(i.attrib.get("ValueOfConstant")) if is_const else None,
                          category=i.attrib.get("ComObjCategory", ""), hidden=i.attrib.get("IsHidden") == "true",
                          connected_type=_int(i.attrib.get("ConnectedFunctionType"), -1))
                ins.append(pin)
                if pin.connected and pin.name:
                    from_inputs.setdefault((pin.comobj, pin.bit) if pin.is_binary else (pin.comobj, None), pin.name)
            for o in b.iter("Output"):
                res = o.attrib.get("ResolutionIndex", "")
                pin = Pin(name=o.attrib.get("Name", ""), comobj=_int(o.attrib.get("ComObject"), -1),
                          bit=_int(o.attrib.get("BitIndex")), negated=o.attrib.get("IsNegated") == "true",
                          category=o.attrib.get("ComObjCategory", ""), resolution=_int(res) if res != "" else None,
                          dimension=o.attrib.get("Dimension", ""))
                outs.append(pin)
                if pin.connected:
                    key = (pin.comobj, pin.bit) if pin.is_binary else (pin.comobj, None)
                    if pin.name:
                        names.setdefault(key, pin.name)
                    if not pin.is_binary and pin.resolution is not None:
                        resolution.setdefault(pin.comobj, pin.resolution)
                        dimension.setdefault(pin.comobj, pin.dimension)
            props = {_int(p.attrib.get("Identification")): p.attrib.get("Value", "") for p in b.iter("Property")}
            sheet.blocks.append(Block(type_id=_int(b.attrib.get("Identification")), sheet=name, sheet_index=index,
                                      order=k, inputs=ins, outputs=outs, props=props,
                                      x=_int(pos.attrib.get("X")) if pos is not None else 0,
                                      y=_int(pos.attrib.get("Y")) if pos is not None else 0))
        for so in root.iter("SheetOutput"):
            ext = [_int(i.text) for i in so.iter("int")]
            if ext:
                n, bit = _int(so.attrib.get("ComObject"), -1), so.attrib.get("BitIndex", "")
                key = (n, _int(bit)) if so.attrib.get("Type") == "Binary" else (n, None)
                sheet.external_outputs[key] = ext
        sheets.append(sheet)
    sheets.sort(key=lambda s: s.index)
    for src in (from_inputs, generic):
        for key, nm in src.items():
            names.setdefault(key, nm)
    prog = Program(sheets=sheets, resolution=resolution, names=names, dimension=dimension, setpoints=setpoints)
    _scale_constants(prog)
    return prog


def _res(prog: Program, pin: Pin | None) -> int:
    if pin is None or not pin.connected:
        return 0
    return prog.resolution.get(pin.comobj, pin.resolution or 0)


def _scale_constants(prog: Program) -> None:
    """ValueOfConstant is stored in the raw integer of the pin it feeds; convert to engineering units."""
    for b in prog.blocks:
        ins, outs = b.inputs, b.outputs
        first_in = next((p for p in ins if p.connected), None)
        first_out = outs[0] if outs else None
        for k, p in enumerate(ins):
            if p.raw_const is None:
                continue
            if b.type_id in (3, 61, 54):                       # Comp Hyst, Ramp, Validator: in the input's resolution
                r = _res(prog, ins[0] if ins[0].connected else first_in)
            elif b.type_id == 33:                             # Delay: times in 0.1 s
                r = 1
            elif b.type_id == 59:                             # Interpolation: X in input units, Y in output units
                r = _res(prog, ins[0]) if k in (1, 3) else _res(prog, first_out)
            elif b.type_id == 51:                             # AxB/C+D: plain numbers
                r = 0
            else:                                             # Ana Switch, Mux, Math: output units
                r = _res(prog, first_out)
            p.const = p.raw_const / (10 ** r)


# --------------------------------------------------------------------------------------------- listing
def _pin_text(prog: Program, p: Pin) -> str:
    if p.raw_const is not None:
        return f"{p.const:g}"
    if not p.connected:
        return "-"
    base = prog.name_of(p)
    if p.is_binary and (p.comobj, p.bit) not in prog.names and p.name:
        base = p.name
    ref = f"#{p.comobj}.{p.bit}" if p.is_binary else f"#{p.comobj}"
    if p.connected_type == 2 or p.comobj in prog.setpoints:
        ref += " setpoint"
    txt = f"{base} [{ref}]"
    return f"NOT {txt}" if p.negated else txt


def describe_block(prog: Program, b: Block) -> str:
    ins = [p for p in b.inputs if not p.hidden and (p.connected or p.raw_const is not None)]
    outs = [p for p in b.outputs if p.connected]
    t = b.type_id
    I = lambda k: _pin_text(prog, b.inputs[k]) if k < len(b.inputs) else "-"
    O = lambda k: (("NOT " if b.outputs[k].negated else "") + f"{prog.name_of(b.outputs[k]) or b.outputs[k].name}") if k < len(b.outputs) else "-"
    if t == 1:
        op = "OR" if b.props.get(0) == "1" else "AND"
        return f"{O(0)} = {f' {op} '.join(_pin_text(prog, p) for p in ins)}"
    if t == 3:
        return f"{O(0)} = Comp Hyst({I(0)}; on {I(1)}, off {I(2)})"
    if t == 5:
        return f"{O(0)} = Ana Switch({I(0)} when switch 0, {I(1)} when switch 1; switch {I(2)})"
    if t == 17:
        op = MATH_OPS.get(_int(b.props.get(12)), f"op {b.props.get(12)}")
        return f"{O(0)} = {op}({', '.join(_pin_text(prog, p) for p in ins)})"
    if t == 24:
        return f"{', '.join(O(k) for k in range(len(outs)))} = bits {b.props.get(18, '0')}*4+0..3 of {I(0)}"
    if t == 33:
        return f"{O(0)} = Delay({I(0)}; up {I(1)} s, down {I(2)} s)"
    if t == 39:
        return f"{O(0)} = RS(set {I(0)}, reset {I(1)}; reset priority {b.props.get(10)})"
    if t == 45:
        sel = [I(k) for k in range(3) if b.inputs[k].connected]
        data = [_pin_text(prog, p) for p in b.inputs[3:] if not p.hidden]
        return f"{O(0)} = Mux(select bits {', '.join(sel)}; inputs 0..{len(data) - 1}: {', '.join(data)})"
    if t == 51:
        return f"{O(0)} = {I(0)} x {I(1)} / {I(2)} + {I(3)}  (invalid flag -> {O(1)})"
    if t == 52:
        return f"{O(0)} = Convert({I(0)}) (overflow flag -> {O(1)})"
    if t == 53:
        return f"{O(0)} = Comp 4({' + '.join(f'{_pin_text(prog, p)} x {2 ** k}' for k, p in enumerate(ins))})"
    if t == 54:
        return f"{O(0)} = Validator({I(0)}; default {I(1)}) (invalid flag -> {O(1)})"
    if t == 58:
        return (f"{O(0)} / {O(1)} = Breaker(command {I(0)}, feedback {I(3)}); status -> {O(4)}, "
                f"fail to close -> {O(5)}, fail to open -> {O(6)}, other fail -> {O(7)}")
    if t == 59:
        return f"{O(0)} = Interpolation({I(0)}; X1 {I(1)} -> Y1 {I(2)}, X2 {I(3)} -> Y2 {I(4)}) (out of range -> {O(1)}, invalid -> {O(2)})"
    if t == 61:
        return f"{O(0)} = Ramp({I(0)}; up {I(1)} per s, down {I(2)} per s)"
    return f"{', '.join(O(k) for k in range(len(outs)))} = {b.type_name}({', '.join(_pin_text(prog, p) for p in ins)})"


def describe(prog: Program) -> str:
    lines = ["# InteliNeo 5500 PLC program (sheet exports of 2026-10-14)", "",
             "Blocks are listed in execution order. `[#n]` is the controller communication object, `.b` a bit; "
             "`setpoint` marks a user setpoint. Constants are shown in engineering units.", ""]
    for s in prog.sheets:
        lines.append(f"## Sheet {s.index}: {s.name or '(no name)'}")
        lines.append("")
        for k, b in enumerate(s.blocks, 1):
            lines.append(f"{k}. **{b.type_name}** - {describe_block(prog, b)}")
        if s.external_outputs:
            lines.append("")
            lines.append("Outputs wired to controller functions:")
            for (n, bit), ids in s.external_outputs.items():
                nm = prog.names.get((n, bit), prog.names.get((n, None), f"#{n}"))
                lines.append(f"- {nm} -> " + "; ".join(f"{EXTERNAL_OUTPUTS.get(i, f'controller function {i}')}" for i in ids))
        lines.append("")
    lines.append("## Values the program reads from the controller")
    lines.append("")
    for p in prog.external_inputs():
        nm = EXTERNAL_INPUT_NAMES.get(p.comobj)
        shown = prog.name_of(p)
        ref = f"#{p.comobj}.{p.bit}" if p.is_binary else f"#{p.comobj}"
        tag = " (setpoint)" if p.comobj in prog.setpoints else ""
        lines.append(f"- {shown} [{ref}]{tag}" + (f" - {nm}" if nm and nm != shown else ""))
    lines.append("")
    lines.append("## User setpoints used by the program")
    lines.append("")
    for n in sorted(prog.setpoints):
        v = SETPOINTS_2026_10_14.get(n)
        lines.append(f"- {prog.setpoints[n]} [#{n}] = {v:g} {prog.dimension.get(n, '')}" if v is not None else f"- {prog.setpoints[n]} [#{n}]")
    lines.append("")
    lines.append("## Reading notes (to confirm in InteliConfig)")
    lines.append("")
    lines += ["- Math Fc operation codes: 0 add, 1 subtract (code 4 is only used for the site power-factor displays).",
              "- Comp Hyst with the ON level below the OFF level works inverted (output 1 below ON, 0 above OFF); "
              "this is how the UPS blocks use it.",
              "- Mux: the first connected binary inputs are the select bits (first = least significant), the analog "
              "inputs are the data inputs 0..n.",
              "- Decomp 4 with group 0 gives bits 0..3 of the number; Comp 4 builds the number from bits 0..3.",
              "- Breaker block: ON/OFF follow the command, status follows the feedback; the fail outputs come when "
              "feedback does not follow the command (timeout not visible in the export).",
              "- Ramp constants are rates per second in the input's unit; Delay constants are in 0.1 s."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------- interpreter
class Plc:
    """Runs the program. Values are engineering units; None = invalid (####)."""

    def __init__(self, prog: Program, setpoints: dict[int, float] | None = None, breaker_timeout_s: float = 5.0):
        self.prog = prog
        self.setpoints = dict(SETPOINTS_2026_10_14)
        if setpoints:
            self.setpoints.update(setpoints)
        self.values: dict = {}
        self.state: dict = {}
        self.breaker_timeout_s = breaker_timeout_s
        self.inputs: dict = {}

    # --- access
    def _get(self, p: Pin):
        if p.raw_const is not None:
            return p.const
        if not p.connected:
            return None
        if p.connected_type == 2 or p.comobj in self.setpoints and not p.is_binary:
            v = self.setpoints.get(p.comobj)
        else:
            v = self.values.get(p.key, self.inputs.get(p.key))
            if v is None and p.is_binary:
                v = 0
        if p.is_binary and v is not None:
            v = int(bool(v))
            if p.negated:
                v = 1 - v
        return v

    def _set(self, p: Pin, v) -> None:
        if not p.connected:
            return
        if p.is_binary:
            v = int(bool(v)) if v is not None else 0
            if p.negated:
                v = 1 - v
        self.values[p.key] = v

    def get(self, comobj: int, bit: int | None = None):
        return self.values.get((comobj, bit) if bit is not None else comobj)

    def by_name(self, name: str):
        for key, nm in self.prog.names.items():
            if nm == name:
                k = key if key[1] is not None else key[0]
                if k in self.values:
                    return self.values[k]
        return None

    # --- one cycle
    def step(self, inputs: dict, dt: float = 0.1) -> dict:
        """inputs: {comobj: value} for analog, {(comobj, bit): 0/1} for binaries. Returns the value store."""
        self.inputs = inputs
        for b in self.prog.blocks:
            self._run(b, dt)
        return self.values

    def _run(self, b: Block, dt: float) -> None:
        g = self._get
        t = b.type_id
        sid = (b.sheet_index, b.order)
        st = self.state.setdefault(sid, {})
        ins = b.inputs
        outs = b.outputs
        if t == 1:
            vals = [g(p) for p in ins if p.connected]
            if b.props.get(0) == "1":
                out = any(v for v in vals)
            else:
                out = all(v for v in vals) and bool(vals)
            self._set(outs[0], out)
        elif t == 3:
            x, on, off = g(ins[0]), g(ins[1]), g(ins[2])
            q = st.get("q", 0)
            if x is not None and on is not None and off is not None:
                if on > off:
                    q = 1 if x > on else (0 if x < off else q)
                elif on < off:
                    q = 1 if x < on else (0 if x > off else q)
                else:
                    q = 1 if x > on else (0 if x < on else q)
            st["q"] = q
            self._set(outs[0], q)
        elif t == 5:
            self._set(outs[0], g(ins[1]) if g(ins[2]) else g(ins[0]))
        elif t == 17:
            vals = [g(p) for p in ins if p.connected or p.raw_const is not None]
            op = _int(b.props.get(12))
            if any(v is None for v in vals) or not vals:
                out = None
            elif op == 0:
                out = sum(vals)
            elif op == 1:
                out = vals[0] - sum(vals[1:])
            elif op == 2:
                out = abs(vals[0] - sum(vals[1:]))
            elif op in (3, 4):
                out = sum(vals) / len(vals)
            elif op == 5:
                out = max(vals)
            else:
                out = sum(vals)
            self._set(outs[0], out)
        elif t == 24:
            v = g(ins[0])
            n = int(v) if v is not None else 0
            grp = _int(b.props.get(18))
            for k, o in enumerate(outs):
                self._set(o, (n >> (grp * 4 + k)) & 1)
        elif t == 33:
            x = g(ins[0]) or 0
            up, down = g(ins[1]) or 0, g(ins[2]) or 0
            reset = g(ins[3]) if len(ins) > 3 and ins[3].connected else 0
            q, timer = st.get("q", 0), st.get("t", 0.0)
            if reset:
                q, timer = 0, 0.0
            elif x and not q:
                timer += dt
                if timer >= up:
                    q, timer = 1, 0.0
            elif not x and q:
                timer += dt
                if timer >= down:
                    q, timer = 0, 0.0
            else:
                timer = 0.0
            st["q"], st["t"] = q, timer
            self._set(outs[0], q)
        elif t == 39:
            s_, r_ = g(ins[0]), g(ins[1])
            q = st.get("q", 0)
            if r_:
                q = 0
            elif s_:
                q = 1
            st["q"] = q
            self._set(outs[0], q)
        elif t == 45:
            sel = 0
            for k in range(3):
                if ins[k].connected and g(ins[k]):
                    sel |= 1 << k
            data = ins[3:]
            self._set(outs[0], g(data[sel]) if sel < len(data) and (data[sel].connected or data[sel].raw_const is not None) else None)
        elif t == 51:
            a, bb, c, d = g(ins[0]), g(ins[1]), g(ins[2]), g(ins[3]) if len(ins) > 3 else 0
            if None in (a, bb, c) or not c:
                self._set(outs[0], None)
                if len(outs) > 1:
                    self._set(outs[1], 1)
            else:
                self._set(outs[0], a * bb / c + (d or 0))
                if len(outs) > 1:
                    self._set(outs[1], 0)
        elif t == 52:
            v = g(ins[0])
            self._set(outs[0], v)
            if len(outs) > 1:
                self._set(outs[1], v is None)
        elif t == 53:
            n = 0
            for k, p in enumerate(p for p in ins if p.connected):
                if g(p):
                    n |= 1 << k
            self._set(outs[0], n)
            if len(outs) > 1:
                self._set(outs[1], n)
        elif t == 54:
            v = g(ins[0])
            self._set(outs[0], v if v is not None else (g(ins[1]) or 0))
            if len(outs) > 1:
                self._set(outs[1], v is None)
        elif t == 58:
            cmd = g(ins[0]) or 0
            fb = g(ins[3]) or 0
            timer = st.get("t", 0.0) + dt if cmd != fb else 0.0
            st["t"] = timer
            fail = timer >= self.breaker_timeout_s
            self._set(outs[0], cmd)
            self._set(outs[1], not cmd)
            self._set(outs[4], fb)
            self._set(outs[5], fail and cmd)
            self._set(outs[6], fail and not cmd)
            self._set(outs[7], 0)
        elif t == 59:
            x, x1, y1, x2, y2 = (g(ins[k]) for k in range(5))
            if None in (x, x1, y1, x2, y2) or x2 == x1:
                self._set(outs[0], None)
                if len(outs) > 1:
                    self._set(outs[1], 0)
                if len(outs) > 2:
                    self._set(outs[2], 1)
            else:
                oor = x < min(x1, x2) or x > max(x1, x2)
                xc = min(max(x, min(x1, x2)), max(x1, x2))
                self._set(outs[0], y1 + (xc - x1) * (y2 - y1) / (x2 - x1))
                if len(outs) > 1:
                    self._set(outs[1], oor)
                if len(outs) > 2:
                    self._set(outs[2], 0)
        elif t == 61:
            x = g(ins[0])
            up, down = g(ins[1]), g(ins[2])
            en = _int(b.props.get(16), 3)
            y = st.get("y")
            if x is None:
                pass
            elif y is None:
                y = x
            else:
                if x > y:
                    y = min(x, y + up * dt) if (en & 1) and up is not None else x
                elif x < y:
                    y = max(x, y - down * dt) if (en & 2) and down is not None else x
            st["y"] = y
            self._set(outs[0], y)
        else:
            for o in outs:
                self._set(o, None)


# --------------------------------------------------------------------------------------------- demo
def plant_inputs(*, pv_kw: float, max_avail_kw: float = 3520, curtailment_pct: float, gensets_kw: float,
                 running_nominal_kw: float, mains_import_kw: tuple = (0, 0, 0), on_grid: bool = False,
                 gens_loaded: int = 3, pv_cb=(1, 1), ups_fb=(1, 1), comm_fail=(0, 0), pvcb_close=1,
                 fault_reset=0, user_buttons=(1, 1, 1, 1), manual_pv=0, pv_valid: bool = True,
                 neo_pv_sample_kw: float | None = None) -> dict:
    """Controller values as the PLC sees them (one cycle)."""
    neo_pv = pv_kw if neo_pv_sample_kw is None else neo_pv_sample_kw    # the controller's own PV term
    d = {
        15594: pv_kw, 15595: 0.0, 20183: gensets_kw, 20182: 0.0, 10658: running_nominal_kw + neo_pv,
        14364: curtailment_pct, 19433: pv_kw if pv_valid else None, 19424: max_avail_kw if pv_valid else None,
        19400: 0.0,
        19419: mains_import_kw[0], 19412: mains_import_kw[1], 19405: mains_import_kw[2],
        19436: mains_import_kw[0], 19415: mains_import_kw[1], 19408: mains_import_kw[2],
        19418: 0.0, 19411: 0.0, 19404: 0.0, 19435: 0.0, 19414: 0.0, 19407: 0.0,
        19417: 1.0, 19410: 1.0, 19403: 1.0, 19434: 1.0, 19413: 1.0, 19406: 1.0,
        (10169, 0): int(on_grid), (10170, 0): int(on_grid), (10752, 0): int(on_grid),
        (8546, 5): 1, (8546, 6): 1, (8546, 7): 1,
        (8235, 0): pv_cb[0], (8235, 1): pv_cb[1], (8235, 2): ups_fb[0], (8235, 3): ups_fb[1],
        (20743, 0): user_buttons[0], (20743, 1): user_buttons[1], (20743, 2): user_buttons[2], (20743, 3): user_buttons[3],
        (20743, 9): manual_pv,
        (9145, 7): pvcb_close, (9149, 13): 1, (11897, 0): fault_reset,
        (11900, 1): comm_fail[0], (11900, 2): comm_fail[1],
    }
    for k in range(5):
        d[(10196, k)] = int(k < gens_loaded)
    return d


def _settle(plc: Plc, inputs: dict, seconds: float = 120, dt: float = 0.1) -> dict:
    n = int(seconds / dt)
    for _ in range(n):
        plc.step(inputs, dt)
    return plc.values


def demo(prog: Program) -> str:
    out = []
    plc = Plc(prog)
    show = lambda title: out.append(f"{title}\n    PV req (kW) {plc.get(21265)!s:>8}  PV req protected {plc.get(21269)!s:>8}  "
                                    f"setpoint to SAU {plc.get(21267)!s:>8}  Gen Load % {plc.get(21263)!s:>6}  "
                                    f"UPS1 cmd {plc.get(10435, 6)}  UPS2 cmd {plc.get(10435, 7)}  "
                                    f"PV CB1 ON {plc.get(10425, 2)}  Q req {plc.get(21249)}")
    _settle(plc, plant_inputs(pv_kw=1000, curtailment_pct=31.0, gensets_kw=790, running_nominal_kw=3150))
    show("gensets, curtailment 31 % of 3,520 kW, PV at 1,000 kW, gensets 790 kW of 3,150")
    _settle(plc, plant_inputs(pv_kw=1000, curtailment_pct=60.0, gensets_kw=790, running_nominal_kw=3150), 1)
    show("curtailment jumps to 60 % (request 2,112 kW) with PV still at 1,000: step protection")
    _settle(plc, plant_inputs(pv_kw=1000, curtailment_pct=60.0, gensets_kw=790, running_nominal_kw=3150, comm_fail=(1, 0)), 1)
    show("one PV logger session fails for one second")
    _settle(plc, plant_inputs(pv_kw=1000, curtailment_pct=60.0, gensets_kw=790, running_nominal_kw=3150), 1)
    show("one second after the session is back (ramp 25 kW/s up)")
    _settle(plc, plant_inputs(pv_kw=1000, curtailment_pct=60.0, gensets_kw=790, running_nominal_kw=3150), 30)
    show("30 s later")
    plc = Plc(prog)
    _settle(plc, plant_inputs(pv_kw=1041, curtailment_pct=29.6, gensets_kw=788, running_nominal_kw=3150))
    show("14 Oct 12:25 before the OFF toggle: PV 1,041, gensets 788 kW")
    _settle(plc, plant_inputs(pv_kw=1041, curtailment_pct=29.6, gensets_kw=1829, running_nominal_kw=3150,
                              neo_pv_sample_kw=0, pvcb_close=0), 1)
    show("PV breakers opened: gensets pick up 1,829 kW, the controller already dropped its PV term, the SAU value still reads 1,041")
    _settle(plc, plant_inputs(pv_kw=210, curtailment_pct=29.6, gensets_kw=1829, running_nominal_kw=3150,
                              neo_pv_sample_kw=0, pvcb_close=0), 10)
    show("10 s later the SAU value reads 210 (history 12:25:29: Gen Nom 2,940, Gen Load 57 %, both UPS shed)")
    plc = Plc(prog)
    _settle(plc, plant_inputs(pv_kw=1445, curtailment_pct=0, gensets_kw=0, running_nominal_kw=0, gens_loaded=0,
                              on_grid=True, mains_import_kw=(-399, -370, -341)))
    show("1 Oct 08:19 on the grid: PV 1,445 kW, three feeders exporting 1,110 kW")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", nargs="?", default=DEFAULT_DIR)
    ap.add_argument("--out", help="write the listing to this file")
    ap.add_argument("--demo", action="store_true", help="run the program on a few plant situations")
    a = ap.parse_args()
    prog = load_program(a.directory)
    if a.demo:
        print(demo(prog))
        return 0
    text = describe(prog)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {a.out} ({len(prog.blocks)} blocks in {len(prog.sheets)} sheets)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
