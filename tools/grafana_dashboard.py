"""
Builds grafana/dashboards/cci-plant-monitoring.json - the "CCI Plant Monitoring" Grafana dashboard.

Every query reads the collector's own tables (readings, events) with the device and point ids of the
real plant (config/site.plant.yaml): neo-01, inv-01..inv-10, pv-array, main-01..main-03. The same
dashboard shows data\\telemetry.sqlite (the plant) and data\\grafana\\plant-demo.sqlite (the demo).

    python tools/grafana_dashboard.py
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "grafana", "dashboards", "cci-plant-monitoring.json")

DS = {"type": "frser-sqlite-datasource", "uid": "plant-sqlite"}
INVERTERS = [f"inv-{i:02d}" for i in range(1, 11)]
MAINS = ["main-01", "main-02", "main-03"]

# colours: one per energy source, used everywhere the source appears
C_PV, C_GEN, C_MAINS, C_LOAD, C_CCI = "#F2CC0C", "#FF9830", "#5794F2", "#D8D9DA", "#E02F44"
C_OK, C_BAD, C_WARN, C_IDLE = "#73BF69", "#F2495C", "#FF9830", "#464C54"
KW = "suffix: kW"          # plant people read kW; Grafana's own unit would round 1404 kW to "1 MW"

TIME = "CAST(strftime('%s', ts) AS INTEGER)"
MINUTE = "CAST(strftime('%s', strftime('%Y-%m-%dT%H:%M:00', ts)) AS INTEGER)"   # lines up devices polled seconds apart
RANGE = ("ts >= strftime('%Y-%m-%dT%H:%M:%S', $__from/1000, 'unixepoch') "
         "AND ts <= strftime('%Y-%m-%dT%H:%M:%S', $__to/1000, 'unixepoch')")


# ---------------------------------------------------------------- SQL
def q_series(device, points, *, bucket=False):
    """One time series per point of one device: {point: legend}."""
    t = MINUTE if bucket else TIME
    cols = ", ".join(f"MAX(CASE WHEN point_id='{p}' THEN value END) AS \"{n}\"" for p, n in points.items())
    ids = ", ".join(f"'{p}'" for p in points)
    return (f"SELECT {t} AS time, {cols} FROM readings WHERE device_id='{device}' AND point_id IN ({ids}) "
            f"AND quality='good' AND {RANGE} GROUP BY time ORDER BY time")


def q_devices(devices, point, *, names=None):
    """The same point of several devices, one series each."""
    names = names or {d: d for d in devices}
    cols = ", ".join(f"MAX(CASE WHEN device_id='{d}' THEN value END) AS \"{names[d]}\"" for d in devices)
    ids = ", ".join(f"'{d}'" for d in devices)
    return (f"SELECT {MINUTE} AS time, {cols} FROM readings WHERE point_id='{point}' AND device_id IN ({ids}) "
            f"AND quality='good' AND {RANGE} GROUP BY time ORDER BY time")


def q_last(device, point, alias="value", expr="value"):
    return (f"SELECT {expr} AS \"{alias}\" FROM readings WHERE device_id='{device}' AND point_id='{point}' "
            f"AND quality='good' ORDER BY ts DESC LIMIT 1")


def target(sql, ref="A", fmt="table"):
    return {"refId": ref, "datasource": DS, "queryType": fmt, "rawQueryText": sql, "queryText": sql,
            "timeColumns": ["time"]}


# ---------------------------------------------------------------- panels
_id = [0]


def pid():
    _id[0] += 1
    return _id[0]


def thresholds(*steps):
    """steps: (value, colour) with the first value None."""
    return {"mode": "absolute", "steps": [{"value": v, "color": c} for v, c in steps]}


def vmap(mapping):
    """{value: (text, colour)} -> Grafana value mappings."""
    return [{"type": "value", "options": {str(k): {"text": t, "color": c, "index": i}
                                          for i, (k, (t, c)) in enumerate(mapping.items())}}]


ON_OFF = vmap({0: ("OFF", C_IDLE), 1: ("ON", C_OK)})
OK_WHEN_1 = vmap({1: ("OK", C_OK), 0: ("NOT OK", C_BAD)})
OK_WHEN_0 = vmap({0: ("OK", C_OK), 1: ("ACTIVE", C_BAD)})
COMM = vmap({0: ("ONLINE", C_OK), 1: ("NO DATA", C_BAD)})


def panel(kind, title, x, y, w, h, targets, *, desc="", unit=None, decimals=None, color=None, mappings=None,
          th=None, options=None, custom=None, overrides=None, minv=None, maxv=None, transparent=False, extra=None):
    defaults = {"custom": custom or {}, "mappings": mappings or []}
    if unit:
        defaults["unit"] = unit
    if decimals is not None:
        defaults["decimals"] = decimals
    if color:
        defaults["color"] = color
    if th:
        defaults["thresholds"] = th
    if minv is not None:
        defaults["min"] = minv
    if maxv is not None:
        defaults["max"] = maxv
    p = {"id": pid(), "type": kind, "title": title, "description": desc, "datasource": DS,
         "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets,
         "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
         "options": options or {}, "transparent": transparent}
    if extra:
        p.update(extra)
    return p


def row(title, y):
    return {"id": pid(), "type": "row", "title": title, "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
            "panels": []}


def stat(title, x, y, w, h, targets, *, spark=False, color_mode="value", text_mode="auto", fixed=None, **kw):
    opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": color_mode, "graphMode": "area" if spark else "none", "textMode": text_mode,
            "justifyMode": "center", "orientation": "auto", "wideLayout": True, "showPercentChange": False}
    if fixed:
        kw["color"] = {"mode": "fixed", "fixedColor": fixed}
    elif "color" not in kw:
        kw["color"] = {"mode": "thresholds"}
    return panel("stat", title, x, y, w, h, targets, options=opts, **kw)


def series_style(name, colour, *, stack=None, fill=None, width=None, dash=None, bars=False):
    props = [{"id": "color", "value": {"mode": "fixed", "fixedColor": colour}}]
    if stack is not None:
        props.append({"id": "custom.stacking", "value": {"mode": "normal" if stack else "none", "group": "A"}})
    if fill is not None:
        props.append({"id": "custom.fillOpacity", "value": fill})
    if width is not None:
        props.append({"id": "custom.lineWidth", "value": width})
    if dash:
        props.append({"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 6]}})
    if bars:
        props.append({"id": "custom.drawStyle", "value": "bars"})
    return {"matcher": {"id": "byName", "options": name}, "properties": props}


def timeseries(title, x, y, w, h, targets, *, unit=None, fill=18, stack=False, overrides=None, legend="bottom",
               calcs=("lastNotNull", "max", "mean"), bars=False, interp="smooth", step=False, **kw):
    custom = {"drawStyle": "bars" if bars else "line", "lineInterpolation": "stepAfter" if step else interp,
              "lineWidth": 2, "fillOpacity": fill, "gradientMode": "opacity", "showPoints": "never",
              "spanNulls": 3600000, "stacking": {"mode": "normal" if stack else "none", "group": "A"},
              "axisBorderShow": False, "axisPlacement": "auto", "barAlignment": 0, "barWidthFactor": 0.75}
    opts = {"legend": {"showLegend": True, "displayMode": "table" if legend == "right" else "list",
                       "placement": legend, "calcs": list(calcs) if legend == "right" else []},
            "tooltip": {"mode": "multi", "sort": "desc"}}
    if "color" not in kw:
        kw["color"] = {"mode": "palette-classic"}
    return panel("timeseries", title, x, y, w, h, targets, unit=unit, custom=custom, options=opts,
                 overrides=overrides, **kw)


def state_timeline(title, x, y, w, h, targets, mappings, **kw):
    opts = {"showValue": "auto", "mergeValues": True, "alignValue": "center", "rowHeight": 0.82,
            "legend": {"showLegend": False}, "tooltip": {"mode": "single"}}
    return panel("state-timeline", title, x, y, w, h, targets, mappings=mappings, options=opts,
                 custom={"fillOpacity": 85, "lineWidth": 0}, color={"mode": "fixed", "fixedColor": C_IDLE}, **kw)


def gauge(title, x, y, w, h, targets, *, unit, minv, maxv, th, **kw):
    opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showThresholdLabels": False, "showThresholdMarkers": True, "minVizHeight": 75, "minVizWidth": 75,
            "sizing": "auto"}
    return panel("gauge", title, x, y, w, h, targets, unit=unit, minv=minv, maxv=maxv, th=th, options=opts,
                 color={"mode": "thresholds"}, **kw)


def bargauge(title, x, y, w, h, targets, *, unit, minv, maxv, th, orientation="horizontal", mode="lcd", **kw):
    opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": orientation, "displayMode": mode, "valueMode": "color", "namePlacement": "auto",
            "showUnfilled": True, "sizing": "auto", "minVizHeight": 16, "minVizWidth": 8, "maxVizHeight": 300}
    return panel("bargauge", title, x, y, w, h, targets, unit=unit, minv=minv, maxv=maxv, th=th, options=opts,
                 color={"mode": "continuous-GrYlRd"} if kw.pop("gradient", False) else {"mode": "thresholds"}, **kw)


# ---------------------------------------------------------------- the dashboard
def build():
    P = []
    y = 0

    # ---- header -------------------------------------------------------------------------------
    header = """
