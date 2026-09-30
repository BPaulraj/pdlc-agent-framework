# Workflow

The single source of truth is [pipeline/pipeline.yaml](../pipeline/pipeline.yaml). `scripts/pdlc.py next <story>` walks it top to bottom and returns the first stage that still needs work. The `/pdlc-run` orchestrator only executes what `next` returns.

## Trigger

- **Automatic:** `scripts/sprint_watcher.py` queries the team's `@CurrentIteration` for the configured story types (excluding closed states, optionally requiring `start_tag`). New stories get a run. Stories whose run is ready again (clarification answered, gate approved) are resumed. A per-story lock prevents double processing.
- **Manual:** `/pdlc-run <story>` at any time. In offline mode, pass `--story-file`.
- **Story edits:** every `/pdlc-run` re-fetches the story. If the title, description, AC or attachments changed, the run restarts from intake: earlier artifacts are archived to `history/` and approvals are voided. Tag and comment changes don't count.

## Stage kinds and statuses

| Kind | Who does the work | Completion |
|---|---|---|
| `agent` | Claude Code subagent | `pdlc.py complete` validates the outputs exist and parse, then records their hashes |
| `script` | deterministic command | exit 0 → complete; exit 3 → the script rerouted the run (e.g. seal failure → redesign) |
| `wait` | outside world (PO/BA) | condition becomes true (clarification task closed / offline answers file exists) |
| `gate` | human | approval whose hash equals the current hash of the gate's review artifacts |

Stage statuses: `pending → in_progress → done` (or `skipped` when disabled or its condition is false). A stage becomes `blocked` after `max_attempts_per_stage` failures, when a reviewer returns `blocked`, or when clarification rounds run out. `python scripts/pdlc.py unblock <story> <stage>` clears it after a human fixes the cause.

## Loops and rework

| Trigger | Goes back to | Bound |
|---|---|---|
| Test reviewer `request_changes` | test-design (then data, regression, review again) | `max_auto_rework_loops` |
| Validator or automation reviewer `request_changes` | automation-design (then seal, **G2 again**) | `max_auto_rework_loops` |
| Seal validation failure | automation-design | `max_attempts_per_stage` |
| Clarification still unresolved | raise-clarification (round N+1 task) | `max_clarification_rounds` |
| Human rejects G1 / G2 / G3 | test-design / automation-design / automation-design | none (human decides) |
| Story content changed | intake | none |

On every reset, the affected outputs move to `runs/<story>/history/<n>-<stage>/`, and the reason is written to `feedback/`. The next agent run receives all feedback files and must list what it addressed.

## Clarification task

`ado.py raise-clarifications` creates (or, on retry, updates) one child Task under the story titled **To clarify with stakeholders** (with `(round N)` for follow-ups). It is assigned to `clarification.assign_to`, and its description holds a question table with ID, question, why it matters, blocking, proposed assumption, and an empty **Answer** column. PO/BA answer in the table or in Discussion, optionally update the story's AC, and close the task. Blocking questions pause the pipeline. Non-blocking questions are included but the pipeline proceeds on the proposed assumption. The resolver then confirms or rejects each assumption.

## Human gates

| Gate | Human sees | Approving means |
|---|---|---|
| **G1** test approval | strategy, test cases, data plan, regression selection, review report | tests are right; automation may be designed from them |
| **G2** automation design | proposal manifest + complete staged files + seal info | these exact files may be applied to a branch |
| **G3** automation + publish | apply receipt, validation, diff check, review | the branch is acceptable, and the approved test cases may be uploaded to ADO |

Approvals record the approver (git email by default), time, comment, and the SHA-256 of every reviewed file. If any reviewed file changes, the approval is void and the gate re-opens. `apply` and `publish-test-cases` re-verify the hashes themselves, so they don't rely on the orchestrator. The approve/reject skills have `disable-model-invocation: true`, so the model can't trigger them itself.

After G3, a human pushes the `pdlc/<story>` branch and raises the PR. The framework never pushes or merges; `git push` is denied in `.claude/settings.json`.
