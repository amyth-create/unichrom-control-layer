# Runbook: bringing the layer up on the WinGPC PC

Everything below happens on the Windows PC that runs WinGPC UniChrom, logged in
to the interactive desktop session (UI Automation and screen capture need it).

## 0. Install

```bash
py -3.11 -m venv %USERPROFILE%\wingpc-agent-venv
%USERPROFILE%\wingpc-agent-venv\Scripts\activate
pip install -e ".[windows,llm,dev]"
```

Optional but useful: `pip install psutil` (richer process info in the probe).

## 1. Rehearse against the simulator (no instrument needed)

```bash
python sim\fake_wingpc.py --autostart
```

In a second terminal:

```bash
wingpc-agent probe
wingpc-agent monitor --seconds 20
```

`probe` should list the simulator window, its menu captions and one large
plot candidate. `monitor` should print growing `n` and `peaks` counts.

## 2. Probe the real WinGPC

Start WinGPC, load a project, open the Raw Data window (ideally while a run is
acquiring), then:

```bash
wingpc-agent probe > probe.txt
```

Read `%USERPROFILE%\.wingpc-agent\discovery.json`:

* `registry.progids` / `registry.typelibs` non-empty -> try
  `wingpc-agent com-inspect <ProgID>`; a real automation object beats clicking.
* `documentation_files` -> open the ChromPilot Control PDFs and look for any
  automation / LIMS / auto-export option; if an end-of-sample export exists,
  enable it to the folder you will set as `export_dir`.
* `menu_items` -> the real menu captions. Fix every `menu:` path in
  `recipes/wingpc_default.yaml` to match, then set `verified: true` per recipe.
* `plot_candidates[0]` -> the chart pane. Put its UIA path into
  `plot.control_path` (e.g. `[".*Raw ?Data.*", "Pane:index=3"]`) or its
  rectangle into `plot.rect`.
* `file_census` -> learn WinGPC's extensions; add the data folders to
  `project_dirs`.

## 3. Write the config

```bash
wingpc-agent config-init
```

Edit `%USERPROFILE%\.wingpc-agent\config.yaml`: `project_dirs`, `export_dir`,
`plot.*` (window title regex, control path or rect, trace colour, axis span if
fixed), `poll_hz`. Point the env var at it:

```bash
setx WINGPC_AGENT_CONFIG %USERPROFILE%\.wingpc-agent\config.yaml
```

## 4. Verify recipes one at a time

With the simulator or a *non-critical* WinGPC session, set
`allow_unverified_recipes: true` temporarily and run each observe/control
recipe from the CLI-driven agent or an MCP client:

```bash
python agent\run_agent.py --watch "Run the recipe ascii_export_raw_data with path C:\exports\test.txt and read the file back."
```

Flip `verified: true` as each one works. Leave hazardous recipes
(`sequence_start/stop/pause`) unverified until you have watched them once.

## 5. Connect an LLM

Claude Desktop: copy `agent/claude_desktop_config.example.json` into the
Claude Desktop config and restart it. Claude Code: `claude mcp add wingpc --
python -m wingpc_agent.cli serve`. Headless: `python agent\run_agent.py "<task>"`.

Typical first conversation:

1. `wingpc_status` -> `chrom_live_start` -> `chrom_live_image`
2. model reads the axis labels -> `chrom_set_axes 0 30 -10 120 min mV`
3. `chrom_live_snapshot` every 15-30 s while a run is on; report peaks.
4. At the end: `export_ascii` -> exact numbers.

## 6. Hazardous actions

When the model asks to start/stop/pause a sequence or close a window, the
server replies `ActionDenied` with instructions. The operator, at the PC, runs

```bash
wingpc-agent confirm sequence_stop --reason "run 12 finished early"
```

and gives the printed token to the model (`confirm_token=...`). Tokens are
single-use and expire after `confirm_ttl_s` (default 300 s). To allow a
hazardous tool unattended, add it to `unattended_ok` in
`recipes/policy_default.yaml` (not recommended for `sequence_start`).

Everything the model did is in `%USERPROFILE%\.wingpc-agent\audit.jsonl`
(`audit_tail` tool shows the last entries).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `BackendUnavailable: pywinauto missing` | `pip install "wingpc-agent[windows]"` in the same venv the server runs from |
| `ElementNotFound: no window matching` | Check `main_window_title_re` against `probe` -> `windows`; WinGPC must be running in the same user session |
| `menu path ... not found` | Captions differ (language pack / version). Read `menu_items` from the discovery report and fix the recipe |
| `coverage` in snapshot near 0 | Wrong `trace_rgb` / `color_tolerance`, or the rectangle is not the plot. Use `chrom_live_image` to see what is being captured |
| trace values look mirrored or clipped | `plot.rect`/control path includes axis labels; tighten it, or let auto-box detection work by clearing `rect` |
| `recipe ... is not verified` | Intended: verify on this installation first (step 4) |
| Snapshot `calibrated: false` | Call `chrom_set_axes` after reading the axis labels, or fix `plot.x_min..y_max` in config when the scale is fixed |