<div style="display:flex;align-items:center;gap:22px;height:100%;padding:0 6px;">
  <img src="/public/img/cci-logo.png" alt="" style="height:76px;width:76px;border-radius:18px;box-shadow:0 6px 22px rgba(224,47,68,.35);">
  <div style="flex:1;min-width:0;">
    <div style="font-size:30px;font-weight:700;letter-spacing:.3px;line-height:1.1;">CCI Plant Monitoring</div>
    <div style="font-size:14px;opacity:.72;margin-top:6px;">Erbil Coca-Cola hybrid plant &nbsp;·&nbsp; 3.52 MWp solar (SAU + 10 Sineng) &nbsp;·&nbsp; 5 gensets on the InteliNeo master &nbsp;·&nbsp; 3 mains incomers (InteliMains 210)</div>
  </div>
  <div style="text-align:right;font-size:12px;opacity:.6;line-height:1.6;">Read-only &nbsp;·&nbsp; Modbus TCP<br>Asia/Baghdad</div>
</div>"""
    P.append(panel("text", "", 0, y, 24, 4, [], transparent=True,
                   options={"mode": "html", "content": header, "code": {"language": "html", "showLineNumbers": False}}))
    y += 4

    # ---- plant now ------------------------------------------------------------------------------
    kpi = [
        ("Site load", "neo-01", "load_kw", C_LOAD, "Gensets + mains import + PV, from the InteliNeo master."),
        ("Solar PV", "neo-01", "pv_prod_kw", C_PV, "PV actual P aggregated by the SAU, as the master sees it."),
        ("Gensets", "neo-01", "power_kw", C_GEN, "All gensets together (InteliNeo 'Gensets P')."),
        ("Mains import", "neo-01", "mains_power_kw", C_MAINS, "Government grid import; negative = export."),
    ]
    for i, (title, d, p, col, desc) in enumerate(kpi):
        P.append(stat(title, i * 4, y, 4, 5, [target(q_series(d, {p: title}))], spark=True, unit=KW,
                      decimals=0, fixed=col, desc=desc))
    share = (f"SELECT l.t AS time, 100.0 * MAX(0, p.v) / l.v AS \"Solar share\" FROM "
             f"(SELECT {MINUTE} t, value v FROM readings WHERE device_id='neo-01' AND point_id='load_kw' AND quality='good' AND {RANGE}) l "
             f"JOIN (SELECT {MINUTE} t, value v FROM readings WHERE device_id='neo-01' AND point_id='pv_prod_kw' AND quality='good' AND {RANGE}) p "
             f"ON p.t = l.t WHERE l.v > 0 ORDER BY time")
    P.append(stat("Solar share of load", 16, y, 4, 5, [target(share)], spark=True, unit="percent", decimals=0,
                  minv=0, maxv=100, th=thresholds((None, C_IDLE), (15, "#FADE2A"), (40, C_OK)),
                  desc="Solar PV / site load, right now. The line shows it over the selected time range."))
    lost = (f"SELECT {TIME} AS time, MAX(CASE WHEN point_id='available_kw' THEN value END) - "
            f"MAX(CASE WHEN point_id='ac_power_kw' THEN value END) AS \"Solar held back\" FROM readings "
            f"WHERE device_id='pv-array' AND point_id IN ('available_kw','ac_power_kw') AND quality='good' AND {RANGE} "
            f"GROUP BY time HAVING COUNT(*) = 2 ORDER BY time")
    P.append(stat("Solar held back", 20, y, 4, 5, [target(lost)], spark=True, unit=KW, decimals=0,
                  th=thresholds((None, C_OK), (150, "#FADE2A"), (600, C_WARN)),
                  desc="What the sun allows now (SAU 'Maximum Active Power Available') minus what the array delivers: "
                       "curtailment by the master while on gensets, tripped inverters, SAU stalls."))
    y += 5

    flow = q_series("neo-01", {"pv_prod_kw": "Solar PV", "power_kw": "Gensets", "mains_power_kw": "Mains import",
                               "load_kw": "Site load"})
    P.append(timeseries("Power flow - where the load comes from", 0, y, 17, 10, [target(flow)], unit=KW,
                        fill=35, stack=True, legend="right", decimals=0,
                        overrides=[series_style("Solar PV", C_PV), series_style("Gensets", C_GEN),
                                   series_style("Mains import", C_MAINS),
                                   series_style("Site load", C_LOAD, stack=False, fill=0, width=2, dash=True)],
                        desc="Stacked sources (PV, gensets, mains) against the site load (dashed). "
                             "Markers: grid lost / back and SAU stalls."))
    mix = (f"SELECT ({q_last('neo-01', 'pv_prod_kw', 'v')}) AS \"Solar PV\", "
           f"({q_last('neo-01', 'power_kw', 'v')}) AS \"Gensets\", "
           f"MAX(0, ({q_last('neo-01', 'mains_power_kw', 'v')})) AS \"Mains import\"")
    P.append(panel("piechart", "Source mix now", 17, y, 7, 10, [target(mix)], unit=KW, decimals=0,
                   color={"mode": "palette-classic"},
                   options={"pieType": "donut", "displayLabels": ["percent"],
                            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                            "legend": {"showLegend": True, "displayMode": "table", "placement": "bottom",
                                       "values": ["value", "percent"]},
                            "tooltip": {"mode": "single"}},
                   overrides=[series_style("Solar PV", C_PV), series_style("Gensets", C_GEN),
                              series_style("Mains import", C_MAINS)]))
    y += 10

    plant_state = q_series("neo-01", {"mains_available": "Government grid", "running": "Gensets on bus",
                                      "pv_operable": "PV operable (SAU)", "system_ready": "System ready",
                                      "alarm_active": "Alarm / warning"})
    P.append(state_timeline("Plant state", 0, y, 24, 6, [target(plant_state)], ON_OFF,
                            overrides=[{"matcher": {"id": "byName", "options": "Alarm / warning"},
                                        "properties": [{"id": "mappings", "value": vmap({0: ("clear", C_IDLE), 1: ("ACTIVE", C_BAD)})}]},
                                       {"matcher": {"id": "byName", "options": "Government grid"},
                                        "properties": [{"id": "mappings", "value": vmap({0: ("OFF", C_BAD), 1: ("ON", C_MAINS)})}]},
                                       {"matcher": {"id": "byName", "options": "Gensets on bus"},
                                        "properties": [{"id": "mappings", "value": vmap({0: ("stopped", C_IDLE), 1: ("RUNNING", C_GEN)})}]}]))
    y += 6

    # ---- gensets --------------------------------------------------------------------------------
    P.append(row("Gensets  ·  5 × ComAp, through the InteliNeo master", y)); y += 1
    for g in range(1, 6):
        can = f"(CAST(value AS INTEGER) >> {g - 1}) & 1"
        P.append(stat(f"Gen {g}", (g - 1) * 4, y, 4, 5,
                      [target(q_last("neo-01", f"g{g}_running", "Breaker"), "A"),
                       target(q_last("neo-01", "can_present_mask", "On CAN bus", can), "B")],
                      color_mode="background", text_mode="value_and_name",
                      mappings=vmap({1: ("LOADED", C_OK), 0: ("STANDBY", C_IDLE)}),
                      overrides=[{"matcher": {"id": "byName", "options": "On CAN bus"},
                                  "properties": [{"id": "mappings", "value": vmap({1: ("present", "#37872D"), 0: ("MISSING", C_BAD)})}]}],
                      desc=f"Gen {g}: GCB closed (Neo 'Gen Loaded 16' bit {g - 1}) and whether its controller answers on the CAN bus (CAN16 bit {g - 1})."))
    running = (f"SELECT " + " + ".join(f"({q_last('neo-01', f'g{g}_running', 'v')})" for g in range(1, 6)) + " AS \"Running\"")
    P.append(stat("Gensets running", 20, y, 4, 5, [target(running)], unit="none", decimals=0, color_mode="background",
                  th=thresholds((None, C_IDLE), (1, C_GEN)), desc="Gensets with the breaker closed, of 5."))
    y += 5
    gens = q_series("neo-01", {"running_power_kw": "Gensets P", "running_nominal_kw": "Running nominal",
                               "actual_reserve_kw": "Spinning reserve"})
    P.append(timeseries("Gensets - power, capacity on line, reserve", 0, y, 12, 9, [target(gens)], unit=KW,
                        decimals=0, step=True,
                        overrides=[series_style("Gensets P", C_GEN, fill=30),
                                   series_style("Running nominal", "#B877D9", fill=0, dash=True),
                                   series_style("Spinning reserve", "#8AB8FF", fill=8)],
                        desc="Running nominal = the capacity of the gensets on the bus. The master starts one more when the reserve "
                             "falls under the start reserve and stops one above the stop reserve."))
    loading = (f"SELECT 100.0 * ({q_last('neo-01', 'running_power_kw', 'v')}) / "
               f"NULLIF(({q_last('neo-01', 'running_nominal_kw', 'v')}), 0) AS \"Loading\"")
    P.append(gauge("Genset loading", 12, y, 4, 9, [target(loading)], unit="percent", minv=0, maxv=100,
                   th=thresholds((None, C_BAD), (30, C_OK), (85, C_WARN), (95, C_BAD)), decimals=0,
                   desc="Gensets P / running nominal. Under ~30 % for long is wet-stacking territory; the master curtails PV to keep it up."))
    P.append(stat("Reserves", 16, y, 4, 9,
                  [target(q_last("neo-01", "actual_reserve_kw", "Actual"), "A"),
                   target(q_last("neo-01", "start_reserve_kw", "Start below"), "B"),
                   target(q_last("neo-01", "stop_reserve_kw", "Stop above"), "C")],
                  unit=KW, decimals=0, text_mode="value_and_name", fixed="#8AB8FF",
                  extra={"options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                                     "colorMode": "value", "graphMode": "none", "textMode": "value_and_name",
                                     "justifyMode": "auto", "orientation": "horizontal", "wideLayout": True}}))
    P.append(stat("Hours & starts (all gensets)", 20, y, 4, 9,
                  [target(q_last("neo-01", "running_hours", "Running hours"), "A"),
                   target(q_last("neo-01", "num_starts", "Starts"), "B")],
                  decimals=0, fixed="#FF9830", text_mode="value_and_name",
                  overrides=[{"matcher": {"id": "byName", "options": "Running hours"},
                              "properties": [{"id": "unit", "value": "suffix: h"}]}],
                  extra={"options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                                     "colorMode": "value", "graphMode": "none", "textMode": "value_and_name",
                                     "justifyMode": "auto", "orientation": "horizontal", "wideLayout": True}}))
    y += 9
    gtl = q_series("neo-01", {f"g{g}_running": f"Gen {g}" for g in range(1, 6)})
    P.append(state_timeline("Which genset ran when", 0, y, 24, 6, [target(gtl)],
                            vmap({0: ("", "transparent"), 1: ("running", C_GEN)})))
    y += 6

    # ---- InteliNeo master ------------------------------------------------------------------------
    P.append(row("InteliNeo master controller  ·  192.168.0.112", y)); y += 1
    P.append(stat("Mode", 0, y, 4, 4, [target(q_last("neo-01", "controller_mode"))], color_mode="background",
                  mappings=vmap({0: ("OFF", C_BAD), 1: ("MAN", C_WARN), 2: ("AUTO", C_OK), 3: ("TEST", C_WARN)})))
    P.append(stat("Breaker state", 4, y, 5, 4, [target(q_last("neo-01", "breaker_state"))], color_mode="background",
                  mappings=vmap({0: ("Init", C_IDLE), 1: ("Breakers off", C_BAD), 2: ("Island (gensets)", C_GEN),
                                 3: ("Mains operation", C_MAINS), 4: ("Parallel to mains", "#B877D9"),
                                 5: ("Reverse sync", C_WARN), 6: ("Synchronising", C_WARN), 7: ("Mains fault", C_BAD),
                                 8: ("Valid fault", C_BAD), 9: ("Mains return", C_WARN), 10: ("Multi island", C_GEN),
                                 11: ("Multi parallel", "#B877D9"), 12: ("Emergency manual", C_BAD), 13: ("Start-up sync", C_WARN)})))
    P.append(stat("Ethernet", 9, y, 3, 4, [target(q_last("neo-01", "eth_status"))], color_mode="background",
                  mappings=vmap({0: ("Unknown", C_IDLE), 1: ("Disconnected", C_BAD), 2: ("Connecting", C_WARN),
                                 3: ("Connected", C_OK), 4: ("Failure", C_BAD)})))
    P.append(gauge("Bus frequency", 12, y, 4, 4, [target(q_last("neo-01", "bus_frequency_hz"))], unit="hertz",
                   minv=49, maxv=51, decimals=2,
                   th=thresholds((None, C_BAD), (49.5, C_WARN), (49.8, C_OK), (50.2, C_WARN), (50.5, C_BAD))))
    P.append(gauge("Bus voltage L-N", 16, y, 4, 4, [target(q_last("neo-01", "bus_voltage_v"))], unit="volt",
                   minv=200, maxv=260, decimals=0,
                   th=thresholds((None, C_BAD), (215, C_WARN), (220, C_OK), (243, C_WARN), (250, C_BAD))))
    P.append(stat("Controller battery", 20, y, 4, 4, [target(q_last("neo-01", "battery_v"))], unit="volt", decimals=1,
                  th=thresholds((None, C_BAD), (23, C_WARN), (24.5, C_OK))))
    y += 4
    healthy = [("system_ready", "System ready"), ("bus_healthy", "Bus healthy"), ("mains_available", "Mains available"),
               ("pv_operable", "PV operable"), ("pvcb_status", "PV breaker"), ("mode_auto", "In AUTO")]
    alarms = [("alarm_active", "Alarm / warning"), ("common_shutdown", "Shutdown"), ("alarm_mains_fail", "Mains fail"),
              ("pv_logger_comm_fail", "PV logger comm fail"), ("peripheral_comm_fail", "Peripheral comm fail"),
              ("estop", "Emergency stop")]
    for title, x, items, maps in (("Healthy signals", 0, healthy, OK_WHEN_1), ("Alarm signals", 12, alarms, OK_WHEN_0)):
        P.append(stat(title, x, y, 12, 5,
                      [target(q_last("neo-01", p, n), chr(65 + i)) for i, (p, n) in enumerate(items)],
                      color_mode="background", text_mode="value_and_name", mappings=maps,
                      extra={"options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                                         "colorMode": "background", "graphMode": "none", "textMode": "value_and_name",
                                         "justifyMode": "center", "orientation": "vertical", "wideLayout": True}}))
    y += 5
    P.append(timeseries("Bus frequency", 0, y, 8, 7, [target(q_series("neo-01", {"bus_frequency_hz": "Frequency"}))],
                        unit="hertz", decimals=2, fill=10, overrides=[series_style("Frequency", "#73BF69")],
                        extra={"fieldConfig": {"defaults": {"unit": "hertz", "decimals": 2, "custom": {
                            "drawStyle": "line", "lineWidth": 1, "fillOpacity": 10, "showPoints": "never", "spanNulls": 3600000,
                            "thresholdsStyle": {"mode": "line+area"}},
                            "thresholds": thresholds((None, "rgba(242,73,92,0.12)"), (49.8, "transparent"), (50.2, "rgba(242,73,92,0.12)")),
                            "color": {"mode": "fixed", "fixedColor": "#73BF69"}}, "overrides": []}}))
    P.append(timeseries("Bus voltage (L-N)", 8, y, 8, 7,
                        [target(q_series("neo-01", {"bus_voltage_l1_v": "L1", "bus_voltage_l2_v": "L2", "bus_voltage_l3_v": "L3"}))],
                        unit="volt", decimals=0, fill=0))
    P.append(timeseries("Power factor", 16, y, 8, 7,
                        [target(q_series("neo-01", {"gensets_pf": "Gensets", "mains_pf": "Mains", "pv_pf": "PV"}))],
                        unit="none", decimals=2, fill=0, minv=0, maxv=1,
                        overrides=[series_style("Gensets", C_GEN), series_style("Mains", C_MAINS), series_style("PV", C_PV)]))
    y += 7

    # ---- solar -----------------------------------------------------------------------------------
    P.append(row("Solar PV  ·  Sineng SAU-100 + 10 × SP-320K-H  ·  192.168.0.128", y)); y += 1
    P.append(stat("SAU link", 0, y, 4, 4, [target(q_last("pv-array", "comm_error"))], color_mode="background",
                  mappings=COMM, desc="Whether the SAU answered the last poll (the collector sets comm_error)."))
    P.append(stat("Inverters working", 4, y, 4, 4,
                  [target(q_last("pv-array", "inverters_working", "Working"), "A"),
                   target(q_last("pv-array", "inverters_faulty", "Faulty"), "B")],
                  text_mode="value_and_name", decimals=0,
                  overrides=[{"matcher": {"id": "byName", "options": "Faulty"},
                              "properties": [{"id": "thresholds", "value": thresholds((None, C_OK), (1, C_BAD))}]}],
                  th=thresholds((None, C_IDLE), (1, C_OK)),
                  extra={"options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                                     "colorMode": "value", "graphMode": "none", "textMode": "value_and_name",
                                     "justifyMode": "center", "orientation": "horizontal", "wideLayout": True}}))
    P.append(stat("Available from the sun", 8, y, 4, 4, [target(q_last("pv-array", "available_kw"))], unit=KW,
                  decimals=0, fixed="#FFF899"))
    P.append(stat("Array output", 12, y, 4, 4, [target(q_last("pv-array", "ac_power_kw"))], unit=KW, decimals=0,
                  fixed=C_PV))
    P.append(stat("Solar energy today", 16, y, 4, 4, [target(q_last("pv-array", "energy_today_kwh"))], unit="kwatth",
                  decimals=2, fixed=C_PV))
    P.append(gauge("PV allowed by master", 20, y, 4, 4, [target(q_last("neo-01", "pv_curtailment_pct"))],
                   unit="percent", minv=0, maxv=100, decimals=0,
                   th=thresholds((None, C_WARN), (60, "#FADE2A"), (99, C_OK)),
                   desc="InteliNeo 'Curtailment Output': % of the PV rating the master allows."))
    y += 4
    pvq = q_series("pv-array", {"available_kw": "Available (sun)", "ac_power_kw": "Delivered"})
    P.append(timeseries("Solar - available vs delivered", 0, y, 12, 9, [target(pvq)], unit=KW, decimals=0,
                        overrides=[series_style("Available (sun)", "#FFF899", fill=6, dash=True),
                                   series_style("Delivered", C_PV, fill=40)],
                        desc="The gap is solar energy left on the table: curtailment while on gensets, trips, SAU stalls."))
    P.append(bargauge("Inverters now", 12, y, 12, 9,
                      [target(" UNION ALL ".join(
                          f"SELECT '{d.upper()}' AS inverter, ({q_last(d, 'ac_power_kw', 'v')}) AS kW" for d in INVERTERS))],
                      unit=KW, minv=0, maxv=352, decimals=0,
                      th=thresholds((None, C_PV)),
                      extra={"options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
                                         "orientation": "horizontal", "displayMode": "lcd", "valueMode": "color",
                                         "namePlacement": "left", "showUnfilled": True, "sizing": "auto",
                                         "minVizHeight": 16, "minVizWidth": 8, "maxVizHeight": 300}}))
    y += 9
    names = {d: d.upper() for d in INVERTERS}
    P.append(timeseries("Inverter output", 0, y, 12, 8, [target(q_devices(INVERTERS, "ac_power_kw", names=names))],
                        unit=KW, decimals=0, fill=0, legend="right", calcs=("lastNotNull", "max")))
    P.append(timeseries("Inverter cabin temperature", 12, y, 12, 8,
                        [target(q_devices(INVERTERS, "cabin_temp_c", names=names))], unit="celsius", decimals=1, fill=0,
                        legend="right", calcs=("lastNotNull", "max"),
                        extra={"fieldConfig": {"defaults": {"unit": "celsius", "decimals": 1, "color": {"mode": "palette-classic"},
                                                            "custom": {"lineWidth": 1, "fillOpacity": 0, "showPoints": "never",
                                                                       "spanNulls": 3600000, "thresholdsStyle": {"mode": "dashed"}},
                                                            "thresholds": thresholds((None, "transparent"), (55, C_WARN), (70, C_BAD))},
                                               "overrides": []}}))
    y += 8
    cols = [("grid_connected", "On grid"), ("ac_power_kw", "AC kW"), ("dc_power_kw", "DC kW"),
            ("efficiency_pct", "Efficiency %"), ("cabin_temp_c", "Cabin °C"), ("energy_today_kwh", "Today kWh"),
            ("energy_total_kwh", "Total MWh"), ("fault", "Fault"), ("comm_error", "Link")]
    sel = ", ".join(
        (f"({q_last('d', p, 'v')}) / 1000.0 AS \"{n}\"" if p == "energy_total_kwh" else f"({q_last('d', p, 'v')}) AS \"{n}\"")
        .replace("device_id='d'", "device_id=i.id") for p, n in cols)
    devs = " UNION ALL ".join(f"SELECT '{d}' AS id" for d in INVERTERS)
    table_sql = f"SELECT UPPER(i.id) AS Inverter, {sel} FROM ({devs}) i ORDER BY i.id"

    def col(name, props):
        return {"matcher": {"id": "byName", "options": name}, "properties": props}

    cell = lambda t: {"id": "custom.cellOptions", "value": t}
    P.append(panel("table", "Inverter fleet", 0, y, 24, 13, [target(table_sql)], decimals=1,
                   custom={"align": "center", "cellOptions": {"type": "auto"}, "inspect": False},
                   options={"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
                   overrides=[
                       col("Inverter", [{"id": "custom.width", "value": 110}]),
                       col("On grid", [{"id": "mappings", "value": vmap({1: ("● generating", C_OK), 0: ("○ off", C_IDLE)})},
                                       cell({"type": "color-text"})]),
                       col("AC kW", [cell({"type": "gauge", "mode": "lcd", "valueDisplayMode": "text"}), {"id": "min", "value": 0},
                                     {"id": "max", "value": 352}, {"id": "decimals", "value": 0},
                                     {"id": "color", "value": {"mode": "continuous-YlRd"}}]),
                       col("DC kW", [{"id": "decimals", "value": 0}]),
                       col("Efficiency %", [cell({"type": "color-text"}), {"id": "decimals", "value": 1},
                                            {"id": "thresholds", "value": thresholds((None, C_IDLE), (90, C_WARN), (97, C_OK))},
                                            {"id": "color", "value": {"mode": "thresholds"}}]),
                       col("Cabin °C", [cell({"type": "color-background", "mode": "gradient"}), {"id": "decimals", "value": 1},
                                        {"id": "thresholds", "value": thresholds((None, "#37872D"), (55, C_WARN), (70, C_BAD))},
                                        {"id": "color", "value": {"mode": "thresholds"}}]),
                       col("Today kWh", [{"id": "decimals", "value": 0}]),
                       col("Total MWh", [{"id": "decimals", "value": 1}]),
                       col("Fault", [{"id": "mappings", "value": vmap({0: ("—", C_IDLE), 1: ("FAULT", C_BAD)})},
                                     cell({"type": "color-background"})]),
                       col("Link", [{"id": "mappings", "value": COMM}, cell({"type": "color-background"})]),
                   ]))
    y += 13
    strings = q_series("$inverter", {f"string_current_{s}_a": f"String {s}" for s in range(1, 13)})
    P.append(timeseries("String currents - $inverter", 0, y, 24, 8, [target(strings)], unit="amp", decimals=1,
                        fill=0, legend="right", calcs=("lastNotNull", "max", "mean"),
                        desc="Pick the inverter at the top of the dashboard. A string far under its neighbours "
                             "while the sun is up = a blown fuse, a loose connector or shading."))
    y += 8

    # ---- mains -----------------------------------------------------------------------------------
    P.append(row("Mains  ·  3 × ComAp InteliMains 210 G2", y)); y += 1
    for m, d in enumerate(MAINS):
        P.append(stat(f"MAIN {m + 1}", m * 4, y, 4, 6,
                      [target(q_last(d, "available", "Mains breaker"), "A"),
                       target(q_last(d, "power_kw", "Import"), "B"),
                       target(q_last(d, "load_p_kw", "Load"), "C")],
                      text_mode="value_and_name", decimals=0,
                      overrides=[{"matcher": {"id": "byName", "options": "Mains breaker"},
                                  "properties": [{"id": "mappings", "value": vmap({1: ("CLOSED", C_OK), 0: ("OPEN", C_BAD)})}]},
                                 {"matcher": {"id": "byName", "options": "Import"},
                                  "properties": [{"id": "unit", "value": KW}, {"id": "color", "value": {"mode": "fixed", "fixedColor": C_MAINS}}]},
                                 {"matcher": {"id": "byName", "options": "Load"},
                                  "properties": [{"id": "unit", "value": KW}, {"id": "color", "value": {"mode": "fixed", "fixedColor": C_LOAD}}]}],
                      extra={"options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                                         "colorMode": "value", "graphMode": "none", "textMode": "value_and_name",
                                         "justifyMode": "center", "orientation": "horizontal", "wideLayout": True}}))
    P.append(timeseries("Import per incomer", 12, y, 12, 12,
                        [target(q_devices(MAINS, "power_kw", names={d: d.replace("main-0", "MAIN ") for d in MAINS}))],
                        unit=KW, decimals=0, fill=25, stack=True,
                        overrides=[series_style("MAIN 1", "#5794F2"), series_style("MAIN 2", "#8AB8FF"),
                                   series_style("MAIN 3", "#1F60C4")]))
    mains_tl = q_devices(MAINS, "available", names={d: d.replace("main-0", "MAIN ") for d in MAINS})
    P.append(state_timeline("Mains breakers", 0, y + 6, 12, 6, [target(mains_tl)],
                            vmap({0: ("open", C_BAD), 1: ("closed", C_MAINS)})))
    y += 12

    # ---- energy ----------------------------------------------------------------------------------
    P.append(row("Energy & savings", y)); y += 1
    day = "date(ts, '+3 hours')"                                  # Erbil local day
    daily = (
        f"SELECT CAST(strftime('%s', d) AS INTEGER) + 32400 AS time, "
        f"MAX(pv) AS \"Solar PV\", MAX(0, MAX(pcc) - MIN(pcc) - MAX(pv) - (MAX(mn) - MIN(mn))) AS \"Gensets\", "
        f"MAX(mn) - MIN(mn) AS \"Mains\" FROM ("
        f"SELECT {day} AS d, "
        f"CASE WHEN device_id='pv-array' AND point_id='energy_today_kwh' THEN value END AS pv, "
        f"CASE WHEN device_id='neo-01' AND point_id='pcc_kwh' THEN value END AS pcc, "
        f"CASE WHEN device_id='neo-01' AND point_id='mains_kwh' THEN value END AS mn "
        f"FROM readings WHERE ((device_id='pv-array' AND point_id='energy_today_kwh') OR "
        f"(device_id='neo-01' AND point_id IN ('pcc_kwh','mains_kwh'))) AND quality='good' AND {RANGE}"
        f") GROUP BY d ORDER BY d")
    P.append(timeseries("Energy per day - by source", 0, y, 14, 9, [target(daily)], unit="kwatth", decimals=0,
                        bars=True, stack=True, fill=85, legend="bottom",
                        minv=0, extra={"timeFrom": "7d"},
                        overrides=[series_style("Solar PV", C_PV), series_style("Gensets", C_GEN),
                                   series_style("Mains", C_MAINS)],
                        desc="Solar = the SAU's daily generation; Mains = InteliNeo mains kWh counter; Gensets = site energy "
                             "(PCC kWh counter) minus both. Set the time range to 7 days or more."))
    today = f"date(ts, '+3 hours') = date('now', '+3 hours')"
    pv_today = (f"SELECT MAX(value) FROM readings WHERE device_id='pv-array' AND point_id='energy_today_kwh' "
                f"AND quality='good' AND {today}")
    P.append(stat("Diesel saved by solar today", 14, y, 5, 9, [target(f"SELECT ({pv_today}) * 0.27 AS \"Diesel\"")],
                  unit="suffix: L", decimals=0, fixed=C_OK, spark=False,
                  desc="Estimate: solar kWh today x 0.27 L/kWh (typical genset fuel use at part load). Change the factor "
                       "in tools/grafana_dashboard.py when the gensets' real consumption is known."))
    P.append(stat("CO₂ avoided today", 19, y, 5, 9, [target(f"SELECT ({pv_today}) * 0.72 / 1000 AS \"CO2\"")],
                  unit="suffix: t", decimals=1, fixed="#56A64B",
                  desc="Estimate: solar kWh today x 0.72 kg CO2/kWh (diesel generation)."))
    y += 9

    # ---- comms & events --------------------------------------------------------------------------
    P.append(row("Communications & events", y)); y += 1
    all_dev = ["neo-01", "pv-array"] + MAINS + INVERTERS
    comm_names = {"neo-01": "NEO master", "pv-array": "SAU", **{d: d.replace("main-0", "MAIN ") for d in MAINS},
                  **{d: d.upper() for d in INVERTERS}}
    comm = (f"SELECT {MINUTE} AS time, " + ", ".join(
        f"MAX(CASE WHEN device_id='{d}' THEN value END) AS \"{comm_names[d]}\"" for d in all_dev) +
        f" FROM readings WHERE point_id='comm_error' AND {RANGE} GROUP BY time ORDER BY time")
    P.append(state_timeline("Device links (every poll)", 0, y, 24, 10, [target(comm)],
                            vmap({0: ("", C_OK), 1: ("NO DATA", C_BAD)}),
                            desc="Green = the device answered. Red = no valid data (for the inverters: the SAU in front of them was silent)."))
    y += 10
    events = (f"SELECT {TIME} AS time, UPPER(COALESCE(severity,'info')) AS Severity, COALESCE(device_id,'-') AS Device, "
              f"kind AS Kind, message AS Message FROM events WHERE {RANGE} ORDER BY ts DESC LIMIT 300")
    P.append(panel("table", "Event log", 0, y, 24, 11, [target(events)],
                   custom={"align": "left", "cellOptions": {"type": "auto"}, "inspect": False},
                   options={"showHeader": True, "cellHeight": "sm", "footer": {"show": False},
                            "sortBy": [{"displayName": "time", "desc": True}]},
                   overrides=[
                       {"matcher": {"id": "byName", "options": "time"},
                        "properties": [{"id": "unit", "value": "dateTimeAsLocal"}, {"id": "custom.width", "value": 190},
                                       {"id": "displayName", "value": "Time"}]},
                       {"matcher": {"id": "byName", "options": "Severity"},
                        "properties": [{"id": "custom.width", "value": 110}, {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                                       {"id": "mappings", "value": vmap({"ERROR": ("ERROR", C_BAD), "WARNING": ("WARNING", C_WARN),
                                                                         "INFO": ("INFO", "#3274D9")})}]},
                       {"matcher": {"id": "byName", "options": "Device"}, "properties": [{"id": "custom.width", "value": 110}]},
                       {"matcher": {"id": "byName", "options": "Kind"}, "properties": [{"id": "custom.width", "value": 150}]},
                   ]))
    y += 11

    annotations = {"list": [
        {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
         "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
        {"datasource": DS, "enable": True, "name": "Grid lost / back", "iconColor": C_MAINS,
         "target": target(f"SELECT {TIME} AS time, message AS text FROM events WHERE kind IN ('grid_lost','grid_restored') AND {RANGE}")},
        {"datasource": DS, "enable": True, "name": "SAU stalls", "iconColor": C_BAD,
         "target": target(f"SELECT {TIME} AS time, message AS text FROM events WHERE kind='comms_lost' AND device_id='pv-array' AND {RANGE}")},
    ]}

    templating = {"list": [{
        "type": "custom", "name": "inverter", "label": "Inverter (string view)", "query": ",".join(INVERTERS),
        "current": {"text": "inv-01", "value": "inv-01"}, "multi": False, "includeAll": False, "hide": 0,
        "options": [{"text": d, "value": d, "selected": d == "inv-01"} for d in INVERTERS],
    }]}

    return {
        "uid": "cci-plant-monitoring", "title": "CCI Plant Monitoring", "tags": ["cci", "plant", "erbil"],
        "timezone": "browser", "editable": True, "graphTooltip": 1, "schemaVersion": 39, "version": 1,
        "time": {"from": "now-24h", "to": "now"}, "refresh": "30s",
        "timepicker": {"refresh_intervals": ["30s", "1m", "5m", "15m", "1h"]},
        "fiscalYearStartMonth": 0, "liveNow": False, "weekStart": "",
        "annotations": annotations, "templating": templating, "links": [], "panels": P,
    }


if __name__ == "__main__":
    d = build()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1, ensure_ascii=False)
    print(f"{len(d['panels'])} panels -> {OUT}")
