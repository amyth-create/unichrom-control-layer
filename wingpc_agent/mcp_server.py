"""MCP server exposing WinGPC control + observation tools to an LLM.

Run on the WinGPC PC:
    wingpc-agent serve                 # stdio  (Claude Desktop / Claude Code / agent/run_agent.py)
    wingpc-agent serve --http 8765     # streamable-http for a remote agent

Tool families
  wingpc_*   process / window status, discovery report
  ui_*       generic UI Automation (tree, find, click, type, menu, keys, screenshot)
  recipe_*   named multi-step procedures from recipes/*.yaml (verified paths)
  chrom_*    real-time chromatogram: start/stop monitor, snapshot (numbers),
             image (PNG for the model's vision), history, peaks, axis calibration
  files_*    exports on disk (ground-truth numbers)
Every call passes through safety.Gate (policy levels + audit log).
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent

from . import __version__
from .backends import files as files_be
from .backends import screen
from .chromatogram import ascii_parser
from .chromatogram.analysis import describe, find_peaks
from .config import Settings
from .errors import ActionDenied, BackendUnavailable, ElementNotFound, RecipeError, WinGPCAgentError
from .recipes import RecipeBook
from .safety import Gate, Policy
from .stream import LiveMonitor
from .gbl.bridge import Bridge, BridgeConfig, Instrument
from .gbl.client import FolderClient, GBLError, HttpClient
from .gbl.method import from_gbl


class Context:
    """Lazily constructed shared state (so `wingpc-agent serve` starts even
    when WinGPC is not running yet)."""

    def __init__(self, settings: Settings):
        self.s = settings
        self.gate = Gate(Policy.load(settings.policy_file), settings.audit_log,
                         interactive_confirm=False,   # the server is usually headless
                         token_file=Path(settings.state_dir) / "confirm_tokens.json")
        self.book = RecipeBook.load(settings.recipes_file) if Path(settings.recipes_file).exists() else RecipeBook({})
        self._uia = None
        self.monitor = LiveMonitor(settings, screen.grab, self._plot_rect)
        self._file_events: list[dict[str, Any]] = []
        dirs = [d for d in ([settings.export_dir] if settings.export_dir else []) + list(settings.project_dirs) if d]
        self.watcher = None
        if dirs:
            self.watcher = files_be.DirWatcher(dirs, settings.export_extensions, self._on_file)
            self.watcher.start()

    @property
    def uia(self):
        if self._uia is None:
            from .backends.uia import UIA
            self._uia = UIA(self.s.main_window_title_re)
        return self._uia

    def _plot_rect(self) -> list[int] | None:
        p = self.s.plot
        if p.control_path:
            try:
                return self.uia.rect("|".join(p.control_path))
            except (BackendUnavailable, ElementNotFound):
                return None
        if p.window_title_re and p.rect is None:
            try:
                w = self.uia.window(p.window_title_re, timeout=1)
                r = w.rectangle()
                return [r.left, r.top, r.right, r.bottom]
            except (BackendUnavailable, ElementNotFound):
                return None
        return None

    # ---------------------------------------------------------------- GBL bridge
    _bridge: Bridge | None = None

    def gbl_client(self):
        g = self.s.gbl
        if g.campaign_dir:
            return FolderClient(g.campaign_dir)
        return HttpClient(g.url)

    def bridge(self, run_recipe) -> Bridge:
        if self._bridge is None:
            g = self.s.gbl
            cfg = BridgeConfig(
                method_strategy=g.method_strategy, template_file=g.template_file,
                method_out_dir=g.method_out_dir, export_dir=self.s.export_dir or self.s.state_dir,
                fixed_extra_min=g.fixed_extra_min, post_time_min=g.post_time_min, status_path=g.status_path,
                detector_column_re=g.detector_column_re, upload_format=g.upload_format,
                auto_read=g.auto_read, confirm_each_run=False,   # headless: confirmation is the token
                pressure_warn_bar=g.pressure_warn_bar, pressure_max_bar=g.pressure_max_bar,
                export_strategy=g.export_strategy, automation_dir=g.automation_dir, automation_template=g.automation_template,
            )
            inst = Instrument(
                run_recipe=run_recipe,
                read_text=lambda path: self.uia.read_text(path),
                read_pressure=(lambda: _bar(self.uia.read_text(g.pressure_path))) if g.pressure_path else None,
                monitor_snapshot=lambda: self.monitor.snapshot(max_points=50, with_peaks=False),
                monitor_start=self.monitor.start,
                notify_operator=lambda msg: self.gate.audit("operator_notice", "bridge", {"msg": msg[:500]}),
                wait_operator=None,
            )
            self._bridge = Bridge(self.gbl_client(), inst, cfg, audit=self.gate.audit)
        return self._bridge

    def _on_file(self, ev) -> None:
        self._file_events.append({"t": time.time(), **ev.__dict__})
        self._file_events = self._file_events[-200:]


def _bar(text: str) -> float | None:
    """'Pressure: 184.3 bar' -> 184.3 (first number in the text)."""
    m = re.search(r"[-+]?\d+(?:[.,]\d+)?", text or "")
    return float(m.group(0).replace(",", ".")) if m else None


def build_server(settings: Settings | None = None) -> MCPServer:
    s = settings or Settings.load()
    ctx = Context(s)
    srv = MCPServer(
        name="wingpc-agent",
        version=__version__,
        instructions=(
            "You are operating PSS/Agilent WinGPC UniChrom (GPC/SEC chromatography data system) through "
            "this server. Observe before acting: call wingpc_status, then chrom_live_snapshot / chrom_live_image. "
            "Prefer recipe_* tools (verified multi-step procedures) over raw ui_* clicks. Hazardous actions "
            "(start/stop/pause a sequence, close windows) need a confirm_token that only a human operator can "
            "issue; if a call returns ActionDenied, report it to the operator instead of retrying. Numbers from "
            "chrom_live_* come from pixels of the live plot: use them for trends, timing and peak counting; use "
            "files_read_ascii on an exported file when exact values matter."
        ),
    )

    def guard(tool: str, args: dict[str, Any], token: str | None = None) -> None:
        ctx.gate.check(tool, args, token)

    def err(e: Exception) -> str:
        kind = type(e).__name__
        return json.dumps({"error": kind, "message": str(e)})

    # ------------------------------------------------------------ status
    @srv.tool(description="Is WinGPC running? Which windows exist? Monitor + watcher state. Call this first.")
    def wingpc_status() -> str:
        guard("wingpc_status", {})
        out: dict[str, Any] = {"server_version": __version__, "settings": {
            "poll_hz": s.poll_hz, "export_dir": s.export_dir, "project_dirs": s.project_dirs,
            "plot": s.plot.__dict__, "allow_unverified_recipes": s.allow_unverified_recipes}}
        try:
            wins = ctx.uia.windows()
            out["windows"] = [w for w in wins if w["visible"] and w["title"]]
            out["wingpc_main_window_found"] = any(
                re.search(s.main_window_title_re, w["title"] or "") for w in wins)
        except BackendUnavailable as e:
            out["windows"] = {"unavailable": str(e)}
        out["monitor"] = {"running": ctx.monitor.running, **ctx.monitor.stats.__dict__}
        out["file_watcher"] = {"active": ctx.watcher is not None, "recent_events": ctx._file_events[-5:]}
        return json.dumps(out, default=str)

    @srv.tool(description="Return the discovery report written by `wingpc-agent probe` (install dir, docs, COM "
                          "ProgIDs, UI tree menu names, plot control candidates, file census).")
    def discovery_report(section: str | None = None) -> str:
        guard("discovery_report", {"section": section})
        p = Path(s.state_dir) / "discovery.json"
        if not p.exists():
            return json.dumps({"error": "no report yet; operator must run `wingpc-agent probe` on the WinGPC PC"})
        data = json.loads(p.read_text(encoding="utf-8"))
        if section:
            return json.dumps(data.get(section, {"error": f"no section {section}", "sections": list(data)}), default=str)
        data.pop("ui_tree_main", None)   # too big; ask for it by section
        return json.dumps(data, default=str)

    # ------------------------------------------------------------ UI
    @srv.tool(description="Dump the UI Automation tree of a window (default: WinGPC main). Returns control types, "
                          "names, automation ids, rects and a `path` usable by ui_click/ui_type/ui_read_text.")
    def ui_tree(title_re: str | None = None, max_depth: int = 4, max_children: int = 60) -> str:
        guard("ui_tree", {"title_re": title_re})
        try:
            return json.dumps(ctx.uia.tree(title_re, max_depth, max_children).to_dict(), default=str)
        except WinGPCAgentError as e:
            return err(e)

    @srv.tool(description="Find controls by name regex and/or control type (Button, MenuItem, Edit, Text, "
                          "ComboBox, List, DataGrid, StatusBar, Pane...) inside a window.")
    def ui_find(name_re: str | None = None, control_type: str | None = None, title_re: str | None = None,
                max_results: int = 40) -> str:
        guard("ui_find", {"name_re": name_re, "control_type": control_type})
        try:
            return json.dumps([e.to_dict() for e in ctx.uia.find(title_re, name_re, control_type, max_results)], default=str)
        except WinGPCAgentError as e:
            return err(e)

    @srv.tool(description="Read the text/value of a control given its UIA path (from ui_tree/ui_find).")
    def ui_read_text(path: str) -> str:
        guard("ui_read_text", {"path": path})
        try:
            return json.dumps({"path": path, "text": ctx.uia.read_text(path)})
        except WinGPCAgentError as e:
            return err(e)

    @srv.tool(description="Click a control by UIA path (uses the Invoke pattern when possible, so the mouse "
                          "does not move). Set double=true for double-click.")
    def ui_click(path: str, double: bool = False) -> str:
        guard("ui_click", {"path": path, "double": double})
        try:
            return json.dumps({"clicked": ctx.uia.click(path, double)}, default=str)
        except WinGPCAgentError as e:
            return err(e)

    @srv.tool(description="Set the text of an edit control (UIA path).")
    def ui_type(path: str, text: str) -> str:
        guard("ui_type", {"path": path, "text": text})
        try:
            ctx.uia.set_text(path, text)
            return json.dumps({"ok": True})
        except WinGPCAgentError as e:
            return err(e)

    @srv.tool(description="Select an item in a combobox/list by visible text (UIA path).")
    def ui_select(path: str, item: str) -> str:
        guard("ui_select", {"path": path, "item": item})
        try:
            ctx.uia.select(path, item)
            return json.dumps({"ok": True})
        except WinGPCAgentError as e:
            return err(e)

    @srv.tool(description="Invoke a menu path, e.g. [\"File\",\"ASCII export...\"], in a window (default main).")
    def ui_menu(path: list[str], title_re: str | None = None) -> str:
        guard("ui_menu", {"path": path, "title_re": title_re})
        try:
            ctx.uia.menu(path, title_re)
            return json.dumps({"ok": True})
        except WinGPCAgentError as e:
            return err(e)

    @srv.tool(description="Send keystrokes (pywinauto syntax: ^s, %f, {ENTER}, {TAB}) to a focused window.")
    def ui_keys(keys: str, title_re: str | None = None) -> str:
        guard("ui_keys", {"keys": keys, "title_re": title_re})
        try:
            ctx.uia.send_keys(keys, title_re)
            return json.dumps({"ok": True})
        except WinGPCAgentError as e:
            return err(e)

    @srv.tool(description="Close a window by title regex. HAZARDOUS (may discard unsaved work).")
    def ui_close_window(title_re: str, confirm_token: str | None = None) -> str:
        try:
            guard("ui_close_window", {"title_re": title_re}, confirm_token)
            ctx.uia.close_window(title_re)
            return json.dumps({"ok": True})
        except WinGPCAgentError as e:
            return err(e)

    @srv.tool(description="Screenshot of a window (title regex; default WinGPC main) or the whole screen. "
                          "Returns a PNG image the model can look at (axis labels, dialogs, status bars).")
    def ui_screenshot(title_re: str | None = None, whole_screen: bool = False, max_width: int = 1400) -> list[Any]:
        guard("ui_screenshot", {"title_re": title_re, "whole_screen": whole_screen})
        try:
            rect = None
            if not whole_screen:
                w = ctx.uia.window(title_re)
                r = w.rectangle()
                rect = [r.left, r.top, r.right, r.bottom]
            img = screen.grab(rect)
            png = screen.to_png_bytes(img, max_width)
            return [ImageContent(type="image", data=base64.b64encode(png).decode(), mimeType="image/png"),
                    TextContent(type="text", text=json.dumps({"rect": rect, "size": img.size}))]
        except WinGPCAgentError as e:
            return [TextContent(type="text", text=err(e))]

    # ------------------------------------------------------------ recipes
    @srv.tool(description="List available recipes (named multi-step UI procedures) with level, params, verified flag.")
    def recipes_list() -> str:
        guard("recipes_list", {})
        return json.dumps({"source": ctx.book.source, "recipes": ctx.book.summary()})

    def _run_recipe(name: str, params: dict[str, Any], token: str | None) -> dict[str, Any]:
        rec = ctx.book.get(name)
        tool_name = f"recipe_{name}" if f"recipe_{name}" in ctx.gate.policy.levels else "recipe_run"
        # enforce the stricter of the tool level and the recipe's own level
        if rec.level == "hazardous" and ctx.gate.policy.level(tool_name) != "hazardous":
            tool_name = f"recipe_{name}"
            ctx.gate.policy.levels[tool_name] = "hazardous"
        guard(tool_name, {"recipe": name, **params}, token)
        if not rec.verified and not s.allow_unverified_recipes:
            raise RecipeError(f"recipe {name!r} is not verified on this installation; operator must verify UI paths "
                              f"(see docs/RUNBOOK.md) or set allow_unverified_recipes.")
        steps = rec.render(**params)
        log: list[dict[str, Any]] = []
        for i, st in enumerate(steps):
            kind = st.get("kind")
            t0 = time.time()
            try:
                if kind == "focus":
                    ctx.uia.focus(st.get("title_re"))
                elif kind == "activate_child":
                    ctx.uia.activate_child(st["title_re"], st.get("main_title_re"))
                elif kind == "menu":
                    ctx.uia.menu(st["path"], st.get("window_re"))
                elif kind == "click":
                    ctx.uia.click(st["target"], bool(st.get("double", False)))
                elif kind == "type":
                    ctx.uia.set_text(st["target"], str(st["text"]))
                elif kind == "keys":
                    ctx.uia.send_keys(st["keys"], st.get("title_re"))
                elif kind == "select":
                    ctx.uia.select(st["target"], str(st["item"]))
                elif kind == "wait":
                    ok = ctx.uia.wait(st.get("title_re"), st.get("target"), float(st.get("timeout", 10)))
                    if not ok:
                        raise ElementNotFound(f"wait timed out: {st}")
                elif kind == "wait_gone":
                    ok = ctx.uia.wait(st.get("title_re"), st.get("target"), float(st.get("timeout", 10)), gone=True)
                    if not ok:
                        raise ElementNotFound(f"wait_gone timed out: {st}")
                elif kind == "assert":
                    ctx.uia.resolve(st["target"], timeout=float(st.get("timeout", 3)))
                elif kind == "sleep":
                    time.sleep(float(st.get("seconds", 0.5)))
                else:
                    raise RecipeError(f"unknown step kind {kind!r}")
                log.append({"step": i, "kind": kind, "ok": True, "ms": round((time.time() - t0) * 1000)})
            except Exception as e:
                log.append({"step": i, "kind": kind, "ok": False, "error": repr(e), "detail": st})
                return {"recipe": name, "ok": False, "log": log}
        out = {"recipe": name, "ok": True, "log": log}
        if rec.returns == "path":
            out["path"] = params.get("path")
        return out

    @srv.tool(description="Run a recipe by name with params (see recipes_list). Hazardous recipes need confirm_token.")
    def recipe_run(name: str, params: dict[str, Any] | None = None, confirm_token: str | None = None) -> str:
        try:
            return json.dumps(_run_recipe(name, params or {}, confirm_token), default=str)
        except WinGPCAgentError as e:
            return err(e)

    @srv.tool(description="Export the current Raw Data window to an ASCII .txt via the Data Editor recipe, then "
                          "parse it and return a numeric summary + first peaks. `path` defaults to the export dir.")
    def export_ascii(path: str | None = None, confirm_token: str | None = None) -> str:
        try:
            dest = path or str(Path(s.export_dir or s.state_dir) / f"export_{int(time.time())}.txt")
            res = _run_recipe("ascii_export_raw_data", {"path": dest}, confirm_token)
            if res["ok"]:
                deadline = time.time() + 20
                while time.time() < deadline and not Path(dest).exists():
                    time.sleep(0.5)
                if Path(dest).exists():
                    res["parsed"] = _parse_summary(dest)
                else:
                    res["warning"] = "recipe finished but the file did not appear; check the save dialog path field"
            return json.dumps(res, default=str)
        except WinGPCAgentError as e:
            return err(e)

    # ------------------------------------------------------------ live chromatogram
    @srv.tool(description="Start the real-time monitor (captures the live Raw Data plot at poll_hz and extracts "
                          "the trace). Idempotent.")
    def chrom_live_start() -> str:
        guard("chrom_live_start", {})
        ctx.monitor.start()
        return json.dumps({"running": ctx.monitor.running, "poll_hz": s.poll_hz})

    @srv.tool(description="Stop the real-time monitor.")
    def chrom_live_stop() -> str:
        guard("chrom_live_stop", {})
        ctx.monitor.stop()
        return json.dumps({"running": False})

    @srv.tool(description="Latest extracted trace (downsampled x/y), descriptive stats, detected peaks and monitor "
                          "stats. If axes are uncalibrated, values are fractions 0..1 of the plot box.")
    def chrom_live_snapshot(max_points: int = 300, with_peaks: bool = True) -> str:
        guard("chrom_live_snapshot", {"max_points": max_points})
        if not ctx.monitor.running and ctx.monitor.latest() is None:
            try:
                ctx.monitor.tick()      # one-shot capture even when not running
            except Exception as e:
                return err(e)
        return json.dumps(ctx.monitor.snapshot(max_points, with_peaks), default=str)

    @srv.tool(description="PNG of the latest captured plot region (look at axis labels, cursor, legend). Also "
                          "returns the pixel rectangle so you can relate pixels to data.")
    def chrom_live_image(max_width: int = 1200) -> list[Any]:
        guard("chrom_live_image", {})
        fr = ctx.monitor.latest()
        if fr is None or fr.image is None:
            try:
                fr = ctx.monitor.tick()
            except Exception as e:
                return [TextContent(type="text", text=err(e))]
        png = screen.to_png_bytes(fr.image, max_width)
        meta = {"t": fr.t, "plot_area_px": fr.trace.to_dict(10)["plot_area_px"], "size": fr.image.size,
                "calibrated": fr.trace.calibrated}
        return [ImageContent(type="image", data=base64.b64encode(png).decode(), mimeType="image/png"),
                TextContent(type="text", text=json.dumps(meta))]

    @srv.tool(description="Per-frame history (t, y_last, y_max, changed) for the last N seconds - detect run "
                          "start/end, flow changes, flat-lining.")
    def chrom_live_history(seconds: float = 120, stride: int = 1) -> str:
        guard("chrom_live_history", {"seconds": seconds})
        return json.dumps({"frames": ctx.monitor.history(seconds, stride), "events": ctx.monitor.events()}, default=str)

    @srv.tool(description="Re-run peak detection on the latest frame with explicit parameters.")
    def chrom_live_peaks(min_prominence: float | None = None, smooth_window: int = 5, baseline_window: int | None = None) -> str:
        guard("chrom_live_peaks", {"min_prominence": min_prominence})
        fr = ctx.monitor.latest()
        if fr is None or fr.trace.x.size == 0:
            return json.dumps({"error": "no trace yet; call chrom_live_start / chrom_live_snapshot"})
        peaks, params = find_peaks(fr.trace.x, fr.trace.y, min_prominence=min_prominence,
                                   smooth_window=smooth_window, baseline_window=baseline_window)
        return json.dumps({"peaks": [p.to_dict() for p in peaks], "params": params,
                           "units": list(fr.trace.units), "calibrated": fr.trace.calibrated})

    @srv.tool(description="Tell the monitor what the plot axes span (read them from chrom_live_image), so traces "
                          "come back in minutes / signal units instead of fractions.")
    def chrom_set_axes(x_min: float, x_max: float, y_min: float, y_max: float,
                       x_unit: str = "min", y_unit: str = "signal") -> str:
        guard("chrom_set_axes", {"x": [x_min, x_max], "y": [y_min, y_max]})
        ctx.monitor.set_axes(x_min, x_max, y_min, y_max, x_unit, y_unit)
        return json.dumps({"calibration": ctx.monitor.calibration().to_dict()})

    # ------------------------------------------------------------ Gradient Bench LLM
    @srv.tool(description="State of the Gradient Bench LLM campaign (its /api/state): campaign facts, limits, "
                          "budget, next step and the method in flight, if any.")
    def gbl_state() -> str:
        guard("gbl_state", {})
        try:
            return json.dumps(ctx.gbl_client().state(), default=str)
        except GBLError as e:
            return err(e)

    @srv.tool(description="The GBL method waiting to be run on the instrument (gradient timetable, temperature, "
                          "run number) as a WinGPC operator table. Empty when nothing is in flight or the trace "
                          "has already been uploaded.")
    def gbl_pending_method() -> str:
        guard("gbl_pending_method", {})
        try:
            pend = ctx.gbl_client().pending()
            if not pend:
                return json.dumps({"pending": None})
            m = from_gbl(pend)
            return json.dumps({"pending_step": pend.get("step"), "already_uploaded": bool(pend.get("trace")),
                               "method": m.to_dict(), "table": m.table_text(),
                               "rationale": pend.get("rationale"), "expectation": pend.get("expectation")})
        except (GBLError, ValueError) as e:
            return err(e)

    @srv.tool(description="Upload an ASCII export (a file in the export dir, e.g. from export_ascii) to GBL as the "
                          "trace of the run in flight. GBL validates it and stores it as traces/runNN.txt.")
    def gbl_upload_trace(path: str, original_name: str | None = None) -> str:
        guard("gbl_upload_trace", {"path": path})
        p = Path(path)
        if not p.exists():
            return json.dumps({"error": "file not found", "path": path})
        try:
            pend = ctx.gbl_client().upload(p, original_name)
            return json.dumps({"stored_as": pend.get("trace"), "trace_facts": pend.get("trace_facts"),
                               "step": pend.get("step")}, default=str)
        except GBLError as e:
            return err(e)

    @srv.tool(description="Ask GBL's model to read the uploaded trace (its /api/read job) and return the reading: "
                          "peaks, unresolved regions, verdict vs the best run. Recording stays with the analyst.")
    def gbl_read(timeout_s: float = 600) -> str:
        guard("gbl_read", {})
        try:
            pend = ctx.gbl_client().read(timeout_s)
            return json.dumps({"reading": pend.get("reading"), "compared_to": pend.get("compared_to"),
                               "step": pend.get("step")}, default=str)
        except GBLError as e:
            return err(e)

    @srv.tool(description="HAZARDOUS: run GBL's pending method on WinGPC end to end - apply the method (strategy "
                          "from config: manual/template/recipe), start the sequence, wait for the run, ASCII export, "
                          "upload to GBL and (if auto_read) have GBL read it. Needs a confirm_token. Returns a "
                          "step-by-step report.")
    def gbl_run_cycle(confirm_token: str | None = None) -> str:
        try:
            guard("gbl_run_cycle", {}, confirm_token)
        except WinGPCAgentError as e:
            return err(e)
        # Recipes inside the cycle run under the same confirmation: the cycle itself was approved.
        def run_recipe(name: str, params: dict[str, Any], token: str | None) -> dict[str, Any]:
            rec = ctx.book.get(name)
            tok = token
            if rec.level == "hazardous":
                tok = ctx.gate.issue_token(f"recipe_{name}", f"covered by approved gbl_run_cycle")
            return _run_recipe(name, params, tok)
        try:
            rep = ctx.bridge(run_recipe).run_once()
            return json.dumps(rep.to_dict(), default=str)
        except (WinGPCAgentError, GBLError) as e:
            return err(e)

    @srv.tool(description="Reports of GBL run cycles executed by this server (newest last).")
    def gbl_reports(n: int = 5) -> str:
        guard("gbl_reports", {"n": n})
        b = ctx._bridge
        return json.dumps([r.to_dict() for r in (b.reports[-n:] if b else [])], default=str)

    # ------------------------------------------------------------ files
    @srv.tool(description="Recent files in the export/project directories (newest first).")
    def files_list_recent(limit: int = 30, since_minutes: float | None = None, extensions: list[str] | None = None) -> str:
        guard("files_list_recent", {"limit": limit})
        dirs = ([s.export_dir] if s.export_dir else []) + list(s.project_dirs)
        rows = files_be.list_recent(dirs, extensions or None, limit, since_minutes * 60 if since_minutes else None)
        return json.dumps({"dirs": dirs, "files": rows, "watch_events": ctx._file_events[-20:]}, default=str)

    @srv.tool(description="Parse a WinGPC ASCII export (.txt): tables, columns, ranges, peaks of the first y column. "
                          "Set include_data=true to also get downsampled x/y.")
    def files_read_ascii(path: str, include_data: bool = False, max_points: int = 400) -> str:
        guard("files_read_ascii", {"path": path})
        p = Path(path)
        allowed = [Path(d).resolve() for d in ([s.export_dir] if s.export_dir else []) + list(s.project_dirs) + [s.state_dir]]
        if not p.exists():
            return json.dumps({"error": "file not found", "path": path})
        if allowed and not any(str(p.resolve()).startswith(str(a)) for a in allowed):
            return json.dumps({"error": "path outside export/project dirs", "allowed": [str(a) for a in allowed]})
        try:
            return json.dumps(_parse_summary(str(p), include_data, max_points), default=str)
        except Exception as e:
            return err(e)

    @srv.tool(description="Last N audit-log entries (what the agent did, what was denied).")
    def audit_tail(n: int = 30) -> str:
        guard("audit_tail", {"n": n})
        p = Path(s.audit_log)
        if not p.exists():
            return "[]"
        lines = p.read_text(encoding="utf-8").splitlines()[-n:]
        return "[" + ",".join(lines) + "]"

    def _parse_summary(path: str, include_data: bool = False, max_points: int = 400) -> dict[str, Any]:
        tables = ascii_parser.parse_file(path)
        out: dict[str, Any] = {"path": path, "n_tables": len(tables), "tables": []}
        for t in tables:
            d = t.summary()
            if t.data.shape[0] > 5 and t.data.shape[1] >= 2:
                peaks, params = find_peaks(t.x, t.y(1))
                d["peaks_col1"] = [p.to_dict() for p in peaks[:30]]
                d["peak_params"] = params
                d["describe_col1"] = describe(t.x, t.y(1))
                if include_data:
                    from .chromatogram.analysis import downsample
                    xs, ys = downsample(t.x, t.y(1), max_points)
                    d["x"], d["y"] = xs, ys
            out["tables"].append(d)
        return out

    srv._wingpc_ctx = ctx   # type: ignore[attr-defined]  (tests / CLI introspection)
    return srv
