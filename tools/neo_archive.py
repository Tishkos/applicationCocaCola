"""
Reads what can be read from an InteliNeo / InteliGen 4 archive (.aig4) without InteliConfig.
Read-only: it opens the file, prints, and optionally writes text files next to it. Nothing else.

  python tools/neo_archive.py <archive.aig4> [--out <folder>]

What it finds:
  - identity: controller type, firmware, serial
  - the Modbus-master device definitions ("ECU" datapoints): register, type, scale, function per point
    (these are the registers the controller reads from the SAU and the InteliMains - a free register map)
  - the Modbus-master connection table: IP addresses and ports of those devices
  - the names of the PLC sheets, PLC outputs, user setpoints and user protections (the logic's vocabulary)
  - the text records of the history (mode changes, setpoint changes, forced values, group link events)

Parts of the archive are encrypted (the PLC program body, screens); those are listed, not opened.
The binary layout was worked out from one archive (firmware 1.2.0.43) - another version may differ.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import zipfile

SECTION_NAMES = {
    "sheets": (rb"IM210 data aquisition", 400),
}


def bcd(b: int) -> int:
    return (b >> 4) * 10 + (b & 15)


def strings(buf: bytes, minlen: int = 4):
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % minlen, buf):
        yield m.start(), m.group().decode()


def identity(data: bytes) -> dict:
    m = re.search(rb"(Inteli\w+ \d+)-(\d+\.\d+\.\d+\.\d+)", data[:2000])
    serial = data[30:34].hex().upper() if len(data) > 34 else ""
    return {"controller": m.group(1).decode() if m else "?", "firmware": m.group(2).decode() if m else "?", "serial_hint": serial}


def embedded_zips(data: bytes):
    """Each embedded zip holds one entry 'content'. Deflated ones are the Modbus-master datapoint XMLs."""
    starts = [m.start() for m in re.finditer(re.escape(b"PK\x03\x04"), data)]
    ends = [m.start() for m in re.finditer(re.escape(b"PK\x05\x06"), data)]
    out = []
    for s, e in zip(starts, ends):
        try:
            z = zipfile.ZipFile(io.BytesIO(data[s:e + 22]))
            zi = z.infolist()[0]
            if zi.compress_type == 8:
                out.append((s, "xml", z.read(zi.filename).decode("utf-8", "replace")))
            else:
                out.append((s, f"encrypted (method {zi.compress_type}, {zi.file_size} bytes)", ""))
        except Exception as exc:  # noqa: BLE001
            out.append((s, f"unreadable ({exc})", ""))
    return out


def datapoints(xml: str):
    rows = []
    for m in re.finditer(r"<datapoint ([^>]*)/>", xml):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        rows.append(attrs)
    return rows


def connection_table(data: bytes):
    """Modbus-master targets: '<timeout u16> 03 <ip4> <port u16>' records; timeout 1000/3000 ms, port 502."""
    out = []
    for m in re.finditer(rb"[\x00-\xff]{2}\x03\xc0\xa8[\x00-\xff]{2}\xf6\x01", data[:200000]):
        b = m.group()
        out.append({"ip": f"192.168.{b[5]}.{b[6]}", "port": 502, "timeout_ms": int.from_bytes(b[0:2], "little"),
                    "offset": m.start()})
    return out


def names_between(data: bytes, start_marker: bytes, count: int, stop_re=None):
    i = data.find(start_marker)
    if i < 0:
        return []
    out = []
    for off, s in strings(data[i:i + 20000], 3):
        if stop_re and re.match(stop_re, s):
            break
        out.append(s)
        if len(out) >= count:
            break
    return out


def history_text_records(data: bytes, rec_len: int = 222):
    """History records are fixed-size; text reasons sit at offset 11 after a BCD timestamp at 2..7."""
    hits = [m.start() for m in re.finditer(rb"Forced Value|U=\d+,T=|\d \+ \d|\d - \d", data[400000:])]
    if not hits:
        return []
    first = 400000 + hits[0] - 11
    base = first - ((first - 400000) // rec_len) * rec_len
    recs = []
    for t in range(base, len(data) - rec_len + 1, rec_len):
        r = data[t:t + rec_len]
        try:
            d, mo, y, H, M, S = [bcd(b) for b in r[2:8]]
        except Exception:  # noqa: BLE001
            continue
        if not (1 <= mo <= 12 and 1 <= d <= 31 and H < 24 and M < 60 and S < 60 and 20 <= y <= 40):
            continue
        txt = r[11:70].split(b"\x00")[0]
        if re.fullmatch(rb"[\x20-\x7e]{3,}", txt):
            recs.append((f"20{y:02d}-{mo:02d}-{d:02d} {H:02d}:{M:02d}:{S:02d}", txt.decode()))
    return sorted(recs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("archive")
    ap.add_argument("--out", help="folder for the extracted text files (default: none, print only)")
    a = ap.parse_args()
    data = open(a.archive, "rb").read()
    lines = []
    say = lines.append

    ident = identity(data)
    say(f"# {os.path.basename(a.archive)}")
    say(f"controller {ident['controller']}, firmware {ident['firmware']}, {len(data):,} bytes")
    say("")

    say("## Modbus-master device definitions (ECU datapoints)")
    zips = embedded_zips(data)
    n = 0
    for off, kind, xml in zips:
        if kind != "xml":
            say(f"- part at {off}: {kind}")
            continue
        n += 1
        say(f"- definition {n} (at {off}):")
        for dp in datapoints(xml):
            fc = dp.get("function", "?")
            say(f"    FC{fc:>2} addr {dp.get('address', '?'):>5}  type {dp.get('datatype', '?')}  x{float(dp.get('factor', 1)):g}"
                f"  {dp.get('dim', ''):5} {dp.get('name', '')}" + ("  [WRITE by the controller]" if fc in ("5", "6", "16") else ""))
    say("")

    say("## Modbus-master connections (ip:port, timeout)")
    for c in connection_table(data):
        say(f"- {c['ip']}:{c['port']}  timeout {c['timeout_ms']} ms")
    say("")

    say("## PLC sheets")
    for s in names_between(data, b"IM210 data aquisition", 12, stop_re=r"^[^A-Za-z]"):
        say(f"- {s}")
    say("")
    say("## User setpoints")
    seen = set()
    for s in names_between(data, b"PV setpoints", 40, stop_re=r"^(Measure|Mains/Bus Freq)$"):
        if s not in seen:
            seen.add(s); say(f"- {s}")
    say("")
    say("## History - text records (mode / setpoint / forced value / group events)")
    for ts, txt in history_text_records(data):
        say(f"- {ts}  {txt}")

    text = "\n".join(lines)
    print(text)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        open(os.path.join(a.out, "archive_summary.md"), "w", encoding="utf-8").write(text + "\n")
        for i, (off, kind, xml) in enumerate(z for z in zips if z[1] == "xml"):
            open(os.path.join(a.out, f"ecu_definition_{i + 1}.xml"), "w", encoding="utf-8").write(xml)
        print(f"\nwritten to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
