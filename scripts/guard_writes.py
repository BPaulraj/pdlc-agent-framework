#!/usr/bin/env python3
"""Claude Code PreToolUse hook: keep agents out of places only scripts may write.

Blocks (exit code 2, reason shown to the agent):
  - Write/Edit/NotebookEdit into paths.application_repo, paths.automation_repo or the apply worktrees.
    Approved automation reaches the repo only through `python scripts/pdlc.py apply`.
  - Write/Edit of runs/*/state.json (approvals and stage state are owned by scripts/pdlc.py).
  - Bash/PowerShell commands that name a protected repo path together with a write-like operation.
    This is best-effort; the primary control is that design/review agents have no shell at all.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

WRITE_OPS = re.compile(
    r"(>|\brm\b|\bdel\b|\bmv\b|\bcp\b|\bmove\b|\bcopy\b|\bmkdir\b|\btouch\b|\bsed\s+-i|\btee\b|"
    r"\bgit\s+(-C\s+\S+\s+)?(commit|push|checkout|switch|reset|restore|clean|merge|rebase|stash|apply|am|rm|mv|add|worktree\s+add|branch\s+-[dDmM])|"
    r"Set-Content|Add-Content|Out-File|New-Item|Remove-Item|Move-Item|Copy-Item|Rename-Item)", re.I)


def norm(p: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(p))).replace("\\", "/").rstrip("/")


def inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def block(reason: str) -> None:
    print(f"BLOCKED by pdlc guard: {reason}", file=sys.stderr)
    sys.exit(2)


def main():
    try:
        event = json.load(sys.stdin)
        from common import ROOT, configured_path, load_config
        cfg = load_config()
    except Exception:  # noqa: BLE001 - never break the session because of the guard itself
        sys.exit(0)
    protected = {}
    for key in ("application_repo", "automation_repo", "worktrees_dir"):
        p = configured_path(cfg, key)
        if p:
            protected[key] = norm(p)
    tool = event.get("tool_name", "")
    tin = event.get("tool_input") or {}

    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        target = tin.get("file_path") or tin.get("notebook_path")
        if not target:
            sys.exit(0)
        t = norm(target if os.path.isabs(target) else Path(event.get("cwd") or ROOT) / target)
        for key, root in protected.items():
            if inside(t, root):
                block(f"{target} is inside paths.{key}, which agents may not edit. Stage proposed files under "
                      f"runs/<story>/10-automation-design/files/ instead; `pdlc.py apply` applies them after approval.")
        if re.search(r"/runs/us-\d+/state\.json$", t):
            block("state.json is owned by scripts/pdlc.py; use its commands (complete, fail, approve, ...)")

    if tool in ("Bash", "PowerShell"):
        cmd = tin.get("command", "")
        if re.match(r"\s*(python|py)(\.exe)?\s+(\S*/)?scripts/(pdlc|ado|index_steps)\.py\b", cmd.replace("\\", "/")):
            sys.exit(0)
        flat = os.path.normcase(cmd).replace("\\", "/")
        for key, root in protected.items():
            if root in flat and WRITE_OPS.search(cmd):
                block(f"command writes into paths.{key}. Repository changes go through `python scripts/pdlc.py apply`.")
    sys.exit(0)


if __name__ == "__main__":
    main()
