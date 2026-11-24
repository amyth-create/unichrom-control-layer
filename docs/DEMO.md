# The closed-loop demo

`demo/run_demo.py` runs the whole loop on one machine with no instrument, no
WinGPC and no model key:

```
Gradient Bench LLM (demo mode, real Flask app over HTTP)
      │ /api/suggest  ─ proposes a gradient (its offline stand-in walks the limits)
      ▼
wingpc-agent bridge
      │ reads the method from /api/state
      │ applies it through the `apply_gradient_method` recipe parameters
      │ starts the sequence, watches status text + live trace + pump pressure
      │ exports ASCII (time, UV, pressure) -> keeps time + UV -> POST /api/upload
      │ POST /api/read  -> GBL's reader counts peaks and gives a verdict
      ▼
simulated LC (wingpc_agent/sim/instrument.py)
      linear-solvent-strength retention, 7 components, gradient-dependent
      pressure from the water/acetonitrile viscosity curve
```

```bash
pip install -e ".[dev]" flask python-dotenv      # plus a checkout of gradient-bench-llm next to this repo
python demo/run_demo.py --runs 5
python demo/run_demo.py --runs 3 --spike-run 2    # a blockage in run 2: the bridge stops the run
```

Each cycle prints the bridge's step list (apply_method, start, watch with
pressure statistics, export, validate, upload, read), GBL's verdict and the
recorded best run. `demo/out/summary.md` has the table, `demo/out/runNN.png`
the trace with the pressure curve, and the temporary campaign folder holds
GBL's own record (`runs.csv`, `traces/runNN_2col.txt`, `transcript.jsonl`).

What is real and what is simulated:

| part | in the demo | in the lab |
|---|---|---|
| Gradient Bench LLM | the real app, demo asker instead of the Azure model | the real app with a deployment |
| bridge | the real `Bridge`, recipe strategy, pressure limits | the same code |
| recipes | a stub maps recipe names to simulator calls | pywinauto drives WinGPC's windows |
| status / pressure | simulator strings and numbers | `ui_read_text` on ChromPilot's status and pressure read-outs (`gbl.status_path`, `gbl.pressure_path`) |
| ASCII export | the simulator writes a WinGPC-like 3-column file | WinGPC's Data Editor export |
| record | the demo records the model's verdict | the analyst records on GBL's page |

Speed: `--accel 6` runs six simulated minutes per second, so a 20 min method
takes about four seconds including the two minutes of re-equilibration.
