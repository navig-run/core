---
id: deployment-checklist
spec_version: 1
name: Deployment Checklist
version: 1.0.0
category: ops
license: MIT
description: A guided pre-deployment verification checklist for production releases.
author: navig
tags: [deploy, checklist, runbook, ops]
target: local

inputs: []

steps:
  - id: run-tests
    kind: instruction
    safety: safe
    text: |
      Run the test suite locally and confirm it is green (e.g. `pytest tests/ -v`).
  - id: check-staging
    kind: instruction
    safety: safe
    text: |
      Verify the staging deployment succeeded and is healthy before touching prod.
  - id: review-changelog
    kind: instruction
    safety: safe
    text: |
      Confirm every change is documented in `CHANGELOG.md`.
  - id: backup-prod
    kind: instruction
    safety: safe
    text: |
      Take a verified backup of the production database first. For a runnable,
      receipt-backed export, apply the `db-snapshot` block instead of a manual dump.
  - id: deploy
    kind: instruction
    safety: safe
    text: |
      Deploy via your CI/CD pipeline. For a receipt-backed, health-verified rollout,
      apply the `safe-deployment` block.
  - id: verify-health
    kind: instruction
    safety: safe
    text: |
      Confirm health checks pass post-deploy (`navig health`) and watch logs for a
      few minutes (`navig logs --follow`).
  - id: notify
    kind: instruction
    safety: safe
    text: |
      Post the deployment announcement to the team channel.

verify:
  kind: none

receipt:
  redact: []
---
# Pre-deployment checklist

A guided runbook of the verification steps to walk before shipping to production:
tests green, staging healthy, changelog current, prod backed up, deploy, verify,
notify.

## Guided (instruction) block

This is a human checklist — no single machine-verifiable outcome — so it ships as
an **instruction** block (`verification: none`). Where a step *does* have a
receipt-backed equivalent, it points you at the executable block: **db-snapshot**
(the backup) and **safe-deployment** (the rollout, verified by a health check).

## Apply

```
navig apply deployment-checklist
```
