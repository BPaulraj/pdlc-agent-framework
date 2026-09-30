---
name: pdlc-status
description: Show PDLC pipeline status — all story runs, or one story's stages, gates, approvals and feedback.
argument-hint: "[story-id]"
allowed-tools: Bash(python scripts/pdlc.py:*), Read
---

# /pdlc-status $ARGUMENTS

Run `python scripts/pdlc.py status $ARGUMENTS` and present the output.
- **No story given:** one line per run, grouped as needs a human (gates, blocked), waiting on PO/BA, ready to resume, and complete.
- **Story given:** its stage table, followed by the single next action and the exact command for it (`/pdlc-run`, `/pdlc-approve`, `/pdlc-reject`, or `python scripts/pdlc.py unblock ...`).

Read-only: don't change anything.
