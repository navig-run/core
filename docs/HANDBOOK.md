# NAVIG Developer Handbook

> Primary reference for navig-core contributors. Updated incrementally — no walls of text.

---

## Tool Architecture

### Two registries, one bridge

| Registry | Location | Interface |
|----------|----------|-----------|
| **Registry A** — async-native | `navig.tools.registry` | `BaseTool.run(args, on_status) → ToolResult` |
| **Registry B** — sync/async flexible | `navig.tools.router` | `ToolHandler(**params) → Any` + `ToolMeta` |

Use `navig.tools.bridge.bridge_all(base_reg, router_reg)` to register all `BaseTool` objects into the ToolRouter without modifying either registry.

### ToolRouter

```python
from navig.tools.router import get_tool_router
from navig.tools.schemas import ToolCallAction

router = get_tool_router()                           # standard mode
router = get_tool_router({"safety_mode": "strict"}) # custom policy (fresh instance)

result = router.execute(action)                      # sync
result = await router.async_execute(action)          # async (preferred)
```

**Safety modes:** `permissive` | `standard` (default) | `strict`

| Mode | DANGEROUS tools | MODERATE tools |
|------|-----------------|----------------|
| permissive | destructive patterns blocked | allowed |
| standard | destructive patterns blocked | destructive patterns blocked |
| strict | ALL blocked | risky + destructive blocked |

### Built-in tool packs

| Pack module | Domain | Key tools |
|-------------|--------|-----------|
| `web_pack` | WEB | `web_search`, `web_fetch` |
| `image_pack` | IMAGE | `image_generate` |
| `code_pack` | CODE | `code_sandbox` |
| `system_pack` | SYSTEM | `system_info`, `file_read`, `file_write` |
| `data_pack` | DATA | `data_extract`, `csv_parse` |
| `api_pack` | GENERAL | `api_call` |
| **`exec_pack`** | SYSTEM | **`bash_exec`** (DANGEROUS) |

### bash_exec

Shell command execution with hard output cap (50,000 chars), async subprocess, configurable timeout, and env injection.

```python
result = await router.async_execute(ToolCallAction(
    tool="bash_exec",
    parameters={
        "command": "ls -la /tmp",
        "cwd": "/tmp",            # optional
        "timeout_seconds": 30,    # default: 60
        "env_extra": {"FOO": "1"} # optional
    },
))
# result.output: {"stdout": "...", "stderr": "...", "returncode": 0, "timed_out": False, "truncated": False}
```

**Blocked in `strict` safety mode.** In `standard` mode, destructive patterns (e.g. `rm -rf`) trigger a DENIED response.

---

## Hook System

Observable pub/sub for ToolRouter execution lifecycle.

```python
from navig.tools.hooks import get_hook_registry, ToolEvent

hooks = get_hook_registry()

@hooks.on(ToolEvent.AFTER_EXECUTE)
def audit(ev):
    print(f"{ev.tool} done in {ev.elapsed_ms:.1f}ms → {ev.status}")

@hooks.on(ToolEvent.ERROR)
def alert(ev):
    logger.error("Tool %s failed: %s", ev.tool, ev.error)
```

**Events:** `BEFORE_EXECUTE` · `AFTER_EXECUTE` · `DENIED` · `ERROR` · `NOT_FOUND`

Hook callbacks are **sync-only** and **never raise** — exceptions are caught and logged.

Reset in tests: `from navig.tools.hooks import reset_hook_registry; reset_hook_registry()`

---

## Agent ↔ ToolRouter Wiring

`TaskExecutor._execute_step()` dispatches 12 hardcoded action types first (e.g. `wait`, `command`, `auto.*`), then **falls through to ToolRouter** for any registered tool name.

```python
# An LLM can now plan steps like:
{"action": "web_search", "params": {"query": "navig python"}}
{"action": "bash_exec",  "params": {"command": "git status"}}
{"action": "system_info", "params": {}}
```

`MultiStepAction` (a chain of `ToolCallAction`) is executed via:

```python
result = await executor.execute_multi_step_action(multi_step_action)
```

---

## Skill Eligibility

Filter skills before injecting them into the LLM context:

```python
from navig.skills.eligibility import SkillEligibilityContext, filter_skills
from navig.skills.loader import load_all_skills

all_skills = {s.id: s for s in load_all_skills()}
ctx = SkillEligibilityContext.default()       # current OS, safety_max=elevated
ctx = SkillEligibilityContext.strict()        # safe only, user_invocable only
ctx = SkillEligibilityContext.permissive()    # no restrictions

active_ids = filter_skills(list(all_skills), all_skills, ctx)
```

Eligibility checks (in order): platform → safety ceiling → user_invocable gate → required_tags → excluded_tags.

---

## Onboarding (`navig init`)

The onboarding flow is implemented in `navig/onboarding/` and driven by `OnboardingEngine`.

### CLI

```bash
navig init
navig init --resume
navig init --step matrix
navig init --reset
navig init --dry-run
```

### Step order

1. `workspace-init`
2. `workspace-templates`
3. `config-file`
4. `configure-ssh`
5. `verify-network`
6. `core-navig`
7. `ai-provider`
8. `vault-init`
9. `first-host`
10. `matrix`
11. `telegram-bot`
12. `email`
13. `social-networks`
14. `runtime-secrets`
15. `skills-activation`
16. `review`

### Persistence rules

- Non-secret values are written to `~/.navig/config.yaml`
- Secrets are written to `~/.navig/vault/vault.db`
- Progress is persisted after each step to `~/.navig/onboarding.json`
- Onboarding events are appended to `~/.navig/navig.onboarding.log`

### Path contract (cross-platform)

NAVIG uses `navig/platform/paths.py` as the only path-definition layer.

- `config_dir()` → persistent user config root
  - Windows: `%USERPROFILE%\.navig`
  - Linux/macOS: `~/.navig`
- `data_dir()` → persistent app databases and machine-readable state under the config root
- `cache_dir()` → OS-native ephemeral cache
  - Windows: `%LOCALAPPDATA%\navig\cache`
  - Linux: `${XDG_CACHE_HOME:-~/.cache}/navig`
  - macOS: `~/Library/Caches/navig`
- `local_state_dir()` → OS-native runtime state
  - Windows: `%LOCALAPPDATA%\navig\state`
  - Linux: `${XDG_STATE_HOME:-~/.local/state}/navig`
  - macOS: `~/Library/Application Support/NAVIG`
- `local_log_dir()` → OS-native logs
  - Windows: `%LOCALAPPDATA%\navig\logs`
  - Linux: `${XDG_STATE_HOME:-~/.local/state}/navig/logs`
  - macOS: `~/Library/Logs/NAVIG`
- `user_content_dir()` → user-facing generated content
  - Windows/macOS: `~/Documents/NAVIG`
  - Linux: `~/NAVIG`

