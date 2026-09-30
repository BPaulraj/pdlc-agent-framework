# Agent Catalog

Every agent is a Claude Code subagent in `.claude/agents/`, invoked by the `/pdlc-run` orchestrator with an explicit list of inputs, guidelines, templates, outputs, and feedback to address. Agents write only to their own stage folder. Scripts enforce order, gates, and all external writes.

| Stage | Agent | Tools | Reads | Writes | Gate/loop |
|---|---|---|---|---|---|
| intake | `pdlc-story-intake` | Read Grep Glob Write | `01-intake/story.json`, attachments | `01-intake/context.yaml` | — |
| analysis | `pdlc-story-analyst` | Read Grep Glob Write | context, story | `02-analysis/story-analysis.yaml` | open questions → clarification task |
| raise-clarification | *script* `ado.py raise-clarifications` | — | questions | ADO Task "To clarify with stakeholders" (or offline questions file) | — |
| await-clarification | *wait* | — | task state | — | until task closed / answers file |
| clarification-resolution | `pdlc-clarification-resolver` | Read Grep Glob Write | answers, story | `03-clarification/resolved-requirements.yaml` | unresolved blocking → follow-up round |
| impact-analysis | `pdlc-impact-analyst` | Read Grep Glob Write | **application repo** | `04-impact/impact-analysis.yaml` | optional |
| test-strategy | `pdlc-test-strategist` | Read Grep Glob Write | requirements, impact | `05-strategy/test-strategy.yaml` | optional |
| test-design | `pdlc-test-designer` | Read Grep Glob Write | refined AC, strategy, master pack | `06-test-design/test-cases.yaml` | — |
| test-data | `pdlc-test-data-designer` | Read Grep Glob Write | test cases, automation repo | `07-test-data/test-data-plan.yaml` | optional |
| regression-selection | `pdlc-regression-selector` | Read Grep Glob Write | impact, master pack export | `08-regression/regression-selection.yaml` | — |
| test-review | `pdlc-test-reviewer` | Read Grep Glob Write | all of the above | `09-test-review/review-report.yaml` | request_changes → test-design (max loops) |
| **G1** | human | | 05–09 | approval hash | reject → test-design |
| automation-index | `pdlc-automation-indexer` | Read Grep Glob Write | automation repo + `index_steps.py` output | `repo-profile.yaml`, `components.md` (cache) | only when base_ref moved |
| automation-design | `pdlc-automation-designer` | Read Grep Glob Write | approved tests, index, language pack | `10-automation-design/proposal.yaml` + `files/**` | — |
| seal-proposal | *script* `pdlc.py seal-proposal` | — | proposal | `sealed.json` (base blob ids, new hashes) | invalid → back to design |
| **G2** | human | | `10-automation-design/` | approval hash | reject → automation-design |
| automation-apply | `pdlc-automation-applier` | Read Bash Write | sealed proposal | worktree branch `pdlc/<story>`, `11-apply/*` | refuses without valid G2 |
| automation-validate | `pdlc-automation-validator` | Read Grep Glob Bash Write | worktree | `12-validation/*` | automation defect → redesign (G2 again) |
| automation-review | `pdlc-automation-reviewer` | Read Grep Glob Bash Write | diff, proposal, story | `13-automation-review/*` | request_changes → redesign |
| **G3** | human | | 11–13 | approval hash | reject → automation-design |
| ado-publish | `pdlc-ado-publisher` | Read Bash Write | approved test cases, regression selection | ADO Test Cases + suites, `14-publish/publish-receipt.json` | verifies G1 + G3 |
| execution | `pdlc-execution-triager` | Read Grep Glob Bash Write | worktree, regression selection | `15-execution/*`, defect drafts | optional |
| traceability-report | `pdlc-traceability-reporter` | Read Grep Glob Write | everything | `16-report/traceability-report.md` → story comment | — |
| pack-curation | `pdlc-regression-pack-curator` | Read Grep Glob Write | tests, regression, master pack | `17-curation/pack-curation.yaml` | optional |
| — | `pdlc-retro-learner` (`/pdlc-retro`) | Read Grep Glob Write | all runs' feedback & reviews | `retro/<date>-retro.md` | proposals only |

## Why these additions

- **Intake** separates *collecting* context from *judging* it, so every later agent works from the same cited facts instead of re-reading raw HTML.
- **Clarification resolution:** a closed task isn't the same as an answered question. This agent catches vague answers and runs follow-up rounds.
- **Impact analysis** grounds regression selection in the real application code rather than titles and guesses.
- **Test strategy** puts risk-based thinking (levels, techniques, what not to test) before case writing, and gives the G1 approver the "why".
- **Test data:** most "automation failures" are data problems. Designing data up front makes cases executable and parallel-safe.
- **Automation indexer** is what makes the framework language-independent: it learns each repo's conventions and reusable components, so design is reuse-first in any stack.
- **Validator:** a reviewer can't tell whether code compiles or steps bind. The validator proves it with allowlisted commands.
- **Execution/triage, traceability, pack curation and retro** close the loop: evidence for the PO, a master pack that doesn't rot, and agents that improve sprint over sprint.

## Candidates for later (outside testing)
Requirements-to-design impact for developers, API contract/schema compatibility review, accessibility/security/performance test planning agents, CI failure diagnosis and flaky-test management, release readiness and change-risk summary, and escaped-defect analysis feeding the regression pack. Add each only when there's an owned data source and a decision it improves.
