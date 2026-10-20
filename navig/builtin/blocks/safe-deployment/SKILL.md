---
name: Safe Application Deployment
description: Deploy a build to a host with a backup, config test, and a post-deploy health check as the verified outcome.
allowed-tools: Bash
version: 1.0.0
generated-by: navig-block
block-id: safe-deployment
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

## Apply this outcome

This is a NAVIG **block**. Run it end-to-end with:

```
navig apply safe-deployment --input host=<string> --input app_path=<string> --input build_dir=<path> --input service=<string>
```

### Inputs

- `host` (string, required) — Target deployment host (a configured navig host)
- `app_path` (string, required) — Application path on the remote host
- `build_dir` (path, required) — Local directory of build artifacts to upload
- `service` (string, required) — Service to validate and restart (e.g. nginx)

