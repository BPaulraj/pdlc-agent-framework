#!/usr/bin/env python3
"""Start or resume PDLC runs for user stories scoped into the team's current sprint.

For each story in @CurrentIteration (matching story_types / excluded_states / optional start_tag):
  - no run yet                    -> init the run and launch `claude -p "/pdlc-run US-<id>"`
  - run waiting for clarification -> relaunch when the clarification task has been closed
  - anything else                 -> left alone (gates wait for a human; see /pdlc-status)

Usage:
  python scripts/sprint_watcher.py                 # one pass
  python scripts/sprint_watcher.py --interval 15   # poll every 15 minutes
  python scripts/sprint_watcher.py --dry-run       # list what would happen

Schedule one pass every 15 minutes with Windows Task Scheduler, cron, or a CI job on this machine.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, show_path, die, load_config, run_path, story_key  # noqa: E402


def in_sprint_story_ids(cfg) -> list[int]:
    from ado import Ado
    a = cfg["azure_devops"]
    types = ", ".join(f"'{t}'" for t in a["story_types"])
    states = ", ".join(f"'{s}'" for s in a["excluded_states"])
    query = ("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project "
             f"AND [System.WorkItemType] IN ({types}) AND [System.State] NOT IN ({states}) "
             "AND [System.IterationPath] = @CurrentIteration")
    if a.get("start_tag"):
        query += f" AND [System.Tags] CONTAINS '{a['start_tag']}'"
    return Ado(cfg).wiql(query + " ORDER BY [Microsoft.VSTS.Common.BacklogPriority] ASC")


def next_action(story: str) -> dict:
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "pdlc.py"), "next", story],
                       capture_output=True, text=True, cwd=ROOT)
    return json.loads(r.stdout) if r.returncode == 0 else {"action": "error", "detail": r.stderr.strip()}


LOCK_MAX_AGE_S = 3 * 3600


def launch(story: str, dry_run: bool) -> None:
    log_dir = ROOT / ".pdlc" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    lock = ROOT / ".pdlc" / "locks" / f"{story}.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < LOCK_MAX_AGE_S:
        print(f"  skip: {story} is already being processed ({show_path(lock)})")
        return
    log = log_dir / f"{story}-{datetime.now():%Y%m%d-%H%M%S}.log"
    cmd = ["claude", "-p", f"/pdlc-run {story}", "--permission-mode", "acceptEdits"]
    print(f"  launch: {' '.join(cmd)}  (log: {show_path(log)})")
    if dry_run:
        return
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(datetime.now()), encoding="utf-8")
    try:
        with open(log, "w", encoding="utf-8") as fh:
            subprocess.run(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT, shell=sys.platform == "win32")
    finally:
        lock.unlink(missing_ok=True)


def one_pass(cfg, dry_run: bool) -> None:
    ids = in_sprint_story_ids(cfg)
    print(f"{datetime.now():%Y-%m-%d %H:%M} {len(ids)} stories in the current sprint")
    for wid in ids:
        story = story_key(wid)
        if not (run_path(cfg, story) / "state.json").exists():
            print(f"{story}: new in sprint -> starting")
            if not dry_run:
                subprocess.run([sys.executable, str(ROOT / "scripts" / "pdlc.py"), "init", story], cwd=ROOT, check=True)
            launch(story, dry_run)
            continue
        nxt = next_action(story)
        if nxt["action"] in ("run_agent", "run_script"):
            print(f"{story}: ready at {nxt['stage']} -> resuming")
            launch(story, dry_run)
        else:
            print(f"{story}: {nxt['action']} {nxt.get('gate') or nxt.get('stage') or ''}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=0, help="minutes between passes; 0 = run once")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    if not cfg["azure_devops"].get("enabled"):
        die("azure_devops.enabled is false; the watcher needs ADO. Start offline runs with /pdlc-run instead.")
    while True:
        one_pass(cfg, args.dry_run)
        if not args.interval:
            return
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
