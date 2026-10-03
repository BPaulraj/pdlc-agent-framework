# Regression Selection and Impact Guidelines

## Impact analysis (application code)
- Trace from the story's nouns and verbs (entities, screens, endpoints) to code: routes/controllers, services, UI components, DB tables and migrations, configuration, scheduled jobs, message topics.
- Follow **one hop of dependencies** in each direction: who calls the changed code, and what it calls. Shared utilities such as pricing, auth, validation and formatting widen the impact. Say so explicitly.
- Record confidence. Without an application repo, or when the change can't be located, record the impact as `low` confidence and explain why.

## Searching the master pack
The pack can hold thousands of cases. A JSONL line holds the full case with all its steps, so a broad Grep of the JSONL returns whole cases and floods your context. Search in three steps:
1. **Shortlist from the search index** `.pdlc/cache/master-pack/master-pack-index.tsv`: one short row per case (`id, priority, automation_status, state, area_path, iteration_path, suites, title, tags, shared_step_refs`). Grep it by title keywords, suite path, area/iteration path and tags. Use `index.md` (cases per suite) to orient first.
2. **Search step text without pulling whole cases:** Grep `master-pack.jsonl` with `-o` and `-n` and a bounded pattern such as `.{0,60}<term>.{0,60}`, with a `head_limit`. You get short snippets and line numbers, not full cases. Read a hit with `Read` (offset = line number, limit = 1).
3. **Read full cases only for shortlisted ids:** Grep `master-pack.jsonl` for `^\{"id": <id>,` (one line per case).

Never Read the whole JSONL or Grep it without `-o`.

**Shared steps** are inlined where each case references them, and every such step carries `"shared_step": <id>`; each case also lists its `shared_step_refs`. A step reading `[shared step N not available]` means that content couldn't be read, so don't judge coverage from the rest of the case alone.

## Selecting from the master pack
- When the story changes behaviour inside a shared step, every case using it is affected. Find them all in the `shared_step_refs` column of `master-pack-index.tsv` (Grep `\b<id>\b`), flag them `needs_update`, and note in `update_note` that the fix belongs in shared step `<id>` once, not in each case.
- Start from the impacted modules and dependencies, then search the master pack by suite path, area path, title keywords, and step text (entity names, screen names, API names).
- Relations, strongest first: **direct** (tests the changed behaviour), **dependency** (tests a consumer or provider), **shared_data**, **shared_ui**, **integration**, **historical_defect_area**.
- Priority:
  - **must_run:** direct, or dependency with high risk.
  - **should_run:** medium-risk dependencies and shared data.
  - **could_run:** broad smoke around the area.
- Flag `needs_update: true` when the story changes behaviour an existing case asserts. Those cases will fail for the right reason.
- List **excluded near-misses** with reasons. That shows the search was real and helps the reviewer.
- Report **gaps** honestly. Never pad the selection with loosely related cases to look thorough.
- Keep the set proportionate: a one-screen tweak should not select half the pack. If more than about 15% of the pack looks impacted, say so and explain.
