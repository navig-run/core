---
id: emergency-debug
spec_version: 1
name: Emergency Debugging
version: 1.0.0
category: ops
license: MIT
description: Rapidly diagnose a failing service and container on a remote host (status, logs, ports, resources, recent errors).
author: navig
tags: [debug, incident, diagnostics, ssh, ops, read-only]
allowed-tools: Bash
target: local

inputs:
  - key: host
    type: string
    label: Host to debug (a configured navig host)
    required: true
    default: production
  - key: service
    type: string
    label: systemd service to inspect
    required: true
    default: nginx
  - key: container
    type: string
    label: Docker container to inspect
    required: true
    default: app-container
  - key: log_lines
    type: int
    label: How many recent log lines to pull
    required: true
    default: 100

steps:
  - id: set-host
    kind: command
    safety: safe
    capabilities: [exec:navig]
    network: required
    argv: [navig, host, use, "{{inputs.host}}"]

  - id: diagnose
    kind: command
    safety: safe
    capabilities: [exec:navig]
    network: required
    argv:
      - navig
      - run
      - "echo '-- resources --'; uptime; free -h; df -h; echo '-- service status --'; systemctl status {{inputs.service}} --no-pager 2>&1 | head -30; echo '-- service logs --'; journalctl -u {{inputs.service}} -n {{inputs.log_lines}} --no-pager 2>/dev/null | tail -{{inputs.log_lines}}; echo '-- listening ports --'; ss -tlnp 2>/dev/null | head -20; echo '-- containers --'; docker ps -a 2>/dev/null || echo '(docker unavailable)'; echo '-- container logs --'; docker logs {{inputs.container}} --tail {{inputs.log_lines}} 2>/dev/null || echo '(no such container)'; echo '-- recent errors --'; journalctl -p err --since '1 hour ago' --no-pager 2>/dev/null | tail -50; echo '-- debug complete --'"

verify:
  kind: none

receipt:
  redact: []
---
# Rapidly triage a failing service

One-shot incident diagnostic: system resources, `systemctl status` and logs for
`service`, listening ports, container state and logs for `container`, and the
last hour of system-level errors.

## Honest scope

A **diagnostic**, not a verifiable outcome (`verification: none`) — you read the
output to find the fault. All probes run in a single remote shell chained with
`;` and guarded (`2>/dev/null || echo …`), so a missing service or container
never aborts the triage — the resilient equivalent of the old workflow's
`continue_on_error` + `skip_on_error`.

## Apply

```
navig apply emergency-debug \
  --input host=production \
  --input service=nginx \
  --input container=app-container \
  --input log_lines=100
```

Read-only — no `--approve` needed.
