"""`wingpc-agent probe`: learn what THIS WinGPC installation exposes.

Produces ~/.wingpc-agent/discovery.json (+ ui_tree.json) answering:
  * is WinGPC running, which exe/version, install dir, Documentation folder
  * registered COM ProgIDs / TypeLibs that look like PSS/WinGPC (automation?)
  * top-level windows + full UIA tree of the main window (menu names!)
  * what file types live in the project folders (raw/method/calibration/...)
  * candidate plot controls (large custom-drawn panes in a window whose
    title matches the Raw Data window regex)
The LLM reads this report through the `discovery_report` tool and the operator
uses it to correct recipes/wingpc_default.yaml.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

from .backends import com_probe, files
from .config import Settings
from .errors import BackendUnavailable, ElementNotFound


def _processes(names: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        import psutil  # type: ignore
    except ImportError:
        psutil = None  # type: ignore
    if psutil:
        low = [n.lower() for n in names]
        for p in psutil.process_iter(["pid", "name", "exe", "create_time"]):
            try:
                nm = (p.info["name"] or "").lower()
                if any(nm.startswith(n.replace(".exe", "")) for n in low):
                    out.append(p.info)
            except Exception:
                continue
        return out
    if sys.platform.startswith("win"):
        import subprocess
        try:
            txt = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10).stdout
            for ln in txt.splitlines():
                cols = [c.strip('"') for c in ln.split('","')]
                if cols and any(cols[0].lower().startswith(n.lower().replace(".exe", "")) for n in names):
                    out.append({"name": cols[0], "pid": int(cols[1]) if len(cols) > 1 and cols[1].isdigit() else None})
        except Exception:
            pass
    return out


def _install_dirs(reg: dict[str, Any]) -> list[str]:
    dirs: set[str] = set()
    for row in reg.get("uninstall", []) if reg.get("available") else []:
        loc = row.get("InstallLocation")
        if loc and os.path.isdir(loc):
            dirs.add(loc)
    for row in reg.get("app_paths", []) if reg.get("available") else []:
        p = row.get("path")
        if p and os.path.exists(p):
            dirs.add(str(Path(p).parent))
    for guess in (r"C:\Program Files\PSS\WinGPC", r"C:\Program Files (x86)\PSS\WinGPC", r"C:\PSS", r"C:\WinGPC",
                  r"C:\Program Files\Agilent\WinGPC", r"C:\Program Files (x86)\Agilent\WinGPC"):
        if os.path.isdir(guess):
            dirs.add(guess)
    return sorted(dirs)


def _docs(dirs: list[str]) -> list[str]:
    out: list[str] = []
    for d in dirs:
        for root, subdirs, fs in os.walk(d):
            if os.path.basename(root).lower() in ("documentation", "docs", "doc", "manuals", "help"):
                for f in fs:
                    out.append(os.path.join(root, f))
            # don't descend forever into huge trees
            if root.count(os.sep) - d.count(os.sep) > 3:
                subdirs[:] = []
    return out[:200]


def _candidate_plot_controls(uia, title_re: str) -> list[dict[str, Any]]:
    """Large children with no name/value in the Raw Data window = probably the chart."""
    try:
        w = uia.window(title_re, timeout=3)
    except ElementNotFound:
        return []
    cands = []
    wr = w.rectangle()
    for el in w.descendants():
        try:
            r = el.rectangle()
            area = max(0, r.width()) * max(0, r.height())
            if area < 0.15 * max(1, wr.width() * wr.height()):
                continue
            ei = el.element_info
            cands.append({
                "control_type": str(ei.control_type), "name": str(ei.name), "auto_id": str(ei.automation_id),
                "class_name": str(ei.class_name), "rect": [r.left, r.top, r.right, r.bottom], "area_px": area,
            })
        except Exception:
            continue
    cands.sort(key=lambda c: -c["area_px"])
    return cands[:8]


def run_probe(settings: Settings, ui_tree: bool = True, depth: int = 6) -> dict[str, Any]:
    t0 = time.time()
    report: dict[str, Any] = {
        "probe_time": t0,
        "host": {"platform": platform.platform(), "python": sys.version.split()[0], "is_windows": sys.platform.startswith("win")},
        "processes": _processes(settings.process_names),
    }
    reg = com_probe.scan_registry()
    report["registry"] = reg
    inst = _install_dirs(reg)
    report["install_dirs"] = inst
    report["documentation_files"] = _docs(inst)
    dirs = list(settings.project_dirs)
    for guess in (str(Path.home() / "Documents" / "WinGPC"), r"C:\WinGPC\Data", r"C:\PSS\Data", r"C:\Data"):
        if os.path.isdir(guess) and guess not in dirs:
            dirs.append(guess)
    report["project_dirs"] = dirs
    report["file_census"] = files.file_type_census(dirs) if dirs else {}
    report["recent_files"] = files.list_recent(dirs, None, limit=30) if dirs else []

    # UI
    try:
        from .backends.uia import UIA
        uia = UIA(settings.main_window_title_re)
        report["windows"] = uia.windows()
        if ui_tree:
            try:
                tree = uia.tree(settings.main_window_title_re, max_depth=depth)
                report["ui_tree_main"] = tree.to_dict()
                report["menu_items"] = _collect_menu_names(tree.to_dict())
            except ElementNotFound as e:
                report["ui_tree_main"] = {"error": str(e)}
        report["plot_candidates"] = _candidate_plot_controls(uia, settings.plot.window_title_re)
    except BackendUnavailable as e:
        report["windows"] = {"error": str(e)}

    report["cache_folders"] = _cache_folders(settings)
    report["duration_s"] = round(time.time() - t0, 2)
    report["next_steps"] = _advice(report)
    out_dir = Path(settings.state_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "discovery.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def _cache_folders(settings: Settings) -> list[dict[str, Any]]:
    """WinGPC's cache folder C:\\wingpc_8#n holds WinGPC_8.INI (default
    directories), the ChromPilot method/sequence/configuration XML files, the
    encrypted logbooks and Instrumentn.* default methods (manual p. 550-552).
    Report what is there and whether the ChromPilot files are plain XML (then the
    template strategy can render them)."""
    out: list[dict[str, Any]] = []
    import glob as _glob
    pats = list(settings.cache_dirs) + [r"C:\wingpc_8#*"]
    seen: set[str] = set()
    for pat in pats:
        for d in _glob.glob(pat):
            if d in seen or not os.path.isdir(d):
                continue
            seen.add(d)
            row: dict[str, Any] = {"dir": d, "files": sorted(os.listdir(d))[:200]}
            ini = os.path.join(d, "WinGPC_8.INI")
            if os.path.exists(ini):
                try:
                    txt = Path(ini).read_text(encoding="latin-1", errors="replace")
                    row["ini_dir_lines"] = [ln.strip() for ln in txt.splitlines()
                                            if any(k in ln.lower() for k in ("dir", "path", "folder", "verzeichnis"))][:40]
                except OSError:
                    pass
            for f in sorted(os.listdir(d)):
                fl = f.lower()
                if fl.startswith("pss.chrompilot") or fl.endswith((".spm", ".sps", ".aut")):
                    fp = os.path.join(d, f)
                    try:
                        head = Path(fp).read_bytes()[:400]
                        row.setdefault("chrompilot_files", []).append({
                            "file": f, "bytes": os.path.getsize(fp),
                            "looks_like_xml": head.lstrip().startswith((b"<?xml", b"<")),
                            "head": head.decode("utf-8", errors="replace")[:200],
                        })
                    except OSError:
                        continue
            out.append(row)
    return out


def _collect_menu_names(node: dict[str, Any], acc: list[str] | None = None, prefix: str = "") -> list[str]:
    acc = acc if acc is not None else []
    ct = node.get("control_type", "")
    nm = node.get("name", "")
    if ct in ("MenuItem", "MenuBar", "Menu") and nm:
        acc.append(f"{prefix}{nm}")
    for c in node.get("children") or []:
        _collect_menu_names(c, acc, prefix + (nm + " > " if ct == "MenuItem" and nm else ""))
    return acc


def _advice(r: dict[str, Any]) -> list[str]:
    tips = []
    if not r["host"]["is_windows"]:
        tips.append("Run this probe on the Windows PC that has WinGPC installed.")
        return tips
    if not r["processes"]:
        tips.append("WinGPC is not running. Start it, load a project, open the Raw Data window, re-run the probe.")
    reg = r.get("registry", {})
    if reg.get("progids") or reg.get("typelibs"):
        tips.append("COM ProgIDs/TypeLibs found. Try `wingpc-agent com-inspect <progid>` - a real automation API beats UI clicking.")
    else:
        tips.append("No WinGPC COM automation registered. Control will go through UI Automation (recipes).")
    if r.get("documentation_files"):
        tips.append("Read the Documentation folder (ChromPilot Control docs) for any LIMS/automation hooks.")
    if r.get("menu_items"):
        tips.append("Compare recipes/wingpc_default.yaml menu paths with report['menu_items'] and set verified: true.")
    for cf in r.get("cache_folders") or []:
        xmls = [c for c in cf.get("chrompilot_files", []) if c.get("looks_like_xml")]
        if xmls:
            tips.append(f"{cf['dir']}: ChromPilot method/sequence files are XML ({', '.join(x['file'] for x in xmls[:3])}). "
                        "Export a *.spm with a gradient from the Instrument Manager and use it as the template strategy's template.")
        if cf.get("ini_dir_lines"):
            tips.append(f"WinGPC_8.INI default directories: {cf['ini_dir_lines'][:5]} -> put the project folder into project_dirs / gbl.automation_dir.")
    if r.get("plot_candidates"):
        c = r["plot_candidates"][0]
        tips.append(f"Likely plot control: {c['control_type']} class={c['class_name']} rect={c['rect']}. Put its UIA path in plot.control_path or its rect in plot.rect.")
    if not r.get("file_census"):
        tips.append("Set project_dirs in the config so exports/raw files can be watched.")
    return tips