Legacy `NAVIG_HOME` is treated as a deprecated alias for `NAVIG_CONFIG_DIR`.

### Actual end-to-end init flow

`navig init` now performs the filesystem bootstrap in this order:

1. Resolve all canonical paths through `navig/platform/paths.py`
2. Create required config/data/cache/state/log/content directories with `ensure_dirs()`
3. Open the platform-equivalent `local_log_dir()/init.log` for best-effort init logging
4. Detect legacy `~/Documents/.navig` and migrate its contents into the canonical config root
   - identical files are deduplicated
   - conflicting files abort migration and preserve the legacy source directory
5. Continue normal onboarding:
   - create or load `genesis.json`
   - render first-run or resume UI
   - execute onboarding steps through `OnboardingEngine`
6. Persist onboarding state and print the completion summary

Normal usage after init:

- edit settings in `config.yaml`
- secrets remain in `vault/vault.db`
- runtime caches/logs live under the OS-native local state and log directories
- user-generated exports and images live under `user_content_dir()`

### Integration storage

| Integration | Config keys | Vault labels |
|------------|-------------|--------------|
| Core NAVIG | `core.default_language`, `core.timezone`, `core.data_storage_path`, `core.log_level` | — |
| Matrix | `matrix.homeserver_url`, `matrix.default_room_id` | `matrix/access_token` |
| Telegram | `telegram.chat_id`, `telegram.mode` | `telegram/bot_token` |
| Email | `email.smtp_host`, `email.smtp_port`, `email.smtp_user`, `email.sender_name`, `email.sender_email`, optional IMAP keys | `email/smtp_password` |
| Social / Twitter-X | `social.twitter.status` | `social/twitter_api_key`, `social/twitter_api_secret` |
| Social / LinkedIn | `social.linkedin.status` | `social/linkedin_access_token` |
| Social / Mastodon | `social.mastodon.status`, `social.mastodon.instance_url` | `social/mastodon_access_token` |
| Runtime API keys | runtime-specific config remains in providers; onboarding writes secrets only | provider labels such as `openai/api_key`, `anthropic/api_key`, `spotify/client_secret`, etc. |

### Validation behavior

- Matrix → `/_matrix/client/v3/account/whoami`
- Telegram → `https://api.telegram.org/bot<TOKEN>/getMe`
- Email → SMTP EHLO handshake only
- Twitter/X → OAuth app-token exchange
- LinkedIn → `GET /v2/me`
- Mastodon → `GET /api/v1/accounts/verify_credentials`

Timeouts should surface the standard recovery message instead of aborting the flow:

> `Could not reach [service]. Check your internet connection or skip and configure later.`

---

## Network & Runtime Configuration Reference

Regular users configure NAVIG exclusively through `~/.navig/config.yaml` (written by `navig init`).
The `.env` file and environment variables are for **developers, Docker deployments, and CI only**.

**User config files (in priority order):**

| File | Who touches it | Purpose |
|------|---------------|---------|
| `~/.navig/config.yaml` | Users | All non-secret settings — copy `config/config.example.yaml` as a starting point |
| `~/.navig/vault/vault.db` | vault commands | Secrets (API keys, tokens, passwords) |
| `.navig/config.yaml` | Project-local | Per-project overrides — merged on top of global |
| `config/defaults.yaml` | Nobody | Factory defaults — defines every key and its default |
| `.env` / env vars | Devs / CI / Docker | Override any config value without editing YAML |

**Quick start for users:**
```bash
navig init                          # guided — recommended for first-time setup
navig config show                   # see all current values
navig config set gateway.port 9000  # change a single value
```

### Ports & endpoints

All port/host values are read from config at runtime — never hardcoded.
Override priority: **env var** → `~/.navig/config.yaml` → `config/defaults.yaml`.

| Component | Config key | Env var | Default | Notes |
|-----------|-----------|---------|---------|-------|
| Gateway server | `gateway.port` | — | `8789` | All local CLIs (`navig cron`, `navig flux`, `navig status`) |
| Forge MCP bridge | — | — | `42070` | navig-bridge VS Code extension |
| Browser daemon | `daemon.browser_port` | — | `7421` | Go host daemon (`navig webhook`, browser integrations) |
| Mesh multicast group | — | `NAVIG_MESH_MULTICAST_GROUP` | `224.0.0.251` | Change in restricted enterprise networks |
| Mesh multicast port | — | `NAVIG_MESH_MULTICAST_PORT` | `5354` | RFC 4607 LAN multicast range |
| OAuth callback | `oauth.redirect_uri` | — | `http://127.0.0.1:1455/auth/callback` | — |

### Changing the gateway port

```yaml
# ~/.navig/config.yaml
gateway:
  port: 9000
```

Every command module (`cron`, `flux`, `status`, `dashboard`, `telegram_mesh`) reads this key at
call time via `_gw_base()`. The server and CLI must share the same port.

### Mini agent connection

```yaml
# ~/.navig/config.yaml  — or: navig config set mini.url http://...
mini:
  url: "http://your-nas:9191"
  ssh_host: "your-nas-hostname"
  # secret stored in vault: navig vault set mini.secret <value>
```

Env overrides: `MINI_AGENT_URL`, `MINI_SSH_HOST`, `MINI_AGENT_SECRET`.

### TTS voice customisation

```yaml
# No yaml key — use env vars:
# NAVIG_TTS_MODEL=tts-1-hd   (upgrade to HD)
# NAVIG_TTS_VOICE=onyx        (any OpenAI voice: alloy echo fable onyx nova shimmer)
```

### Air-gapped / offline environments

| Concern | Solution |
|---------|---------|
| Latency probe fails | `NAVIG_LATENCY_PROBE_HOST=10.0.0.1` (any reachable LAN host) |
| Mesh multicast blocked | `NAVIG_MESH_MULTICAST_GROUP=239.255.0.1` + `NAVIG_MESH_MULTICAST_PORT=5354` |

---

## Testing

```bash
# Full suite
py -3 -m pytest tests/ --no-cov -q

# New infrastructure only
py -3 -m pytest tests/test_hooks.py tests/test_exec_pack.py tests/test_agent_tool_dispatch.py --no-cov -q

# Verify no stray lab references
py -3 -c "import subprocess; r = subprocess.run(['grep', '-r', '.lab/', 'navig/', 'tests/'], capture_output=True); print('clean' if not r.stdout else r.stdout.decode())"
```

---

## Adding a New Tool Domain

1. Create `navig/tools/domains/my_pack.py` with a `register_tools(registry)` function.
2. Add `"navig.tools.domains.my_pack"` to `_load_builtin_packs()` in `navig/tools/router.py`.
3. Each tool is a `ToolMeta` + handler registered via `registry.register(meta, handler=fn)`.
4. Add tests in `tests/test_my_pack.py`.

---

## Adding a Hook Observer

