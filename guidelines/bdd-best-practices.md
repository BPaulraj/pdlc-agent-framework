# BDD / Gherkin Best Practices

- **Declarative, not imperative.** Write `When the customer applies coupon "SAVE10"`, not `When I click "#coupon" and type "SAVE10" and click "Apply"`. UI mechanics live in step code and page objects.
- **One behaviour per scenario.** A scenario has one `When`, or a single business action split across a few `When` lines. `Then` asserts outcomes the business cares about.
- **Given = state, When = action, Then = observable outcome.** No assertions in `Given`, no setup in `Then`.
- **Keep scenarios short:** 3–7 steps. Use a `Background` only for context every scenario in the file truly shares, and keep it to 1–3 lines.
- **Scenario Outline** is for the same behaviour with different data (e.g. boundaries). Don't use it to cram different behaviours together. Give examples meaningful column names.
- **Reuse the domain vocabulary** already in the repo's feature files (see `step-usage.md`). Consistent wording is what makes steps reusable.
- **Parameterise** with cucumber expressions (`{string}`, `{int}`) or the repo's equivalent. Don't write near-duplicate steps that differ only in a literal.
- **Tags:** `@US-<id>` on every new scenario, `@TC-<key>` per scenario for traceability, plus the repo's suite tags (`@regression`, `@smoke`, ...) as the repo profile describes.
- **Independence:** no scenario relies on another's side effects. Create and clean up data per scenario.
- **Feature file per capability**, placed under the repo's existing domain folder structure. Extend an existing feature file when the capability already has one.
- Feature text must be readable by the PO without explanation.
