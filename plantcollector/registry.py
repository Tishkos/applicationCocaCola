"""
Device registry: which devices exist, how to reach them, and what points they expose.
Read-only by design — there is no notion of a command anywhere in this package.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

import yaml
try:
    from pydantic import BaseModel, Field
except ImportError:                                     # the light package for slow sites: no pydantic
    from ._mini_model import BaseModel, Field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES_DIR = os.path.join(ROOT, "profiles")


class Quality(str, Enum):
    GOOD = "good"
    STALE = "stale"
    BAD = "bad"


class PointDefinition(BaseModel):
    id: str
    type: Literal["number", "boolean", "enum", "string"] = "number"
    unit: str = ""
    max_age_ms: int = 300_000
    raw: dict[str, Any] = Field(default_factory=dict)     # adapter-specific mapping
    description: str = ""


class Device(BaseModel):
    id: str
    name: str = ""                                        # shown instead of the id when set
    # "meter" is the site load meter (its load_kw is the site load); "analyzer" is a power meter on one feeder,
    # shown and stored but never counted into the site's totals
    kind: Literal["genset", "pv", "grid", "bess", "meter", "analyzer", "controller", "other"] = "other"
    vendor: str = ""
    model: str = ""
    firmware: str = ""
    adapter: str
    connection: dict[str, Any] = Field(default_factory=dict)
    points: list[PointDefinition] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)  # simulator only (rated_kw, ...)
    profile_status: str = "draft"
    source_document: str = ""

    def point(self, pid: str) -> PointDefinition | None:
        return next((p for p in self.points if p.id == pid), None)


class SiteConfig(BaseModel):
    site_id: str
    name: str = ""
    timezone: str = "Asia/Baghdad"
    fuel_price_per_l: float = 1.0
    poll_interval_s: float = 30.0


@dataclass
class Registry:
    site: SiteConfig
    devices: dict[str, Device] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def by_kind(self, kind: str) -> list[Device]:
        return [d for d in self.devices.values() if d.kind == kind]


def _yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:        # the profiles are UTF-8 ("°C"); Windows would read them as cp1252
        return yaml.safe_load(f) or {}


ADDED_DEVICES = os.path.join(ROOT, "config", "added-devices.yaml")


def added_devices(cfg: dict, path: str = ADDED_DEVICES, config_name: str = "site.plant.yaml") -> tuple[list[dict], dict]:
    """The devices of config/added-devices.yaml that this site's settings do not have yet (an update keeps the
    plant's settings file, so a device added later would never reach an installed PC), and the blocks to use when
    the settings have none (e.g. `meters`). Only for the real plant's settings of the site named there: nothing for a
    demo config (simulated devices) or another site."""
    try:
        add = _yaml(path)
    except (OSError, yaml.YAMLError):
        return [], {}
    if not add or str(add.get("site_id", "")) != str((cfg.get("site") or {}).get("site_id", "")):
        return [], {}
    if config_name not in [str(x) for x in (add.get("configs") or ["site.plant.yaml"])]:
        return [], {}                                    # e.g. site.eth1.yaml: a PC on another network
    have = cfg.get("devices") or []
    if any(str(e.get("adapter")) == "simulator" for e in have):
        return [], {}                                    # the demo: it has the same site id, but no real devices
    ids = {str(e.get("id")) for e in have}
    sk = cfg.get("skip_added_devices") or []
    skip = {s.strip() for s in sk.split(",")} if isinstance(sk, str) else {str(x) for x in sk}
    ends = {(str((e.get("connection") or {}).get("host")), int((e.get("connection") or {}).get("unit_id", 1)))
            for e in have if (e.get("connection") or {}).get("host")}
    out = []
    for e in add.get("devices") or []:
        c = e.get("connection") or {}
        if str(e.get("id")) in ids or str(e.get("id")) in skip:
            continue
        if c.get("host") and (str(c.get("host")), int(c.get("unit_id", 1))) in ends:
            continue                                     # the settings already read this device under another id
        out.append(e)
    blocks = {k: v for k, v in add.items() if k not in ("site_id", "devices", "configs") and k not in cfg}
    return out, blocks


def load_registry(site_config_path: str) -> Registry:
    cfg = _yaml(site_config_path)
    site = SiteConfig(**{k: v for k, v in cfg["site"].items() if k in SiteConfig.model_fields})
    from . import sitetime
    sitetime.set_zone(site.timezone)                     # the plant's time for everything a person reads
    more, blocks = added_devices(cfg, config_name=os.path.basename(site_config_path))
    if more or blocks:
        cfg = {**cfg, **blocks, "devices": list(cfg.get("devices") or []) + more}
    reg = Registry(site=site, extra={k: v for k, v in cfg.items() if k not in ("site", "devices")})
    reg.extra["added_devices"] = [str(e.get("id")) for e in more]
    for entry in cfg.get("devices", []):
        prof_path = entry["profile"]
        if not os.path.isabs(prof_path):
            prof_path = os.path.join(PROFILES_DIR, prof_path)
        prof = _yaml(prof_path)
        prof.pop("commands", None)                       # profiles may document commands; we never use them
        merged = {**prof, **{k: v for k, v in entry.items() if k != "profile"}}
        if merged.get("kind") not in ("genset", "pv", "grid", "bess", "meter", "analyzer", "controller"):
            merged["kind"] = "other"
        lim = merged.get("limits") or {}
        merged["limits"] = {k: v for k, v in lim.items() if v is not None}
        reg.devices[entry["id"]] = Device(**merged)
    return reg