```python
# In any module that should observe tool execution:
from navig.tools.hooks import get_hook_registry, ToolEvent

def _setup_hooks():
    hooks = get_hook_registry()
    hooks.register(ToolEvent.AFTER_EXECUTE, _on_tool_done)

def _on_tool_done(ev):
    metrics.increment("tool.calls", tags={"tool": ev.tool, "status": ev.status})
```

Call `_setup_hooks()` during your module's initialization. The registry survives for the process lifetime.


---

## `navig deploy` — Remote Deploy System

Push files to a remote host, run apply scripts, restart a service, verify health, and roll back on failure — all via NAVIG's existing SSH transport.

### Quick Start

```bash
# Initialise a deploy config for the current project
navig deploy init

# Dry-run to preview what would happen
navig deploy run --dry-run

# Deploy to the active host/app context
navig deploy run

# Deploy to a specific host/app
navig deploy run --host prod --app myapi
```

### All Commands

| Command | Purpose |
|---------|---------|
| `navig deploy init [--force]` | Create `.navig/deploy.yaml` scaffold |
| `navig deploy check [--host] [--app]` | Verify config + SSH reachability |
| `navig deploy run [--host] [--app] [--dry-run] [--skip-backup] [--no-auto-rollback] [--verbose]` | Full deploy lifecycle |
| `navig deploy rollback [--host] [--app] [--dry-run]` | Restore last snapshot |
| `navig deploy status [--host] [--app]` | Show cached last-deploy state |
| `navig deploy history [--limit] [--host] [--app] [--json]` | View deploy log |

### Deploy Lifecycle

```
pre_check → backup → push → apply → restart → health → cleanup
```

- **pre_check** — Tests SSH reachability and records disk usage
- **backup** — `cp -r <target> <backup_dir>/<timestamp>` on remote (fast, same-disk)
- **push** — rsync via subprocess ssh; excludes `.env`, `.git/`, `node_modules/`, etc.
- **apply** — Runs zero or more shell commands on the remote (migrations, cache clear, etc.)
- **restart** — Fires the configured adapter (`systemd`, `docker-compose`, `pm2`, `command`)
- **health** — Polls `curl` on the remote host until `expected_status` is returned
- **cleanup** — Prunes old snapshots keeping only the last *n*

If **backup** has run and any subsequent phase fails, the engine automatically calls **rollback** (unless `--no-auto-rollback` was passed).

### `.navig/deploy.yaml` Reference

**Minimal:**
```yaml
push:
  source: ./dist/
  target: /var/www/myapp/
```

**Full example:**
```yaml
host: prod
app: myapp

push:
  source: ./dist/
  target: /var/www/myapp/
  excludes:
    - "*.map"
    - "tests/"

apply:
  commands:
    - "cd /var/www/myapp && php artisan migrate --force"
    - "cd /var/www/myapp && php artisan cache:clear"

restart:
  adapter: systemd          # systemd | docker-compose | pm2 | command
  service: myapp.service

health_check:
  url: http://localhost/health
  expected_status: 200
  retries: 5
  interval_seconds: 3
  timeout_seconds: 10

backup:
  enabled: true
  remote_path: /var/backups
  keep_last: 5
```

### Adapters

| Adapter | `service` field | Remote command |
|---------|----------------|----------------|
| `systemd` | unit name e.g. `myapp.service` | `systemctl restart <service>` |
| `docker-compose` | compose file e.g. `docker-compose.yml` | `docker compose -f <file> up -d` |
| `pm2` | pm2 app name | `pm2 reload <service>` |
| `command` | arbitrary shell string | executes as-is |

### Global Defaults (`~/.navig/config.yaml`)

```yaml
deploy:
  default_backup: true
  default_health_retries: 5
  default_health_interval_seconds: 5
  default_health_timeout_seconds: 30
  restart_settle_seconds: 2
  snapshot_keep_last: 5
  history_keep: 50
```

### Rollback

```bash
navig deploy rollback                 # restore last snapshot
navig deploy rollback --dry-run       # preview only
```

Snapshots are `cp -r` copies on the remote (same-disk, fast). Metadata cached at `~/.navig/cache/last_deploy_<app>.json`.

### Architecture Notes

- All SSH calls go through `navig.remote.RemoteOperations.execute_command()`
- `rsync` is the only subprocess call (push phase)
- `DeployEngine` accepts `on_progress: ProgressCallback` for custom rendering
- History at `~/.navig/cache/deploy_history.jsonl` (JSON-lines, trimmed to `history_keep`)
- Tests: `tests/test_deploy_config.py`, `tests/test_deploy_adapters.py`, `tests/test_deploy_lifecycle.py`

### Adding a New Adapter

1. Add class in `navig/deploy/adapters.py` implementing `restart_commands()` and `execute(dry_run) -> PhaseResult`
2. Register in `build_adapter()` factory in the same file
3. Add tests in `tests/test_deploy_adapters.py`

---

## navig update

Upgrades the installed NAVIG version on local and remote nodes.
**Distinct from `navig deploy`** — deploy pushes project files; update upgrades the NAVIG engine itself.

### Commands

| Command | Description |
|---------|-------------|
| `navig update` | Apply local update (default; backward compat) |
| `navig update check` | Show current vs. latest version (no changes) |
| `navig update run` | Apply updates to one or more nodes |
| `navig update rollback <version>` | Roll back to a specific version |
| `navig update status` | Show installed version and source config |
| `navig update history` | Show recent update history |
| `navig update nodes` | List all known nodes |
| `navig update source` | Show configured update source |

### Targeting nodes

```bash
navig update run                     # local only (default)
navig update run --host web-prod     # single SSH host
navig update run --group staging     # named host group
navig update run --all               # local + all configured hosts
navig update run --dry-run           # preview only
navig update run --force             # update even if already on latest
```

### Host groups

Define groups in `~/.navig/config.yaml`:

```yaml
groups:
  staging: [web-staging, db-staging]
  prod:    [web-prod-1, web-prod-2, db-primary]
```

Then: `navig update run --group prod`

### Update sources

Configure in `~/.navig/config.yaml` under `update.source`:

```yaml
update:
  channel: stable          # stable | beta | nightly
  source:
    type: pypi             # pypi | github | git-repo | artifact-url | local-file
    package: navig
```

| Source type | Required keys | Description |
|-------------|---------------|-------------|
| `pypi` | `package` | Default — check PyPI for latest release |
| `github` | `repo` | GitHub releases API (`org/repo`) |
| `git-repo` | `path` or `remote` | Latest git tag in a repo |
| `artifact-url` | `url` | Fetch version from a URL (JSON or plain text) |
| `local-file` | `path` | Read version string from a local file |

### Rollback

```bash
navig update rollback 2.4.15         # restore specific version locally
navig update rollback 2.4.15 --host web-prod  # rollback remote node
```

Rollback uses `pip install navig==<version>` (or `uv pip install`). No filesystem snapshot required.

