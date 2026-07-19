---
id: backup-essential
spec_version: 1
name: Backup Essential Config
version: 1.0.0
category: ops
license: MIT
description: A guided checklist to review and back up essential NAVIG config files before a change.
author: navig
tags: [backup, config, checklist, runbook, ops]
target: local

inputs: []

steps:
  - id: list-config
    kind: instruction
    safety: safe
    text: |
      List the NAVIG config directory to see what you are about to preserve:
      `navig file list /etc/navig`
  - id: read-config
    kind: instruction
    safety: safe
    text: |
      Review the current global config before changing anything:
      `navig file show /etc/navig/config.yaml`
  - id: check-git
    kind: instruction
    safety: safe
    text: |
      If the config directory is version-controlled, confirm a clean state:
      `navig run "git status"`

verify:
  kind: none

receipt:
  redact: []
---
# Back up essential config — guided checklist

A quick, guided runbook for reviewing and preserving essential NAVIG config
before you make a change.

## Why this is a guided (instruction) block

Config backup is a judgement call about *what* to preserve and *where* — there is
no single machine-verifiable outcome to assert, so it ships as an **instruction**
block (`verification: none`) that walks you through the review. For a *verifiable*
database export, use the executable **db-snapshot** block instead.

## Apply

```
navig apply backup-essential
```
