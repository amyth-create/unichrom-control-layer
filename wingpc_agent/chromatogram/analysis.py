"""Baseline, smoothing, peak detection and run-state heuristics (numpy only).

Deliberately simple and transparent: the LLM gets the numbers *and* the
parameters used, so it can reason about them instead of trusting a black box.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np


@dataclass
class Peak:
    index: int
    x: float
    height: float          # above baseline
    area: float            # trapezoid above baseline between bounds
    start_x: float
    end_x: float
    width_half: float      # FWHM in x units (approx)
    prominence: float

    def to_dict(self) -> dict[str, Any]:
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def smooth(y: np.ndarray, window: int = 5) -> np.ndarray:
    if window <= 1 or y.size < window:
        return y.astype(float)
    k = np.ones(window) / window
    pad = window // 2
    yp = np.pad(y.astype(float), (pad, pad), mode="edge")
    return np.convolve(yp, k, mode="valid")[: y.size]


def rolling_min_baseline(y: np.ndarray, window: int) -> np.ndarray:
    """Morphological-opening-style baseline: rolling min then rolling mean."""
    n = y.size
    if n == 0:
        return y
    window = max(3, min(window, n))
    pad = window // 2
    yp = np.pad(y, (pad, pad), mode="edge")
    # rolling minimum via stride trick
    shape = (n, window)
    strides = (yp.strides[0], yp.strides[0])
    win = np.lib.stride_tricks.as_strided(yp[: n + window - 1], shape=shape, strides=strides)
    mn = win.min(axis=1)
    return smooth(mn, window)


def find_peaks(x: np.ndarray, y: np.ndarray, *, min_prominence: float | None = None,
               min_distance: int = 3, smooth_window: int = 5, baseline_window: int | None = None,
               max_peaks: int = 50) -> tuple[list[Peak], dict[str, Any]]:
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    n = y.size
    if n < 5:
        return [], {"reason": "too few points"}
    ys = smooth(y, smooth_window)
    bw = baseline_window or max(11, n // 10)
    base = rolling_min_baseline(ys, bw)
    sig = ys - base
    # noise sigma of the RAW trace (diff of iid noise has sigma*sqrt(2))
    noise = _mad(np.diff(y)) * 1.4826 / np.sqrt(2) if n > 10 else 0.0
    rng = float(sig.max() - sig.min())
    prom_thr = min_prominence if min_prominence is not None else max(5 * noise, 0.02 * (rng + 1e-12))

    # local maxima
    idx = np.where((sig[1:-1] > sig[:-2]) & (sig[1:-1] >= sig[2:]))[0] + 1
    peaks: list[Peak] = []
    for i in idx:
        # prominence: height above the higher of the two surrounding minima
        l = i
        while l > 0 and sig[l - 1] <= sig[i]:
            l -= 1
        r = i
        while r < n - 1 and sig[r + 1] <= sig[i]:
            r += 1
        left_min = sig[l:i + 1].min()
        right_min = sig[i:r + 1].min()
        prom = sig[i] - max(left_min, right_min)
        if prom < prom_thr:
            continue
        half = sig[i] - prom / 2
        li = i
        while li > l and sig[li] > half:
            li -= 1
        ri = i
        while ri < r and sig[ri] > half:
            ri += 1
        # integration bounds: walk down to the baseline-ish level
        lb = i
        while lb > 0 and sig[lb - 1] < sig[lb] and sig[lb - 1] > 0.02 * sig[i]:
            lb -= 1
        rb = i
        while rb < n - 1 and sig[rb + 1] < sig[rb] and sig[rb + 1] > 0.02 * sig[i]:
            rb += 1
        area = float(np.trapezoid(sig[lb:rb + 1], x[lb:rb + 1])) if rb > lb else 0.0
        peaks.append(Peak(int(i), float(x[i]), float(sig[i]), area, float(x[lb]), float(x[rb]),
                          float(abs(x[ri] - x[li])), float(prom)))
    # enforce min distance, keep most prominent
    peaks.sort(key=lambda p: -p.prominence)
    kept: list[Peak] = []
    for p in peaks:
        if all(abs(p.index - q.index) >= min_distance for q in kept):
            kept.append(p)
        if len(kept) >= max_peaks:
            break
    kept.sort(key=lambda p: p.index)
    params = {
        "smooth_window": smooth_window, "baseline_window": bw, "noise_sigma": float(noise),
        "prominence_threshold": float(prom_thr), "n_points": int(n),
    }
    return kept, params


def _mad(a: np.ndarray) -> float:
    if a.size == 0:
        return 0.0
    med = np.median(a)
    return float(np.median(np.abs(a - med)))


def describe(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    """Compact run-state summary for the LLM: level, drift, noise, last values."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if y.size == 0:
        return {"n": 0}
    tail = y[-max(5, y.size // 20):]
    head = y[: max(5, y.size // 20)]
    d: dict[str, Any] = {
        "n": int(y.size),
        "x_first": float(x[0]), "x_last": float(x[-1]),
        "y_min": float(y.min()), "y_max": float(y.max()),
        "y_last": float(y[-1]),
        "y_tail_mean": float(tail.mean()), "y_tail_std": float(tail.std()),
        "drift_tail_minus_head": float(tail.mean() - head.mean()),
    }
    if y.size > 10:
        d["noise_sigma"] = float(_mad(np.diff(y)) * 1.4826)
        slope = np.polyfit(x[-tail.size:], tail, 1)[0] if tail.size > 2 and x[-1] != x[-tail.size] else 0.0
        d["tail_slope_per_x"] = float(slope)
    return d


def downsample(x: np.ndarray, y: np.ndarray, max_points: int = 400) -> tuple[list[float], list[float]]:
    """Min/max-preserving bucket downsample so peaks survive for the LLM."""
    n = len(x)
    if n <= max_points:
        return [float(v) for v in x], [float(v) for v in y]
    buckets = max_points // 2
    edges = np.linspace(0, n, buckets + 1).astype(int)
    xs: list[float] = []
    ys: list[float] = []
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        seg = y[a:b]
        i_min, i_max = int(np.argmin(seg)), int(np.argmax(seg))
        for i in sorted({i_min, i_max}):
            xs.append(float(x[a + i]))
            ys.append(float(y[a + i]))
    return xs, ys
