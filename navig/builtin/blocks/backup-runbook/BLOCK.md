---
id: backup-runbook
spec_version: 1
name: Database Backup Runbook
version: 1.0.0
category: ops
license: MIT
description: A guided procedure for creating and verifying database backups, with recovery and scheduling notes.
author: navig
tags: [backup, database, runbook, ops]
target: local

inputs: []

steps:
  - id: switch-host
    kind: instruction
    safety: safe
    text: |
      Switch to the target host:  `navig host use production`
  - id: check-space
    kind: instruction
    safety: safe
    text: |
      Confirm enough disk for the backup file:  `navig run "df -h"`
  - id: create-backup
    kind: instruction
    safety: safe
    text: |
      Create the backup. For a runnable, receipt-backed export to your workspace,
      apply the `db-snapshot` block instead of a manual dump.
  - id: verify-integrity
    kind: instruction
    safety: safe
    text: |
      Verify the backup — confirm the file exists, note its size, and checksum it.
  - id: test-restore
    kind: instruction
    safety: safe
    text: |
      Optionally rehearse a restore in staging (dry-run) so recovery is proven, not
      assumed.

verify:
  kind: none

receipt:
  redact: []
---
# Database backup runbook

A guided procedure for creating and verifying database backups.

## Recovery & scheduling notes

- If a backup fails, check disk space and DB connectivity, then retry.
- If verification fails, delete the corrupt backup and retry.
- Keep at least 3 verified backups before deleting old ones.
- Store backups on separate storage; encrypt anything sensitive.
- Schedule: daily automated backups, weekly restore verification, monthly retention cleanup.

## Guided (instruction) block

This is a doc-style runbook (procedure + recovery + schedule), not a single
machine-verifiable outcome, so it ships as an **instruction** block. For the
*executable, receipt-backed* export, apply the **db-snapshot** block.

## Apply

```
navig apply backup-runbook
```
