"""Command line: probe | serve | monitor | parse | confirm | com-inspect | config-init."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import Settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="wingpc-agent", description=__doc__)
    ap.add_argument("--config", help="YAML settings file (default: $WINGPC_AGENT_CONFIG)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="discover what this WinGPC install exposes")
    p.add_argument("--no-ui-tree", action="store_true")
    p.add_argument("--depth", type=int, default=6)

    p = sub.add_parser("serve", help="run the MCP server")
    p.add_argument("--http", type=int, metavar="PORT", help="streamable-http on this port instead of stdio")
    p.add_argument("--host", default="127.0.0.1")

    p = sub.add_parser("monitor", help="print live trace stats to the console (no LLM)")
    p.add_argument("--seconds", type=float, default=30)

    p = sub.add_parser("parse", help="parse an ASCII export and print a summary")
    p.add_argument("path")
    p.add_argument("--data", action="store_true")

    p = sub.add_parser("confirm", help="human: issue a one-time confirmation token for a hazardous tool")
    p.add_argument("tool")
    p.add_argument("--reason", default="operator approved")

    p = sub.add_parser("com-inspect", help="instantiate a discovered COM ProgID and list its members")
    p.add_argument("progid")

    p = sub.add_parser("bridge", help="Gradient Bench LLM: run every pending method on WinGPC and upload the export")
    p.add_argument("--gbl", help="GBL base URL (default from config / GBL_URL, http://127.0.0.1:5052)")
    p.add_argument("--campaign-dir", help="folder mode: use gradient_llm directly on this campaign (GBL page not running)")
    p.add_argument("--strategy", choices=["manual", "template", "recipe"], help="how the method gets into WinGPC")
    p.add_argument("--once", action="store_true", help="run one cycle and exit")
    p.add_argument("--yes", action="store_true", help="do not ask before starting each injection (unattended)")
    p.add_argument("--no-read", action="store_true", help="upload only; skip GBL's model read")
    p.add_argument("--dry-run", action="store_true", help="show the pending method and the plan, change nothing")

    p = sub.add_parser("config-init", help="write a starter config YAML")
    p.add_argument("path", nargs="?", default=str(Path.home() / ".wingpc-agent" / "config.yaml"))

    a = ap.parse_args(argv)
    s = Settings.load(a.config)

    if a.cmd == "probe":
        from .discovery import run_probe
        rep = run_probe(s, ui_tree=not a.no_ui_tree, depth=a.depth)
        slim = {k: v for k, v in rep.items() if k != "ui_tree_main"}
        print(json.dumps(slim, indent=2, default=str))
        print(f"\nfull report (incl. UI tree): {Path(s.state_dir) / 'discovery.json'}", file=sys.stderr)
        return 0

    if a.cmd == "serve":
        from .mcp_server import build_server
        srv = build_server(s)
        if a.http:
            srv.run("streamable-http", host=a.host, port=a.http)
        else:
            srv.run("stdio")
        return 0

    if a.cmd == "monitor":
        from .backends import screen
        from .stream import LiveMonitor
        mon = LiveMonitor(s, screen.grab)
        mon.start()
        t_end = time.time() + a.seconds
        try:
            while time.time() < t_end:
                time.sleep(1.0 / max(0.2, s.poll_hz))
                snap = mon.snapshot(max_points=20)
                d = snap.get("describe") or {}
                print(f"ticks={snap['stats']['ticks']} changed={snap.get('changed_since_previous')} "
                      f"n={d.get('n')} y_last={d.get('y_last')} peaks={len(snap.get('peaks', []))} "
                      f"err={snap['stats']['last_error']}")
        except KeyboardInterrupt:
            pass
        mon.stop()
        return 0

    if a.cmd == "parse":
        from .chromatogram import ascii_parser
        from .chromatogram.analysis import find_peaks
        for t in ascii_parser.parse_file(a.path):
            summ = t.summary()
            if t.data.shape[0] > 5 and t.data.shape[1] >= 2:
                peaks, params = find_peaks(t.x, t.y(1))
                summ["peaks"] = [p.to_dict() for p in peaks]
            if a.data:
                summ["data_head"] = t.data[:10].tolist()
            print(json.dumps(summ, indent=2, default=str))
        return 0

    if a.cmd == "confirm":
        from .safety import Gate, Policy
        tokfile = Path(s.state_dir) / "confirm_tokens.json"
        gate = Gate(Policy.load(s.policy_file), s.audit_log, interactive_confirm=False, token_file=tokfile)
        tool = a.tool if a.tool.startswith(("recipe_", "ui_")) else f"recipe_{a.tool}"
        tok = gate.issue_token(tool, a.reason)      # audit entry
        data = json.loads(tokfile.read_text()) if tokfile.exists() else {}
        data[tok] = {"tool": tool, "exp": time.time() + gate.policy.confirm_ttl_s}
        tokfile.write_text(json.dumps(data))
        print(f"token for {tool} (valid {gate.policy.confirm_ttl_s}s): {tok}", file=sys.stderr)
        print(tok)
        return 0

    if a.cmd == "com-inspect":
        from .backends.com_probe import try_instantiate
        print(json.dumps(try_instantiate(a.progid), indent=2))
        return 0

    if a.cmd == "bridge":
        return _bridge_cmd(s, a)

    if a.cmd == "config-init":
        Path(a.path).parent.mkdir(parents=True, exist_ok=True)
        s.dump(a.path)
        print(a.path)
        return 0
    return 1


def _bridge_cmd(s: Settings, a) -> int:
    from .gbl.bridge import Bridge, BridgeConfig, Instrument
    from .gbl.client import FolderClient, GBLError, HttpClient
    from .mcp_server import build_server

    g = s.gbl
    if a.gbl:
        g.url = a.gbl
    if a.campaign_dir:
        g.campaign_dir = a.campaign_dir
    if a.strategy:
        g.method_strategy = a.strategy
    client = FolderClient(g.campaign_dir) if g.campaign_dir else HttpClient(g.url)
    try:
        st = client.state()
    except GBLError as e:
        print(f"cannot talk to Gradient Bench LLM: {e}", file=sys.stderr)
        return 2
    print(f"GBL campaign: {(st.get('campaign') or {}).get('name')}  next: {(st.get('next') or {}).get('title')}")
    if a.dry_run:
        from .gbl.method import from_gbl
        pend = client.pending()
        if pend and not pend.get("trace"):
            m = from_gbl(pend)
            print(f"pending run {m.run_no}: {pend.get('describe')}\n{m.table_text()}")
            print(f"strategy={g.method_strategy} export_dir={s.export_dir or s.state_dir} auto_read={not a.no_read}")
        else:
            print("nothing waiting for a trace")
        return 0

    # Reuse the MCP server's context so recipes, gate, monitor and audit are the same objects.
    srv = build_server(s)
    ctx = srv._wingpc_ctx
    cfg = BridgeConfig(
        method_strategy=g.method_strategy, template_file=g.template_file, method_out_dir=g.method_out_dir,
        export_dir=s.export_dir or s.state_dir, fixed_extra_min=g.fixed_extra_min, post_time_min=g.post_time_min,
        status_path=g.status_path, detector_column_re=g.detector_column_re, upload_format=g.upload_format,
        auto_read=not a.no_read, confirm_each_run=not a.yes,
    )

    def run_recipe(name, params, token):
        # Operator-driven CLI: hazardous recipes are confirmed at the console by the Gate itself.
        ctx.gate.interactive_confirm = not a.yes
        if a.yes:
            ctx.gate.policy.unattended_ok.update({f"recipe_{name}"})
        import asyncio
        res = asyncio.run(srv.call_tool("recipe_run", {"name": name, "params": params, "confirm_token": token}))
        content = res.content if hasattr(res, "content") else res
        return json.loads(content[0].text)

    def wait_operator(prompt: str) -> bool:
        try:
            return input(prompt).strip().lower() in ("y", "yes")
        except EOFError:
            return False

    inst = Instrument(run_recipe=run_recipe, read_text=lambda p: ctx.uia.read_text(p),
                      monitor_snapshot=lambda: ctx.monitor.snapshot(max_points=50, with_peaks=False),
                      monitor_start=ctx.monitor.start, notify_operator=print,
                      wait_operator=None if a.yes else wait_operator)
    bridge = Bridge(client, inst, cfg, audit=ctx.gate.audit)

    def show(rep):
        for st in rep.steps:
            print(f"  [{'ok' if st.ok else 'FAIL'}] {st.step}: {st.detail[:160]}")
        print(f"run {rep.run_no}: {'complete' if rep.ok else 'incomplete'}; export={rep.export_path} uploaded_as={rep.uploaded_as}")

    if a.once:
        show(bridge.run_once())
        return 0
    print(f"polling GBL every {g.poll_s:.0f}s for methods to run (Ctrl-C to stop)")
    try:
        bridge.serve(poll_s=g.poll_s, on_report=show)
    except KeyboardInterrupt:
        bridge.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
