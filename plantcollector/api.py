"""
HTTP API + dashboard. Nothing here can write to a device; the one POST triggers a read.

  /                               dashboard
  /api/v1/health                  collector status
  /api/v1/local/summary           live values, KPIs, device comms, active faults
  POST /api/v1/local/poll         read every device now (a read, not a write)
  GET/POST /api/v1/local/settings site read rate, the master's live reading, each device's rate (data/settings.json)
  /api/v1/tags/registers          the Modbus register map published for the Turkey platform
  /api/v1/local/series            time series (hours= | from= to=, step=, keys=)
  /api/v1/local/alarms            event log (hours=)
  /api/v1/local/tags              every point with its latest value
  /api/v1/local/daily             daily totals (date=YYYY-MM-DD)
  /api/v1/local/export.csv|xlsx   wide table (from=, to=, step=)
  /api/v1/local/report.xlsx       daily report workbook (date=)
  /api/v1/tags                    the DT Turkey tag list with live values
  /api/v1/tags/{name}             one tag
  /api/v1/tags.csv|xlsx           tag list as a file
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import export, updates, views
from . import __version__
from .core import POLL_MIN_S, POLL_OPTIONS_S, Collector

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MAX_EXPORT_DAYS = 62


def _file(data: bytes, name: str, kind: str) -> Response:
    media = {"csv": "text/csv; charset=utf-8",
             "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[kind]
    return Response(content=data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


def create_app(c: Collector) -> FastAPI:
    app = FastAPI(title="Plant data collector", version="1.0", docs_url="/api/docs", redoc_url=None)
    app.state.collector = c

    @app.exception_handler(Exception)
    async def _unhandled(request, exc):
        logging.getLogger("collector").exception("request failed: %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}",
                                                      "path": request.url.path, "hint": "details in data/collector.log"})
    catalog = views.load_tag_catalog()

    @app.middleware("http")
    async def _fresh_pages(request, call_next):
        # the page and its scripts are asked again every time (a quick "not modified" when unchanged): without this the
        # window keeps showing the previous version's page for hours after an update, with only the version number new
        resp = await call_next(request)
        path = request.url.path
        if path in ("/", "/report") or path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.mount("/static", StaticFiles(directory=os.path.join(WEB_DIR, "static")), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(os.path.join(WEB_DIR, "index.html"))

    @app.get("/report", include_in_schema=False)
    def day_report_page():
        """The printable one-page day report (web/report.html); it reads the API below."""
        return FileResponse(os.path.join(WEB_DIR, "report.html"))

    @app.get("/api/v1/health")
    def health():
        return {"ok": True, "simulated": c.simulated, "polls": c.polls,
                "last_poll": c.last_poll_at.isoformat() if c.last_poll_at else None,
                "devices": {d: views.device_comms(c, d) for d in c.registry.devices}}

    @app.get("/api/v1/local/live")
    def live(minutes: float | None = Query(None, gt=0, le=60)):
        """What feeds the plant now and the last minutes of it (15, or 60 when the master is read every 30 s or
        slower), from the live reader."""
        return views.live_view(c, minutes)

    @app.get("/api/v1/local/summary")
    def summary():
        return views.summary(c)

    @app.get("/api/v1/local/settings")
    def get_settings():
        return _settings()

    @app.post("/api/v1/local/settings")
    def set_settings(body: dict):
        """Any of: poll_interval_s (the site read), live_every_s (the master's live reading, 0 = off),
        device_every: {row key: seconds} (0 = with every site read; the keys of GET .reading.groups)."""
        known = ("poll_interval_s", "live_every_s", "device_every")
        if not any(k in body for k in known):
            raise HTTPException(400, f"nothing to change: send one of {', '.join(known)}")
        try:
            if "poll_interval_s" in body:
                c.set_poll_interval(int(body.get("poll_interval_s") or 0))
            if "live_every_s" in body:
                c.set_live_every(int(body.get("live_every_s")))
            if "device_every" in body:
                if not isinstance(body["device_every"], dict):
                    raise ValueError("device_every must be {row key: seconds}")
                rows = {g["key"]: g["devices"] for g in views.reading_settings(c)["groups"]}
                for key, sec in body["device_every"].items():
                    if key not in rows:
                        raise ValueError(f"unknown row {key}")
                    c.set_device_every(rows[key], int(sec))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        return _settings()

    def _settings():
        pub = getattr(c, "publisher", None)
        pcfg = (c.registry.extra.get("publish") or {}).get("modbus_tcp") or {}
        return {"poll_interval_s": int(c.poll_interval_s), "min_s": POLL_MIN_S,
                "publish": pub.status() if pub else {"enabled": bool(pcfg.get("enabled")), "running": False,
                                                     "bind": pcfg.get("bind", "0.0.0.0"), "port": pcfg.get("port", 502), "unit_id": pcfg.get("unit_id", 1),
                                                     "error": getattr(c, "publisher_error", None)},
                "options": [{"value": v, "label": f"{v // 3600} h" if v >= 3600 and v % 3600 == 0 else f"{v // 60} min"} for v in POLL_OPTIONS_S],
                "source": c.settings_source, "simulated": c.simulated, "config": os.path.basename(c.config_path),
                "settings_file": c.settings_path, "database": c.db_path, "version": updates.installed_version(),
                "update": updates.status(c.data_dir), "reading": views.reading_settings(c)}

    @app.get("/api/v1/local/version")
    def version():
        """The installed version and what the auto-updater last found (it checks GitHub Releases)."""
        return updates.status(c.data_dir)

    def _from_this_pc(request: Request):
        """The update buttons work only from the dashboard on this PC itself: the request must come from this PC,
        to this PC's own address, from this dashboard's page (its own header, which another web site cannot send
        without the browser asking first, and an Origin of this dashboard when the browser sends one)."""
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise HTTPException(403, "only from the PC the collector runs on")
        local = ("localhost", "127.0.0.1", "[::1]", "testserver")
        if request.headers.get("x-cci-request") != "update":
            raise HTTPException(403, "only from the dashboard's own Updates panel")
        hostname = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
        if hostname not in local:
            raise HTTPException(403, "only through this PC's own address")
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1].rsplit(":", 1)[0].lower() not in local:
            raise HTTPException(403, "not from another web site")

    @app.post("/api/v1/local/update/check")
    def update_check(request: Request):
        """Let the updater look at GitHub Releases now. Only from this PC itself: another PC on the plant
        network cannot."""
        _from_this_pc(request)
        try:
            return updates.request_check(c.data_dir)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/v1/local/update/install")
    def update_install(request: Request):
        """Install the newer release now (the collector stops for about a minute). Only from this PC itself."""
        _from_this_pc(request)
        try:
            return updates.request_check(c.data_dir, install=True)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/v1/local/poll")
    def poll_now():
        """Read every device right now (the only POST; it reads, it does not write)."""
        t0 = time.time()
        c.poll()
        devs = {d: views.device_comms(c, d) for d in c.registry.devices}
        return {"ok": True, "took_s": round(time.time() - t0, 2), "time": c.last_poll_at.isoformat(),
                "devices": devs, "answering": sum(1 for v in devs.values() if v == "ok"), "total": len(devs)}

    @app.get("/api/v1/local/series")
    def series(hours: float | None = Query(None, gt=0, le=24 * MAX_EXPORT_DAYS), frm: str | None = Query(None, alias="from"),
               to: str | None = None, step: int | None = Query(None, ge=10), keys: str | None = None):
        since, until = views.parse_range(frm, to, hours, c.now())
        if until - since > timedelta(days=MAX_EXPORT_DAYS):
            raise HTTPException(400, f"range longer than {MAX_EXPORT_DAYS} days")
        return views.series(c, since, until, step, keys.split(",") if keys else None)

    @app.get("/api/v1/local/alarms")
    def alarms(hours: float = Query(24, gt=0, le=24 * 366), limit: int = Query(300, ge=1, le=5000),
               frm: str | None = Query(None, alias="from"), to: str | None = None):
        if frm:                                   # a fixed window (the day report): from .. to
            since, until = views.parse_range(frm, to, None, c.now())
            ev = [e for e in c.store.events(since, 5000) if e["ts"] < until.isoformat()][:limit]
            return {"active": c.active_issues(), "events": ev}
        since = c.now() - timedelta(hours=hours)
        return {"active": c.active_issues(), "events": c.store.events(since, limit)}

    @app.get("/api/v1/local/tags")
    def tags():
        return views.tags(c)

    @app.get("/api/v1/local/daily")
    def daily(date: str | None = None):
        day = datetime.fromisoformat(date).replace(tzinfo=timezone.utc) if date else None
        return c.daily_totals(day)

    @app.get("/api/v1/local/export.{fmt}")
    def export_window(fmt: str, frm: str | None = Query(None, alias="from"), to: str | None = None,
                      step: int = Query(300, ge=10), hours: float | None = Query(None, gt=0)):
        if fmt not in ("csv", "xlsx"):
            raise HTTPException(404)
        since, until = views.parse_range(frm, to, hours, c.now())
        if until - since > timedelta(days=MAX_EXPORT_DAYS):
            raise HTTPException(400, f"range longer than {MAX_EXPORT_DAYS} days; export in pieces")
        headers, rows = export.wide_table(c, since, until, step)
        name = f"{c.registry.site.site_id}_{since.strftime('%Y%m%d-%H%M')}_{until.strftime('%Y%m%d-%H%M')}_{step}s.{fmt}"
        data = export.to_csv(headers, rows) if fmt == "csv" else export.to_xlsx(c, headers, rows, since, until, step)
        return _file(data, name, fmt)

    @app.get("/api/v1/local/report.xlsx")
    def report(date: str | None = None):
        day = datetime.fromisoformat(date).replace(tzinfo=timezone.utc) if date else c.now()
        return _file(export.daily_report_xlsx(c, day), f"{c.registry.site.site_id}_daily_{day.strftime('%Y-%m-%d')}.xlsx", "xlsx")

    # --- the tag list DT Turkey asked for --------------------------------------------------------

    @app.get("/api/v1/tags/registers")
    def tag_registers():
        """The Modbus register map published for the Turkey platform (same whether the server runs or not)."""
        from .publish import MAP_VERSION, build_map
        return {"map_version": MAP_VERSION, "unit_id": ((c.registry.extra.get("publish") or {}).get("modbus_tcp") or {}).get("unit_id", 1),
                "encoding": "float32 ABCD (2 registers) / boolean uint16", "tags": build_map(catalog)}

    @app.get("/api/v1/tags")
    def tag_values():
        return {"version": "1.2", "count": len(catalog), "time": c.now().isoformat(),
                "tags": views.catalog_values(c, catalog)}

    @app.get("/api/v1/tags.{fmt}")
    def tag_file(fmt: str):
        if fmt == "csv":
            return _file(export.tags_csv(c, catalog), "tags.csv", "csv")
        if fmt == "xlsx":
            return _file(export.tags_xlsx(c, catalog), "tags.xlsx", "xlsx")
        raise HTTPException(404)

    @app.get("/api/v1/tags/{name}")
    def one_tag(name: str):
        for t in views.catalog_values(c, catalog):
            if t["name"].lower() == name.lower():
                return t
        raise HTTPException(404, f"no tag named {name}")

    return app
