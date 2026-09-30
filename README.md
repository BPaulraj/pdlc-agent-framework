# Testing-First PDLC Agent Framework

Agentic AI for the testing side of the product development lifecycle. It works with any programming language. When a user story is scoped into a sprint in Azure DevOps, a pipeline of specialised agents analyses the story, asks the PO/BA clarifying questions, designs functional and regression tests, designs and applies BDD automation, and uploads the test cases to ADO. Humans approve at three gates.

**Design principle: agents propose, scripts dispose.** LLM agents (Claude Code subagents) produce reviewable artifacts. Deterministic Python scripts own the stage order, gate approvals (bound to content hashes), every ADO write, and every repository write.

```
 Sprint scope ─► Intake ─► Story analysis ─► [Clarify task → PO/BA answers] ─► Clarification resolution
                                                                                     │
    ┌────────────────────────────────────────────────────────────────────────────────┘
    ▼
 Impact analysis (app code) ─► Test strategy ─► Test design ─► Test data ─► Regression selection ─► Test review
                                                                                   (auto-rework loop) │
 ══ G1 human: approve tests ══◄────────────────────────────────────────────────────────────────────────┘
    ▼
 Automation index (when repo changed) ─► Automation design (staged, repo untouched) ─► seal
 ══ G2 human: approve exact files ══
    ▼
 Apply (new branch in a git worktree) ─► Validate (compile / dry-run / run) ─► Automation review (diff == approved)
 ══ G3 human: accept automation + authorise ADO upload ══
    ▼
 ADO upload ─► [Execution & triage] ─► Traceability report (posted on story) ─► Regression-pack curation
```

## Agents

| # | Agent | Your list | What it adds |
|---|---|---|---|
| 1 | `pdlc-story-intake` | — | Cited context manifest from story, AC, attachments (mockups/PDFs), links, comments |
| 2 | `pdlc-story-analyst` | **1** | Ambiguity/gap analysis → questions in a **"To clarify with stakeholders"** Task under the story |
| 3 | `pdlc-clarification-resolver` | — | Checks that the PO/BA answers actually resolve each question; refined AC baseline; follow-up rounds |
| 4 | `pdlc-impact-analyst` | (part of 3) | Reads the **application code** to find impacted modules, interfaces and dependencies |
| 5 | `pdlc-test-strategist` | — | Risk-based priorities, test levels/types, techniques, exploratory charters |
| 6 | `pdlc-test-designer` | **2** | Functional test cases (title, preconditions, action/expected steps), traced to AC |
| 7 | `pdlc-test-data-designer` | — | Synthetic, isolated data sets that reuse the repo's builders/fixtures |
| 8 | `pdlc-regression-selector` | **3** | Master-pack cases to re-run, with reasons, priorities, near-misses and gaps |
| 9 | `pdlc-test-reviewer` | **4** | Independent review; sends work back automatically before the human sees it |
| — | **Gate G1** | ✔ | Human approves tests + regression |
| 10 | `pdlc-automation-indexer` | — | Profiles the automation repo (language, framework, conventions, reusable components) |
| 11 | `pdlc-automation-designer` | **5** | Feature files + step definitions + classes, reuse-first, staged outside the repo |
| — | **Gate G2** | ✔ | Human approves the exact files (hash-bound) |
| 12 | `pdlc-automation-applier` | **6** | Applies only the approved files to a new branch in a separate git worktree |
| 13 | `pdlc-automation-validator` | — | Runs allowlisted compile/dry-run/new-scenario checks and classifies failures |
| 14 | `pdlc-automation-reviewer` | **7** | Deterministic diff-vs-manifest check + scope/reuse/standards review |
| — | **Gate G3** | ✔ | Human accepts the automation and authorises the upload |
| 15 | `pdlc-ado-publisher` | **8** | Test Case work items linked to the story, per-story suite and regression suite |
| 16 | `pdlc-execution-triager` | — | *(optional)* Runs new + regression tests, triages failures, drafts defects (never files them) |
| 17 | `pdlc-traceability-reporter` | — | AC → test case → ADO id → scenario → result; posted as a story comment |
| 18 | `pdlc-regression-pack-curator` | — | *(optional)* Promote/update/retire master-pack cases; automation backlog |
| 19 | `pdlc-retro-learner` | — | `/pdlc-retro`: learns from gate rejections and review loops, proposes guideline edits |

Details: [docs/agent-catalog.md](docs/agent-catalog.md) · Flow and states: [docs/workflow.md](docs/workflow.md) · Controls: [docs/guardrails.md](docs/guardrails.md) · Design: [docs/architecture.md](docs/architecture.md) · Drift over time: [docs/evals.md](docs/evals.md) · **Full process diagrams + reference tables (PDF): [docs/pdlc-knowledge-base.pdf](docs/pdlc-knowledge-base.pdf)**

## Setup

