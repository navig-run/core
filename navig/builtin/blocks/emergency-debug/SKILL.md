---
name: Emergency Debugging
description: Rapidly diagnose a failing service and container on a remote host (status, logs, ports, resources, recent errors).
allowed-tools: Bash
version: 1.0.0
generated-by: navig-block
block-id: emergency-debug
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

## Apply this outcome

This is a NAVIG **block**. Run it end-to-end with:

```
navig apply emergency-debug --input host=<string> --input service=<string> --input container=<string> --input log_lines=<int>
```

### Inputs

- `host` (string, required) — Host to debug (a configured navig host)
- `service` (string, required) — systemd service to inspect
- `container` (string, required) — Docker container to inspect
- `log_lines` (int, required) — How many recent log lines to pull

