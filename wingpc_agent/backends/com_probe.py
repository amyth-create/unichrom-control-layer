"""Look for an automation surface WinGPC might register but not advertise.

The 1990s-2000s UniChrom lineage mentioned OLE Automation; PSS WinGPC is a
.NET/Win32 hybrid. If an installer registered a COM server (ProgID / TypeLib)
we can drive it directly instead of clicking. This module only *reports*; it
never instantiates anything unless `try_instantiate` is explicitly called.
"""
from __future__ import annotations

import sys
from typing import Any

_IS_WIN = sys.platform.startswith("win")

KEYWORDS = ("wingpc", "unichrom", "chrompilot", "pss", "polymer", "gpc")


def scan_registry(keywords=KEYWORDS) -> dict[str, Any]:
    if not _IS_WIN:
        return {"available": False, "reason": "not Windows"}
    import winreg  # type: ignore

    found: dict[str, list[dict[str, str]]] = {"progids": [], "typelibs": [], "clsids": [], "uninstall": [], "app_paths": []}

    def _iter(hive, path):
        try:
            with winreg.OpenKey(hive, path) as k:
                i = 0
                while True:
                    try:
                        yield winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
        except OSError:
            return

    # ProgIDs live directly under HKCR
    for name in _iter(winreg.HKEY_CLASSES_ROOT, ""):
        low = name.lower()
        if any(k in low for k in keywords) and "." in name:
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"{name}\\CLSID") as k:
                    clsid, _ = winreg.QueryValueEx(k, "")
            except OSError:
                clsid = ""
            found["progids"].append({"progid": name, "clsid": clsid})
    # TypeLibs
    for tl in _iter(winreg.HKEY_CLASSES_ROOT, "TypeLib"):
        for ver in _iter(winreg.HKEY_CLASSES_ROOT, f"TypeLib\\{tl}"):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"TypeLib\\{tl}\\{ver}") as k:
                    desc, _ = winreg.QueryValueEx(k, "")
            except OSError:
                desc = ""
            if any(kw in str(desc).lower() for kw in keywords):
                found["typelibs"].append({"guid": tl, "version": ver, "description": desc})
    # Installed programs + App Paths tell us the install dir
    for hive, path in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ):
        for sub in _iter(hive, path):
            try:
                with winreg.OpenKey(hive, f"{path}\\{sub}") as k:
                    name, _ = winreg.QueryValueEx(k, "DisplayName")
                    if not any(kw in str(name).lower() for kw in keywords):
                        continue
                    row = {"name": str(name)}
                    for v in ("DisplayVersion", "InstallLocation", "Publisher", "UninstallString"):
                        try:
                            row[v] = str(winreg.QueryValueEx(k, v)[0])
                        except OSError:
                            pass
                    found["uninstall"].append(row)
            except OSError:
                continue
    for hive, path in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
    ):
        for sub in _iter(hive, path):
            if any(kw in sub.lower() for kw in keywords):
                try:
                    with winreg.OpenKey(hive, f"{path}\\{sub}") as k:
                        found["app_paths"].append({"exe": sub, "path": str(winreg.QueryValueEx(k, "")[0])})
                except OSError:
                    pass
    found["available"] = True  # type: ignore[assignment]
    return found


def try_instantiate(progid: str) -> dict[str, Any]:
    """Late-bound COM instantiation + member listing. Explicit opt-in only."""
    if not _IS_WIN:
        return {"ok": False, "reason": "not Windows"}
    try:
        import win32com.client  # type: ignore
        obj = win32com.client.Dispatch(progid)
        members = []
        try:
            ti = obj._oleobj_.GetTypeInfo()
            attr = ti.GetTypeAttr()
            for i in range(attr.cFuncs):
                fd = ti.GetFuncDesc(i)
                members.append(ti.GetNames(fd.memid)[0])
        except Exception:
            members = [m for m in dir(obj) if not m.startswith("_")]
        return {"ok": True, "progid": progid, "members": sorted(set(members))[:300]}
    except Exception as e:
        return {"ok": False, "progid": progid, "error": repr(e)}
