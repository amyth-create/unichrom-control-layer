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

    if a.cmd == "config-init":
        Path(a.path).parent.mkdir(parents=True, exist_ok=True)
        s.dump(a.path)
        print(a.path)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
