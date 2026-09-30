# Web UI test design

This is the framework's original surface — most of `guidelines/test-design.md` is already
written with it in mind. This file adds only what's specific beyond that baseline.

## Conventions

- Name elements by their visible label, never by selector/DOM id/CSS class — automation
  design resolves the actual selector later, from the label and the repo's page objects.
- Preconditions state the starting page, session/role, and any feature flag — not how you
  got there ("logged in as `standard_customer`, on the cart page", not "log in, then
  navigate to...").
- One user action per step, imperative voice: "Select 'Apply'", "Enter 'SAVE10' in the
  coupon field". Give the verifying step its own line with the exact expected result.

## `interface_details` (optional)

Use it only when the default `steps`/`preconditions` don't already say enough — e.g. a
case whose behaviour specifically depends on viewport or browser. See
`test-case-contract.yaml`. Most web-ui cases need no `interface_details` at all — that's
expected, not a gap.

## Risks to consider (error guessing)

Back/forward navigation, double-submit, session timeout mid-flow, browser refresh on a
multi-step form, client-side validation disagreeing with server-side validation,
copy/paste into fields with format constraints, right-to-left locales if in scope.

## Automation signal

`automation.level: "ui"` only when the behaviour genuinely requires rendering (visual
layout, client-side JS, drag-and-drop). Prefer `api`/`component` for anything the UI
merely displays or forwards to a service — say so in the case's `automation.reason`.
