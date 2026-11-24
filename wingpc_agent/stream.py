"""Real-time monitor: screen -> trace -> ring buffer -> events.

Runs in a background thread inside the MCP server. Each tick:
  * capture the plot rectangle (UIA control rect if resolvable, else config rect)
  * extract the trace
  * store a Frame (timestamp, trace, image hash)
  * compute quick deltas vs the previous frame (new points, max change, peaks)
Observers (the LLM via tools, or the file watcher) read the latest frame
without blocking the loop.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
from PIL import Image

from .chromatogram.analysis import describe, find_peaks
from .chromatogram.plot_extract import AxisCalibration, PlotArea, Trace, extract_trace
from .config import Settings


@dataclass
class Frame:
    t: float
    trace: Trace
    img_hash: str
    changed: bool
    image: Image.Image | None = None     # kept only for the latest frame
    note: str | None = None


@dataclass
class MonitorStats:
    ticks: int = 0
    errors: int = 0
    last_error: str | None = None
    last_tick_ms: float = 0.0
    started_at: float | None = None
    last_change_at: float | None = None
    source: str = "screen"


class LiveMonitor:
    def __init__(self, settings: Settings, grab: Callable[[list[int] | None], Image.Image],
                 rect_resolver: Callable[[], list[int] | None] | None = None):
        self.s = settings
        self.grab = grab
        self.rect_resolver = rect_resolver
        self.frames: deque[Frame] = deque(maxlen=max(10, int(settings.ring_seconds * settings.poll_hz)))
        self.stats = MonitorStats()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=500)
        self._last_hash = ""

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.stats = MonitorStats(started_at=time.time())
        self._thread = threading.Thread(target=self._loop, name="wingpc-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    # ------------------------------------------------------------- settings
    def calibration(self) -> AxisCalibration | None:
        p = self.s.plot
        if None in (p.x_min, p.x_max, p.y_min, p.y_max):
            return None
        return AxisCalibration(p.x_min, p.x_max, p.y_min, p.y_max, p.x_unit, p.y_unit)

    def set_axes(self, x_min: float, x_max: float, y_min: float, y_max: float,
                 x_unit: str | None = None, y_unit: str | None = None) -> None:
        p = self.s.plot
        p.x_min, p.x_max, p.y_min, p.y_max = x_min, x_max, y_min, y_max
        if x_unit:
            p.x_unit = x_unit
        if y_unit:
            p.y_unit = y_unit
        self._events.append({"t": time.time(), "type": "axes_set", "cal": self.calibration().to_dict()})

    # ------------------------------------------------------------- loop
    def _rect(self) -> list[int] | None:
        if self.rect_resolver:
            try:
                r = self.rect_resolver()
                if r and r[2] - r[0] > 50 and r[3] - r[1] > 50:
                    return r
            except Exception:
                pass
        return self.s.plot.rect

    def tick(self) -> Frame:
        t0 = time.time()
        rect = self._rect()
        img = self.grab(rect)
        h = hashlib.blake2b(img.tobytes(), digest_size=8).hexdigest()
        changed = h != self._last_hash
        self._last_hash = h
        p = self.s.plot
        trace = extract_trace(img, calibration=self.calibration(),
                              trace_rgb=tuple(p.trace_rgb) if p.trace_rgb else None,
                              tolerance=p.color_tolerance)
        fr = Frame(t0, trace, h, changed, image=img)
        with self._lock:
            if self.frames:
                self.frames[-1].image = None   # keep memory bounded
            self.frames.append(fr)
            self.stats.ticks += 1
            self.stats.last_tick_ms = (time.time() - t0) * 1000
            if changed:
                self.stats.last_change_at = t0
        return fr

    def _loop(self) -> None:
        period = 1.0 / max(0.1, self.s.poll_hz)
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self.tick()
            except Exception as e:   # keep the loop alive; report via stats
                with self._lock:
                    self.stats.errors += 1
                    self.stats.last_error = repr(e)
            dt = time.time() - t0
            self._stop.wait(max(0.05, period - dt))

    # ------------------------------------------------------------- readers
    def latest(self) -> Frame | None:
        with self._lock:
            return self.frames[-1] if self.frames else None

    def snapshot(self, max_points: int = 400, with_peaks: bool = True) -> dict[str, Any]:
        fr = self.latest()
        if fr is None:
            return {"running": self.running, "frame": None, "stats": self.stats.__dict__}
        tr = fr.trace
        out: dict[str, Any] = {
            "running": self.running,
            "t": fr.t, "age_s": round(time.time() - fr.t, 2),
            "changed_since_previous": fr.changed,
            "trace": tr.to_dict(max_points),
            "stats": self.stats.__dict__,
        }
        if tr.x.size:
            out["describe"] = describe(tr.x, tr.y)
            if with_peaks:
                peaks, params = find_peaks(tr.x, tr.y)
                out["peaks"] = [p.to_dict() for p in peaks]
                out["peak_params"] = params
        if not tr.calibrated:
            out["note"] = ("Axes are NOT calibrated: x and y are fractions of the plot area (0..1). "
                           "Read the axis labels from a screenshot and call chrom_set_axes.")
        return out

    def history(self, seconds: float = 60.0, stride: int = 1) -> list[dict[str, Any]]:
        cutoff = time.time() - seconds
        with self._lock:
            rows = [f for f in self.frames if f.t >= cutoff]
        out = []
        for f in rows[::max(1, stride)]:
            d = {"t": f.t, "changed": f.changed, "n": int(f.trace.x.size)}
            if f.trace.y.size:
                d.update({"y_last": float(f.trace.y[-1]), "y_max": float(f.trace.y.max()),
                          "x_last": float(f.trace.x[-1])})
            out.append(d)
        return out

    def events(self, n: int = 50) -> list[dict[str, Any]]:
        return list(self._events)[-n:]
