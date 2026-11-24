"""The MCP server must build and answer observe/parse tools on a non-Windows host
(so CI can run); Windows-only tools must fail with a clear BackendUnavailable."""
import asyncio
import json

import pytest

from tests.synth import gaussian_chrom
from wingpc_agent.config import Settings
from wingpc_agent.mcp_server import build_server


@pytest.fixture
def server(tmp_path):
    s = Settings.load()
    s.state_dir = str(tmp_path)
    s.audit_log = str(tmp_path / "audit.jsonl")
    s.export_dir = str(tmp_path)
    return build_server(s)


def _call(server, tool_name, **args):
    res = asyncio.run(server.call_tool(tool_name, args))
    # mcp 2.x returns a CallToolResult; older builds returned a list / tuple
    if hasattr(res, "content"):
        return res.content
    return res[0] if isinstance(res, tuple) else res


def test_tool_list_contains_families(server):
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    for fam in ("wingpc_status", "ui_tree", "ui_click", "recipe_run", "chrom_live_snapshot",
                "chrom_live_image", "chrom_set_axes", "files_read_ascii", "export_ascii", "audit_tail"):
        assert fam in names
    assert len(names) >= 25


def test_status_degrades_gracefully_offline(server):
    out = json.loads(_call(server, "wingpc_status")[0].text)
    assert "windows" in out and "monitor" in out
    if isinstance(out["windows"], dict):
        assert "unavailable" in out["windows"]


def test_ui_click_reports_backend_unavailable_not_crash(server):
    import sys
    out = json.loads(_call(server, "ui_click", path=".*WinGPC.*|Button:Start")[0].text)
    if not sys.platform.startswith("win"):
        assert out["error"] == "BackendUnavailable"


def test_hazardous_recipe_denied_without_token(server):
    out = json.loads(_call(server, "recipe_run", name="sequence_stop")[0].text)
    assert out["error"] == "ActionDenied"


def test_unverified_recipe_refused(server):
    ctx = server._wingpc_ctx
    tok = ctx.gate.issue_token("recipe_sequence_stop", "t")
    out = json.loads(_call(server, "recipe_run", name="sequence_stop", confirm_token=tok)[0].text)
    assert out["error"] == "RecipeError" and "not verified" in out["message"]


def test_files_read_ascii_roundtrip(server, tmp_path):
    x, y = gaussian_chrom(n=600)
    p = tmp_path / "export.txt"
    p.write_text("Time\tRI\n" + "\n".join(f"{a:.4f}\t{b:.4f}" for a, b in zip(x, y)), encoding="utf-8")
    out = json.loads(_call(server, "files_read_ascii", path=str(p))[0].text)
    assert out["n_tables"] == 1
    t = out["tables"][0]
    assert t["rows"] == 600 and len(t["peaks_col1"]) == 3


def test_files_read_ascii_outside_dirs_blocked(server, tmp_path):
    other = tmp_path.parent / "elsewhere.txt"
    other.write_text("1\t2\n3\t4\n")
    out = json.loads(_call(server, "files_read_ascii", path=str(other))[0].text)
    assert "error" in out
