"""
Turns a day of trace files into the numbers the questions need: comm fails per session with their lengths,
whether both sessions fall together, the SAU's response times and our connection tests next to the master's
fails, the cadence of the setpoint words the master writes, the regulator through a fail, the load steps.

    python -m plantcollector.cli report-trace --date 2026-10-16          (files in data/trace)
"""

from __future__ import annotations

import collections
import csv
import os
import statistics

from . import sitetime
from datetime import datetime, timedelta


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _read(path: str) -> list[dict]:
    """Rows of a trace CSV; a row whose timestamp does not parse (a cut last line) is dropped, not fatal."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                _ts(r["ts"])
            except (ValueError, KeyError, TypeError):
                continue
            out.append(r)
    return out


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _pct(values: list[float], p: float):
    if not values:
        return None
    vs = sorted(values)
    k = max(0, min(len(vs) - 1, int(round((len(vs) - 1) * p))))
    return vs[k]


def fails_from_events(events: list[dict], device: str = "neo-01") -> list[dict]:
    """Each comm fail as {session, start, end, seconds} from the rising and falling edges of the two bits."""
    out: list[dict] = []
    open_: dict[str, datetime] = {}
    for e in events:
        if e["device"] != device or e["point"] not in ("ecu2_comm_fail", "ecu3_comm_fail"):
            continue
        s = "group 1" if e["point"] == "ecu2_comm_fail" else "group 2"
        t = _ts(e["ts"])
        if e["new"] == "1":
            open_[s] = t
        elif e["new"] == "0" and s in open_:
            t0 = open_.pop(s)
            out.append(dict(session=s, start=t0, end=t, seconds=(t - t0).total_seconds()))
    for s, t0 in open_.items():
        out.append(dict(session=s, start=t0, end=None, seconds=None))
    return sorted(out, key=lambda f: f["start"])


def episodes(fails: list[dict], gap_s: float = 120.0) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for f in fails:
        if groups and (f["start"] - (groups[-1][-1]["end"] or groups[-1][-1]["start"])).total_seconds() <= gap_s:
            groups[-1].append(f)
        else:
            groups.append([f])
    return groups


def _in_fail(t: datetime, fails: list[dict], margin_s: float = 0) -> bool:
    for f in fails:
        end = f["end"] or (f["start"] + timedelta(seconds=600))
        if f["start"] - timedelta(seconds=margin_s) <= t <= end + timedelta(seconds=margin_s):
            return True
    return False


def _drop_catalogue(neo: list[dict], sau: list[dict], fails: list[dict], min_kw: float = 200.0, min_pct: float = 0.2) -> list[str]:
    """Every fall of the solar reading of at least min_kw and min_pct within 30 s, tagged with what else moved:
    the comm-fail bits, the setpoint the SAU holds, the sun (available kW), the inverter counts, the bus
    frequency and voltage, and whether the gensets or the grid took the step (a fall nobody took is a reading)."""
    rows = [(_ts(r["ts"]), r) for r in neo if _num(r.get("pv_prod_kw")) is not None]
    out = ["## Solar drops and their most likely cause (falls of >= 200 kW and >= 20 % within 30 s)"]
    if len(rows) < 2:
        out += ["No usable solar rows.", ""]
        return out
    sau_rows = [(_ts(r["ts"]), r) for r in sau] if sau else []

    def near(seq, t, before_s=0.0, after_s=0.0):
        return [r for tt, r in seq if t - timedelta(seconds=before_s) <= tt <= t + timedelta(seconds=after_s)]

    def setpoint(r):
        a, b = _num(r.get("set_2100")), _num(r.get("set_2101"))
        return None if a is None or b is None else (int(a) << 16 | int(b)) * 0.1

    events, verdicts = [], collections.Counter()
    i = 0
    while i < len(rows):
        t0, r0 = rows[i]
        pv0 = _num(r0["pv_prod_kw"])
        window = [(t, r) for t, r in rows[i:] if (t - t0).total_seconds() <= 30]
        tmin, rmin = min(window, key=lambda x: _num(x[1]["pv_prod_kw"]))
        fall = pv0 - _num(rmin["pv_prod_kw"])
        if fall >= min_kw and pv0 > 0 and fall / pv0 >= min_pct:
            for t, r in window:                                   # the fall starts at the last row still near the level before
                if t >= tmin:
                    break
                if _num(r["pv_prod_kw"]) >= pv0 - 0.05 * pv0:
                    t0, r0 = t, r
            tags = []
            if any(f["start"] - timedelta(seconds=5) <= tmin and (f["end"] or f["start"] + timedelta(seconds=600)) >= t0 - timedelta(seconds=5) for f in fails):
                tags.append("comm fail")
            before_sau, during_sau = near(sau_rows, t0, before_s=20), near(sau_rows, tmin, before_s=5, after_s=10)
            sp0 = next((setpoint(r) for r in reversed(before_sau) if setpoint(r) is not None), None)
            sp1 = next((setpoint(r) for r in during_sau if setpoint(r) is not None), None)
            if sp0 is not None and sp1 is not None and sp1 < 0.5 * max(sp0, 1):
                tags.append(f"setpoint on the SAU fell {sp0:.0f} -> {sp1:.0f} kW")
            av0 = next((_num(r.get("available_kw")) for r in reversed(before_sau) if _num(r.get("available_kw")) is not None), None)
            av1 = next((_num(r.get("available_kw")) for r in during_sau if _num(r.get("available_kw")) is not None), None)
            if av0 is not None and av1 is not None and (av0 - av1) >= 0.5 * fall:
                tags.append(f"the sun fell too (available {av0:.0f} -> {av1:.0f} kW)")
            for key in ("inverters_online", "inverters_working", "inverters_faulty", "inverters_alarm"):
                v0 = next((_num(r.get(key)) for r in reversed(before_sau) if _num(r.get(key)) is not None), None)
                v1 = next((_num(r.get(key)) for r in during_sau if _num(r.get(key)) is not None), None)
                if v0 is not None and v1 is not None and v0 != v1:
                    tags.append(f"{key} {v0:.0f} -> {v1:.0f}")
            span = near(rows, tmin, before_s=(tmin - t0).total_seconds() + 5, after_s=5)
            fz = [_num(r.get("bus_frequency_hz")) for r in span if _num(r.get("bus_frequency_hz")) not in (None, 0)]
            if fz and (min(fz) < 48.5 or max(fz) > 51.5):
                tags.append(f"bus frequency {min(fz):.2f}-{max(fz):.2f} Hz")
            vr = [_num(r.get("bus_voltage_rel_pct")) for r in span if _num(r.get("bus_voltage_rel_pct")) not in (None, 0)]
            if vr and (min(vr) < 85 or max(vr) > 115):
                tags.append(f"bus voltage {min(vr):.0f}-{max(vr):.0f} %")
            g0, g1 = _num(r0.get("power_kw")), max((_num(r.get("power_kw")) or 0) for r in near(rows, tmin, after_s=30))
            m0, m1 = _num(r0.get("mains_power_kw")), max((_num(r.get("mains_power_kw")) or 0) for r in near(rows, tmin, after_s=30))
            took = "the gensets took it" if g0 is not None and g1 - g0 >= 0.3 * fall else \
                   "the grid took it" if m0 is not None and m1 - m0 >= 0.3 * fall else "nobody took it (a reading, not a real drop)"
            if "comm fail" in tags:
                cause = "comm fail -> zero setpoint"
            elif any(t.startswith("setpoint on the SAU fell") for t in tags):
                cause = "zero or low setpoint without a comm fail (invalid-value path)"
            elif any(t.startswith("the sun fell") for t in tags):
                cause = "cloud"
            elif any(t.startswith(("bus frequency", "bus voltage", "inverters_faulty", "inverters_alarm")) for t in tags):
                cause = "inverter grid-protection trip"
            elif took.startswith("nobody"):
                cause = "reading artefact (inverters lost by the SAU)"
            else:
                cause = "unexplained"
            verdicts[cause] += 1
            events.append(f"- {sitetime.hms(t0)} {pv0:.0f} -> {pv0 - fall:.0f} kW in {(tmin - t0).total_seconds():.0f} s: {cause}; {took}"
                          + (f"; {', '.join(tags)}" if tags else ""))
            i += len(window) + 60
        else:
            i += 1
    if not events:
        out += ["None in the master's rows.", ""]
        return out
    out.append(f"{len(events)} drops: " + "; ".join(f"{n} x {c}" for c, n in verdicts.most_common()) + ".")
    out += events[:40]
    if len(events) > 40:
        out.append(f"- ... {len(events) - 40} more")
    out.append("")
    return out


def summarize(trace_dir: str, date: str) -> str:
    """A markdown summary of one day's files. Every number says what it is counted from."""
    neo = _read(os.path.join(trace_dir, f"neo-01_{date}.csv"))
    sau = _read(os.path.join(trace_dir, f"pv-array_{date}.csv"))
    ev = _read(os.path.join(trace_dir, f"events_{date}.csv"))
    con = _read(os.path.join(trace_dir, f"connect_{date}.csv"))
    L = [f"# Trace of {date}", "", f"All times are {sitetime.label()} (the PC's clock; the master's own clock may differ).", ""]
    L.append(f"Rows: master {len(neo)}, SAU {len(sau)}, events {len(ev)}, connection tests {len(con)}.")
    if neo:
        L.append(f"Master read from {sitetime.hms(_ts(neo[0]['ts']))} to {sitetime.hms(_ts(neo[-1]['ts']))} ({sitetime.label()}); reads that got no answer at all: "
                 f"{sum(1 for r in neo if r.get('ok_blocks') == '0')}.")
    L.append("")

    # ---- comm fails
    fails = fails_from_events(ev)
    L.append("## Comm fails (from the master's ECU 2 / ECU 3 bits in the event log)")
    if not fails:
        L.append("None declared while the trace ran.")
    else:
        lens = [f["seconds"] for f in fails if f["seconds"] is not None]
        by = {s: [f for f in fails if f["session"] == s] for s in ("group 1", "group 2")}
        L.append(f"{len(fails)} fails: group 1 {len(by['group 1'])}, group 2 {len(by['group 2'])}; "
                 f"{len(episodes(fails))} episodes (fails closer than 2 min).")
        if lens:
            L.append(f"Length: median {statistics.median(lens):.0f} s, p95 {_pct(lens, 0.95):.0f} s, max {max(lens):.0f} s "
                     f"(what a hold time N has to cover).")
        both = 0
        for f in by["group 1"]:
            if any(abs((g["start"] - f["start"]).total_seconds()) <= 5 for g in by["group 2"]):
                both += 1
        L.append(f"Both sessions within 5 s of each other: {both} of {len(by['group 1'])} group-1 fails "
                 f"(both silent = nothing we write reaches the SAU; one alive = the zero gets through the other).")
        L.append("")
        L.append(f"| start ({sitetime.label()}) | session | seconds |")
        L.append("|---|---|---|")
        for f in fails[:40]:
            secs = "open" if f["seconds"] is None else f"{f['seconds']:.0f}"
            L.append(f"| {sitetime.hms(f['start'])} | {f['session']} | {secs} |")
        if len(fails) > 40:
            L.append(f"| ... | {len(fails) - 40} more | |")
    L.append("")

    # ---- the stall catcher
    watch = _read(os.path.join(trace_dir, f"watch_{date}.csv"))
    if watch:
        reads = [r for r in watch if r.get("kind") == "read"]
        all_pings = [r for r in watch if r.get("kind") == "ping"]
        pings = [r for r in all_pings if r.get("device") != "neo-01"]          # the SAU's; the master's are the control
        master_pings = [r for r in all_pings if r.get("device") == "neo-01"]
        if reads:
            L.append("## The stall catcher (one ping and one one-register read of the SAU every second)")
        else:
            L.append("## The SAU, passive (pinged every second, never read by us: no load from this program)")
            L.append(f"Pings of the SAU: {len(pings)}, failed {sum(1 for r in pings if r.get('ok') != '1')}; "
                     f"pings of the master (the control): {len(master_pings)}, failed {sum(1 for r in master_pings if r.get('ok') != '1')}.")
        rms = [_num(r["ms"]) for r in reads if r.get("ok") == "1" and _num(r["ms"]) is not None]
        bad_reads = [r for r in reads if r.get("ok") != "1"]
        slow = [r for r in reads if r.get("ok") == "1" and (_num(r["ms"]) or 0) >= 1000]
        bad_pings = [r for r in pings if r.get("ok") != "1"]
        if not reads:
            pass
        elif rms:
            L.append(f"Reads: {len(reads)}, answered median {statistics.median(rms):.0f} ms, p95 {_pct(rms, 0.95):.0f} ms, max {max(rms):.0f} ms; "
                     f"unanswered {len(bad_reads)}, answered but slower than 1 s {len(slow)}. Pings: {len(pings)}, failed {len(bad_pings)}.")
        else:
            L.append(f"Reads: {len(reads)}, none answered. Pings: {len(pings)}, failed {len(bad_pings)}.")
        macs = sorted({str(r.get("detail")) for r in watch if r.get("kind") == "arp" and r.get("ok") == "1" and r.get("device") != "neo-01"})
        if macs:
            L.append(f"MAC address seen for the SAU: {', '.join(macs)}" + (" - MORE THAN ONE: two boxes answer for this address (ARP flux or an IP conflict)." if len(macs) > 1 else " (one, as it should be)."))
        stall_rows = sorted(bad_reads + slow, key=lambda r: r["ts"])
        eps: list[list[dict]] = []
        for r in stall_rows:
            if eps and (_ts(r["ts"]) - _ts(eps[-1][-1]["ts"])).total_seconds() <= 10:
                eps[-1].append(r)
            else:
                eps.append([r])
        if eps:
            L.append(f"Stall episodes (unanswered or >= 1 s, gaps under 10 s joined): {len(eps)}:")
            for ep in eps[:30]:
                t0, t1 = _ts(ep[0]["ts"]), _ts(ep[-1]["ts"])
                worst = max((_num(r["ms"]) or 0) for r in ep)
                p_win = [r for r in pings if t0 - timedelta(seconds=2) <= _ts(r["ts"]) <= t1 + timedelta(seconds=2)]
                p_bad = sum(1 for r in p_win if r.get("ok") != "1")
                with_fail = any(f["start"] - timedelta(seconds=15) <= t1 and (f["end"] or f["start"] + timedelta(seconds=600)) >= t0 for f in fails)
                L.append(f"- {sitetime.hms(t0)} for {(t1 - t0).total_seconds() + 1:.0f} s, worst {worst:.0f} ms, "
                         f"{sum(1 for r in ep if r.get('ok') != '1')} unanswered; " + ("no pings" if not p_win else f"ping failed {p_bad}x" if p_bad else "ping fine") + "; "
                         f"master fail at the same time: {'yes' if with_fail else 'no'}")
            if len(eps) > 30:
                L.append(f"- ... {len(eps) - 30} more")
        if fails:
            verdicts = collections.Counter()
            lines = []
            all_gaps = [(_ts(b["ts"]) - _ts(a["ts"])).total_seconds() for a, b in zip(reads, reads[1:])]
            normal_gap = statistics.median(all_gaps) if all_gaps else 1.0
            sau_rows = _read(os.path.join(trace_dir, f"pv-array_{date}.csv"))
            for f in fails:
                lo = f["start"] - timedelta(seconds=12)
                hi = f["end"] or f["start"] + timedelta(seconds=30)
                win_r = [r for r in reads if lo <= _ts(r["ts"]) <= hi]
                win_p = [r for r in pings if lo <= _ts(r["ts"]) <= hi]
                if not win_r and win_p:
                    p_bad = sum(1 for r in win_p if r.get("ok") != "1")
                    v = ("SAU did not answer pings either: the box or the network to it" if p_bad else
                         "SAU answered every ping (the box was up); we do not read it, so its Modbus timing is in the capture section")
                elif not win_r:
                    v = "no watch rows (SAU not watched then)"
                else:
                    timeouts = [r for r in win_r if r.get("ok") != "1" and str(r.get("detail", "")).startswith(("timeout", "error"))]
                    excs = [r for r in win_r if r.get("ok") != "1" and str(r.get("detail", "")).startswith("exception")]
                    worst = max((_num(r["ms"]) or 0) for r in win_r)
                    gaps = [(_ts(b["ts"]) - _ts(a["ts"])).total_seconds() for a, b in zip(win_r, win_r[1:])]
                    hole = max(gaps) if gaps else 0                         # a hole in our reads = our request stuck behind a stalled one
                    queued = hole - normal_gap if hole >= normal_gap + 1.5 else 0
                    for r in sau_rows:                                      # our 5-s poll may be the request that met the stall
                        if lo <= _ts(r["ts"]) <= hi:
                            if r.get("ok_blocks") == "0":
                                timeouts.append(r)
                            elif (_num(r.get("ms")) or 0) >= 2000:
                                worst = max(worst, _num(r["ms"]))
                    p_bad = sum(1 for r in win_p if r.get("ok") != "1")
                    ping_txt = "no pings" if not win_p else ("ping failed too" if p_bad else "ping ok")
                    if timeouts and p_bad:
                        v = f"SAU unreachable ({ping_txt}): the box or the network to it"
                    elif timeouts or worst >= 2000 or queued >= 2:
                        v = f"SAU alive ({ping_txt}) but its application answered nobody for {max(worst, queued * 1000) / 1000:.1f} s: a stall inside the SAU"
                    elif excs:
                        slow_exc = max((_num(r["ms"]) or 0) for r in excs)
                        v = f"SAU answered us with a Modbus exception ({excs[0].get('detail')}) after {slow_exc:.0f} ms ({ping_txt}): the server is alive and the backend is busy; a longer timeout would not help, the hold would"
                    elif worst >= 1000:
                        v = f"SAU slow to us ({worst:.0f} ms) but answering ({ping_txt}): a stall near the master's limit"
                    else:
                        v = f"SAU answered us normally ({ping_txt}): the fail is not the SAU being silent; look at the path to the master or the master's own module"
                verdicts[v] += 1
                lines.append(f"- {sitetime.hms(f['start'])} {f['session']}: {v}")
            L.append("")
            L.append("Verdict per master comm fail (our reads and pings in the 12 s before and during it):")
            L.extend(lines[:40])
            L.append("")
            L.append("Totals: " + "; ".join(f"{n} x {v}" for v, n in verdicts.most_common()) + ".")
        L.append("")

    # ---- the SAU next to the fails
    L.append("## The SAU as we see it (our own reads and connection tests)")
    if sau:
        ms = [_num(r["ms"]) for r in sau if _num(r["ms"]) is not None]
        bad = [r for r in sau if r.get("ok_blocks") == "0"]
        L.append(f"Response time: median {statistics.median(ms):.0f} ms, p95 {_pct(ms, 0.95):.0f} ms, max {max(ms):.0f} ms; "
                 f"reads with no answer: {len(bad)} of {len(sau)}.")
        if fails:
            inside = [r for r in sau if _in_fail(_ts(r["ts"]), fails)]
            inside_bad = sum(1 for r in inside if r.get("ok_blocks") == "0")
            inside_ms = [_num(r["ms"]) for r in inside if _num(r["ms"]) is not None and r.get("ok_blocks") != "0"]
            L.append(f"During the master's fails: {len(inside)} of our reads, {inside_bad} unanswered"
                     + (f", answered ones median {statistics.median(inside_ms):.0f} ms" if inside_ms else "") + ".")
            if inside and inside_bad == 0:
                L.append("The SAU answered us while the master logged a fail: the fault is not the SAU being dead; look at the master's sessions and the write overlap.")
            elif inside and inside_bad == len(inside):
                L.append("The SAU answered nobody during the fails: the SAU box or the network to it.")
    else:
        L.append("No SAU rows (the SAU was not traced).")
    if con:
        fails_con = [r for r in con if r.get("ok") != "1"]
        hosts = sorted({r["host"] for r in con})
        L.append(f"Connection tests: {len(con)} on {', '.join(hosts)}; failed {len(fails_con)}"
                 + (": " + ", ".join(f"{r['ts'][11:19]} {r['host']}" for r in fails_con[:10]) if fails_con else "") + ".")
    L.append("")

    # ---- the inverters on the SAU's RS485 (the counts the SAU reports)
    if sau and "inverters_online" in sau[0]:
        L.append("## Inverters on the SAU's RS485 (online count the SAU reports)")
        pts = [(_ts(r["ts"]), _num(r.get("inverters_online"))) for r in sau if _num(r.get("inverters_online")) is not None]
        if pts:
            vals = [v for _, v in pts]
            drops = [(t, v) for (t, v), (t0, v0) in zip(pts[1:], pts[:-1]) if v < v0]
            L.append(f"Online inverters: max {max(vals):.0f}, min {min(vals):.0f}; the count fell {len(drops)} times"
                     + (": " + ", ".join(f"{sitetime.hms(t)} to {v:.0f}" for t, v in drops[:12]) if drops else "") + ".")
            if fails:
                near = 0
                for f in fails:
                    if any(0 <= (f["start"] - t).total_seconds() <= 30 for t, _ in drops):
                        near += 1
                L.append(f"Master comm fails with an inverter dropping off the RS485 in the 30 s before them: {near} of {len(fails)}. "
                         "A high share says the fail is born on the RS485 bus (inverter off, SAU stalled, master timed out).")
        else:
            L.append("No inverter counts in the SAU rows.")
        L.append("")

    # ---- the setpoint words
    if sau and "set_2101" in sau[0]:
        L.append("## The setpoint words the master writes (read back from the SAU)")
        for key in ("set_2100", "set_2101", "set_2102", "set_2103"):
            vals = [(r["ts"], r.get(key, "")) for r in sau]
            changes = [(t, v) for (t, v), (t0, v0) in zip(vals[1:], vals[:-1]) if v != v0 and v != "" and v0 != ""]
            distinct = len({v for _, v in vals if v != ""})
            L.append(f"- {key}: {distinct} distinct values, {len(changes)} changes between reads.")
        try:
            dec = [(int(float(r["set_2100"])) << 16 | int(float(r["set_2101"]))) * 0.1 for r in sau if r.get("set_2100") not in ("", None) and r.get("set_2101") not in ("", None)]
            if dec:
                L.append(f"- AP Set decoded as 32-bit at 2100-2101 (group 1 layout): min {min(dec):.1f}, max {max(dec):.1f} kW.")
            dec2 = [(int(float(r["set_2101"])) << 16 | int(float(r["set_2102"]))) * 0.1 for r in sau if r.get("set_2101") not in ("", None) and r.get("set_2102") not in ("", None)]
            if dec2:
                L.append(f"- the same decoded at 2101-2102 (group 2 layout): min {min(dec2):.1f}, max {max(dec2):.1f} kW. "
                         "One of the two should look like a real setpoint, the other like garbage; that tells which layout the SAU uses.")
        except ValueError:
            pass
        L.append("")

    # ---- the regulator and the solar through a fail
    if neo and fails:
        L.append("## The master's regulator and the solar through the fails")
        for f in [f for f in fails if f["seconds"]][:5]:
            before = [r for r in neo if f["start"] - timedelta(seconds=30) <= _ts(r["ts"]) < f["start"]]
            during = [r for r in neo if f["start"] <= _ts(r["ts"]) < (f["end"] or f["start"] + timedelta(seconds=600))]
            after = [r for r in neo if (f["end"] or f["start"]) < _ts(r["ts"]) <= (f["end"] or f["start"]) + timedelta(seconds=30)]
            def m(rows, key):
                v = [_num(r.get(key)) for r in rows if _num(r.get(key)) is not None]
                return f"{statistics.mean(v):.1f}" if v else "-"
            L.append(f"- {sitetime.hms(f['start'])} {f['session']} {f['seconds']:.0f} s: curtailment % before/during/after "
                     f"{m(before, 'pv_curtailment_pct')} / {m(during, 'pv_curtailment_pct')} / {m(after, 'pv_curtailment_pct')}; "
                     f"PV kW {m(before, 'pv_prod_kw')} / {m(during, 'pv_prod_kw')} / {m(after, 'pv_prod_kw')}; "
                     f"gensets kW {m(before, 'power_kw')} / {m(during, 'power_kw')} / {m(after, 'power_kw')}.")
        L.append("If the curtailment % collapses during a fail and is still low after it, the hold is undone the moment the link returns.")
        L.append("")

    # ---- every solar drop, with its most likely cause
    if neo:
        L.extend(_drop_catalogue(neo, sau, fails))

    # ---- load steps
    if neo:
        L.append("## Load steps (from gensets + solar + mains, one-minute windows)")
        series = []
        fails_all = fails_from_events(ev)
        for r in neo:
            g, pv = _num(r.get("power_kw")), _num(r.get("pv_prod_kw"))
            if g is None or pv is None:                       # an invalid solar reading (during a fail) is not a load drop
                continue
            if fails_all and _in_fail(_ts(r["ts"]), fails_all, margin_s=5):   # the master reports PV = 0 inside a fail: not load
                continue
            mains = _num(r.get("mains_power_kw"), 0.0)
            series.append((_ts(r["ts"]), g + pv + max(0.0, mains)))
        drops = []
        j = 0
        for i, (t, v) in enumerate(series):
            while j < len(series) and (series[j][0] - t).total_seconds() < 60:
                j += 1
            window = series[i:j]
            if len(window) > 1:
                drops.append((t, v - min(w for _, w in window)))
        if drops:
            worst = max(drops, key=lambda d: d[1])
            L.append(f"Largest fall within one minute: {worst[1]:.0f} kW at {sitetime.hms(worst[0])}; "
                     f"p99 of one-minute falls: {_pct([d for _, d in drops], 0.99):.0f} kW. The reverse-power margin with three sets is about 788 kW.")
        L.append("")
    return "\n".join(L)
