"""Turn a screenshot of WinGPC's live Raw Data plot into a numeric trace.

Pipeline
  1. find the plot area: largest light rectangle bounded by dark axis lines
     (or use a caller-supplied inner rectangle)
  2. build a mask of trace pixels: either a configured RGB colour (+tolerance)
     or the dominant saturated colour inside the plot area
  3. for every pixel column take the median trace row -> (col, row)
  4. map pixels to data units with an AxisCalibration (x_min..x_max,
     y_min..y_max). Without calibration we return normalised 0..1 units and
     say so.

This is deliberately robust rather than clever: grid lines are grey (low
saturation) and get rejected; a cursor or legend of the same colour as the
trace can add outliers, which the column-median + continuity filter removes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image


@dataclass
class AxisCalibration:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    x_unit: str = "min"
    y_unit: str = "signal"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class PlotArea:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass
class Trace:
    x: np.ndarray
    y: np.ndarray
    area: PlotArea
    calibrated: bool
    trace_rgb: tuple[int, int, int]
    coverage: float            # fraction of plot columns with a trace pixel
    units: tuple[str, str]

    def to_dict(self, max_points: int = 400) -> dict[str, Any]:
        from .analysis import downsample
        xs, ys = downsample(self.x, self.y, max_points)
        return {
            "n": int(self.x.size),
            "calibrated": self.calibrated,
            "units": list(self.units),
            "x": xs, "y": ys,
            "plot_area_px": [self.area.left, self.area.top, self.area.right, self.area.bottom],
            "trace_rgb": list(self.trace_rgb),
            "coverage": round(self.coverage, 3),
        }


# --------------------------------------------------------------------------- 1
def find_plot_area(img: np.ndarray, min_frac: float = 0.3) -> PlotArea:
    """Locate the drawing area. Strategy: rows/cols that are mostly 'paper'
    (bright, low saturation) form the plot; axis lines are long dark runs.
    Fallback: whole image with a small margin."""
    h, w, _ = img.shape
    gray = img.mean(axis=2)
    paper = _paper_mask(img)
    dark = gray < 90

    # Preferred: bounding box of rows/cols that are mostly paper. Axis lines,
    # tick labels and the window chrome are excluded automatically; the trace
    # itself only removes a few pixels per column.
    col_frac = paper.mean(axis=0)
    row_frac = paper.mean(axis=1)
    cols = np.where(col_frac > 0.5)[0]
    rows = np.where(row_frac > 0.5)[0]
    if cols.size > w * min_frac and rows.size > h * min_frac:
        # take the largest contiguous run (ignore a stray paper-coloured legend)
        c0, c1 = _largest_run(cols)
        r0, r1 = _largest_run(rows)
        if (c1 - c0) > w * min_frac and (r1 - r0) > h * min_frac:
            return PlotArea(int(c0), int(r0), int(c1), int(r1))

    # long dark horizontal / vertical lines = axes or frame
    row_dark = dark.mean(axis=1)
    col_dark = dark.mean(axis=0)
    h_lines = np.where(row_dark > 0.5)[0]
    v_lines = np.where(col_dark > 0.5)[0]

    top, bottom, left, right = 0, h - 1, 0, w - 1
    if h_lines.size:
        # bottom axis = lowest long line that still has paper above it
        below = h_lines[h_lines > h * 0.4]
        above = h_lines[h_lines < h * 0.6]
        if below.size:
            bottom = int(below.min()) - 1
        if above.size and paper[above.max() + 1: above.max() + 10].mean() > 0.5:
            top = int(above.max()) + 1
    if v_lines.size:
        leftc = v_lines[v_lines < w * 0.6]
        rightc = v_lines[v_lines > w * 0.4]
        if leftc.size:
            left = int(leftc.max()) + 1
        if rightc.size and rightc.min() > left + w * 0.3:
            right = int(rightc.min()) - 1

    area = PlotArea(left, top, right, bottom)
    if area.width < w * min_frac or area.height < h * min_frac:
        # no usable frame found -> use paper bounding box
        ys, xs = np.where(paper)
        if xs.size > 0.05 * paper.size:
            area = PlotArea(int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        else:
            m = max(2, min(h, w) // 40)
            area = PlotArea(m, m, w - m, h - m)
    return area


def _paper_mask(img: np.ndarray, tol: int = 10) -> np.ndarray:
    """Pixels close to the dominant colour of the central 50 % of the image
    (the plot background), so a grey window background (240) is told apart
    from white paper (255) and from grey grid lines (~200)."""
    h, w, _ = img.shape
    centre = img[h // 4: 3 * h // 4, w // 4: 3 * w // 4].reshape(-1, 3).astype(int)
    q = (centre // 4)
    keys = q[:, 0] * 4096 + q[:, 1] * 64 + q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    k = vals[np.argmax(counts)]
    mode = np.median(centre[keys == k], axis=0)
    return np.abs(img.astype(int) - mode.astype(int)).max(axis=2) <= tol


def _largest_run(idx: np.ndarray, max_gap: int = 6) -> tuple[int, int]:
    """Largest contiguous run of indices, bridging gaps <= max_gap pixels
    (grid lines, cursor, legend borders interrupt the paper by 1-3 px)."""
    if idx.size == 0:
        return 0, 0
    breaks = np.where(np.diff(idx) > max_gap)[0]
    starts = np.r_[0, breaks + 1]
    ends = np.r_[breaks, idx.size - 1]
    j = int(np.argmax(idx[ends] - idx[starts]))
    return int(idx[starts[j]]), int(idx[ends[j]])


# --------------------------------------------------------------------------- 2
def dominant_trace_color(img: np.ndarray, area: PlotArea, min_sat: int = 60) -> tuple[int, int, int] | None:
    sub = img[area.top:area.bottom, area.left:area.right].reshape(-1, 3).astype(int)
    # (int cast above: uint8 arithmetic would overflow in the quantisation below)
    sat = sub.max(axis=1) - sub.min(axis=1)
    cand = sub[sat >= min_sat]
    if cand.size == 0:
        # monochrome trace (black on white) - pick dark pixels
        gray = sub.mean(axis=1)
        cand = sub[gray < 80]
        if cand.size == 0:
            return None
        return (0, 0, 0)
    # quantise to 32 levels and take the mode
    q = (cand // 32)
    keys = q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    k = vals[np.argmax(counts)]
    sel = cand[keys == k]
    return tuple(int(v) for v in np.median(sel, axis=0))


def trace_mask(img: np.ndarray, rgb: tuple[int, int, int], tol: int) -> np.ndarray:
    d = np.abs(img.astype(int) - np.array(rgb, int)).max(axis=2)
    m = d <= tol
    if rgb == (0, 0, 0):
        # monochrome: dark AND not grey grid (grid is lighter)
        gray = img.mean(axis=2)
        m = gray < 80
    return m


# --------------------------------------------------------------------------- 3/4
def extract_trace(image: Image.Image | np.ndarray, *, calibration: AxisCalibration | None = None,
                  area: PlotArea | None = None, trace_rgb: tuple[int, int, int] | None = None,
                  tolerance: int = 60, max_jump_frac: float = 0.35) -> Trace:
    img = np.asarray(image.convert("RGB") if isinstance(image, Image.Image) else image)
    area = area or find_plot_area(img)
    rgb = tuple(trace_rgb) if trace_rgb else dominant_trace_color(img, area)
    if rgb is None:
        empty = np.array([], float)
        return Trace(empty, empty, area, calibration is not None, (0, 0, 0), 0.0,
                     (calibration.x_unit, calibration.y_unit) if calibration else ("frac", "frac"))
    mask = trace_mask(img, rgb, tolerance)[area.top:area.bottom, area.left:area.right]
    cols: list[int] = []
    rows: list[float] = []
    for c in range(mask.shape[1]):
        r = np.where(mask[:, c])[0]
        if r.size:
            cols.append(c)
            rows.append(float(np.median(r)))
    if not cols:
        empty = np.array([], float)
        return Trace(empty, empty, area, calibration is not None, rgb, 0.0,
                     (calibration.x_unit, calibration.y_unit) if calibration else ("frac", "frac"))
    cols_a = np.array(cols, float)
    rows_a = np.array(rows, float)
    # continuity filter: drop isolated pixels that jump > max_jump_frac of height
    if rows_a.size > 3:
        med = np.median(np.stack([np.roll(rows_a, 1), rows_a, np.roll(rows_a, -1)]), axis=0)
        bad = np.abs(rows_a - med) > max_jump_frac * area.height
        bad[0] = bad[-1] = False
        cols_a, rows_a = cols_a[~bad], rows_a[~bad]
    # pixel -> fraction (y up)
    xf = cols_a / max(1, area.width - 1)
    yf = 1.0 - rows_a / max(1, area.height - 1)
    coverage = len(cols) / max(1, mask.shape[1])
    if calibration:
        x = calibration.x_min + xf * (calibration.x_max - calibration.x_min)
        y = calibration.y_min + yf * (calibration.y_max - calibration.y_min)
        return Trace(x, y, area, True, rgb, coverage, (calibration.x_unit, calibration.y_unit))
    return Trace(xf, yf, area, False, rgb, coverage, ("frac", "frac"))
