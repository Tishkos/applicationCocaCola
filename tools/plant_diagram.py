"""One-line and communication overview of the Erbil plant, drawn from what the archive, the history and the
site visit of 14 October 2026 established. Solid lines are power, dashed blue lines are communication, dashed
amber is what still has to be verified on site.

    python tools/plant_diagram.py                 -> docs/plant_overview.svg (standalone, light colours)
    python tools/plant_diagram.py --html page.html -> the same drawing inside a small page (page colours apply)
"""

import argparse
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1240, 800

ARIA = ("Single-line and communication overview of the Erbil hybrid plant: three grid incomers with "
        "InteliMains 210 G2 controllers feed a plant bus; five gensets with IG4 200 controllers and ten Sineng "
        "inverters behind two PV breakers feed the same bus; the InteliNeo 5500 master talks to the genset and "
        "mains controllers over CAN and to the Sineng SAU-100 over Modbus TCP, where it writes the PV setpoint; "
        "two UPS with unverified wiring hang off the plant load; a plant PC runs the read-only collector.")


def t(x, y, s, cls="", anchor="start", size=None):
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    c = f' class="{cls}"' if cls else ""
    z = f' font-size="{size}"' if size else ""
    return f'<text x="{x}" y="{y}"{a}{c}{z}>{s}</text>'


def line(x1, y1, x2, y2, cls="pwr", extra=""):
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="{cls}"{extra}/>'


def poly(points, cls, extra=""):
    pts = " ".join(f"{x},{y}" for x, y in points)
    return f'<polyline points="{pts}" class="{cls}"{extra}/>'


def box(x, y, w, h, lines, cls="dev", size=13):
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" class="{cls}"/>']
    n = len(lines)
    for i, s in enumerate(lines):
        yy = y + h / 2 + (i - (n - 1) / 2) * (size + 3) + size / 2 - 2
        out.append(t(x + w / 2, round(yy, 1), s, "lbl", "middle", size))
    return "\n".join(out)


def breaker(x, y, label, side="left", cmd=False, cls="pwr"):
    """A switch symbol on a vertical line through (x, y): gap 18 px, blade drawn slanted."""
    out = [line(x, y - 20, x, y - 9, cls), line(x, y + 9, x, y + 20, cls),
           line(x, y + 9, x + 9, y - 9, cls), f'<circle cx="{x}" cy="{y - 9}" r="1.8" class="dot"/>']
    if side == "left":
        out.append(t(x - 8, y + 4, label, "lbl", "end"))
        if cmd:
            out.append(t(x - 8, y + 18, "cmd ← Neo PLC", "cmd", "end", 11))
    else:
        out.append(t(x + 14, y + 4, label, "lbl"))
        if cmd:
            out.append(t(x + 14, y + 18, "cmd ← Neo PLC", "cmd", "start", 11))
    return "\n".join(out)


