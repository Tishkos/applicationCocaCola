"""
What most likely happened in one comm fail, in plain words, from everything the session saw around it:
the pings of the SAU and the master, our own reads of the master, and - when the capture runs - the SAU's
Modbus answers to the master, its TCP acknowledgements, resets, and who else was talking to it at that moment.

Each rule says only what the evidence shows; where the evidence is missing (no mirror) it says so.
"""

from __future__ import annotations

LATE_MS = 1000


def diagnose(start: float, end: float, pings: list[tuple], live=None, master_reads: list[tuple] | None = None,
             master: str | None = None, me: str | None = None) -> dict:
    """start/end: epoch seconds of the fail. pings: (t, device, ok). live: a LiveCapture or None.
    master_reads: (t, ok) of our own reads of the master. Returns {"tag", "text"}."""
    end = end or start + 30
    sau_p = [ok for t, dev, ok in pings if dev == "pv-array" and start - 2 <= t <= end + 2]
    mas_p = [ok for t, dev, ok in pings if dev == "neo-01" and start - 2 <= t <= end + 2]
    reads = [ok for t, ok in (master_reads or []) if start - 2 <= t <= end + 2]
    sau_lost = sum(1 for ok in sau_p if not ok)
    notes: list[str] = []
    tag, text = None, None

    if sau_p and sau_lost == len(sau_p):
        tag = "SAU unreachable"
        text = (f"The SAU did not answer a single ping during the fail ({len(sau_p)} pings): the box itself or the network "
                f"to it was down, not only its program.")
    elif sau_lost:
        notes.append(f"the SAU missed {sau_lost} of {len(sau_p)} pings at that moment")
    mas_lost = sum(1 for ok in mas_p if not ok)
    if tag is None and mas_lost and sau_lost < len(sau_p or [1]):
        tag = "master restart"
        text = (f"The master itself stopped answering ping ({mas_lost} of {len(mas_p)}) while the SAU "
                + ("answered every ping" if not sau_lost else f"answered {len(sau_p) - sau_lost} of {len(sau_p)} pings")
                + ": the master restarted (a configuration write does that) or its own network dropped, and it flagged the "
                "PV link as lost while it came back. Not the SAU.")
    elif mas_p and all(not ok for ok in mas_p):
        notes.append("the master did not answer ping either, so this PC's own network link may have been down")
    if reads and all(not ok for ok in reads):
        notes.append("the master did not answer our reads either")

    w = live.window(start - 12, end + 2) if live is not None else None
    mtx = [x for x in (w["txs"] if w else []) if x["client"] == master]
    if tag is None and mtx:
        late = [x for x in mtx if x["ans_ms"] is not None and x["ans_ms"] >= LATE_MS]
        busy = [x for x in mtx if x["exc"] == 6 and (x["ans_ms"] or 0) >= 500]
        none = [x for x in mtx if x["ans_ms"] is None]
        early = [x for x in late + busy + none if x["ack_ms"] is not None and (x["ans_ms"] is None or x["ans_ms"] - x["ack_ms"] >= 200)]
        worst = max([x["ans_ms"] for x in mtx if x["ans_ms"] is not None] or [0])
        ack_txt = (f"its TCP acknowledged the requests within {max(x['ack_ms'] for x in early):.0f} ms, so they reached the SAU; "
                   if early else "")
        if busy:
            tag = "SAU busy"
            text = (f"The SAU was busy: {ack_txt}its program answered {len(busy)} of the master's requests late with exception 06 "
                    f"(Server Device Busy), the slowest after {worst / 1000:.2f} s.")
        elif late:
            tag = "SAU slow"
            text = (f"The SAU's program was slow: {ack_txt}it answered {len(late)} of the master's requests after 1 s or more "
                    f"(slowest {worst / 1000:.2f} s), longer than the master waits.")
        elif none and early:
            tag = "SAU hung"
            text = (f"The SAU received the master's requests ({ack_txt.rstrip('; ')}) but never answered {len(none)} of them: "
                    f"its program stopped answering.")
        elif none:
            tag = "no answer"
            text = (f"{len(none)} of the master's requests got no answer and no TCP acknowledgement: the SAU's network side, "
                    f"the cable or the switch dropped them.")
        else:
            tag = "master side"
            text = (f"On the wire the SAU answered all {len(mtx)} of the master's requests in time (slowest {worst:.0f} ms). "
                    f"The fail was declared by the master's own module, not caused by a slow SAU.")
        resets = [r for r in w["resets"]]
        if resets:
            notes.append(f"the connection was reset {len(resets)} time(s)")
        if w["retrans"]:
            notes.append(f"{len(w['retrans'])} TCP retransmission(s), a sign of packet loss")
    elif tag is None:
        if sau_p:
            tag = "SAU up"
            text = ("The SAU answered every ping, so the box was up and reachable. The master's own Modbus traffic is not "
                    "visible without the port mirror, so whether the SAU's program answered late can't be shown here; "
                    "in Sineng's own capture of 8 Sep that was the cause (exception 06 after 2.5 s).")
        else:
            tag = "unknown"
            text = "No pings and no capture around this fail, so nothing more can be said."

    if w:
        others = {}
        for t, peer, what, detail in w["other"]:
            if what == "ping" or (what == "web page" and not detail):
                continue                                    # count requests, not packets
            k = (peer, what)
            others[k] = others.get(k, 0) + 1
        clients = {x["client"] for x in w["txs"] if x["client"] not in (master, me)}
        from .livecap import is_log_download
        downloads = [d for t, p, what, d in w["other"] if d and is_log_download(d)]
        if downloads:
            notes.append("someone was downloading a log from the SAU's web page at that moment")
        for (peer, what), n in sorted(others.items(), key=lambda kv: -kv[1])[:3]:
            who = "this PC" if peer == me else peer
            notes.append(f"{who} was using the SAU's {what} at the same time ({n} requests), extra load on the SAU")
        if clients:
            notes.append(f"other Modbus clients of the SAU at that moment: {', '.join(sorted(clients))}")
    if notes:
        text += " Also: " + "; ".join(notes) + "."
    return {"tag": tag, "text": text}
