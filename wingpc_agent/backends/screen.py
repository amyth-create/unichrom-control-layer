"""Screen capture of a window / control rectangle.

Uses `mss` when available (fast, multi-monitor aware), else Pillow's
ImageGrab (Windows/macOS only). Returns Pillow images; chromatogram
extraction works on numpy arrays derived from them.
"""
from __future__ import annotations

import io
import sys
from typing import Sequence

from PIL import Image

from ..errors import BackendUnavailable


def grab(rect: Sequence[int] | None = None) -> Image.Image:
    """rect = (left, top, right, bottom) in screen pixels; None = whole primary screen."""
    try:
        import mss  # type: ignore

        with mss.mss() as sct:
            if rect is None:
                mon = sct.monitors[1]
            else:
                l, t, r, b = rect
                mon = {"left": int(l), "top": int(t), "width": int(r - l), "height": int(b - t)}
            shot = sct.grab(mon)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except ImportError:
        pass
    if not (sys.platform.startswith("win") or sys.platform == "darwin"):
        raise BackendUnavailable("screen capture needs Windows/macOS or the `mss` package")
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=tuple(rect) if rect else None, all_screens=True).convert("RGB")


def to_png_bytes(img: Image.Image, max_width: int | None = 1400) -> bytes:
    if max_width and img.width > max_width:
        h = int(img.height * max_width / img.width)
        img = img.resize((max_width, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
