---
name: pdlc-approve
description: Human approval of a PDLC gate (G1 tests, G2 automation design, G3 applied automation + ADO publish). Only a human may invoke this.
argument-hint: "<story-id> <gate-id> [comment]"
disable-model-invocation: true
allowed-tools: Bash(python scripts/pdlc.py:*)
---

# /pdlc-approve $ARGUMENTS

The user is approving a gate. Arguments: story id, gate id (`G1-test-approval`, `G2-automation-design-approval` or `G3-automation-and-publish-approval`; accept the short forms `G1`/`G2`/`G3` and expand them), and an optional comment.

1. Run `python scripts/pdlc.py approve <story> <gate> --comment "<comment>"`. The approver identity defaults to the git user email. The approval is bound to the SHA-256 of the gate's artifacts, so any later edit invalidates it.
2. Report the result in one line.
3. Tell the user to continue with `/pdlc-run <story>` (or that the sprint watcher will resume it).
4. Suggest — don't run it yourself — capturing this approval as a golden-set baseline, so drift can be
   tracked across runs later (see `docs/evals.md`). This is the QA lead's call, not automatic: only a
   story whose output is representative and worth using as a future reference is worth capturing, and
   picking the name is a deliberate, curatorial act, not a mechanical one. Print the ready-to-run command:
   `python scripts/golden_eval.py capture <story> --name <suggested-name>`
   Suggest `<suggested-name>` as `<story>-<gate-short>` (e.g. `US-1234-g1`), lower-cased, but let the
   user pick their own. `meta.json` already records exactly which gate(s) were approved at capture time,
   so no extra flag is needed. Mention it is skippable and does not block anything.

Do not run the pipeline, edit artifacts, or approve any other gate.
