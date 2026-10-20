---
name: Session Startup
description: A guided session-startup checklist — verify hosts, connectivity, containers, disk, and open work before you start operating.
version: 1.0.0
generated-by: navig-block
block-id: startup
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

## Apply this outcome

This is a NAVIG **block**. Run it end-to-end with:

```
navig apply startup
```

### Inputs

- (none)

