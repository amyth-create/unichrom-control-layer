"""Translate a Gradient Bench LLM method into what WinGPC / ChromPilot needs.

A GBL method is

    {"gradient": [{"time_min": 0, "pct_b": 5}, {"time_min": 20, "pct_b": 95}],
     "temperature_c": 40}

WinGPC's ChromPilot controls the LC modules through Agilent RC.Net drivers; the
pump timetable and the column oven setpoint live in the *instrument method*
that a sequence line references. How that method is edited depends on the
installation, so three strategies exist (see bridge.MethodApplier):

  manual    print the table; the operator keys it in; the bridge continues
  template  render a method file from a template the operator exported once
            (placeholders below), then a recipe loads it
  recipe    a verified UI recipe fills the timetable grid directly

Placeholders for the template strategy (Python str.format on the file text):
  {temperature_c}      column oven setpoint
  {duration_min}       time of the last node
  {start_b} {end_b}    %B at the first / last node
  {start_a} {end_a}    100 - %B
  {n_rows}             number of timetable rows
  {timetable_rows}     one line per node, rendered with `row_format`
  {b0}.. {b9}, {t0}.. {t9}   %B and time of node i (blank when absent)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass
class GradientRow:
    time_min: float
    pct_b: float

    @property
    def pct_a(self) -> float:
        return round(100.0 - self.pct_b, 6)


@dataclass
class Method:
    rows: list[GradientRow]
    temperature_c: float
    run_no: int | None = None
    describe: str = ""
    extra: dict[str, Any] = field(default_factory=dict)   # fixed parts the campaign states (flow, post time...)

    @property
    def duration_min(self) -> float:
        return self.rows[-1].time_min

    @property
    def start_b(self) -> float:
        return self.rows[0].pct_b

    @property
    def end_b(self) -> float:
        return self.rows[-1].pct_b

    def key(self) -> tuple:
        return (tuple((round(r.time_min, 4), round(r.pct_b, 4)) for r in self.rows), round(self.temperature_c, 4))

    def table_text(self) -> str:
        """The operator view, identical in wording to GBL's gradient table."""
        lines = [f"{'time [min]':>10}  {'%B':>6}  {'%A':>6}"]
        for r in self.rows:
            lines.append(f"{r.time_min:10.1f}  {r.pct_b:6.1f}  {r.pct_a:6.1f}")
        lines.append(f"column temperature: {self.temperature_c:g} °C")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {"gradient": [{"time_min": r.time_min, "pct_b": r.pct_b} for r in self.rows],
                "temperature_c": self.temperature_c, "run": self.run_no, "describe": self.describe,
                "duration_min": self.duration_min}


def from_gbl(pending: Mapping[str, Any]) -> Method:
    """Build a Method from GBL's pending run (``/api/state`` -> ``pending`` or ``state.json``)."""
    m = pending.get("method") or pending
    nodes = m.get("gradient")
    if not isinstance(nodes, list) or len(nodes) < 2:
        raise ValueError("GBL method needs at least two gradient nodes")
    rows = [GradientRow(float(n["time_min"]), float(n["pct_b"])) for n in nodes]
    rows.sort(key=lambda r: r.time_min)
    if abs(rows[0].time_min) > 1e-9:
        raise ValueError("first gradient node must be at 0 min")
    for a, b in zip(rows, rows[1:]):
        if b.pct_b < a.pct_b - 1e-9:
            raise ValueError("%B must not fall along the gradient")
    return Method(rows, float(m["temperature_c"]), pending.get("run"), str(pending.get("describe") or ""))


def expected_run_minutes(method: Method, fixed_extra_min: float = 0.0, post_time_min: float | None = None) -> float:
    """How long the acquisition should take: the gradient plus whatever the
    campaign runs around it (re-equilibration, wash) that the data system
    counts in the run time. Both come from config; the default is the gradient."""
    extra = fixed_extra_min if post_time_min is None else fixed_extra_min + post_time_min
    return method.duration_min + max(0.0, extra)


def render_template(template_text: str, method: Method, row_format: str = "{time_min:.2f}\t{pct_a:.1f}\t{pct_b:.1f}",
                    row_sep: str = "\n") -> str:
    """Fill a method-file template. Unknown braces in the template are left alone
    by escaping them first, so XML/INI files with their own braces survive."""
    fields: dict[str, Any] = {
        "temperature_c": method.temperature_c, "duration_min": method.duration_min,
        "start_b": method.start_b, "end_b": method.end_b,
        "start_a": round(100 - method.start_b, 6), "end_a": round(100 - method.end_b, 6),
        "n_rows": len(method.rows),
        "timetable_rows": row_sep.join(row_format.format(time_min=r.time_min, pct_b=r.pct_b, pct_a=r.pct_a)
                                       for r in method.rows),
    }
    for i in range(10):
        fields[f"b{i}"] = f"{method.rows[i].pct_b:g}" if i < len(method.rows) else ""
        fields[f"t{i}"] = f"{method.rows[i].time_min:g}" if i < len(method.rows) else ""
    fields.update(method.extra)
    safe = template_text.replace("{", "{{").replace("}", "}}")
    for k in fields:
        safe = safe.replace("{{" + k + "}}", "{" + k + "}")
    return safe.format(**fields)


def write_method_file(template_path: str | Path, out_dir: str | Path, method: Method, **kw: Any) -> Path:
    tpl = Path(template_path)
    text = render_template(tpl.read_text(encoding="utf-8"), method, **kw)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"gbl_run{method.run_no or 0:02d}_{method.start_b:g}-{method.end_b:g}B_{method.duration_min:g}min{tpl.suffix}"
    out = out_dir / name
    out.write_text(text, encoding="utf-8")
    return out
