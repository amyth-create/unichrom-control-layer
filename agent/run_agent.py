"""Claude-driven operator loop for WinGPC, talking to the MCP server over stdio.

The tool side (UI automation, screen capture) must run on the lab PC, so the
agent loop is hosted here with the Anthropic SDK tool runner rather than on
Managed Agents. Run on the WinGPC PC (or anywhere that can spawn
`wingpc-agent serve` there):

    pip install "wingpc-agent[llm]"        # anthropic SDK
    set ANTHROPIC_API_KEY=...              # or `ant auth login`
    python agent/run_agent.py "Start the live monitor, calibrate the axes from the screenshot, and tell me when the first peak elutes."

Safety: hazardous tools are gated server-side (confirm tokens). This client
additionally shows every tool call it is about to make when --watch is set.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from anthropic import AsyncAnthropic
from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SYSTEM = """You are a careful GPC/SEC lab operator driving WinGPC UniChrom through tools.

Working rules
- Observe before you act: wingpc_status, then chrom_live_snapshot / chrom_live_image.
- Live numbers come from pixels of the plot. Calibrate axes with chrom_set_axes after reading the axis
  labels from chrom_live_image; until then values are fractions of the plot box and you must say so.
- Prefer recipe_* tools over raw ui_* clicks. Never guess menu names: use ui_tree / ui_find first.
- Hazardous actions (start/stop/pause sequence, closing windows) require a human-issued confirm_token.
  If a tool returns ActionDenied, stop and tell the operator exactly which command to run. Never retry.
- Report what you observed (retention times, peak count, baseline drift, errors) with the units you have,
  and distinguish screen-derived estimates from values read from an ASCII export.
- When monitoring a run, poll chrom_live_snapshot at a sensible cadence (every 10-30 s) and summarise
  changes rather than dumping raw arrays.
"""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", nargs="?", default="Check the state of WinGPC and the live chromatogram, then summarise.")
    ap.add_argument("--server-cmd", default=sys.executable)
    ap.add_argument("--server-args", nargs="*", default=["-m", "wingpc_agent.cli", "serve"])
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--max-iterations", type=int, default=60)
    ap.add_argument("--watch", action="store_true", help="print each tool call before it runs")
    a = ap.parse_args()

    client = AsyncAnthropic()
    params = StdioServerParameters(command=a.server_cmd, args=a.server_args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as mcp:
            await mcp.initialize()
            tools_result = await mcp.list_tools()
            tools = [async_mcp_tool(t, mcp) for t in tools_result.tools]
            runner = client.beta.messages.tool_runner(
                model=a.model,
                max_tokens=16000,
                system=SYSTEM,
                thinking={"type": "adaptive"},
                output_config={"effort": a.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",            # policy-decline rescue on another model (see README)
                tools=tools,
                messages=[{"role": "user", "content": a.task}],
                max_iterations=a.max_iterations,
            )
            final = None
            async for message in runner:
                final = message
                for block in message.content:
                    if block.type == "tool_use" and a.watch:
                        print(f"-> {block.name} {json.dumps(block.input)[:300]}", file=sys.stderr)
                    elif block.type == "text" and block.text.strip():
                        print(block.text)
            if final is not None and final.stop_reason == "refusal":
                print("The model declined this request.", file=sys.stderr)
                return 2
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
