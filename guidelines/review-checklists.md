# Review Checklists

Reviewers are independent: judge the artifacts against the story, the guidelines and the evidence. Don't judge them against the producer's own rationale. Use `blocking` only for issues that would make the gate decision wrong.

## Test design review (functional cases + test data + regression)
- [ ] Every refined AC is covered: positive, negative, and boundary where relevant. The coverage matrix is complete.
- [ ] No case tests behaviour absent from the requirements (invented expected results).
- [ ] Assumptions that cases depend on are labelled and traceable to non-blocking questions.
- [ ] Titles are unique and specific. Each case has one objective and the steps are atomic.
- [ ] Expected results are observable and exact, with no "works as expected".
- [ ] Preconditions and test data are concrete, synthetic, and support independent runs.
- [ ] Techniques fit the inputs: boundaries on ranges, decision tables on interacting rules.
- [ ] No duplicates within the set or against the master pack.
- [ ] Every `existing_coverage` entry cites a real master-pack id, its `evidence` is quoted from that case's steps, and its verdict holds. `full` only when every value and boundary of the condition is asserted. `full`/`partial` cases are in a reusable state (`master_pack.reusable_states`), and no `full` verdict sits on an AC that introduces new behaviour (**major**).
- [ ] Every AC has `search_terms`, and a spot-check of them finds no obvious missed match.
- [ ] Every `existing_coverage` entry appears in the regression selection (or is excluded with a reason). `conflicts` entries are flagged `needs_update`.
- [ ] Priorities reflect risk.
- [ ] Every case has an `area_path` that is its module (not the story's team path) and appears in the master pack's known area paths or in a human answer, or is `null` with an open `area_path_questions` entry. Every `test_type` is a key of `test_plan.iteration_paths`.
- [ ] Regression selections have credible reasons. Must-run covers every directly impacted module, near-misses are listed, and gaps are reported, not hidden.
- [ ] Master-pack cases whose expected behaviour changes are flagged `needs_update`.

### Per-interface checks (test-design-packs/<interface>/)
- [ ] `web-ui`: elements named by visible label, not selector; `interface_details` present only when the case genuinely depends on viewport/browser.
- [ ] `api`: `expected_response` asserts specific fields/values, not just the status code; `endpoint` is a path template with no real host; negative cases assert the exact error code/shape.
- [ ] `batch-async`: `expected_side_effects` are specific and checkable (not "processing succeeds"); `completion_signal` is stated; an SLA is present if the story implies one.
- [ ] `db-validation`: before/after states are both concrete; `assertions` check exact values, not just row presence; `isolation` is stated.
- [ ] `other`: flagged in the designer's summary as a possible gap, not silently treated as a permanent home for a recurring surface.

## Automation review (applied branch)
- [ ] The diff equals the approved manifest (`13-automation-review/diff-check.json` → `ok: true`). Anything else is **blocking**.
- [ ] Every automated scenario maps to an approved test case, and every approved automation candidate is automated or has a stated reason.
- [ ] Existing steps and components were reused wherever one fits. New steps don't duplicate existing ones under different wording.
- [ ] Gherkin follows the BDD guidelines: business language, one behaviour per scenario, no UI choreography.
- [ ] Code follows the repo profile conventions and the coding standards: structure, naming, waits (no fixed sleeps), assertions, DI, logging.
- [ ] No hard-coded secrets, environment URLs or credentials. Test data follows the data plan.
- [ ] Scenarios are tagged `@US-<id>` plus test case keys, and hooks and cleanup keep tests independent.
- [ ] Validation results are understood: failures are classified, and nothing flaky is hidden.
