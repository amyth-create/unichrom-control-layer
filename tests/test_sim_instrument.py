import time

import numpy as np

from wingpc_agent.chromatogram import ascii_parser
from wingpc_agent.chromatogram.analysis import find_peaks
from wingpc_agent.gbl.bridge import Bridge, BridgeConfig
from wingpc_agent.sim.instrument import SimConfig, SimInstrument
from tests.test_gbl import FakeGBL, PENDING

FAST = SimConfig(accel=60.0, sample_hz=5.0, post_time_min=0.5)   # 1 simulated minute per 1/60 s


def _run(sim, method):
    sim.apply_method(method)
    sim.start()
    while "Running" in sim.status():
        time.sleep(0.02)


def test_gradient_changes_retention_and_export_parses(tmp_path):
    sim = SimInstrument(FAST)
    shallow = {"gradient": [{"time_min": 0, "pct_b": 5}, {"time_min": 20, "pct_b": 60}], "temperature_c": 30}
    steep = {"gradient": [{"time_min": 0, "pct_b": 30}, {"time_min": 8, "pct_b": 95}], "temperature_c": 50}
    _run(sim, shallow)
    tr_shallow = [p["t_r"] for p in sim.expected_peaks()]
    assert "Finished" in sim.status()
    p = sim.export_ascii(tmp_path / "shallow.txt")
    t = ascii_parser.parse_file(str(p))[0]
    assert t.columns == ["Time [min]", "UV 254 [mAU]", "Pressure [bar]"] and t.data.shape[0] > 2000
    peaks, _ = find_peaks(t.x, t.y(1), min_prominence=5)
    assert len(peaks) >= 6, [pk.x for pk in peaks]
    _run(sim, steep)
    tr_steep = [p["t_r"] for p in sim.expected_peaks()]
    assert tr_steep[-1] < tr_shallow[-1]          # steeper + hotter elutes earlier
    assert all(b > a for a, b in zip(tr_shallow, tr_shallow[1:]))   # shallow: elution order A..F preserved
    gap = lambda tr, i: abs(tr[i + 1] - tr[i])
    assert gap(tr_steep, 2) < gap(tr_shallow, 2) and gap(tr_steep, 4) < gap(tr_shallow, 4)   # B/C and D/E pairs close up


def test_pressure_follows_viscosity_curve():
    sim = SimInstrument(SimConfig(pressure_noise_bar=0.0))
    sim.apply_method({"gradient": [{"time_min": 0, "pct_b": 0}, {"time_min": 10, "pct_b": 100}], "temperature_c": 30})
    p = [sim._pressure(t) for t in (0.0, 2.5, 5.0, 10.0)]        # 0, 25, 50, 100 %B
    assert p[1] > p[0] > p[3] and p[0] == 180.0 and p[3] < 100
    sim.apply_method({"gradient": [{"time_min": 0, "pct_b": 0}, {"time_min": 10, "pct_b": 0}], "temperature_c": 50})
    assert sim._pressure(0.0) < 180.0                             # hotter -> lower


def _instrument(sim, export_dir):
    from wingpc_agent.gbl.bridge import Instrument
    calls = []
    def run_recipe(name, params, token):
        calls.append(name)
        if name == "apply_gradient_method":
            nodes = [{"time_min": float(params[f"t{i}"]), "pct_b": float(params[f"b{i}"])}
                     for i in range(10) if params.get(f"t{i}")]
            sim.apply_method({"gradient": nodes, "temperature_c": float(params["temperature_c"])})
        elif name == "sequence_start":
            sim.start()
        elif name == "sequence_stop":
            sim.stop()
        elif name == "ascii_export_raw_data":
            sim.export_ascii(params["path"])
        return {"ok": True, "log": []}
    inst = Instrument(run_recipe=run_recipe, read_text=lambda p: sim.status(), read_pressure=sim.pressure_bar,
                      monitor_snapshot=sim.snapshot, notify_operator=lambda m: None)
    return inst, calls


def test_bridge_runs_sim_with_recipe_strategy_and_normalises(tmp_path):
    sim = SimInstrument(FAST)
    gbl = FakeGBL(PENDING)
    inst, calls = _instrument(sim, tmp_path)
    cfg = BridgeConfig(method_strategy="recipe", export_dir=str(tmp_path), fixed_extra_min=FAST.post_time_min,
                       status_path="sim", detector_column_re="UV", auto_read=False, confirm_each_run=False,
                       time_scale=FAST.accel * 60, watch_poll_s=0.05, min_elapsed_fraction=0.5,
                       pressure_warn_bar=150, pressure_max_bar=1000)
    rep = Bridge(gbl, inst, cfg).run_once()
    assert rep.ok, rep.to_dict()
    assert calls[:2] == ["apply_gradient_method", "sequence_start"] and "ascii_export_raw_data" in calls
    assert sim.method["gradient"][-1]["pct_b"] == 95 and sim.method["temperature_c"] == 40
    steps = {s.step: s for s in rep.steps}
    assert "pressure_warning" in steps
    assert steps["watch"].data["pressure"]["samples"] > 5 and steps["watch"].data["pressure"]["max_bar"] > 150
    assert gbl.uploads[0][0].endswith("_2col.txt")              # three columns -> time + UV


def test_bridge_aborts_on_pressure_spike(tmp_path):
    sim = SimInstrument(SimConfig(accel=60.0, sample_hz=10.0, post_time_min=0.5, pressure_spike=(2.0, 20.0, 400.0)))
    gbl = FakeGBL(PENDING)
    inst, calls = _instrument(sim, tmp_path)
    cfg = BridgeConfig(method_strategy="recipe", export_dir=str(tmp_path), status_path="sim", auto_read=False,
                       confirm_each_run=False, time_scale=60 * 60, watch_poll_s=0.02, min_elapsed_fraction=0.5,
                       pressure_max_bar=400)
    rep = Bridge(gbl, inst, cfg).run_once()
    assert not rep.ok
    assert "pressure_abort" in [s.step for s in rep.steps] and "sequence_stop" in calls
    assert "Stopped" in sim.status() and not gbl.uploads
