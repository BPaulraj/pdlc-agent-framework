#!/usr/bin/env python3
"""Golden-set evaluation harness for the PDLC pipeline.

Captures a human-approved run's artifacts as a reference baseline, lets you replay the
same story input through the pipeline again later (after a model swap, a prompt/guideline
edit, or just periodically), and reports a structural diff against the baseline.

This is deliberately NOT a quality judge — consistent with the repo's "agents propose,
scripts dispose" principle, it only computes deterministic facts: which files changed,
which list items (test cases, files, findings, ...) were added or removed by stable id,
and a full text diff for anything that changed. A human still decides whether detected
drift is an improvement or a regression. What this buys you: drift becomes visible across
many runs over time (via reports/history.jsonl), instead of only being caught — or missed —
one run at a time at the gates.

Storage: golden/<name>/{meta.json, story-input/, baseline/, reports/}. Like runs/, this may
contain real story content; golden/ is git-ignored by default (see .gitignore). Force-add a
specific case if you want it version-controlled (e.g. one built from examples/stories/).

Usage:
  python scripts/golden_eval.py capture <story> --name NAME [--force] [--allow-unapproved]
  python scripts/golden_eval.py list
  python scripts/golden_eval.py replay <name> --story <new-story> [--force]
  python scripts/golden_eval.py compare <name> --story <run-story>
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from difflib import unified_diff
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    ROOT, die, hash_artifacts, load_config, load_pipeline, load_yaml, now,
    resolve_output, run_path, sha256_file, show_path, story_key,
)

GOLDEN_DIR = ROOT / "golden"

# Preference order for a stable identifier when diffing list items by id rather than position.
ID_KEYS = ("key", "id", "path", "acceptance_criterion", "check", "title")


def case_dir(name: str) -> Path:
    return GOLDEN_DIR / name


def stage_outputs(cfg, stages, story) -> dict:
    """rel-output-path -> resolved Path, for every non-cache stage output."""
    out = {}
    for s in stages:
        for rel in s.get("outputs", []):
            if rel.startswith("{cache}"):
                continue
            out[rel] = resolve_output(cfg, story, rel)
    return out


# ================================================================ capture

def cmd_capture(cfg, args):
    story = story_key(args.story)
    run = run_path(cfg, story)
    state_p = run / "state.json"
    if not state_p.exists():
        die(f"no run for {story}; nothing to capture")
    state = json.loads(state_p.read_text(encoding="utf-8"))
    approvals = state.get("approvals", {})
    if not approvals and not args.allow_unapproved:
        die("no gate approvals recorded for this run; capturing unreviewed output as a "
            "reference baseline defeats the point. Pass --allow-unapproved to override "
            "for bootstrapping/testing only.")
    dest = case_dir(args.name)
    if dest.exists():
        if not args.force:
            die(f"golden case {args.name!r} already exists at {show_path(dest)}; use --force to overwrite")
        shutil.rmtree(dest)
    stages = load_pipeline()

    # story input: whatever intake captured, plus any clarification answers, so it can be replayed
    input_dir = dest / "story-input"
    input_dir.mkdir(parents=True, exist_ok=True)
    intake = run / "01-intake"
    if intake.exists():
        for p in intake.rglob("*"):
            if p.is_file():
                target = input_dir / p.relative_to(intake)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, target)
    clar = run / "03-clarification"
    if clar.exists():
        for p in clar.glob("answers*"):
            shutil.copyfile(p, input_dir / p.name)

    # baseline: every produced stage output that exists
    baseline_dir = dest / "baseline"
    copied = {}
    for rel, p in stage_outputs(cfg, stages, story).items():
        if p.exists():
            target = baseline_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
            copied[rel] = sha256_file(p)

    meta = {
        "name": args.name,
        "captured_at": now(),
        "captured_from_story": story,
        "approvals": {g: {"by": a["by"], "at": a["at"], "hash": a["hash"]} for g, a in approvals.items()},
        "baseline_files": copied,
        "guideline_hash": hash_artifacts(ROOT, ["guidelines/"])[0],
        "template_hash": hash_artifacts(ROOT, ["templates/"])[0],
    }
    (dest / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"captured golden case {args.name!r} from {story}: {len(copied)} baseline files "
          f"({len(approvals)} gate approval(s) recorded) -> {show_path(dest)}")


# ================================================================ list

def cmd_list(cfg, args):
    if not GOLDEN_DIR.exists():
        print("no golden cases yet")
        return
    found = False
    for d in sorted(GOLDEN_DIR.iterdir()):
        meta_p = d / "meta.json"
        if not meta_p.exists():
            continue
        found = True
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        history = d / "reports" / "history.jsonl"
        last = ""
        if history.exists():
            lines = [l for l in history.read_text(encoding="utf-8").splitlines() if l.strip()]
            if lines:
                h = json.loads(lines[-1])
                last = (f" | last compare {h['at']} vs {h['run_story']}: "
                        f"{h['files_changed']}/{h['files_compared']} files changed")
        print(f"{meta['name']:<24} from {meta['captured_from_story']:<10} "
              f"{len(meta['baseline_files'])} files  captured {meta['captured_at']}{last}")
    if not found:
        print("no golden cases yet")


# ================================================================ replay

def cmd_replay(cfg, args):
    dest = case_dir(args.name)
    if not (dest / "meta.json").exists():
        die(f"no golden case {args.name!r} at {show_path(dest)}")
    story_file = dest / "story-input" / "story.json"
    if not story_file.exists():
        die(f"golden case {args.name!r} has no captured story.json to replay from")
    story = story_key(args.story)
    cmd = [sys.executable, str(ROOT / "scripts" / "pdlc.py"), "init", story,
           "--story-file", str(story_file)]
    if args.force:
        cmd.append("--force")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(r.returncode)
    if list((dest / "story-input").glob("answers*")):
        print(f"note: golden case {args.name!r} had clarification answers on file; this replay starts "
              f"fresh and will pause at 'await-clarification' again. Captured answers are at "
              f"{show_path(dest / 'story-input')} if you want to reuse them verbatim.")
    print(f"replay run initialised. Continue with: python scripts/pdlc.py next {story} --claim   "
          f"(or /pdlc-run {story})\nWhen it reaches the stage(s) you want to check, run:\n"
          f"  python scripts/golden_eval.py compare {args.name} --story {story}")


# ================================================================ compare

def summarize(data) -> dict:
    """List-valued top-level keys -> {count, ids} using the first identifying field found."""
    out = {}
    if not isinstance(data, dict):
        return out
    for k, v in data.items():
        if not isinstance(v, list):
            continue
        ids = []
        for item in v:
            if isinstance(item, dict):
                ident = next((item[f] for f in ID_KEYS if item.get(f) is not None), None)
                ids.append(str(ident) if ident is not None else json.dumps(item, sort_keys=True)[:80])
            else:
                ids.append(str(item))
        out[k] = ids
    return out


def load_any(p: Path):
    if p.suffix in (".yaml", ".yml"):
        return load_yaml(p)
    if p.suffix == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    return None  # text files (e.g. .feature, .md): diffed as text only, no id-level summary


def cmd_compare(cfg, args):
    dest = case_dir(args.name)
    meta_p = dest / "meta.json"
    if not meta_p.exists():
        die(f"no golden case {args.name!r} at {show_path(dest)}")
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    story = story_key(args.story)
    run = run_path(cfg, story)
    baseline_dir = dest / "baseline"
    stages = load_pipeline()

    rows = []
    diff_sections = []
    files_compared = files_changed = total_added = total_removed = 0

    for rel, base_hash in sorted(meta["baseline_files"].items()):
        base_p = baseline_dir / rel
        cand_p = run / rel
        files_compared += 1
        if not cand_p.exists():
            rows.append((rel, "MISSING", "", ""))
            files_changed += 1
            continue
        cand_hash = sha256_file(cand_p)
        if cand_hash == base_hash:
            rows.append((rel, "identical", "", ""))
            continue
        files_changed += 1
        base_data, cand_data = load_any(base_p), load_any(cand_p)
        added_s = removed_s = ""
        if isinstance(base_data, dict) and isinstance(cand_data, dict):
            bs, cs = summarize(base_data), summarize(cand_data)
            for key in sorted(set(bs) | set(cs)):
                b_ids, c_ids = set(bs.get(key, [])), set(cs.get(key, []))
                added, removed = c_ids - b_ids, b_ids - c_ids
                if added or removed:
                    added_s += f"{key}:+{len(added)} "
                    removed_s += f"{key}:-{len(removed)} "
                    total_added += len(added)
                    total_removed += len(removed)
        rows.append((rel, "CHANGED", added_s.strip(), removed_s.strip()))
        base_text = base_p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        cand_text = cand_p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        diff = "".join(unified_diff(base_text, cand_text, fromfile=f"baseline/{rel}", tofile=f"{story}/{rel}"))
        diff_sections.append(f"\n## {rel}\n\n```diff\n{diff}```\n")

    produced = {rel for rel, p in stage_outputs(cfg, stages, story).items() if p.exists()}
    for rel in sorted(produced - set(meta["baseline_files"])):
        rows.append((rel, "NEW (not in baseline)", "", ""))
        files_compared += 1
        files_changed += 1

    header = f"{'file':<48} {'status':<22} {'added':<20} {'removed':<20}"
    table = "\n".join([header, "-" * len(header)] +
                       [f"{r:<48} {s:<22} {a:<20} {rm:<20}" for r, s, a, rm in rows])
    print(table)
    print(f"\n{files_changed}/{files_compared} files changed, {total_added} items added, "
          f"{total_removed} items removed (structural diff only - not a quality judgement; "
          f"read the full report for what actually changed).")
    if meta["guideline_hash"] != hash_artifacts(ROOT, ["guidelines/"])[0]:
        print("note: guidelines/ changed since this baseline was captured; some drift may be intentional.")
    if meta["template_hash"] != hash_artifacts(ROOT, ["templates/"])[0]:
        print("note: templates/ changed since this baseline was captured; some drift may be intentional.")

    reports = dest / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = reports / f"{ts}-{story}.md"
    report_path.write_text(
        f"# Golden compare: {args.name} (baseline {meta['captured_from_story']}) vs {story}\n\n"
        f"_{now()}_\n\n```\n{table}\n```\n" + "".join(diff_sections),
        encoding="utf-8")
    with open(reports / "history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now(), "run_story": story, "files_compared": files_compared,
                             "files_changed": files_changed, "items_added": total_added,
                             "items_removed": total_removed, "report": show_path(report_path)}) + "\n")
    print(f"\nfull diff written to {show_path(report_path)}")


# ================================================================ main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("capture")
    p.add_argument("story"); p.add_argument("--name", required=True)
    p.add_argument("--force", action="store_true"); p.add_argument("--allow-unapproved", action="store_true")
    sub.add_parser("list")
    p = sub.add_parser("replay")
    p.add_argument("name"); p.add_argument("--story", required=True); p.add_argument("--force", action="store_true")
    p = sub.add_parser("compare")
    p.add_argument("name"); p.add_argument("--story", required=True)
    args = ap.parse_args()
    cfg = load_config()
    {"capture": cmd_capture, "list": cmd_list, "replay": cmd_replay,
     "compare": cmd_compare}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
