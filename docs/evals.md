# Evaluating reliability: golden-set drift vs. live gate metrics

Two complementary tools, for two different questions:

- **`scripts/golden_eval.py`** (below) — *"did quality drift for a fixed input we
  replayed on purpose?"* A controlled experiment: same story, run again later, diffed
  against a human-approved baseline. Sparse — only for cases someone chose to capture.
- **`scripts/gate_metrics.py`** ([jump down](#live-gate-metrics)) — *"is the live system's
  approval/rework behaviour trending in a bad direction?"* Passive aggregation over every
  real run's already-logged history. Dense — covers every story, for free, no setup.

Neither judges quality by itself; both surface facts for a human to interpret, consistent
with the repo's "agents propose, scripts dispose" principle.

## Golden-set evaluation

The pipeline's per-run quality controls (independent reviewer agents, hash-bound human
gates, deterministic diff checks) catch a bad *run*. None of them tell you whether agent
output is drifting over *many* runs — after a model swap, a prompt or guideline edit, or
just over time. `scripts/golden_eval.py` closes that gap: it snapshots a human-approved
run as a reference baseline, lets you replay the same story input later, and reports a
structural diff against the baseline.

**This tool does not judge quality.** Consistent with the repo's "agents propose, scripts
dispose" principle, it only computes deterministic facts — which files changed, which list
items (test cases, files, findings, ...) were added or removed by stable id, and a full
text diff for anything that changed. A human still decides whether detected drift is an
improvement or a regression. What it buys you is *visibility*: drift shows up across many
runs over time (`golden/<name>/reports/history.jsonl`), instead of only being caught, or
missed, one run at a time at the gates.

## Workflow

**1. Capture a baseline**, any time a run has at least one real gate approval (or pass
`--allow-unapproved` to bootstrap with an unreviewed run for testing the harness itself):

```
python scripts/golden_eval.py capture US-1234 --name checkout-coupon-v1
```

This copies the story input (`01-intake/`, any clarification answers) and every produced
stage output into `golden/checkout-coupon-v1/`, plus a `meta.json` recording the gate
approval(s), and hashes of `guidelines/` and `templates/` at capture time (so a later
compare can tell you whether the guidelines moved, which may explain drift on its own).

**2. Replay the same input** through the current pipeline (after upgrading a model,
editing a guideline, or just periodically):

```
python scripts/golden_eval.py replay checkout-coupon-v1 --story US-9001
python scripts/pdlc.py next US-9001 --claim      # or /pdlc-run US-9001
```

Run it through whichever stages you care about — you don't have to complete the full
pipeline to compare test-design output.

**3. Compare against the baseline:**

```
python scripts/golden_eval.py compare checkout-coupon-v1 --story US-9001
```

Prints a per-file table (identical / changed / missing / new, with items added/removed by
stable id — test case `key`, proposal file `path`, AC id, etc.) and writes the full text
diff to `golden/checkout-coupon-v1/reports/<timestamp>-US-9001.md`. Each compare also
appends one line to `reports/history.jsonl`, so you can track the rate of drift over time
rather than reading one run's diff in isolation.

```
python scripts/golden_eval.py list
```

lists every captured case with its file count and last compare result.

## When to run it

- Before and after swapping the model used for agent runs.
- Before and after any non-trivial edit to `guidelines/` or `templates/`.
- Periodically (e.g. monthly) against your best 5–10 stories, as a regression check
  independent of any single run's gate outcome.

## Storage and retention

`golden/` is git-ignored by default, same as `runs/` — a captured case can contain real
story text. Force-add (`git add -f golden/<name>`) a specific case if you want it
version-controlled, e.g. one built from `examples/stories/US-1001.json`.

## Live gate metrics

`scripts/gate_metrics.py` reads every `runs/US-*/state.json`'s `history` log — already
written by `pdlc.py` on every `approve`/`reject`/auto-rework/block — and rolls it up:

```
python scripts/gate_metrics.py                  # all time
python scripts/gate_metrics.py --since 2026-09-01
python scripts/gate_metrics.py --json            # for scripting/dashboards
```

It reports, per gate (G1/G2/G3): approved/rejected/invalidated counts, rejection rate,
and average lead time from story init to approval. Separately, it reports rework events
(which stage work gets sent back to, and why — human gate rejection, auto-reviewer
rework, or clarification follow-up), which stages have hit the auto-rework loop cap
(`max_auto_rework_loops`) and needed a human as a result, and which stages are currently
blocked or have been blocked in the window. Story totals and "blocked now" are always
as-of-now; `--since` only windows the flow metrics (approvals, resets, blocks), so you
can watch a trend without losing the current snapshot.

Nothing here is captured beyond what `pdlc.py` already logs — it's read-only and free to
run any time, e.g. weekly, or before/after a guideline change to see if rejection rates
move.

Read together: if `gate_metrics.py` shows a gate's rejection rate climbing, a golden-set
`compare` for that stage is the next step to see *what* actually changed in the output.
