"""Each genset's share of the gensets' power, worked out from the site master.

The master's Modbus table has no kW per genset. It gives the gensets' power all together (Gensets P), which
gensets have their breaker closed (Gen Loaded bits g1_running ... g5_running) and the nominal power of the
gensets on the bus (Running Nominal Power Of All). Gensets on the bus share the load in proportion to their own
nominal power (the controllers' load sharing), so each one carries about

    gensets power x its nominal / the nominal of the gensets on the bus.

A genset's own nominal is learnt from the running nominal seen with different gensets on the bus: a genset
alone, a set of gensets one more than a set already seen, or a set whose other gensets are known. Until a
genset's nominal is known, the gensets on the bus whose nominal is not known split what is left of the running
nominal equally (all of them equally when nothing is known). A genset still ramping up or down after its breaker
moved carries less than its share for those seconds; that is not seen here. Real kW per genset needs the
genset controllers' own Modbus lists.
"""
from __future__ import annotations

import re
import statistics
import threading
from collections import deque
from datetime import datetime, timedelta, timezone

_RUN = re.compile(r"^g(\d+)_running$")
LO_KW, HI_KW = 50.0, 5000.0          # a plausible genset nominal
SETTLE_S = 20.0                      # history: a running nominal this soon after a breaker moved is not used


def _ts(t) -> float:
    if isinstance(t, (int, float)):
        return float(t)
    d = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _members(mask: int) -> list[int]:
    return [i + 1 for i in range(mask.bit_length()) if mask >> i & 1]


class GensetShares:
    """Learns each genset's nominal and splits the gensets' power by it. Thread-safe."""

    def __init__(self, master_id: str, numbers: list[int], *, keep: int = 30, min_seen: int = 2):
        self.master_id = master_id
        self.numbers = sorted(numbers)
        self.min_seen = max(1, int(min_seen))
        self._keep = keep
        self._seen: dict[int, deque] = {}          # breaker mask -> running nominals seen with it
        self._nominal: dict[int, float] | None = None
        self._lock = threading.Lock()
        self.seeded = False

    @classmethod
    def for_registry(cls, reg) -> "GensetShares | None":
        """For the site master: the genset-kind device with Gen Loaded bits and a running nominal."""
        for d in reg.devices.values():
            if d.kind != "genset" or not d.point("running_nominal_kw"):
                continue
            nums = sorted(int(m.group(1)) for p in d.points if (m := _RUN.match(p.id)))
            if nums:
                return cls(d.id, nums)
        return None

    # ------------------------------------------------------------------ learning
    def observe(self, mask: int, running_nominal) -> None:
        if not mask or not isinstance(running_nominal, (int, float)) or isinstance(running_nominal, bool):
            return
        v = float(running_nominal)
        if not LO_KW <= v <= HI_KW * len(_members(mask)):
            return
        with self._lock:
            q = self._seen.get(mask)
            if q is None:
                q = self._seen[mask] = deque(maxlen=self._keep)
            q.append(v)
            self._nominal = None

    def seed(self, store, since: datetime, until: datetime) -> int:
        """Learn from the stored history of the master (the running nominal and the breaker bits). Returns how
        many readings were used."""
        self.seeded = True
        bits: dict[int, list[tuple[float, bool]]] = {}
        for g in self.numbers:
            bits[g] = [(_ts(t), bool(v >= 0.5)) for t, v in store.history(self.master_id, f"g{g}_running", since, until)
                       if isinstance(v, (int, float))]
        if any(not b for b in bits.values()):
            return 0
        moved = sorted(t for b in bits.values() for i, (t, _v) in enumerate(b) if i and b[i - 1][1] != _v)
        used = 0
        idx = {g: 0 for g in self.numbers}
        mi = 0
        for t, v in store.history(self.master_id, "running_nominal_kw", since, until):
            if not isinstance(v, (int, float)):
                continue
            t = _ts(t)
            mask, known = 0, True
            for g in self.numbers:
                b, i = bits[g], idx[g]
                while i + 1 < len(b) and b[i + 1][0] <= t:
                    i += 1
                idx[g] = i
                if b[i][0] > t:
                    known = False
                    break
                if b[i][1]:
                    mask |= 1 << (g - 1)
            while mi < len(moved) and moved[mi] <= t - SETTLE_S:
                mi += 1
            if not known or (mi < len(moved) and moved[mi] <= t):
                continue                     # a breaker moved within SETTLE_S before this reading
            if mask:
                self.observe(mask, v)
                used += 1
        return used

    def nominals(self) -> dict[int, float]:
        """Each genset's nominal that the readings so far tell (kW)."""
        with self._lock:
            if self._nominal is not None:
                return dict(self._nominal)
            med = {m: statistics.median(q) for m, q in self._seen.items() if len(q) >= self.min_seen}
            known: dict[int, float] = {}
            for _ in range(len(self.numbers) + 1):
                cand: dict[int, list[float]] = {}
                for m, v in med.items():
                    gs = _members(m)
                    unk = [g for g in gs if g not in known]
                    if len(unk) == 1:
                        cand.setdefault(unk[0], []).append(v - sum(known[g] for g in gs if g in known))
                for a, va in med.items():
                    for b, vb in med.items():
                        d = b & ~a
                        if a & ~b == 0 and d and d & (d - 1) == 0:     # b = a and one genset more
                            g = d.bit_length()
                            if g not in known:
                                cand.setdefault(g, []).append(vb - va)
                new = {g: round(statistics.median(v)) for g, v in cand.items()}
                new = {g: float(x) for g, x in new.items() if LO_KW <= x <= HI_KW}
                if not new:
                    break
                known.update(new)
            self._nominal = known
            return dict(known)

    # ------------------------------------------------------------------ the split
    def split(self, total_kw, on: dict[int, bool | None], running_nominal=None) -> dict[int, float | None]:
        """{genset number: kW} for the gensets' power `total_kw` with the breakers `on`: 0 for a genset off the
        bus, None for every genset when a breaker or the power is not known."""
        if not isinstance(total_kw, (int, float)) or isinstance(total_kw, bool) or any(on.get(g) is None for g in self.numbers):
            return {g: None for g in self.numbers}
        S = [g for g in self.numbers if on[g]]
        out: dict[int, float | None] = {g: 0.0 for g in self.numbers}
        if not S:
            return out if abs(float(total_kw)) < 5 else {g: None for g in self.numbers}
        mask = sum(1 << (g - 1) for g in S)
        rn = float(running_nominal) if isinstance(running_nominal, (int, float)) and not isinstance(running_nominal, bool) and running_nominal > 0 else None
        if rn is not None:
            self.observe(mask, rn)
        nom = self.nominals()
        w = {g: nom.get(g) for g in S}
        unk = [g for g in S if w[g] is None]
        if unk:
            have = [w[g] for g in S if w[g] is not None]
            rest = rn - sum(have) if rn is not None else None
            if rest is not None and rest >= LO_KW * len(unk):
                each = rest / len(unk)
            elif have:
                each = sum(have) / len(have)
            else:
                each = 1.0
            for g in unk:
                w[g] = each
        tw = sum(w.values())
        for g in S:
            out[g] = round(float(total_kw) * w[g] / tw, 1)
        return out

    def status(self) -> dict:
        nom = self.nominals()
        return {"master": self.master_id, "gensets": [{"n": g, "nominal_kw": nom.get(g)} for g in self.numbers]}


def seed_window(now: datetime, days: float = 30.0) -> tuple[datetime, datetime]:
    return now - timedelta(days=days), now
