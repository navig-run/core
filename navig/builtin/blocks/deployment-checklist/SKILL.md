---
name: Deployment Checklist
description: A guided pre-deployment verification checklist for production releases.
version: 1.0.0
generated-by: navig-block
block-id: deployment-checklist
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

## Apply this outcome

This is a NAVIG **block**. Run it end-to-end with:

```
navig apply deployment-checklist
```

### Inputs

- (none)

