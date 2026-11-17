"""The analyzers' energy export: kWh for today and the two days before, from the meters' own energy counters.

Three sheets, in the plant's agreed layout:
  Daily kWh Summary   each analyzer's kWh per day, its 3-day total, and all analyzers together
  5-Min Charts        one chart per analyzer: the three days over each other, kWh per 5-minute interval
  5-Min Data          the numbers behind the charts

How the kWh is worked out. Every analyzer keeps a counter of the energy into its load (e_in_kwh), stored about
every five minutes. The counter's value at a time between two readings is taken on the straight line between them
(a cumulative counter between readings minutes apart), so:
  - a 5-minute interval's kWh is the counter at its end minus at its start. When the readings around either end
    are too far apart (a reading was missed), the interval is left blank: never a zero that was not measured;
  - a day's kWh is the counter at the end of the day minus at its start, so it stays exact when 5-minute readings
    were missed in between. Today runs to the latest reading. A day with readings for only part of it is counted
    from its first to its last reading and listed under "Data coverage";
  - a counter that goes backwards (reset, replaced meter) ends one stretch and begins another; the stretches are
    added, the step back is not.
Days are the plant's local days (site time zone).
"""
from __future__ import annotations

import io
from bisect import bisect_right
from datetime import datetime, time, timedelta, timezone

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference, Series
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.marker import Marker
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText
from openpyxl.chart.title import Title
from openpyxl.chart.text import Text
from openpyxl.drawing.line import LineProperties
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from openpyxl.drawing.text import CharacterProperties, Font as DrawFont, Paragraph, ParagraphProperties, RegularTextRun
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .core import Collector
from .views import _counter_point, local_tz

DAYS = 3
STEP_S = 300
SLOTS = 86400 // STEP_S                 # 288 five-minute intervals in a day
DAY_GAP_S = 3600                        # a day's start or end may sit this far inside a gap between two readings
EDGE_S = 600                            # a day whose readings start or end further than this from its edge is "partial"

# the workbook's colours (the plant's example file)
NAVY, NAVY2, INK, MUTED, RED = "14283F", "243D59", "172C46", "66788D", "E2231A"
ZEBRA, LINE, WHITE, TODAY_TINT, TODAY_SUM = "F5F8FB", "D8E1EB", "FFFFFF", "F0F9F6", "EEF9F6"
AMBER_TX, AMBER_BG = "875814", "FFF2DE"
DAY_LINE = ["2276D2", "F4A340", "1BA888"]                    # the three days' lines in the charts
DAY_CHIP = [("2276D2", "E9F3FF"), ("AB6509", "FFF4E6"), ("087A63", "EEF9F6")]


# ---------------------------------------------------------------------------------------------- the numbers
def _label(d) -> str:
    if d.name and d.name != d.id:
        return d.name
    head, _, n = d.id.partition("-")
    return f"{head.capitalize()} {n}" if n else d.id


def _readings(c: Collector, did: str, pid: str, since: datetime, until: datetime) -> tuple[list[float], list[float]]:
    ts, vs = [], []
    for t, v in c.store.history(did, pid, since, until):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            d = datetime.fromisoformat(t)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            e = d.timestamp()
            if ts and e <= ts[-1]:
                continue                               # the same instant twice: the first one stands
            ts.append(e)
            vs.append(float(v))
    return ts, vs


def _counter_at(ts: list[float], vs: list[float], t: float, max_gap: float) -> float | None:
    """The counter at time t: on the line between the reading before and the reading after, when those two are
    no more than max_gap apart and the counter did not go backwards between them. None otherwise."""
    i = bisect_right(ts, t)
    if i and ts[i - 1] == t:
        return vs[i - 1]
    if i == 0 or i >= len(ts):
        return None
    t0, t1, v0, v1 = ts[i - 1], ts[i], vs[i - 1], vs[i]
    if t1 - t0 > max_gap or v1 < v0:
        return None
    return v0 + (v1 - v0) * (t - t0) / (t1 - t0)


def _climb(points: list[float]) -> tuple[float, bool]:
    """The energy along a run of counter values: every step up added; a step back (a reset) is not counted."""
    total, reset = 0.0, False
    for a, b in zip(points, points[1:]):
        if b >= a:
            total += b - a
        else:
            reset = True
    return total, reset


def _hm(epoch: float, tz) -> str:
    return datetime.fromtimestamp(epoch, tz).strftime("%H:%M")


