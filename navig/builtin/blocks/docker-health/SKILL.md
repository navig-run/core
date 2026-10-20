---
name: Docker Health Check
description: A guided checklist to verify Docker containers are healthy and resources are adequate.
version: 1.0.0
generated-by: navig-block
block-id: docker-health
---

# Docker health checklist

A guided runbook for verifying Docker infrastructure health: daemon, running
containers, resource usage, logs, disk headroom, and stopped containers.

## Guided (instruction) block

A diagnostic checklist, not a single verifiable outcome (`verification: none`).
For a runnable full-host sweep, apply the **server-health** block. You can also
automate a periodic reminder with `navig trigger add`.

## Apply

```
navig apply docker-health
```

## Apply this outcome

This is a NAVIG **block**. Run it end-to-end with:

```
navig apply docker-health
```

### Inputs

- (none)

