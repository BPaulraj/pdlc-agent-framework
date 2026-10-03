# Functional Test Design Guidelines

## Baseline
- Design from the **refined acceptance criteria** (`03-clarification/resolved-requirements.yaml`) when it exists, otherwise from the story-analysis AC plus stated assumptions. Cite the source for each case.
- Every AC gets at least one positive case and, where input or state is involved, at least one negative case. The coverage summary must list every AC. A condition counts as covered by either a new case or a `full` entry in `existing_coverage`.

## Techniques: pick deliberately and record the choice in `technique`
- **Equivalence partitioning:** one case per valid/invalid class. Don't test several invalid classes in one case.
- **Boundary value analysis:** min-1, min, max, max+1 (and just inside) for every ranged input or threshold.
- **Decision tables:** interacting conditions (e.g. membership × coupon type × min spend). Collapse impossible or irrelevant rules and say which ones were collapsed.
- **State transition:** valid transitions and at least the most dangerous invalid ones.
- **Pairwise/combinatorial:** for configuration or compatibility matrices too large to enumerate.
- **Use case / end-to-end flow:** one or two journey cases per story, not more.
- **Error guessing:** from the story-analysis risks, e.g. double submit, back button, session timeout, special characters, concurrency.

## Writing the case
- **Title:** `Verify <behaviour> when <condition>`. Unique, specific, 128 characters or fewer. No "Test 1" or "Check page".
- **One objective per case.** If the title needs "and", split the case.
- **Preconditions** set state: user and role, data, feature flags, starting page. They are not actions.
- **Steps:** one user or system action per step, in imperative voice ("Enter…", "Select…"). Name UI elements by their visible label.
- **Expected results** are observable and exact, e.g. "Total shows $90.00", "Error 'Coupon expired' is shown under the field", "Order status is 'Paid'". Never write "works correctly", "as expected" or "successfully". Not every step needs an expected result, but the verifying steps do.
- **Test data** is concrete and synthetic. Reference data sets (`TD-n`) for anything non-trivial.
- **Priority** (1–4) follows risk and business impact, not the order the cases were written in.
- **Automation suitability** is judged separately from importance. Prefer API/component level when UI adds no value.

## Existing master-pack coverage
- Before writing cases for an AC, search the master pack for it (see "Searching the master pack" in `guidelines/regression-selection.md`). Derive two or three terms per AC from its entities, screens, endpoints, messages and synonyms, and record them in `coverage_summary[].search_terms`.
- Judge a candidate from its **steps and expected results**, never its title alone. Quote the matching step in `evidence`; if it comes from a shared step (it carries `shared_step`), say so, e.g. `"shared step 5501, step 2 expected: '...'"`. A case with a `[shared step N not available]` step can't be judged `full`.
- Record every match in `existing_coverage` with a verdict:
  - **full:** the existing case asserts this exact condition, including the same values and boundaries. Don't write a new case.
  - **partial:** it covers part of the condition, e.g. the happy path but not the new boundary. Write new cases for the `delta` only.
  - **conflicts:** it asserts behaviour this story changes. Write new cases for the new behaviour; regression selection will flag the old case `needs_update`.
- **Eligibility for `full` and `partial`:** the case's `state` must be in `azure_devops.master_pack.reusable_states` in `config/framework.yaml` (default `Ready`). A case in `Design` or `Closed` can't be trusted as coverage; write a new case instead.
- **`full` only fits an AC that restates existing behaviour**, e.g. "the existing email validation still applies on the new screen". An AC that introduces new behaviour can't already have an exact test: use `partial` or `conflicts`, or write a new case.
- `full` and `partial` cases are linked to the story (Tested By) and added to its suite at publish, so they count as the story's coverage in ADO and in the readiness report. Their steps are never changed.
- When in doubt between full and partial, choose partial. A missing condition costs more than a slightly overlapping case.
- `existing_coverage` is the hand-off to regression selection: every listed case gets selected for regression, so it must be accurate.

## Area path and test type (where the case lands in ADO)
- **`area_path` is the module the case tests**, e.g. `Gmail\Messages\Inbox`, never the story's area path (that is a team path in another project). One story can produce cases with different area paths when it touches several modules.
- Pick it from the "Known area paths" list in `.pdlc/cache/master-pack/index.md`, guided by the modules in `04-impact/impact-analysis.yaml`. The master pack's query-based folders select cases by area path, so a wrong one files the case in the wrong folder.
- **Never invent an area path.** If no known path fits a module, set the case's `area_path: null` and add an `area_path_questions` entry: the module, the closest known path, and the question "should a new area path be created, and what is its full path?". A human answers at G1; you then apply the answer on rework and fill in `answer`.
- **`test_type`** is one of the keys under `azure_devops.test_plan.iteration_paths` in `config/framework.yaml`, chosen by what the case validates: functional behaviour → `functional`, integration between internal systems → `system_integration_internal`, and so on. The publisher maps it to the case's iteration path; never use the story's sprint.

## Avoid
- Duplicating master-pack cases. If an existing case fully covers a condition, record it in `existing_coverage` instead of re-writing it.
- Testing implementation details, or testing unchanged behaviour outside the story scope (that is regression's job).
- Hidden dependencies between cases. Each case must run on its own.
