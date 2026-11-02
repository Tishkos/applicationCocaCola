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
    kind: Literal["genset", "pv", "grid", "bess", "meter", "controller", "other"] = "other"
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


def load_registry(site_config_path: str) -> Registry:
    cfg = _yaml(site_config_path)
    site = SiteConfig(**{k: v for k, v in cfg["site"].items() if k in SiteConfig.model_fields})
    from . import sitetime
    sitetime.set_zone(site.timezone)                     # the plant's time for everything a person reads
    reg = Registry(site=site, extra={k: v for k, v in cfg.items() if k not in ("site", "devices")})
    for entry in cfg.get("devices", []):
        prof_path = entry["profile"]
        if not os.path.isabs(prof_path):
            prof_path = os.path.join(PROFILES_DIR, prof_path)
        prof = _yaml(prof_path)
        prof.pop("commands", None)                       # profiles may document commands; we never use them
        merged = {**prof, **{k: v for k, v in entry.items() if k != "profile"}}
        if merged.get("kind") not in ("genset", "pv", "grid", "bess", "meter", "controller"):
            merged["kind"] = "other"
        lim = merged.get("limits") or {}
        merged["limits"] = {k: v for k, v in lim.items() if v is not None}
        reg.devices[entry["id"]] = Device(**merged)
    return reg
