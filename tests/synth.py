"""Synthetic chromatogram + rendered 'WinGPC-like' plot for tests (no matplotlib)."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw


def gaussian_chrom(n=2000, x_max=30.0, peaks=((9.0, 100.0, 0.35), (12.5, 60.0, 0.5), (18.0, 25.0, 0.8)),
                   baseline=5.0, drift=0.2, noise=0.3, seed=0):
    rng = np.random.default_rng(seed)
    x = np.linspace(0, x_max, n)
    y = baseline + drift * x + rng.normal(0, noise, n)
    for mu, h, sig in peaks:
        y += h * np.exp(-0.5 * ((x - mu) / sig) ** 2)
    return x, y


def render_plot(x, y, size=(900, 500), margin=(70, 30, 30, 50), x_range=None, y_range=None,
                trace_rgb=(0, 0, 255), grid=True, bg=(255, 255, 255)):
    """Render like a typical Win32 chart: white paper, black axes, grey grid, coloured trace."""
    W, H = size
    ml, mt, mr, mb = margin
    img = Image.new("RGB", size, (240, 240, 240))
    d = ImageDraw.Draw(img)
    left, top, right, bottom = ml, mt, W - mr, H - mb
    d.rectangle([left, top, right, bottom], fill=bg)
    x0, x1 = x_range or (float(x.min()), float(x.max()))
    y0, y1 = y_range or (float(y.min()) - 5, float(y.max()) + 5)
    if grid:
        for i in range(1, 6):
            gx = left + (right - left) * i / 6
            gy = top + (bottom - top) * i / 6
            d.line([gx, top, gx, bottom], fill=(200, 200, 200))
            d.line([left, gy, right, gy], fill=(200, 200, 200))
    # axes (2px black)
    d.line([left, bottom, right, bottom], fill=(0, 0, 0), width=2)
    d.line([left, top, left, bottom], fill=(0, 0, 0), width=2)
    pts = []
    for xi, yi in zip(x, y):
        px = left + (xi - x0) / (x1 - x0) * (right - left)
        py = bottom - (yi - y0) / (y1 - y0) * (bottom - top)
        pts.append((px, py))
    d.line(pts, fill=trace_rgb, width=1)
    # tick labels (text is dark but short -> should not confuse the extractor)
    for i in range(0, 7):
        xv = x0 + (x1 - x0) * i / 6
        d.text((left + (right - left) * i / 6 - 8, bottom + 6), f"{xv:.0f}", fill=(0, 0, 0))
    return img, (x0, x1, y0, y1), (left, top, right, bottom)