### Architecture notes

- Package: `navig/update/` — independent of `navig/deploy/`
- `UpdateEngine` orchestrates: discover → compare → install → verify → commit | rollback
- SSH install delegates to `navig update run --force` on the remote, falling back to `pip install --upgrade navig`
- History: `~/.navig/cache/update_history.jsonl` (JSONL, trimmed to `history_keep`)
- Tests: `tests/test_update_sources.py`, `tests/test_update_checker.py`, `tests/test_update_targets.py`, `tests/test_update_lifecycle.py`

### Adding a new source type

1. Add class in `navig/update/sources.py` implementing `label: str` property and `latest_version() -> str`
2. Register in `build_source()` factory in the same file
3. Add tests in `tests/test_update_sources.py`

---

## Deploy & Update — Release Roadmap

> Canonical tracking of what shipped, what's next, and what's future.
> **Last updated:** 2026-03-17

---

### MVP1 — Shipped ✅

**`navig deploy`**

| Feature | Module | Status |
|---------|--------|--------|
| `deploy init` — scaffold `.navig/deploy.yaml` | `navig/deploy/config.py` | ✅ |
| `deploy check` — SSH reachability + config validation | `navig/deploy/lifecycle.py` | ✅ |
| `deploy run` with full lifecycle (pre_check → backup → push → apply → restart → health → cleanup) | `navig/deploy/lifecycle.py` | ✅ |
| `deploy rollback` — restore remote snapshot | `navig/deploy/lifecycle.py` | ✅ |
| `deploy status` — show cached last-deploy state | `navig/commands/deploy.py` | ✅ |
| `deploy history` — JSONL history with filters | `navig/deploy/history.py` | ✅ |
| Adapters: `systemd`, `docker-compose`, `pm2`, `command` | `navig/deploy/adapters.py` | ✅ |
| rsync push with configurable excludes | `navig/deploy/push.py` | ✅ |
| Auto-rollback on failure | `navig/deploy/lifecycle.py` | ✅ |
| Global defaults in `~/.navig/config.yaml` | `config/defaults.yaml` | ✅ |
| 57 passing tests | `tests/test_deploy_*` | ✅ |

**`navig update`**

| Feature | Module | Status |
|---------|--------|--------|
| `update check` — local + SSH version check | `navig/update/checker.py` | ✅ |
| `update run` — install via PyPI/git, verify, rollback on fail | `navig/update/lifecycle.py` | ✅ |
| `update rollback <version>` — pip-pin to old version | `navig/update/lifecycle.py` | ✅ |
| `update status` — show installed version + source | `navig/commands/update.py` | ✅ |
| `update history` — per-node history | `navig/update/history.py` | ✅ |
| `update nodes` — list local + SSH nodes | `navig/commands/update.py` | ✅ |
| `update source` — show configured source | `navig/commands/update.py` | ✅ |
| Sources: PyPI, GitHub, git-repo, artifact-URL, local-file | `navig/update/sources.py` | ✅ |
| Node targeting: `--host`, `--group`, `--all` | `navig/update/targets.py` | ✅ |
| Named host groups in `~/.navig/config.yaml` | `navig/config.py` | ✅ |
| Channels: stable / beta / nightly | `navig/update/sources.py` | ✅ |
| Backward-compat shim for `navig update --check` | `navig/commands/update.py` | ✅ |
| 53 passing tests | `tests/test_update_*` | ✅ |

---

### P2 — Multi-host & Environments (MVP2)

> Target milestone: next sprint. All items are `navig deploy` scope unless noted.

| # | Feature | CLI surface | Design notes |
|---|---------|-------------|-------------|
| 2.1 | **Environments** (`--env staging/production`) with per-env config overrides | `navig deploy run --env staging` | Overlay `.navig/deploy.staging.yaml` on top of base `deploy.yaml`. Env file only needs keys that differ. |
| 2.2 | **Multi-host parallel deploy** | `navig deploy run --host prod --host staging` | Accept `--host` multiple times. Fan-out via `ThreadPoolExecutor`. Results table per host. |
| 2.3 | **Blue-green swap strategy** | `strategy: blue-green` in `deploy.yaml` | Symlink or nginx upstream swap. Two slots (`blue`/`green`). Cutover only after health passes. |
| 2.4 | **Remote pre-flight hooks** | `pre_flight:` block in `deploy.yaml` | Run arbitrary commands on remote before backup. Abort deploy if any exit non-zero. Built-in checks: disk space, DB migration safety. |
| 2.5 | **NAVIG mesh-aware deploy** | `navig deploy run --mesh` | Push to all online peer nodes discovered via `navig/mesh/registry.py`. Respects `NodeRecord.role`. |
| 2.6 | **Approval gate** (operational-factory integration) | `approval: required` in `deploy.yaml` | Enqueue deploy as a proposed action in `deploy/operational-factory/`. Proceed only after dashboard approval. |
| 2.7 | **Slack/Telegram notifications** | `notify:` block in `deploy.yaml` | Post deploy start, success, rollback events. Reuse existing `navig/gateway/` channels. |
| 2.8 | **Git tag on successful deploy** | `git_tag: v{version}-{env}-{timestamp}` | Run `git tag` + optional `git push --tags` after cleanup phase. Configurable format string. |

**Implementation order (recommended):** 2.1 → 2.4 → 2.7 → 2.2 → 2.8 → 2.6 → 2.3 → 2.5

**Key files to touch for P2:**
- `navig/deploy/config.py` — env overlay loader, multi-host target list
- `navig/deploy/lifecycle.py` — pre-flight phase, blue-green cutover, tagging
- `navig/deploy/notify.py` *(new)* — channel-agnostic notifier
- `navig/cli/__init__.py` or `navig/commands/deploy.py` — `--env`, repeated `--host`, `--mesh` flags
- `config/defaults.yaml` — `deploy.notify:`, `deploy.approval:` defaults

---

### P3 — Advanced (Future)

> No timeline set. Design before implementing.

| # | Feature | Notes |
|---|---------|-------|
| 3.1 | **Artifact-based deploy** (tarballs, S3, container registry) | New push adapter. `push.source` can be a URL or `s3://` prefix. Pull-model instead of rsync push. |
| 3.2 | **Zero-downtime rolling restarts** | Health-check-gated rolling restart across multiple instances. `strategy: rolling` + `max_unavailable`. |
| 3.3 | **Database migration safety checks** | Pre-flight hook integrations: check pending migrations, warn on destructive DDL, require `--migrate` explicit flag. |
| 3.4 | **Deploy slots / canary traffic splitting** | `strategy: canary`, `canary_weight: 10`. Requires nginx or Traefik upstream config management. |
| 3.5 | **`navig deploy plan`** (Terraform-style diff output) | Dry-run with structured diff: files changed, commands to run, service impact. Machine-readable `--json`. |

---

### Capability Map Quick View

