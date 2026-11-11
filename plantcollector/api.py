"""
HTTP API + dashboard. Nothing here can write to a device; the one POST triggers a read.

  /                               dashboard
  /api/v1/health                  collector status
  /api/v1/local/summary           live values, KPIs, device comms, active faults
  POST /api/v1/local/poll         read every device now (a read, not a write)
  GET/POST /api/v1/local/settings site read rate, the master's live reading, each device's rate (data/settings.json)
  /api/v1/tags/registers          the Modbus register map published for the Turkey platform (solar + analyzer block)
  /api/v1/tags/analyzers[.csv|.xlsx]  the analyzer block's tags with live values, and as a file
  /api/v1/local/series            time series (hours= | from= to=, step=, keys=)
  /api/v1/local/alarms            event log (hours=)
  /api/v1/local/tags              every point with its latest value
  /api/v1/local/daily             daily totals (date=YYYY-MM-DD)
  /api/v1/local/export.csv|xlsx   wide table (from=, to=, step=, devices=)
  /api/v1/local/consumption[.csv|.xlsx]  plant load consumption per analyzer (from=, to=)
  /api/v1/local/settings/backup, POST /settings/restore  the dashboard's own setup as a file
  /api/v1/local/report.xlsx       daily report workbook (date=)
  /api/v1/tags                    the DT Turkey tag list with live values
  /api/v1/tags/{name}             one tag
  /api/v1/tags.csv|xlsx           tag list as a file
"""

from __future__ import annotations

