"""Recipes: named, data-driven UI procedures.

The exact menu names in WinGPC UniChrom are NOT publicly documented and differ
between versions and language packs, so no menu path is hard-coded in Python.
Each recipe in recipes/*.yaml is a list of steps; the probe (`wingpc-agent
probe`) dumps the real UI tree so the YAML can be corrected, after which the
operator sets `verified: true`. Unverified recipes refuse to run unless
Settings.allow_unverified_recipes is set.

Step kinds:
  menu:    path: ["File", "ASCII export..."]        -> walk the menu bar
  click:   target: "<uia path>"                     -> click a control
  type:    target: "<uia path>", text: "..."        -> set text (with_spaces ok)
  keys:    keys: "^s" / "{ENTER}"                   -> pywinauto send_keys syntax
  wait:    target: "<uia path>" | title_re: ...     -> wait until exists (timeout)
  wait_gone: same, until it disappears
  sleep:   seconds: 0.5
  select:  target, item: "..."                      -> combobox / list item
  assert:  target exists (fails recipe otherwise)

A "uia path" is `WindowTitleRegex|ctrl_type:Name|ctrl_type:Name...`, e.g.
`.*WinGPC.*|MenuBar:Application|MenuItem:File`. `auto_id=...` and `index=N`
are also accepted as segments. See backends/uia.py:resolve().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import RecipeError


@dataclass
class Recipe:
    name: str
    description: str
    level: str                      # observe | control | hazardous
    steps: list[dict[str, Any]]
    verified: bool = False
    params: dict[str, str] = field(default_factory=dict)   # name -> description
    returns: str | None = None      # e.g. "path of created file"

    def render(self, **kwargs: Any) -> list[dict[str, Any]]:
        missing = [p for p in self.params if p not in kwargs]
        if missing:
            raise RecipeError(f"recipe {self.name} missing params {missing}")
        out = []
        for step in self.steps:
            out.append({k: _fmt(v, kwargs) for k, v in step.items()})
        return out


def _fmt(v: Any, kw: dict[str, Any]) -> Any:
    if isinstance(v, str):
        try:
            return v.format(**kw)
        except (KeyError, IndexError):
            return v
    if isinstance(v, list):
        return [_fmt(x, kw) for x in v]
    return v


class RecipeBook:
    def __init__(self, recipes: dict[str, Recipe], source: str = "<memory>"):
        self.recipes = recipes
        self.source = source

    @classmethod
    def load(cls, path: str | Path) -> "RecipeBook":
        p = Path(path)
        if not p.exists():
            raise RecipeError(f"recipe file not found: {p}")
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        recipes = {}
        for name, body in (data.get("recipes") or {}).items():
            recipes[name] = Recipe(
                name=name,
                description=body.get("description", ""),
                level=body.get("level", "control"),
                steps=body.get("steps") or [],
                verified=bool(body.get("verified", False)),
                params=body.get("params") or {},
                returns=body.get("returns"),
            )
        return cls(recipes, str(p))

    def get(self, name: str) -> Recipe:
        if name not in self.recipes:
            raise RecipeError(f"unknown recipe {name!r}; known: {sorted(self.recipes)}")
        return self.recipes[name]

    def summary(self) -> list[dict[str, Any]]:
        return [
            {
                "name": r.name,
                "description": r.description,
                "level": r.level,
                "verified": r.verified,
                "params": r.params,
                "returns": r.returns,
            }
            for r in self.recipes.values()
        ]