```
navig deploy
  MVP1 ✅  init · check · run · rollback · status · history
           adapters: systemd · docker-compose · pm2 · command
           rsync push · auto-rollback · snapshot backup
  P2   ◻   --env · multi-host parallel · blue-green · pre-flight
           mesh deploy · approval gate · notifications · git-tag
  P3   ◻   artifacts · rolling · db-safety · canary · plan

navig update
  MVP1 ✅  check · run · rollback · status · history · nodes · source
           sources: pypi · github · git-repo · artifact-url · local-file
           channels: stable · beta · nightly
           node groups · SSH targeting · auto-rollback
  P2   ◻   (inherits mesh-aware targeting from deploy P2.5)
           Parallel SSH updates (concurrency > 1)
           Approval gate integration
  P3   ◻   OCI/container-based NAVIG packaging
           Signed release verification (GPG / sigstore)
```

---

## Capability Map — MVP1 vs Deferred

> **Last verified:** 2026-03-17 — 2648 tests pass.

NAVIG ships with a deliberate capability tier system. This section is the authoritative reference for what is active, what exists but is gated, and what is deferred to a future release.

The machine-readable source is `navig/core/capability_registry.py`.

---

### The MVP Promise

> **NAVIG MVP is a reliable AI operator that runs locally, persists context, acts through tools, and is controlled through CLI + Telegram for real infrastructure and workflow tasks.**

If a capability does not strengthen that sentence right now, it is not MVP.

---

### Tier Definitions

| Tier | Meaning | Default state |
|---|---|---|
| **CORE** | Always loaded. Required for the MVP promise. | Always on |
| **OPTIONAL** | Exists and works. Gated by a config key. Off by default. | Off — flip config key to enable |
| **LABS** | Exists and preserved. Not in runtime. No hot-path imports. | Off — not wired to startup |

---

### MVP1 — What Ships Active

These capabilities are fully operational in every default NAVIG install.

| Capability | Module | CLI |
|---|---|---|
| **Daemon / Kernel** | `navig.daemon` | `navig service` |
| **Telegram Gateway** | `navig.gateway.channels.telegram` | `navig telegram` / `navig tg` |
| **Vault** | `navig.vault` | `navig vault` / `navig cred` |
| **Memory** | `navig.memory` | `navig memory` |
| **Tools Registry** | `navig.tools` | `navig tools` |
| **Agent Conversation Loop** | `navig.agent` | `navig agent` |
| **Storage Engine** | `navig.storage` | *(internal)* |
| **Infrastructure Commands** | `navig.commands` | `navig host` · `navig run` · `navig db` · `navig docker` · `navig file` · `navig web` · `navig backup` |
| **Onboarding / Init** | `navig.onboarding` | `navig init` |

These are fully active, fully tested, and protected. Do not degrade them.

---

### OPTIONAL — Exists, Off By Default

These subsystems are **fully implemented and preserved** in the codebase. They are off because they add operational surface before the core promise is excellent, require optional dependencies, or carry support complexity.

**To activate any of them:** set the config key in `~/.navig/config.yaml`, then restart the daemon.

#### Matrix Channel

| | |
|---|---|
| **What it does** | Control NAVIG through Matrix/Element clients. Supports E2EE via `matrix-nio`. |
| **Files** | `navig/gateway/channels/matrix.py` · `navig/comms/matrix.py` · `navig/commands/matrix.py` |
| **CLI** | `navig matrix` / `navig mx` |
| **Config key** | `matrix.enabled: true` |
| **Also requires** | `pip install navig[matrix]` |
| **E2EE** | Separate gate: `matrix.e2ee: true` (enable only after base channel is stable) |
| **Why deferred** | "Almost working" E2EE is worse than no E2EE. Two control surfaces before one is excellent adds QA surface and user confusion. |

#### Mesh / Flux (LAN Node Coordination)

| | |
|---|---|
| **What it does** | P2P LAN mesh: multiple NAVIG nodes elect a leader, sync state, route tasks across machines. UDP multicast discovery + REST election. |
| **Files** | `navig/mesh/` — `registry.py` · `discovery.py` · `election.py` · `router.py` · `collective.py` · `sync_manager.py` · `auth.py` |
| **CLI** | `navig mesh` · `navig flux` / `navig fx` |
| **Config key** | `mesh.enabled: true` |
| **Why deferred** | A mesh is an entire product layer (distributed state, discovery, trust, failure handling, version skew). NAVIG must win as a single node first. |

#### Deck Web UI

| | |
|---|---|
| **What it does** | Browser-accessible dashboard: vault management, LLM model selector, auth, static assets. Built into the gateway server. |
| **Files** | `navig/gateway/deck/` — `auth.py` · `routes/core.py` · `routes/vault.py` · `routes/models.py` |
| **CLI** | `navig deck` |
| **Config key** | `gateway.deck_enabled: true` |
| **Why deferred** | Second control surface before the first (Telegram/CLI) is excellent creates competing UX expectations and visual polish requirements. |

#### Voice (Full Stack)

| | |
|---|---|
| **What it does** | STT (Whisper/cloud), TTS (edge-tts/cloud), wake word detection, streaming real-time transcription. |
| **Files** | `navig/voice/` — `stt.py` · `tts.py` · `pipeline.py` · `wake_word.py` · `streaming_stt.py` · `session_manager.py` |
| **CLI** | `navig voice` |
| **Config key** | `voice.enabled: true` |
| **Also requires** | `pip install navig[voice]` |
| **MVP1 status** | Basic STT/TTS (`stt.py` + `tts.py`) already active via **try-import** in the Telegram channel — if `navig[voice]` is installed, voice replies work automatically. Wake word and streaming STT are gated. |
| **Why deferred** | Wake word adds driver conflicts, device permissions, ambient noise failures, and cross-platform edge cases. |

#### Blackbox / Flight Recorder

| | |
|---|---|
| **What it does** | Event timeline recorder for every tool call and agent decision. Crypto-sealed forensic export bundle for audit. |
| **Files** | `navig/blackbox/` — `recorder.py` · `timeline.py` · `export.py` · `seal.py` · `bundle.py` · `crash.py` |
| **CLI** | `navig blackbox` / `navig bb` |
| **Config key** | `blackbox.enabled: true` *(key exists; wiring is MVP2)* |
| **MVP1.5 path** | `recorder.py` + `export.py` (plain event log) can be activated before the seal layer. |
| **Why deferred** | Crypto-sealed forensics only matter when users depend on NAVIG for auditable workflows. Getting there first. |

#### SelfHeal (Diagnostics + Auto-PR)

