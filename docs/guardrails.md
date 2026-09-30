# Guardrails

Each control below states how it is enforced. "Instruction" means the agent is told to follow it; "enforced" means code prevents violation.

## Write boundaries

| Rule | Enforcement |
|---|---|
| Agents never edit the application repo, the automation repo, or apply worktrees | **Enforced:** `scripts/guard_writes.py` PreToolUse hook blocks Write/Edit there, and blocks Bash/PowerShell write operations naming those paths |
| Design/review agents can't run commands | **Enforced:** their `tools:` list has no Bash |
| Only approved files reach the automation repo | **Enforced:** `pdlc.py apply` requires a valid G2 hash, applies only sealed paths, re-checks every staged file's hash, and verifies modified files still match their sealed base blob |
| Changes land on a new branch in a separate worktree, never in your checkout | **Enforced:** `apply` uses `git worktree add -b pdlc/<story>` under `paths.worktrees_dir` |
| Nothing beyond the manifest changes | **Enforced:** `pdlc.py verify-diff` (out-of-manifest / not-applied / content-mismatch / uncommitted); the reviewer marks any hit as blocking |
| No push or merge | **Enforced:** `git push` is denied in `.claude/settings.json`; no script pushes |
| Run state and approvals can't be forged by agents | **Enforced:** the hook blocks writes to `runs/*/state.json`; `approve`/`reject` skills are human-invoked only (`disable-model-invocation`) |
| ADO writes are limited to specific shapes | **Enforced:** only `ado.py` holds the PAT and it can only create the clarification task, Test Cases, suites, a story tag and a report comment. Bugs are drafts only |
| Test cases are uploaded exactly as approved | **Enforced:** `publish-test-cases` verifies the G1 and G3 hashes; it is idempotent through the receipt |
| Commands run only from an allowlist | **Enforced:** `pdlc.py run-check` executes only `automation.checks.*` from config, inside the worktree, with a timeout |

The Bash/PowerShell part of the hook is a heuristic (it can't see through `cd` plus relative paths). The real control is that agents that need no shell don't get one, and agents with a shell are told to use only the `pdlc.py`/`ado.py` wrappers and read-only git.

## Untrusted content and secrets

- Story text, attachments, comments, code and master-pack content are data, not instructions. Every agent carries this rule (`guidelines/agent-contract.md`), and the orchestrator never approves on anyone's behalf.
- The PAT comes from an environment variable. Config holds only its name. Artifacts must not contain credentials or real personal data, and test data is synthetic.
- `runs/` and `.pdlc/` are git-ignored because they contain story content and attachments. Apply your retention policy to them.

## Quality rules (instruction + review)

- Every test case traces to an AC, a clarified answer or a labelled assumption. No invented expected results.
- Regression picks carry reasons. Near-misses and gaps are reported, not hidden.
- Automation is reuse-first, with justification for anything new. No refactors, dependency or config changes, or sleeps.
- Skipped checks are reported as skipped, never as passed.
- Independent reviewer agents check producer agents before any human gate. Their loops are bounded.
- Per-run quality is checked at the gates; drift *across* runs (after a model swap, or a guideline/template
  edit) and live rejection/rework trends are tracked separately with `scripts/golden_eval.py` and
  `scripts/gate_metrics.py` — see [docs/evals.md](evals.md).

## Review checklist for approvers
- G1: Does every AC have exact, observable expected results? Are assumptions acceptable? Is the regression scope credible and proportionate?
- G2: Is every new component justified against what already exists? Does each file change only what its `change_summary` says?
- G3: Is `diff-check.json` `ok: true`? Were checks actually run (not skipped)? Are failures classified with evidence?
