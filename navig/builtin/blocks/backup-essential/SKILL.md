---
name: Backup Essential Config
description: A guided checklist to review and back up essential NAVIG config files before a change.
version: 1.0.0
generated-by: navig-block
block-id: backup-essential
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

## Apply this outcome

This is a NAVIG **block**. Run it end-to-end with:

```
navig apply backup-essential
```

### Inputs

- (none)

