---
name: Database Backup Runbook
description: A guided procedure for creating and verifying database backups, with recovery and scheduling notes.
version: 1.0.0
generated-by: navig-block
block-id: backup-runbook
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

## Apply this outcome

This is a NAVIG **block**. Run it end-to-end with:

```
navig apply backup-runbook
```

### Inputs

- (none)

