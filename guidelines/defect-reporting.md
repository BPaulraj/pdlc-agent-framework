# Failure Triage and Defect Reporting Guidelines

## Classify every failure
| Class | Typical evidence |
|---|---|
| `product_defect` | Correct steps and data, and the application response contradicts an approved requirement. Reproducible. |
| `automation_defect` | Locator not found after a UI change, wrong step binding, assertion on the wrong element, script error. |
| `environment` | 5xx/timeouts across unrelated tests, DNS/SSL issues, a service down, deployment mismatch. |
| `test_data` | Missing or consumed data, collisions from parallel runs, expired fixtures. |
| `flaky` | Passes on rerun with no change, or is timing-dependent. Needs pass/fail history as evidence. |
| `inconclusive` | Not enough evidence. Say what would decide it. |

- Never label a failure `product_defect` on a guess. Cite the requirement it violates and the evidence, and list alternative explanations.
- Rerun a failing test at most once, and only to check flakiness. Report both results.

## Defect drafts (product_defect only)
- Title: `<Area>: <observable wrong behaviour> when <condition>`.
- Minimal repro steps, exact expected vs. actual, environment/build, test data, and evidence (log excerpt, screenshot path, request/response).
- Link the test case and story. Severity reflects user impact, not how the test failed.
- Drafts are filed by a human. The framework never creates Bugs automatically.
