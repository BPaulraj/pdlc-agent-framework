# API test design

For any concern the strategy assigns to level `api`/`integration` — REST, RPC, or a
service boundary tested below the UI.

## Conventions

- `steps[].action` names the call precisely: method + endpoint (or operation), not "call
  the API". `steps[].expected_result` asserts the status code AND the specific response
  fields/values that matter — never just "returns 200" or "success".
- Preconditions state auth/role (which token/scope, not "authenticated"), and any
  server-side state the call depends on (an existing resource, its current value).
- Negative cases assert the *exact* error shape the story/contract promises: status code,
  error code/message field — not just "4xx returned".
- One call (and its assertions) per case, unless the objective is explicitly a sequence
  (e.g. create-then-fetch idempotency) — then say so in the objective.

## `interface_details`

See `test-case-contract.yaml` for the shape: `method`, `endpoint` (a path template, never
a resolved URL), `auth`, `request_body`, `expected_status`, `expected_response` (the
specific fields/values this case checks, not the whole schema), `headers` (only when a
header matters to this case).

## Risks to consider (error guessing)

Missing/expired/malformed auth token, wrong content-type, oversized payload, idempotency
on retry (same request twice), concurrent modification (two calls racing the same
resource), pagination edges (empty page, last page, beyond range), rate limiting,
partial/invalid JSON body, unicode/injection characters in string fields.

## Automation signal

API cases are almost always `automation.level: "api"` — flag the rare exception (e.g. a
case that specifically verifies the UI reflects an API error) with a reason.