| | |
|---|---|
| **What it does** | Scans codebase for issues, generates patches, creates git branches, submits PRs autonomously. |
| **Files** | `navig/selfheal/` — `scanner.py` · `patcher.py` · `git_manager.py` · `pr_builder.py` · `heal_pr_submitter.py` |
| **CLI** | `navig contribute` |
| **Config key** | `contribute.enabled: true` (scanner); `contribute.auto_pr: true` (PR flow — additional gate) |
| **MVP1.5 path** | `scanner.py` + `patcher.py` are safe for read-only diagnostics. Enable with `contribute.enabled: true`. |
| **Why deferred** | Auto-PR is impressive once and trust-destroying if it misfires. Scanner first, PR automation after trust is established. |

#### Proactive Engine (External Providers)

| | |
|---|---|
| **What it does** | Fires messages to users at the right moment without being asked. Providers: Google Calendar, ICS files, IMAP email, user state. |
| **Files** | `navig/agent/proactive/` — `engine.py` · `engagement.py` · `user_state.py` · `google_calendar.py` · `ics_calendar.py` · `imap_email.py` |
| **CLI** | `navig proactive` |
| **Config key** | `proactive.enabled: true` (external providers) |
| **MVP1 status** | `user_state.py` + `engagement.py` (the engagement tick) are **already active** via try-import guards in the Telegram channel. This gate only covers the external data providers. |
| **Why deferred** | External providers add OAuth complexity, credential surface, and failure modes. One good engagement loop first. |

#### Identity / Sigil

| | |
|---|---|
| **What it does** | Generates a unique visual sigil and identity entity for each NAVIG install. Display card via `navig whoami`. |
| **Files** | `navig/identity/` — `entity.py` · `genesis.py` · `models.py` · `renderer.py` · `sigil_store.py` |
| **CLI** | `navig whoami` · used in `navig init` |
| **Config key** | None — cosmetic, call-site only |
| **MVP1 status** | Already works. `navig whoami` renders your sigil card today. The subsystem is not in the runtime hot-path — no gate needed. |
| **Why minimal** | Brand/lore layer, not a runtime responsibility. Genesis step in `navig init` will be made skippable. |

---

### LABS — Exists, Not In Runtime

These are preserved in the codebase with zero startup cost. They are not loaded, not imported, not wired. Think of them as **conceptual seams** for future development.

#### Formations / Council (Multi-Agent Orchestration)

| | |
|---|---|
| **What it does** | Multiple NAVIG agents with different roles collaborate on a task via a deliberation loop. |
| **Files** | `navig/formations/` — `registry.py` · `loader.py` · `council.py` · `schema.py` · `types.py` |
| **CLI** | `navig formation` · `navig council` |
| **Startup cost** | Zero — not imported at startup |
| **When to activate** | NAVIG reliably completes multi-step infra tasks alone. Users ask for parallel delegation. |

#### Profiler / Perf Instrumentation

| | |
|---|---|
| **What it does** | Internal profiling subsystem for measuring agent decision latency and tool execution bottlenecks. |
| **Files** | `navig/perf/profiler.py` (empty `__init__.py`) |
| **Startup cost** | Zero — `__init__.py` is empty; nothing imported |
| **When to activate** | When `navig doctor` needs profiling data to diagnose a specific slow operation. |

#### genesis_lab (Visual Showcase)

| | |
|---|---|
| **What it does** | Particle-system animation of NAVIG's identity and capabilities. Demo / visual identity tool. |
| **Files** | `scripts/genesis_lab/` — standalone, never imported by `navig/` package |
| **Run it** | `cd scripts/genesis_lab && python main.py` |
| **Startup cost** | Zero |
| **When to activate** | Public launch; consider moving to a separate repo. |

---

### How to Enable an OPTIONAL Capability

```yaml
# ~/.navig/config.yaml  (or project .navig/config.yaml)

matrix:
  enabled: true            # flip this

mesh:
  enabled: true            # flip this

gateway:
  deck_enabled: true       # flip this

voice:
  enabled: true            # also: pip install navig[voice]

proactive:
  enabled: true            # activates external providers

contribute:
  enabled: true            # activates selfheal scanner
```

Then restart the daemon:

```bash
navig service restart
```

---

### Capability Registry (Machine-Readable)

The single source of truth for all tiers is `navig/core/capability_registry.py`.

```python
from navig.core.capability_registry import REGISTRY, CapabilityTier, is_enabled

# Check what tier a capability is
REGISTRY["mesh"].tier          # CapabilityTier.OPTIONAL
REGISTRY["formations"].tier    # CapabilityTier.LABS
REGISTRY["vault"].tier         # CapabilityTier.CORE

# Check if a capability is active given a config dict
is_enabled("mesh", config)     # False unless mesh.enabled = true
is_enabled("vault", config)    # Always True (CORE)
```

**Rule for adding new capabilities:**
1. Add an entry to `REGISTRY` in `capability_registry.py` with the correct tier
2. Add the config key default in `config/defaults.yaml` (`enabled: false`)
3. Wrap the import at the call site in a try/except guard
4. Document in `.navig/plans/MVP2_FEATURES.md` with thin-seam spec
5. Never remove files — quarantine by deregistering, not deleting

---

### What Was Deliberately NOT Removed

Every feature in the OPTIONAL and LABS tiers has its code fully intact. The quarantine is architectural (import guards + registry tier), not destructive. Reasons:

- **Total removal causes re-invention** in worse form later
- **Conceptual seams** preserve the interface contracts for clean reactivation
- **CLI commands remain registered** (lazy-loaded) so `navig mesh --help` still works when mesh is off — it just explains how to enable it
- **Optional deps** (`navig[voice]`, `navig[matrix]`) handle package-level gating cleanly


---

## Feature Concepts

This section explains the purpose, design concept, and activation path for every optional and upcoming capability in NAVIG. Use this as a reference when deciding what to build next or when onboarding contributors.

---

### Identity / Sigil

**What it does:**
Every NAVIG installation generates a unique visual identity called a *sigil* � a 9?9 symmetric glyph grid rendered in the terminal. The sigil is derived deterministically from a machine fingerprint (MAC address + hostname + username). Same machine always produces the same entity; different machines always produce different ones.

**The entity record includes:**
- An **archetype name** (e.g. WRAITH-4A7F, ORACLE-B2DE) sampled from archetypes like Leviathan, Chimera, Sentinel
- A **machine name** (e.g. VOIDPETREL, IRONSHARD) � a two-part compound
- A **palette** from NAVIG's oceanic colour system (abyssal, biolumen, void_pulse, nautilus, kraken_gold)
- The **9?9 sigil glyph grid** � depth-shaded using dense/mid/light tiers
- A **resonance score** (60�99) � cosmetic strength indicator
- A **subsystem boot order** � the sequence in which subsystems are assigned to this entity

**Why it exists:**
Two reasons. First: every AI operator needs a face. The sigil is that face � a stable, non-secret visual fingerprint that makes "your NAVIG" feel distinct. Second: the seed that generates the sigil is also used to derive a machine name for mesh routing (future use). The identity system is the foundation for per-node addressing.

