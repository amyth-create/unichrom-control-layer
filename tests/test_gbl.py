"""Gradient Bench LLM bridge.

The integration test drives the REAL gradient_llm package (checked out next to
this repo, or installed) through its Flask app with a test-client transport,
so the bridge is exercised against GBL's actual /api/state, /api/upload and
/api/read (demo asker) - no network, no model, no Windows. If gradient_llm is
not importable the integration test is skipped and the unit tests still run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from tests.synth import gaussian_chrom
from wingpc_agent.gbl.bridge import Bridge, BridgeConfig, Instrument, RunReport
from wingpc_agent.gbl.client import HttpClient
from wingpc_agent.gbl.method import Method, GradientRow, expected_run_minutes, from_gbl, render_template, write_method_file

PENDING = {"run": 3, "source": "llm", "step": "upload", "trace": None, "describe": "5 to 95 %B in 20 min, 40 °C",
           "method": {"gradient": [{"time_min": 0, "pct_b": 5}, {"time_min": 15, "pct_b": 60},
                                   {"time_min": 20, "pct_b": 95}], "temperature_c": 40}}


# ------------------------------------------------------------------ method
def test_from_gbl_and_table():
    m = from_gbl(PENDING)
    assert m.run_no == 3 and m.duration_min == 20 and m.start_b == 5 and m.end_b == 95
    txt = m.table_text()
    assert "%B" in txt and "40 °C" in txt and "15.0" in txt
    assert expected_run_minutes(m, fixed_extra_min=5) == 25


def test_from_gbl_rejects_falling_b():
    bad = {"method": {"gradient": [{"time_min": 0, "pct_b": 50}, {"time_min": 10, "pct_b": 20}], "temperature_c": 30}}
    with pytest.raises(ValueError):
        from_gbl(bad)


def test_render_template_keeps_foreign_braces(tmp_path):
    tpl = ("<Method><Oven>{temperature_c}</Oven><Pump rows=\"{n_rows}\">\n{timetable_rows}\n</Pump>"
           "<Keep>{not_a_field}</Keep><B2>{b2}</B2><B5>[{b5}]</B5></Method>")
    m = from_gbl(PENDING)
    out = render_template(tpl, m, row_format="<Row t=\"{time_min:.1f}\" b=\"{pct_b:.0f}\"/>")
    assert "<Oven>40.0</Oven>" in out and 'rows="3"' in out
    assert '<Row t="15.0" b="60"/>' in out
    assert "{not_a_field}" in out and "<B2>95</B2>" in out and "<B5>[]</B5>" in out
    (tmp_path / "t.xml").write_text(tpl)
    p = write_method_file(tmp_path / "t.xml", tmp_path / "out", m)
    assert p.exists() and p.suffix == ".xml" and "run03" in p.name


# ------------------------------------------------------------------ fakes
class FakeGBL:
    """Minimal GBL: one pending method; upload/read mutate it like the real loop."""
    def __init__(self, pending):
        self.pend = dict(pending)
        self.uploads = []
    def state(self):
        return {"campaign": {"name": "fake"}, "pending": self.pend}
    def pending(self):
        return self.pend
    def upload(self, path, original_name=None):
        self.uploads.append((str(path), original_name))
        self.pend = {**self.pend, "trace": f"run{self.pend['run']:02d}.txt", "step": "read",
                     "trace_facts": {"points": 123}}
        return self.pend
    def read(self, timeout_s=600):
        self.pend = {**self.pend, "reading": {"n_peaks": 3, "verdict": "better", "summary": "three peaks"}, "step": "check"}
        return self.pend


def fake_instrument(tmp_path, export_writer, calls, snapshots=None):
    def run_recipe(name, params, token):
        calls.append((name, params, token))
        if name == "ascii_export_raw_data":
            export_writer(params["path"])
        return {"ok": True, "log": [{"step": 0, "ok": True}]}
    it = iter(snapshots or [])
    def snap():
        try:
            return next(it)
        except StopIteration:
            return {"changed_since_previous": False, "trace": {"calibrated": True, "x": [30.0]}}
    return Instrument(run_recipe=run_recipe, monitor_snapshot=snap, monitor_start=lambda: None,
                      notify_operator=lambda m: None, wait_operator=None)


def write_export(path, cols=2, n=600):
    x, y = gaussian_chrom(n=n)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("WinGPC ASCII export\nSample: PS\n\n")
        if cols == 2:
            fh.write("Time [min]\tRI [mV]\n" + "\n".join(f"{a:.4f}\t{b:.4f}" for a, b in zip(x, y)))
        else:
            fh.write("Time [min]\tRI [mV]\tUV 254 [mAU]\tPressure [bar]\n" +
                     "\n".join(f"{a:.4f}\t{b:.4f}\t{b*0.5:.4f}\t{80:.1f}" for a, b in zip(x, y)))


# ------------------------------------------------------------------ bridge with fakes
def test_bridge_cycle_manual_strategy(tmp_path):
    gbl = FakeGBL(PENDING)
    calls = []
    inst = fake_instrument(tmp_path, write_export, calls)
    cfg = BridgeConfig(method_strategy="manual", export_dir=str(tmp_path), run_timeout_margin_min=0.01,
                       flat_seconds_means_done=0, min_elapsed_fraction=0, confirm_each_run=False)
    rep = Bridge(gbl, inst, cfg).run_once()
    steps = [s.step for s in rep.steps]
    assert rep.ok, rep.to_dict()
    assert steps == ["apply_method", "start", "started_at", "watch", "export", "validate", "upload", "read"]
    assert [c[0] for c in calls] == ["sequence_start", "ascii_export_raw_data"]
    assert gbl.uploads and gbl.uploads[0][0].endswith(".txt") and "_2col" not in gbl.uploads[0][0]
    assert rep.uploaded_as == "run03.txt" and rep.reading["verdict"] == "better"


def test_bridge_normalises_multicolumn_export(tmp_path):
    gbl = FakeGBL(PENDING)
    calls = []
    inst = fake_instrument(tmp_path, lambda p: write_export(p, cols=4), calls)
    cfg = BridgeConfig(method_strategy="manual", export_dir=str(tmp_path), flat_seconds_means_done=0, min_elapsed_fraction=0,
                       detector_column_re="UV", confirm_each_run=False, auto_read=False)
    rep = Bridge(gbl, inst, cfg).run_once()
    assert rep.ok
    up = gbl.uploads[0][0]
    assert up.endswith("_2col.txt")
    lines = Path(up).read_text().splitlines()
    assert any("UV 254" in l for l in lines[:6])
    data = [l for l in lines if l and not l.startswith("#") and l[0].isdigit()]
    assert len(data) == 600 and len(data[0].split("\t")) == 2


def test_bridge_template_strategy_calls_load_recipe(tmp_path):
    (tmp_path / "m.tpl").write_text("T={temperature_c}\n{timetable_rows}\n")
    gbl = FakeGBL(PENDING)
    calls = []
    inst = fake_instrument(tmp_path, write_export, calls)
    cfg = BridgeConfig(method_strategy="template", template_file=str(tmp_path / "m.tpl"),
                       method_out_dir=str(tmp_path / "methods"), export_dir=str(tmp_path),
                       flat_seconds_means_done=0, min_elapsed_fraction=0, auto_read=False, confirm_each_run=False)
    rep = Bridge(gbl, inst, cfg).run_once()
    assert rep.ok, rep.to_dict()
    assert calls[0][0] == "load_instrument_method" and calls[0][1]["path"].endswith(".tpl")
    assert "T=40.0" in Path(calls[0][1]["path"]).read_text()


def test_bridge_nothing_pending():
    gbl = FakeGBL({**PENDING, "trace": "run03.txt", "step": "read"})
    rep = Bridge(gbl, fake_instrument(None, write_export, []), BridgeConfig()).run_once()
    assert not rep.ok and rep.steps[0].step == "poll"


def test_bridge_watch_uses_status_text(tmp_path):
    gbl = FakeGBL(PENDING)
    calls = []
    texts = iter(["Running", "Running", "Finished"])
    inst = fake_instrument(tmp_path, write_export, calls,
                           snapshots=[{"changed_since_previous": True, "trace": {"calibrated": False}}] * 10)
    inst.read_text = lambda path: next(texts, "Finished")
    cfg = BridgeConfig(method_strategy="manual", export_dir=str(tmp_path), status_path="x|Text:Status",
                       auto_read=False, confirm_each_run=False, flat_seconds_means_done=1000, min_elapsed_fraction=0)
    m = Method([GradientRow(0, 5), GradientRow(0.01, 95)], 30, 3)   # 0.01 min run so the test is fast
    rep = Bridge(gbl, inst, cfg).run_once(method=m)
    w = next(s for s in rep.steps if s.step == "watch")
    assert w.ok and "status text" in w.detail


def test_bridge_start_failure_aborts_before_export(tmp_path):
    gbl = FakeGBL(PENDING)
    def run_recipe(name, params, token):
        return {"ok": False, "log": [{"step": 1, "ok": False, "error": "ElementNotFound"}]}
    inst = Instrument(run_recipe=run_recipe, notify_operator=lambda m: None)
    rep = Bridge(gbl, inst, BridgeConfig(export_dir=str(tmp_path), confirm_each_run=False, min_elapsed_fraction=0)).run_once()
    assert not rep.ok and rep.steps[-1].step == "abort" and not gbl.uploads


# ------------------------------------------------------------------ HTTP client against the real GBL app
def _gbl_root():
    for cand in (Path(__file__).resolve().parents[2] / "gradient-bench-llm", Path.home() / "gradient-bench-llm"):
        if (cand / "gradient_llm").is_dir():
            return cand
    return None


@pytest.fixture
def gbl_app(tmp_path, monkeypatch):
    root = _gbl_root()
    if root and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    pytest.importorskip("gradient_llm")
    pytest.importorskip("flask")
    from gradient_llm.api.app import create_app
    from gradient_llm.store import campaign as C
    monkeypatch.setattr(C, "REGISTRY", tmp_path / "reg" / "recent.json")
    info = {"name": "bridge test", "sample": "mix", "column": "C18", "mobile_phase_a": "water", "mobile_phase_b": "MeCN"}
    camp = C.create(tmp_path, info, budget=3)
    app = create_app(demo=True)
    app.config["OPEN"]["campaign"] = camp
    return app, camp


def flask_transport(app):
    tc = app.test_client()
    def t(method, url, body, headers):
        path = url.split("://", 1)[-1].split("/", 1)[1]
        if method == "GET":
            r = tc.get("/" + path)
        else:
            r = tc.post("/" + path, data=body, headers=headers)
        return r.status_code, r.data
    return t


def test_http_client_against_real_gbl(gbl_app, tmp_path):
    app, camp = gbl_app
    client = HttpClient("http://gbl.test", transport=flask_transport(app))
    st = client.state()
    assert st["campaign"]["name"] == "bridge test" and client.pending() is None
    # the analyst enters a method -> GBL puts it in flight
    r = app.test_client().post("/api/manual", json={"method": {"gradient": [{"time_min": 0, "pct_b": 10},
                                                                                 {"time_min": 15, "pct_b": 90}],
                                                                    "temperature_c": 35}})
    assert r.status_code == 200, r.data
    pend = client.pending()
    assert pend["step"] == "upload" and pend["run"] == 1
    m = from_gbl(pend)
    assert m.duration_min == 15 and m.temperature_c == 35

    # the bridge runs it on the "instrument" and uploads the export
    calls = []
    inst = fake_instrument(tmp_path, write_export, calls)
    cfg = BridgeConfig(method_strategy="manual", export_dir=str(tmp_path / "exports"), flat_seconds_means_done=0, min_elapsed_fraction=0,
                       confirm_each_run=False, auto_read=True)
    rep = Bridge(client, inst, cfg).run_once()
    assert rep.ok, json.dumps(rep.to_dict(), default=str)
    assert rep.uploaded_as == "run01.txt"
    stored = camp.traces / "run01.txt"
    assert stored.exists() and stored.read_bytes() == Path(rep.export_path).read_bytes()   # byte for byte
    # GBL's demo asker read it: a reading with peaks is on the pending run
    pend = client.pending()
    assert pend["step"] == "check" and pend["reading"]["n_peaks"] >= 3
    # a second cycle must not re-run: the trace is uploaded
    rep2 = Bridge(client, inst, cfg).run_once()
    assert not rep2.ok and rep2.steps[0].step == "poll"
    # the analyst records on GBL's page; the loop is closed
    r = app.test_client().post("/api/record", json={})
    assert r.status_code == 200 and r.get_json()["best"] == 1


def test_bridge_automation_file_export(tmp_path):
    """WinGPC Automation writes NNN_<template>.txt per sample into the project folder; the bridge waits for it."""
    import threading
    gbl = FakeGBL(PENDING)
    calls = []
    proj = tmp_path / "project"
    proj.mkdir()
    (proj / "001_gbl.txt").write_text("old run\n")            # an older report must be ignored
    import os, time as _t
    os.utime(proj / "001_gbl.txt", (_t.time() - 3600, _t.time() - 3600))

    def run_recipe(name, params, token):
        calls.append(name)
        if name == "sequence_start":
            def later():
                _t.sleep(0.3)
                write_export(proj / "002_gbl.txt")
            threading.Thread(target=later, daemon=True).start()
        return {"ok": True, "log": []}
    inst = Instrument(run_recipe=run_recipe, monitor_snapshot=lambda: {"changed_since_previous": False,
                      "trace": {"calibrated": True, "x": [30.0]}}, notify_operator=lambda m: None)
    cfg = BridgeConfig(method_strategy="manual", export_dir=str(tmp_path), export_strategy="automation_file",
                       automation_dir=str(proj), automation_template="gbl.txt", automation_wait_s=10,
                       flat_seconds_means_done=0, min_elapsed_fraction=0, auto_read=False, confirm_each_run=False)
    rep = Bridge(gbl, inst, cfg).run_once()
    assert rep.ok, rep.to_dict()
    assert "ascii_export_raw_data" not in calls
    assert rep.export_path.endswith("002_gbl.txt")
    assert gbl.uploads[0][0].endswith("002_gbl.txt")
