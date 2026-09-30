# Regression Selection and Impact Guidelines

## Impact analysis (application code)
- Trace from the story's nouns and verbs (entities, screens, endpoints) to code: routes/controllers, services, UI components, DB tables and migrations, configuration, scheduled jobs, message topics.
- Follow **one hop of dependencies** in each direction: who calls the changed code, and what it calls. Shared utilities such as pricing, auth, validation and formatting widen the impact. Say so explicitly.
- Record confidence. Without an application repo, or when the change can't be located, record the impact as `low` confidence and explain why.

## Selecting from the master pack
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
