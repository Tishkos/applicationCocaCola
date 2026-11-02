"""
ComAp InteliSCADA 3.x reader (for the licensed SCADA machine, if it runs 24/7).

Flow proven on site: POST /api/auth/login {} -> anonymous token; POST /api/auth/sites/{id}/unlock
{password, rememberPassword} -> site token (idle timeout -> re-unlock on 401);
GET /api/trends/{site} -> trend sources; POST /api/trends/query -> samples.

point.raw: {trend: "<trend id or name>"}
connection: {url, password, site_id (optional), lookback_min, stale_after_s}
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from ..registry import Device, Quality
from ..store import Reading
from . import Adapter, reading

_CLIENTS: dict[str, "Client"] = {}
_LOCK = threading.Lock()


def _ts(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class Client:
    def __init__(self, base: str, password: str, site_id: str | None):
        self.base, self.password, self.site_id = base.rstrip("/"), password, site_id
        self.s = requests.Session()
        self.anon: str | None = None
        self.token: str | None = None
        self._trends: list[dict] = []
        self._trends_at = 0.0
        self.last_query = 0.0
        self.min_interval = 10.0

    def _h(self, t): return {"Authorization": f"Bearer {t}"} if t else {}

    def login(self):
        r = self.s.post(f"{self.base}/api/auth/login", json={}, timeout=15); r.raise_for_status()
        self.anon = r.json()["accessToken"]

    def unlock(self):
        if not self.anon:
            self.login()
        if not self.site_id:
            sites = self.s.get(f"{self.base}/api/sites", headers=self._h(self.anon), timeout=15).json()
            sites = [sites] if isinstance(sites, dict) else sites
            if len(sites) != 1:
                raise RuntimeError("set connection.site_id (several sites on this InteliSCADA)")
            self.site_id = sites[0]["id"]
        body = {"password": self.password, "rememberPassword": False}
        r = self.s.post(f"{self.base}/api/auth/sites/{self.site_id}/unlock", headers=self._h(self.anon), json=body, timeout=15)
        if r.status_code == 401:
            self.login()
            r = self.s.post(f"{self.base}/api/auth/sites/{self.site_id}/unlock", headers=self._h(self.anon), json=body, timeout=15)
        r.raise_for_status()
        self.token = r.json()["accessToken"]

    def req(self, method: str, path: str, **kw) -> Any:
        if not self.token:
            self.unlock()
        for attempt in (1, 2):
            r = self.s.request(method, f"{self.base}{path}", headers=self._h(self.token), timeout=20, **kw)
            if r.status_code == 401 and attempt == 1:
                self.unlock(); continue
            if r.status_code == 429:
                time.sleep(5); continue
            r.raise_for_status()
            return r.json() if r.content else None
        raise RuntimeError(f"{method} {path}: unauthorized")

    def trends(self) -> list[dict]:
        if not self.token:
            self.unlock()
        if time.time() - self._trends_at > 600:
            self._trends = self.req("GET", f"/api/trends/{self.site_id}") or []
            self._trends_at = time.time()
        return self._trends

    def trend_id(self, key: str) -> str | None:
        for t in self.trends():
            if t.get("id") == key or str(t.get("name", "")).lower() == key.lower():
                return t.get("id")
        return None

    def query(self, ids: list[str], since: datetime) -> list[dict]:
        wait = self.min_interval - (time.time() - self.last_query)
        if wait > 0:
            time.sleep(wait)
        body = {"siteDefinitionId": self.site_id, "from": since.isoformat(), "to": datetime.now(timezone.utc).isoformat(),
                "requestedTrends": ids, "maxDataPoints": 500}
        out = self.req("POST", "/api/trends/query", json=body) or []
        self.last_query = time.time()
        return out


def client(base: str, password: str, site_id: str | None) -> Client:
    with _LOCK:
        if base not in _CLIENTS:
            _CLIENTS[base] = Client(base, password, site_id)
        return _CLIENTS[base]


class InteliScadaAdapter(Adapter):
    name = "inteliscada"

    def __init__(self, device: Device):
        super().__init__(device)
        c = device.connection
        self.c = client(c.get("url", os.environ.get("ISCADA_URL", "http://localhost:8801")),
                        c.get("password", os.environ.get("ISCADA_PASSWORD", "")), c.get("site_id") or None)
        self.lookback_min = float(c.get("lookback_min", 10))
        self.stale_after_s = float(c.get("stale_after_s", 300))

    def connect(self) -> bool:
        try:
            self.c.unlock(); return True
        except Exception:
            return False

    def read(self) -> dict[str, Reading]:
        now = datetime.now(timezone.utc)
        keyed, out = {}, {}
        try:
            for p in self.device.points:
                tid = self.c.trend_id(str(p.raw.get("trend", ""))) if p.raw.get("trend") else None
                if tid:
                    keyed[tid] = p
                else:
                    out[p.id] = reading(p, None, Quality.BAD, "no trend mapping", now)
            data = self.c.query(list(keyed), now - timedelta(minutes=self.lookback_min)) if keyed else []
        except Exception as exc:
            return {p.id: reading(p, None, Quality.BAD, f"inteliscada: {exc}", now) for p in self.device.points}
        latest = {}
        for t in data:
            samples = t.get("samples") or []
            if samples:
                last = samples[-1]
                ts = last.get("timestamp") or last.get("time") or last.get("t")
                val = last.get("value") if "value" in last else last.get("v")
                if ts is not None:
                    latest[t["id"]] = (_ts(ts), val)
        for tid, p in keyed.items():
            if tid not in latest:
                out[p.id] = reading(p, None, Quality.BAD, "no samples in window", now)
                continue
            ts, val = latest[tid]
            if p.type == "boolean" and val is not None:
                val = bool(val)
            q = Quality.STALE if (now - ts).total_seconds() > self.stale_after_s else Quality.GOOD
            out[p.id] = reading(p, val, q, ts=ts)
        return out
