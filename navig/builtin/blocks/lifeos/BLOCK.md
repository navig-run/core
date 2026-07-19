---
id: lifeos
spec_version: 1
name: Life OS Daily Routine
version: 0.1.0
category: productivity
license: MIT
description: A template daily-routine outline (draft) — the commands are aspirational and not yet wired to real CLI handlers.
author: navig
tags: [productivity, routine, template, draft]
target: local

inputs: []

steps:
  - id: morning-priorities
    kind: instruction
    safety: safe
    text: |
      Pick the top 3 priorities for the day, then capture them as tasks.
  - id: review-context
    kind: instruction
    safety: safe
    text: |
      Skim relevant project context and notes before you start.
  - id: capture
    kind: instruction
    safety: safe
    text: |
      Capture new to-dos, notes, and any expense as they come up.
  - id: close-loops
    kind: instruction
    safety: safe
    text: |
      At the end of the day, mark completed tasks done to close open loops.

verify:
  kind: none

receipt:
  redact: []
---
# Life OS — daily routine (draft)

A template outline for a personal daily operating routine: set priorities, load
context, capture as you go, and close loops.

## Draft — read before applying

This block is a **draft** (`v0.1.0`). The original runbook referenced commands
(`task next`, `know.search`, `finance.record`, …) that are **not wired to real
CLI handlers**, so this ships as a plain **instruction** outline rather than
promising execution it cannot deliver. Treat it as a starting point to adapt; it
does not run any command.

## Apply

```
navig apply lifeos
```
