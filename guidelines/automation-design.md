# Automation Design Guidelines

- **Read the index first:** `.pdlc/cache/automation-index/` holds `repo-profile.yaml`, `components.md`, `step-catalog.md`, `features.md` and `step-usage.md`. Then open the specific files you intend to reuse or modify, to confirm signatures.
- **Follow the repository, not the template.** The repo's actual language, framework, naming, structure, formatting, assertion style and DI pattern win. Templates and language packs are defaults for the parts the repo doesn't settle.
- **Reuse ladder:** use an existing step as-is, then parameterise or extend an existing step or method, then add a new step that uses existing page objects or clients, then add a new page object or client method, and only then add a new class. Record in `reuse.new_components.why_new` why each rung above did not fit, citing what you searched.
- **Minimal change:** only the files needed to automate the approved candidate test cases. No refactors, reformatting, dependency or build-file changes, config changes, or "while I'm here" fixes. Propose those separately in `checks.risks`.
- **Modify** operations: stage the full final file. Change only what `change_summary` says, and preserve everything else byte-for-byte, including line endings, imports order and formatting.
- **No sleeps, no hard-coded environments or credentials.** Use the repo's waits, config and secret mechanisms.
- **Traceability:** every scenario is tagged `@<story>` and `@<test case key>`, and `scenario_map` covers every approved test case.
- **Design-time is read-only.** Never touch the automation repo. Stage files under `runs/<story>/10-automation-design/files/`. `pdlc.py apply` applies them after human approval, in a separate git worktree and branch.
- After approval, nothing outside the approved manifest may change. A compile error or failed scenario sends the proposal back for redesign, and it needs approval again.
- Report the commands you expect to verify the change, prerequisites, and any flakiness risks.
