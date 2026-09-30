#!/usr/bin/env python3
"""Aggregate gate-approval and rework outcomes across every run, for trend-watching.

scripts/golden_eval.py answers "did quality drift for a fixed input we replayed on
purpose" — a controlled, sparse check, run by choice. This answers the complementary
question from live traffic: is a gate's rejection rate rising, which stage chronically
sends work back, how many stories are sitting blocked right now. Pure aggregation over
what pdlc.py already logs into every runs/US-*/state.json's `history` list — nothing new
is captured, so this is read-only and cheap to run any time.

Usage:
  python scripts/gate_metrics.py [--since YYYY-MM-DD] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_config, load_pipeline, print_json, runs_dir  # noqa: E402


def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def parse_since(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def load_runs(cfg):
    for p in sorted(runs_dir(cfg).glob("US-*/state.json")):
        try:
            yield p.parent.name, json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt/partial state.json shouldn't kill the report
            continue


def classify_reset(reason: str) -> str:
    r = (reason or "").lower()
    if "follow-up clarification" in r:
        return "clarification follow-up"
    if "requested changes" in r:
        return "auto-reviewer rework"
    if "rejected" in r:
        return "human gate rejection"
    return "other/manual (invalidate)"


def avg(nums: list) -> float | None:
    return sum(nums) / len(nums) if nums else None


def fmt_duration(seconds: float) -> str:
    hours = seconds / 3600
    return f"{hours / 24:.1f}d" if hours >= 48 else f"{hours:.1f}h"


def collect(cfg, since: datetime | None):
    gate_ids = [s["id"] for s in load_pipeline() if s["kind"] == "gate"]

    gates = {g: {"approved": 0, "rejected": 0, "invalidated": 0, "lead_times_s": []} for g in gate_ids}
    resets: dict[tuple, int] = {}          # (sent_back_to, trigger) -> count
    exhausted: dict[str, int] = {}         # stage -> auto-rework-limit-hit count
    blocked_events: dict[str, int] = {}    # stage -> times it was blocked (any cause)
    story = {"total": 0, "completed": 0, "blocked_now": 0, "in_progress": 0, "cycle_times_s": []}

    for story_id, state in load_runs(cfg):
        events = state.get("history", [])
        init_at = next((e["at"] for e in events if e.get("event") == "init"), state.get("created_at"))
        story["total"] += 1
        if state.get("completed_at"):
            story["completed"] += 1
            if init_at:
                try:
                    story["cycle_times_s"].append(
                        (parse_dt(state["completed_at"]) - parse_dt(init_at)).total_seconds())
                except Exception:  # noqa: BLE001
                    pass
        elif any(st.get("status") == "blocked" for st in state.get("stages", {}).values()):
            story["blocked_now"] += 1
        else:
            story["in_progress"] += 1

        for e in events:
            try:
                at = parse_dt(e["at"])
            except Exception:  # noqa: BLE001
                continue
            if since and at < since:
                continue
            ev = e.get("event")
            if ev == "approved" and e.get("gate") in gates:
                gates[e["gate"]]["approved"] += 1
                if init_at:
                    try:
                        gates[e["gate"]]["lead_times_s"].append((at - parse_dt(init_at)).total_seconds())
                    except Exception:  # noqa: BLE001
                        pass
            elif ev == "rejected" and e.get("gate") in gates:
                gates[e["gate"]]["rejected"] += 1
            elif ev == "approval_invalidated" and e.get("gate") in gates:
                gates[e["gate"]]["invalidated"] += 1
            elif ev == "reset":
                key = (e.get("start", "?"), classify_reset(e.get("reason", "")))
                resets[key] = resets.get(key, 0) + 1
            elif ev == "auto_rework_exhausted":
                exhausted[e.get("stage", "?")] = exhausted.get(e.get("stage", "?"), 0) + 1
            elif ev == "blocked":
                blocked_events[e.get("stage", "?")] = blocked_events.get(e.get("stage", "?"), 0) + 1

    return gates, resets, exhausted, blocked_events, story


def print_report(gates, resets, exhausted, blocked_events, story, since):
    window = f" (since {since.date()})" if since else " (all time)"
    print(f"# Gate & rework metrics{window}\n")

    print(f"Stories: {story['total']} total, {story['completed']} completed, "
          f"{story['blocked_now']} blocked now, {story['in_progress']} in progress")
    ct = avg(story["cycle_times_s"])
    if ct:
        print(f"Avg cycle time init->complete: {fmt_duration(ct)} "
              f"(over {len(story['cycle_times_s'])} completed stories)")
    print()

    print(f"{'gate':<36} {'approved':<9} {'rejected':<9} {'rej. rate':<10} {'invalidated':<12} {'avg lead time':<14}")
    print("-" * 94)
    for g, s in gates.items():
        decisions = s["approved"] + s["rejected"]
        rate = f"{100 * s['rejected'] / decisions:.0f}%" if decisions else "-"
        lt = avg(s["lead_times_s"])
        print(f"{g:<36} {s['approved']:<9} {s['rejected']:<9} {rate:<10} {s['invalidated']:<12} "
              f"{fmt_duration(lt) if lt else '-':<14}")
    print()

    if resets:
        print(f"{'sent back to':<28} {'trigger':<26} {'count':<6}")
        print("-" * 62)
        for (target, category), count in sorted(resets.items(), key=lambda kv: -kv[1]):
            print(f"{target:<28} {category:<26} {count:<6}")
    else:
        print("no rework/rejection events in this window")
    print()

    if exhausted:
        print(f"{'auto-rework limit hit at':<28} {'count':<6}")
        print("-" * 34)
        for stage, count in sorted(exhausted.items(), key=lambda kv: -kv[1]):
            print(f"{stage:<28} {count:<6}")
        print("(findings from these went to the human gate as-is instead of looping again)\n")

    if blocked_events:
        print(f"{'stage blocked':<28} {'count':<6}")
        print("-" * 34)
        for stg, count in sorted(blocked_events.items(), key=lambda kv: -kv[1]):
            print(f"{stg:<28} {count:<6}")
    else:
        print("no stages blocked in this window")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="only count events at/after this date (YYYY-MM-DD); "
                                     "story totals/blocked-now are always as-of-now")
    ap.add_argument("--json", action="store_true", help="print raw aggregates as JSON instead of the report")
    args = ap.parse_args()
    cfg = load_config()
    since = parse_since(args.since) if args.since else None
    gates, resets, exhausted, blocked_events, story = collect(cfg, since)
    if args.json:
        print_json({
            "since": args.since,
            "gates": gates,
            "resets": {f"{target} | {trigger}": count for (target, trigger), count in resets.items()},
            "auto_rework_exhausted": exhausted,
            "blocked_events": blocked_events,
            "story": story,
        })
        return
    print_report(gates, resets, exhausted, blocked_events, story, since)


if __name__ == "__main__":
    main()
