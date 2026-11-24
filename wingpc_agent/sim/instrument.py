"""A headless simulated gradient LC behind WinGPC's seams.

It stands where WinGPC + ChromPilot + the LC stack stand in production and
answers the same four questions the bridge asks of them:

    apply_method(method)      the pump timetable and oven setpoint
    start() / stop()          the sequence
    status() / pressure_bar() what ChromPilot's Instrument Manager shows
    snapshot()                what the live Raw Data plot shows (x, y so far)
    export_ascii(path)        what Data Editor -> ASCII export writes

Chemistry is a linear-solvent-strength model: for each component
log k = log k_w - S * phi, with phi the organic fraction at the column inlet
(pump timetable delayed by the dwell time). Retention is found by integrating
the migration through the column, so a steeper or hotter gradient really does
move and merge peaks, and Gradient Bench LLM's proposals have consequences.

Pressure follows the water/acetonitrile viscosity curve (maximum around 20-30
% B, acetonitrile about half as viscous as water) scaled by flow and column
back-pressure and reduced by temperature, with a little noise, so the bridge's
pressure monitor has something real to watch.

Time runs `accel` simulated minutes per real second (default 4: a 20 min run
takes 5 s). Set accel=1/60 for real time.
"""
from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# (name, log10 k_w, S, relative response). A seven-component mixture with two close pairs.
DEFAULT_SAMPLE = [
    ("uracil (t0 marker)", -0.5, 1.0, 0.25),
    ("A", 1.30, 3.2, 1.0),
    ("B", 1.55, 3.4, 0.8),
    ("C", 1.70, 3.75, 0.6),     # B/C: co-elute near 43 %B at the column -> steep gradients fuse them
    ("D", 2.30, 4.0, 0.9),
    ("E", 2.45, 4.35, 0.7),     # D/E: same, the pair the campaign should learn to pull apart
    ("F", 3.10, 4.8, 0.5),
]


@dataclass
class SimConfig:
    accel: float = 4.0                 # simulated minutes per real second
    sample_hz: float = 5.0             # detector sampling rate (simulated)
    t0_min: float = 1.0                # column dead time at 1 mL/min
    dwell_min: float = 0.5             # gradient delay pump -> column
    flow_ml_min: float = 1.0
    post_time_min: float = 2.0         # re-equilibration counted in the acquisition
    plates: float = 1500.0
    noise_mau: float = 0.4
    drift_mau_per_min: float = 0.05
    p0_bar: float = 180.0              # back-pressure in water at 30 C, 1 mL/min
    pressure_noise_bar: float = 1.0
    pressure_spike: tuple[float, float, float] | None = None   # (t_from_min, t_to_min, extra_bar) e.g. a blockage
    seed: int = 0
    sample: list[tuple[str, float, float, float]] = field(default_factory=lambda: list(DEFAULT_SAMPLE))


