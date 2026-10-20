---
name: Server Health Check
description: Run a comprehensive read-only diagnostic sweep on a remote server (uptime, memory, disk, services, docker, network).
allowed-tools: Bash
version: 1.0.0
generated-by: navig-block
block-id: server-health
---

# Diagnose a remote server's health

Runs a read-only sweep — OS/kernel, uptime and load, memory, disk, running and
failed services, docker, outbound network, recent logins — and prints one report.

## Honest scope

This is a **diagnostic**, not a verifiable outcome, so it reports
`verification: none` — there is no single machine-checkable end state for "is the
server healthy"; you read the report. Every check is chained in one remote shell
with `;` (and guarded with `|| echo …`), so a missing tool (e.g. no docker) never
aborts the sweep — the resilient equivalent of the old workflow's
`continue_on_error`. Running it as a single `navig run` also fixes the legacy
workflow's broken quote-splitting.

## Apply

```
navig apply server-health --input host=production
```

Nothing here mutates the server, so no `--approve` is needed.

## Apply this outcome

This is a NAVIG **block**. Run it end-to-end with:

```
navig apply server-health --input host=<string>
```

### Inputs

- `host` (string, required) — Host to inspect (a configured navig host)

