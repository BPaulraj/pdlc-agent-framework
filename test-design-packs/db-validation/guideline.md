# DB / data-processing validation test design

For cases whose objective is that *data ends up correct* after a process (migration, ETL,
transformation, trigger, computed/aggregate field) — the data outcome matters, not the
process's external interface.

## Conventions

- State the **before** dataset (seed/fixture) and the **after** assertion explicitly —
  this is a before/after comparison, not a single query check. If there's no meaningful
  "before", state what invariant holds instead (referential integrity, no orphans, no
  duplicates).
- Assert **exact values**, not just presence/row-count, whenever the story implies a
  computation: "customer_ltv = 1042.50", not "customer_ltv is populated".
- Every case states its **isolation**: which schema/instance, and how it's reset
  (transactional rollback, dedicated seeded schema, truncate-after) — data cases are the
  easiest to make flaky by leaking state between runs.
- Prefer asserting through the same path the application/consumers actually read (a view,
  an API, a report query) over raw table inspection, when both are available and the story
  is about user-visible correctness rather than internal schema shape.

## `interface_details`

See `test-case-contract.yaml`: `seed_data`, `action`, `target`, `assertions` (exact
values/invariants, not just row presence), `isolation`.

## Risks to consider (error guessing)

Null/missing source fields propagating incorrectly, type coercion/precision loss (float
rounding on money), timezone shift on date fields, duplicate source rows producing
duplicate or double-counted output, partial/failed load leaving inconsistent state,
character encoding on text fields, overflow on aggregates, reprocessing the same source
window (idempotency of the transform itself).

## Automation signal

`automation.level: "api"` or a project-specific "data"/"integration" level, asserting via
a DB client or the repo's existing data-assertion helpers — reuse-first, per
`guidelines/automation-design.md`. Rarely a `ui` case.