def body():
    o = []
    # ---- grid incomers ---------------------------------------------------------------------------
    o.append(t(80, 34, "Grid, three incomers", "hdr", size=15))
    o.append(t(80, 54, "export cap 1,300 kW per closed feeder (PLC setpoint)", "muted"))
    for i, x in enumerate((300, 620, 940), start=1):
        o.append(line(x - 28, 72, x + 28, 72, "pwr"))
        o.append(t(x, 66, f"Mains {i}", "lbl", "middle"))
        o.append(line(x, 72, x, 250, "pwr"))
        o.append(breaker(x, 212, f"MCB{i}"))
        ip = {1: "107", 2: "111", 3: "110"}[i]
        o.append(box(x + 18, 110, 150, 40, ["InteliMains 210 G2", f"192.168.0.{ip}"]))
        o.append(line(x + 93, 100, x + 93, 110, "comm"))
    # CAN bus: along y=100 through the three mains boxes, down the left edge, along y=480 to the master
    o.append(poly([(1033, 100), (40, 100), (40, 480), (640, 480), (640, 540)], "comm"))
    o.append(t(625, 522, "CAN bus · power management, load sharing", "comm-t", "end"))
    # ---- plant bus -------------------------------------------------------------------------------
    o.append(line(80, 250, 1160, 250, "bus"))
    o.append(t(650, 240, "Plant bus · load ≈ 2 MW on 14 Oct", "lbl"))
    # ---- gensets ---------------------------------------------------------------------------------
    o.append(t(80, 276, "Gensets", "hdr", size=15))
    for i, x in enumerate((130, 230, 330, 430, 530), start=1):
        o.append(line(x, 250, x, 336, "pwr"))
        o.append(breaker(x, 300, f"GCB{i}"))
        o.append(f'<circle cx="{x}" cy="{360}" r="24" class="gen"/>')
        o.append(t(x, 365, "G", "lbl", "middle", 15))
        o.append(t(x + 30, 365, f"Gen {i}", "lbl"))
        o.append(line(x, 384, x, 400, "pwr"))
        ip = {1: "105", 2: "106", 3: "113", 4: "114", 5: "115"}[i]
        o.append(box(x - 45, 400, 90, 40, ["IG4 200", f".{ip}"]))
        o.append(line(x, 440, x, 480, "comm"))
    o.append(t(80, 500, "5 sets, running nominal ≈ 1,050 kW each · Gen P Min 25 %", "lbl"))
    # ---- plant load and the two UPS --------------------------------------------------------------
    o.append(line(740, 250, 740, 300, "pwr"))
    o.append(box(660, 300, 160, 50, ["Plant load", "bottling lines, chillers"]))
    for i, x in enumerate((690, 790), start=1):
        o.append(line(x, 350, x, 360, "verify"))
        o.append(breaker(x, 378, f"UPS{i} CB", "right" if i == 2 else "left", cmd=True, cls="verify"))
        o.append(line(x, 398, x, 408, "verify"))
        o.append(box(x - 45, 408, 90, 40, [f"UPS {i}", "350 kVA"], "dev-v"))
    o.append(t(740, 470, "wiring and feedbacks not verified", "verify-t", "middle"))
    # ---- PV ----------------------------------------------------------------------------------------
    o.append(t(1190, 276, "Solar", "hdr", "end", 15))
    for i, (x, bx, names) in enumerate(((930, 870, "Inverters 1–5"), (1130, 1070, "Inverters 6–10")), start=1):
        o.append(line(x, 250, x, 320, "pwr"))
        o.append(breaker(x, 300, f"PV CB{i}", "left" if i == 1 else "right", cmd=True))
        o.append(line(x, 320, x, 340, "verify"))
        o.append(box(bx, 340, 120, 50, [names, "5 × SP-320K-H, 320 kW"]))
    o.append(t(1030, 335, "pairing to verify", "verify-t", "middle", 11))
    o.append(box(970, 450, 120, 50, ["SAU-100", "192.168.0.128"]))
    o.append(line(1000, 450, 930, 390, "comm"))
    o.append(line(1060, 450, 1130, 390, "comm"))
    o.append(t(1030, 425, "RS485", "comm-t", "middle", 11))
    o.append(t(1030, 528, "3.2 MW installed · max available 3,520 kW", "muted", "middle"))
    # ---- master ------------------------------------------------------------------------------------
    o.append(box(520, 540, 240, 60, ["InteliNeo 5500 · master", "192.168.0.112", "PLC: PV request, breaker commands"], size=12))
    o.append(poly([(760, 570), (1030, 570), (1030, 500)], "comm", ' marker-start="url(#a-comm)" marker-end="url(#a-comm)"'))
    o.append(t(780, 586, "Modbus TCP, 2 sessions, 1–2 s, timeout 3 s", "comm-t", size=11))
    o.append(t(780, 602, "reads PV power, writes setpoint 2100/2101", "comm-t", size=11))
    # ---- plant PC ----------------------------------------------------------------------------------
    o.append(box(880, 650, 300, 70, ["Plant PC · 192.168.0.200 · collector, read only",
                                     "hourly: master, 3 × IM210, SAU array · dashboard",
                                     "Modbus server for the CCI / Turkey platform"], size=12))
    o.append(poly([(880, 690), (640, 690), (640, 600)], "comm", ' marker-end="url(#a-comm)"'))
    o.append(t(760, 682, "Modbus TCP reads", "comm-t", "middle", 11))
    o.append(line(1070, 650, 1070, 500, "comm", ' marker-end="url(#a-comm)"'))
    o.append(t(1078, 630, "reads", "comm-t", size=11))
    o.append(line(1030, 720, 1030, 740, "comm"))
    o.append(t(1030, 756, "CCI Wi-Fi 172.17.76.x · internet · Turkey platform", "comm-t", "middle", 11))
    o.append(t(1050, 640, "IG4 1\u20135: Modbus server to confirm", "verify-t", "end", 11))
    # ---- legend ------------------------------------------------------------------------------------
    o.append(t(80, 660, "Legend", "hdr", size=15))
    o.append(line(80, 682, 130, 682, "pwr"));      o.append(t(140, 686, "power", "lbl"))
    o.append(line(80, 706, 130, 706, "comm"));     o.append(t(140, 710, "communication (CAN, Modbus TCP, RS485)", "lbl"))
    o.append(line(80, 730, 130, 730, "verify"));   o.append(t(140, 734, "not verified yet, to check on the next visit", "lbl"))
    o.append(t(80, 758, "cmd ← Neo PLC", "cmd", size=11)); o.append(t(190, 758, "breaker commanded by the master’s PLC program", "lbl"))
    o.append(breaker(420, 706, "", cls="pwr")); o.append(t(440, 710, "breaker (drawn closed)", "lbl"))
    return "\n".join(s for s in o if s)


