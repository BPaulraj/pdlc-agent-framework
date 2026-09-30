# Test Data Guidelines

- Every non-trivial test case gets a data set (`TD-n`) listing exact values, how to create it, and how to clean up.
- **Synthetic by default.** Never use real customer or employee data. If production-like data is needed, specify masking.
- **Reuse existing data builders, factories, fixtures and seed APIs** from the automation repo (see the automation index). Cite them by file:line.
- Boundary cases need data sitting exactly on the boundaries. List which boundaries each data set covers.
- **Isolation:** data must support parallel and repeated runs. Use unique suffixes, per-test users, and no shared mutable records.
- **Time:** make time-dependent data relative (e.g. `today+30d`) and state the time zone. Flag cases that need clock control.
- Name the environment dependencies: feature flags, configuration, third-party sandboxes, email/SMS catchers.
- Link data sets to test cases through `used_by` in the data plan. The data designer never edits `test-cases.yaml`.