def analyzer_energy(c: Collector, now: datetime | None = None) -> dict:
    """Per analyzer: kWh for today and the two days before (daily, and per 5-minute interval), from its counter."""
    reg = c.registry
    tz = local_tz(reg)
    now = now or c.now()
    today = now.astimezone(tz).date()
    days = [today - timedelta(days=k) for k in range(DAYS - 1, -1, -1)]
    edge = [datetime.combine(d, time.min, tzinfo=tz).timestamp() for d in days + [today + timedelta(days=1)]]
    now_e = now.timestamp()
    every = float(getattr(getattr(c, "meters", None), "store_every_s", 300) or 300)
    slot_gap = 1.5 * max(every, STEP_S)                  # more than this between two readings: one was missed
    removed = getattr(c, "removed", set())
    out, notes = [], []
    latest_e = None
    for d in reg.by_kind("analyzer"):
        if d.id in removed:
            continue
        pid, _kw = _counter_point(d)
        label = _label(d)
        row = {"id": d.id, "label": label, "counter_point": pid, "daily": [None] * DAYS, "total": None,
               "intervals": [[None] * SLOTS for _ in range(DAYS)], "last_reading": None, "through": None}
        ts, vs = _readings(c, d.id, pid, datetime.fromtimestamp(edge[0] - DAY_GAP_S, timezone.utc), now) if pid else ([], [])
        if not ts:
            notes.append(f"{label}: no energy-counter reading in these three days." if pid else f"{label}: this meter has no energy counter.")
            out.append(row)
            continue
        last_e = ts[-1]
        row["last_reading"] = datetime.fromtimestamp(last_e, tz).isoformat(timespec="seconds")
        latest_e = last_e if latest_e is None else max(latest_e, last_e)
        for k in range(DAYS):
            lo, hi = edge[k], edge[k + 1]
            inside = [(t, v) for t, v in zip(ts, vs) if lo < t < min(hi, now_e + 1)]
            a = _counter_at(ts, vs, lo, DAY_GAP_S)
            is_today = k == DAYS - 1
            b = None if is_today else _counter_at(ts, vs, hi, DAY_GAP_S)
            pts = ([a] if a is not None else []) + [v for _t, v in inside] + ([b] if b is not None else [])
            if len(pts) >= 2:
                kwh, reset = _climb(pts)
                row["daily"][k] = round(kwh, 3)
                first_t = lo if a is not None else inside[0][0]
                last_t = hi if b is not None else inside[-1][0]
                dn = days[k].strftime("%d %b").lstrip("0")
                if reset:
                    notes.append(f"{label}, {dn}: the counter went backwards (reset or replaced meter); the step back is not counted.")
                late, early = first_t - lo > EDGE_S, (not is_today) and hi - last_t > EDGE_S
                if late and early:
                    notes.append(f"{label}, {dn}: counter readings only from {_hm(first_t, tz)} to {_hm(last_t, tz)}; counted between those.")
                elif late:
                    notes.append(f"{label}, {dn}: counter readings start at {_hm(first_t, tz)}; counted from then.")
                elif early:
                    notes.append(f"{label}, {dn}: counter readings stop at {_hm(last_t, tz)}; counted until then.")
                elif is_today and now_e - last_t > 3 * every:
                    notes.append(f"{label}, {dn}: last counter reading at {_hm(last_t, tz)}; counted until then.")
                seq = [t for t, _v in inside]
                gaps = [(u, w) for u, w in zip(seq, seq[1:]) if w - u > slot_gap]
                if gaps:
                    u, w = max(gaps, key=lambda g: g[1] - g[0])
                    more = f" and {len(gaps) - 1} shorter" if len(gaps) > 1 else ""
                    notes.append(f"{label}, {dn}: no reading {_hm(u, tz)}-{_hm(w, tz)}{more}; those 5-minute cells are blank, the day's total still counts that energy.")
            elif not is_today or lo < now_e - 3 * every:
                notes.append(f"{label}, {days[k].strftime('%d %b').lstrip('0')}: no energy-counter readings.")
            # the 5-minute intervals: both ends known, and not after the latest reading
            prev = _counter_at(ts, vs, lo, slot_gap)
            for i in range(SLOTS):
                t1 = lo + (i + 1) * STEP_S
                if t1 > last_e:
                    break
                cur = _counter_at(ts, vs, t1, slot_gap)
                if prev is not None and cur is not None and cur >= prev:
                    row["intervals"][k][i] = round(cur - prev, 3)
                    if is_today:
                        row["through"] = i + 1
                prev = cur
        got = [x for x in row["daily"] if x is not None]
        row["total"] = round(sum(got), 3) if got else None
        out.append(row)
    daily_all = []
    for k in range(DAYS):
        got = [r["daily"][k] for r in out if r["daily"][k] is not None]
        daily_all.append(round(sum(got), 3) if got else None)
    got = [x for x in daily_all if x is not None]
    # how much of each day the 5-minute intervals cover (today: of the intervals that have ended)
    cover = []
    for k in range(DAYS):
        expect = SLOTS if k < DAYS - 1 else max(0, min(SLOTS, int((now_e - edge[k]) // STEP_S)))
        have = sum(1 for r in out for x in r["intervals"][k][:expect] if x is not None)
        cover.append(round(100 * have / (expect * len(out)), 1) if expect and out else None)
    through = max((r["through"] for r in out if r["through"]), default=None)
    off = now.astimezone(tz).utcoffset() or timedelta(0)
    mins = int(off.total_seconds() // 60)
    return {"days": [d.isoformat() for d in days], "timezone": reg.site.timezone or "UTC",
            "utc_offset": f"{'+' if mins >= 0 else '-'}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}", "step_s": STEP_S,
            "generated": now.astimezone(tz).isoformat(timespec="seconds"),
            "latest_reading": datetime.fromtimestamp(latest_e, tz).isoformat(timespec="seconds") if latest_e else None,
            "through": f"{(through * STEP_S) // 3600:02d}:{(through * STEP_S) % 3600 // 60:02d}" if through else None,
            "analyzers": out, "all": {"daily": daily_all, "total": round(sum(got), 3) if got else None},
            "coverage_pct": cover, "notes": notes}


# ---------------------------------------------------------------------------------------------- the workbook
def _font(size=10, bold=False, color=INK, italic=False) -> Font:
    return Font(name="Arial", size=size, bold=bold, italic=italic, color=color)


def _fill(rgb: str) -> PatternFill:
    return PatternFill("solid", fgColor=rgb)


def _put(ws, ref, value=None, font=None, fill=None, align=None, fmt=None, border=None):
    cell = ws[ref]
    if value is not None:
        cell.value = value
    if font is not None:
        cell.font = font
    if fill is not None:
        cell.fill = fill
    if align is not None:
        cell.alignment = align
    if fmt is not None:
        cell.number_format = fmt
    if border is not None:
        cell.border = border
    return cell


CENTER = Alignment(horizontal="center", vertical="center")
LEFT_MID = Alignment(horizontal="left", vertical="center")
MID = Alignment(vertical="center")
RIGHT_MID = Alignment(horizontal="right", vertical="center")
RIGHT = Alignment(horizontal="right")
UNDER = Border(bottom=Side(style="thin", color=LINE))


def _span(days: list) -> str:
    a, b = days[0], days[-1]
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {b.strftime('%B %Y')}"
    if a.year == b.year:
        return f"{a.day} {a.strftime('%b')} – {b.day} {b.strftime('%b %Y')}"
    return f"{a.day} {a.strftime('%b %Y')} – {b.day} {b.strftime('%b %Y')}"


def _dm(d) -> str:
    return f"{d.day} {d.strftime('%b')}"


def _chart_text(size: int) -> RichText:
    cp = CharacterProperties(sz=size, latin=DrawFont(typeface="Arial"), ea=DrawFont(typeface="Arial"), cs=DrawFont(typeface="Arial"))
    return RichText(p=[Paragraph(pPr=ParagraphProperties(defRPr=cp), endParaRPr=cp)])


def _chart_title(text: str, size: int) -> Title:
    cp = CharacterProperties(sz=size, latin=DrawFont(typeface="Arial"), ea=DrawFont(typeface="Arial"), cs=DrawFont(typeface="Arial"))
    return Title(tx=Text(rich=RichText(p=[Paragraph(pPr=ParagraphProperties(defRPr=cp), r=[RegularTextRun(rPr=cp, t=text)])])), overlay=False)


def _grid() -> ChartLines:
    return ChartLines(spPr=GraphicalProperties(ln=LineProperties(solidFill="CCCCCC", prstDash="dash", w=9525)))


def workbook(c: Collector, data: dict | None = None) -> bytes:
    data = data or analyzer_energy(c)
    days = [datetime.fromisoformat(d).date() for d in data["days"]]
    rows = data["analyzers"]
    n = len(rows)
    gen = datetime.fromisoformat(data["generated"])
    latest = datetime.fromisoformat(data["latest_reading"]) if data["latest_reading"] else None
    through = data["through"]
    cover = data["coverage_pct"]
    day_txt = [f"{d.day} {d.strftime('%b %Y')}" for d in days]
    today_note = (f"{_dm(days[-1])} is a partial day: the last 5-minute interval ends at {through}." if through
                  else f"{_dm(days[-1])}: no 5-minute interval has a reading yet.")

    wb = Workbook()
    # ---------------- sheet 1: the daily summary
    s = wb.active
    s.title = "Daily kWh Summary"
    s.sheet_view.showGridLines = False
    s.sheet_properties.tabColor = NAVY
    for col, w in zip("ABCDEFGHIJ", (3, 24, 19, 19, 19, 21, 4, 18, 38, 4)):
        s.column_dimensions[col].width = w
    _put(s, "B2", "Analyzer energy export", _font(16, True))
    _put(s, "B3", f"Daily energy consumption (kWh), {_span(days)}", _font(11, color=MUTED))
    for col in "BCDEF":
        s[f"{col}4"].border = Border(bottom=Side(style="medium", color=RED))
    s.row_dimensions[2].height = 33
    s.row_dimensions[6].height = 34
    for ref, val, font, fill in (("B6", "METERS", _font(10, True), ZEBRA), ("C6", n, _font(13, True, RED), ZEBRA),
                                 ("D6", "INTERVAL", _font(10, True), ZEBRA), ("E6", "5 minutes", _font(10, True), ZEBRA),
                                 ("F6", f"LATEST {latest.strftime('%H:%M')}" if latest else "NO READINGS", _font(10, True, AMBER_TX), AMBER_BG)):
        _put(s, ref, val, font, _fill(fill), CENTER)
    _put(s, "B8", "3-day consumption by analyzer", _font(12, True))
    s.row_dimensions[9].height = 34
    head = _font(10, True, WHITE)
    _put(s, "B9", "Analyzer", head, _fill(NAVY), LEFT_MID)
    for col, d in zip("CDE", days):
        _put(s, f"{col}9", datetime(d.year, d.month, d.day), head, _fill(NAVY), CENTER, "dd/mm/yyyy")
    _put(s, "F9", "3-day total (kWh)", head, _fill(NAVY), CENTER)
    r0 = 10
    for i, a in enumerate(rows):
        r = r0 + i
        s.row_dimensions[r].height = 30
        fill = _fill(WHITE if i % 2 == 0 else ZEBRA)
        _put(s, f"B{r}", a["label"], _font(11), fill, MID, border=UNDER)
        for col, v in zip("CDE", a["daily"]):
            _put(s, f"{col}{r}", v, _font(11), _fill(TODAY_SUM) if col == "E" else fill, RIGHT_MID, "#,##0.0", UNDER)
        _put(s, f"F{r}", a["total"], _font(11, True, NAVY), fill, RIGHT_MID, "#,##0.0", UNDER)
    rt = r0 + n
    s.row_dimensions[rt].height = 40
    tot = _font(11, True, WHITE)
    _put(s, f"B{rt}", "ALL ANALYZERS", tot, _fill(NAVY), MID)
    for col, v in zip("CDE", data["all"]["daily"]):
        _put(s, f"{col}{rt}", v, tot, _fill(NAVY), RIGHT_MID, "#,##0.0")
    _put(s, f"F{rt}", data["all"]["total"], tot, _fill(NAVY), RIGHT_MID, "#,##0.0")
    # the details panel beside the table
    _put(s, "H9", "EXPORT DETAILS", head, _fill(NAVY))
    _put(s, "I9", None, head, _fill(NAVY))
    details = [("Date range", f"{_span(days)} (today + two previous days)"),
               ("Daily metric", "Imported energy consumption (kWh)"),
               ("Calculation", "Energy-counter differences, per meter"),
               ("Trend interval", f"Every 5 minutes, plant time (UTC{data['utc_offset']})"),
               ("Chart unit", "kWh consumed in each 5-min interval"),
               ("Latest reading", f"{latest.strftime('%H:%M')} on {_dm(latest.date())} {latest.year}" if latest else "none in these three days"),
               ("Missing data", "Blank intervals, not false zeros")]
    for i, (k, v) in enumerate(details):
        r = r0 + i
        if not s.row_dimensions[r].height:
            s.row_dimensions[r].height = 30
        stripe = _fill(WHITE if i % 2 == 0 else ZEBRA)
        _put(s, f"H{r}", k, _font(10, True, MUTED), stripe, MID)
        _put(s, f"I{r}", v, _font(10), stripe, MID)
    # the notes under the table, and the data coverage beside them
    rn = max(rt, r0 + len(details) - 1) + 2
    s.row_dimensions[rn].height = 30
    _put(s, f"B{rn}", "Reporting notes", _font(11, True))
    lines = ["Daily cells show kWh consumed, not cumulative meter counter values.",
             (f"{_dm(days[-1])} is partial, through the latest reading at {latest.strftime('%H:%M')}; previous days cover 24 hours."
              if latest and latest.date() == days[-1] else f"{_dm(days[-1])} has no reading yet; previous days cover 24 hours."),
             "A missing 5-minute reading stays blank; it never becomes zero.",
             "kWh is calculated from the differences of each meter's own energy counter."]
    for i, t in enumerate(lines, 1):
        s.row_dimensions[rn + i].height = 22
        _put(s, f"B{rn + i}", t, _font(10, color=MUTED))
    _put(s, f"H{rn}", "DATA COVERAGE", _font(10, True, AMBER_TX), _fill(AMBER_BG))
    _put(s, f"I{rn}", None, _font(10, True, AMBER_TX), _fill(AMBER_BG))
    notes = data["notes"]
    shown = notes[:10] + ([f"... and {len(notes) - 10} more."] if len(notes) > 10 else [])
    if not shown:
        shown = ["Every analyzer has counter readings for the whole period."]
    for i, t in enumerate(shown, 1):
        if not s.row_dimensions[rn + i].height:
            s.row_dimensions[rn + i].height = 22
        _put(s, f"H{rn + i}", t, _font(10, color=MUTED, italic=True))

    # ---------------- sheet 3 (built before the charts that read it): the 5-minute numbers
    dws = wb.create_sheet("5-Min Data")
    dws.sheet_view.showGridLines = False
    dws.sheet_properties.tabColor = "E6EDF4"
    for col, w in zip("ABCDEF", (3, 20, 19, 19, 19, 4)):
        dws.column_dimensions[col].width = w
    _put(dws, "B2", "Five-minute energy readings", _font(16, True))
    _put(dws, "B3", f"Readings in kWh per 5-minute interval, from the meters' energy counters. Local time (UTC{data['utc_offset']}).", _font(10, color=MUTED, italic=True))
    _put(dws, "B5", "00:00 indicates the interval 00:00–00:05. Unavailable and future readings are blank.", _font(10, color=MUTED, italic=True))
    dws.row_dimensions[2].height = 33
    block = SLOTS + 6                                   # header, column heads, 288 rows, blank, note, two blanks
    first_row = {}
    times = [f"{(i * STEP_S) // 3600:02d}:{(i * STEP_S) % 3600 // 60:02d}" for i in range(SLOTS)]
    f_time, f_num = _font(10, color=MUTED), _font(10)
    fz, ft = _fill(ZEBRA), _fill(TODAY_TINT)
    for bi, a in enumerate(rows):
        h = 8 + bi * block
        dws.row_dimensions[h].height = 27
        dws.row_dimensions[h + 1].height = 26
        for col in "BCDE":
            _put(dws, f"{col}{h}", None, head, _fill(NAVY))
        _put(dws, f"B{h}", a["label"].upper())
        _put(dws, f"E{h}", "kWh / 5 min", align=RIGHT)
        _put(dws, f"B{h + 1}", "Time of day", head, _fill(NAVY2), CENTER)
        for col, t in zip("CDE", day_txt):
            _put(dws, f"{col}{h + 1}", t, head, _fill(NAVY2), CENTER)
        first_row[a["id"]] = h + 2
        for i in range(SLOTS):
            r = h + 2 + i
            dws.row_dimensions[r].height = 18
            z = fz if i % 2 == 0 else None
            _put(dws, f"B{r}", times[i], f_time, z, MID)
            _put(dws, f"C{r}", a["intervals"][0][i], f_num, z, RIGHT_MID, "0.000")
            _put(dws, f"D{r}", a["intervals"][1][i], f_num, z, RIGHT_MID, "0.000")
            _put(dws, f"E{r}", a["intervals"][2][i], f_num, ft, RIGHT_MID, "0.000")
        _put(dws, f"B{h + 2 + SLOTS + 1}", today_note, _font(9, color=MUTED, italic=True))
    dws.freeze_panes = "A10"

    # ---------------- sheet 2: one chart per analyzer
    cws = wb.create_sheet("5-Min Charts", 1)
    cws.sheet_view.showGridLines = False
    cws.sheet_properties.tabColor = RED
    cws.column_dimensions["A"].width = 3
    for col in "BCDEFGHIJK":
        cws.column_dimensions[col].width = 13
    cws.column_dimensions["L"].width = 3
    _put(cws, "B2", "Five-minute energy comparison", _font(16, True))
    _put(cws, "B3", "One chart for every analyzer. Compare energy use at the same time of day across three dates.", _font(11, color=MUTED))
    cws.row_dimensions[2].height = 33
    cws.row_dimensions[5].height = 30
    chips = []
    for k in range(DAYS):
        if k == DAYS - 1:
            chips.append(f"{_dm(days[k])}: partial through {through}" if through else f"{_dm(days[k])}: no readings yet")
        elif cover[k] is None or cover[k] >= 99.95:
            chips.append(f"{_dm(days[k])}: complete")
        elif cover[k] <= 0:
            chips.append(f"{_dm(days[k])}: no readings")
        else:
            chips.append(f"{_dm(days[k])}: {cover[k]:.1f} % of readings" if cover[k] >= 99 else f"{_dm(days[k])}: {cover[k]:.0f} % of readings")
    for (cols, text), (tx, bg) in zip((("BC", chips[0]), ("DE", chips[1]), ("FGHI", chips[2])), DAY_CHIP):
        for col in cols:
            _put(cws, f"{col}5", None, _font(10, True, tx), _fill(bg), MID)
        cws[f"{cols[0]}5"].value = text
    span_words = f"{days[0].day}, {days[1].day}, and {days[2].day} {days[2].strftime('%B')}" if len({(d.year, d.month) for d in days}) == 1 \
        else ", ".join(_dm(d) for d in days[:-1]) + f", and {_dm(days[-1])}"
    for bi, a in enumerate(rows):
        h = 8 + bi * 20
        cws.row_dimensions[h].height = 27
        for col in "BCDEFGHIJK":
            _put(cws, f"{col}{h}", None, head, _fill(NAVY2))
        _put(cws, f"B{h}", a["label"].upper())
        _put(cws, f"I{h}", "THREE-DAY TOTAL (kWh)", _font(9, True, "C9D9E9"))
        _put(cws, f"K{h}", a["total"], _font(11, True, WHITE), align=RIGHT, fmt="#,##0.0")
        fr = first_row[a["id"]]
        ch = LineChart()
        ch.title = _chart_title(f"{a['label']} — 5-minute energy (kWh)", 975)
        ch.roundedCorners = False
        ch.display_blanks = "gap"
        for k, col in enumerate((3, 4, 5)):
            ser = Series(Reference(dws, min_col=col, min_row=fr, max_row=fr + SLOTS - 1), title=day_txt[k])
            ser.graphicalProperties = GraphicalProperties(solidFill=DAY_LINE[k])
            ser.graphicalProperties.line = LineProperties(solidFill=DAY_LINE[k], prstDash="solid", w=19050)
            ser.marker = Marker(symbol="none")
            ser.smooth = False
            ch.series.append(ser)
        ch.set_categories(Reference(dws, min_col=2, min_row=fr, max_row=fr + SLOTS - 1))
        ch.x_axis.title = "Time of day"
        ch.x_axis.tickLblSkip = 24
        ch.x_axis.tickMarkSkip = 24
        ch.x_axis.majorGridlines = _grid()
        ch.x_axis.txPr = _chart_text(675)
        ch.x_axis.delete = False
        ch.y_axis.title = "kWh / 5 min"
        ch.y_axis.number_format = "0.0"
        ch.y_axis.majorGridlines = _grid()
        ch.y_axis.txPr = _chart_text(750)
        ch.y_axis.delete = False
        ch.legend.position = "t"
        ch.legend.txPr = _chart_text(825)
        ch.graphical_properties = GraphicalProperties(ln=LineProperties(solidFill="D9D9D9", prstDash="solid", w=9525))
        ch.anchor = TwoCellAnchor(_from=AnchorMarker(col=1, row=h, colOff=0, rowOff=0), to=AnchorMarker(col=11, row=h + 17, colOff=0, rowOff=0))
        cws.add_chart(ch)
        _put(cws, f"B{h + 18}", f"Chart lines compare the same 5-minute time slot across {span_words}.", _font(9, color=MUTED, italic=True))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def filename(c: Collector, data: dict) -> str:
    return f"{c.registry.site.site_id}_analyzer-kwh_{data['days'][0]}_{data['days'][-1]}.xlsx"
