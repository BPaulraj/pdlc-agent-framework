---
name: pdlc-reject
description: Human rejection of a PDLC gate with feedback; sends the run back to the stage that produces the rejected work. Only a human may invoke this.
argument-hint: "<story-id> <gate-id> <feedback>"
disable-model-invocation: true
allowed-tools: Bash(python scripts/pdlc.py:*)
---

# /pdlc-reject $ARGUMENTS

The user is rejecting a gate. Arguments: story id, gate id (short forms `G1`/`G2`/`G3` expand to the full ids), then free-text feedback.

1. If the feedback is empty, ask what should change. The agents need specifics.
2. Run `python scripts/pdlc.py reject <story> <gate> --feedback "<feedback>"`. This stores the feedback, archives the affected artifacts under `runs/<story>/history/`, and resets the run to the gate's rework stage (G1 → test-design, G2/G3 → automation-design).
3. Report the result, and tell the user `/pdlc-run <story>` will rework with their feedback.
