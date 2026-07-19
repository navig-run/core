---
id: server-health
spec_version: 1
name: Server Health Check
version: 1.0.0
category: ops
license: MIT
description: Run a comprehensive read-only diagnostic sweep on a remote server (uptime, memory, disk, services, docker, network).
author: navig
tags: [health, diagnostics, ssh, ops, read-only]
allowed-tools: Bash
target: local

inputs:
  - key: host
    type: string
    label: Host to inspect (a configured navig host)
    required: true
    default: production

steps:
  - id: set-host
    kind: command
    safety: safe
    capabilities: [exec:navig]
    network: required
    argv: [navig, host, use, "{{inputs.host}}"]

  - id: report
    kind: command
    safety: safe
    capabilities: [exec:navig]
    network: required
    argv:
      - navig
      - run
      - "echo '-- system --'; uname -a; cat /etc/os-release 2>/dev/null | head -5; echo '-- uptime & load --'; uptime; echo '-- memory --'; free -h; echo '-- disk --'; df -h; echo '-- running services --'; systemctl list-units --type=service --state=running 2>/dev/null | head -20; echo '-- failed services --'; systemctl list-units --type=service --state=failed 2>/dev/null; echo '-- docker --'; docker ps 2>/dev/null || echo '(docker unavailable)'; echo '-- network --'; ping -c 3 8.8.8.8 || echo '(ping failed)'; echo '-- recent logins --'; lastlog 2>/dev/null | head -10; echo '-- health report complete --'"

verify:
  kind: none

receipt:
  redact: []
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