import json
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
        device_every: {row key: seconds} (0 = with every site read; the keys of GET .reading.groups),
        register_names: {analyzer id: {point id: name}} (the plant's own name for a register; "" = none)."""
        known = ("poll_interval_s", "live_every_s", "device_every", "register_names")
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
            if "register_names" in body:
                if not isinstance(body["register_names"], dict) or not all(isinstance(v, dict) for v in body["register_names"].values()):
                    raise ValueError("register_names must be {device id: {point id: name}}")
                for did, names in body["register_names"].items():
                    for pid, nm in names.items():
                        c.set_register_name(did, pid, nm)
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

    @app.post("/api/v1/local/analyzers")
    def add_analyzer(body: dict):
        """Add a power meter of the same type as the plant's analyzers (Schneider PM800): {host, port (502),
        unit_id (its Modbus address), name (optional)}. Read-only, every 15 s, on the gateway's kept connection."""
        try:
            return c.add_analyzer(body.get("host"), body.get("port", 502), body.get("unit_id"), body.get("name", ""))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc))

    @app.delete("/api/v1/local/analyzers/{device_id}")
    def remove_analyzer(device_id: str):
        """Stop reading an analyzer added from the dashboard (its readings stay in the database)."""
        try:
            c.remove_analyzer(device_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"removed": device_id}

    @app.get("/api/v1/local/registers")
    def registers():
        """Every register read from each analyzer: address as sent and as the meter's manual counts it, kind, unit,
        description and the plant's own name (set with POST /api/v1/local/settings register_names)."""
        return {d.id: c.register_list(d.id) for d in c.registry.devices.values() if d.kind in c.NAMED_KINDS and d.id not in c.removed}

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

    @app.post("/api/v1/local/update/download")
    def update_download(request: Request):
        """Download and check the newer release now; it is installed only with "Install". Only from this PC itself."""
        _from_this_pc(request)
        try:
            return updates.request_check(c.data_dir, download=True)
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
        """Read every device right now: the site read, the live reader and the meter reader (it reads, it does
        not write)."""
        t0 = time.time()
        cycles = c.read_everything_now()
        devs = {d: views.device_comms(c, d) for d in c.registry.devices}
        return {"ok": True, "took_s": round(time.time() - t0, 2), "time": c.last_poll_at.isoformat(),
                "devices": devs, "answering": sum(1 for v in devs.values() if v == "ok"), "total": len(devs),
                "readers": cycles}

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
                      step: int = Query(300, ge=10), hours: float | None = Query(None, gt=0),
                      devices: str | None = Query(None, description="device ids, comma-separated; empty = all")):
        """Every reading of the chosen devices (all when `devices` is empty) as one wide table, averaged per step.
        Column headers carry the plant's own register names where they were given on the Analyzers tab."""
        if fmt not in ("csv", "xlsx"):
            raise HTTPException(404)
        since, until = views.parse_range(frm, to, hours, c.now())
        if until - since > timedelta(days=MAX_EXPORT_DAYS):
            raise HTTPException(400, f"range longer than {MAX_EXPORT_DAYS} days; export in pieces")
        wanted = [x for x in (devices or "").split(",") if x.strip()] or None
        try:
            chosen = export.export_devices(c, wanted)
            headers, rows = export.wide_table(c, since, until, step, wanted)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        part = "" if wanted is None else ("_" + (chosen[0].id if len(chosen) == 1 else f"{len(chosen)}devices"))
        name = f"{c.registry.site.site_id}{part}_{since.strftime('%Y%m%d-%H%M')}_{until.strftime('%Y%m%d-%H%M')}_{step}s.{fmt}"
        data = export.to_csv(headers, rows) if fmt == "csv" else export.to_xlsx(c, headers, rows, since, until, step, wanted)
        return _file(data, name, fmt)

    @app.get("/api/v1/local/consumption")
    def consumption(frm: str | None = Query(None, alias="from"), to: str | None = None, hours: float | None = Query(None, gt=0)):
        """Plant load consumption: each analyzer's energy over the window from its own counter (or from its kW
        when the counter is not read), the kW now, the share of the metered total, and the site load to compare."""
        since, until = views.parse_range(frm, to, hours, c.now())
        if until - since > timedelta(days=MAX_EXPORT_DAYS):
            raise HTTPException(400, f"range longer than {MAX_EXPORT_DAYS} days")
        return views.consumption(c, since, until)

    @app.get("/api/v1/local/consumption.{fmt}")
    def consumption_file(fmt: str, frm: str | None = Query(None, alias="from"), to: str | None = None, hours: float | None = Query(None, gt=0)):
        if fmt not in ("csv", "xlsx"):
            raise HTTPException(404)
        since, until = views.parse_range(frm, to, hours, c.now())
        if until - since > timedelta(days=MAX_EXPORT_DAYS):
            raise HTTPException(400, f"range longer than {MAX_EXPORT_DAYS} days")
        data = views.consumption(c, since, until)
        name = f"{c.registry.site.site_id}_consumption_{since.strftime('%Y%m%d-%H%M')}_{until.strftime('%Y%m%d-%H%M')}.{fmt}"
        return _file(export.consumption_csv(data) if fmt == "csv" else export.consumption_xlsx(c, data), name, fmt)

    @app.get("/api/v1/local/settings/backup")
    def settings_backup():
        """The dashboard's own settings as a file to keep: the analyzers added here, the register names, the reading
        rates. Restored with POST /api/v1/local/settings/restore, on this PC or another."""
        data = c.settings_backup()
        name = f"cci-plant-monitoring-settings_{c.registry.site.site_id}_{c.now().strftime('%Y-%m-%d')}.json"
        return Response(content=json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/v1/local/settings/restore")
    def settings_restore(body: dict):
        """Apply a settings backup: analyzers not present yet are added, register names and reading rates are set.
        Returns what was done; an analyzer that cannot be added (duplicate address, limit) is reported, not fatal."""
        try:
            return c.settings_restore(body)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/v1/local/report.xlsx")
    def report(date: str | None = None):
        day = datetime.fromisoformat(date).replace(tzinfo=timezone.utc) if date else c.now()
        return _file(export.daily_report_xlsx(c, day), f"{c.registry.site.site_id}_daily_{day.strftime('%Y-%m-%d')}.xlsx", "xlsx")

    # --- the tag list DT Turkey asked for --------------------------------------------------------

    @app.get("/api/v1/tags/registers")
    def tag_registers():
        """The Modbus register map published for the Turkey platform (same whether the server runs or not): the
        solar tags, and the analyzer block (a fixed slot per analyzer number; the analyzers read now)."""
        from . import publish as pb
        return {"map_version": pb.MAP_VERSION, "unit_id": ((c.registry.extra.get("publish") or {}).get("modbus_tcp") or {}).get("unit_id", 1),
                "encoding": "float32 ABCD (2 registers) / boolean uint16; the analyzers' energy counter uint32 (2 registers, high word first)",
                "tags": pb.build_map(catalog),
                "analyzers": {"values_start": pb.AN_VALUES_START, "slot_registers": pb.AN_SLOT_REGISTERS, "quality_start": pb.AN_QUALITY_START,
                              "slot_quality_registers": pb.AN_SLOT_QUALITY, "slots": pb.AN_SLOTS,
                              "slot_formula": "value address = 2000 + 32 x (n - 1) + offset; quality address = 4000 + 16 x (n - 1) + index; n = the analyzer's number",
                              "points": [{"index": j, "tag": t, "point": pid, "type": typ, "unit": u, "offset": o} for j, (t, pid, typ, u, o) in enumerate(pb.ANALYZER_POINTS)],
                              "tags": pb.analyzer_map(c)}}

    @app.get("/api/v1/tags/analyzers")
    def analyzer_tags():
        """The analyzer block's tags with their live values: what the Turkey platform reads from registers 2000+."""
        return {"map_version": __import__("plantcollector.publish", fromlist=["MAP_VERSION"]).MAP_VERSION, "time": c.now().isoformat(),
                "tags": views.analyzer_tag_values(c)}

    @app.get("/api/v1/tags/analyzers.{fmt}")
    def analyzer_tag_file(fmt: str):
        """The analyzer tag list as a file for the Turkey platform: tag, analyzer, its names, addresses, type, unit."""
        rows = views.analyzer_tag_values(c)
        if fmt == "csv":
            return _file(export.analyzer_tags_csv(rows), "analyzer-tags.csv", "csv")
        if fmt == "xlsx":
            return _file(export.analyzer_tags_xlsx(c, rows), "analyzer-tags.xlsx", "xlsx")
        raise HTTPException(404)

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
