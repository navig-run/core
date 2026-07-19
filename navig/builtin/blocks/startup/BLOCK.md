---
id: startup
spec_version: 1
name: Session Startup
version: 1.0.0
category: ops
license: MIT
description: A guided session-startup checklist — verify hosts, connectivity, containers, disk, and open work before you start operating.
author: navig
tags: [startup, checklist, runbook, ops]
target: local

inputs: []

steps:
  - id: check-hosts
    kind: instruction
    safety: safe
    text: |
      List configured hosts and confirm the active host is the one you intend to
      operate on:  `navig host list`
  - id: test-connection
    kind: instruction
    safety: safe
    text: |
      Test SSH connectivity to the active host:  `navig host test`
  - id: check-containers
    kind: instruction
    safety: safe
    text: |
      Check running containers on the active host:  `navig docker ps`
  - id: check-disk
    kind: instruction
    safety: safe
    text: |
      Confirm the host has headroom on disk:  `navig run "df -h"`
  - id: check-resources
    kind: instruction
    safety: safe
    text: |
      Glance at CPU / memory / load:  `navig host monitor show --resources`
  - id: review-work
    kind: instruction
    safety: safe
    text: |
      Review open work before you start:  `gh pr list` and `gh run list` (recent CI).

verify:
  kind: none

receipt:
  redact: []
---
# Session startup checklist

A short, guided runbook for the start of an operating session: confirm the active
host, connectivity, containers, disk headroom, resources, and open work.

## Why this is a guided (instruction) block

These are checks *you* read and act on — there is no single machine-verifiable
outcome, so it ships as an **instruction** block (`verification: none`). It walks
you through each step rather than executing them; run the commands shown as you
go. Deep infra diagnostics live in the executable **server-health** block.

## Apply

```
navig apply startup
```
