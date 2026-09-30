# Test-design packs

The base test case envelope (`templates/functional-test-case.yaml`) is interface-neutral:
key, title, objective, priority, steps, acceptance-criteria refs. A pack supplies only
what differs by *how the system under test is exercised* — the shape of `interface_details`
on each test case, and the conventions/techniques specific to that surface.

- `guideline.md`: conventions, step/expected-result phrasing, common error-guessing risks,
  and the automation signal for this interface. Read alongside the universal
  `guidelines/test-design.md`, never instead of it — that file's coverage, technique and
  writing rules apply to every case regardless of interface.
- `test-case-contract.yaml`: the shape of `interface_details` for this interface, with a
  worked example. Copy the example's field names; don't invent new ones per case.

## How it's wired into the pipeline

1. `pdlc-test-strategist` assigns an `interface` (this pack's folder name) to each concern
   in `test_levels`, based on what the story and impact analysis actually describe (an
   endpoint, a scheduled job, a UI flow, a data migration...).
2. `pdlc-test-designer` reads that pack's `guideline.md` and `test-case-contract.yaml` for
   every case a concern produces, and sets `interface` + `interface_details` on the case
   accordingly.
3. `pdlc-test-reviewer` checks each case against its pack's expectations (see
   `guidelines/review-checklists.md` → "Per-interface checks").
4. `pdlc-automation-designer` reads the same `interface_details` to pick the right
   automation shape (UI step / REST client / batch trigger+poll / DB assertion helper)
   instead of inferring it from free-text steps.

One story can mix interfaces freely — a single `test-cases.yaml` still holds every case;
only the per-case `interface`/`interface_details` differ. No pipeline fork, no separate
file per interface.

## Packs included

| Pack | For |
|---|---|
| `web-ui` | Browser-rendered user flows |
| `api` | REST/RPC/service calls exercised below the UI |
| `batch-async` | Scheduled jobs, queue/event consumers, anything without a synchronous response |
| `db-validation` | Data-outcome correctness: migrations, ETL, transformations, computed/aggregate fields |
| `other` | Fallback — no dedicated pack yet; rely on plain-language `steps`/`expected_result` |

## Add a pack

1. Create `test-design-packs/<name>/`.
2. Write `guideline.md`: phrasing conventions, data shape, and techniques/risks specific to
   this surface. Don't repeat what `guidelines/test-design.md` already says generically.
3. Write `test-case-contract.yaml`: the `interface_details` shape, with one fully worked
   example case. Keep field names short and consistent with existing packs where the
   concept overlaps (e.g. every pack with a "trigger" calls it `trigger`).
4. Add a bullet to `guidelines/review-checklists.md` → "Per-interface checks": what a
   reviewer must see the case demonstrate before approving it.
5. Mention the new pack's name is now a valid `interface` value — there's no enum to edit
   anywhere else; agents discover packs by directory, not a hardcoded list.

Candidates worth splitting out once they recur: `messaging-async` (a queue/topic consumer
tested as a unit, separate from the batch job that reads its output — compare against
`batch-async` first; they may overlap enough to share one pack), `graphql` (if `api`'s
REST-shaped contract doesn't fit), `cli`.
