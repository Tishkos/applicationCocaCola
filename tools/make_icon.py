#!/usr/bin/env python3
"""Every size of the Plant Collector icon, derived from one artwork: app/PlantCollector-master.png
(1024 px, transparent outside the rounded square). Red rounded square, the letters C C with a
lightning bolt as the I, a gauge with its needle on top - "CCI" read as power, under a meter.

    python tools/make_icon.py                   writes app/PlantCollector.ico (256 … 16 px), app/PlantCollector.png (256),
                                                plantcollector/web/static/app-icon.png (512, the dashboard's logo) and
                                                plantcollector/web/static/favicon.png (64, the browser-tab icon)
    python tools/make_icon.py --cut render.png  makes app/PlantCollector-master.png from a render of the logo on a
                                                white background (finds the square, fits the corner radius, cuts it
                                                out with a clean anti-aliased edge); then derives the sizes as above
"""
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = os.path.join(ROOT, "app", "PlantCollector-master.png")
OUT_ICO = os.path.join(ROOT, "app", "PlantCollector.ico")
OUT_PNG = os.path.join(ROOT, "app", "PlantCollector.png")
STATIC = os.path.join(ROOT, "plantcollector", "web", "static")
OUT_WEB = os.path.join(STATIC, "app-icon.png")
OUT_FAV = os.path.join(STATIC, "favicon.png")
ICO_SIZES = (256, 128, 64, 48, 32, 24, 16)


def cut_out(render_path, size=1024, inset_px=2):
    """The logo on white -> transparent RGBA master. The square is found as the red area, its corner
    radius fitted from the edge, and the mask drawn `inset_px` inside the painted edge so the thin
    white rim of the render stays out."""
    im = Image.open(render_path).convert("RGBA")
    a = np.array(im).astype(int)
    red = (a[..., 0] - a[..., 1]) > 40
    ys, xs = np.where(red)
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max(), ys.max()
    # corner radius: the left edge x at a few rows below the top follows a circle of radius R
    est = []
    for dy in (20, 40, 60, 90, 120):
        left = np.where(red[y0 + dy])[0].min() - x0
        b = left + dy
        est.append(b + math.sqrt(max(b * b - (left * left + dy * dy), 0)))
    radius = int(round(float(np.median(est))))
    side = max(x1 - x0 + 1, y1 - y0 + 1)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    bx, by = int(round(cx - side / 2)), int(round(cy - side / 2))
    crop = im.crop((bx, by, bx + side, by + side))
    S = 4
    mask = Image.new("L", (side * S, side * S), 0)
    ox, oy = (x0 - bx) * S, (y0 - by) * S
    ImageDraw.Draw(mask).rounded_rectangle(
        [ox + inset_px * S, oy + inset_px * S, ox + (x1 - x0 + 1 - inset_px) * S - 1, oy + (y1 - y0 + 1 - inset_px) * S - 1],
        radius=radius * S, fill=255)
    rgba = np.array(crop)
    rgba[..., 3] = np.array(mask.resize((side, side), Image.LANCZOS))
    out = Image.fromarray(rgba, "RGBA").resize((size, size), Image.LANCZOS)
    out.save(MASTER, optimize=True)
    print("master written:", MASTER, "- square of", side, "px in the render, corner radius", radius, "px")


def derive():
    master = Image.open(MASTER).convert("RGBA")
    frames = [master.resize((n, n), Image.LANCZOS) for n in ICO_SIZES]
    frames[0].save(OUT_ICO, format="ICO", sizes=[f.size for f in frames], append_images=frames[1:])
    frames[0].save(OUT_PNG, optimize=True)
    os.makedirs(STATIC, exist_ok=True)
    master.resize((512, 512), Image.LANCZOS).save(OUT_WEB, optimize=True)
    master.resize((64, 64), Image.LANCZOS).save(OUT_FAV, optimize=True)
    print("written", OUT_ICO, list(ICO_SIZES), OUT_PNG, OUT_WEB, OUT_FAV)


def main(argv):
    if len(argv) >= 2 and argv[0] == "--cut":
        cut_out(argv[1])
    elif not os.path.exists(MASTER):
        sys.exit("no artwork: %s (make it with --cut <render.png>)" % MASTER)
    derive()


if __name__ == "__main__":
    main(sys.argv[1:])
