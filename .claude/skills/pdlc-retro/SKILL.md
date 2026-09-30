---
name: pdlc-retro
description: Sprint retrospective for the PDLC agents — mines gate rejections, review loops and clarification patterns across runs and proposes guideline/template improvements.
argument-hint: "[optional focus, e.g. 'test design' or 'automation']"
allowed-tools: Read, Glob, Agent
---

# /pdlc-retro $ARGUMENTS

Invoke the Agent tool with `subagent_type: pdlc-retro-learner` and this prompt:

"Analyse all runs under runs/ (focus: $ARGUMENTS, or everything if empty). Write retro/<today's date>-retro.md with metrics, recurring issues and proposed guideline/template/agent edits. Propose only."

Then summarise the top 3–5 proposals for the user, and ask which ones to apply. If the user picks some, apply them yourself as normal edits so they can review the diff.