DEFS = """<defs>
<marker id="a-comm" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path d="M0,0 L10,5 L0,10 z" class="comm-f"/></marker>
</defs>"""

# The same class names serve the page (tokens from the page CSS) and the standalone file (fixed colours).
STYLE_RULES = """
.d text {{ font-family: {font}; font-size: 13px; fill: {fg}; }}
.d .hdr {{ font-weight: 600; }}
.d .muted {{ fill: {muted}; font-size: 12px; }}
.d .pwr {{ stroke: {fg}; stroke-width: 1.6; fill: none; stroke-linecap: round; }}
.d .bus {{ stroke: {fg}; stroke-width: 5; stroke-linecap: round; }}
.d .dot {{ fill: {fg}; }}
.d .gen {{ stroke: {fg}; stroke-width: 1.6; fill: {panel}; }}
.d .dev {{ stroke: {fg}; stroke-width: 1.2; fill: {panel}; }}
.d .dev-v {{ stroke: {verify}; stroke-width: 1.2; stroke-dasharray: 5 4; fill: {panel}; }}
.d .comm {{ stroke: {comm}; stroke-width: 1.5; fill: none; stroke-dasharray: 6 4; }}
.d .comm-t {{ fill: {comm}; }}
.d .comm-f {{ fill: {comm}; }}
.d .verify {{ stroke: {verify}; stroke-width: 1.5; fill: none; stroke-dasharray: 5 4; }}
.d .verify-t {{ fill: {verify}; }}
.d .cmd {{ fill: {muted}; font-style: italic; }}
"""

LIGHT = dict(font="'IBM Plex Sans', 'Segoe UI', Arial, sans-serif", fg="#1d2229", muted="#5c6570",
             panel="#ffffff", comm="#1f5fbf", verify="#a86d00")


def svg(inner_style=None):
    style = f"<style>{inner_style}</style>\n" if inner_style else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" class="d" role="img" aria-label="{ARIA}">\n'
            f'{style}{DEFS}\n{body()}\n</svg>\n')


def standalone():
    return svg(STYLE_RULES.format(**LIGHT))