class SimInstrument:
    def __init__(self, cfg: SimConfig | None = None):
        self.cfg = cfg or SimConfig()
        self.rng = random.Random(self.cfg.seed)
        self.method: dict[str, Any] | None = None
        self._state = "Idle"
        self._t = 0.0                      # simulated minutes since injection
        self._x: list[float] = []
        self._y: list[float] = []
        self._p: list[float] = []
        self._peaks: list[tuple[str, float, float, float]] = []   # name, tR, sigma, height
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.runs_done = 0
        self.log: list[str] = []

    # ------------------------------------------------------------ method
    def apply_method(self, method: dict[str, Any]) -> None:
        g = sorted(({"time_min": float(n["time_min"]), "pct_b": float(n["pct_b"])} for n in method["gradient"]),
                   key=lambda n: n["time_min"])
        self.method = {"gradient": g, "temperature_c": float(method["temperature_c"])}
        self.log.append(f"method set: {g} @ {self.method['temperature_c']} C")

    def phi_pump(self, t: float) -> float:
        """Organic fraction at the pump at simulated minute t (holds the last node)."""
        g = self.method["gradient"]
        if t <= g[0]["time_min"]:
            return g[0]["pct_b"] / 100
        for a, b in zip(g, g[1:]):
            if t <= b["time_min"]:
                f = (t - a["time_min"]) / max(1e-9, b["time_min"] - a["time_min"])
                return (a["pct_b"] + f * (b["pct_b"] - a["pct_b"])) / 100
        return g[-1]["pct_b"] / 100

    def duration_min(self) -> float:
        return self.method["gradient"][-1]["time_min"] + self.cfg.post_time_min

    # ------------------------------------------------------------ chemistry
    def _retention(self) -> list[tuple[str, float, float, float]]:
        c = self.cfg
        T = self.method["temperature_c"]
        t0 = c.t0_min / c.flow_ml_min
        temp_factor = math.exp(-0.025 * (T - 30.0))           # hotter -> smaller k
        out = []
        dt = 0.005
        for name, logkw, S, resp in c.sample:
            x, t = 0.0, 0.0
            while x < 1.0 and t < 500:
                phi = self.phi_pump(max(0.0, t - c.dwell_min))
                k = temp_factor * 10 ** (logkw - S * phi)
                x += dt / (t0 * (1 + k))
                t += dt
            k_el = temp_factor * 10 ** (logkw - S * self.phi_pump(max(0.0, t - c.dwell_min)))
            # gradient compression: width set by k at elution, narrower for steep gradients
            sigma = max(0.02, t0 * (1 + min(k_el, 3.0)) / math.sqrt(c.plates)) * 1.6
            height = 100.0 * resp / (sigma * 25)
            out.append((name, t, sigma, height))
        return out

    def _signal(self, t: float) -> float:
        y = 2.0 + self.cfg.drift_mau_per_min * t + self.rng.gauss(0, self.cfg.noise_mau)
        y += 6.0 * math.exp(-0.5 * ((t - 0.55) / 0.06) ** 2)   # injection disturbance
        for _, tr, sig, h in self._peaks:
            y += h * math.exp(-0.5 * ((t - tr) / sig) ** 2)
        return y

    def _pressure(self, t: float) -> float:
        c = self.cfg
        phi = self.phi_pump(t)
        rel = 1.0 - 0.55 * phi + 1.3 * phi * (1 - phi)       # water/MeCN relative viscosity
        temp = math.exp(-0.02 * (self.method["temperature_c"] - 30.0))
        p = c.p0_bar * c.flow_ml_min * rel * temp
        if c.pressure_spike and c.pressure_spike[0] <= t <= c.pressure_spike[1]:
            p += c.pressure_spike[2]
        return p + self.rng.gauss(0, c.pressure_noise_bar)

    # ------------------------------------------------------------ run control
    def start(self) -> None:
        if self.method is None:
            raise RuntimeError("no method loaded")
        if self._thread and self._thread.is_alive():
            raise RuntimeError("a run is already in progress")
        self._peaks = self._retention()
        with self._lock:
            self._t, self._x, self._y, self._p = 0.0, [], [], []
            self._state = "Running"
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="sim-lc", daemon=True)
        self._thread.start()
        self.log.append("run started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:
            self._state = "Stopped"
        self.log.append("run stopped by operator")

    def _loop(self) -> None:
        """Simulated time follows the wall clock (accel simulated minutes per real
        second); samples are generated in batches up to the current simulated
        time, so the sample rate is exact whatever the accel."""
        c = self.cfg
        step = 1.0 / (c.sample_hz * 60.0)             # simulated minutes between samples
        end = self.duration_min()
        start = time.time()
        while not self._stop.is_set():
            target = min(end, (time.time() - start) * c.accel)
            with self._lock:
                while self._t <= target + 1e-12:
                    t = self._t
                    self._x.append(round(t, 5))
                    self._y.append(self._signal(t))
                    self._p.append(self._pressure(t))
                    self._t += step
                done = self._t > end
            if done:
                break
            time.sleep(min(0.05, step / c.accel * 10))
        with self._lock:
            if not self._stop.is_set():
                self._state = "Finished"
                self.runs_done += 1

    # ------------------------------------------------------------ what WinGPC would show
    def status(self) -> str:
        with self._lock:
            return f"Sequence {self._state}  t = {self._t:.2f} min" if self._state == "Running" else f"Sequence {self._state}"

    def pressure_bar(self) -> float | None:
        with self._lock:
            return self._p[-1] if self._p else None

    def snapshot(self) -> dict[str, Any]:
        """Shaped like LiveMonitor.snapshot(): the bridge only reads trace.x / calibrated / changed."""
        with self._lock:
            x = list(self._x[-50:])
            running = self._state == "Running"
        return {"running": True, "changed_since_previous": running, "trace": {"calibrated": True, "x": x, "n": len(self._x)}}

    def export_ascii(self, path: str | Path) -> Path:
        with self._lock:
            x, y, p = list(self._x), list(self._y), list(self._p)
        m = self.method
        g = ", ".join(f"{n['time_min']:g} min {n['pct_b']:g} %B" for n in m["gradient"])
        lines = ["WinGPC UniChrom ASCII export (simulated instrument)",
                 f"Method: {g}; {m['temperature_c']:g} C; {self.cfg.flow_ml_min:g} mL/min",
                 f"Points: {len(x)}", "", "Time [min]\tUV 254 [mAU]\tPressure [bar]"]
        lines += [f"{a:.4f}\t{b:.4f}\t{c:.2f}" for a, b, c in zip(x, y, p)]
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out

    def expected_peaks(self) -> list[dict[str, float]]:
        return [{"name": n, "t_r": round(tr, 3), "sigma": round(s, 3)} for n, tr, s, _ in self._peaks]

    def trace_arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        with self._lock:
            return np.array(self._x), np.array(self._y), np.array(self._p)
