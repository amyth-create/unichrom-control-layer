"""The bridge: one GBL run, end to end, on WinGPC.

    poll GBL            pending.step == "upload"  (a method is in flight, no trace yet)
    apply method        manual | template | recipe   (gbl.method, MethodApplier)
    start run           recipe sequence_start      HAZARDOUS -> confirmation unless unattended_ok
    watch run           LiveMonitor + optional status text + expected duration
    export ASCII        recipe ascii_export_raw_data -> file in export_dir
    validate            our parser AND GBL's rules (time first, >= 20 rows); normalise if needed
    upload              POST /api/upload  (GBL copies it into traces/runNN.txt)
    read (optional)     POST /api/read    (GBL's model reads it; the analyst still records)

Everything the bridge does is logged to the audit file and returned as a
RunReport so the LLM on top (or the operator) can see each step's outcome.
Recording the verdict is deliberately left to GBL's own page: that is the
analyst's decision in GBL's design.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..chromatogram import ascii_parser
from ..errors import ActionDenied, WinGPCAgentError
from .client import Client, GBLError
from .method import Method, expected_run_minutes, from_gbl, write_method_file


@dataclass
class StepResult:
    step: str
    ok: bool
    detail: str = ""
    t: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunReport:
    run_no: int | None
    method: dict[str, Any]
    steps: list[StepResult] = field(default_factory=list)
    export_path: str | None = None
    uploaded_as: str | None = None
    reading: dict[str, Any] | None = None
    ok: bool = False

    def add(self, step: str, ok: bool, detail: str = "", **data: Any) -> StepResult:
        s = StepResult(step, ok, detail, data=data)
        self.steps.append(s)
        return s

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# --------------------------------------------------------------------------- hooks
# The bridge does not import pywinauto itself; the MCP server / CLI inject
# callables, so the bridge is testable on any OS with fakes.
@dataclass
class Instrument:
    run_recipe: Callable[[str, dict[str, Any], str | None], dict[str, Any]]     # name, params, confirm_token
    read_text: Callable[[str], str] | None = None                                 # UIA path -> text
    read_pressure: Callable[[], float | None] | None = None                     # pump pressure in bar, if readable
    monitor_snapshot: Callable[[], dict[str, Any]] | None = None                  # LiveMonitor.snapshot()
    monitor_start: Callable[[], None] | None = None
    notify_operator: Callable[[str], None] = print
    wait_operator: Callable[[str], bool] | None = None                            # blocking prompt; None = headless


@dataclass
class BridgeConfig:
    method_strategy: str = "manual"            # manual | template | recipe
    template_file: str | None = None
    method_out_dir: str | None = None
    load_method_recipe: str = "load_instrument_method"       # used with template strategy
    apply_method_recipe: str = "apply_gradient_method"       # used with recipe strategy
    start_recipe: str = "sequence_start"
    export_recipe: str = "ascii_export_raw_data"
    export_dir: str = "."
    # "recipe": run export_recipe after the run.
    # "automation_file": WinGPC's Automation (Definition > Automation properties >
    #   Report > "Create ASCII MWD Report", template name e.g. gbl.txt) writes
    #   NNN_gbl.txt into the project folder for every sample; wait for that file.
    export_strategy: str = "recipe"
    automation_dir: str | None = None          # project folder WinGPC writes into
    automation_template: str = "gbl.txt"       # the template name set in the Report tab
    automation_wait_s: float = 180.0
    fixed_extra_min: float = 0.0               # wash / re-equilibration counted in the run
    post_time_min: float | None = None
    run_timeout_factor: float = 1.5
    run_timeout_margin_min: float = 3.0
    status_path: str | None = None             # UIA path of a status text, e.g. Sequence Manager state
    status_done_re: str = r"(finished|complete|idle|ready|beendet|fertig)"
    status_running_re: str = r"(running|acquir|läuft|aufnahme)"
    flat_seconds_means_done: float = 90.0      # live plot unchanged this long after expected end -> done
    min_elapsed_fraction: float = 0.8          # ignore 'done' signals before this share of the expected time
    time_scale: float = 1.0                    # simulated minutes per real minute (1.0 on a real instrument)
    watch_poll_s: float = 2.0
    stop_recipe: str = "sequence_stop"
    pressure_warn_bar: float | None = None     # log a warning above this
    pressure_max_bar: float | None = None      # abort the run (stop recipe) above this for 2 consecutive samples
    detector_column_re: str | None = None      # pick this y column when normalising (e.g. "UV|254")
    upload_format: str = "original"            # original | normalised
    auto_read: bool = True
    confirm_each_run: bool = True              # ask the operator before starting an injection


def _pstats(p: list[float]) -> dict[str, Any]:
    if not p:
        return {"samples": 0}
    a = np.asarray(p)
    return {"samples": int(a.size), "min_bar": round(float(a.min()), 1), "max_bar": round(float(a.max()), 1),
            "mean_bar": round(float(a.mean()), 1), "last_bar": round(float(a[-1]), 1)}


class Bridge:
    def __init__(self, gbl: Client, instrument: Instrument, cfg: BridgeConfig,
                 audit: Callable[[str, str, dict[str, Any]], None] | None = None):
        self.gbl = gbl
        self.inst = instrument
        self.cfg = cfg
        self.audit = audit or (lambda ev, tool, args: None)
        self._stop = threading.Event()
        self.reports: list[RunReport] = []
        self.last_seen_key: tuple | None = None

    # ------------------------------------------------------------------ GBL side
    def pending_method(self) -> Method | None:
        pend = self.gbl.pending()
        if not pend or pend.get("trace") or pend.get("step") not in (None, "upload"):
            return None
        return from_gbl(pend)

    # ------------------------------------------------------------------ one cycle
    def run_once(self, confirm_token: str | None = None, method: Method | None = None) -> RunReport:
        method = method or self.pending_method()
        if method is None:
            rep = RunReport(None, {})
            rep.add("poll", False, "GBL has no method waiting for a trace (nothing in flight, or already uploaded)")
            return rep
        rep = RunReport(method.run_no, method.to_dict())
        self.reports.append(rep)
        self.audit("gbl_cycle_start", "bridge", method.to_dict())
        try:
            self._apply_method(method, rep, confirm_token)
            self._start(method, rep, confirm_token)
            self._watch(method, rep)
            path = self._export(method, rep, confirm_token)
            path = self._validate(path, rep)
            self._upload(path, rep)
            if self.cfg.auto_read:
                self._read(rep)
            rep.ok = all(s.ok for s in rep.steps)
        except (WinGPCAgentError, GBLError, ValueError, OSError) as e:
            rep.add("abort", False, f"{type(e).__name__}: {e}")
            rep.ok = False
        self.audit("gbl_cycle_end", "bridge", {"run": method.run_no, "ok": rep.ok})
        return rep

    # ------------------------------------------------------------------ steps
    def _apply_method(self, m: Method, rep: RunReport, token: str | None) -> None:
        strat = self.cfg.method_strategy
        if strat == "manual":
            msg = (f"GBL run {m.run_no}: key this method into the WinGPC/ChromPilot instrument method, "
                   f"save it, make sure the sequence line uses it:\n{m.table_text()}")
            self.inst.notify_operator(msg)
            if self.inst.wait_operator:
                if not self.inst.wait_operator("Method entered and saved? [y/N] "):
                    raise ActionDenied("operator did not confirm the method entry")
            rep.add("apply_method", True, "manual entry by operator", table=m.table_text())
            return
        if strat == "template":
            if not self.cfg.template_file:
                raise ValueError("template strategy needs template_file")
            out = write_method_file(self.cfg.template_file, self.cfg.method_out_dir or self.cfg.export_dir, m)
            rep.add("write_method_file", True, str(out), path=str(out))
            res = self.inst.run_recipe(self.cfg.load_method_recipe, {"path": str(out)}, token)
            rep.add("load_method", bool(res.get("ok")), json.dumps(res.get("log", []))[-500:], recipe=res)
            if not res.get("ok"):
                raise WinGPCAgentError(f"recipe {self.cfg.load_method_recipe} failed")
            return
        if strat == "recipe":
            params: dict[str, Any] = {"temperature_c": f"{m.temperature_c:g}", "n_rows": len(m.rows),
                                      "duration_min": f"{m.duration_min:g}"}
            for i in range(10):
                params[f"t{i}"] = f"{m.rows[i].time_min:g}" if i < len(m.rows) else ""
                params[f"b{i}"] = f"{m.rows[i].pct_b:g}" if i < len(m.rows) else ""
                params[f"a{i}"] = f"{m.rows[i].pct_a:g}" if i < len(m.rows) else ""
            res = self.inst.run_recipe(self.cfg.apply_method_recipe, params, token)
            rep.add("apply_method", bool(res.get("ok")), json.dumps(res.get("log", []))[-500:], recipe=res)
            if not res.get("ok"):
                raise WinGPCAgentError(f"recipe {self.cfg.apply_method_recipe} failed")
            return
        raise ValueError(f"unknown method_strategy {strat!r}")

    def _start(self, m: Method, rep: RunReport, token: str | None) -> None:
        if self.cfg.confirm_each_run and self.inst.wait_operator:
            if not self.inst.wait_operator(f"Start injection for GBL run {m.run_no} ({m.describe or m.table_text()})? [y/N] "):
                raise ActionDenied("operator declined to start the run")
        if self.inst.monitor_start:
            self.inst.monitor_start()
        res = self.inst.run_recipe(self.cfg.start_recipe, {}, token)
        rep.add("start", bool(res.get("ok")), json.dumps(res.get("log", []))[-500:], recipe=res)
        if not res.get("ok"):
            raise WinGPCAgentError(f"start recipe failed: {res}")
        rep.add("started_at", True, time.strftime("%Y-%m-%d %H:%M:%S"))

    def _watch(self, m: Method, rep: RunReport) -> None:
        """Block until the run is over. Signals, in order of trust:
        1. status text matches status_done_re after having matched running
        2. live plot x_last (calibrated) reached the expected run length
        3. live plot unchanged for flat_seconds_means_done after expected end
        4. hard timeout -> fail"""
        expected = expected_run_minutes(m, self.cfg.fixed_extra_min, self.cfg.post_time_min)
        timeout = expected * self.cfg.run_timeout_factor + self.cfg.run_timeout_margin_min
        t0 = time.time()
        seen_running = False
        last_change = t0
        last_hash_t = None
        done_re = re.compile(self.cfg.status_done_re, re.I)
        run_re = re.compile(self.cfg.status_running_re, re.I)
        reason = None
        pressures: list[float] = []
        over = 0
        warned = False
        while not self._stop.is_set():
            elapsed_min = (time.time() - t0) / 60 * self.cfg.time_scale
            if self.inst.read_pressure:
                try:
                    pb = self.inst.read_pressure()
                except Exception:
                    pb = None
                if pb is not None:
                    pressures.append(float(pb))
                    if self.cfg.pressure_warn_bar and pb > self.cfg.pressure_warn_bar and not warned:
                        warned = True
                        rep.add("pressure_warning", True, f"{pb:.0f} bar above warn level {self.cfg.pressure_warn_bar:.0f} at {elapsed_min:.1f} min")
                        self.inst.notify_operator(f"[bridge] pressure {pb:.0f} bar above warn level")
                    over = over + 1 if (self.cfg.pressure_max_bar and pb > self.cfg.pressure_max_bar) else 0
                    if over >= 2:
                        rep.add("pressure_abort", False, f"{pb:.0f} bar above max {self.cfg.pressure_max_bar:.0f}; stopping the run",
                                pressure=_pstats(pressures))
                        try:
                            self.inst.run_recipe(self.cfg.stop_recipe, {}, None)
                        except Exception as e:   # the abort must be reported even if the stop click fails
                            rep.add("stop", False, repr(e))
                        raise WinGPCAgentError("run aborted: pressure limit exceeded")
            if self.cfg.status_path and self.inst.read_text:
                try:
                    txt = self.inst.read_text(self.cfg.status_path)
                    if run_re.search(txt):
                        seen_running = True
                    if seen_running and done_re.search(txt) and elapsed_min >= 0.5 * self.cfg.min_elapsed_fraction * expected:
                        reason = f"status text '{txt.strip()[:60]}' after {elapsed_min:.1f} min"
                        break
                except WinGPCAgentError:
                    pass
            if self.inst.monitor_snapshot:
                snap = self.inst.monitor_snapshot()
                tr = snap.get("trace") or {}
                if snap.get("changed_since_previous"):
                    last_change = time.time()
                if tr.get("calibrated") and tr.get("x"):
                    if tr["x"][-1] >= expected - 1e-6 and elapsed_min >= self.cfg.min_elapsed_fraction * expected:
                        reason = f"plot reached {tr['x'][-1]:.2f} min >= expected {expected:.2f}"
                        break
                if elapsed_min >= self.cfg.min_elapsed_fraction * expected and time.time() - last_change >= self.cfg.flat_seconds_means_done:
                    reason = f"plot unchanged for {self.cfg.flat_seconds_means_done:.0f}s after expected end"
                    break
            elif elapsed_min >= self.cfg.min_elapsed_fraction * expected:
                reason = f"expected duration {expected:.1f} min elapsed (no monitor available)"
                break
            if elapsed_min > timeout:
                rep.add("watch", False, f"timed out after {elapsed_min:.1f} min (expected {expected:.1f})", pressure=_pstats(pressures))
                raise WinGPCAgentError("run did not finish in time")
            self._stop.wait(self.cfg.watch_poll_s)
        rep.add("watch", True, reason or "stopped", expected_min=expected,
                elapsed_min=round((time.time() - t0) / 60 * self.cfg.time_scale, 2), pressure=_pstats(pressures))

    def _export(self, m: Method, rep: RunReport, token: str | None) -> str:
        if self.cfg.export_strategy == "automation_file":
            return self._export_automation_file(m, rep)
        out = Path(self.cfg.export_dir) / f"gbl_run{m.run_no or 0:02d}_{time.strftime('%Y%m%d-%H%M%S')}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        res = self.inst.run_recipe(self.cfg.export_recipe, {"path": str(out)}, token)
        ok = bool(res.get("ok"))
        deadline = time.time() + 30
        while ok and time.time() < deadline and not out.exists():
            time.sleep(0.5)
        if not ok or not out.exists():
            rep.add("export", False, f"recipe ok={ok}, file exists={out.exists()}", recipe=res)
            raise WinGPCAgentError("ASCII export did not produce a file")
        rep.add("export", True, str(out), path=str(out), bytes=out.stat().st_size)
        rep.export_path = str(out)
        return str(out)

    def _export_automation_file(self, m: Method, rep: RunReport) -> str:
        """Wait for the per-sample ASCII report WinGPC's automation writes
        (`NNN_<template>` in the project folder), newer than the run start."""
        folder = Path(self.cfg.automation_dir or self.cfg.export_dir)
        started = next((s.t for s in rep.steps if s.step == "start"), time.time() - 1)
        suffix = "_" + self.cfg.automation_template.lower()
        deadline = time.time() + self.cfg.automation_wait_s
        found: Path | None = None
        while time.time() < deadline and not self._stop.is_set():
            cands = [p for p in folder.glob("*" + self.cfg.automation_template)
                     if p.name.lower().endswith(suffix) and p.stat().st_mtime >= started - 2]
            if cands:
                found = max(cands, key=lambda p: p.stat().st_mtime)
                size = found.stat().st_size
                self._stop.wait(1.0)                      # let WinGPC finish writing
                if found.stat().st_size == size and size > 0:
                    break
            self._stop.wait(1.0)
        if not found:
            rep.add("export", False, f"no new *{suffix} in {folder} within {self.cfg.automation_wait_s:.0f}s "
                                     f"(is Automation activated with Create ASCII MWD Report?)")
            raise WinGPCAgentError("automation ASCII report did not appear")
        rep.add("export", True, str(found), path=str(found), bytes=found.stat().st_size, strategy="automation_file")
        rep.export_path = str(found)
        return str(found)

    def _validate(self, path: str, rep: RunReport) -> str:
        tables = ascii_parser.parse_file(path)
        big = [t for t in tables if t.data.shape[0] >= 20 and t.data.shape[1] >= 2]
        if not big:
            rep.add("validate", False, "no table with >=20 rows and >=2 columns in the export")
            raise ValueError("export is not a usable chromatogram")
        t = max(big, key=lambda x: x.data.shape[0])
        n_num_cols = t.data.shape[1]
        want_norm = self.cfg.upload_format == "normalised" or n_num_cols > 2 or (t.x[-1] - t.x[0]) <= 0
        detail = f"{t.data.shape[0]} rows, {n_num_cols} numeric cols, x {t.x[0]:g}..{t.x[-1]:g}"
        if not want_norm:
            rep.add("validate", True, detail + " (uploading original)")
            return path
        col = 1
        if self.cfg.detector_column_re and len(t.columns) == n_num_cols:
            rx = re.compile(self.cfg.detector_column_re, re.I)
            hits = [i for i, c in enumerate(t.columns) if i > 0 and rx.search(c)]
            if hits:
                col = hits[0]
        y = t.data[:, col]
        mask = ~(np.isnan(t.x) | np.isnan(y))
        norm = Path(path).with_name(Path(path).stem + "_2col.txt")
        with open(norm, "w", encoding="utf-8") as fh:
            fh.write(f"# normalised by wingpc-agent from {Path(path).name}; columns: {t.columns[0]} / {t.columns[col]}\n")
            for k, v in t.meta.items():
                fh.write(f"# {k}: {v}\n")
            fh.write(f"{t.columns[0]}\t{t.columns[col]}\n")
            for a, b in zip(t.x[mask], y[mask]):
                fh.write(f"{a:.5f}\t{b:.6g}\n")
        rep.add("validate", True, detail + f" -> normalised to 2 columns ({t.columns[col]})", normalised=str(norm))
        return str(norm)

    def _upload(self, path: str, rep: RunReport) -> None:
        pend = self.gbl.upload(path, Path(path).name)
        rep.uploaded_as = pend.get("trace")
        rep.add("upload", bool(pend.get("trace")), f"GBL stored it as {pend.get('trace')}",
                trace_facts=pend.get("trace_facts") or {})
        if not pend.get("trace"):
            raise GBLError("GBL did not register the upload")

    def _read(self, rep: RunReport) -> None:
        try:
            pend = self.gbl.read()
        except GBLError as e:
            rep.add("read", False, str(e))
            return
        rep.reading = pend.get("reading")
        r = rep.reading or {}
        rep.add("read", bool(r), f"{r.get('n_peaks')} peaks; verdict {r.get('verdict', 'n/a')}: {r.get('summary', '')[:200]}")

    # ------------------------------------------------------------------ daemon
    def serve(self, poll_s: float = 5.0, on_report: Callable[[RunReport], None] | None = None) -> None:
        """Keep polling GBL; run every method that appears. Ctrl-C / stop() ends it."""
        while not self._stop.is_set():
            try:
                m = self.pending_method()
            except GBLError as e:
                self.inst.notify_operator(f"[bridge] {e}")
                m = None
            if m is not None and m.key() != self.last_seen_key:
                self.last_seen_key = m.key()
                rep = self.run_once(method=m)
                if on_report:
                    on_report(rep)
                if not rep.ok:
                    self.inst.notify_operator(f"[bridge] run {m.run_no} did not complete: "
                                              f"{rep.steps[-1].step}: {rep.steps[-1].detail}")
            self._stop.wait(poll_s)

    def stop(self) -> None:
        self._stop.set()
