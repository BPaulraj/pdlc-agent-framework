# Functional Test Design Guidelines

## Baseline
- Design from the **refined acceptance criteria** (`03-clarification/resolved-requirements.yaml`) when it exists, otherwise from the story-analysis AC plus stated assumptions. Cite the source for each case.
- Every AC gets at least one positive case and, where input or state is involved, at least one negative case. The coverage summary must list every AC.

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

## Avoid
- Duplicating master-pack cases. If an existing case covers the behaviour, reference it for regression instead of re-writing it.
- Testing implementation details, or testing unchanged behaviour outside the story scope (that is regression's job).
- Hidden dependencies between cases. Each case must run on its own.
