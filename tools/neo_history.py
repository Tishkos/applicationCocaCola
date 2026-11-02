"""
Reads an InteliConfig history export (History.xls) and writes a clean timeline plus a per-day summary.
Read-only.   python tools/neo_history.py History.xls [--out docs/site]

Columns kept: time, reason, mode, MCB1-3 state, running genset nominal (Gen Nom), genset load %, PV actual,
PV maximum, PV request, site load (P Site), mains import per InteliMains, bus voltage and frequency, I/O words.
The per-day summary counts the events that matter for the plant: PV breaker openings, mode toggles, PV logger
communication failures, UPS shedding, bus under-voltage/-frequency, grid transfers.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import os
import sys


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_history(path: str) -> list[dict]:
    import xlrd
    sh = xlrd.open_workbook(path).sheet_by_index(0)
    hdr, seen, cols = sh.row_values(0), {}, []
    for h in hdr:
        seen[h] = seen.get(h, 0) + 1
        cols.append(h if seen[h] == 1 else f"{h}_{seen[h]}")
    out = []
    for i in range(1, sh.nrows):
        d = dict(zip(cols, sh.row_values(i)))
        try:
            ts = dt.datetime.strptime(f"{d['Date']} {str(d['Time'])[:8]}", "%m/%d/%Y %H:%M:%S")
        except (ValueError, KeyError):
            continue
        bit = lambda k: (str(d.get(k, ""))[:1] if isinstance(d.get(k), str) else "")
        out.append({
            "ts": ts.isoformat(sep=" "), "reason": d.get("Reason", ""), "mode": d.get("Mode", ""),
            "mcb1": bit("MCB1"), "mcb2": bit("MCB2"), "mcb3": bit("MCB3"),
            "gen_nom_kw": num(d.get("Gen Nom")), "gen_load_pct": num(d.get("Gen Load%")),
            "pv_kw": num(d.get("Actual P")), "pv_max_kw": num(d.get("Max AP")), "pv_req_kw": num(d.get("PV P req")),
            "p_site_kw": num(d.get("P Site")), "im1_kw": num(d.get("IM1")), "im2_kw": num(d.get("IM2")), "im3_kw": num(d.get("IM3")),
            "v_l1": num(d.get("Vm1")), "f_hz": num(d.get("Mfrq")), "bin": d.get("BIN", ""), "bout": d.get("BOUT", ""),
            "fvst": d.get("FVST", ""), "inverters_working": num(d.get("Work")), "text": d.get("VBat") if d.get("Reason") in ("SetpointChange", "Forced Value", "Groups Linked", "Groups Unlinked", "Terminal") else "",
        })
    out.sort(key=lambda r: r["ts"])
    return out


def day_summary(recs: list[dict]) -> list[dict]:
    days = collections.defaultdict(lambda: collections.Counter())
    extra = collections.defaultdict(dict)
    for r in recs:
        d = r["ts"][:10]
        c = days[d]
        c["events"] += 1
        c["pvcb_opened"] += r["reason"] == "PVCB Opened"
        c["mode_toggles"] += r["reason"] == "SetpointChange" and "CON(8315)" in str(r["text"])
        c["pv_comm_fail"] += "PV Logger" in r["reason"] and "Comm Fail" in r["reason"]
        c["pv_above_request_warn"] += r["reason"].startswith("AHI PV prod")
        c["ups_shedding"] += "Shedding" in r["reason"]
        c["bus_under_f"] += r["reason"] == "Mains/Bus <f"
        if r["reason"] == "Groups Unlinked":
            extra[d].setdefault("to_grid", r["ts"][11:16])
        if r["reason"] == "Groups Linked":
            extra[d].setdefault("to_gensets", r["ts"][11:16])
        if r["reason"] == "PVCB Closed" and r["v_l1"]:
            extra[d].setdefault("v_at_pv_reconnect", []).append(int(r["v_l1"]))
    rows = []
    for d in sorted(days):
        row = {"day": d, **{k: days[d][k] for k in ("events", "pvcb_opened", "mode_toggles", "pv_comm_fail", "pv_above_request_warn", "ups_shedding", "bus_under_f")}}
        row["to_grid"] = extra[d].get("to_grid", "")
        row["to_gensets"] = extra[d].get("to_gensets", "")
        vs = extra[d].get("v_at_pv_reconnect", [])
        row["v_min_at_pv_reconnect"] = min(vs) if vs else ""
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xls")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    recs = read_history(a.xls)
    days = day_summary(recs)
    w = csv.DictWriter(sys.stdout, fieldnames=list(days[0].keys()))
    w.writeheader(); w.writerows(days)
    print(f"\n{len(recs)} events from {recs[0]['ts']} to {recs[-1]['ts']}", file=sys.stderr)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        stem = f"neo_history_{recs[0]['ts'][:10]}_{recs[-1]['ts'][:10]}"
        with open(os.path.join(a.out, stem + ".csv"), "w", newline="", encoding="utf-8") as f:
            cw = csv.DictWriter(f, fieldnames=list(recs[0].keys())); cw.writeheader(); cw.writerows(recs)
        with open(os.path.join(a.out, stem + "_days.csv"), "w", newline="", encoding="utf-8") as f:
            cw = csv.DictWriter(f, fieldnames=list(days[0].keys())); cw.writeheader(); cw.writerows(days)
        print(f"written to {a.out}/{stem}*.csv", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
