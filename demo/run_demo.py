"""End-to-end demo: Gradient Bench LLM  <->  control layer  <->  simulated LC.

    python demo/run_demo.py --runs 5 --gbl-root ../gradient-bench-llm

What happens, N times:
  1. GBL (offline demo mode, its own Flask app over real HTTP) proposes a method
  2. the bridge reads it from /api/state and applies it to the instrument
     through the `apply_gradient_method` recipe parameters (recipe strategy)
  3. the simulated LC runs the gradient; the bridge watches the status text,
     the live trace and the pump pressure (warn / abort limits)
  4. the instrument's ASCII export (time, UV, pressure) is validated, reduced to
     time + UV because it has three columns, and uploaded to /api/upload
  5. GBL's demo reader reads it and gives a verdict against the best run
  6. the verdict is recorded (in the demo only; in the lab the analyst does this)
Output: a table on stdout, demo/out/summary.md, one PNG per run and the
campaign folder with GBL's own record (runs.csv, traces/, transcript.jsonl).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "tests"))

from wingpc_agent.gbl.bridge import Bridge, BridgeConfig, Instrument          # noqa: E402
from wingpc_agent.gbl.client import GBLError, HttpClient                       # noqa: E402
from wingpc_agent.sim.instrument import SimConfig, SimInstrument              # noqa: E402


def start_gbl(root: Path, campaign_dir: Path, port: int, budget: int):
    sys.path.insert(0, str(root))
    from gradient_llm.api.app import create_app
    from gradient_llm.store import campaign as C
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    info = {"name": "demo-wingpc-bridge", "sample": "seven-component test mix (uracil + A..F)",
            "column": "C18 100 x 2.1 mm, 1.8 um", "mobile_phase_a": "water + 0.1 % formic acid",
            "mobile_phase_b": "acetonitrile", "flow_rate": "1.0 mL/min", "detector": "UV 254 nm",
            "instrument": "simulated LC behind WinGPC UniChrom (wingpc-agent demo)",
            "fixed_method": "2 min re-equilibration after the last node is counted in the run",
            "notes": "The peak near 0.55 min is the injection disturbance, uracil marks t0 at about 1 min."}
    camp = C.create(campaign_dir, info, budget=budget,
                    limits={"start_b": [5, 30], "end_b": [60, 95], "duration_min": [8, 20], "temperature_c": [30, 50]})
    app = create_app(demo=True)
    app.config["OPEN"]["campaign"] = camp
    th = threading.Thread(target=lambda: app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False),
                          daemon=True)
    th.start()
    return camp


def make_instrument(sim: SimInstrument, export_dir: Path, events: list[str]) -> Instrument:
    def run_recipe(name: str, params: dict, token):
        events.append(f"recipe {name}")
        if name == "apply_gradient_method":
            nodes = [{"time_min": float(params[f"t{i}"]), "pct_b": float(params[f"b{i}"])}
                     for i in range(10) if params.get(f"t{i}") not in ("", None)]
            sim.apply_method({"gradient": nodes, "temperature_c": float(params["temperature_c"])})
        elif name == "sequence_start":
            sim.start()
        elif name == "sequence_stop":
            sim.stop()
        elif name == "ascii_export_raw_data":
            sim.export_ascii(params["path"])
        else:
            return {"ok": False, "log": [{"error": f"unknown recipe {name}"}]}
        return {"ok": True, "log": [{"step": 0, "kind": "sim", "ok": True}]}

    return Instrument(run_recipe=run_recipe, read_text=lambda path: sim.status(), read_pressure=sim.pressure_bar,
                      monitor_snapshot=sim.snapshot, monitor_start=lambda: None,
                      notify_operator=lambda m: events.append(m), wait_operator=None)


def render(sim: SimInstrument, rep, out_png: Path) -> None:
    from PIL import Image, ImageDraw
    x, y, p = sim.trace_arrays()
    if x.size < 10:
        return
    from synth import render_plot
    img, _, box = render_plot(x, y, size=(1000, 420), trace_rgb=(0, 0, 200))
    d = ImageDraw.Draw(img)
    l, t, r, b = box
    pr = p / max(1.0, p.max())
    pts = [(l + xi / x[-1] * (r - l), b - pri * (b - t) * 0.95) for xi, pri in zip(x, pr)]
    d.line(pts, fill=(200, 60, 0), width=1)
    d.text((l + 6, t + 4), f"run {rep.run_no}: {rep.method.get('describe')}   blue = UV 254 [mAU], orange = pressure (max {p.max():.0f} bar)", fill=(0, 0, 0))
    for pk in sim.expected_peaks():
        px = l + pk["t_r"] / x[-1] * (r - l)
        d.text((px - 4, b - 14), pk["name"][0], fill=(90, 90, 90))
    img.save(out_png)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--gbl-root", default=str(HERE.parent.parent / "gradient-bench-llm"))
    ap.add_argument("--port", type=int, default=5099)
    ap.add_argument("--accel", type=float, default=6.0, help="simulated minutes per real second")
    ap.add_argument("--pressure-max", type=float, default=400.0)
    ap.add_argument("--pressure-warn", type=float, default=260.0)
    ap.add_argument("--spike-run", type=int, default=0, help="inject a pressure spike (blockage) in this run number to show the abort")
    ap.add_argument("--out", default=str(HERE / "out"))
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    camp_parent = Path(tempfile.mkdtemp(prefix="gbl-demo-"))
    camp = start_gbl(Path(a.gbl_root), camp_parent, a.port, budget=a.runs)
    client = HttpClient(f"http://127.0.0.1:{a.port}")
    for _ in range(50):                         # wait for Flask
        try:
            client.state()
            break
        except GBLError:
            time.sleep(0.1)

    sim = SimInstrument(SimConfig(accel=a.accel))
    events: list[str] = []
    inst = make_instrument(sim, out / "exports", events)
    cfg = BridgeConfig(method_strategy="recipe", export_dir=str(out / "exports"), fixed_extra_min=sim.cfg.post_time_min,
                       status_path="sim|Text:Status", detector_column_re="UV", auto_read=True, confirm_each_run=False,
                       time_scale=a.accel * 60, watch_poll_s=0.2, min_elapsed_fraction=0.5, flat_seconds_means_done=2.0,
                       pressure_warn_bar=a.pressure_warn, pressure_max_bar=a.pressure_max)
    bridge = Bridge(client, inst, cfg)

    rows = []
    print(f"GBL campaign: {camp.root}\n")
    for i in range(1, a.runs + 1):
        sim.cfg.pressure_spike = (3.0, 6.0, 300.0) if i == a.spike_run else None
        t_start = time.time()
        pend = client.suggest()                       # 1. GBL proposes (demo asker)
        m_desc = pend.get("describe") or json.dumps(pend.get("method"))
        print(f"run {i}: GBL proposes  {m_desc}")
        rep = bridge.run_once()                       # 2-5. apply, run, watch, export, upload, read
        for s in rep.steps:
            print(f"   [{'ok' if s.ok else 'FAIL'}] {s.step}: {s.detail[:110]}")
        watch = next((s for s in rep.steps if s.step in ("watch", "pressure_abort")), None)
        pstats = (watch.data.get("pressure") if watch else None) or {}
        verdict = None
        if rep.ok:
            res = client.record()                     # 6. record (demo only)
            verdict = res["run"]["verdict"]
            print(f"   recorded: verdict {verdict} (by {res['run']['verdict_by']}), best run is now {res['best']}")
        else:
            client._json("/api/discard")
            print("   run discarded in GBL (export moved to traces/discarded)")
        if rep.export_path:
            render(sim, rep, out / f"run{i:02d}.png")
        rows.append({"run": i, "method": m_desc, "ok": rep.ok, "verdict": verdict,
                     "n_peaks_read": (rep.reading or {}).get("n_peaks"), "p_max_bar": pstats.get("max_bar"),
                     "sim_peaks": [p["t_r"] for p in sim.expected_peaks()], "seconds": round(time.time() - t_start, 1),
                     "export": rep.export_path, "uploaded_as": rep.uploaded_as})
        print()

    runs = json.loads((camp.root / "runs.json").read_text())
    best = json.loads((camp.root / "campaign.json").read_text()).get("best")
    lines = ["# Demo: Gradient Bench LLM -> wingpc-agent bridge -> simulated LC", "",
             f"campaign folder: `{camp.root}`  (GBL's own record: runs.json, runs.csv, traces/, transcript.jsonl)", "",
             "| run | method (GBL proposal) | bridge | pressure max [bar] | GBL reading: peaks | verdict | s |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['run']} | {r['method']} | {'complete' if r['ok'] else 'aborted'} | {r['p_max_bar']} | "
                     f"{r['n_peaks_read']} | {r['verdict'] or '-'} | {r['seconds']} |")
    lines += ["", f"best run according to GBL: **{best}**", "",
              f"recorded runs in GBL: {len(runs)}; exports in `{out / 'exports'}`; plots `run01.png`..",
              "", "Simulated true retention times (min) per run:", ""]
    for r in rows:
        lines.append(f"- run {r['run']}: {r['sim_peaks']}")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
