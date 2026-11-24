"""File-system side of WinGPC: project folders and ASCII exports.

WinGPC writes its own binary project/raw files (format undocumented) and can
export ASCII .txt from the Data Editor / "Save ASCII Report". We treat those
exports as ground truth for numbers; the screen path is for *live* viewing.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable


@dataclass
class FileEvent:
    path: str
    kind: str          # created | modified
    size: int
    mtime: float


def list_recent(dirs: Iterable[str], extensions: Iterable[str] | None = None, limit: int = 50,
                since_s: float | None = None) -> list[dict]:
    exts = {e.lower() for e in (extensions or [])}
    now = time.time()
    rows = []
    for d in dirs:
        p = Path(d)
        if not p.exists():
            continue
        for root, _dirs, files in os.walk(p):
            for f in files:
                fp = Path(root) / f
                if exts and fp.suffix.lower() not in exts:
                    continue
                try:
                    st = fp.stat()
                except OSError:
                    continue
                if since_s is not None and now - st.st_mtime > since_s:
                    continue
                rows.append({"path": str(fp), "size": st.st_size, "mtime": st.st_mtime})
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows[:limit]


def file_type_census(dirs: Iterable[str], max_files: int = 20000) -> dict[str, dict]:
    """Extension -> {count, example, total_bytes}. Used by the probe to learn
    what WinGPC actually writes (project, raw, method, calibration, report...)."""
    census: dict[str, dict] = {}
    n = 0
    for d in dirs:
        for root, _dirs, files in os.walk(d):
            for f in files:
                n += 1
                if n > max_files:
                    return census
                fp = Path(root) / f
                ext = fp.suffix.lower() or "<none>"
                c = census.setdefault(ext, {"count": 0, "example": str(fp), "total_bytes": 0})
                c["count"] += 1
                try:
                    c["total_bytes"] += fp.stat().st_size
                except OSError:
                    pass
    return census


class DirWatcher:
    """Polling watcher (robust on network shares where inotify/ReadDirectoryChanges
    misbehave). Uses watchdog when present, else a 1 s poll."""

    def __init__(self, dirs: Iterable[str], extensions: Iterable[str] | None,
                 on_event: Callable[[FileEvent], None], poll_s: float = 1.0):
        self.dirs = [str(d) for d in dirs]
        self.exts = {e.lower() for e in (extensions or [])}
        self.on_event = on_event
        self.poll_s = poll_s
        self._seen: dict[str, tuple[int, float]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._snapshot(initial=True)
        self._thread = threading.Thread(target=self._loop, name="wingpc-dirwatch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_s):
            self._snapshot()

    def _snapshot(self, initial: bool = False) -> None:
        for d in self.dirs:
            if not os.path.isdir(d):
                continue
            for root, _dirs, files in os.walk(d):
                for f in files:
                    fp = os.path.join(root, f)
                    if self.exts and os.path.splitext(f)[1].lower() not in self.exts:
                        continue
                    try:
                        st = os.stat(fp)
                    except OSError:
                        continue
                    key = (st.st_size, st.st_mtime)
                    prev = self._seen.get(fp)
                    self._seen[fp] = key
                    if initial or prev == key:
                        continue
                    self.on_event(FileEvent(fp, "created" if prev is None else "modified", *key))


def tail_text(path: str, max_bytes: int = 200_000, encoding: str | None = None) -> str:
    """Read the last `max_bytes` of a text export (WinGPC exports are often
    cp1252 on German/English Windows; try utf-8 first)."""
    p = Path(path)
    size = p.stat().st_size
    with open(p, "rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
        raw = fh.read()
    for enc in ([encoding] if encoding else []) + ["utf-8-sig", "cp1252", "latin-1"]:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError, TypeError):
            continue
    return raw.decode("latin-1", errors="replace")
