# Architecture

```
                 +------------------------------------------------------------+
                 |  Windows PC running WinGPC UniChrom (+ ChromPilot)          |
                 |                                                            |
   LLM           |  wingpc-agent serve  (MCP server, Python)                   |
 (Claude Desktop,|  +------------------------------------------------------+  |
  Claude Code,   |  |  mcp_server.py   25 tools                            |  |
  agent/run_agent|  |   wingpc_* ui_* recipe_* chrom_* files_* audit_tail  |  |
  .py)           |  +------+-----------+-------------+----------+----------+  |
      |  MCP     |         |           |             |          |             |
      | stdio /  |   safety.Gate   recipes.py   stream.LiveMonitor  files    |
      | http     |   (policy +     (YAML steps)  (thread: grab ->  (watch,   |
      +----------+    audit)           |          extract -> ring)  parse)   |
                 |         |           v             |                       |
                 |         |     backends/uia.py     v                       |
                 |         |     (pywinauto UIA/  backends/screen.py          |
                 |         |      win32)          (mss / ImageGrab)           |
                 |         v           |             |                       |
                 |   +-----------------v-------------v---------------------+ |
                 |   |            WinGPC UniChrom windows                    | |
                 |   |  menus, dialogs, Sequence Manager, Raw Data plot      | |
                 |   +------------------------------------------------------+ |
                 |            |  ASCII export (.txt)  ^  COM? (probe)          |
                 |            v                       |                        |
                 |     export / project dirs  <-- backends/files.py            |
                 +------------------------------------------------------------+
```

## Layers

1. **discovery.py** - `wingpc-agent probe`. Finds the process, install dir,
   `Documentation` folder, registered COM ProgIDs/TypeLibs, all top-level
   windows, the full UIA tree of the main window (so menu captions are
   known), big custom-drawn panes in the Raw Data window (plot candidates)
   and a census of file types in the project folders. Output:
   `~/.wingpc-agent/discovery.json`, readable by the LLM via `discovery_report`.

2. **backends/** - thin, degradable wrappers.
   * `uia.py`: windows, tree dump, `resolve("<title re>|Type:Name|auto_id=..")`,
     click (Invoke pattern first, mouse as fallback), set text, menus
     (`menu_select` then UIA walk), keys, wait/wait_gone, close.
   * `screen.py`: capture a rectangle -> PIL image; PNG encoding with
     downscale for the model's vision.
   * `files.py`: recent files, extension census, polling DirWatcher,
     encoding-tolerant text tail.
   * `com_probe.py`: registry scan + opt-in late-bound instantiation.

3. **chromatogram/** - numbers.
   * `plot_extract.py`: plot box from the paper-coloured region (mode colour
     of the image centre, grid gaps bridged) with a dark-axis fallback;
     trace colour = configured or dominant saturated colour; one median row
     per pixel column; continuity filter; pixel -> data via `AxisCalibration`.
     Tested on synthetic renders: retention time error < 1 px, height error
     < 5 %.
   * `ascii_parser.py`: dialect auto-detection (tab/;/,/space x ./,) with a
     thousands-separator sanity rule, header/meta capture, multi-block files
     (Save ASCII Report).
   * `analysis.py`: moving-average smoothing, rolling-min baseline, prominence
     based peak picking with FWHM and area, descriptive stats, min/max
     preserving downsampling for compact LLM payloads.

4. **stream.py** - `LiveMonitor` thread. Each tick grabs the plot rectangle
   (UIA control rect -> window rect -> configured rect), hashes the image
   (cheap "did anything change"), extracts the trace and appends a Frame to a
   ring buffer sized by `ring_seconds * poll_hz`. Readers get `snapshot()`
   (trace + stats + peaks + calibration note), `history()` (per-frame
   summaries) and `events()`.

5. **recipes.py + recipes/*.yaml** - multi-step UI procedures with params and
   a `verified` flag. Unverified recipes are refused unless the operator
   opts in, because a wrong click in a CDS can discard a run.

6. **safety.py** - `Policy` (observe / control / hazardous / denied per tool)
   and `Gate` (consumes one-time confirmation tokens issued by a human via
   `wingpc-agent confirm <tool>`; JSONL audit log). The LLM cannot edit the
   policy; it is not exposed as a tool.

7. **mcp_server.py** - builds the `MCPServer` with those tools. Every tool
   passes through the gate, returns JSON text (or PNG `ImageContent` for the
   screenshot tools) and never raises into the transport: errors come back as
   `{"error": "<Type>", "message": ...}` so the model can reason about them.

8. **agent/run_agent.py** - optional headless operator loop using the
   Anthropic SDK tool runner over the MCP stdio transport (Claude Desktop or
   Claude Code can also connect directly; see `agent/claude_desktop_config.example.json`).

## Real-time data path, end to end

```
WinGPC draws a point -> screen pixels -> mss grab (~5 ms) -> paper/axis box
-> colour mask -> column medians (799 columns ~ 1 ms) -> AxisCalibration
-> Frame(t, x[], y[]) -> ring buffer -> chrom_live_snapshot JSON (<= 300 pts)
                                     -> chrom_live_image PNG (model vision)
```
Latency is one poll period (default 0.5 s). Resolution is one pixel column of
the plot (30 min over 800 px = 2.25 s) - fine for "has the peak eluted", not
for Mp to four digits. When exact numbers matter the agent runs
`export_ascii` and reads the file.

## Why MCP + a self-hosted loop

The tools must run inside the interactive Windows session that owns the
WinGPC windows (UI Automation and screen capture do not work from a service
or another machine). Exposing them as an MCP server means any MCP client
(Claude Desktop, Claude Code, the included tool-runner script, or a Managed
Agent via a tunnel) can drive the software, while the policy and audit stay
on the lab PC.

## Upgrade paths

* **COM found by the probe** -> implement `backends/com.py` and point recipes
  at it; the tool surface stays the same.
* **Chart exposes UIA values** (some .NET charts do) -> replace pixel
  extraction with `ui_read_text` on the series; same `Trace` type.
* **Auto-export at sample end exists** -> DirWatcher already emits events;
  add a tool that returns the newest export parsed.
