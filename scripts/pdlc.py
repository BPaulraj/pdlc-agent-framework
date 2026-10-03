#!/usr/bin/env python3
"""PDLC run orchestrator: deterministic state machine, human gates, proposal sealing and apply.

The LLM orchestrator (/pdlc-run skill) only ever asks `next` what to do and reports back with
`complete` / `fail`. Everything that decides order, approval validity or repository writes lives here.

Usage:
  python scripts/pdlc.py init <story> [--story-file FILE] [--force]
  python scripts/pdlc.py next <story> [--claim]
  python scripts/pdlc.py complete <story> <stage>
  python scripts/pdlc.py fail <story> <stage> --reason TEXT
  python scripts/pdlc.py status [<story>]
  python scripts/pdlc.py gate-summary <story> <gate>
  python scripts/pdlc.py approve <story> <gate> [--by NAME] [--comment TEXT]
  python scripts/pdlc.py reject <story> <gate> --feedback TEXT [--by NAME]
  python scripts/pdlc.py verify-approval <story> <gate>
  python scripts/pdlc.py invalidate <story> --from <stage> --reason TEXT
  python scripts/pdlc.py unblock <story> <stage>
  python scripts/pdlc.py seal-proposal <story>
  python scripts/pdlc.py apply <story>
  python scripts/pdlc.py run-check <story> <compile|dry_run|run_new|run_regression>
  python scripts/pdlc.py verify-diff <story>
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    show_path,
    ROOT, cache_dir, configured_path, die, expand_files, hash_artifacts, is_set, load_config,
    load_pipeline, load_state, load_yaml, log_event, now, print_json, resolve_output, run_path,
    save_state, sha256_file, story_key, story_number,
)

DONE = ("done", "skipped")


# ================================================================ conditions

def _analysis(cfg, story) -> dict:
    p = run_path(cfg, story) / "02-analysis" / "story-analysis.yaml"
    return (load_yaml(p) or {}) if p.exists() else {}


def cond_has_open_questions(cfg, story, state) -> bool:
    if state.get("clarification", {}).get("pending_followup"):
        return True
    qs = _analysis(cfg, story).get("questions") or []
    return any((q or {}).get("status", "open") == "open" for q in qs)


def cond_clarification_answered(cfg, story, state) -> bool:
    rounds = state.get("clarification", {}).get("rounds") or []
    if not rounds:
        return False
    latest = rounds[-1]
    if latest.get("offline"):
        return (run_path(cfg, story) / latest["answers_file"]).exists()
    import ado  # lazy: only needed when ADO is enabled
    return ado.task_is_closed(cfg, latest["task_id"])


def cond_automation_index_stale(cfg, story, state) -> bool:
    meta = cache_dir(cfg) / "automation-index" / "index-meta.json"
    needed = [meta, cache_dir(cfg) / "automation-index" / "repo-profile.yaml",
              cache_dir(cfg) / "automation-index" / "components.md"]
    if not all(p.exists() for p in needed):
        return True
    recorded = json.loads(meta.read_text(encoding="utf-8")).get("commit")
    repo = configured_path(cfg, "automation_repo")
    return bool(repo) and recorded != git_rev(repo, cfg["automation"]["base_ref"])


CONDITIONS = {
    "has_open_questions": cond_has_open_questions,
    "clarification_answered": cond_clarification_answered,
    "automation_index_stale": cond_automation_index_stale,
}


# ================================================================ state helpers

def new_state(story: str, offline: bool) -> dict:
    return {"story": story_key(story), "story_id": story_number(story), "created_at": now(),
            "offline": offline, "stages": {}, "approvals": {}, "loops": {},
            "clarification": {"rounds": []}, "feedback": [], "history": []}


def stage_index(stages: list[dict], stage_id: str) -> int:
    for i, s in enumerate(stages):
        if s["id"] == stage_id:
            return i
    die(f"unknown stage {stage_id!r}")


def stage_state(state: dict, stage_id: str) -> dict:
    return state["stages"].setdefault(stage_id, {"status": "pending", "attempts": 0})


def reset_range(cfg, story, state, stages, start_id: str, end_id: str | None, reason: str) -> None:
    """Return stages [start..end] to pending; archive their run-dir outputs; drop affected approvals."""
    i0 = stage_index(stages, start_id)
    i1 = stage_index(stages, end_id) if end_id else len(stages) - 1
    if i0 <= stage_index(stages, "analysis"):
        state["clarification"]["pending_followup"] = None  # re-analysis decides what is still open
    state["completed_at"] = None
    base = run_path(cfg, story)
    archive = base / "history" / f"{len(state['history']):03d}-{start_id}"
    for s in stages[i0:i1 + 1]:
        for rel in s.get("outputs", []):
            if rel.startswith("{cache}"):
                continue
            src = base / rel
            if src.exists():
                dst = archive / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
        if s["kind"] == "gate":
            state["approvals"].pop(s["id"], None)
        if s["id"] == "automation-design":  # staged files belong to the proposal being reworked
            staged = base / "10-automation-design" / "files"
            if staged.exists():
                dst = archive / "10-automation-design" / "files"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(staged), str(dst))
        state["stages"][s["id"]] = {"status": "pending", "attempts": 0}
    log_event(state, "reset", start=start_id, end=end_id or stages[-1]["id"], reason=reason,
              archived_to=show_path(archive))


def add_feedback(cfg, story, state, source: str, text: str) -> str:
    fb_dir = run_path(cfg, story) / "feedback"
    fb_dir.mkdir(parents=True, exist_ok=True)
    name = f"{len(state['feedback']) + 1:02d}-{source}.md"
    (fb_dir / name).write_text(f"# Feedback from {source}\n\n_{now()}_\n\n{text}\n", encoding="utf-8")
    rel = f"feedback/{name}"
    state["feedback"].append(rel)
    return rel


def gate_hash(cfg, story, gate: dict) -> tuple[str, dict]:
    return hash_artifacts(run_path(cfg, story), gate["review"])


def approval_valid(cfg, story, state, gate: dict) -> bool:
    a = state["approvals"].get(gate["id"])
    return bool(a and a["decision"] == "approved" and a["hash"] == gate_hash(cfg, story, gate)[0])


def prior_inputs(cfg, story, state, stages, upto: int) -> list[str]:
    base = run_path(cfg, story)
    inputs = []
    intake = base / "01-intake"
    if intake.exists():
        inputs += [show_path(p) for p in sorted(intake.rglob("*")) if p.is_file()]
    for s in stages[:upto]:
        if state["stages"].get(s["id"], {}).get("status") != "done":
            continue
        for rel in s.get("outputs", []):
            p = resolve_output(cfg, story, rel)
            if p.exists():
                inputs.append(show_path(p))
    clar = base / "03-clarification"
    if clar.exists():
        inputs += [show_path(p) for p in sorted(clar.glob("answers*")) if p.is_file()]
    return sorted(set(inputs))


def fmt_cmd(cmd: str, story: str) -> str:
    return cmd.replace("{story}", story_key(story))


# ================================================================ commands

def cmd_init(cfg, args):
    story = story_key(args.story)
    base = run_path(cfg, story)
    if (base / "state.json").exists() and not args.force:
        print(f"{story} already initialised ({show_path(base)}); use --force to restart.")
        return
    if args.force and base.exists():
        shutil.rmtree(base)
    offline = not cfg["azure_devops"].get("enabled")
    if offline and not args.story_file:
        die("azure_devops.enabled is false: pass --story-file (see examples/stories/)")
    (base / "01-intake").mkdir(parents=True, exist_ok=True)
    if args.story_file:
        shutil.copyfile(args.story_file, base / "01-intake" / "story.json")
    state = new_state(story, offline)
    log_event(state, "init", story_file=args.story_file)
    save_state(cfg, story, state)
    print(f"Initialised {story} ({'offline' if offline else 'ADO'} mode) at {show_path(base)}")


def compute_next(cfg, story, state, claim: bool) -> dict:
    stages = load_pipeline()
    max_attempts = cfg["framework"].get("max_attempts_per_stage", 3)
    for i, s in enumerate(stages):
        st = stage_state(state, s["id"])
        if s["kind"] == "gate" and st["status"] == "done" and not approval_valid(cfg, story, state, s):
            st["status"] = "pending"  # approved artifacts were edited afterwards: ask again
            log_event(state, "approval_invalidated", gate=s["id"])
        if st["status"] in DONE:
            continue
        if s.get("enabled_by") and not cfg.get("stages", {}).get(s["enabled_by"], False):
            st.update(status="skipped", reason=f"disabled in config stages.{s['enabled_by']}")
            continue
        if s.get("run_if") and not CONDITIONS[s["run_if"]](cfg, story, state):
            st.update(status="skipped", reason=f"condition {s['run_if']} is false")
            continue
        if st["status"] == "blocked":
            return {"action": "blocked", "stage": s["id"], "reason": st.get("reason"),
                    "resolve": f"fix the cause, then: python scripts/pdlc.py unblock {story} {s['id']}"}
        if s["kind"] == "wait":
            if CONDITIONS[s["until"]](cfg, story, state):
                st.update(status="done", completed_at=now())
                log_event(state, "wait_satisfied", stage=s["id"])
                continue
            return {"action": "wait", "stage": s["id"], "message": s.get("message", "")}
        if s["kind"] == "gate":
            if approval_valid(cfg, story, state, s):
                st.update(status="done", completed_at=now())
                continue
            h, files = gate_hash(cfg, story, s)
            stale = s["id"] in state["approvals"]
            return {"action": "await_gate", "gate": s["id"], "title": s.get("title", ""),
                    "review": sorted(files), "hash": h,
                    "note": "previous approval no longer matches the artifacts" if stale else "",
                    "approve": f"/pdlc-approve {story} {s['id']}",
                    "reject": f"/pdlc-reject {story} {s['id']} <feedback>"}
        # agent / script
        if claim:
            st["attempts"] = st.get("attempts", 0) + 1
            if st["attempts"] > max_attempts:
                st.update(status="blocked", reason=f"exceeded {max_attempts} attempts")
                log_event(state, "blocked", stage=s["id"], reason=st["reason"])
                return {"action": "blocked", "stage": s["id"], "reason": st["reason"]}
            st.update(status="in_progress", started_at=now())
            log_event(state, "claimed", stage=s["id"], attempt=st["attempts"])
        out = {"action": "run_script" if s["kind"] == "script" else "run_agent",
               "story": story, "stage": s["id"], "attempt": st.get("attempts", 0),
               "run_dir": show_path(run_path(cfg, story)),
               "outputs": [show_path(resolve_output(cfg, story, o)) for o in s.get("outputs", [])],
               "complete": f"python scripts/pdlc.py complete {story} {s['id']}",
               "fail": f"python scripts/pdlc.py fail {story} {s['id']} --reason \"<why>\""}
        if s["kind"] == "script":
            out["command"] = fmt_cmd(s["command"], story)
        else:
            out.update(agent=s["agent"], before=[fmt_cmd(c, story) for c in s.get("before", [])],
                       inputs=prior_inputs(cfg, story, state, stages, i),
                       guidelines=s.get("guidelines", []), templates=s.get("templates", []),
                       feedback=[f"{out['run_dir']}/{f}" for f in state["feedback"]],
                       config="config/framework.yaml")
        return out
    if state.get("completed_at") is None:
        state["completed_at"] = now()
        log_event(state, "run_complete")
    return {"action": "complete", "story": story,
            "report": f"{show_path(run_path(cfg, story))}/16-report/traceability-report.md"}


def cmd_next(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    result = compute_next(cfg, story, state, args.claim)
    save_state(cfg, story, state)
    print_json(result)


def on_complete_check(cfg, story, state, stages, s) -> str | None:
    rule = s.get("on_complete")
    if not rule:
        return None
    base = run_path(cfg, story)
    if rule["check"] == "review_decision":
        report = load_yaml(base / rule["report"]) or {}
        decision = str(report.get("decision", "")).lower()
        if decision == "blocked":
            st = stage_state(state, s["id"])
            st.update(status="blocked", reason=report.get("summary") or "reviewer reported a blocker needing a human")
            log_event(state, "blocked", stage=s["id"], reason=st["reason"])
            return f"blocked: {st['reason']}"
        if decision in ("request_changes", "reject"):
            loops = state["loops"].get(s["id"], 0)
            if loops >= cfg["framework"].get("max_auto_rework_loops", 2):
                log_event(state, "auto_rework_exhausted", stage=s["id"])
                return "auto-rework limit reached; findings go to the human gate as-is"
            state["loops"][s["id"]] = loops + 1
            # Keep a copy of the report as feedback before the reset archives it.
            text = (base / rule["report"]).read_text(encoding="utf-8")
            fb = add_feedback(cfg, story, state, f"{s['id']}-auto-review",
                              f"Automatic rework requested by `{s['id']}` (loop {loops + 1}).\n\n```yaml\n{text}\n```")
            reset_range(cfg, story, state, stages, rule["rework_to"], s["id"], f"{s['id']} requested changes")
            return f"auto-rework #{loops + 1}: back to {rule['rework_to']} with {fb}"
    if rule["check"] == "clarification_loop":
        resolved = load_yaml(base / "03-clarification" / "resolved-requirements.yaml") or {}
        state["clarification"]["pending_followup"] = None
        if resolved.get("status") == "needs_follow_up" and resolved.get("follow_up_questions"):
            rounds = len(state["clarification"]["rounds"])
            if rounds >= cfg["framework"].get("max_clarification_rounds", 3):
                st = stage_state(state, s["id"])
                st.update(status="blocked", reason=f"still unresolved after {rounds} clarification rounds")
                log_event(state, "blocked", stage=s["id"], reason=st["reason"])
                return f"blocked: {st['reason']}"
            fu = base / "03-clarification" / f"follow-up-round-{rounds + 1}.yaml"
            fu.write_text(yaml_dump({"questions": resolved["follow_up_questions"]}), encoding="utf-8")
            state["clarification"]["pending_followup"] = fu.relative_to(base).as_posix()
            reset_range(cfg, story, state, stages, rule["rework_to"], s["id"], "follow-up clarification needed")
            return f"follow-up clarification round {rounds + 1} queued"
    return None


def yaml_dump(obj) -> str:
    import yaml
    return yaml.safe_dump(obj, sort_keys=False, allow_unicode=True)


def cmd_complete(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    stages = load_pipeline()
    s = stages[stage_index(stages, args.stage)]
    if s["kind"] in ("gate", "wait"):
        die(f"{s['id']} is a {s['kind']}; it cannot be completed directly")
    missing, invalid, outputs = [], [], {}
    for rel in s.get("outputs", []):
        p = resolve_output(cfg, story, rel)
        if not p.exists():
            missing.append(rel)
            continue
        try:
            if p.suffix in (".yaml", ".yml"):
                load_yaml(p)
            elif p.suffix == ".json":
                json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            invalid.append(f"{rel}: {e}")
        outputs[rel] = sha256_file(p)
    if missing or invalid:
        die("cannot complete " + s["id"] + ": " + "; ".join(
            [f"missing {m}" for m in missing] + [f"unparseable {x}" for x in invalid]))
    st = stage_state(state, s["id"])
    st.update(status="done", completed_at=now(), outputs=outputs)
    log_event(state, "completed", stage=s["id"])
    note = on_complete_check(cfg, story, state, stages, s)
    save_state(cfg, story, state)
    print(f"{s['id']}: done" + (f" ({note})" if note else ""))


def cmd_fail(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    st = stage_state(state, args.stage)
    max_attempts = cfg["framework"].get("max_attempts_per_stage", 3)
    blocked = st.get("attempts", 0) >= max_attempts or args.block
    st.update(status="blocked" if blocked else "pending", reason=args.reason)
    log_event(state, "failed", stage=args.stage, reason=args.reason, blocked=blocked)
    save_state(cfg, story, state)
    print(f"{args.stage}: {'blocked' if blocked else 'will retry'} — {args.reason}")


def cmd_unblock(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    st = stage_state(state, args.stage)
    st.update(status="pending", attempts=0, reason=None)
    log_event(state, "unblocked", stage=args.stage, by=who(None))
    save_state(cfg, story, state)
    print(f"{args.stage}: unblocked")


def who(name: str | None) -> str:
    if name:
        return name
    try:
        out = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True, timeout=10)
        if out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return getpass.getuser()


def _gate(stages, gate_id) -> dict:
    """Accept full ids or short forms like 'G1' / 'g2'."""
    matches = [x["id"] for x in stages if x["kind"] == "gate"
               and (x["id"].lower() == gate_id.lower() or x["id"].lower().startswith(gate_id.lower() + "-"))]
    s = stages[stage_index(stages, matches[0] if len(matches) == 1 else gate_id)]
    if s["kind"] != "gate":
        die(f"{gate_id} is not a gate")
    return s


def _require_gate_is_current(cfg, story, state, gate_id):
    nxt = compute_next(cfg, story, state, claim=False)
    if nxt.get("action") != "await_gate" or nxt.get("gate") != gate_id:
        die(f"{gate_id} is not awaiting a decision (current: {nxt.get('action')} {nxt.get('gate') or nxt.get('stage') or ''})")
    return nxt


TEST_CASES = "06-test-design/test-cases.yaml"


def open_area_path_questions(cfg, story) -> list[str]:
    doc = load_yaml(run_path(cfg, story) / TEST_CASES) or {}
    open_q = [f"{', '.join(q.get('case_keys') or [])}: {q.get('question') or q.get('module')}"
              for q in doc.get("area_path_questions") or [] if not q.get("answer")]
    open_q += [f"{tc.get('key')}: no area_path" for tc in doc.get("test_cases") or []
               if not tc.get("area_path") and not any(tc.get("key") in (q.get("case_keys") or [])
                                                      for q in doc.get("area_path_questions") or [])]
    return open_q


def cmd_approve(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    gate = _gate(load_pipeline(), args.gate)
    _require_gate_is_current(cfg, story, state, gate["id"])
    if TEST_CASES in (gate.get("review") or []):
        open_q = open_area_path_questions(cfg, story)
        if open_q:
            die("cannot approve: test cases still have open area path questions. Answer them with\n"
                f"  /pdlc-reject {story} {gate['id']} \"<case keys>: use area path <full path>\"\n"
                "(create any new area path in ADO first). Open:\n  " + "\n  ".join(open_q))
    h, files = gate_hash(cfg, story, gate)
    state["approvals"][gate["id"]] = {"decision": "approved", "by": who(args.by), "at": now(),
                                      "comment": args.comment or "", "hash": h, "files": files}
    log_event(state, "approved", gate=gate["id"], by=who(args.by), hash=h)
    save_state(cfg, story, state)
    print(f"{gate['id']} approved by {who(args.by)} (hash {h[:12]}, {len(files)} files)")


def cmd_reject(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    stages = load_pipeline()
    gate = _gate(stages, args.gate)
    _require_gate_is_current(cfg, story, state, gate["id"])
    fb = add_feedback(cfg, story, state, f"{gate['id']}-human", f"Rejected by {who(args.by)}.\n\n{args.feedback}")
    log_event(state, "rejected", gate=gate["id"], by=who(args.by), feedback=fb)
    reset_range(cfg, story, state, stages, gate["rework_to"], gate["id"], f"{gate['id']} rejected")
    save_state(cfg, story, state)
    print(f"{gate['id']} rejected; run returns to {gate['rework_to']} with {fb}. Continue with /pdlc-run {story}")


def cmd_verify_approval(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    gate = _gate(load_pipeline(), args.gate)
    if not approval_valid(cfg, story, state, gate):
        die(f"{gate['id']} has no valid approval for the current artifacts", 2)
    print(f"{gate['id']}: approval valid")


def cmd_invalidate(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    stages = load_pipeline()
    reset_range(cfg, story, state, stages, args.from_stage, None, args.reason)
    state["completed_at"] = None
    save_state(cfg, story, state)
    print(f"reset from {args.from_stage}: {args.reason}")


def cmd_status(cfg, args):
    from common import runs_dir
    if not args.story:
        rows = []
        for p in sorted(runs_dir(cfg).glob("US-*/state.json")):
            story = p.parent.name
            state = json.loads(p.read_text(encoding="utf-8"))
            nxt = compute_next(cfg, story, json.loads(json.dumps(state)), claim=False)
            where = nxt.get("gate") or nxt.get("stage") or ""
            rows.append(f"{story:<10} {nxt['action']:<12} {where}")
        print("\n".join(rows) if rows else "no runs yet")
        return
    story = story_key(args.story)
    state = load_state(cfg, story)
    nxt = compute_next(cfg, story, json.loads(json.dumps(state)), claim=False)
    print(f"{story}  ({'offline' if state.get('offline') else 'ADO'})  next: {nxt['action']} "
          f"{nxt.get('gate') or nxt.get('stage') or ''}")
    for s in load_pipeline():
        st = state["stages"].get(s["id"], {"status": "pending"})
        extra = st.get("reason") or ""
        if s["kind"] == "gate" and s["id"] in state["approvals"]:
            a = state["approvals"][s["id"]]
            extra = f"approved by {a['by']} at {a['at']}"
        print(f"  {st['status']:<12} {s['kind']:<6} {s['id']:<36} {extra}")
    if state["feedback"]:
        print("  feedback: " + ", ".join(state["feedback"]))


def cmd_gate_summary(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    gate = _gate(load_pipeline(), args.gate)
    base = run_path(cfg, story)
    h, files = gate_hash(cfg, story, gate)
    print(f"# {gate['id']} — {gate.get('title', '')}\n\nStory {story} · artifact hash `{h[:16]}`\n")
    for rel in files:
        p = base / rel
        line = f"- `{rel}`"
        if p.suffix in (".yaml", ".yml"):
            data = load_yaml(p) or {}
            bits = []
            for key in ("test_cases", "selected", "excluded", "gaps", "findings", "files", "scenarios", "data_sets"):
                if isinstance(data.get(key), list):
                    bits.append(f"{len(data[key])} {key}")
            cov = data.get("existing_coverage")
            if isinstance(cov, list) and cov:
                verdicts: dict[str, int] = {}
                for e in cov:
                    verdicts[str(e.get("verdict"))] = verdicts.get(str(e.get("verdict")), 0) + 1
                bits.append("reused master-pack coverage: " + ", ".join(f"{n} {v}" for v, n in sorted(verdicts.items())))
            if data.get("decision"):
                bits.append(f"decision: {data['decision']}")
            line += f" — {', '.join(bits)}" if bits else ""
        print(line)
    open_q = open_area_path_questions(cfg, story) if TEST_CASES in files else []
    if open_q:
        print("\n**Open area path questions (must be answered before approval):**\n" +
              "\n".join(f"- {q}" for q in open_q) +
              f"\nAnswer with: /pdlc-reject {story} {gate['id']} \"<case keys>: use area path <full path>\"")
    print(f"\nApprove: /pdlc-approve {story} {gate['id']}\nReject:  /pdlc-reject {story} {gate['id']} <feedback>")


# ================================================================ automation repo operations

def git(repo: Path, *argv, check=True, text=True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, text=text)
    if check and r.returncode != 0:
        die(f"git {' '.join(argv)} failed in {repo}: {(r.stderr or r.stdout).strip() if text else r.stderr}")
    return r


def git_rev(repo: Path, ref: str) -> str | None:
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def automation_repo(cfg) -> Path:
    repo = configured_path(cfg, "automation_repo")
    if not repo or not (repo / ".git").exists():
        die("paths.automation_repo is not set to a git repository in config/framework.yaml")
    return repo


def safe_rel(path: str) -> str | None:
    p = PurePosixPath(path.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts or ":" in p.parts[0]:
        return None
    return p.as_posix()


def cmd_seal_proposal(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    stages = load_pipeline()
    base = run_path(cfg, story) / "10-automation-design"
    proposal = load_yaml(base / "proposal.yaml") or {}
    repo = automation_repo(cfg)
    base_ref = cfg["automation"]["base_ref"]
    commit = git_rev(repo, base_ref) or die(f"base_ref {base_ref!r} not found in {repo}")
    staged_root = base / "files"
    errors, warnings, sealed = [], [], []
    declared = set()
    for f in proposal.get("files") or []:
        rel = safe_rel(str(f.get("path", "")))
        op = f.get("operation")
        if not rel:
            errors.append(f"unsafe or empty path: {f.get('path')!r}")
            continue
        declared.add(rel)
        if op not in ("create", "modify"):
            errors.append(f"{rel}: operation must be create or modify (got {op!r}); deletes need a human")
            continue
        staged = staged_root / rel
        if not staged.is_file():
            errors.append(f"{rel}: staged content missing at 10-automation-design/files/{rel}")
            continue
        exists = git(repo, "cat-file", "-e", f"{commit}:{rel}", check=False).returncode == 0
        entry = {"path": rel, "operation": op, "new_sha256": sha256_file(staged)}
        if op == "create" and exists:
            errors.append(f"{rel}: marked create but already exists at {base_ref}")
        elif op == "modify":
            if not exists:
                errors.append(f"{rel}: marked modify but does not exist at {base_ref}")
                continue
            # Compare by git blob id so CRLF/LF checkout conversion doesn't count as a change.
            entry["base_blob"] = git(repo, "rev-parse", f"{commit}:{rel}").stdout.strip()
            local = repo / rel
            if local.exists() and git(repo, "hash-object", "--", rel).stdout.strip() != entry["base_blob"]:
                warnings.append(f"{rel}: your local checkout differs from {base_ref}; the change is applied on {base_ref}")
        sealed.append(entry)
    if staged_root.exists():
        for p in staged_root.rglob("*"):
            if p.is_file() and p.relative_to(staged_root).as_posix() not in declared:
                errors.append(f"staged file not declared in proposal.yaml: {p.relative_to(staged_root).as_posix()}")
    if not sealed and not errors:
        errors.append("proposal declares no files")
    if errors:
        text = "Proposal failed deterministic validation:\n\n" + "\n".join(f"- {e}" for e in errors)
        fb = add_feedback(cfg, story, state, "seal-proposal", text)
        reset_range(cfg, story, state, stages, "automation-design", "seal-proposal", "proposal failed validation")
        save_state(cfg, story, state)
        print(text + f"\n\nSent back to automation-design ({fb}). Run `next` again.")
        sys.exit(3)
    out = {"sealed_at": now(), "base_ref": base_ref, "base_commit": commit, "files": sealed, "warnings": warnings}
    (base / "sealed.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"sealed {len(sealed)} files against {base_ref}@{commit[:10]}" +
          "".join(f"\nwarning: {w}" for w in warnings))


def worktree_path(cfg, story) -> Path:
    return configured_path(cfg, "worktrees_dir") / story_key(story)


def cmd_apply(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    stages = load_pipeline()
    gate = _gate(stages, "G2-automation-design-approval")
    if not approval_valid(cfg, story, state, gate):
        die("G2-automation-design-approval is missing or no longer matches the proposal; nothing applied", 2)
    run = run_path(cfg, story)
    sealed = json.loads((run / "10-automation-design" / "sealed.json").read_text(encoding="utf-8"))
    repo = automation_repo(cfg)
    wt = worktree_path(cfg, story)
    branch = f"{cfg['automation']['branch_prefix']}{story}"
    # Always start from a clean worktree at the sealed base commit.
    if wt.exists():
        git(repo, "worktree", "remove", "--force", str(wt), check=False)
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
    git(repo, "worktree", "prune", check=False)
    if git(repo, "rev-parse", "--verify", branch, check=False).returncode == 0:
        git(repo, "branch", "-D", branch)
    wt.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-b", branch, str(wt), sealed["base_commit"])
    applied = []
    for f in sealed["files"]:
        target = wt / f["path"]
        if f["operation"] == "modify" and git(wt, "hash-object", "--", f["path"]).stdout.strip() != f["base_blob"]:
            die(f"{f['path']} changed since sealing; re-run automation-design")
        if f["operation"] == "create" and target.exists():
            die(f"{f['path']} already exists in the worktree")
        src = run / "10-automation-design" / "files" / f["path"]
        if sha256_file(src) != f["new_sha256"]:
            die(f"staged {f['path']} changed after sealing; approval does not cover it")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        applied.append(f["path"])
    git(wt, "add", "--", *applied)
    title = ""
    story_json = run / "01-intake" / "story.json"
    if story_json.exists():
        title = json.loads(story_json.read_text(encoding="utf-8")).get("title", "")
    git(wt, "commit", "-m", f"{story}: test automation{(' for ' + title) if title else ''}\n\n"
                            f"Applied by pdlc-agent-framework from approved proposal "
                            f"{state['approvals'][gate['id']]['hash'][:12]}.")
    head = git(wt, "rev-parse", "HEAD").stdout.strip()
    receipt = {"applied_at": now(), "worktree": str(wt), "branch": branch, "base_commit": sealed["base_commit"],
               "head_commit": head, "files": applied,
               "approval": {k: state["approvals"][gate["id"]][k] for k in ("by", "at", "hash")}}
    (run / "11-apply").mkdir(parents=True, exist_ok=True)
    (run / "11-apply" / "apply-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    log_event(state, "applied", branch=branch, head=head, files=len(applied))
    save_state(cfg, story, state)
    print_json(receipt)


def cmd_run_check(cfg, args):
    story = story_key(args.story)
    command = (cfg["automation"].get("checks") or {}).get(args.check)
    out_dir = run_path(cfg, story) / "12-validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {"check": args.check, "command": command or "", "started_at": now()}
    wt = worktree_path(cfg, story)
    if not is_set(command):
        result.update(status="skipped", reason=f"automation.checks.{args.check} is not configured")
    elif not wt.exists():
        result.update(status="skipped", reason="worktree missing; run automation-apply first")
    else:
        command = command.replace("{tags}", f"@{story}")
        result["command"] = command
        log = out_dir / f"{args.check}.log"
        t0 = time.time()
        try:
            with open(log, "w", encoding="utf-8", errors="replace") as fh:
                r = subprocess.run(command, shell=True, cwd=wt, stdout=fh, stderr=subprocess.STDOUT,
                                   timeout=cfg["automation"].get("check_timeout_seconds", 1800))
            result.update(status="passed" if r.returncode == 0 else "failed", exit_code=r.returncode)
        except subprocess.TimeoutExpired:
            result.update(status="timeout")
        result.update(duration_s=round(time.time() - t0, 1), log=show_path(log))
    (out_dir / f"{args.check}.result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print_json(result)
    if result.get("log"):
        tail = Path(ROOT / result["log"]).read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        print("--- last 40 log lines ---\n" + "\n".join(tail))


def cmd_verify_diff(cfg, args):
    story = story_key(args.story)
    run = run_path(cfg, story)
    sealed = json.loads((run / "10-automation-design" / "sealed.json").read_text(encoding="utf-8"))
    receipt = json.loads((run / "11-apply" / "apply-receipt.json").read_text(encoding="utf-8"))
    wt = Path(receipt["worktree"])
    changed = {}
    for line in git(wt, "diff", "--name-status", f"{sealed['base_commit']}..HEAD").stdout.splitlines():
        status, _, path = line.partition("\t")
        changed[path.strip()] = status.strip()
    uncommitted = [l for l in git(wt, "status", "--porcelain").stdout.splitlines() if l.strip()]
    manifest = {f["path"]: f for f in sealed["files"]}
    out_of_manifest = sorted(set(changed) - set(manifest))
    not_applied = sorted(set(manifest) - set(changed))
    content_mismatch = [p for p, f in manifest.items()
                        if (wt / p).exists() and sha256_file(wt / p) != f["new_sha256"]]
    result = {"checked_at": now(), "base_commit": sealed["base_commit"],
              "head_commit": git(wt, "rev-parse", "HEAD").stdout.strip(), "changed_files": changed,
              "out_of_manifest": out_of_manifest, "not_applied": not_applied,
              "content_mismatch": content_mismatch, "uncommitted": uncommitted,
              "ok": not (out_of_manifest or not_applied or content_mismatch or uncommitted)}
    out = run / "13-automation-review"
    out.mkdir(parents=True, exist_ok=True)
    (out / "diff-check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print_json(result)


# ================================================================ main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("story"); p.add_argument("--story-file"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("next"); p.add_argument("story"); p.add_argument("--claim", action="store_true")
    p = sub.add_parser("complete"); p.add_argument("story"); p.add_argument("stage")
    p = sub.add_parser("fail"); p.add_argument("story"); p.add_argument("stage"); p.add_argument("--reason", required=True); p.add_argument("--block", action="store_true")
    p = sub.add_parser("unblock"); p.add_argument("story"); p.add_argument("stage")
    p = sub.add_parser("status"); p.add_argument("story", nargs="?")
    p = sub.add_parser("gate-summary"); p.add_argument("story"); p.add_argument("gate")
    p = sub.add_parser("approve"); p.add_argument("story"); p.add_argument("gate"); p.add_argument("--by"); p.add_argument("--comment")
    p = sub.add_parser("reject"); p.add_argument("story"); p.add_argument("gate"); p.add_argument("--feedback", required=True); p.add_argument("--by")
    p = sub.add_parser("verify-approval"); p.add_argument("story"); p.add_argument("gate")
    p = sub.add_parser("invalidate"); p.add_argument("story"); p.add_argument("--from", dest="from_stage", required=True); p.add_argument("--reason", required=True)
    p = sub.add_parser("seal-proposal"); p.add_argument("story")
    p = sub.add_parser("apply"); p.add_argument("story")
    p = sub.add_parser("run-check"); p.add_argument("story"); p.add_argument("check", choices=["compile", "dry_run", "run_new", "run_regression"])
    p = sub.add_parser("verify-diff"); p.add_argument("story")
    args = ap.parse_args()
    cfg = load_config()
    {"init": cmd_init, "next": cmd_next, "complete": cmd_complete, "fail": cmd_fail, "unblock": cmd_unblock,
     "status": cmd_status, "gate-summary": cmd_gate_summary, "approve": cmd_approve, "reject": cmd_reject,
     "verify-approval": cmd_verify_approval, "invalidate": cmd_invalidate, "seal-proposal": cmd_seal_proposal,
     "apply": cmd_apply, "run-check": cmd_run_check, "verify-diff": cmd_verify_diff}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
