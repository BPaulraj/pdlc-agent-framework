# Language packs

The pipeline, agents and artifacts are language-neutral. A language pack supplies only the defaults that the target repository does not already settle:

- `conventions.md`: idioms, structure, and "how we do X here" for that stack.
- `*.template`: skeletons for new files (step definitions, page objects, API clients, runners).

The automation indexer picks the closest pack and records it in `.pdlc/cache/automation-index/repo-profile.yaml` (`language_pack`). You can pin it with `automation.language_pack` in `config/framework.yaml`. **Repository conventions always win over pack defaults.**

## Add a pack
1. Create `language-packs/<stack>/` (e.g. `typescript-playwright-bdd`, `python-behave`, `csharp-reqnroll`).
2. Write `conventions.md` using the same headings as `java-cucumber/conventions.md`.
3. Add templates for the file kinds your repo creates most often. Use `${placeholder}` markers.
4. If your step-definition syntax isn't recognised by `scripts/index_steps.py` (`STEP_PATTERNS`), add a regex there.

Packs included: `java-cucumber` (Cucumber-JVM + JUnit/TestNG, Selenium or Playwright-Java, RestAssured).