1. **Prerequisites:** Claude Code, Python 3.10+ (`pip install -r requirements.txt`), and git. Open this folder in Claude Code; the agents, skills and write-guard hook load from `.claude/`.
2. **Point at your repos** in [config/framework.yaml](config/framework.yaml): `paths.application_repo` and `paths.automation_repo` (absolute paths), and `automation.base_ref` (e.g. `main` or `develop`). Keep the automation repo checked out on `base_ref` so agents read what the branch will be built from.
3. **Try it offline first** (`azure_devops.enabled: false`):
   ```
   /pdlc-run US-1001 --story-file examples/stories/US-1001.json
   ```
   Questions for the PO/BA are written to `runs/US-1001/03-clarification/questions-round-1.md`. Put answers in `answers-round-1.md` and run `/pdlc-run US-1001` again.
4. **Build the automation index.** It runs automatically at the automation stage. Afterwards, check `.pdlc/cache/automation-index/repo-profile.yaml` → `suggested_checks`, verify the commands, and copy them into `automation.checks`. Only those commands can ever be executed.
5. **Connect ADO:** fill in `azure_devops.*`, including `test_plan.plan_id`, `parent_suite_id` and `master_pack.plan_id`. Set a PAT (Work Items R/W, Test Management R/W) in the `ADO_PAT` environment variable, then set `enabled: true`.
6. **Automatic start on sprint scope:** schedule the watcher, for example every 15 minutes with Windows Task Scheduler:
   ```
   python scripts/sprint_watcher.py            # one pass: start new in-sprint stories, resume answered/approved ones
   python scripts/sprint_watcher.py --dry-run  # see what it would do
   ```

## Daily use

| Command | Who | What |
|---|---|---|
| `/pdlc-run <story>` | anyone / watcher | Start or resume; runs until the next gate, wait, or block |
| `/pdlc-status [story]` | anyone | Where every story is; the next action |
| `/pdlc-approve <story> G1\|G2\|G3 [comment]` | **human only** | Approve the exact artifacts shown (any later edit voids it) |
| `/pdlc-reject <story> G1\|G2\|G3 <feedback>` | **human only** | Send back with feedback; agents rework and it re-appears at the gate |
| `/pdlc-retro` | QA lead | Sprint retro across runs → proposed guideline/template changes |
| `python scripts/ado.py export-csv <story>` | anyone | Test cases as ADO-import CSV (for Excel review or manual import) |
| `python scripts/golden_eval.py capture/replay/compare` | QA lead | Snapshot an approved run, replay it later, diff for drift — see [docs/evals.md](docs/evals.md) |
| `python scripts/gate_metrics.py [--since DATE]` | QA lead | Rejection rate, rework hotspots, blocked stories across all runs — see [docs/evals.md](docs/evals.md) |

Each story's artifacts live in `runs/US-<id>/` (numbered per stage), with `state.json`, `feedback/` and `history/` (archived earlier versions).

## Customising (templates & guidelines)

- `templates/`: output contracts per agent. Replace them with your formats (test case template, feature file, step definition contract, and so on) but keep the key names, or update the agent that uses them.
- `guidelines/`: test design techniques, story analysis checklist, BDD practices, coding standards, regression rules, review checklists. They are starters; replace them with your team's standards.
- `test-design-packs/`: per-interface test-case contracts (web-ui, api, batch-async, db-validation, other). `pdlc-test-strategist` assigns an interface per concern; `pdlc-test-designer` follows that pack's conventions and `interface_details` shape. Add packs for other surfaces (messaging, GraphQL, CLI, ...); see its README.
- `language-packs/`: language-specific *automation* defaults (Java + Cucumber included) — how code is written, not how test cases are designed. Add packs for other stacks; see its README.
- `pipeline/pipeline.yaml`: which files each stage reads, stage order, gates. Optional stages can be toggled in `config/framework.yaml → stages`.

## Repository map

```
.claude/agents/        19 subagents (one per role)
.claude/skills/        /pdlc-run, /pdlc-approve, /pdlc-reject, /pdlc-status, /pdlc-retro
.claude/settings.json  write-guard hook + narrow permission allowlist
config/framework.yaml  paths, ADO, checks allowlist, stage toggles (no secrets)
pipeline/pipeline.yaml stage graph: agents, inputs, outputs, gates, rework routes
scripts/pdlc.py        state machine, gates, seal/apply/verify-diff, allowlisted checks
scripts/ado.py         ADO REST adapter (story, clarification task, master pack, test cases, report)
scripts/index_steps.py language-agnostic step/feature indexer
scripts/extract_attachments.py  converts Word/Excel story attachments to text for intake (images/PDF/text need no help)
scripts/sprint_watcher.py  polls @CurrentIteration and launches runs
scripts/guard_writes.py    PreToolUse hook: agents cannot write to repos or state
scripts/golden_eval.py     capture/replay/compare approved runs to track drift over time
scripts/gate_metrics.py    rejection rate, rework hotspots, blocked stories across all runs
templates/ guidelines/ test-design-packs/ language-packs/ examples/ docs/
```
