---
id: safe-deployment
spec_version: 1
name: Safe Application Deployment
version: 1.0.0
category: ops
license: MIT
description: Deploy a build to a host with a backup, config test, and a post-deploy health check as the verified outcome.
author: navig
tags: [deploy, ops, ssh, rollback, health]
allowed-tools: Bash
target: local

inputs:
  - key: host
    type: string
    label: Target deployment host (a configured navig host)
    required: true
    default: production
  - key: app_path
    type: string
    label: Application path on the remote host
    required: true
    default: /var/www/app
  - key: build_dir
    type: path
    label: Local directory of build artifacts to upload
    required: true
    default: ./build
  - key: service
    type: string
    label: Service to validate and restart (e.g. nginx)
    required: true
    default: nginx

steps:
  - id: set-host
    kind: command
    safety: safe
    capabilities: [exec:navig]
    network: required
    argv: [navig, host, use, "{{inputs.host}}"]

  - id: backup
    kind: command
    safety: destructive
    capabilities: [remote, exec:navig]
    network: required
    argv:
      - navig
      - run
      - "cp -r {{inputs.app_path}} {{inputs.app_path}}.backup.$(date +%Y%m%d_%H%M%S)"

  - id: upload
    kind: command
    safety: destructive
    capabilities: [remote, exec:navig]
    network: required
    argv: [navig, upload, "{{inputs.build_dir}}", "{{inputs.app_path}}/"]

  - id: permissions
    kind: command
    safety: destructive
    capabilities: [remote, exec:navig]
    network: required
    argv:
      - navig
      - run
      - "chown -R www-data:www-data {{inputs.app_path}}"

  - id: config-test
    kind: command
    safety: moderate
    capabilities: [exec:navig]
    network: required
    argv:
      - navig
      - run
      - "{{inputs.service}} -t"

  - id: restart
    kind: command
    safety: destructive
    capabilities: [service, exec:navig]
    network: required
    argv:
      - navig
      - run
      - "systemctl restart {{inputs.service}}"

verify:
  kind: command
  level: external-check
  argv: [navig, health]
  expect:
    exit_code: 0

receipt:
  redact: []
---
# Deploy an application safely to a remote host

Deploys a local build to `app_path` on a navig-configured `host`, then proves the
service came back healthy. The **post-deploy health check is the verified
outcome** — the receipt records `verified` only if the host reports healthy after
the restart.

## Steps

1. **set-host** — point navig at `host` (safe).
2. **backup** — snapshot the current deployment to `…backup.<timestamp>` on the
   server *(destructive — needs `--approve backup`)*.
3. **upload** — transfer `build_dir` to `app_path` *(destructive — `--approve upload`)*.
4. **permissions** — `chown` the deployed files *(destructive — `--approve permissions`)*.
5. **config-test** — `{{inputs.service}} -t` validates config **before** any
   restart; a bad config aborts the run here (nothing gets restarted).
6. **restart** — restart the service *(destructive — `--approve restart`)*.

Every step that mutates the server is individually gated: a blanket `--yes` is
**not** enough (`reliability over cleverness`).

## Apply

```
navig apply safe-deployment \
  --input host=production \
  --input app_path=/var/www/app \
  --input build_dir=./build \
  --input service=nginx \
  --approve backup --approve upload --approve permissions --approve restart
```

Preview the exact plan without touching anything:

```
navig apply safe-deployment --input build_dir=./build --dry-run
```

## Verify — and its limits

The machine verify runs `navig health` against the target host after the restart
(an **external check** of the live service). It confirms the host/service is
healthy post-deploy; it does not assert application-level correctness (your smoke
tests still own that).
