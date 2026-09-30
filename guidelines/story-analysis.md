# Story Analysis Guidelines

Goal: make the story testable before anyone designs tests, and ask the fewest, sharpest questions that unblock it.

## Checklist: read every story against these
- **Actors and permissions:** who can do this, who must not, and what anonymous, expired or locked users see.
- **Inputs and validation:** types, formats, mandatory/optional, min/max lengths and values, special characters, and what happens on invalid input (message text, field highlight, blocked submit).
- **Business rules:** calculations, rounding, currency and time zones, precedence when rules interact (decision-table gaps).
- **States and lifecycle:** statuses, allowed transitions, what happens to in-flight items, and idempotency on repeat or double submit.
- **Error and edge handling:** downstream failures, timeouts, empty states, zero or many records, concurrency, partial success.
- **Data:** where it comes from, persistence, audit and history, migration of existing data, retention.
- **UI/UX:** every screen state (loading, empty, error, success), mockup vs. text contradictions, accessibility expectations.
- **Non-functional:** performance targets, security (authorisation, input sanitisation, PII), accessibility (WCAG level), compatibility (browsers/devices), localisation.
- **Integration:** APIs, events, reports, emails and notifications affected; contract changes; backward compatibility.
- **Scope:** what is explicitly out of scope, related stories, feature flags, rollout.
- **Acceptance criteria quality:** each AC is observable, unambiguous and independently verifiable. Watch for vague words: *fast, user-friendly, appropriate, correct, etc., and/or, should, as needed, similar to*.

## Questions
- One question per unknown. Make it specific and answerable, and offer options where possible ("A or B?").
- `why_it_matters` names the test behaviour that depends on the answer.
- **Blocking** means a reasonable tester could design contradictory expected results without the answer. Otherwise the question is **non-blocking** and must carry a `proposed_assumption`.
- Don't ask what the story, attachments, linked items or earlier answers already answer. Cite them instead. Mark previously answered questions `status: answered` with `answer_ref`.
- Don't ask about implementation details testers don't need.
- Keep to about 10 questions. If more exist, the story is probably too big: say so in `invest_assessment.small`.

## Readiness
- `ready`: no open questions.
- `ready_with_assumptions`: only non-blocking questions remain.
- `not_ready`: at least one blocking question.
