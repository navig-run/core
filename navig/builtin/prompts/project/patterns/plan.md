---
title: Plan it
description: Turn an idea (and its brief) into a plan doc with phases and checkbox tasks.
input: idea
output: plan
---

You are turning ONE IDEA into a plan document for a project, given the idea, its brief if any, and
the project's CONTEXT PACK (which shows how the project's existing plans are written — match that
style and vocabulary).

Write markdown:

# <short plan title>

> Status: proposed · from idea "<the idea>"

## Goal
What is true when this is done, in 2-3 sentences. How we will know (a check someone can run).

## Phases
2-4 phases, smallest shippable first. For each:

### Phase N — <name>
One line of intent, then the tasks as a checklist (`- [ ] …`), each task small enough for one
sitting, concrete (names the screen, route, file or command when the context shows it).

## Out of scope
Bullets — what this plan deliberately does not do.

## Risks
Bullets with the mitigation in the same line.

Never invent facts about the codebase that the context does not show — say what must be checked
first as a task instead.
