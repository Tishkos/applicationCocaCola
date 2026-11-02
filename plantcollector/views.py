"""
Read models for the dashboard and the API: live summary, time series, tag values.
Everything here is derived from the store; nothing talks to a device.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml

from .core import Collector
from .registry import PROFILES_DIR, Quality, Registry

_G_RUNNING = re.compile(r"^g(\d+)_running$")


_FIXED_OFFSETS_H = {"Asia/Baghdad": 3, "Asia/Riyadh": 3, "Europe/Istanbul": 3, "Asia/Dubai": 4, "Europe/Budapest": 1, "UTC": 0}


def local_tz(reg: Registry):
    """Site time zone. Windows has no tz database unless the `tzdata` package is installed
    (it is in requirements.txt); fall back to a fixed offset, then UTC, rather than fail."""
    name = reg.site.timezone or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        if name in _FIXED_OFFSETS_H:
            return timezone(timedelta(hours=_FIXED_OFFSETS_H[name]), name)
        return timezone.utc


# --- series definitions ------------------------------------------------------------------------

def series_specs(reg: Registry) -> dict[str, tuple[str, list[tuple[str, str]]]]:
    """key -> (aggregation, [(device, point), ...]). Built from whatever the registry has."""
    def having(kind: str | None, *pids: str) -> list[tuple[str, str]]:
        out = []
        for d in reg.devices.values():
            if kind and d.kind != kind:
                continue
            for pid in pids:
                if d.point(pid):
                    out.append((d.id, pid))
                    break
        return out

    specs: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    load = having("meter", "load_kw") or having(None, "load_kw")
    specs["load_kw"] = ("sum", load)
    # bus frequency / voltage: a meter, else the site master's bus measurement, else whatever device has one
    # (never mixed: the inverters sit on an 800 V winding, the bus is 400 V)
    specs["frequency_hz"] = ("avg", having("meter", "frequency_hz") or having(None, "bus_frequency_hz") or having(None, "frequency_hz"))
    specs["voltage_v"] = ("avg", having("meter", "voltage_v") or having(None, "bus_voltage_l1_v") or having(None, "voltage_l1_v"))
    # site PV total: the master's aggregated reading when there is one (what the controller acts on),
    # else the sum of the PV devices
    pv = having("pv", "power_kw", "ac_power_kw")
    specs["pv_kw"] = ("sum", having(None, "pv_prod_kw") or pv)
    specs["pv_available_kw"] = ("sum", having("pv", "available_kw"))
    specs["irradiance_wm2"] = ("avg", having("pv", "irradiance_wm2"))
    specs["genset_kw"] = ("sum", having("genset", "power_kw"))
    specs["mains_kw"] = ("sum", having("grid", "power_kw") or having(None, "mains_power_kw"))
    specs["grid_available"] = ("avg", having("grid", "available") or having(None, "mains_available"))
    specs["bess_kw"] = ("sum", having("bess", "power_kw"))
    specs["soc_pct"] = ("avg", having("bess", "soc_pct"))
    for g in reg.by_kind("genset"):
        for pid in ("power_kw", "fuel_level_pct", "fuel_rate_lph", "coolant_temp_c"):
            if g.point(pid):
                specs[f"{g.id}/{pid}"] = ("sum", [(g.id, pid)])
        # a site master exposes the gensets it supervises as g1_power_kw, g2_power_kw ...
        for p in g.points:
            m = re.match(r"^g(\d+)_power_kw$", p.id)
            if m:
                specs[f"{g.id}/g{m.group(1)}_power_kw"] = ("sum", [(g.id, p.id)])
            if _G_RUNNING.match(p.id):                      # which genset ran when (dashboard timeline)
                specs[f"{g.id}/{p.id}"] = ("avg", [(g.id, p.id)])
        # a site master's capacity on line and spinning reserve (dashboard genset chart)
        for pid in ("running_power_kw", "running_nominal_kw", "actual_reserve_kw"):
            if g.point(pid):
                specs[f"{g.id}/{pid}"] = ("sum", [(g.id, pid)])
    for p in reg.by_kind("pv"):
        for pid in ("power_kw", "ac_power_kw", "energy_today_kwh", "cabin_temp_c"):
            if p.point(pid):
                specs[f"{p.id}/{pid}"] = ("sum", [(p.id, pid)])
    if len(reg.by_kind("grid")) > 1:                        # several mains incomers: one line each
        for d in reg.by_kind("grid"):
            for pid in ("power_kw", "available"):
                if d.point(pid):
                    specs[f"{d.id}/{pid}"] = ("avg", [(d.id, pid)])
    for d in reg.devices.values():                          # device links (dashboard timeline)
        if d.point("comm_error"):
            specs[f"{d.id}/comm_error"] = ("avg", [(d.id, "comm_error")])
    return {k: v for k, v in specs.items() if v[1]}


def pick_step(seconds: float, target_points: int = 420, floor: int = 30) -> int:
    """Bucket size for a window: never finer than the poll interval, ~420 points at most."""
    raw = max(floor, int(seconds / target_points))
    for s in (30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 21600, 86400):
        if raw <= s:
            return s
    return 86400


def series(c: Collector, since: datetime, until: datetime, step: int | None = None,
           keys: list[str] | None = None) -> dict:
    specs = series_specs(c.registry)
    if keys:
        specs = {k: v for k, v in specs.items() if k in keys}
    step = step or pick_step((until - since).total_seconds(), floor=max(30, int(c.registry.site.poll_interval_s)))
    pairs = sorted({pr for _agg, prs in specs.values() for pr in prs})
    data = c.store.resample(since, until, step, pairs) if pairs else {}
    b0 = int(since.timestamp()) // step * step
    b1 = int(until.timestamp()) // step * step
    buckets = list(range(b0, b1 + step, step))
    out: dict[str, list[float | None]] = {}
    for key, (agg, prs) in specs.items():
        cols = [data.get(pr, {}) for pr in prs]
        vals: list[float | None] = []
        for b in buckets:
            xs = [col[b] for col in cols if b in col]
            if not xs:
                vals.append(None)
            else:
                v = sum(xs) if agg == "sum" else sum(xs) / len(xs)
                vals.append(round(v, 3))
        out[key] = vals
    return {"from": since.isoformat(), "to": until.isoformat(), "step": step,
            "t": [b * 1000 for b in buckets], "series": out}


# --- live summary --------------------------------------------------------------------------------

class SummaryCache:
    def __init__(self, ttl: float = 20.0):
        self.ttl, self._at, self._val = ttl, 0.0, None

    def get(self, fn):
        if time.time() - self._at > self.ttl:
            self._val, self._at = fn(), time.time()
        return self._val


_daily_cache = SummaryCache(30.0)


def device_comms(c: Collector, did: str) -> str:
    dev = c.registry.devices[did]
    rows = {pid: r for pid, r in c.store.latest(did).items() if not (dev.point(pid) and dev.point(pid).raw.get("derived"))}
    if not rows:
        return "lost"
    if all(r.quality == Quality.BAD for r in rows.values()):
        # a device whose profile has no register addresses yet is "unmapped", not "lost"
        return "unmapped" if all((r.error or "").startswith("no register mapping") for r in rows.values()) else "lost"
    now = c.now()
    for r in rows.values():
        if r.quality == Quality.STALE:
            return "stale"
        p = dev.point(r.point_id)
        allowed = max(p.max_age_ms / 1000 if p else 300, 2 * c.registry.site.poll_interval_s + 60)
        if r.quality == Quality.GOOD and (now - r.source_ts).total_seconds() > allowed:
            return "stale"
    return "ok"


def _sum(c: Collector, prs: list[tuple[str, str]], agg: str = "sum") -> float | None:
    xs = [c.store.value(d, p) for d, p in prs]
    xs = [float(x) for x in xs if isinstance(x, (int, float, bool))]
    if not xs:
        return None
    return round(sum(xs), 1) if agg == "sum" else round(sum(xs) / len(xs), 2)


def summary(c: Collector) -> dict:
    reg, st = c.registry, c.store
    specs = series_specs(reg)
    live = {k: _sum(c, prs, agg) for k, (agg, prs) in specs.items() if "/" not in k}

    # genset count: per-genset devices, plus gN_running bits on a site master
    total = running = 0
    for g in reg.by_kind("genset"):
        bits = [p.id for p in g.points if _G_RUNNING.match(p.id)]
        if bits:
            total += len(bits)
            running += sum(1 for b in bits if st.value(g.id, b))
        elif g.point("running"):
            total += 1
            running += 1 if st.value(g.id, "running") else 0

    today = _daily_cache.get(lambda: c.daily_totals())
    tot = today["totals"]
    load_kw, pv_kw = live.get("load_kw"), live.get("pv_kw")
    kpi = {
        "load_kw": load_kw, "pv_kw": pv_kw, "genset_kw": live.get("genset_kw"), "bess_kw": live.get("bess_kw"),
        "mains_kw": live.get("mains_kw"), "soc_pct": live.get("soc_pct"), "frequency_hz": live.get("frequency_hz"),
        "voltage_v": live.get("voltage_v"), "irradiance_wm2": live.get("irradiance_wm2"),
        "pv_share_now_pct": round(100 * pv_kw / load_kw, 1) if pv_kw is not None and load_kw else None,
        "gensets_running": running, "gensets_total": total,
        "pv_share_pct_today": tot["pv_share_pct"], "pv_kwh_today": tot["pv_kwh"], "load_kwh_today": tot["load_kwh"],
        "genset_kwh_today": tot["genset_kwh"], "fuel_l_today": tot["fuel_l"], "fuel_cost_today": tot["fuel_cost"],
        "fuel_saved_l_today": tot["fuel_saved_vs_no_solar_l"], "fuel_metered": tot.get("fuel_metered", True),
        "fuel_price_per_l": reg.site.fuel_price_per_l,
        "pv_curtailed_kwh_today": tot["pv_curtailed_kwh"],
        "grid_kwh_today": tot["grid_kwh"], "grid_hours_today": tot["grid_hours"], "grid_share_pct_today": tot["grid_share_pct"],
        "grid_available": (live.get("grid_available") or 0) >= 0.5 if live.get("grid_available") is not None else None,
    }
    devices = []
    for d in reg.devices.values():
        rows = st.latest(d.id)
        devices.append({
            "id": d.id, "kind": d.kind, "vendor": d.vendor, "model": d.model, "adapter": d.adapter,
            "profile_status": d.profile_status, "comms": device_comms(c, d.id),
            "points": {pid: {"v": r.value, "q": r.quality.value, "unit": r.unit, "ts": r.source_ts.isoformat(),
                             "err": r.error} for pid, r in rows.items()},
        })
    now = c.now()
    return {
        "site": reg.site.name, "site_id": reg.site.site_id, "timezone": reg.site.timezone,
        "mode": "read-only", "simulated": c.simulated, "time": now.isoformat(),
        "poll_interval_s": c.registry.site.poll_interval_s,
        "collector": {"polls": c.polls, "last_poll": c.last_poll_at.isoformat() if c.last_poll_at else None,
                      "next_poll": (c.last_poll_at + timedelta(seconds=c.registry.site.poll_interval_s)).isoformat() if c.last_poll_at else None,
                      "uptime_s": int((datetime.now(timezone.utc) - c.started_at).total_seconds()),
                      "rows": st.count(), "first_sample": st.first_ts(), "config": os.path.basename(c.config_path),
                      "database": c.db_path},
        "kpi": kpi,
        "active_faults": [f"{i['code']}:{i['device']}" for i in c.active_issues()],
        "devices": devices,
    }


# --- flat point list -----------------------------------------------------------------------------

def tags(c: Collector) -> list[dict]:
    out = []
    now = c.now()
    for d in c.registry.devices.values():
        rows = c.store.latest(d.id)
        for p in d.points:
            r = rows.get(p.id)
            out.append({
                "device": d.id, "kind": d.kind, "point": p.id, "unit": p.unit, "type": p.type,
                "description": p.description,
                "value": None if r is None else r.value,
                "quality": "bad" if r is None else r.quality.value,
                "ts": None if r is None else r.source_ts.isoformat(),
                "age_s": None if r is None else int((now - r.source_ts).total_seconds()),
                "error": None if r is None else r.error,
                "address": p.raw.get("address"), "register_kind": p.raw.get("kind"),
            })
    return out


# --- the Turkey tag catalogue ----------------------------------------------------------------------

def load_tag_catalog(path: str | None = None) -> list[dict]:
    path = path or os.path.join(PROFILES_DIR, "tag_catalog.yaml")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return list(doc.get("tags", []))


def catalog_values(c: Collector, catalog: list[dict]) -> list[dict]:
    out = []
    for t in catalog:
        did, pid = t.get("device"), t.get("point")
        r = c.store.latest(did).get(pid) if did in c.registry.devices else None
        out.append({
            "name": t["name"], "device": did, "point": pid, "type": t.get("type", "float"), "unit": t.get("unit", ""),
            "value": None if r is None else r.value,
            "quality": "bad" if r is None else r.quality.value,
            "ts": None if r is None else r.source_ts.isoformat(),
            "mapped": did in c.registry.devices and bool(c.registry.devices[did].point(pid)),
        })
    return out


def parse_range(frm: str | None, to: str | None, hours: float | None, now: datetime) -> tuple[datetime, datetime]:
    def parse(s: str) -> datetime:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    until = parse(to) if to else now
    since = parse(frm) if frm else until - timedelta(hours=hours or 24)
    if since >= until:
        since = until - timedelta(hours=1)
    return since, until
