"""Action policy + audit log.

Every tool the LLM can call is classified in recipes/policy_default.yaml as
  observe  : read-only, always allowed
  control  : changes software state, allowed, logged
  hazardous: can ruin a run or damage hardware (stop sequence, change flow,
             delete data...). Requires a confirmation token obtained from a
             human through `confirm_action`, or an explicit allow-list entry.
  denied   : never executed by the agent

The LLM never sees the policy as something it can edit: the file is read by
the server process, not exposed as a tool.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ActionDenied

LEVELS = ("observe", "control", "hazardous", "denied")


@dataclass
class Policy:
    default_level: str = "control"
    levels: dict[str, str] = field(default_factory=dict)
    # Tool names a human pre-approved for unattended use even though hazardous.
    unattended_ok: set[str] = field(default_factory=set)
    confirm_ttl_s: int = 300

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        p = Path(path)
        if not p.exists():
            return cls()
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls(
            default_level=data.get("default_level", "control"),
            levels={k: v for k, v in (data.get("tools") or {}).items()},
            unattended_ok=set(data.get("unattended_ok") or []),
            confirm_ttl_s=int(data.get("confirm_ttl_s", 300)),
        )

    def level(self, tool: str) -> str:
        lvl = self.levels.get(tool, self.default_level)
        if lvl not in LEVELS:
            raise ValueError(f"bad level {lvl!r} for {tool}")
        return lvl


class Gate:
    """Decides whether a tool call may run, and records it."""

    def __init__(self, policy: Policy, audit_path: str | Path, interactive_confirm: bool = True,
                 token_file: str | Path | None = None):
        self.policy = policy
        self.audit_path = Path(audit_path)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.interactive_confirm = interactive_confirm
        self._tokens: dict[str, tuple[str, float]] = {}  # token -> (tool, expiry)
        # `wingpc-agent confirm <tool>` (run by a human in another process) writes
        # tokens here; the server consumes them once.
        self.token_file = Path(token_file) if token_file else self.audit_path.parent / "confirm_tokens.json"

    # -- confirmation tokens -------------------------------------------------
    def issue_token(self, tool: str, reason: str) -> str:
        """Called by a *human-facing* path (CLI prompt / UI), never by the LLM."""
        tok = secrets.token_urlsafe(12)
        self._tokens[tok] = (tool, time.time() + self.policy.confirm_ttl_s)
        self.audit("confirm_issued", tool, {"reason": reason})
        return tok

    def _consume(self, tool: str, token: str | None) -> bool:
        if not token:
            return False
        entry = self._tokens.pop(token, None)
        if entry is None:
            entry = self._pop_file_token(token)
        if not entry:
            return False
        t_tool, exp = entry
        return t_tool == tool and time.time() <= exp

    def _pop_file_token(self, token: str) -> tuple[str, float] | None:
        try:
            if not self.token_file.exists():
                return None
            data = json.loads(self.token_file.read_text(encoding="utf-8") or "{}")
            row = data.pop(token, None)
            # also drop expired leftovers
            now = time.time()
            data = {k: v for k, v in data.items() if float(v.get("exp", 0)) > now}
            self.token_file.write_text(json.dumps(data), encoding="utf-8")
            if row:
                return str(row["tool"]), float(row["exp"])
        except (OSError, ValueError, KeyError):
            return None
        return None

    # -- the check -----------------------------------------------------------
    def check(self, tool: str, args: dict[str, Any], token: str | None = None) -> None:
        lvl = self.policy.level(tool)
        if lvl == "denied":
            self.audit("denied", tool, args)
            raise ActionDenied(f"{tool} is denied by policy")
        if lvl == "hazardous":
            if tool in self.policy.unattended_ok or self._consume(tool, token):
                self.audit("allowed_hazardous", tool, args)
                return
            if self.interactive_confirm:
                ok = self._ask_human(tool, args)
                if ok:
                    self.audit("allowed_hazardous_interactive", tool, args)
                    return
            self.audit("needs_confirmation", tool, args)
            raise ActionDenied(
                f"{tool} is hazardous and needs human confirmation. Ask the operator to run "
                f"`wingpc-agent confirm {tool}` and pass the printed token as confirm_token."
            )
        self.audit(lvl, tool, args)

    def _ask_human(self, tool: str, args: dict[str, Any]) -> bool:
        """Blocking console prompt on the server PC. Returns False when no TTY."""
        try:
            import sys
            if not sys.stdin.isatty():
                return False
            ans = input(f"[wingpc-agent] LLM requests HAZARDOUS action {tool} {json.dumps(args)[:200]} - allow? [y/N] ")
            return ans.strip().lower() in ("y", "yes")
        except Exception:
            return False

    # -- audit ---------------------------------------------------------------
    def audit(self, event: str, tool: str, args: dict[str, Any]) -> None:
        rec = {"ts": time.time(), "event": event, "tool": tool, "args": _safe(args)}
        with open(self.audit_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")


def _safe(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return str(obj)