PAGE = """<title>Erbil Hybrid Plant</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&display=swap">
<style>
/* layout: one drawing sheet on a quiet paper ground, notes in a single column beneath it */
:root { --bg: #f3f4f2; --fg: #1d2229; --muted: #5c6570; --panel: #ffffff; --sheet: #fbfbfa; --rule: #d6d9d4; --comm: #1f5fbf; --verify: #a86d00; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg: #15181c; --fg: #e6e8e3; --muted: #9aa3ad; --panel: #1f2429; --sheet: #1a1e23; --rule: #343a41; --comm: #7fb0ff; --verify: #ffc457; color-scheme: dark; } }
:root[data-theme="dark"] { --bg: #15181c; --fg: #e6e8e3; --muted: #9aa3ad; --panel: #1f2429; --sheet: #1a1e23; --rule: #343a41; --comm: #7fb0ff; --verify: #ffc457; color-scheme: dark; }
body { background: var(--bg); color: var(--fg); font-family: 'IBM Plex Sans', 'Segoe UI', Arial, sans-serif; font-size: 15px; line-height: 1.5;
       padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 28px 48px; }
main { max-width: 1240px; margin: 0 auto; }
h1 { font-size: 22px; font-weight: 600; margin: 0 0 4px; text-wrap: balance; }
.sub { color: var(--muted); margin: 0 0 20px; }
figure { margin: 0; background: var(--sheet); border: 1px solid var(--rule); border-radius: 4px; overflow-x: auto; }
figure svg { display: block; min-width: 1000px; width: 100%; height: auto; }
figcaption { padding: 12px 16px; border-top: 1px solid var(--rule); color: var(--muted); font-size: 14px; }
.notes { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 24px; margin-top: 24px; }
.notes h2 { font-size: 16px; font-weight: 600; margin: 0 0 8px; }
.notes ul { margin: 0; padding-left: 20px; }
.notes li { margin: 4px 0; }
.v h2 { color: var(--verify); }
__STYLE__
</style>
<main>
<h1>Erbil hybrid plant</h1>
<p class="sub">As known on 15 October 2026 from the master's archive, its history and the site visit of 14 October.</p>
<figure>
__SVG__
<figcaption>Three grid incomers, five gensets and ten inverters on one plant bus. The master runs the gensets and mains over CAN and sends the solar its power setpoint over Modbus TCP; the plant PC only reads.</figcaption>
</figure>
<div class="notes">
<section>
<h2>Established</h2>
<ul>
<li>Controller addresses, Modbus unit ids and the master's PLC program: from the archive of 14 October.</li>
<li>Running nominal 3,150 kW with three sets, 4,150 kW with four; Gen P Min 25 %: from the history and setpoints.</li>
<li>The PV setpoint path: the PLC's request goes through two Modbus sessions to the SAU; any comm fail on either session forces it to 0.</li>
<li>Export cap 1,300 kW per closed mains feeder; PV CB1/CB2 and the UPS breakers are commanded by the PLC.</li>
</ul>
</section>
<section class="v">
<h2>To verify on the next visit</h2>
<ul>
<li>Where the two UPS are wired and what they feed; whether their feedbacks reach the master.</li>
<li>Which inverter group sits behind PV CB1 and which behind PV CB2.</li>
<li>Each genset's nominal rating and its reverse-power settings (IG4 archives).</li>
<li>Whether the five IG4 have their Modbus server on, so the collector can read them.</li>
<li>The SAU's point table (registers 2100 and 2101), its ramp and watchdog settings, and where its meters M1 and M3 sit.</li>
<li>Why the gensets run by hand since 1 October.</li>
</ul>
</section>
</div>
</main>
"""


def page():
    return PAGE.replace("__STYLE__", STYLE_RULES.format(font="inherit", fg="var(--fg)", muted="var(--muted)",
                                                        panel="var(--panel)", comm="var(--comm)", verify="var(--verify)")
                        ).replace("__SVG__", svg())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "plant_overview.svg"))
    ap.add_argument("--html", default=None, help="also write the drawing inside a small page")
    a = ap.parse_args()
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(standalone())
    print("wrote", a.out)
    if a.html:
        with open(a.html, "w", encoding="utf-8") as f:
            f.write(page())
        print("wrote", a.html)


if __name__ == "__main__":
    main()
