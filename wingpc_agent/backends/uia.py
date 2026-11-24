"""Windows UI Automation backend built on pywinauto.

WinGPC UniChrom is a classic Win32/.NET desktop app. We use the "uia" backend
first (exposes menus, buttons, text boxes, lists, status bars for WinForms /
WPF / most Win32 controls) and fall back to the "win32" backend for the
legacy windows that UIA does not describe.

The LLM never gets raw coordinates by default; it addresses controls by UIA
paths (see recipes.py), which the probe dumps. Coordinates are a last resort
(`click_xy`) and are logged.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any, Iterable

from ..errors import BackendUnavailable, ElementNotFound

_IS_WIN = sys.platform.startswith("win")


def _require():
    if not _IS_WIN:
        raise BackendUnavailable("UIA backend needs Windows + pywinauto (pip install 'wingpc-agent[windows]')")
    try:
        import pywinauto  # noqa: F401
    except ImportError as e:  # pragma: no cover - windows only
        raise BackendUnavailable("pywinauto missing: pip install 'wingpc-agent[windows]'") from e


@dataclass
class ElementInfo:
    control_type: str
    name: str
    auto_id: str
    class_name: str
    rect: list[int]            # l, t, r, b
    enabled: bool
    visible: bool
    value: str | None = None   # text/value pattern if any
    path: str | None = None    # resolvable path for later calls
    children: list["ElementInfo"] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("children") is None:
            d.pop("children", None)
        return d


class UIA:
    def __init__(self, main_title_re: str = r".*WinGPC.*", backend: str = "uia"):
        _require()
        from pywinauto import Desktop  # type: ignore
        self.backend = backend
        self.desktop = Desktop(backend=backend)
        self.main_title_re = main_title_re

    # ------------------------------------------------------------------ windows
    def windows(self) -> list[dict[str, Any]]:
        out = []
        for w in self.desktop.windows():
            try:
                out.append({
                    "title": w.window_text(),
                    "class": w.class_name(),
                    "pid": w.process_id(),
                    "rect": _rect(w),
                    "visible": w.is_visible(),
                })
            except Exception:
                continue
        return out

    def window(self, title_re: str | None = None, timeout: float = 5.0):
        from pywinauto.timings import TimeoutError as PwTimeout  # type: ignore
        title_re = title_re or self.main_title_re
        try:
            w = self.desktop.window(title_re=title_re)
            w.wait("exists", timeout=timeout)
            return w
        except PwTimeout as e:
            raise ElementNotFound(f"no window matching {title_re!r}") from e

    def focus(self, title_re: str | None = None) -> None:
        w = self.window(title_re)
        try:
            w.set_focus()
        except Exception:
            # minimized / cross-process focus can fail; restore then retry once
            try:
                w.restore()
                w.set_focus()
            except Exception:
                pass

    # ------------------------------------------------------------------ tree
    def tree(self, title_re: str | None = None, max_depth: int = 6, max_children: int = 80) -> ElementInfo:
        w = self.window(title_re)
        return self._describe(w, depth=0, max_depth=max_depth, max_children=max_children,
                              path=f"{title_re or self.main_title_re}")

    def _describe(self, el, depth: int, max_depth: int, max_children: int, path: str) -> ElementInfo:
        info = _info(el)
        info.path = path
        if depth < max_depth:
            kids = []
            try:
                children = el.children()
            except Exception:
                children = []
            for i, c in enumerate(children[:max_children]):
                ci = _info(c)
                seg = f"{ci.control_type}:{ci.name}" if ci.name else (
                    f"auto_id={ci.auto_id}" if ci.auto_id else f"{ci.control_type}:index={i}")
                kids.append(self._describe(c, depth + 1, max_depth, max_children, f"{path}|{seg}"))
            info.children = kids
        return info

    def find(self, title_re: str | None, name_re: str | None = None, control_type: str | None = None,
             max_results: int = 50) -> list[ElementInfo]:
        w = self.window(title_re)
        out: list[ElementInfo] = []
        rx = re.compile(name_re) if name_re else None
        for el in w.descendants():
            try:
                ci = _info(el)
            except Exception:
                continue
            if control_type and ci.control_type.lower() != control_type.lower():
                continue
            if rx and not rx.search(ci.name or ""):
                continue
            out.append(ci)
            if len(out) >= max_results:
                break
        return out

    # ------------------------------------------------------------------ resolve
    def resolve(self, path: str, timeout: float = 5.0):
        """`<title regex>|<seg>|<seg>...` -> pywinauto wrapper.

        seg := ctrl_type:Name | auto_id=X | index=N | ctrl_type:index=N
        """
        parts = path.split("|")
        el = self.window(parts[0], timeout=timeout)
        for seg in parts[1:]:
            el = self._child(el, seg)
        return el

    def _child(self, parent, seg: str):
        kwargs: dict[str, Any] = {}
        if seg.startswith("auto_id="):
            kwargs["auto_id"] = seg[len("auto_id="):]
        elif seg.startswith("index="):
            idx = int(seg[len("index="):])
            kids = parent.children()
            if idx >= len(kids):
                raise ElementNotFound(f"index {idx} out of range under {seg}")
            return kids[idx]
        else:
            ctype, _, name = seg.partition(":")
            if ctype:
                kwargs["control_type"] = ctype
            if name.startswith("index="):
                kids = parent.children(control_type=ctype) if ctype else parent.children()
                idx = int(name[len("index="):])
                if idx >= len(kids):
                    raise ElementNotFound(f"{seg}: only {len(kids)} children")
                return kids[idx]
            if name:
                kwargs["title_re"] = re.escape(name) if not _looks_like_regex(name) else name
        try:
            child = parent.child_window(**kwargs)
            child.wait("exists", timeout=3)
            return child
        except Exception as e:
            raise ElementNotFound(f"segment {seg!r} not found (kwargs={kwargs})") from e

    # ------------------------------------------------------------------ actions
    def click(self, path: str, double: bool = False) -> dict[str, Any]:
        el = self.resolve(path)
        try:
            if double:
                el.double_click_input()
            else:
                # Prefer the UIA Invoke pattern - does not move the mouse.
                try:
                    el.invoke()
                except Exception:
                    el.click_input()
        except Exception as e:
            raise ElementNotFound(f"click failed on {path}: {e}") from e
        return _info(el).to_dict()

    def click_xy(self, x: int, y: int, double: bool = False) -> None:
        from pywinauto import mouse  # type: ignore
        if double:
            mouse.double_click(coords=(x, y))
        else:
            mouse.click(coords=(x, y))

    def set_text(self, path: str, text: str) -> None:
        el = self.resolve(path)
        try:
            el.set_edit_text(text)
        except Exception:
            el.set_focus()
            el.type_keys("^a{BACKSPACE}", set_foreground=True)
            el.type_keys(text, with_spaces=True, with_tabs=True, set_foreground=True)

    def send_keys(self, keys: str, title_re: str | None = None) -> None:
        from pywinauto.keyboard import send_keys  # type: ignore
        self.focus(title_re)
        send_keys(keys, with_spaces=True, pause=0.02)

    def menu(self, path: Iterable[str], title_re: str | None = None) -> None:
        w = self.window(title_re)
        items = list(path)
        try:
            w.menu_select("->".join(items))
            return
        except Exception:
            pass
        # Fallback: walk MenuBar via UIA (apps with owner-drawn or .NET menus)
        try:
            bar = w.child_window(control_type="MenuBar")
            cur = bar
            for i, item in enumerate(items):
                mi = cur.child_window(title_re=_loose(item), control_type="MenuItem")
                mi.wait("exists", timeout=3)
                try:
                    mi.expand() if i < len(items) - 1 else mi.invoke()
                except Exception:
                    mi.click_input()
                time.sleep(0.15)
                cur = self.desktop  # submenus are top-level popups in UIA
                if i < len(items) - 1:
                    cur = self.desktop.window(control_type="Menu", found_index=0)
        except Exception as e:
            raise ElementNotFound(f"menu path {items} not found: {e}") from e

    def select(self, path: str, item: str) -> None:
        el = self.resolve(path)
        try:
            el.select(item)
        except Exception as e:
            raise ElementNotFound(f"cannot select {item!r} in {path}: {e}") from e

    def read_text(self, path: str) -> str:
        el = self.resolve(path)
        for getter in ("get_value", "window_text", "texts"):
            try:
                v = getattr(el, getter)()
                if isinstance(v, list):
                    v = "\n".join(map(str, v))
                if v:
                    return str(v)
            except Exception:
                continue
        return ""

    def wait(self, title_re: str | None = None, path: str | None = None, timeout: float = 10, gone: bool = False) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if path:
                    self.resolve(path, timeout=0.5)
                else:
                    self.window(title_re, timeout=0.5)
                exists = True
            except ElementNotFound:
                exists = False
            if exists != gone:
                return True
            time.sleep(0.2)
        return False

    def close_window(self, title_re: str) -> None:
        self.window(title_re).close()

    def rect(self, path: str) -> list[int]:
        return _rect(self.resolve(path))


# ---------------------------------------------------------------------- helpers
def _rect(el) -> list[int]:
    r = el.rectangle()
    return [r.left, r.top, r.right, r.bottom]


def _info(el) -> ElementInfo:
    ei = el.element_info
    value = None
    try:
        if hasattr(el, "get_value"):
            value = el.get_value()
    except Exception:
        value = None
    try:
        rect = _rect(el)
    except Exception:
        rect = [0, 0, 0, 0]
    return ElementInfo(
        control_type=str(getattr(ei, "control_type", "") or ""),
        name=str(getattr(ei, "name", "") or ""),
        auto_id=str(getattr(ei, "automation_id", "") or ""),
        class_name=str(getattr(ei, "class_name", "") or ""),
        rect=rect,
        enabled=bool(getattr(ei, "enabled", True)),
        visible=bool(getattr(ei, "visible", True)),
        value=value,
    )


def _looks_like_regex(s: str) -> bool:
    return any(ch in s for ch in ".*?[]()|^$\\")


def _loose(item: str) -> str:
    """'ASCII export...' -> regex tolerant of accelerators/ellipsis: 'ASCII export'."""
    core = re.escape(item.replace("&", "").rstrip(". "))
    return rf"^{core}.*"
