# Closing the loop with Gradient Bench LLM

[Gradient Bench LLM](https://github.com/amyth-create/gradient-bench-llm) (GBL)
develops HPLC gradient methods: its model proposes a gradient timetable and a
column temperature, the analyst runs it and uploads the detector's ASCII
export, the model reads the export and gives a verdict, the analyst records.
Its loop is **propose -> upload -> read -> record** and the step it cannot do
itself is "run it on the instrument".

`wingpc_agent.gbl` does that step with WinGPC UniChrom:

```
 Gradient Bench LLM (Flask, :5052)                 wingpc-agent (on the WinGPC PC)
 ───────────────────────────────                   ────────────────────────────────
 propose  -> state.json {method, run}   ──GET /api/state──>  bridge.pending_method()
                                                             gbl.method.from_gbl()  -> operator table /
                                                                                       method file / recipe
                                                             apply method  (manual | template | recipe)
                                                             recipe sequence_start      [hazardous]
                                                             watch: status text, live plot, duration
                                                             recipe ascii_export_raw_data -> export_dir
                                                             validate (parser) -> normalise if >2 columns
 upload   <- traces/runNN.txt           <─POST /api/upload──  bridge._upload()
 read     <- model reads the export     <─POST /api/read────  bridge._read()   (auto_read)
 record   analyst confirms on GBL's page                     (never automated)
```

GBL itself is not modified; the bridge speaks its existing HTTP API exactly as
GBL's own page does. Recording the verdict stays with the analyst by design.

## What the control layer receives from GBL

`GET /api/state` -> `pending`:

```json
{"run": 3, "source": "llm", "step": "upload", "trace": null,
 "method": {"gradient": [{"time_min": 0, "pct_b": 5}, {"time_min": 15, "pct_b": 60},
                         {"time_min": 20, "pct_b": 95}], "temperature_c": 40},
 "rationale": "...", "expectation": "...", "describe": "0 min 5 %B, 15 min 60 %B, 20 min 95 %B, 40 °C"}
```

`gbl.method.from_gbl` turns that into a `Method` (rows of time/%B/%A, oven
setpoint, expected run length) and `table_text()` prints it the way an
operator keys it into ChromPilot.

## What the control layer sends back

The ASCII export WinGPC wrote, as a multipart `file` on `POST /api/upload`.
GBL copies it byte for byte into `traces/runNN.txt` and refuses anything that
is not a time/signal table of at least 20 rows. Two rules apply before upload:

* the export must parse (our parser) with a table of >= 20 rows and >= 2
  numeric columns, otherwise the cycle fails before touching GBL;
* if the export has **more than two numeric columns** (RI + UV + pressure...)
  or `upload_format: normalised` is set, a two-column copy `*_2col.txt`
  (time + the column matching `detector_column_re`, else column 2) is
  uploaded instead, with the original kept next to it. GBL's reader takes
  column 2 (or the last column when the middle one is constant), so a
  multi-detector export would otherwise be read against the wrong detector.

If `auto_read` is on the bridge then calls `POST /api/read` and returns the
model's reading (peaks, unresolved regions, verdict) in its report.

## Getting the method into WinGPC

The pump timetable and oven setpoint live in ChromPilot's instrument method.
How that editor looks is installation-specific, so there are three strategies
(`gbl.method_strategy` in the config):

| strategy | what happens | when to use |
|---|---|---|
| `manual` (default) | the bridge prints the table and waits for the operator to key it in and confirm; start, watch, export and upload are automated | first days; any installation where the method editor is not automatable |
| `template` | the bridge renders a method file from a template you exported once (placeholders `{temperature_c}`, `{timetable_rows}`, `{b0}`.., `{t0}`..) and the `load_instrument_method` recipe loads it | when ChromPilot/WinGPC can import a method file (check the Documentation folder; RC.Net method files are XML) |
| `recipe` | the `apply_gradient_method` recipe types the values into the editor's controls | after the probe has mapped the editor's UIA tree and the recipe is verified |

Starting an injection is **hazardous**: on the CLI the operator is asked per
run (`--yes` for unattended), through MCP the `gbl_run_cycle` tool needs a
confirmation token from `wingpc-agent confirm gbl_run_cycle`.

## Pressure monitoring

While a run is on, the bridge samples the pump pressure (`gbl.pressure_path`,
a UIA path to ChromPilot's pressure read-out; the first number in that text is
taken as bar). Above `pressure_warn_bar` it logs one warning and notifies the
operator; above `pressure_max_bar` for two consecutive samples it runs the
`sequence_stop` recipe, marks the cycle `pressure_abort` and uploads nothing.
The watch step's report carries min / max / mean / last pressure.

## Knowing when the run is over

`Bridge._watch` stops on the first of:

1. a status text (UIA path `gbl.status_path`, e.g. the Sequence Manager's
   state label found by the probe) matching `status_done_re` after it matched
   `status_running_re`;
2. the live plot's calibrated x reaching the expected run length
   (gradient length + `fixed_extra_min` + `post_time_min`);
3. the live plot unchanged for `flat_seconds_means_done` after the expected
   end;
4. a hard timeout (`expected * 1.5 + 3 min`) -> the cycle fails.

Set `fixed_extra_min` to whatever the campaign's "fixed parts of the method"
add to the acquisition (re-equilibration inside the run, post time).

## Running it

```bash
# GBL on the same PC (or anywhere reachable), campaign open in its page
python -m gradient_llm.api.app
```

```bash
# see what GBL is waiting for, change nothing
wingpc-agent bridge --gbl http://127.0.0.1:5052 --dry-run
```

```bash
# one run, operator confirms the method entry and the injection
wingpc-agent bridge --once
```

```bash
# unattended: every method GBL proposes gets run and uploaded (method strategy must be template/recipe)
wingpc-agent bridge --yes --strategy template
```

Config (`~/.wingpc-agent/config.yaml`):

```yaml
gbl:
  enabled: true
  url: http://127.0.0.1:5052
  method_strategy: manual         # manual | template | recipe
  template_file: null             # C:\methods\gbl_template.xml for the template strategy
  fixed_extra_min: 5              # re-equilibration counted in the acquisition
  status_path: null               # e.g. ".*Sequence ?Manager.*|Text:Status"
  detector_column_re: "UV|254"
  upload_format: original         # or normalised
  auto_read: true
  confirm_each_run: true
```

Folder mode (`--campaign-dir`) uses the `gradient_llm` package directly on
the campaign folder and is only for when GBL's page is *not* running against
that folder; it cannot call the model, so `auto_read` is off there.

## For the LLM on top

Through MCP the operator model sees `gbl_state`, `gbl_pending_method`,
`gbl_upload_trace`, `gbl_read`, `gbl_run_cycle` (hazardous) and
`gbl_reports`. A typical instruction: "GBL has proposed run 4; run it on
WinGPC and tell me what GBL's model saw." It calls `gbl_pending_method`, asks
the operator for a token, calls `gbl_run_cycle`, and reports the step list and
reading. GBL's own model and the model driving WinGPC never talk directly; the
campaign folder is the shared record.

## Tests

`tests/test_gbl.py` runs the bridge against fakes and, when the
gradient-bench-llm checkout sits next to this repo (or is installed), against
GBL's real Flask app in demo mode: manual method -> bridge cycle -> the export
lands byte for byte in `traces/run01.txt` -> GBL's demo reader reads it -> the
analyst records -> run 1 is the best run.
