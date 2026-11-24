# wingpc-agent

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-35%20passing-brightgreen.svg)](tests/)
[![MCP server](https://img.shields.io/badge/MCP-server-8A2BE2.svg)](https://modelcontextprotocol.io/)
[![Status: pre-alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)](docs/RUNBOOK.md)

A control-and-observation layer that lets an LLM operate **PSS / Agilent
WinGPC UniChrom** (GPC/SEC chromatography data system) and watch the live
chromatogram in real time.

WinGPC has no public API, no scripting language and no live data feed (see
[docs/RESEARCH.md](docs/RESEARCH.md)). This project therefore sits *on top of
the running desktop application*:

* **Control** through Windows UI Automation (pywinauto): menus, dialogs,
  ChromPilot Sequence Manager buttons, text fields. Multi-step procedures are
  data-driven **recipes** that are verified per installation.
* **Observation** of the live Raw Data plot through screen capture plus trace
  extraction (pixels -> minutes/signal), at a few frames per second, with
  peak detection and compact JSON for the model. Screenshots are also handed
  to the model's vision for axis labels, dialogs and status bars.
* **Ground truth** from WinGPC's own ASCII exports, triggered by the agent and
  parsed in any separator/decimal dialect.
* **Discovery** first: `wingpc-agent probe` dumps the real menu captions,
  plot pane, file types, documentation folder and any registered COM server,
  so nothing is hard-coded from guesses.
* **Safety**: every tool goes through a policy (observe / control / hazardous
  / denied), hazardous actions need a one-time token a human issues at the
  PC, and everything is written to an audit log.

The tools are exposed as an **MCP server**, so Claude Desktop, Claude Code or
the included Anthropic-SDK agent loop can drive it.

## Layout

```
wingpc_agent/
  cli.py            probe | serve | bridge | monitor | parse | confirm | com-inspect | config-init
  mcp_server.py     31 MCP tools (wingpc_*, ui_*, recipe_*, chrom_*, files_*, gbl_*, audit_tail)
  gbl/              Gradient Bench LLM bridge: client.py, method.py, bridge.py
  discovery.py      what does THIS installation expose?
  stream.py         real-time monitor thread + ring buffer
  recipes.py        YAML recipes (verified UI procedures)
  safety.py         policy levels, confirmation tokens, audit log
  config.py         settings (YAML + env)
  backends/         uia.py (pywinauto), screen.py, files.py, com_probe.py
  chromatogram/     plot_extract.py, ascii_parser.py, analysis.py
recipes/            wingpc_default.yaml (menu paths, unverified), policy_default.yaml
agent/              run_agent.py (Claude tool-runner over MCP stdio), Claude Desktop config example
sim/fake_wingpc.py  Tk stand-in for the Raw Data window for rehearsals
tests/              35 tests (parser, peaks, pixel extraction, policy, server offline, GBL bridge)
docs/               RESEARCH.md, ARCHITECTURE.md, RUNBOOK.md, GRADIENT_BENCH.md
```

## Quick start

On the WinGPC PC (Windows):

```bash
pip install -e ".[windows,llm,dev]"
```

```bash
wingpc-agent probe
```

```bash
wingpc-agent serve
```

Then connect an MCP client, or run the headless operator:

```bash
python agent/run_agent.py --watch "Start the live monitor, calibrate the axes from the screenshot, and report each peak as it elutes."
```

Full procedure, including verifying recipes and the simulator rehearsal:
[docs/RUNBOOK.md](docs/RUNBOOK.md).

## Working with Gradient Bench LLM

[Gradient Bench LLM](https://github.com/amyth-create/gradient-bench-llm) proposes
gradient methods and reads the uploaded export; it cannot run the instrument.
`wingpc-agent bridge` polls its API for the method in flight, applies it in
WinGPC (manual, template or recipe strategy), starts and watches the run,
exports the ASCII trace and uploads it back, optionally triggering GBL's model
read. Recording stays with the analyst. See [docs/GRADIENT_BENCH.md](docs/GRADIENT_BENCH.md).

```bash
wingpc-agent bridge --gbl http://127.0.0.1:5052 --once
```

## Tools the LLM sees

| Family | Tools | Level |
|---|---|---|
| status | `wingpc_status`, `discovery_report`, `audit_tail` | observe |
| UI | `ui_tree`, `ui_find`, `ui_read_text`, `ui_screenshot` | observe |
| UI | `ui_click`, `ui_type`, `ui_select`, `ui_menu`, `ui_keys` | control |
| UI | `ui_close_window` | hazardous |
| recipes | `recipes_list`, `recipe_run`, `export_ascii` | per recipe |
| live chromatogram | `chrom_live_start/stop`, `chrom_live_snapshot`, `chrom_live_image`, `chrom_live_history`, `chrom_live_peaks`, `chrom_set_axes` | observe / control |
| files | `files_list_recent`, `files_read_ascii` | observe |
| Gradient Bench LLM | `gbl_state`, `gbl_pending_method`, `gbl_reports` / `gbl_upload_trace`, `gbl_read` / `gbl_run_cycle` | observe / control / hazardous |

Hazardous recipes shipped: `sequence_start`, `sequence_stop`, `sequence_pause`.

## What is verified and what is not

* Verified here (Linux container, 25 tests): ASCII dialect detection incl.
  German decimal comma, multi-block reports; peak picking on noisy drifting
  baselines; plot-box detection and trace extraction from rendered plots
  (retention time within one pixel, height within 5 %); policy gate and token
  flow; MCP server builds, lists 25 tools and degrades cleanly without
  Windows.
* **Not yet verified** (needs the real PC): pywinauto against WinGPC's actual
  control tree, the menu captions in `recipes/wingpc_default.yaml`, where
  WinGPC's chart pane sits, the ASCII export column layout, and whether the
  installer registers any COM automation object. The probe + runbook exist to
  settle exactly those.

## Model notes

`agent/run_agent.py` uses `claude-opus-5-5` with adaptive thinking and
enables Anthropic's server-side refusal fallback (`fallbacks: "default"`) so a
policy decline is retried on another model inside the same call; remove the
two lines in the runner call if you do not want that. Set
`ANTHROPIC_API_KEY` or run `ant auth login` first.

## License

MIT, see [LICENSE](LICENSE).