**Where it runs:**
- 
avig init > sigil-genesis step (Phase 1 bootstrap, final step) � generated once on first init, redisplayed on re-init
- 
avig whoami > renders the full sigil card at any time

**Files:** 
avig/identity/entity.py � 
avig/identity/seed.py � 
avig/identity/renderer.py � 
avig/identity/sigil_store.py
**Stored at:** ~/.navig/entity.json
**Status: CORE � always active, no config gate needed**

---

### Matrix Channel

**What it does:**
Connects NAVIG to a Matrix homeserver as a second control channel alongside Telegram. Users can issue commands, receive responses, and (optionally) communicate with full end-to-end encryption via the [matrix-nio](https://github.com/poljar/matrix-nio) library.

**The concept:**
Telegram is a centralised service under a corporate entity. Matrix is a federated, open protocol. For users running NAVIG in high-security or self-hosted environments, Matrix provides a completely independent channel that doesn't route through Telegram's infrastructure.

E2EE (matrix.e2ee: true) adds the libolm cryptographic layer � device key exchange, Megolm session negotiation � making the channel fully end-to-end encrypted even if the homeserver is compromised.

**Activation:**
`yaml
# ~/.navig/config.yaml
matrix:
  enabled: true
  homeserver: "https://matrix.example.com"
  user_id: "@your-bot:example.com"
  access_token: "syt_..."
  room_id: "!yourroom:example.com"
  e2ee: false   # set true to add libolm encryption
`
`ash
pip install navig[matrix]
`

**Files:** 
avig/gateway/channels/matrix.py
**Optional dep group:** 
avig[matrix] > matrix-nio[e2ee]
**Status: OPTIONAL � ships disabled**

---

### Mesh / Flux

**What it does:**
Allows multiple NAVIG instances on a local network (LAN) to discover each other via UDP multicast and form a cooperative peer mesh. A running mesh can distribute a shutdown sequence (yield the leadership token before a node stops), forward commands to peer nodes, and � in future phases � share workloads across multiple NAVIG operators.

**The concept:**
NAVIG is designed to eventually run as a distributed formation, not just a single node. The mesh is the underlying communication fabric. Phase 1 (current) covers: peer discovery (UDP 224.0.0.251:5354), leadership election yield on shutdown, and proxy routing through authenticated mesh tokens. Phase 2 (Flux) adds actual workload delegation and formation-level consensus.

**Important:** The mesh election endpoint is a local HTTP call � it never goes outside your LAN. No WAN mesh until Phase 2 is explicitly confirmed.

**Activation:**
`yaml
# ~/.navig/config.yaml
mesh:
  enabled: true
  election_endpoint: "http://127.0.0.1:8090/mesh/election/yield"
`

**Files:** 
avig/mesh/ (fully isolated, never imported at startup when disabled)
**Status: OPTIONAL � ships disabled**

---

### Deck Web UI

**What it does:**
Embeds a web application into the Telegram bot as a Telegram WebApp button. When configured, the Deck button appears at the bottom of the chat (the hamburger menu area) and opens the NAVIG Deck browser UI � a real-time dashboard showing system status, active tasks, chat history, and quick actions.

**The concept:**
The Telegram interface is powerful for natural language, but has no persistent visual state. The Deck is the persistent UI layer � a browser app that talks to the same NAVIG backend via the gateway API. It's designed for the moments when a user wants to *see* the state of their formation, not just ask about it.

**Cloudflare / hosting note:**
The Deck URL must be served over HTTPS (Telegram WebApp requirement). Common approaches:
- Cloudflare Tunnel from your server running 
avig-dock (
avig-dock is the Next.js Deck app)
- Any HTTPS-capable reverse proxy in front of a local Next.js instance
- A hosted deploy

**Activation:**
The Deck button and /deck command appear in the Telegram bot *only* when 	elegram.deck_url is set. Users without a Deck deployment see a note in /help explaining how to enable it � the command does not appear in the command list.

`yaml
# ~/.navig/config.yaml
telegram:
  deck_url: "https://your-deck.example.com"
`

**Files:** 
avig/gateway/channels/telegram_commands.py > _get_deck_url(), _handle_deck()
**App repo:** 
avig-dock/ (Next.js)
**Status: OPTIONAL � ships hidden unless deck_url is configured**

---

### Voice (Wake Word + Streaming)

**What it does:**
Adds a voice I/O layer to NAVIG. Captures audio from a microphone, transcribes it (STT � Speech to Text), sends the transcript through the agent pipeline, and reads the response aloud (TTS � Text to Speech). An optional wake word ("NAVIG", configurable) lets users activate the listening loop hands-free.

**The concept:**
Voice makes NAVIG an ambient presence, not just a chat tool. You can issue commands while working, receive spoken briefings, or run a wake-word loop on a dedicated Raspberry Pi / home server. The voice pipeline is designed to be modular: you can use any STT provider (Deepgram, Whisper, system ASR) and any TTS provider (ElevenLabs, OpenAI TTS, pyttsx3 local).

**Basic STT/TTS already works** if the required packages are installed. The wake-word loop (always-on mic listener) requires oice.enabled: true.

**Activation:**
`yaml
# ~/.navig/config.yaml
voice:
  enabled: true
  wake_word: "navig"
  stt_provider: "deepgram"    # or "whisper", "system"
  tts_provider: "elevenlabs"  # or "openai", "local"
`
`ash
pip install navig[voice]
`

**Files:** 
avig/voice/ (STT, TTS, wake-word listener)
**Optional dep group:** 
avig[voice] > deepgram-sdk, pyttsx3, pyaudio
**Status: OPTIONAL � ships disabled**

---

### Blackbox Recorder

**What it does:**
Records every command, response, decision, and event into a local encrypted log � the "blackbox". Designed for post-incident analysis: you can replay what NAVIG did in the last 24 hours, export structured logs for debugging, and (with the seal layer) cryptographically sign the log so tampering is detectable.

**The concept:**
Named after flight data recorders. The principle is that any autonomous agent that takes real actions (file writes, SSH commands, DB queries) should have an immutable audit trail. The blackbox is that trail. It's deliberately separate from the debug log (debug.log) � it captures *what the agent decided to do*, not what the Python code emitted.

**Current state:**
- 
ecorder.py and export.py are complete and safe for read-only diagnostics  
- The crypto-seal layer (write-once, signed chunks) is MVP2 work
- lackbox.enabled: true activates the recorder without the seal

**Activation:**
`yaml
# ~/.navig/config.yaml
blackbox:
  enabled: true
`

**Files:** 
avig/blackbox/recorder.py � 
avig/blackbox/export.py
**Status: OPTIONAL (wiring is MVP2) � code exists, config gate ships disabled**

---

### SelfHeal Scanner

**What it does:**
Periodically scans the running NAVIG installation for known failure patterns: stale SSH keys, broken config values, missing dependency packages, outdated command templates, and schema drift in the database. Produces a structured report of findings with suggested fixes.

**The concept:**
NAVIG is installed on servers that drift over time � packages get updated, keys expire, configs accumulate technical debt. The SelfHeal scanner is the equivalent of a ship's maintenance officer: it doesn't fix things immediately, it identifies what needs attention and queues a repair proposal.

**Two modes:**
- **Scanner only** (contribute.enabled: true) � read-only diagnostics, produces a report
- **Auto-PR mode** (contribute.auto_pr: true) � scanner findings are turned into proposed code patches submitted as pull requests to your NAVIG config repo (trusted only after the scanner has been running in read-only mode for one release cycle)

**Activation:**
`yaml
# ~/.navig/config.yaml
contribute:
  enabled: true       # scanner: safe, read-only
  auto_pr: false      # set true only after trusting the scanner
`

**Files:** 
avig/selfheal/scanner.py � 
avig/selfheal/patcher.py
**Status: OPTIONAL � scanner is MVP1.5 candidate; auto-PR is MVP2**

---

### Proactive Providers (Calendar / Email)

**What it does:**
Connects NAVIG to external data sources (Google Calendar, ICS feeds, IMAP email) and synthesises a proactive engagement loop: NAVIG checks your upcoming calendar and unread email, generates a morning briefing, flags urgent items, and can trigger Telegram messages when a scheduled event is imminent.

**The concept:**
NAVIG's engagement tick is already active � the timer mechanism that would drive proactive outreach runs in the daemon. What's gated behind proactive.enabled is the *external data pipe*: the calendar/email readers that give the proactive tick something to act on. Basic engagement (heartbeat, scheduled responses) works without any external providers.

**Activation:**
`yaml
# ~/.navig/config.yaml
proactive:
  enabled: true
  providers:
    - type: google_calendar
      credentials_path: "~/.navig/google_creds.json"
    - type: imap_email
      host: "imap.gmail.com"
      user: "you@gmail.com"
`

**Files:** 
avig/proactive/providers/ (google_calendar, ics_calendar, imap_email)
**Status: OPTIONAL � engagement tick is CORE; external providers ship disabled**

---

### Quick Activation Reference

| Feature | Config key | Extra install |
|---------|------------|---------------|
| Identity / Sigil | � always on � | � |
| Matrix Channel | matrix.enabled: true | pip install navig[matrix] |
| Mesh / Flux | mesh.enabled: true | � |
| Deck Web UI | 	elegram.deck_url: "<url>" | NAVIG Deck app (navig-dock) |
| Voice | oice.enabled: true | pip install navig[voice] |
| Blackbox Recorder | lackbox.enabled: true | � |
| SelfHeal Scanner | contribute.enabled: true | � |
| SelfHeal Auto-PR | contribute.auto_pr: true | (scanner must be trusted first) |
| Proactive Providers | proactive.enabled: true | provider-specific credentials |

---

*Last updated: 2026-03-17*

---

## March 2026 Sprint — New Commands

### `navig mount` — Drive Junction Registry

Manages NTFS junctions (Windows) or symlinks (other platforms) via a persistent registry.
Registry: `~/.navig/registry/drives.json`. Helper script: `~/.navig/scripts/mount-drive.ps1`.

```bash
navig mount add    <label> <source> [target]   # register + create junction
navig mount list   [--json]                    # list all registered junctions
navig mount remove <label>  [--yes]            # remove junction + registry entry
navig mount verify [--json]                    # check aliveness, update registry
navig mount sync   [--dry-run]                 # verify + regenerate mount-drive.ps1
```

`sync` writes a PowerShell restore script; run on login to recreate junctions after reboot.
`--no-create` on `add` registers without touching the filesystem.

---

### `navig inbox stats / add / ui`

```bash
navig inbox stats [--json]         # routing stats from InboxStore
navig inbox add <url>              # fetch URL -> classify -> route -> persist
navig inbox ui                     # TUI interactive review panel (y/n/q/?)
```

---

### Inbox Neuron Router — `navig/inbox/`

| Module | Purpose |
|--------|---------|
| `store.py` | SQLite WAL store at `~/.navig/runtime/inbox.db` |
| `classifier.py` | BM25 scorer + LLM fallback, 8 routing categories |
| `router.py` | COPY/MOVE/LINK dispatch, RENAME/SKIP/OVERWRITE conflict strategies |
| `hooks.py` | Lifecycle hooks: `before_classify`, `after_classify`, `before_route`, `after_route` |
| `watcher.py` | `watchfiles` backend or polling fallback; global + project inbox dirs |

---

### Layered Settings — `navig/settings/`

Five-layer resolution: `DEFAULTS -> global -> layer -> project -> local`

```python
from navig.settings import get, set_setting
get("ai.model")                                    # "claude-3-7-sonnet-20250219"
set_setting("ai.model", "gpt-4o", layer="project")
```

`${BLACKBOX:key}` references resolved via vault at read time; kept as-is if vault unavailable.

---

## Extensibility Reference (Canonical Structure)

This table is the single source of truth after the March 2026 consolidation.
Removed: root `packs/` (superseded by `packages/`), root `plugins/` (superseded by `packages/`), root `workflows/` (runtime reads `navig/resources/workflows/`).

| Directory | Format | Loaded By | Purpose |
|-----------|--------|-----------|---------|
| `skills/<category>/<name>/SKILL.md` | Markdown + YAML frontmatter | `navig/skills/loader.py`, `navig/commands/skills.py` | AI agent natural-language instructions |
| `skills/official/` | SKILL.md subtree | same | Official built-in skills by domain |
| `navig/skills/builtin/` | SKILL.md | `navig/skills/loader.py` | Always-available inline skills |
| `templates/<app>/template.yaml` | YAML | `navig/template_manager.py` | Server app definitions (paths, services, commands) |
| `packages/<id>/navig.package.json` | JSON manifest | `navig/commands/package.py` | Installable content units (commands, tools, telegram, workflows) |
| `navig/tools/domains/*.py` | Python module with `register_tools(registry)` | `navig/tools/router.py` | Internal ToolRouter domain registrations |
| `navig/resources/workflows/*.yaml` | YAML step sequences | `navig/commands/workflow.py` | Built-in workflow templates |
| `navig/plugins/` | Python classes | `navig/plugins/__init__.py`, `navig/core/kernel.py` | Runtime Python-level plugin manager |

### Adding Content

| Want to add... | Do this |
|---|---|
| AI skill for a new tool/service | `skills/<category>/<name>/SKILL.md` |
| Server app template (paths, services, env) | `templates/<appname>/template.yaml` |
| Installable extension (commands, Telegram, workflows) | `packages/<id>/navig.package.json` + content |
| New ToolRouter tool domain | `navig/tools/domains/<name>_pack.py` + `router.py` entry |
| Built-in workflow | `navig/resources/workflows/<name>.yaml` |
