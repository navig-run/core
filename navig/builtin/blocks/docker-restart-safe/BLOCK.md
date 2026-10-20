---
id: docker-restart-safe
spec_version: 1
name: Docker Restart (safe)
version: 1.0.0
category: ops
license: MIT
description: Restart one container and prove it came back — the approval is a policy gate, not a sentence in a runbook.
author: navig
tags: [docker, restart, container, ops, verified]
allowed-tools: Bash
target: local

inputs:
  - key: container
    type: string
    label: Container name or id (as shown by `navig docker ps`)
    required: true

steps:
  - id: preflight
    kind: command
    safety: safe
    capabilities: [exec:navig]
    network: required
    argv: [navig, docker, inspect, "{{inputs.container}}"]

  - id: restart
    kind: command
    safety: destructive
    capabilities: [remote, exec:navig]
    network: required
    argv: [navig, docker, restart, "{{inputs.container}}"]

verify:
  kind: command
  level: self-check
  argv: [navig, docker, inspect, "{{inputs.container}}"]
  expect:
    exit_code: 0
  timeout_seconds: 60

receipt:
  redact: []
---
# Restart one container — and know it came back

```
navig apply docker-restart-safe --input container=web-1 --approve restart
```

Three things happen, in this order: the container is **inspected** (a typo or a dead host
fails before anything is touched), it is **restarted**, and docker is asked about it **again**
so the receipt records a real machine answer rather than an assumption.

The restart step declares the `remote` capability, so it is **destructive by policy** and
will not run without `--approve restart`. A blanket `--yes` is not enough — the approval
must name the step.

## What this replaces

The old `docker-restart-safe` skill was frontmatter plus a sentence: *"warn the user, then
run `navig docker restart <name>`"*. Nothing enforced the warning, and nothing checked the
result — a restart that silently failed looked exactly like one that worked. Here the
confirmation is the policy gate and the outcome is a verify step, so both are structural.

## What "verified" means here

- The **restart step's exit code** is the proof the restart itself succeeded (a failed
  `docker restart` aborts the run before verification).
- The **verify** (`navig docker inspect <container>`, exit 0) proves docker still answers and
  the container is still known after the restart.
- It does **not** prove the process inside is healthy. Confirm `Up` with
  `navig docker ps`, and read the logs (`navig docker logs --tail 50 <container>`) if the
  service is not answering. The receipt records the inspect result, not a health claim.

## When not to use it

- **The whole stack** — `docker compose down` / `up` is a wider change (networks, volumes,
  ordering); do that deliberately, not through this block.
- **You have not identified the container** — run `navig docker ps` first; this block
  addresses one container by name.
- **A remote host you have not selected** — `navig host use <name>` (or pass the host through
  your configured default) before applying.
