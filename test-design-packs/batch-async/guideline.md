# Batch / async / microservice test design

For scheduled jobs, queue/event consumers, and any processing that doesn't return a
synchronous response — the case has to define how you know it finished and what it was
supposed to change.

## Conventions

- `steps[].action` states the trigger precisely: "publish message X to queue Y", "run job
  Z with parameters {...}", "wait for the 02:00 scheduled run" — not "the batch runs".
- `expected_result` asserts specific, checkable **side effects**: exact record counts,
  specific rows/values written, a specific file/queue/topic produced, a specific status
  transition — never "processing completes successfully".
- State how completion is observed (poll a status field, wait for an output
  file/queue/message, check a completion log line). This becomes the automation's wait
  condition, so vague phrasing here becomes a flaky test later.
- Give every case an explicit timeout/SLA if the story implies one ("completes within 5
  minutes of trigger").

## `interface_details`

See `test-case-contract.yaml`: `trigger`, `trigger_detail`, `input`, `completion_signal`,
`expected_side_effects` (a list — specific and checkable, not "processing succeeds"),
`sla_seconds`, `retry_behaviour`.

## Risks to consider (error guessing)

Poison/malformed message handling and dead-letter routing, duplicate delivery /
idempotency, partial-batch failure (rollback vs. partial commit), out-of-order messages,
retry storms, job overlap if the previous run is still in progress, clock/timezone edges
for scheduled triggers, silent failure (job "succeeds" but processes 0 records when it
shouldn't).

## Automation signal

`automation.level: "api"` or a project-specific "integration" level — almost never `ui`.
Note in `automation.reason` how the automation will detect completion without a fixed
sleep (poll with timeout, not a hard-coded wait) — required by
`guidelines/coding-standards.md` regardless of interface.
