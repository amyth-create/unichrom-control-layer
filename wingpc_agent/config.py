"""Runtime settings. Loaded from (in order) defaults -> YAML file -> environment.

Everything the probe cannot discover automatically lives here so it can be
corrected by hand after the first `wingpc-agent probe` run on the WinGPC PC.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml


@dataclass
class PlotRegion:
    """Where the live chromatogram is drawn, and how pixels map to data units.

    `control_path` is a UIA path (see recipes) to the plot control; if it is
    found at runtime, its rectangle wins over `rect`. `rect` is a fallback
    screen rectangle [left, top, right, bottom] for apps whose plot is a bare
    custom-drawn window with no UIA identity.
    """

    window_title_re: str = r".*(Raw ?Data|Rohdaten|Acquisition|Online).*"
    control_path: list[str] = field(default_factory=list)
    rect: list[int] | None = None
    # Axis calibration. If None, values are reported in normalised plot
    # coordinates (0..1) and the LLM is told so explicitly.
    x_min: float | None = None   # e.g. 0.0 min
    x_max: float | None = None   # e.g. 30.0 min
    y_min: float | None = None   # e.g. -5 mV
    y_max: float | None = None   # e.g. 500 mV
    x_unit: str = "min"
    y_unit: str = "signal"
    # Colour of the trace to follow (RGB). None = pick the dominant non-grey,
    # non-background colour automatically.
    trace_rgb: list[int] | None = None
    color_tolerance: int = 60


@dataclass
class GBLSettings:
    """Gradient Bench LLM bridge (see wingpc_agent/gbl)."""

    enabled: bool = False
    url: str = "http://127.0.0.1:5052"       # GBL's Flask app; or set campaign_dir for folder mode
    campaign_dir: str | None = None
    poll_s: float = 5.0
    method_strategy: str = "manual"           # manual | template | recipe
    template_file: str | None = None
    method_out_dir: str | None = None
    fixed_extra_min: float = 0.0              # wash/re-equilibration time counted in the acquisition
    post_time_min: float | None = None
    status_path: str | None = None            # UIA path of ChromPilot's run-state text (from probe)
    detector_column_re: str | None = None     # e.g. "UV|254" to pick the detector column on normalise
    upload_format: str = "original"           # original | normalised
    auto_read: bool = True                    # call GBL /api/read after the upload
    confirm_each_run: bool = True             # operator must say yes before an injection starts


@dataclass
class Settings:
    # Process / window identification
    process_names: list[str] = field(default_factory=lambda: ["WinGPC.exe", "WinGPC", "ChromPilot.exe"])
    main_window_title_re: str = r".*WinGPC.*"
    # Where WinGPC keeps projects / where ASCII exports land. Filled by probe.
    project_dirs: list[str] = field(default_factory=list)
    export_dir: str | None = None
    export_extensions: list[str] = field(default_factory=lambda: [".txt", ".csv", ".asc", ".dat"])
    # Live monitor
    poll_hz: float = 2.0
    ring_seconds: int = 7200
    plot: PlotRegion = field(default_factory=PlotRegion)
    gbl: GBLSettings = field(default_factory=GBLSettings)
    # Recipes (verified UI paths) and the safety policy
    recipes_file: str = str(Path(__file__).resolve().parent.parent / "recipes" / "wingpc_default.yaml")
    policy_file: str = str(Path(__file__).resolve().parent.parent / "recipes" / "policy_default.yaml")
    audit_log: str = str(Path.home() / ".wingpc-agent" / "audit.jsonl")
    state_dir: str = str(Path.home() / ".wingpc-agent")
    # Set True once a human confirms the recipes work on this installation.
    allow_unverified_recipes: bool = False

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Settings":
        path = path or os.environ.get("WINGPC_AGENT_CONFIG")
        data: dict[str, Any] = {}
        if path and Path(path).exists():
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        plot = PlotRegion(**data.pop("plot", {}) or {})
        gbl = GBLSettings(**data.pop("gbl", {}) or {})
        s = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        s.plot = plot
        s.gbl = gbl
        # Environment overrides for the few things people change often.
        if os.environ.get("WINGPC_EXPORT_DIR"):
            s.export_dir = os.environ["WINGPC_EXPORT_DIR"]
        if os.environ.get("WINGPC_POLL_HZ"):
            s.poll_hz = float(os.environ["WINGPC_POLL_HZ"])
        if os.environ.get("GBL_URL"):
            s.gbl.url = os.environ["GBL_URL"]
            s.gbl.enabled = True
        Path(s.state_dir).mkdir(parents=True, exist_ok=True)
        return s

    def dump(self, path: str | os.PathLike) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(asdict(self), fh, sort_keys=False)
