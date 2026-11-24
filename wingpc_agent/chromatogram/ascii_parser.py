"""Parse WinGPC ASCII exports.

WinGPC's Data Editor "ASCII export..." writes a text table whose column
separator and decimal sign are user-configurable (export filter), optionally
preceded by header/method lines. "Save ASCII Report" writes several blocks
(raw chromatograms, elugram, MWD, method) in one file. We do not assume a fixed
layout: the parser auto-detects delimiter + decimal sign, skips non-numeric
header lines, and splits blocks on runs of blank lines / non-numeric titles.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

_NUM_RE = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")


@dataclass
class Table:
    title: str
    columns: list[str]
    data: np.ndarray            # shape (n_rows, n_cols), float64
    header_lines: list[str] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)   # "key: value" header lines

    @property
    def x(self) -> np.ndarray:
        return self.data[:, 0]

    def y(self, col: int = 1) -> np.ndarray:
        return self.data[:, col]

    def summary(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "title": self.title,
            "columns": self.columns,
            "rows": int(self.data.shape[0]),
            "meta": self.meta,
        }
        if self.data.size:
            d["x_range"] = [float(self.data[:, 0].min()), float(self.data[:, 0].max())]
            d["y_ranges"] = [[float(self.data[:, c].min()), float(self.data[:, c].max())]
                             for c in range(1, self.data.shape[1])]
        return d


def detect_dialect(lines: list[str]) -> tuple[str, str]:
    """Return (delimiter, decimal). Scores candidates on numeric-looking rows."""
    cands = ["\t", ";", ",", " "]
    best = ("\t", ".")
    best_key = (-1, 0)
    sample = [ln for ln in lines if ln.strip()][:400]
    for delim in cands:
        for dec in (".", ","):
            if delim == "," and dec == ",":
                continue
            score = 0
            thousands = 0
            for ln in sample:
                parts = _split(ln, delim)
                if len(parts) < 2:
                    continue
                nums = sum(1 for p in parts if _is_num(p, dec))
                if nums == len(parts):
                    score += len(parts)
                    # '9,011' under dec='.' only works as a thousands group; a
                    # dialect that needs that trick on many tokens is unlikely.
                    thousands += sum(1 for p in parts if _needs_thousands(p, dec))
            key = (score, -thousands)
            if key > best_key:
                best_key, best = key, (delim, dec)
    return best


def _needs_thousands(tok: str, dec: str) -> bool:
    other = "," if dec == "." else "."
    return other in tok


def _split(line: str, delim: str) -> list[str]:
    if delim == " ":
        return [p for p in re.split(r"\s+", line.strip()) if p]
    return [p.strip() for p in line.rstrip("\r\n").split(delim)]


_THOUSANDS_DOT = re.compile(r"^[+-]?\d{1,3}(\.\d{3})+(,\d+)?$")    # 1.234.567,89
_THOUSANDS_COMMA = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")  # 1,234,567.89


def _normalise(tok: str, dec: str) -> str | None:
    """Return a '.'-decimal string, or None if the token cannot be a number
    under this dialect. Thousands separators are accepted only in the strict
    1,234,567.89 / 1.234.567,89 shapes, so '1,75' under dec='.' is rejected
    (it is a decimal-comma number) instead of silently becoming 175."""
    t = tok.replace(" ", "").replace("\u00a0", "")
    if not t:
        return None
    if dec == ",":
        if "." in t:
            if not _THOUSANDS_DOT.match(t):
                return None
            t = t.replace(".", "")
        return t.replace(",", ".")
    if "," in t:
        if not _THOUSANDS_COMMA.match(t):
            return None
        t = t.replace(",", "")
    return t


def _is_num(tok: str, dec: str) -> bool:
    t = _normalise(tok, dec)
    return bool(t and _NUM_RE.match(t))


def _to_float(tok: str, dec: str) -> float:
    t = _normalise(tok, dec)
    if t is None:
        raise ValueError(tok)
    return float(t)


def parse(text: str) -> list[Table]:
    lines = text.splitlines()
    delim, dec = detect_dialect(lines)
    tables: list[Table] = []
    header: list[str] = []
    meta: dict[str, str] = {}
    cols: list[str] | None = None
    rows: list[list[float]] = []
    title = ""

    def flush():
        nonlocal rows, cols, header, meta, title
        if rows:
            width = max(len(r) for r in rows)
            arr = np.full((len(rows), width), np.nan)
            for i, r in enumerate(rows):
                arr[i, :len(r)] = r
            # drop all-NaN columns (ragged exports)
            keep = ~np.all(np.isnan(arr), axis=0)
            arr = arr[:, keep]
            names = cols[:arr.shape[1]] if cols and len(cols) >= arr.shape[1] else [f"col{i}" for i in range(arr.shape[1])]
            tables.append(Table(title or f"block{len(tables) + 1}", names, arr, header[:], dict(meta)))
        rows, cols, header, meta, title = [], None, [], {}, ""

    for ln in lines:
        s = ln.strip()
        if not s:
            if rows:
                flush()
            continue
        parts = _split(ln, delim)
        numeric = len(parts) >= 1 and all(_is_num(p, dec) for p in parts)
        if numeric:
            rows.append([_to_float(p, dec) for p in parts])
            continue
        # non-numeric line
        if rows:
            flush()
        header.append(s)
        if ":" in s and len(parts) == 1:
            k, _, v = s.partition(":")
            if k.strip() and len(k) < 60:
                meta[k.strip()] = v.strip()
        # A line with >=2 tokens right before numbers is probably the column header
        if len(parts) >= 2 and not any(_is_num(p, dec) for p in parts):
            cols = parts
        elif len(parts) == 1 and not title:
            title = s[:120]
    flush()
    return tables


def parse_file(path: str, encoding: str | None = None) -> list[Table]:
    raw = open(path, "rb").read()
    for enc in ([encoding] if encoding else []) + ["utf-8-sig", "cp1252", "latin-1"]:
        try:
            return parse(raw.decode(enc))
        except (UnicodeDecodeError, LookupError, TypeError):
            continue
    return parse(raw.decode("latin-1", errors="replace"))
