# NAVIG - AI-Optimized Command Reference Guide

> **Primary Knowledge Base for AI Assistants**
> Version: 3.25.0 | Last Updated: 2026-09-04

---

## 📋 Table of Contents

1. [Quick Start & Overview](#1-quick-start--overview)
2. [Host Management](#2-host-management)
3. [Application Management](#3-application-management)
4. [⭐ Remote Command Execution (CRITICAL)](#4-remote-command-execution--critical-section)
5. [File Operations](#5-file-operations)
6. [⭐ Docker Operations (NEW)](#6-docker-operations)
7. [Database Operations](#7-database-operations)
8. [Service & Monitoring](#8-service--monitoring)
9. [SSH Tunnel Management](#9-ssh-tunnel-management)
10. [Security Commands](#10-security-commands)
11. [⭐ Local System Management (NEW)](#11-local-system-management)
12. [System Maintenance](#12-system-maintenance)
13. [Template & Addon Management](#13-template--addon-management)
14. [⭐ Execution Modes & Confirmation](#14-execution-modes--confirmation)
15. [⭐ Configuration Backup & Export](#15-configuration-backup--export)
16. [Common Workflows](#16-common-workflows)
17. [Troubleshooting Guide](#17-troubleshooting-guide)
18. [Configuration Reference](#18-configuration-reference)
19. [Global Options](#19-global-options)
20. [⭐ Plugin System (NEW)](#20-plugin-system)
21. [⭐ Workflow System (NEW)](#21-workflow-system)
22. [⭐ AI Integration (MCP & Wiki RAG)](#22-ai-integration-mcp--wiki-rag)
23. [⭐ Autonomous Agent System (Gateway, Heartbeat, Cron)](#23-autonomous-agent-system-gateway-heartbeat-cron)
24. [⭐ Memory & Context Management (NEW)](#24-memory--context-management)
25. [⭐ Autonomous Agent Mode (NEW)](#25-autonomous-agent-mode)
26. [⭐ Information Retrieval (Web Search, Prices, Weather) (NEW)](#26-information-retrieval)
27. [⭐ Advanced AI Features (NEW)](#27-advanced-ai-features-new)
28. [⭐ Operations Dashboard TUI (NEW)](#28-operations-dashboard-tui)
29. [⭐ Command History & Replay (NEW)](#29-command-history--replay)
30. [⭐ Intelligent Suggestions & Quick Actions (NEW)](#30-intelligent-suggestions--quick-actions)
31. [⭐ Event-Driven Automation (Triggers) (NEW)](#31-event-driven-automation-triggers)
32. [⭐ Operations Insights & Analytics (NEW)](#32-operations-insights--analytics)
33. [📖 Additional Documentation](#33-additional-documentation)

---

## Core Mandate
**ALWAYS** use the `NAVIG` tool for the following operations. Manual intervention or alternative tools should be avoided unless explicitly necessary for debugging.

1. **Remote Production Server:** All operations, deployments, and monitoring.
2. **Local Database:** All local database manipulations, queries, and setups.
3. **Database Changes:** Migrations, schema updates, and seeding.
4. **Server Configurations:** Environment variable adjustments and service configs.

### Repository Local-Only Folders (Agent Hygiene)

- `CHANGELOG.md` is tracked and remains the release/public history source of truth.
- `.dev/` is the default local AI working folder for scripts, logs, outputs, and scratch artifacts.
- `.local/` is reserved for backups/moved files and compatibility temp artifacts.
- Keep repo root clean: do not place scratch files directly in root.

## 1. Quick Start & Overview

### What is NAVIG?

**NAVIG** (No Admin Visible In Graveyard) is a unified operations platform for managing both **computer systems** and **personal life** with the same systematic, automation-first approach. It provides a cross-platform Python CLI for secure remote server management via SSH, plus AI-powered personal productivity tools.

**System Operations (DevOps):**
- Remote command execution with shell escaping handling
- Database operations (MySQL, MariaDB, PostgreSQL) via SSH tunnel or Docker
- File upload/download with smart path detection
- Service monitoring and health checks
- AI-assisted troubleshooting

**Life Operations (LifeOps):**
- Knowledge base with semantic search (Memory Bank)
- Personal context and preferences tracking
- Task and workflow automation
- Daily routine management
- Goal tracking and progress monitoring

### Installation

```bash
# Prerequisites: Python 3.10+, SSH access to remote servers

# Install via pip
pip install navig

# Or install from source
git clone https://github.com/navig-run/core.git
cd core
pip install -e .
```

### Installer profiles

After running `install.sh` / `install.ps1`, finalize setup with:

```bash
navig init                        # interactive CLI onboarding (default)
navig init --status               # show compact init/setup status summary
navig init --tui                  # opt-in full-screen TUI onboarding
navig init --profile quickstart   # chat-first bootstrap (Telegram handoff + auto-start)
navig init --profile operator     # silent, non-interactive (recommended for automation)
navig init --profile node         # bare minimum: dirs + CLI check only
navig init --profile architect    # operator + MCP config
navig init --profile system_standard  # operator + system service
navig init --profile system_deep  # system_standard + Windows tray
navig init --profile operator --dry-run   # preview without changes
```

UI selector override (optional):

```bash
NAVIG_INIT_UI=tui navig init   # force TUI mode
NAVIG_INIT_UI=cli navig init   # force classic CLI mode
```

When running `navig init --tui`, the Advanced flow now starts with a tier chooser:

- `Essential` (~2 min): core workspace and safe defaults, integrations deferred
- `Recommended` (~5 min): adds AI provider + vault setup guidance
- `Full` (~8 min): includes optional integration prompts

Tier behavior details:

- `Essential`: skips the long wizard and moves from checks directly to review
- `Recommended`: runs full wizard without optional integration prompts
- `Full`: adds an integrations step with Matrix/SMTP/Social value context and toggles

During `navig init` (engine flow), NAVIG now also offers a dedicated **Web Search Provider** setup step with provider preference capture and API-key onboarding (vault-first, with compatibility fallback).

Verification and completion:

- CLI onboarding now prints step progress (`[n/N %]`) and a verification summary before completion
- TUI onboarding now shows a dedicated verification dashboard before final write/activation

You can always configure deferred integrations later from CLI (`navig init`, `navig matrix`, `navig help email`, etc.).

**Profile overview**

| Profile | Modules applied |
|---------|----------------|
| `quickstart` | alias of `operator` + terminal-to-Telegram onboarding handoff |
| `node` | config dirs, CLI verify, legacy migration |
| `operator` | + shell PATH, vault init, Telegram token |
| `architect` | + MCP config file |
| `system_standard` | + system service registration |
| `system_deep` | + Windows tray install |

**Telegram token** — set `NAVIG_TELEGRAM_BOT_TOKEN` before running to have it
stored automatically (vault + `.env` + `config.yaml`). If absent, the step is
silently skipped; reconfigure later with `navig init` (interactive).

`quickstart` behavior: when a token is available (or entered during the mini tutorial),
NAVIG attempts to auto-start daemon + gateway + Telegram bot and writes a one-time
chat onboarding handoff. On first `/start` in Telegram, the bot displays a continuation
panel (Providers, Intake, Status) to finish setup in chat.

```bash
NAVIG_TELEGRAM_BOT_TOKEN="<token>" navig init --profile operator   # Linux/macOS
```

```powershell
$env:NAVIG_TELEGRAM_BOT_TOKEN="<token>"; navig init --profile operator  # Windows
```

### Roll back the last installer run

```bash
navig init-rollback               # undo most recent run
navig init-rollback --dry-run     # preview without changing anything
navig init-rollback --profile operator  # undo last operator run specifically
```

The manifest lives in `~/.navig/history/install_<profile>_<ts>.jsonl`.

### Basic Workflow

```bash
# Step 1: Add a remote host
navig host add production

# Step 2: Set it as active host
navig host use production

# Step 3: Run commands against the active host
navig run "ls -la /var/www"
navig health
navig logs nginx

# Tip: in-app help topics
navig help db

# Onboarding shortcut
navig quickstart

# Current context summary
navig status
navig status --json
```

Use `navig help` (or `navig help <topic>`) for short, predictable help summaries; add `--plain` or `--json` when scripting.

### Onboarding & Workspace Setup

NAVIG includes an interactive onboarding wizard inspired by Reference Agent that helps you configure AI providers, workspace templates, and bot settings.

```bash
# Launch interactive onboarding wizard
navig onboard                    # Interactive mode - choose flow

# Specific flows
navig onboard --flow quickstart  # Minimal prompts, sensible defaults
navig onboard --flow manual      # Full configuration with all options
navig onboard -n                 # Non-interactive with defaults (for automation)

# Workspace management
navig workspace --status         # Show workspace status
navig workspace --init           # Initialize with templates
navig workspace --path ~/custom  # Use custom workspace path
```

**Workspace Templates:**
The workspace contains markdown files that define your AI agent's personality and capabilities:

| Template | Purpose |
|----------|---------|
| `IDENTITY.md` | Agent name and emoji (e.g., � NAVIG) |
| `SOUL.md` | Personality and behavior guidelines |
| `AGENTS.md` | Multi-agent collaboration definitions |
| `TOOLS.md` | Tool and capability definitions |
| `USER.md` | User preferences and permissions |
| `HEARTBEAT.md` | Periodic status update configuration |
| `BOOTSTRAP.md` | First-run instructions (auto-removes after bootstrap) |

Configuration is stored at `~/.navig/navig.json` and workspace templates at `~/.navig/workspace/`.

### Key Concepts

| Concept | Description |
|---------|-------------|
| **Host** | A remote server with SSH access (IP, user, key) |
| **Application** | A project/site deployed on a host (has its own paths, services) |
| **Active Host** | Currently selected host for operations |
| **Active App** | Currently selected application context |

**Hierarchy:** Hosts contain Applications. Set active host first, then optionally set active app.

### ⚠️ CRITICAL: Always Use NAVIG for Server Operations

| ❌ Never Do This | ✅ Use NAVIG Instead |
|------------------|---------------------|
| `ssh user@host` | `navig run "command"` |
| `scp file user@host:path` | `navig file add file path` |
| `mysql -h host -u user -p` | `navig db run "query"` |
| `mysqldump db > backup.sql` | `navig backup run` |

---

## 1.5 Command Architecture - 4 Pillars

NAVIG follows the industry-standard **noun-verb pattern** (`navig <resource> <action>`) used by Docker, Kubernetes, and GitHub CLI.

### The 4 Pillars

| Pillar | Resource Groups | Purpose |
|--------|-----------------|---------|
| **1. Infrastructure** | `host` (+`monitor`, `security`, `maintenance`) | Remote server management, monitoring, security |
| **2. Services** | `app`, `docker`, `web` (+`hestia`) | Application lifecycle |
| **3. Data** | `db`, `file`, `log`, `backup` | File, database, and backup management |
| **4. Automation** | `flow` (+`template`), `skills`, `ai`, `wiki` | Workflows, skills, AI assistance |

### Nested Commands

Some groups contain nested subcommands:
- `navig host monitor` - Server monitoring (resources, disk, health)
- `navig host security` - Security management (firewall, fail2ban)
- `navig host maintenance` - System maintenance (updates, cleanup)
- `navig web hestia` - HestiaCP control panel management
- `navig flow template` - Template management

### Canonical Actions

All resource groups support consistent actions:

| Action | Description | Example |
|--------|-------------|---------|
| `list` | List resources | `navig host list` |
| `show` | Show detailed info | `navig host show` |
| `add` | Create new resource | `navig host add prod` |
| `edit` | Modify resource | `navig file edit /path --mode 755` |
| `remove` | Delete resource | `navig app remove myapp` |
| `run` | Execute operation | `navig db run "SELECT 1"` |
| `test` | Validate/check | `navig host test` |
| `use` | Set active resource | `navig host use prod` |

### Quick Reference

```bash
# ═══ QUICK START ═══
navig start                        # Start gateway + bot (background)
navig start --foreground           # Start with live logs
navig bot status                   # Check if bot is running
navig bot stop                     # Stop all services
navig init --status                # Setup readiness dashboard

# ═══ PILLAR 1: INFRASTRUCTURE ═══
navig host list                    # List hosts
navig host show                    # Current host info
navig host add <name>              # Add host
navig host use <name>              # Switch host
navig host test                    # Test SSH connection

# Host subgroups (nested)
navig host monitor show            # Health overview
navig host monitor show --disk     # Disk usage
navig host security show           # Security scan
navig host firewall                # Firewall status
navig host maintenance update      # Update packages
navig host maintenance clean       # Cleanup

# ═══ PILLAR 2: SERVICES ═══
navig app list                     # List apps
navig app show                     # Current app info
navig app add <name>               # Add app
navig app use <name>               # Switch app
navig docker list                  # List containers
navig docker run <container> cmd   # Run in container
navig web vhosts                   # List virtual hosts
navig web hestia list --users      # List HestiaCP users
navig web hestia list --domains    # List HestiaCP domains

# ═══ PILLAR 3: DATA ═══
navig file list /path              # List directory
navig file show /path              # View file contents
navig file add local remote        # Upload file
navig file get remote local        # Download file
navig file edit /path --mode 755   # Change permissions
navig log show nginx               # View logs
navig db list                      # List databases
navig db tables <database>         # List tables
navig db run "SELECT 1"            # Execute SQL
navig db run --shell               # Interactive shell
navig backup list                  # List backups
navig backup run                   # Create backup

# ═══ PILLAR 4: AUTOMATION ═══
navig flow list                    # List flows
navig flow run <name>              # Execute flow
navig flow show <name>             # Show flow details
navig flow template list           # List templates
navig flow template add <name>     # Enable template
navig skills list                  # List AI skills
navig skills tree                  # Show skills by category
navig skills show <name>           # Show skill details and commands
navig skills run <skill>:<cmd>     # Run a skill command
navig skills run <skill> [args]    # Run skill entrypoint (py/js)
navig ask "question"               # Ask AI (canonical)
navig ai ask "question"            # Deprecated alias to navig ask
navig wiki show <topic>            # Show wiki page
navig wiki list                    # List wiki pages
```

### Removed and renamed commands (migration)

⚠ Most of these no longer exist at all — they error rather than warn. Verified against the
CLI:

| Removed name | Use instead |
|--------------|-------------|
| `monitor` | `navig host monitor` |
| `security` | `navig host security` |
| `template` | `navig flow template` |
| `addon` | `navig flow template` |
| `hestia` | `navig web hestia` |
| `workflow` | `navig block list` / `navig apply <id>` (see below) |

Three names DO still resolve, and two of them are not what this table used to imply:

| Name | Status |
|------|--------|
| `navig server` | Genuinely deprecated — its own help says *"[DEPRECATED: Use 'navig host']"*. |
| `navig system` | **Live and unrelated** — "System information and maintenance" (`info`, `clean`). It is *not* an alias for `navig host maintenance`. |
| `navig task` | Resolves, but its help says *"retired, superseded by Blocks (see `navig apply`)"*. |

⚠ **The workflow engine is retired.** `navig flow list` says so itself and points at Blocks,
so `workflow → flow` is a migration to a dead end. The live successor is **Blocks**:

| Old | New |
|-----|-----|
| `workflow list` | `navig block list` |
| `workflow show <name>` | `navig block show <name>` |
| `workflow run <name>` | `navig apply <name>` |
| `workflow run <name> --var k=v` | `navig apply <name> --input k=v` |
| `workflow validate <name>` | `navig block verify <name>` |
| `workflow create <name>` | `navig block new` |

`navig flow template` is the exception — templates were not retired and that surface is live.

---

## 1.6 Command-First Navigation

NAVIG uses a command-first UX: each capability is reachable directly without a global navigation menu.

### Core entrypoints

```bash
navig --help                 # Top-level command map
navig help <topic>           # In-app docs for a specific area
navig init --status          # Setup readiness and next actions
```

### Group-first discovery

```bash
navig host --help            # Host management
navig app --help             # Application management
navig db --help              # Database operations
navig file --help            # File operations
navig flow --help            # Workflow automation
```

### Exit codes — what `$?` means

Every NAVIG command is meant to be chained and scripted, so the exit status is part of
the contract, not an afterthought:

| Code | Meaning |
|------|---------|
| `0` | Success. Also an honest **empty** result — a host with no apps, a filter matching nothing, an optional component that is simply absent. Empty is not a failure. |
| `1` | The operation failed — the command could not do what you asked. |
| `2` | Usage error — the named host/app/file does not exist, or a required argument is missing. |

```bash
# safe to chain: the deploy only runs if the migration actually succeeded
navig docker exec app "npm run migrate" && ./deploy.sh

# branch on the distinction
navig app show web
case $? in
  0) echo "ok" ;;
  2) echo "no such app — check 'navig app list'" ;;
  *) echo "failed" ;;
esac
```

`navig docker exec` propagates the **container's own** exit code rather than a flat 1, so
`$?` still tells you what the remote command returned.

> A command that prints a red ✗ always exits non-zero. If you ever see an error message
> with `$?` of 0, that is a bug worth reporting — the build guard
> `tests/quality/test_command_exit_honesty.py` exists to prevent exactly that.

---

## 1.7 In-App Help System

NAVIG ships a built-in help system for quick offline reference without
leaving the terminal.

### List All Topics

```bash
navig help
```

Prints a table of every available help topic and a one-line description.
Use this to discover topics you can dive into.

### View a Topic

```bash
navig help <topic>
```

Renders the full Markdown topic file for that resource group (rich headings,
code blocks, and examples in the terminal), ending with a pointer to
`navig <topic> --help` when the topic is a runnable command. Topics without a
Markdown guide fall through to the command's own `--help` flag reference.
`navig <cmd> --help` itself always shows Typer's exhaustive flag dump — the
guide never replaces it.

**Examples:**

```bash
navig help db          # Database operations
navig help host        # Host management
navig help run         # Remote command execution
navig help file        # File operations
navig help backup      # Backup and restore
navig help docker      # Container management
navig help tunnel      # SSH tunnels
navig help web         # Web server management
navig help config      # Configuration management
navig help flow        # Workflows and automation
navig help wiki        # Wiki and knowledge base
navig help ai          # AI assistant
```

### Machine-Readable Schema

Output the full command schema as JSON (for shell completion or AI tooling):

```bash
navig --schema
# or
navig help --schema
```

This emits a JSON document listing every command group, its description,
and all subcommands. Pipe it to `jq` or use it in automation scripts.

Registry artifacts are generated from command metadata and committed under:

- `generated/commands.json` (authoritative registry)
- `generated/commands.md` (rendered reference)
- `generated/deprecations.json` (deprecation report)
- `generated/completions/commands.txt` (completion source)

Regenerate locally **under Python 3.13 with the first-party plugins installed** — the
committed manifest is built on 3.13, and any other interpreter (or a bare-core install)
silently shrinks the plugin surface. The generator refuses to overwrite the tracked
`generated/` files from a non-3.13 interpreter unless you acknowledge it with
`--allow-interpreter <X.Y>`:

```bash
python tools/export_registry.py --validate --format both --deprecations-report
```

`npm run ci` (via `scripts/ci-local.mjs`) runs a **command manifest freshness** step that
regenerates the manifest under Python 3.13 and checks it against the committed copy, so a stale
catalog fails locally even though CI is billing-blocked. The check is **core-scoped**: the
committed manifest is a superset snapshot from one machine, but which first-party plugins you have
installed varies — so **core** commands (whose `module` is the `navig` package) are compared
strictly (a core command added / removed / renamed / signature-drifted fails, naming it), while a
difference confined to **plugin** commands (`navig_<plugin>`) is a non-fatal note, not a failure.
Regenerate under 3.13 with all first-party plugins installed to refresh the plugin portion.

### Per-Command Help

Every command and subcommand also exposes `--help` via Typer:

```bash
navig db --help
navig db list --help
navig host monitor show --help
```

### Fast Root Help Screen

`navig --help` (and bare `navig`) now prints a compact one-screen command map with:

- status bar (`host`, `profile`, `version`)
- grouped sections (Core, Connections, Apps & Services, Infrastructure,
  Security, Environment, Monitoring, Developer)
- aligned command descriptions for quick scanning
- high-value examples and a rotating single-line tip

Top-level compatibility aliases are also exposed for operator ergonomics:
`navig cert`, `navig key`, `navig firewall`, `navig dns`, `navig port`,
`navig proxy`, `navig env`, `navig secret`, `navig job`, and `navig alias`.

This root screen is intentionally terse. Use topic help for full detail:

```bash
navig help <topic>
navig <command> --help
```

---

## 2. Host Management

Manage remote server configurations.

### `navig host list`

List all configured hosts.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `--all`, `-a` | flag | No | Show detailed information |
| `--format`, `-f` | string | No | Output format: table, json, yaml |

**Examples:**
```bash
# List all hosts
navig host list

# List with detailed info
navig host list --all

# Output as JSON
navig host list --format json
```

**Related Commands:** `navig host use`, `navig host add`

---

### `navig host add <name>`

Add a new remote host configuration via interactive wizard.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Unique identifier for this host |

**Examples:**
```bash
# Add production server (interactive prompts for host, user, SSH key)
navig host add production

# Add staging server
navig host add staging
```

**What the wizard asks:**
- Hostname/IP address
- SSH port (default: 22)
- SSH username
- Authentication method (key or password)
- SSH key path (if using key auth)

**Related Commands:** `navig host list`, `navig host use`, `navig host show --inspect`

---

### `navig host use <name>`

Set the active host for subsequent operations (global).

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Host name to activate |

**Examples:**
```bash
# Switch to production host
navig host use production
# ✓ Switched to host: production

# Switch to staging
navig host use staging
```

**💡 How Host Selection Works:**

NAVIG resolves the active host in this priority order:
1. `NAVIG_ACTIVE_HOST` environment variable (for CI/CD)
2. `.navig/config.yaml:active_host` in current directory (project-local)
3. `~/.navig/cache/active_host.txt` (set by `navig host use`)
4. `default_host` from global config

**For multi-project workflows:** Use `navig context set --host production` to set project-local context that persists automatically. See the Context Management section below.

**Related Commands:** `navig host list`, `navig host show --current`, `navig context set`

---

### Context Management (`navig context`)

Manage host/app context at the project level. Context determines which host NAVIG commands target.

**Resolution Priority:**
1. `--host`/`--app` flags (command line override)
2. `NAVIG_ACTIVE_HOST`/`NAVIG_ACTIVE_APP` (environment variables, ideal for CI/CD)
3. `.navig/config.yaml` in current directory (project-local, set by `navig context set`)
4. User cache at `~/.navig/cache/active_host.txt` (global, set by `navig host use`)
5. `default_host` from global config

**Commands:**
```bash
# Show current context resolution
navig context              # or: navig ctx
navig context show
navig context show --json  # JSON output for scripting

# Set project-local context
navig context set --host production
navig context set --host staging --app myapp

# Clear project context (fall back to global)
navig context clear

# Initialize .navig directory in current project
navig context init
```

**Example Workflow:**
```bash
# In project A (uses production)
cd ~/projects/webapp
navig context set --host production

# In project B (uses staging)
cd ~/projects/test-app
navig context set --host staging

# Now each project remembers its own context
cd ~/projects/webapp
navig run "systemctl status nginx"  # Runs on production

cd ~/projects/test-app
navig run "systemctl status nginx"  # Runs on staging
```

**Project Isolation:**
- Each project can have its own `.navig/config.yaml`
- Add `.navig/` to `.gitignore` to keep context local
- Use `NAVIG_ACTIVE_HOST` in CI/CD for explicit context

**Related Commands:** `navig host use`, `navig host show --current`

---

### `navig host show --current`

Show the currently active host with source information.
(There is no `current` verb — it is a flag on `show`.)

**Examples:**
```bash
navig host show --current
# Output shows source of selection:
# ℹ Source: 📍 local (.navig/config.yaml)
#          Server: production
# ┏━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
# ┃ Property ┃ Value              ┃
# ...
```

**Source indicators:**
- `🔧 env` - From `NAVIG_ACTIVE_HOST` environment variable
- `📍 local` - From project `.navig/config.yaml`
- `📄 legacy` - From legacy `.navig` file
- `🌐 global` - From global cache (`navig host use`)
- `⚓ default` - From global config default

**Related Commands:** `navig host use`, `navig host list`

---

### `navig host remove <name>`

Remove a host configuration.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Host name to remove |

**Examples:**
```bash
# Remove old staging server
navig host remove old-staging
```

**⚠️ Warning:** This deletes all host configuration. Apps associated with this host will become orphaned.

**Related Commands:** `navig host list`, `navig host add`

---

### `navig host show --inspect`

Auto-discover host details (OS, PHP, databases, web servers, paths).
(There is no `inspect` verb — it is a flag on `show`.)

**Examples:**
```bash
# Inspect active host
navig host show --inspect

# Output shows detected:
# - Operating System (Ubuntu 24.04)
# - PHP version (8.4.8)
# - MySQL/PostgreSQL version
# - Nginx/Apache version
# - Web root paths
```

**💡 Tip:** Run this after adding a new host to auto-populate configuration.

**Related Commands:** `navig host add`, `navig host show`

---

### `navig host show [name]`

Show detailed host information.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | No | Host name (uses active host if omitted) |

**Examples:**
```bash
# Show info for active host
navig host show

# Show info for specific host
navig host show production
```

**Related Commands:** `navig host list`, `navig host show --inspect`

---

### `navig host test [name]`

Test host connectivity.

- Remote hosts: SSH connectivity check
- Local hosts (`type: local` or `is_local: true`): local shell probe (SSH skipped)

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | No | Host name (uses active host if omitted) |

**Examples:**
```bash
# Test connection to active host
navig host test

# Test specific host
navig host test staging
```

**Related Commands:** `navig host add`, `navig host show`

---

### `navig host add <new_name> --from <source>`

Clone a host configuration.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source` | string | Yes | Source host name to clone |
| `new_name` | string | Yes | New host name |

**Examples:**
```bash
# Clone production to create staging
navig host add staging --from production
```

**Related Commands:** `navig host add`, `navig host remove`

---

### Editing a host

There is no `edit` verb. A host's configuration is a YAML file under
`~/.navig/hosts/<name>.yaml` — open it directly, or replace the entry:

```bash
# See the current definition first
navig host show production

# Replace it: remove, then re-add through the wizard
navig host remove production
navig host add production

# Or start a new host from an existing one and adjust that
navig host add production-next --from production
```

⚠ `host add --from` CLONES to a new name; it does not modify the source.

**Related Commands:** `navig host show`, `navig host show --inspect`

---

### `navig host use <name>`

Switch the active host context (global). There is no `default` verb — `use` is what pins
the host that later commands act on.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | No | Host name; omit to be prompted |

**Examples:**
```bash
# Make production the active host
navig host use production

# Confirm which host is active, and where that came from
navig host show --current
```

**Related Commands:** `navig host use`, `navig host show --current`

---

## 3. Application Management

Manage applications deployed on hosts.

### `navig app list`

List all configured applications.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `--host`, `-h` | string | No | Filter apps by host |
| `--all`, `-a` | flag | No | Show all apps from all hosts with details |
| `--format`, `-f` | string | No | Output format: table, json, yaml |

**Examples:**
```bash
# List apps on active host
navig app list

# List all apps from all hosts
navig app list --all

# List apps on specific host
navig app list --host production
```

**Related Commands:** `navig app use`, `navig app add`

---

### `navig app add <name>`

Add a new application to a host.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Application name |
| `--host`, `-h` | string | No | Host to add app to (uses active host) |

**Examples:**
```bash
# Add app to active host
navig app add my-laravel-app

# Add app to specific host
navig app add blog --host production
```

**Related Commands:** `navig app list`, `navig app use`

---

### `navig app use [name]`

Set the active application context.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | No | App name to activate (interactive if omitted) |
| `--local`, `-l` | flag | No | Set as local active app (current directory only) |
| `--clear-local` | flag | No | Clear local active app setting |

**Examples:**
```bash
# Set active app
navig app use my-laravel-app

# Interactive selection if name omitted
navig app use

# Set local active app (creates .navig file in current directory)
navig app use my-app --local
```

**Related Commands:** `navig app list`, `navig app current`

---

### `navig app current`

Show the currently active application.

**Examples:**
```bash
navig app current
# Output: Active app: my-laravel-app (on host: production)
```

**Related Commands:** `navig app use`, `navig app list`

---

### `navig app remove <name>`

Remove an application configuration.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | App name to remove |
| `--host`, `-h` | string | No | Host containing the app |
| `--force`, `-f` | flag | No | Skip confirmation prompt |

**Examples:**
```bash
# Remove app (prompts for confirmation)
navig app remove old-blog

# Force remove without confirmation
navig app remove old-blog --force
```

**Related Commands:** `navig app list`, `navig app add`

---

### `navig app show <name>`

Show application configuration details.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | App name to show |
| `--host`, `-h` | string | No | Host containing the app |

**Examples:**
```bash
navig app show my-laravel-app
```

**Related Commands:** `navig app info`, `navig app edit`

---

### `navig app edit <name>`

Open application configuration in default editor.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | App name to edit |
| `--host`, `-h` | string | No | Host containing the app |

**Examples:**
```bash
navig app edit my-laravel-app
```

**Related Commands:** `navig app show`, `navig app info`

---

### `navig app search <query>`

Search for applications across all hosts.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | Search query (app name) |

**Examples:**
```bash
# Search for apps containing "blog"
navig app search blog
```

**Related Commands:** `navig app list`

---

## 4. Remote Command Execution ⭐ CRITICAL SECTION

This section documents the most important and frequently used NAVIG functionality. **Pay special attention to the escaping solutions** as this is the most common pain point.

### `navig run "<command>"`

Execute a shell command on the selected host context.

- Remote hosts: command runs over SSH
- Local hosts (`type: local` or `is_local: true`): command runs directly on local machine (no tunnel)

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | string | No* | Shell command to execute |
| `--stdin`, `-s` | flag | No | Read command from stdin (bypasses escaping) |
| `--file`, `-f` | path | No | Read command from file (bypasses escaping) |

*One of `command`, `--stdin`, or `--file` is required.

---

### `navig mode route`

Manage hybrid routing slot assignments (`small`, `big`, `code`) from CLI.

**Commands:**
```bash
navig mode route show
navig mode route show --json
navig mode route set small --provider ollama --model qwen2.5:3b-instruct
navig mode route set big --provider openai --model gpt-4o-mini
navig mode route set code --provider deepseek --model deepseek-coder
```

---

### 4.1 Simple Command Execution

For straightforward single-line commands without special characters:

```bash
# System commands
navig run "ls -la /var/www"
navig run "df -h"
navig run "systemctl status nginx"
navig run "whoami"

# Laravel/PHP commands
navig run "php artisan migrate"
navig run "php artisan cache:clear"
navig run "composer install --no-dev"

# Service management
navig run "systemctl restart nginx"
navig run "tail -n 100 /var/log/nginx/error.log"
```

---

<a id="42--complex-commands-heredocs-json-special-characters"></a>
### 4.2 ⚠️ Complex Commands (Heredocs, JSON, Special Characters)

**THE PROBLEM:**

When executing commands from PowerShell that contain:
- Multi-line heredocs (`cat > file << 'EOF'`)
- JSON with quotes, colons, backslashes
- Special characters (`$`, `"`, `:`, `\`, `/`)

The command will **FAIL** with parsing errors like:
```
Got unexpected extra arguments (\\: \https://... \server\: ...)
```

**ROOT CAUSE:**
Multiple escaping layers (PowerShell → Python CLI → SSH) interpret quotes and special characters differently, causing the command to be split incorrectly.

---

### ✅ SOLUTION 1: Use `--stdin` with PowerShell Here-String (RECOMMENDED)

```powershell
@'
cat > /var/www/config.json << 'EOF'
{
  "$schema": "https://example.com/schema.json",
  "server": {
    "name": "Production Server",
    "url": "https://api.example.com"
  },
  "api_key": "sk-1234567890",
  "database": {
    "host": "localhost",
    "port": 3306
  }
}
EOF
'@ | navig run --stdin
```

**Why this works:**
- PowerShell here-string (`@'...'@`) prevents ALL variable expansion and escaping
- `--stdin` reads the entire command as-is, bypassing CLI argument parsing
- No need to escape `$`, quotes, colons, or any special characters

**More Examples:**

```powershell
# Create a systemd service file
@'
cat > /etc/systemd/system/myapp.service << 'EOF'
[Unit]
Description=My Application
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/var/www/myapp
ExecStart=/usr/bin/node server.js
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
'@ | navig run --stdin

# Run multiple commands
@'
cd /var/www/myapp
git pull origin main
composer install --no-dev
php artisan migrate --force
php artisan config:cache
systemctl restart php-fpm
'@ | navig run --stdin
```

---

### ✅ SOLUTION 2: Use `--file` (Best for Reusable Scripts)

Create a local script file, then execute it:

**deploy-config.sh:**
```bash
#!/bin/bash
cat > /var/www/config.json << 'EOF'
{
  "$schema": "https://example.com/schema.json",
  "server": {"name": "Production"}
}
EOF
chmod 644 /var/www/config.json
chown www-data:www-data /var/www/config.json
```

**Execute:**
```bash
navig run --file deploy-config.sh
```

**When to use `--file`:**
- Complex deployment scripts you reuse
- Scripts with multiple commands
- Scripts you want to version control

---

### ✅ SOLUTION 3: Use `navig file add` for Config Files (CLEANEST)

Instead of creating files via heredoc, upload them directly:

**Step 1:** Create `config.json` locally:
```json
{
  "$schema": "https://example.com/schema.json",
  "server": {
    "name": "Production Server",
    "url": "https://api.example.com"
  },
  "api_key": "sk-1234567890"
}
```

**Step 2:** Upload it:
```bash
navig file add config.json /var/www/config.json
```

**💡 This is the recommended approach for JSON/YAML config files.**

There is no `upload` verb — verified against the CLI. Use `navig file add ...`.

---

### 📊 Decision Tree: Which Method to Use?

```
Is it a simple, single-line command?
├── YES → Use: navig run "command"
└── NO → Does it contain heredocs or multi-line content?
         ├── YES → Are you on PowerShell?
         │        ├── YES → Use: @'...'@ | navig run --stdin
         │        └── NO (Bash) → Use: cat script.sh | navig run --stdin
         └── NO → Is it a JSON/YAML config file?
                  ├── YES → Use: navig file add (RECOMMENDED)
                  └── NO → Use: navig run --file script.sh
```

---

### 📋 Quick Reference Table

| Command Form | Use Case | Example |
|-------------|----------|---------|
| `navig run "cmd"` | Simple commands | `navig run "ls -la"` |
| `navig run --stdin` | Complex commands, heredocs | `@'...'@ \| navig run -s` |
| `navig run -s` | Short form of --stdin | `cat script.sh \| navig run -s` |
| `navig run --file path` | Reusable scripts | `navig run -f deploy.sh` |
| `navig run -f path` | Short form of --file | `navig run -f setup.sh` |

---

### ❌ vs ✅ Side-by-Side Comparison

| ❌ FAILS | ✅ WORKS |
|----------|----------|
| `navig run "cat > config.json << 'EOF' {\"key\": \"value\"} EOF"` | `@'...'@ \| navig run --stdin` |
| `navig run "echo $HOME"` (PowerShell expands $HOME) | `@'echo $HOME'@ \| navig run -s` |
| Escaping nightmare with nested quotes | No escaping needed with here-string |
| Parsing errors with colons and slashes | Characters preserved exactly |

---

### Real-World Example: AFFiNE Configuration

**This fails:**
```powershell
navig run "cat > /home/user/affine/config.json << 'EOF'
{
  \"$schema\": \"https://github.com/...\",
  \"server\": { \"name\": \"AFFiNE\" }
}
EOF"
# ERROR: Got unexpected extra arguments...
```

**This works:**
```powershell
@'
cat > /home/user/affine/config.json << 'EOF'
{
  "$schema": "https://github.com/toeverything/affine/releases/latest/download/config.schema.json",
  "server": {
    "name": "AFFiNE Lab - Production"
  },
  "copilot": {
    "enabled": true,
    "providers": {
      "openai": {
        "apiKey": "sk-your-api-key",
        "baseURL": "https://openrouter.ai/api/v1"
      }
    }
  }
}
EOF
'@ | navig run --stdin
```

---

## 5. File Operations

Transfer files and manage remote filesystem.

### `navig file add <local> [remote]`

Upload file or directory to remote server.

There is no `upload` verb — verified against the CLI. Use `navig file add <local> [remote]`.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `local` | path | Yes | Local file/directory path |
| `remote` | string | No | Remote path (auto-detects from app config if omitted) |

**Examples:**
```bash
# Upload file to app's web root
navig file add index.php

# Upload to specific remote path
navig file add ./dist /var/www/html/public

# Upload directory recursively
navig file add ./app /var/www/html/app
```

**💡 Tip:** For JSON config files, prefer `navig file add` over heredoc commands.

**Related Commands:** `navig file get`, `navig file list`, `navig file show`

---

### `navig file get <remote> [local]`

Download file or directory from remote server.

⚠ There is no remote-file `download` verb. The name IS taken — `navig download` is the
**media downloader plugin** (TikTok videos/profiles), so calling it with a server path does
something entirely different. To fetch a file from a host, use `navig file get`.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `remote` | string | Yes | Remote file/directory path |
| `local` | path | No | Local path (current directory if omitted) |

**Examples:**
```bash
# Download to current directory
navig file get /var/www/html/config.php

# Download to specific local path
navig file get /var/log/nginx/error.log ./logs/nginx-error.log

# Download directory
navig file get /var/www/html/storage/logs ./local-logs
```

**Related Commands:** `navig file add`, `navig file list`, `navig file show`

---

### `navig file list <path>`

List remote directory contents.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Remote directory path |

**Examples:**
```bash
# List web root
navig file list /var/www/html

# List log directory
navig file list /var/log/nginx
```

**Related Commands:** `navig file add`, `navig file get`, `navig file show`

---

### `navig file add <remote_dir> --dir`

Create directory on remote server.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Remote directory path to create |
| `--parents`, `-p` | flag | No | Create parent directories as needed (default: true) |
| `--mode`, `-m` | string | No | Permission mode (default: 755) |

**Examples:**
```bash
# Create directory
navig file add /var/www/html/uploads --dir

# Create with specific permissions
navig file add /var/www/html/private --dir --mode 700
```

**Related Commands:** `navig file edit --mode`, `navig file edit --owner`

---

### `navig file remove <path>`

Delete file or directory on remote server.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Remote file/directory path |
| `--recursive`, `-r` | flag | No | Delete directories recursively |
| `--force`, `-f` | flag | No | Force deletion without confirmation |

**Examples:**
```bash
# Delete file (prompts for confirmation)
navig file remove /var/www/html/old-file.php

# Delete directory recursively
navig file remove /var/www/html/cache --recursive

# Force delete without confirmation
navig file remove /tmp/logs --recursive --force
```

**⚠️ Warning:** Use with caution. Consider `--dry-run` first.

**Related Commands:** `navig file list`, `navig file add --dir`

---

### `navig file edit <path> --mode <mode>`

Change file/directory permissions. (There is no `chmod` verb; permissions are one of the
things `file edit` sets.)

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `remote` | string | Yes | Remote file/directory path |
| `--mode`, `-m` | string | Yes | Permission mode (e.g., 755, 644) |

**Examples:**
```bash
# Set file permissions
navig file edit /var/www/html/storage --mode 775
```

There is **no recursive form** — `file edit` sets one path. For a tree, run the remote
command directly:

```bash
navig run "chmod -R 775 /var/www/html/storage"
```

**Related Commands:** `navig file edit --owner`, `navig file add --dir`

---

### `navig file edit <path> --owner <owner>`

Change file/directory ownership. (There is no `chown` verb.)

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `remote` | string | Yes | Remote file/directory path |
| `--owner`, `-o` | string | Yes | New owner (user or user:group) |

**Examples:**
```bash
# Change owner
navig file edit /var/www/html --owner www-data

# Change owner and group
navig file edit /var/www/html --owner www-data:www-data
```

There is **no recursive form** — for a tree, run the remote command directly:

```bash
navig run "chown -R www-data:www-data /var/www/html"
```

**Related Commands:** `navig file edit --mode`, `navig file add --dir`

---

### 5.1 File Shortcut Commands ⭐ (NEW - AI-Optimized)

These commands provide direct access to common file operations, eliminating the need for complex `navig run` commands with shell escaping.

#### `navig file show <path>`

Read remote file content directly. **Use this instead of** `navig run "cat /path/file"`.
(There is no `cat` verb.)

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `remote` | string | Yes | Remote file path |
| `--head` | flag | No | Take from the START of the file (pairs with `--lines`) |
| `--tail`, `-t` | flag | No | Take from the END of the file (pairs with `--lines`) |
| `--lines`, `-n` | string | No | How many lines, or a range (e.g. `50` or `100-200`) |
| `--download`, `-d` | path | No | Write the content to a local path instead |
| `--json` | flag | No | Machine-readable output |

⚠ `--head`/`--tail` are **flags, not counts** — the count lives in `--lines`.

**Examples:**
```bash
# Read entire file
navig file show /var/www/html/.env

# Read first 20 lines
navig file show /var/log/nginx/error.log --head --lines 20

# Read last 50 lines
navig file show /var/log/nginx/access.log --tail --lines 50
```

**💡 AI Tip:** Use `navig file show` instead of `navig run "cat ..."` for simpler syntax and proper output handling. Always bound a log read with `--lines`.

**Related Commands:** `navig file get`, `navig file list`

---

#### `navig file edit <path>`

Write content to a remote file. (There is no `write-file` verb;
`file edit` takes exactly the options below.) **Use this instead of** complex heredoc patterns like `navig run "cat > file << 'EOF' ... EOF"`.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `remote` | string | Yes | Remote file path to create/overwrite |
| `--content`, `-c` | string | No | Content to write (for simple strings) |
| `--from-file`, `-f` | path | No | Local file to upload as content |
| `--mode`, `-m` | string | No | Permission mode (default: 644) |

**Examples:**
```bash
# Write simple content
navig file edit /var/www/html/test.txt --content "Hello World"

# Write JSON config from local file (RECOMMENDED for complex content)
navig file edit /var/www/app/config.json --from-file ./config.json

# Write with specific permissions
navig file edit /etc/nginx/conf.d/app.conf --from-file nginx-app.conf --mode 644
```

**💡 AI Tip:** For JSON, YAML, or multi-line content, ALWAYS use `--from-file` to avoid shell escaping issues. Create the file locally first, then upload.

**⚠️ CRITICAL:** This command solves the heredoc escaping problem documented in Section 4.2.

**Related Commands:** `navig file add`, `navig file show`

---

#### `navig file list <path>`

List directory contents. **Use this instead of** `navig run "ls -la /path"`.
(There is no `ls` verb.)

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Remote directory path |
| `--all`, `-a` | flag | No | Show hidden files |
| `--tree`, `-t` | flag | No | Show tree structure |
| `--depth`, `-d` | int | No | Tree depth, with `--tree` (default: 2) |
| `--json` | flag | No | Machine-readable output |

⚠ There is no `--long` / `--human` — the listing format is fixed.

**Examples:**
```bash
# List a directory
navig file list /var/www/html

# Include hidden files
navig file list /var/log/nginx --all

# Tree view, three levels deep
navig file list /var/www --tree --depth 3
```

**💡 AI Tip:** Use `navig file list` instead of `navig run "ls -la ..."` for cleaner syntax.

**Related Commands:** `navig file list --tree`, `navig file show`

---

#### `navig file list <path> --tree`

Display directory tree structure. **Use this instead of** `navig run "find /path -type f"` for structure visualization. (There is no `tree` verb.)

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Remote directory path |
| `--tree`, `-t` | flag | Yes | Render as a tree rather than a flat listing |
| `--depth`, `-d` | int | No | Maximum depth level (default: 2) |
| `--all`, `-a` | flag | No | Include hidden entries |

⚠ There is no `--dirs-only`.

**Examples:**
```bash
# Show tree with the default depth
navig file list /var/www/html --tree

# Show three levels
navig file list /var/www/html --tree --depth 3
```

**Related Commands:** `navig file list`, `navig file list --tree`

---

## 6. Docker Operations

Manage Docker containers on remote servers. **These commands replace complex patterns like** `navig run "docker ps -a | grep ..."`.

### `navig docker ps`

List Docker containers. **Use this instead of** `navig run "docker ps -a | grep pattern"`.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `--all`, `-a` | flag | No | Show all containers (default: true) |
| `--filter`, `-f` | string | No | Filter by name pattern |
| `--json` | flag | No | Output as JSON |

**Examples:**
```bash
# List all containers
navig docker ps

# Filter by name (replaces: docker ps -a | grep nginx)
navig docker ps --filter nginx

# Get JSON output for parsing
navig docker ps --json
```

**Related Commands:** `navig docker logs`, `navig docker exec`

---

### `navig docker logs <container>`

View container logs. **Use this instead of** `navig run "docker logs container 2>&1 | tail -n 50"`.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `container` | string | Yes | Container name or ID |
| `--tail`, `-n` | int | No | Number of lines from end (default: 100) |
| `--follow`, `-f` | flag | No | Follow log output |
| `--since` | string | No | Show logs since timestamp (e.g., "1h", "30m") |

**Examples:**
```bash
# View last 100 lines (default)
navig docker logs nginx

# View last 50 lines
navig docker logs affine -n 50

# View logs from last hour
navig docker logs mysql --since 1h

# Follow logs in real-time
navig docker logs app --follow
```

**Related Commands:** `navig docker ps`, `navig docker exec`

---

### `navig docker exec <container> <command>`

Execute command inside a container. **Use this instead of** `navig run "docker exec container command"`.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `container` | string | Yes | Container name or ID |
| `command` | string | Yes | Command to execute |
| `--interactive`, `-i` | flag | No | Interactive mode |
| `--tty`, `-t` | flag | No | Allocate pseudo-TTY |

**Examples:**
```bash
# Run command in container
navig docker exec nginx "nginx -t"

# Check PHP version in container
navig docker exec php "php -v"

# Access MySQL in container
navig docker exec mysql "mysql -u root -p -e 'SHOW DATABASES'"
```

**Related Commands:** `navig docker logs`, `navig docker inspect`

---

### `navig docker compose <action>`

Manage Docker Compose stacks. **Use this instead of** `navig run "cd /path && docker compose up -d"`.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | Yes | Action: up, down, restart, pull, build, logs |
| `--path`, `-p` | string | No | Path to docker-compose.yml directory |
| `--service`, `-s` | string | No | Specific service name |
| `--detach`, `-d` | flag | No | Run in background (for 'up', default: true) |
| `--build` | flag | No | Build images before starting (for 'up') |

**Examples:**
```bash
# Start stack in specific directory
navig docker compose up --path /home/user/affine

# Stop stack
navig docker compose down --path /var/docker/nextcloud

# Restart specific service
navig docker compose restart --path /app --service nginx

# Pull latest images and restart
navig docker compose pull --path /app
navig docker compose up --path /app

# Build and start
navig docker compose up --path /app --build
```

**Related Commands:** `navig docker ps`, `navig docker logs`

---

### `navig docker restart <container>`

Restart a Docker container.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `container` | string | Yes | Container name or ID |
| `--yes`, `-y` | flag | No | Skip confirmation prompt |

**Examples:**
```bash
# Restart container (prompts for confirmation)
navig docker restart nginx

# Restart without confirmation
navig docker restart nginx --yes
```

**Related Commands:** `navig docker stop`, `navig docker start`

---

### `navig docker stop <container>`

Stop a Docker container.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `container` | string | Yes | Container name or ID |
| `--yes`, `-y` | flag | No | Skip confirmation prompt |

**Examples:**
```bash
# Stop container
navig docker stop nginx

# Stop without confirmation
navig docker stop nginx --yes
```

**Related Commands:** `navig docker start`, `navig docker restart`

---

### `navig docker start <container>`

Start a stopped Docker container.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `container` | string | Yes | Container name or ID |

**Examples:**
```bash
# Start container
navig docker start nginx
```

**Related Commands:** `navig docker stop`, `navig docker restart`

---

### `navig docker stats [container]`

Display container resource usage statistics.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `container` | string | No | Container name (shows all if omitted) |
| `--no-stream` | flag | No | Display once and exit (default: true) |

**Examples:**
```bash
# Show stats for all containers
navig docker stats

# Show stats for specific container
navig docker stats nginx
```

**Related Commands:** `navig docker ps`, `navig docker inspect`

---

### `navig docker inspect <container>`

Display detailed container information.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `container` | string | Yes | Container name or ID |
| `--format`, `-f` | string | No | Go template format string |

**Examples:**
```bash
# Full container details
navig docker inspect nginx

# Get specific field
navig docker inspect nginx --format '{{.State.Status}}'
```

**Related Commands:** `navig docker ps`, `navig docker logs`

---

### 📊 AI Command Optimization Guide

**Before (Complex, Error-Prone):**
```powershell
navig run "docker ps -a | grep nginx"
navig run "docker logs nginx 2>&1 | tail -50"
navig run "cd /app && docker compose up -d"
navig run "cat /var/www/config.json"
@'
cat > /var/www/config.json << 'EOF'
{"key": "value"}
EOF
'@ | navig run --stdin
```

**After (Simple, Reliable):**
```bash
navig docker ps --filter nginx
navig docker logs nginx -n 50
navig docker compose up --path /app
navig file show /var/www/config.json
navig file edit /var/www/config.json --from-file config.json
```

---

## 7. Database Operations

Execute SQL queries and manage databases.

### 7.1 Tunnel-Based Database Commands

These commands use SSH tunnel for secure database access.

#### `navig sql "<query>"`

Execute SQL query through SSH tunnel.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | SQL query to execute |

**Examples:**
```bash
# Simple query
navig sql "SELECT COUNT(*) FROM users"

# Show tables
navig sql "SHOW TABLES"

# Update data
navig sql "UPDATE users SET verified=1 WHERE email='test@example.com'"
```

**Related Commands:** `navig sqlfile`, `navig backup`

---

#### `navig sqlfile <file>`

Execute SQL file through SSH tunnel.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | path | Yes | SQL file to execute |

**Examples:**
```bash
# Run migration file
navig sqlfile migrations/001_create_tables.sql

# Run multiple statements from file
navig sqlfile schema.sql
```

**Related Commands:** `navig sql`, `navig restore`

---

#### `navig backup [path]`

Backup database to local file.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | path | No | Backup file path (default: ~/.navig/backups/<server>_<timestamp>.sql) |

**Examples:**
```bash
# Backup to default location
navig backup

# Backup to custom path
navig backup ~/backups/production-2024-12-06.sql
```

**Related Commands:** `navig restore`, `navig db-dump`

---

#### `navig restore <file>`

Restore database from backup file.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | path | Yes | Backup file to restore from |

**Examples:**
```bash
# Restore database (prompts for confirmation)
navig restore backup.sql
```

**⚠️ Warning:** This is DESTRUCTIVE. Prompts for confirmation unless `--yes` is used.

**Related Commands:** `navig backup`, `navig sqlfile`

---

### 7.2 Docker Database Commands

For databases running in Docker containers.

#### `navig db-containers`

List Docker containers running database services.

**Examples:**
```bash
navig db-containers
# Output: Lists MySQL, MariaDB, PostgreSQL containers
```

**Related Commands:** `navig db-databases`, `navig db-query`

---

#### `navig db-databases`

List all databases on the remote server.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `--container`, `-c` | string | No | Docker container name |
| `--user`, `-u` | string | No | Database user (default: root) |
| `--password`, `-p` | string | No | Database password |
| `--type`, `-t` | string | No | Database type: mysql, mariadb, postgresql |

**Examples:**
```bash
# List databases (native installation)
navig db-databases

# List databases in Docker container
navig db-databases --container mysql_db

# With custom credentials
navig db-databases -c mysql_db -u admin -p secret123
```

**Related Commands:** `navig db-containers`, `navig db-show-tables`

---

#### `navig db-show-tables <database>`

List tables in a specific database.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `database` | string | Yes | Database name |
| `--container`, `-c` | string | No | Docker container name |
| `--user`, `-u` | string | No | Database user |
| `--password`, `-p` | string | No | Database password |
| `--type`, `-t` | string | No | Database type |

**Examples:**
```bash
# Show tables in database
navig db-show-tables myapp_production

# In Docker container
navig db-show-tables mydb --container mysql_db
```

**Related Commands:** `navig db-databases`, `navig db-query`

---

#### `navig db-query "<query>"`

Execute SQL query on remote database.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | SQL query to execute |
| `--container`, `-c` | string | No | Docker container name |
| `--database`, `-d` | string | No | Database name |
| `--user`, `-u` | string | No | Database user |
| `--password`, `-p` | string | No | Database password |
| `--type`, `-t` | string | No | Database type |

**Examples:**
```bash
# Query Docker MySQL
navig db-query "SELECT * FROM users LIMIT 5" -c mysql_db -d myapp

# Query with custom credentials
navig db-query "SELECT COUNT(*) FROM orders" -c mysql_db -d shop -u admin

# Query PostgreSQL
navig db-query "SELECT * FROM products" -c postgres_db -d mydb -t postgresql
```

**Related Commands:** `navig db-databases`, `navig db-dump`

---

#### `navig db-dump <database>`

Dump/backup database to local file.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `database` | string | Yes | Database name to dump |
| `--output`, `-o` | path | No | Output file path |
| `--container`, `-c` | string | No | Docker container name |
| `--user`, `-u` | string | No | Database user |
| `--password`, `-p` | string | No | Database password |
| `--type`, `-t` | string | No | Database type |

**Examples:**
```bash
# Dump database
navig db-dump production_db --output backup-2024-12-06.sql

# Dump from Docker container
navig db-dump mydb -c mysql_db -o backup.sql

# Dump PostgreSQL
navig db-dump mydb -c postgres_db -t postgresql -o pg-backup.sql
```

**Related Commands:** `navig db-databases`, `navig backup`

---

#### `navig db-shell`

Open interactive database shell via SSH.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `--container`, `-c` | string | No | Docker container name |
| `--user`, `-u` | string | No | Database user |
| `--password`, `-p` | string | No | Database password |
| `--database`, `-d` | string | No | Database name |
| `--type`, `-t` | string | No | Database type |

**Examples:**
```bash
# Open MySQL shell
navig db-shell

# Open shell to Docker container
navig db-shell --container mysql_db

# Open PostgreSQL shell
navig db-shell -c postgres_db -t postgresql -d mydb
```

**Related Commands:** `navig db-query`, `navig db-databases`

---

### 7.3 Advanced Database Commands

#### `navig db-list`

List all databases with sizes.

**Examples:**
```bash
navig db-list
```

---

#### `navig db-tables <database>`

List tables in a database with row counts.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `database` | string | Yes | Database name |

**Examples:**
```bash
navig db-tables production_db
```

---

#### `navig db-optimize <table>`

Optimize a database table.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `table` | string | Yes | Table name to optimize |

**Examples:**
```bash
navig db-optimize users
```

---

#### `navig db-repair <table>`

Repair a database table.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `table` | string | Yes | Table name to repair |

**Examples:**
```bash
navig db-repair sessions
```

---

#### `navig db-users`

List database users.

**Examples:**
```bash
navig db-users
```

---

## 8. Service & Monitoring

Monitor server health and manage services.

### `navig logs <service>`

View service logs.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `service` | string | Yes | Service name (nginx, php-fpm, mysql, app, etc.) |
| `--tail`, `-f` | flag | No | Follow logs in real-time (like tail -f) |
| `--lines`, `-n` | int | No | Number of lines to display (default: 50) |

**Examples:**
```bash
# View last 50 lines of nginx logs
navig logs nginx

# Follow logs in real-time
navig logs nginx --tail

# View last 200 lines
navig logs php-fpm --lines 200

# View MySQL logs
navig logs mysql
```

**Common service names:** nginx, php-fpm, mysql, postgresql, redis, docker, app

**Related Commands:** `navig health`, `navig restart`

---

### `navig health`

Run comprehensive health check.

**Examples:**
```bash
navig health
```

**What it checks:**
- Disk usage
- Memory usage
- Load average
- Service status (nginx, php-fpm, mysql, etc.)
- Network connections

**Related Commands:** `navig logs`, `navig health-check`

---

### `navig restart <service>`

Restart a service.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `service` | string | Yes | Service to restart (nginx, php-fpm, mysql, app, docker, all) |

**Examples:**
```bash
# Restart nginx
navig restart nginx

# Restart PHP-FPM
navig restart php-fpm

# Restart all configured services
navig restart all
```

**Related Commands:** `navig logs`, `navig health`

---

### `navig health-check`

> **Removed — this name no longer resolves.** Use `navig host monitor show` instead.
> See [Deprecated Command Aliases](#deprecated-command-aliases) for the full migration table.

---

### `monitor-resources` (removed)

> **Removed — this name no longer resolves.** Use `navig host monitor show --resources` instead.

---

### `monitor-disk` (removed)

> **Removed — this name no longer resolves.** Use `navig host monitor show --disk` instead.
>
> The `--threshold` flag is not supported on the canonical command; configure alert thresholds in `config/defaults.yaml` under `monitoring.disk_threshold`.

---

### `monitor-services` (removed)

> **Removed — this name no longer resolves.** Use `navig host monitor show` instead (services are included in the overview output).

---

### `monitor-network` (removed)

> **Removed — this name no longer resolves.** Use `navig host monitor show --resources` instead (network stats are included in the resources panel).

---

### `monitoring-report` (removed)

> **Removed — this name no longer resolves.** Use `navig host monitor show` and redirect output, or use `navig backup run --config` for full reports.

---

## 9. SSH Tunnel Management

Manage SSH tunnels for secure database access.

### `navig tunnel start`

Start SSH tunnel for database access.

**Examples:**
```bash
navig tunnel start
```

**💡 Tip:** The tunnel is required for `navig sql`, `navig backup`, and `navig restore` commands.

**Related Commands:** `navig tunnel stop`, `navig tunnel status`

---

### `navig tunnel stop`

Stop active SSH tunnel.

**Examples:**
```bash
navig tunnel stop
```

**Related Commands:** `navig tunnel start`, `navig tunnel status`

---

### `navig tunnel restart`

Restart SSH tunnel.

**Examples:**
```bash
navig tunnel restart
```

**💡 Tip:** Use this if tunnel connection becomes unstable.

**Related Commands:** `navig tunnel start`, `navig tunnel stop`

---

### `navig tunnel status`

Show tunnel status (PID, uptime, port).

**Examples:**
```bash
navig tunnel status
```

**Related Commands:** `navig tunnel start`, `navig tunnel restart`

---

### `navig tunnel auto`

Auto-start tunnel if needed, auto-stop when done.

**Examples:**
```bash
navig tunnel auto
```

**Related Commands:** `navig tunnel start`, `navig tunnel status`

---

## 10. Security Commands

Firewall, Fail2Ban, and security auditing.

### `navig firewall-status`

Display UFW firewall status and rules.

**Examples:**
```bash
navig firewall-status
```

**Related Commands:** `navig firewall-add`, `navig firewall-enable`

---

### `navig firewall-add <port>`

Add UFW firewall rule.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `port` | int | Yes | Port number |
| `--protocol`, `-p` | string | No | Protocol: tcp/udp (default: tcp) |
| `--from` | string | No | IP address or subnet (default: any) |

**Examples:**
```bash
# Allow port 8080
navig firewall-add 8080

# Allow port 3000 from specific IP
navig firewall-add 3000 --from 10.0.0.10

# Allow UDP port
navig firewall-add 53 --protocol udp
```

**Related Commands:** `navig firewall-remove`, `navig firewall-status`

---

### `navig firewall-remove <port>`

Remove UFW firewall rule.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `port` | int | Yes | Port number |
| `--protocol`, `-p` | string | No | Protocol: tcp/udp (default: tcp) |

**Examples:**
```bash
navig firewall-remove 8080
```

**Related Commands:** `navig firewall-add`, `navig firewall-status`

---

### `navig firewall-enable`

Enable UFW firewall.

**Examples:**
```bash
navig firewall-enable
```

**Related Commands:** `navig firewall-disable`, `navig firewall-status`

---

### `navig firewall-disable`

Disable UFW firewall.

**Examples:**
```bash
navig firewall-disable
```

**⚠️ Warning:** This disables all firewall protection.

**Related Commands:** `navig firewall-enable`, `navig firewall-status`

---

### `navig fail2ban-status`

Display Fail2Ban status and banned IPs.

**Examples:**
```bash
navig fail2ban-status
```

**Related Commands:** `navig host security show`, `navig apply security-audit`

---

### `navig fail2ban-unban <ip>`

Unban IP address from Fail2Ban.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ip` | string | Yes | IP address to unban |
| `--jail`, `-j` | string | No | Jail name (default: all jails) |

**Examples:**
```bash
# Unban from all jails
navig fail2ban-unban 10.0.0.10

# Unban from specific jail
navig fail2ban-unban 10.0.0.10 --jail sshd
```

**Related Commands:** `navig fail2ban-status`

---

### `navig ssh-audit`

Audit SSH configuration for security issues.

**Examples:**
```bash
navig ssh-audit
```

**Related Commands:** `navig apply security-audit`, `navig host maintenance`

---

### `security-updates` (removed)

> **Removed — this name no longer resolves.** Check updates through the host's maintenance
> surface, or run the package manager directly.

```bash
navig host maintenance
navig run "apt list --upgradable"
```

---

### `navig audit-connections`

Audit active network connections.

**Examples:**
```bash
navig audit-connections
```

**Related Commands:** `navig host monitor show --resources`, `navig apply security-audit`

---

### `security-scan` (removed)

> **Removed — this name no longer resolves.** The guided security analysis ships as a
> **Block**; the host's own settings are under `navig host security`.

```bash
navig apply security-audit --dry-run
navig apply security-audit
navig host security show
```

---

## 11. Local System Management

NAVIG can manage your **local machine** with the same commands used for remote hosts. This enables unified workflows across local and remote environments.

### Switching to Local Machine

Use the `local` host to execute commands on your local machine instead of a remote server.

#### `navig host use local`

Switch execution context to the local machine.

**Examples:**
```bash
# Switch to local machine
navig host use local
# ✓ Switched to host: local

# Now run commands locally
navig run "echo Hello from local machine"
# Hello from local machine

# Check system info locally
navig run "hostname"
```

**Notes:**
- The `local` host is auto-created on first NAVIG run
- Configuration stored at `~/.navig/hosts/local.yaml`
- Uses subprocess execution instead of SSH

---

### System Hosts File Management

View and edit the system hosts file (`/etc/hosts` or `C:\Windows\System32\drivers\etc\hosts`).

#### `navig hosts view`

Display the system hosts file with syntax highlighting (read-only).

**Examples:**
```bash
navig hosts view
```

**Output:**
```
127.0.0.1       localhost
127.0.0.1       my-dev-site.local
10.0.0.10   production-db.internal
```

---

#### `navig hosts edit`

Open the system hosts file in the default text editor.

**Examples:**
```bash
navig hosts edit
```

**⚠️ Requirements:**
- **Windows:** Must run terminal as Administrator
- **Linux/macOS:** Must run with `sudo` or as root

**Error Handling:**
```
❌ Administrator privileges required
Please restart your terminal as Administrator and try again.

Windows: Right-click terminal → "Run as Administrator"
Linux/macOS: Use 'sudo navig hosts edit' or run terminal as root
```

---

### Installed Software Management

List and analyze installed software packages on the local machine.

#### `navig software list`

List installed packages with package name and version.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `--limit`, `-l` | int | No | Limit number of results |
| `--format` | string | No | Output format: `table` (default) or `json` |

**Examples:**
```bash
# List all packages
navig software list

# Limit to 50 results
navig software list --limit 50

# Output as JSON
navig software list --format json
```

**Output (Table):**
```
┌─────────────────────┬──────────┐
│ Package Name        │ Version  │
├─────────────────────┼──────────┤
│ Google Chrome       │ 120.0.1  │
│ Python              │ 3.12.4   │
│ Visual Studio Code  │ 1.85.0   │
└─────────────────────┴──────────┘
```

**Output (JSON):**
```json
[
  {"name": "Google Chrome", "version": "120.0.1"},
  {"name": "Python", "version": "3.12.4"},
  {"name": "Visual Studio Code", "version": "1.85.0"}
]
```

**Package Managers by OS:**
| OS | Package Manager | Command Used |
|----|-----------------|--------------|
| Windows | winget | `winget list` |
| Debian/Ubuntu | dpkg | `dpkg -l` |
| RHEL/Fedora | rpm | `rpm -qa` |
| macOS | Homebrew | `brew list --versions` |

---

### Local Security Audit

#### `navig apply security-audit`

Run a guided security analysis. There is no `security` group — the audit ships as a
**Block**, and the host's own security settings are under `navig host security`.

**Examples:**
```bash
# Run the audit block (preview first if you like)
navig apply security-audit --dry-run
navig apply security-audit

# Inspect the host's security configuration
navig host security show
```

**Checks Performed:**
- Firewall status and configuration
- Open network ports
- User accounts with login shells
- SSH configuration weaknesses
- World-writable files and directories
- Admin/root privilege detection

**Output:**
```
╔══════════════════════════════════════════════════════════╗
║ Security Audit Report - 15 packages analyzed            ║
╚══════════════════════════════════════════════════════════╝

🔴 CRITICAL (1 issue)
┌──────────────────────────────────────────────────────────┐
│ Package: OpenSSL 1.0.2                                   │
│ Issue: CVE-20XX-XXXX - Remote code execution            │
│ Action: Update to OpenSSL 3.0.12 immediately            │
└──────────────────────────────────────────────────────────┘

🟡 MEDIUM (2 issues)
┌──────────────────────────────────────────────────────────┐
│ Package: Python 3.8.5                                    │
│ Issue: End of life - no security updates               │
│ Action: Upgrade to Python 3.12+                         │
└──────────────────────────────────────────────────────────┘
```

**Requirements:**
- Requires AI API key configured (`navig ai config`)
- Uses the configured AI model (GPT-4, Claude, etc.)

---

### Cross-Platform Support

| Feature | Windows | Linux | macOS |
|---------|---------|-------|-------|
| Package Manager | winget | apt/yum/dnf | brew |
| Hosts File | `%SYSTEMROOT%\System32\drivers\etc\hosts` | `/etc/hosts` | `/etc/hosts` |
| Admin Detection | `IsUserAnAdmin()` | `os.geteuid() == 0` | `os.geteuid() == 0` |
| Default Editor | notepad | nano/vim | open -e |
| Firewall Check | `netsh advfirewall` | `ufw/firewall-cmd` | `/usr/libexec/ApplicationFirewall` |

---

### Architecture Overview

NAVIG uses a **ConnectionAdapter** pattern for unified command execution:

```
┌─────────────────────────────────────────────────────────┐
│                   ConnectionAdapter                      │
│  ┌──────────────────┐    ┌──────────────────┐          │
│  │ LocalConnection  │    │  SSHConnection   │          │
│  │ (subprocess)     │    │  (paramiko/ssh)  │          │
│  └──────────────────┘    └──────────────────┘          │
└─────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│                     OSAdapter                            │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐        │
│  │  Windows   │  │   Linux    │  │   macOS    │        │
│  │  Adapter   │  │   Adapter  │  │   Adapter  │        │
│  └────────────┘  └────────────┘  └────────────┘        │
└─────────────────────────────────────────────────────────┘
```

**Key Files:**
- `navig/core/connection.py` - ConnectionAdapter, LocalConnection, SSHConnection
- `navig/adapters/os/` - OSAdapter base + Windows/Linux/macOS implementations
- `navig/local_operations.py` - LocalMachine unified operations class
- `navig/commands/local.py` - CLI commands for local management

---

## 12. System Maintenance

System maintenance and cleanup tasks.

### `navig update-packages`

Update package lists and upgrade packages.

**Examples:**
```bash
navig update-packages
```

**Related Commands:** `navig host maintenance`, `navig apply security-audit`

---

### `navig clean-packages`

Clean package cache and remove orphaned packages.

**Examples:**
```bash
navig clean-packages
```

**Related Commands:** `navig update-packages`, `navig cleanup-temp`

---

### `navig rotate-logs`

Rotate and compress log files.

**Examples:**
```bash
navig rotate-logs
```

**Related Commands:** `navig cleanup-temp`, `navig check-filesystem`

---

### `navig cleanup-temp`

Clean temporary files and caches.

**Examples:**
```bash
navig cleanup-temp
```

**Related Commands:** `navig rotate-logs`, `navig check-filesystem`

---

### `navig check-filesystem`

Check filesystem usage and find large files.

**Examples:**
```bash
navig check-filesystem
```

**Related Commands:** `navig host monitor show --disk`, `navig host maintenance`

---

### `navig system-maintenance`

Run comprehensive system maintenance (all tasks).

**Examples:**
```bash
navig system-maintenance
```

**What it does:**
- Updates packages
- Cleans package cache
- Rotates logs
- Cleans temp files
- Checks filesystem

**Related Commands:** All maintenance commands above

---

## 13. Template & Addon Management

Manage application templates and server configurations.

### `navig flow template list`

List all available templates.

**Examples:**
```bash
navig flow template list
```

**Available templates:** nginx, docker, postgresql, mysql, redis, caddy, traefik, nextcloud, gitea, portainer, grafana, prometheus, etc.

**Related Commands:** `navig flow template add`, `navig flow template show`

---

### `navig flow template add <name>`

Enable a template.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Template name to enable |

**Examples:**
```bash
navig flow template add nginx
navig flow template add postgresql
```

**Related Commands:** `navig flow template remove`, `navig flow template list`

---

### `navig flow template remove <name>`

Disable a template.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Template name to disable |

**Examples:**
```bash
navig flow template remove redis
```

**Related Commands:** `navig flow template add`, `navig flow template list`

---

### `navig flow template show <name>`

Show detailed information about a template.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Template name |

**Examples:**
```bash
navig flow template show nginx
```

**Related Commands:** `navig flow template list`, `navig flow template add`

---

### Editing and checking templates

`navig flow template` has five verbs — `list`, `show`, `add`, `remove`, `run`. There is no
`edit` and no `validate`.

```bash
# Inspect what a template declares
navig flow template show nginx

# Re-add it after changing its files on disk
navig flow template remove nginx
navig flow template add nginx
```

A template is a `template.yaml` / `template.json` on disk; edit that file directly. If it
cannot be parsed, `navig flow template list` reports it as skipped rather than failing.

---

## 14. Execution Modes & Confirmation

Control when NAVIG prompts for confirmation before executing potentially dangerous operations.

### Execution Modes

NAVIG supports two execution modes:

| Mode | Description |
|------|-------------|
| **interactive** (default) | Prompts for confirmation based on confirmation level |
| **auto** | Skips all confirmations (for scripts/automation) |

### Confirmation Levels

Three levels determine which operations require confirmation in interactive mode:

| Level | Confirms | Examples |
|-------|----------|----------|
| **critical** | Only destructive operations | `rm -rf`, `DROP TABLE`, `TRUNCATE`, `reboot` |
| **standard** (default) | Critical + modify operations | `UPDATE`, `INSERT`, file uploads, service restarts |
| **verbose** | All remote operations | `SELECT`, `ls`, `cat`, `grep` |

### `navig config settings`

Display current execution mode and confirmation level settings.

**Examples:**
```bash
navig config settings
```

**Output:**
```
╭──────────────────────────────────────────╮
│           NAVIG Settings                  │
├──────────────────────────────────────────┤
│  Execution Mode: interactive              │
│  Confirmation Level: standard             │
╰──────────────────────────────────────────╯
```

---

### `navig config set-mode <mode>`

Set the default execution mode.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `mode` | string | Yes | Mode: `interactive` or `auto` |

**Examples:**
```bash
# Enable auto mode for scripts
navig config set-mode auto

# Return to interactive mode
navig config set-mode interactive
```

**⚠️ Warning:** Auto mode bypasses all safety confirmations. Use with caution.

---

### `navig config set-confirmation-level <level>`

Set the confirmation level for interactive mode.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `level` | string | Yes | Level: `critical`, `standard`, or `verbose` |

**Examples:**
```bash
# Only confirm destructive operations
navig config set-confirmation-level critical

# Confirm everything (learning/audit mode)
navig config set-confirmation-level verbose

# Default behavior
navig config set-confirmation-level standard
```

---

### CLI Flags for Confirmation Control

| Flag | Description |
|------|-------------|
| `--yes`, `-y` | Auto-confirm for a single command (bypass configured level) |
| `--confirm`, `-c` | Force confirmation prompt even in auto mode |

**Examples:**
```bash
# Auto-confirm single command
navig -y run "rm /var/log/app/*.log"

# Force confirmation in auto mode
navig -c sql "DROP TABLE old_data"
```

---

### Operation Classification

NAVIG automatically classifies operations:

**Critical Operations (always prompted at critical+ levels):**
- Shell: `rm -rf`, `rmdir`, `dd`, `mkfs`, `shutdown`, `reboot`, `kill -9`
- SQL: `DROP`, `TRUNCATE`, `DELETE` (without WHERE), `ALTER TABLE DROP`

**Standard Operations (prompted at standard+ levels):**
- Shell: `mv`, `cp`, `chmod`, `chown`, service commands
- SQL: `UPDATE`, `INSERT`, `CREATE`, `ALTER`
- File: uploads, modifications

**Verbose Operations (only prompted at verbose level):**
- Shell: `ls`, `cat`, `grep`, `find`, `tail`, `head`
- SQL: `SELECT`, `SHOW`, `DESCRIBE`
- File: downloads, listings

---

## 15. Configuration Backup & Export

Backup, export, and share NAVIG configuration between machines.

### `navig backup export`

Export NAVIG configuration to a backup file.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `--output`, `-o` | path | No | Output file path (default: auto-timestamped) |
| `--format`, `-f` | string | No | Format: `json` (default) or `archive` (.tar.gz) |
| `--encrypt`, `-e` | flag | No | Encrypt with password (AES-256) |
| `--include-secrets` | flag | No | Include actual secrets (default: redacted) |
| `--hosts-only` | flag | No | Export only host configurations |
| `--apps-only` | flag | No | Export only application configurations |

**Examples:**
```bash
# Basic export (secrets redacted for safety)
navig backup export

# Export with encryption
navig backup export --encrypt --format archive

# Export to specific location
navig backup export --output ~/backups/navig-prod.json

# Export with secrets (use with caution!)
navig backup export --include-secrets --encrypt
```

**💡 Tip:** Secrets are automatically redacted by default for safe sharing.

---

### `navig backup import`

Import NAVIG configuration from a backup file.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | path | Yes | Path to backup file |
| `--overwrite` | flag | No | Overwrite existing configs (default: merge) |
| `--password`, `-p` | string | No | Password for encrypted backups |
| `--dry-run` | flag | No | Preview import without changes |
| `--hosts-only` | flag | No | Import only host configurations |
| `--apps-only` | flag | No | Import only application configurations |

**Examples:**
```bash
# Import with merge (keeps existing, adds new)
navig backup import navig-export-2025-01-06.json

# Import with overwrite (replaces existing)
navig backup import backup.json --overwrite

# Import encrypted backup
navig backup import backup.tar.gz.enc --password mypassword

# Preview before importing
navig backup import backup.json --dry-run
```

---

### `navig backup list`

List available backup files.

**Examples:**
```bash
navig backup list
```

**Output:**
```
╭─────────────────────────────────────────────────────────╮
│                  Available Backups                       │
├─────────────────────────────────────────────────────────┤
│ navig-export-2025-01-06-143022.json       2.3 KB  Today │
│ navig-export-2025-01-05-091545.tar.gz     1.8 KB  1 day │
│ navig-export-2025-01-04-160302.tar.gz.enc 2.1 KB  2 days│
╰─────────────────────────────────────────────────────────╯
```

---

### `navig backup inspect`

Preview backup contents without importing.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | path | Yes | Path to backup file |
| `--password`, `-p` | string | No | Password for encrypted backups |
| `--json` | flag | No | Output in JSON format |

**Examples:**
```bash
# Inspect backup contents
navig backup inspect navig-export-2025-01-06.json

# Inspect encrypted backup
navig backup inspect backup.enc --password mypassword
```

---

### `navig backup delete`

Delete a backup file.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | path | Yes | Backup file to delete |

**Examples:**
```bash
navig backup delete navig-export-old.json
```

---

### Security Features

**Automatic Secret Redaction:**
By default, sensitive fields are replaced with `[REDACTED]`:
- `password`, `secret`, `key`, `token`
- `api_key`, `apikey`, `api_secret`
- `private_key`, `ssh_key`, `credential`

**Encryption:**
When using `--encrypt`:
- AES-256 encryption via Fernet
- Password-derived key using PBKDF2
- Encrypted files have `.enc` extension

---

### Common Backup Workflows

**Backup Before Changes:**
```bash
navig backup export
# edit ~/.navig/hosts/production.yaml
# If something goes wrong:
navig backup import ~/.navig/exports/navig-export-*.json --overwrite
```

**Share Config with Team:**
```bash
# Export without secrets (safe to share)
navig backup export --output ~/shared-config.json
# Team member imports
navig backup import ~/shared-config.json
```

**Migrate to New Machine:**
```bash
# On old machine
navig backup export --encrypt --include-secrets
# Transfer file, then on new machine
navig backup import backup.enc --password mypassword
```

---

## 16. Common Workflows

Step-by-step command sequences for common tasks.

### Workflow: Initial Server Setup

```bash
# 1. Add new host
navig host add production

# 2. Set as active
navig host use production

# 3. Auto-discover server details
navig host show --inspect

# 4. Test connection
navig host test

# 5. View discovered info
navig host show
```

---

### Workflow: Deploy Application Files

```bash
# 1. Ensure correct host is active
navig host use production

# 2. Backup current files (optional)
navig run "cp -r /var/www/html /var/www/html.backup.$(date +%Y%m%d)"

# 3. Upload new files
navig file add ./dist /var/www/html

# 4. Set permissions
navig run "chown -R www-data:www-data /var/www/html"

# 5. Clear caches (Laravel example)
navig run "cd /var/www/html && php artisan cache:clear"
navig run "php artisan config:cache"

# 6. Restart services
navig restart php-fpm
navig restart nginx
```

---

### Workflow: Create Config File with JSON (Escaping-Safe)

```bash
# OPTION 1: Upload local file (RECOMMENDED)
# Create config.json locally, then:
navig file add config.json /var/www/app/config.json

# OPTION 2: Use --stdin with PowerShell here-string
@'
cat > /var/www/app/config.json << 'EOF'
{
  "api_key": "sk-12345",
  "database": {
    "host": "localhost",
    "port": 3306
  }
}
EOF
'@ | navig run --stdin

# OPTION 3: Use --file
# Create deploy-config.sh locally, then:
navig run --file deploy-config.sh
```

---

### Workflow: Database Backup and Restore

```bash
# 1. Create backup
navig backup ~/backups/production-$(date +%Y%m%d).sql

# 2. Verify backup exists
ls -lh ~/backups/

# 3. Restore (if needed) - PROMPTS FOR CONFIRMATION
navig restore ~/backups/production-20241206.sql
```

---

### Workflow: Troubleshoot Slow Website

```bash
# 1. Check server health
navig health

# 2. Check disk space
navig run "df -h"

# 3. Check memory usage
navig run "free -h"

# 4. View recent nginx errors
navig logs nginx --lines 100

# 5. View PHP-FPM logs
navig logs php-fpm --lines 100

# 6. Check service status
navig host monitor show

# 7. Check network connections
navig audit-connections

# 8. Restart services if needed
navig restart php-fpm
navig restart nginx
```

---

### Workflow: Security Audit

```bash
# 1. Run comprehensive security scan
navig security-scan

# 2. Check firewall status
navig firewall-status

# 3. Check Fail2Ban status
navig fail2ban-status

# 4. Audit SSH configuration
navig ssh-audit

# 5. Check for security updates
navig security-updates

# 6. Audit network connections
navig audit-connections
```

---

### Workflow: Docker Database Operations

```bash
# 1. List database containers
navig db-containers

# 2. List databases in container
navig db-databases --container mysql_db

# 3. Show tables
navig db-show-tables myapp_db --container mysql_db

# 4. Run query
navig db-query "SELECT COUNT(*) FROM users" -c mysql_db -d myapp_db

# 5. Backup database
navig db-dump myapp_db -c mysql_db -o backup.sql

# 6. Open interactive shell
navig db-shell --container mysql_db
```

---

## 17. Troubleshooting Guide

Common issues and solutions.

### Start here: `navig doctor` (self-diagnostics)

Run the built-in health report before debugging anything by hand. It checks config, storage,
vault, sockets, formations, skills, the gateway (including the event processor), AI providers,
and plugin/module wiring — read-only, no state mutated.

```bash
# Full health report (issues only; passing sections summarize to one line)
navig doctor

# Show every check, including passing ones
navig doctor --verbose

# Machine-readable report for scripts and agents — always includes EVERY check
# (--verbose semantics), plain text only (no colors/glyphs), never prompts.
# Exit code matches the human mode: 0 only when every check passes.
navig doctor --json

# Probe a specific gateway port / skip Python dependency checks
navig doctor --port 8789 --skip-deps
```

`--json` prints exactly one JSON document on stdout:

```json
{
  "ok": false,
  "sections": [
    {"name": "Storage", "ok": true, "checks": [
      {"label": "Vault", "ok": true, "warn": false, "detail": "2 item(s) · encryption OK"}
    ]}
  ],
  "summary": {"passed": 24, "warnings": 2, "failed": 1},
  "version": "2.9.1",
  "generated_at": "2026-07-15T12:00:00+00:00"
}
```

Row states: `ok=true` = verified healthy (✓) · `ok=false, warn=true` = warning (⚠) ·
`ok=false, warn=false` = failure (✗). Warnings flip the exit code to 1 just like failures —
in this report a ✓ means *verified*, never "could not check".

### Fix what doctor found: `navig doctor --heal`

Close the observe→repair loop: `--heal` maps failing checks onto fixes that already exist in
navig and runs the **safe** ones, then re-checks and shows before/after.

```bash
# Preview: list failing checks and what WOULD be fixed — executes nothing
navig doctor --heal --dry-run

# Apply the safe fixes, re-check, print before/after
navig doctor --heal

# Machine-readable heal run (actions + final report in one JSON document)
navig doctor --heal --json
```

What auto-heals (safe, additive):
- **Gateway not running / MESH_TOKEN unset** → starts the daemon through the normal
  `navig service start` path — a daemon you stopped deliberately stays stopped.
- **Legacy credentials unmigrated** → runs the vault migration (legacy DB is read-only,
  existing items are skipped).

What stays **report-only** (disruptive — you pull the trigger, `--heal` prints the command):
- a wedged event processor or a stale lighthouse webhook tenant → `navig service restart`;
- leaked debug browsers → `navig cdp stop --all`;
- a PATH near the Windows command-length ceiling with reclaimable entries →
  `navig doctor clean-path` (rewriting your persistent PATH is a machine-wide change,
  so it is never automatic).

Anything else failing is listed as "no automatic remediation". Exit code: 0 only when the
final (post-heal) report is fully green — same parity rule as plain `navig doctor`.

### Windows: `PATH health` and `navig doctor clean-path`

On Windows, `cmd.exe` truncates PATH at **8191 characters**. Past that, commands resolved
through a shell fail with `'x' is not recognized` — naming a tool that *is* installed and
whose directory *is* on PATH. The misdirection is the expensive part: the error sends you
reinstalling dependencies instead of looking at the environment.

The **PATH health** row (Runtime section) reports two different things, measured on two
different paths on purpose. **Headroom** comes from the PATH the current process holds —
that is the string a shell actually resolves against, so it is what breaks. **Reclaimable
space** comes from your persistent *user* PATH, because that is the only one `clean-path`
can rewrite: a shell can inject entries into its own environment that no command can remove,
and reporting those as reclaimable would send you to a cleanup that answers "already clean".

It warns while there is still room to act — nested tooling adds roughly
1300 characters on its own, so a PATH sitting a few hundred under the ceiling is already
one `npm run` chain away from breaking:

```
⚠ PATH health: PATH is 7113 chars — only 1078 under cmd.exe's 8191 limit, and nested
  tooling adds ~1300; 80 missing + 12 duplicate entries hold 4312 chars
  (reclaim: navig doctor clean-path)
```

`clean-path` reclaims exactly that space. It removes **only** directories that do not exist
and exact duplicates — neither can affect which tool a command resolves to, so nothing you
actually use can disappear:

```bash
# Preview: table of every entry that would go, and why — writes nothing
navig doctor clean-path

# Apply it (backs the old value up first, then rewrites the user PATH)
navig doctor clean-path --apply

# Machine-readable
navig doctor clean-path --json
```

Notes:
- **The previous value is saved** to `~/.navig/backups/path-user-<timestamp>.txt` before any
  write. To roll back, paste that value back into your user PATH.
- Only your **user** PATH is touched. The machine-wide PATH needs administrator rights and
  is not navig's to rewrite.
- A prune that would empty PATH is **refused** — that means the read went wrong, not that
  the machine has no PATH.
- Open a **new** terminal afterwards; already-running shells keep the PATH they started with.
- Windows only. The 8191 ceiling is a `cmd.exe` property, so on macOS and Linux the row is
  not shown and the command declines to run.

If dead entries keep coming back, something on the machine is appending temp directories to
your persistent PATH and never removing them — re-running `clean-path` is safe and
idempotent, and the doctor row will tell you when it has grown back.

---

### Issue: "No active host"

**Cause:** No host is currently selected.

**Solution:**
```bash
# List available hosts
navig host list

# Set active host
navig host use production
```

---

### Issue: "SSH Connection Failed"

**Causes & Solutions:**

```bash
# 1. Test SSH connection
navig host test

# 2. Verify SSH key path
navig host show

# 3. Check SSH key permissions (should be 600)
chmod 600 ~/.ssh/id_rsa

# 4. Test manual SSH connection
ssh -i ~/.ssh/id_rsa user@hostname

# 5. Check if host is reachable
ping hostname
```

---

### Issue: "Tunnel Connection Failed"

**Solution:**
```bash
# 1. Check tunnel status
navig tunnel status

# 2. Stop and restart tunnel
navig tunnel stop
navig tunnel start

# 3. Check for port conflicts
navig run "netstat -tuln | grep 3306"

# 4. Try with verbose logging
navig --verbose tunnel start
```

---

### Issue: "SQL Query Failed"

**Solution:**
```bash
# 1. Check tunnel is running
navig tunnel status

# 2. Test basic query
navig sql "SELECT 1"

# 3. Check database exists
navig sql "SHOW DATABASES"

# 4. Verify credentials in host config
navig host show

# 5. Enable verbose mode
navig --verbose sql "YOUR_QUERY"
```

---

### Issue: "Command Parsing Error" / "Heredoc/JSON Escaping Issues"

**Symptoms:**
```
Got unexpected extra arguments (\\: \https://... \server\: ...)
```

**Root Cause:**
PowerShell → Python CLI → SSH command chain has multiple escaping layers that interpret quotes and special characters differently.

**Solutions:**

See [Section 4.2: Complex Commands](#42--complex-commands-heredocs-json-special-characters) for detailed solutions.

**Quick Fix:**
```powershell
# Instead of:
navig run "cat > config.json << 'EOF' {...} EOF"

# Use:
@'
cat > config.json << 'EOF'
{"key": "value"}
EOF
'@ | navig run --stdin

# Or upload the file:
navig file add config.json /var/www/config.json
```

---

### Issue: "Permission Denied"

**Solution:**
```bash
# Check current user
navig run "whoami"

# Check file ownership
navig run "ls -l /path/to/file"

# Fix ownership
navig file edit /path/to/file --owner www-data:www-data

# Fix permissions
navig file edit /path/to/directory --mode 755
navig file edit /path/to/file --mode 644
```

---

### Issue: "File Upload Failed"

**Solution:**
```bash
# 1. Check SSH connection
navig run "pwd"

# 2. Verify remote path exists
navig run "ls -la /remote/path"

# 3. Check permissions
navig run "ls -ld /remote/path"

# 4. Check disk space
navig run "df -h"

# 5. Try with explicit full path
navig file add local.txt /full/remote/path/local.txt
```

---

## 18. Configuration Reference

### Validate Configuration

Validate your YAML configuration files and get file+line error messages:

```bash
# Validate the current project's .navig/ (default when present)
navig config validate

# Validate only global config (~/.navig)
navig config validate --scope global

# Validate both global + project
navig config validate --scope both

# Treat warnings as errors
navig config validate --strict

# Machine-readable output
navig config validate --json
```

---

### VS Code Schema Integration (YAML)

Install JSON Schemas for `hosts/*.yaml` and `apps/*.yaml`:

```bash
# Copy schemas into ~/.navig/schemas/
navig config schema install --scope global

# (Optional) write `.vscode/settings.json` yaml.schemas mappings
navig config schema install --write-vscode-settings
```

---

### Host/App Selection Priority

NAVIG uses a hierarchical system to determine the active host and app:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    HOST RESOLUTION ORDER                             │
├─────────────────────────────────────────────────────────────────────┤
│ 1. NAVIG_ACTIVE_HOST env var     ← For CI/CD and scripting          │
│          ↓ (not set)                                                 │
│ 2. .navig/config.yaml:active_host ← Project-local preference        │
│          ↓ (not found)                                               │
│ 3. ~/.navig/cache/active_host.txt ← Global cache (navig host use)   │
│          ↓ (not found)                                               │
│ 4. default_host from global config ← Fallback                       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                    APP RESOLUTION ORDER                              │
├─────────────────────────────────────────────────────────────────────┤
│ 1. NAVIG_ACTIVE_APP env var      ← For CI/CD and scripting          │
│          ↓ (not set)                                                 │
│ 2. .navig/config.yaml:active_app ← Project-local preference         │
│          ↓ (not found)                                               │
│ 3. ~/.navig/cache/active_app.txt ← Global cache (navig app use)     │
│          ↓ (not found)                                               │
│ 4. default_app from host config  ← Host-level default               │
└─────────────────────────────────────────────────────────────────────┘
```

---

### Project-Local Config: `.navig/config.yaml`

This file can be committed to git and shared with your team:

```yaml
# Project-local NAVIG configuration

# Active host for this project (references ~/.navig/hosts/<name>.yaml)
active_host: production

# Active app for this project (must exist on active_host)
active_app: my-app

# Project metadata
app:
  name: my-project
  initialized: '2025-01-01T00:00:00'
  version: '1.0'

# Execution settings (can override global)
execution:
  mode: interactive  # or 'auto' for scripts
```

**Use Cases:**
- **Multi-project developers:** Each project auto-uses its own host
- **Team sharing:** Commit this file to git; each team member defines the host in their personal `~/.navig/hosts/`
- **CI/CD pipelines:** Override with `NAVIG_ACTIVE_HOST` env var

---

### Environment Variables (For CI/CD and Scripting)

| Variable | Description |
|----------|-------------|
| `NAVIG_ACTIVE_HOST` | Override active host (highest priority) |
| `NAVIG_ACTIVE_APP` | Override active app (highest priority) |
| `NAVIG_CONFIG_DIR` | Override config/log base directory (default: `~/.navig`) |
| `NAVIG_MESSAGING_PROVIDER` | Override active messaging provider (`telegram` or `none`) |

**Usage:**
```powershell
# PowerShell
$env:NAVIG_ACTIVE_HOST = 'production'
$env:NAVIG_ACTIVE_APP = 'my-app'

# Bash/Zsh
export NAVIG_ACTIVE_HOST='production'
export NAVIG_ACTIVE_APP='my-app'
```

**When to use:**
- CI/CD pipelines where you can't rely on project config
- Scripting that needs to target a specific host
- Testing commands against different hosts without changing global state

---

### Messaging Provider (Canonical)

```yaml
messaging:
  provider: telegram  # telegram | none
```

Resolution order for active provider:
1. `NAVIG_MESSAGING_PROVIDER` environment variable
2. `messaging.provider` in config
3. Default: `telegram` (backward-compatible)

Telegram token resolution remains vault-first (with compatibility fallback):
1. Vault v2 labels (`telegram/bot_token`, `telegram/token`, ...)
2. Vault v1 (`telegram` token/bot_token)
3. `TELEGRAM_BOT_TOKEN` env var
4. `telegram.bot_token` in config

---

### Global Config: `~/.navig/config.yaml`

```yaml
# Default host when no other selection exists
default_host: "production"

# OpenRouter API key for AI features
openrouter_api_key: "sk-or-v1-..."

# Logging level
log_level: "INFO"  # DEBUG, INFO, WARNING, ERROR

# AI model preferences (fallback chain)
ai_model_preference:
  - "deepseek/deepseek-coder-33b-instruct"
  - "google/gemini-flash-1.5"
  - "qwen/qwen-2.5-72b-instruct"

# Tunnel settings
tunnel_auto_cleanup: true
tunnel_port_range: [3307, 3399]

# Debug logging
debug_log: false
debug_log_path: null  # Default: .navig/debug.log
debug_log_max_size_mb: 10
debug_log_max_files: 5
```

---

### Host Config: `~/.navig/hosts/<host-name>.yaml`

```yaml
name: "production"
host: "10.0.0.10"
port: 22
user: "deploy"

# Authentication (use ONE method)
ssh_key: "~/.ssh/id_rsa"  # Recommended
ssh_password: null        # Or password if no key

database:
  type: "mysql"           # mysql or postgresql
  remote_port: 3306
  local_tunnel_port: 3307
  name: "myapp_db"
  user: "myapp_user"
  password: "secure_password"

paths:
  web_root: "/var/www/html"
  logs: "/var/log/nginx"
  php_config: "/etc/php/8.3/fpm"
  nginx_config: "/etc/nginx/sites-available"
  app_storage: "/var/www/html/storage"

services:
  web: "nginx"
  php: "php8.3-fpm"
  database: "mysql"
  cache: "redis-server"

metadata:
  os: "Ubuntu 24.04"
  php_version: "8.4.8"
  mysql_version: "8.0.35"
  last_inspected: "2025-12-06T10:00:00Z"
```

---

### App Marker: `.navig` (in app root)

Create a `.navig` file in your project directory containing just the host name:

```
production
```

NAVIG auto-uses this host when running commands from that directory.

---

## 19. Global Options

### Machine Output

Most commands accept `--json` (global flag) and some commands also provide a command-local `--json` flag.
When JSON output is enabled, NAVIG emits a single JSON object with a stable envelope:

- `schema_version`: JSON contract version (currently `1.0.0`)
- `command`: logical command name (e.g. `host.list`, `db.list`, `file.show`)
- `success`: boolean

This is designed to be AI- and automation-friendly.

### Cache Control

NAVIG uses small JSON caches under `~/.navig/cache/` to speed up discovery-style operations.

- `--no-cache`: bypass caches for the current run

Optional TTL settings can be added to global config (`~/.navig/config.yaml`):

```yaml
cache_ttl:
  host_discovery_seconds: 300
  templates_seconds: 3600
  ssh_keys_seconds: 300
```

### Short Aliases

NAVIG includes a few short aliases for faster interactive use:

- `navig h` → `navig host`
- `navig a` → `navig app`
- `navig f` → `navig file`
- `navig t` → `navig tunnel`
- `navig r` → `navig run`

Available on all commands.

| Option | Description |
|--------|-------------|
| `--host`, `-h <name>` | Override active host |
| `--app`, `-p <name>` | Override active app |
| `--verbose` | Detailed logging |
| `--quiet`, `-q` | Minimal output (errors only) |
| `--dry-run` | Show actions without executing |
| `--yes`, `-y` | Auto-confirm prompts (bypass confirmation) |
| `--confirm`, `-c` | Force confirmation prompt (even in auto mode) |
| `--raw` | Plain text output (no Rich formatting - for scripts) |
| `--json` | Output in JSON format (for automation) |
| `--debug-log` | Enable debug logging to .navig/debug.log |
| `--no-cache` | Disable local caches for this run |

**Examples:**
```bash
# Override host for single command
navig --host staging run "ls -la"

# Dry run to preview actions
navig --dry-run restore backup.sql

# Raw output for scripting
navig --raw sql "SELECT COUNT(*) FROM users"

# Verbose logging for debugging
navig --verbose tunnel start

# Auto-confirm a single command
navig -y run "rm -rf /var/cache/*"

# Force confirmation in auto mode
navig -c sql "DROP TABLE old_logs"
```

---

## 20. Plugin System

> **⚠️ DEPRECATED** — The Typer-based plugin model (`navig plugin install/list/enable/disable`,
> `navig.plugins.base.PluginAPI`) has been **retired**.
>
> The new decoupled pack model uses `plugin.json` + `handler.py` + plain `handle()` functions
> and lives in the **[navig-community](https://github.com/navig-run/community)** repository.
>
> SDK: `pip install navig-sdk` (Python) or `npm install navig-sdk` (TypeScript).
> See [navig-community/examples/](https://github.com/navig-run/community/tree/main/examples) for current examples.
>
> The content below is preserved for reference only and describes the **retired system**.

NAVIG supported a modular plugin architecture that allowed extending functionality without modifying core code.

### 19.1 Plugin Management Commands

| Command | Description |
|---------|-------------|
| `navig plugin list` | List all discovered plugins and their status |
| `navig plugin info <name>` | Show detailed information about a plugin |
| `navig plugin enable <name>` | Enable a disabled plugin |
| `navig plugin disable <name>` | Disable a plugin |
| `navig plugin install <path>` | Install a plugin from a directory |
| `navig plugin uninstall <name>` | Remove an installed plugin |

**Examples:**
```bash
# List all plugins
navig plugin list

# Get detailed info about a specific plugin
navig plugin info hello

# Disable a plugin temporarily
navig plugin disable hello

# Re-enable a plugin
navig plugin enable hello
```

### 19.2 Plugin Locations

Plugins are discovered from two locations:

| Location | Type | Description |
|----------|------|-------------|
| `navig/plugins/` | Built-in | Core plugins bundled with NAVIG |
| `~/.navig/plugins/` | User | Custom user-installed plugins |

### 19.3 Using Plugin Commands

Plugin commands are registered under the plugin's name as a subcommand:

```bash
# Pattern: navig <plugin-name> <command> [options]
navig hello greet --name "Developer"
navig hello info
```

### 19.4 Built-in Plugins

#### Hello Plugin (Example/Reference)

The `hello` plugin is included as a reference implementation:

| Command | Description |
|---------|-------------|
| `navig hello greet [--name NAME]` | Display a greeting message |
| `navig hello info` | Show plugin and context information |
| `navig hello remote-demo` | Demo remote command execution via PluginAPI |
| `navig hello config-demo` | Demo configuration access via PluginAPI |

### 19.5 Plugin SDK (For Developers)

Plugins can access NAVIG internals through the `PluginAPI` class:

```python
from navig.plugins.base import PluginAPI

api = PluginAPI()

# Execute remote commands
result = api.run_remote("ls -la /var/www")

# Access configuration
host_config = api.get_host_config("production")
app_config = api.get_app_config("myapp")

# Get active context
active_host = api.get_active_host()
active_app = api.get_active_app()
```

**Available SDK Methods:**
- `run_remote(cmd, host)` - Execute command on remote host
- `get_host_config(name)` - Get host configuration
- `get_app_config(name)` - Get application configuration
- `get_active_host()` - Get currently active host
- `get_active_app()` - Get currently active application
- `get_config_value(key)` - Get global config value
- `set_config_value(key, value)` - Set global config value

### 19.6 Creating Custom Plugins

For full plugin development documentation (retired system), see `.navig/wiki/dev/PLUGIN_DEVELOPMENT.md`.

**Quick Start:**
1. Create a directory in `~/.navig/plugins/` with your plugin name
2. Add `plugin.yaml` with metadata
3. Add `plugin.py` with a `register(app)` function
4. Add `commands.py` with your Typer commands

**Minimal Example:**
```python
# ~/.navig/plugins/myplugin/plugin.py
import typer
from navig.plugins.base import PluginBase

class MyPlugin(PluginBase):
    @property
    def name(self) -> str:
        return "myplugin"

    @property
    def app(self) -> typer.Typer:
        app = typer.Typer(help="My custom plugin")

        @app.command()
        def hello():
            """Say hello"""
            print("Hello from myplugin!")

        return app

def register(app: typer.Typer):
    plugin = MyPlugin()
    if plugin.check_dependencies():
        app.add_typer(plugin.app, name=plugin.name)
```

---

## 21. Workflow System

NAVIG supports reusable command workflows that allow you to define and execute sequences of NAVIG commands.

### 20.1 Block commands (the workflow engine is retired)

⚠ There is no `workflow` group. `navig flow list` reports *"the workflow engine is
retired"* and points here. Reusable command sequences are **Blocks**.

| Command | Description |
|---------|-------------|
| `navig block list` | List installed/discovered blocks |
| `navig block show <name>` | Show the spec: inputs, steps with computed risk, verify |
| `navig apply <name>` | Run the outcome end-to-end and write a receipt |
| `navig apply <name> --dry-run` | Print the resolved plan; resolves no secrets |
| `navig apply <name> --input k=v` | Supply a typed input (repeatable) |
| `navig block verify <name>` | Lint a manifest — does NOT execute it |
| `navig block doctor <name>` | Can this block run *here*? Checks tools and `detect` probes |
| `navig block new` | Scaffold a new `BLOCK.md` |

⚠ Variables are `--input k=v`, not `--var`. A destructive step needs its own
`--approve <step>`; a blanket `--yes` covers only moderate confirmations.

**Examples:**
```bash
# List available blocks
navig block list

# Preview a block without touching anything
navig apply safe-deployment --dry-run

# Execute with typed inputs
navig apply db-snapshot --input host=staging --input db_name=mydb

# Skip moderate prompts (destructive steps still need --approve)
navig apply server-health --yes
```

There is no CLI verb to edit or delete a block: a block is a `BLOCK.md` directory you
edit in place, and `navig install remove <id>` removes an installed one.

### 20.2 Block Locations

| Location | Type | Priority |
|----------|------|----------|
| `.navig/blocks/` | Project-local | Highest |
| `~/.navig/blocks/` | Global | Medium |
| built-in store | Shipped with navig | Lowest |

### 20.3 Built-in ops workflows → now Blocks

The bundled ops workflows are now **Blocks** — run them with `navig apply` (typed
inputs, machine verify, `--approve`-gated destructive steps, signed receipt). See
`docs/blocks-vs-workflows.md`. This engine still runs your own workflows below.

| Block | Run | Description |
|-------|-----|-------------|
| `safe-deployment` | `navig apply safe-deployment …` | Deploy, backup, config-test, verified by a post-deploy health check |
| `db-snapshot` | `navig apply db-snapshot …` | Export a DB and pull it local (verified: dump file exists) |
| `emergency-debug` | `navig apply emergency-debug` | Rapid read-only service/container diagnostics |
| `server-health` | `navig apply server-health` | Read-only server health sweep |

### 20.4 Workflow File Format

```yaml
name: My Workflow
description: What this workflow does
version: "1.0"
author: Your Name

variables:
  host: production
  app_path: /var/www/app

steps:
  - name: Set host
    command: host use ${host}
    description: Connect to target server

  - name: Restart service
    command: run "systemctl restart nginx"
    prompt: "Proceed with restart?"

  - name: Health check
    command: health
    continue_on_error: true
```

**Step Options:**
- `continue_on_error: true` - Continue workflow if step fails
- `skip_on_error: true` - Skip step if previous failed
- `prompt: "Question?"` - Ask for confirmation before executing

### 20.5 Variable Substitution

Variables use `${variable_name}` syntax:

```yaml
variables:
  host: production
  db_name: main_db

steps:
  - name: Dump database
    command: db dump ${db_name} -o backup.sql
```

**Override at runtime:**
```bash
navig apply my-block --input host=staging --input db_name=testdb
```

For complete documentation, see `docs/WORKFLOWS.md`.

---

<a id="22-ai-integration-mcp--wiki-rag"></a>
## 22. ⭐ AI Integration (MCP & Wiki RAG)

NAVIG integrates with AI assistants like GitHub Copilot and Claude via the Model Context Protocol (MCP).

### 22.1 MCP Server for AI Assistants

The NAVIG MCP server exposes your infrastructure configuration to AI assistants, allowing them to:
- Query hosts, apps, and database configurations
- Search the wiki knowledge base
- Access project context and recent errors
- Execute read-only NAVIG commands

**Start MCP Server:**
```bash
navig mcp serve              # Start in stdio mode (for VS Code)
navig mcp serve --port 3000  # Start in SSE mode
```

**Generate Configuration:**
```bash
navig mcp config vscode      # Show VS Code config
navig mcp config claude      # Show Claude Desktop config
navig mcp config vscode -o   # Write to .vscode/mcp.json
```

### 22.2 VS Code Copilot Integration

Add to `.vscode/mcp.json` or VS Code settings:

```json
{
  "mcpServers": {
    "navig": {
      "command": "python",
      "args": ["-m", "navig.mcp_server"],
      "env": {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

**Windows Note (Encoding):** NAVIG emits Unicode (emoji, checkmarks) in help/status output. Setting the UTF-8 env vars above avoids `UnicodeEncodeError` on Windows consoles and VS Code subprocesses.

**Available MCP Tools:**

| Tool | Description |
|------|-------------|
| `navig_list_hosts` | List all configured SSH hosts |
| `navig_list_apps` | List all applications |
| `navig_host_info` | Get details for a specific host |
| `navig_app_info` | Get details for a specific app |
| `navig_search_wiki` | Search wiki knowledge base |
| `navig_list_wiki_pages` | List all wiki pages |
| `navig_read_wiki_page` | Read wiki page content |
| `navig_list_databases` | List database connections |
| `navig_get_context` | Get full NAVIG context for debugging |
| `navig_run_command` | Execute read-only NAVIG commands |

### 22.3 Wiki RAG (Knowledge Base)

The Wiki RAG system provides semantic search across your wiki knowledge base:

**Initialize Wiki:**
```bash
navig wiki init              # Initialize project wiki
navig wiki init --global     # Initialize global wiki
```

**Add Content:**
```bash
navig wiki add ./docs/notes.md           # Add file to inbox
navig wiki add ./docs/api.md -f technical/api  # Add to specific folder
navig wiki rag add "Docker tips..." -t "Docker Guide"  # Add directly to RAG
```

**Search Knowledge Base:**
```bash
navig wiki rag query "how to deploy docker"
navig wiki rag query "nginx configuration" --context  # Get full AI context
```

**Manage Index:**
```bash
navig wiki rag           # Show RAG index status
navig wiki rag rebuild   # Rebuild search index
```

### 22.4 AI Workflows

**Use with GitHub Copilot:**
1. Configure MCP server in VS Code
2. Ask Copilot: "What hosts are configured in NAVIG?"
3. Copilot calls `navig_list_hosts` tool and returns results

**Use with Claude:**
1. Add navig to Claude Desktop config
2. Ask: "Search the wiki for docker deployment guides"
3. Claude uses `navig_search_wiki` to find relevant content

### 22.5 Multi-Provider AI System

NAVIG supports multiple AI providers with automatic fallback:

**Supported Providers:**
- `openai` — OpenAI (GPT-4, GPT-4o, o1, etc.)
- `anthropic` — Anthropic Claude
- `openrouter` — OpenRouter (access to many models)
- `ollama` — Local Ollama models
- `groq` — Groq (fast inference)
- `airllm` — Local inference for 70B+ models on limited VRAM

**Manage Providers:**
```bash
navig ai providers                # List all providers and status
navig ai providers --add openai   # Add API key for OpenAI
navig ai providers --test anthropic  # Test Anthropic connection
navig ai providers --remove groq  # Remove Groq API key

# List all available models
navig ai models
navig ai models --provider airllm
```

### 22.6 AirLLM Local Inference

AirLLM enables running 70B+ parameter models on hardware with limited VRAM (4-8GB) through layer-wise inference.

**Installation:**
```bash
pip install airllm
```

**Configuration:**
```bash
navig ai airllm --status                    # View status
navig ai airllm --configure --model-path meta-llama/Llama-3.3-70B-Instruct
navig ai airllm --configure --compression 4bit --max-vram 8
navig ai airllm --test                      # Test inference
```

**VRAM Requirements:**
- 7B-13B models: 4GB minimum
- 33B-70B models: 4GB with 4bit compression
- 405B models: 8GB with 4bit compression

**Environment Variables:**
- `AIRLLM_MODEL_PATH` — HuggingFace model ID or local path
- `AIRLLM_COMPRESSION` — `4bit`, `8bit`, or empty
- `AIRLLM_MAX_VRAM_GB` — Maximum VRAM (default: 8)
- `HF_TOKEN` — HuggingFace token for gated models

**Usage:**
```bash
# Ask with local model
navig ai ask "Explain this code" --model airllm:deepseek-ai/deepseek-coder-33b-instruct

# Add to fallback chain
# ~/.navig/config.yaml
ai_model_preference:
  - openai:gpt-4o-mini
  - airllm:meta-llama/Llama-3.3-70B-Instruct  # Local fallback
```

See [AirLLM Documentation](providers/airllm.md) for full details.

### 22.7 OAuth Authentication (OpenAI Codex)

For subscription-based access (e.g., ChatGPT/Codex), use OAuth:

**Interactive Login:**
```bash
navig ai login openai-codex      # Opens browser for OAuth
```

**Headless Login (VPS/Remote):**
```bash
navig ai login openai-codex --headless
# Copy the URL, open in your local browser
# After sign-in, paste the redirect URL back
```

**Logout:**
```bash
navig ai logout openai-codex
```

**How OAuth Works:**
1. PKCE challenge/verifier generated
2. Browser opens to `auth.openai.com`
3. User authenticates with OpenAI
4. Callback captured on `localhost:1455` (or pasted manually)
5. Tokens exchanged and stored securely
6. Access token refreshed automatically when expired

**Environment Variables:**
- `OPENAI_API_KEY` — OpenAI API key
- `ANTHROPIC_API_KEY` — Anthropic API key
- `OPENROUTER_API_KEY` — OpenRouter API key

**Configure Fallback Order:**
```yaml
# ~/.navig/config.yaml
ai_model_preference:
  - openai:gpt-4o-mini
  - anthropic:claude-3-haiku
  - openrouter:deepseek/deepseek-coder
```

When a provider fails (rate limit, billing, etc.), NAVIG automatically tries the next provider with exponential backoff.

### 22.7 Telegram Bot Integration

NAVIG includes a Telegram bot for managing servers from anywhere using natural language and slash commands.

**Quick Start (Recommended):**
```bash
# Set your bot token in .env
echo "TELEGRAM_BOT_TOKEN=your_token" >> .env
echo "ALLOWED_TELEGRAM_USERS=your_user_id" >> .env

# Start everything with one command
navig start                  # Gateway + bot (background, recommended)
navig start --foreground     # See live logs
```

**Multilingual behavior (automatic, no setup):**
- The bot auto-detects each incoming message language and replies in that same language.
- Language can switch mid-conversation without commands or confirmation.
- If a message is mixed/ambiguous, response language falls back to last successfully detected language for that user; if none exists, English is used.
- Long-term memory facts are stored in English, then translated silently to the current user language when used in replies.

**Telegram token resolution (env + vault):**
- NAVIG resolves bot token in this order:
  1. Vault (`telegram` provider credential, token/bot_token)
  2. `NAVIG_TELEGRAM_BOT_TOKEN`
  3. `TELEGRAM_BOT_TOKEN`
  4. `telegram.bot_token` in config
- So you can run Telegram bot without exporting env vars when Vault is configured.

**Alternative Start Methods:**

| Command | Description |
|---------|-------------|
| `navig start` | Start gateway + bot together (background) |
| `navig start -f` | Start in foreground (see logs) |
| `navig start --no-gateway` | Bot only (standalone, no session persistence) |
| `navig bot` | Start bot only (standalone) |
| `navig bot --gateway` | Start gateway + bot together |
| `navig bot status` | Check if bot is running |
| `navig bot stop` | Stop all bot/gateway processes |

**Telegram Command Execution Policy (v2.4.17+)**

| Mode | Commands | Behavior |
|------|----------|----------|
| Slash (foreground) | `/about`, `/help`, `/profile`, `/choice`, `/explain_ai` | Immediate user-invoked responses |
| Slash + background orchestration | `/auto_start`, `/auto_stop`, `/auto_status`, `/imagegen`, `/remindme`, `/myreminders`, `/cancelreminder`, `/stats_global` | Slash command controls state/jobs; work may continue in background |
| Business chats only (groups/supergroups) | `/kick`, `/mute`, `/unmute`, `/search` | Command is denied in DM and requires group admin rights |

**Single-message navigation UX (v2.4.2x+)**

- `/start` resets Telegram navigation state and shows the conversational context card (home screen).
- Menu/list workflows prefer in-place updates via `editMessageText` instead of posting new messages.
- Inline navigation uses `🔙 Back` and `🏠 Home` where applicable.
- Callback handlers acknowledge actions with `answerCallbackQuery` so the loading spinner clears immediately.
- If a callback handler fails, NAVIG shows a recovery screen with a `🏠 Home` action.

Canonical Telegram command reference (complete list, options, aliases, callback actions):
- `docs/features/TELEGRAM.md`

Natural-language parity:
- In Telegram, visible slash commands can also be triggered via natural language intents.
- Risky intents require explicit confirmation (`yes`/`cancel`) before execution.

**Command shortcuts:**
- `/plans` → `plans status`
- `/plan <goal>` → `plans add <goal>`
- In group chats, commands can be used as `/command@botname`.

**Beta command visibility**

Some migrated commands are intentionally visible with `(beta)` labels in Telegram command lists and `/help`:
`/music`, `/imagegen`, `/quote`, `/respect`, `/currency`, `/crypto_list`, `/stats_global`.
These are available for controlled rollout while backend orchestration components are finalized.

**Live reminder orchestration**

`/remindme`, `/myreminders`, and `/cancelreminder` are now live and backed by RuntimeStore.
Due reminders are delivered by the Telegram worker background loop (poll interval ~15s).

**Command-first setup:**
```bash
navig gateway start           # Start gateway runtime
navig bot start --gateway     # Start Telegram bot via gateway
navig bot status              # Verify bot and gateway status
```

**Manual Setup:**
```bash
# Copy config template
cp .env.telegram.example .env

# Edit .env with your credentials:
# TELEGRAM_BOT_TOKEN=your_bot_token
# ALLOWED_TELEGRAM_USERS=your_user_id
# Optional legacy override (deprecated): NAVIG_AI_MODEL=openrouter

# Run the bot directly
python -m navig.daemon.telegram_worker --no-gateway
```

**NLP Intent Parser (NEW):**
The bot now includes smart natural language processing to understand commands:

```
You: "show me docker containers"  →  Bot executes /docker
You: "switch to production"       →  Bot executes /use production
You: "how much disk space"        →  Bot executes /disk
You: "remind me in 30 min to check logs"  →  Bot sets reminder
```

Configure in `~/.navig/config.yaml`:
```yaml
telegram:
  nlp_enabled: true           # Enable/disable NLP
  nlp_use_ai: true            # Use AI for intent (more accurate)
  nlp_confidence_threshold: 0.7  # Auto-execute above this
  nlp_confirmation_threshold: 0.4  # Ask confirmation above this
```

See full NLP guide: [TELEGRAM_NLP_GUIDE.md](TELEGRAM_NLP_GUIDE.md)

**Conversational AI Mode (v3.24):**

The bot now prioritizes natural conversation over command parsing. Instead of intercepting
every message as a potential command, it detects genuinely conversational messages and routes
them directly to the AI brain for a natural response:

- **Greetings, identity questions, casual chat** → AI responds with NAVIG personality from SOUL.md
- **Clear command intent** ("show docker containers", "check disk space") → NLP routes to command
- **Ambiguous messages** → AI fallback for natural conversation

The system prompt now injects `SOUL.md` personality, making NAVIG respond as its Deepwatch
persona rather than a generic operations assistant. Identity questions (who are you, what's
your name) go through the AI model for dynamic, context-aware responses instead of hardcoded strings.

Configure the conversational behavior:
```yaml
telegram:
  nlp_enabled: true              # NLP still available for clear commands
  nlp_commands: false            # Set to false to let AI handle everything
```

**Multi-Channel Architecture (Planned):**

NAVIG is designed to communicate across multiple channels with a single AI brain.
See `.navig/plans/CHANNEL_ARCHITECTURE.md` for the full design:

| Channel  | Status    | Description |
|----------|-----------|-------------|
| Telegram | Active    | Primary channel (raw Bot API via httpx) |
| CLI      | Active    | `navig ask` for one-shot queries (`navig ai ask` is deprecated) |
| Web UI   | Planned   | FastAPI + WebSocket streaming |
| Discord  | Planned   | discord.py adapter |
| Email    | Planned   | IMAP polling + SMTP |

**Typing Indicator Configuration:**
The bot shows a "typing..." indicator while AI processes requests. Configure via `.env`:

| Setting | Values | Description |
|---------|--------|-------------|
| `TYPING_MODE` | `instant` (default) | Start typing immediately on message received |
| | `message` | Start typing after acknowledging the message |
| | `never` | Disable typing indicator |
| `TYPING_INTERVAL` | `4.0` (default) | Refresh interval in seconds |

**Heartbeat & Proactive Messages:**

The bot monitors your VS Code formation session via a heartbeat file (`~/.navig/heartbeat.json`) written by the extension every 30 seconds. When a formation is active, the bot checks for actionable items and sends at most one short proactive message per heartbeat window.

| Setting | Default | Description |
|---------|---------|-------------|
| `HEARTBEAT_ENABLED` | `true` | Enable/disable heartbeat monitor |
| `HEARTBEAT_INTERVAL` | `60` | Seconds between heartbeat checks |
| `HEARTBEAT_WINDOW` | `300` | Min seconds between proactive messages |

**Proactive message sources** (checked in the workspace `.navig/plans/` folder):
1. `inbox/*.md` — unprocessed briefs
2. `next-step.md` — explicit next-step marker file
3. `todo.md` — unchecked `- [ ]` items

When VS Code is idle or closed, the bot stays completely silent — no spam, no nag.

**Core Commands:**

| Command | Description |
|---------|-------------|
| `/help` | Interactive help with category navigation |
| `/ping` | Bot health check with latency |
| `/stats` | Usage statistics |
| `/status` | Bot and AI status (includes NLP status) |
| `/reset` | Clear conversation history |

**Server Management:**

| Command | Description | Example |
|---------|-------------|---------|
| `/hosts` | List configured servers | `/hosts` |
| `/use <host>` | Switch to a host | `/use production` |
| `/disk` | Check disk space | `/disk` |
| `/memory` | Check memory usage | `/memory` |
| `/cpu` | Check CPU load | `/cpu` |
| `/docker` | List Docker containers | `/docker` |
| `/logs <container>` | View container logs | `/logs nginx 100` |
| `/restart <container>` | Restart container (confirmation required) | `/restart postgres` |
| `/db` | List databases | `/db` |
| `/db tables <name>` | List tables in database | `/db tables wordpress` |
| `/tables <name>` | Shortcut for tables | `/tables wordpress` |
| `/tunnel` | Show active tunnels | `/tunnel` |
| `/tunnel start <name>` | Start a tunnel | `/tunnel start db-prod` |
| `/tunnel stop <name>` | Stop a tunnel | `/tunnel stop db-prod` |
| `/backup` | List recent backups | `/backup` |
| `/backup create` | Create backup (confirmation required) | `/backup create` |
| `/hestia` | List HestiaCP users | `/hestia` |
| `/hestia domains [user]` | List domains | `/hestia domains admin` |
| `/run <cmd>` | Run remote command | `/run systemctl status nginx` |

**AI Features:**

| Command | Description | Example |
|---------|-------------|---------|
| `/ai_persona [name]` | View/change AI persona | `/ai_persona devops` |
| `/ai_status` | Check AI mode status | `/ai_status` |
| `/formation` | Check VS Code formation status | `/formation` |
| `/remind <time> <msg>` | Set a reminder | `/remind 30m check backup` |
| `/reminders` | List active reminders | `/reminders` |

**Reply-Based AI Commands:**
Reply to any message with these trigger words:
- `explain` / `analyze` - Get AI explanation
- `summarize` / `tldr` - Get brief summary

**Natural Language Examples:**
Ask naturally instead of using commands:

```
You: show docker containers
Bot: 🐙 Understood: /docker
     [container list...]

You: switch to production server
Bot: 🐙 Understood: /use production
     Switched to: production

You: check disk space
Bot: 🐙 Understood: /disk
     Disk Space: /dev/sda1: 45% used

You: remind me in 30 minutes to check backup
Bot: 🐙 Understood: /remind 30m check backup
     ⏰ Reminder set for 30 minutes
```

### 22.8 navig-bridge Extension — Builder Bridge & MCP File Tools

**Version:** v3.39.78+

The navig-bridge VS Code extension exposes workspace file operations to the NAVIG daemon (and any MCP client) over the extension's local MCP WebSocket server (port 42070).

#### New MCP Tools (Sprint 8)

| Tool | Description | Required Args |
|------|-------------|---------------|
| `write_file` | Write content to a workspace-relative path. Creates missing dirs. | `path`, `content` |
| `read_file` | Read full content of a workspace-relative path. | `path` |
| `list_workspace_files` | List files matching a glob pattern. | *(none — defaults to `**/*`)* |
| `get_problems` | Alias for `vscode_get_diagnostics`. Returns all errors/warnings. | *(none)* |

**Example JSON-RPC call (MCP):**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "write_file",
    "arguments": {
      "path": "src/utils/helper.ts",
      "content": "export function hello() { return 'world'; }\n"
    }
  }
}
```

**`list_workspace_files` options:**
```json
{
  "glob": "src/**/*.ts",
  "exclude": "**/node_modules/**",
  "max_results": 100
}
```

#### AI Diff Panel — Automatic Code Routing

When the NAVIG chat produces a code block with an annotated file path, it is automatically written to `.navig/proposed/<path>` and surfaced in the **NAVIG Diff Panel** (sidebar tree view).

**Supported annotation formats:**

````
```typescript
// src/components/Button.tsx
... code here ...
```

```python
# scripts/deploy.py
... code here ...
```

```typescript:src/utils/helper.ts
... code here ...
```
````

Open the diff panel (`Ctrl+Shift+P → NAVIG: Refresh Diff Panel`) to review, accept, or reject each proposed change.

#### Dev Server Preview Command

`Ctrl+Shift+P → NAVIG: Open Dev Server Preview`

Opens your workspace dev server in VS Code's Simple Browser. Port detection order:
1. `navig-bridge.previewPort` setting (if non-zero)
2. Auto-detect from `package.json` `scripts.dev` or `scripts.start` (looks for `--port NNNN`)
3. Default: `http://localhost:3000`

---

## 23. Autonomous Agent System (Gateway, Heartbeat, Cron)

NAVIG includes a full autonomous agent architecture for 24/7 server monitoring and management without manual intervention.

### 23.1 Gateway Server

The gateway is the central control plane that coordinates all autonomous agent operations.

**Starting the Gateway:**
```bash
# Start gateway (foreground)
navig gateway start

# Start on custom port
navig gateway start --port 9000

# Check if gateway is running
navig gateway status
```

**Gateway authentication.** The gateway's admin routes — approvals, audit, cron, MCP,
memory, tasks and more — require a bearer token. If your install has none, one is
**generated and saved to `gateway.auth.token` the first time the gateway starts**, the
same way `deck.api_key` has always been.

You do not need to do anything: the NAVIG CLI reads the token from your config and sends
it automatically. The deck and the desktop app authenticate separately (with
`deck.api_key`) and are unaffected.

```bash
navig config get gateway.auth.token     # if another tool of yours needs it
navig doctor                            # "Gateway auth" row shows the state
```

Any *other* client you have pointed at the gateway needs that token as
`Authorization: Bearer <token>`; it will get a 401 saying so until it does.

> **Why it is not optional.** Without a token every request was accepted. That included
> `POST /approval/{id}/respond` — so any program running on your machine could list the
> agent's pending approvals and answer them, which quietly defeats the approval system
> no matter how carefully it is configured.

**Gateway Features:**
- HTTP/WebSocket API for agent communication
- Session persistence across restarts
- Heartbeat-based health monitoring
- Cron job scheduling
- Multi-channel message routing (Telegram, etc.)
- Hot-reload configuration changes

**API Endpoints:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Gateway health check |
| `/status` | GET | Full status (heartbeat, cron, sessions) |
| `/message` | POST | Route message to agent |
| `/sessions` | GET | List active sessions |
| `/heartbeat/trigger` | POST | Trigger immediate heartbeat |
| `/cron/jobs` | GET/POST | Manage cron jobs |

**Session Management:**
```bash
# List active sessions
navig gateway session list

# Show session details
navig gateway session show agent:default:telegram:12345

# Clear a session
navig gateway session clear agent:default:telegram:12345
```

### 23.2 Heartbeat System

The heartbeat system runs periodic health checks and only notifies when issues are found.

**How It Works:**
1. Every N minutes (default: 30), the AI agent checks all configured hosts
2. If everything is healthy, returns `HEARTBEAT_OK` (no notification)
3. If issues found, sends notification with details and remediation steps

**Commands:**
```bash
# Check heartbeat status
navig heartbeat status

# Trigger immediate heartbeat
navig heartbeat trigger

# View heartbeat history
navig heartbeat history --limit 20

# Configure heartbeat
navig heartbeat configure --interval 15  # 15 minutes
navig heartbeat configure --disable      # Disable heartbeat
navig heartbeat configure --enable       # Enable heartbeat
```

**Configuration in config.yaml:**
```yaml
heartbeat:
  enabled: true
  interval: 30  # minutes
  timeout: 300  # seconds (5 min max per check)

notifications:
  channel: telegram
  recipient: "12345678"  # Telegram user ID
```

**What Heartbeat Checks:**
- Host connectivity (ping each configured host)
- Disk space (warn if >80% used)
- Memory usage (warn if >90% used)
- SSL certificate expiry (warn if <14 days)
- Service status (configurable per host)

### 23.3 Cron Scheduler

Persistent job scheduling with natural language support.

**Adding Jobs:**
```bash
# Natural language scheduling
navig cron add "Disk check" "every 30 minutes" "navig host monitor disk"
navig cron add "Daily backup" "hourly" "navig backup export"
navig cron add "Weekly report" "every 7 days" "Generate weekly server report"

# Cron expression scheduling
navig cron add "Nightly cleanup" "0 2 * * *" "navig run 'cleanup.sh'"
navig cron add "Every 5 min" "*/5 * * * *" "navig host test"
```

**Managing Jobs:**
```bash
# List all jobs
navig cron list

# Show cron service status
navig cron status

# Run a job immediately
navig cron run job_1

# Enable/disable jobs
navig cron enable job_1
navig cron disable job_1

# Remove a job
navig cron remove job_1
```

**Schedule Formats:**
| Format | Example | Description |
|--------|---------|-------------|
| Natural | `every 30 minutes` | Runs every 30 minutes |
| Natural | `hourly` | Runs every hour |
| Natural | `daily` | Runs every 24 hours |
| Natural | `every 2 hours` | Runs every 2 hours |
| Cron | `*/5 * * * *` | Every 5 minutes |
| Cron | `0 9 * * *` | Daily at 9:00 AM |
| Cron | `0 2 * * 0` | Weekly on Sunday at 2:00 AM |

### 23.4 Workspace Files

The autonomous agent uses workspace files for persistent context:

| File | Purpose |
|------|---------|
| `AGENTS.md` | Agent capabilities and channel bindings |
| `SOUL.md` | Agent personality and behavior guidelines |
| `USER.md` | User preferences and patterns |
| `TOOLS.md` | Available NAVIG commands and shortcuts |
| `HEARTBEAT.md` | Health check instructions |
| `MEMORY.md` | Long-term memories and notes |

**Location:** `~/.navig/workspace/`

These files are automatically created on first gateway start. You can edit them to customize agent behavior.

### 23.5 Telegram Bot with Gateway

Enable session persistence for the Telegram bot by connecting to the gateway:

```bash
# In .env file:
TELEGRAM_BOT_TOKEN=your_token
NAVIG_GATEWAY_URL=http://localhost:8789

# Start gateway first
navig gateway start &

# Then start bot
python -m navig.daemon.telegram_worker --no-gateway
```

**Benefits of Gateway Mode:**
- Conversation persists across bot restarts
- Session compaction prevents token overflow
- Heartbeat runs automatically
- Cron jobs execute in background
- Multiple bots can share the same gateway

### 23.6 Full Autonomous Setup

Complete setup for 24/7 autonomous operation:

```bash
# 1. Configure hosts
navig host add production
navig host add staging

# 2. Configure notifications
navig config set notifications.channel telegram
navig config set notifications.recipient "YOUR_TELEGRAM_ID"

# 3. Configure heartbeat
navig heartbeat configure --interval 30 --enable

# 4. Add cron jobs
navig cron add "Health check" "every 30 minutes" "Check all hosts"
navig cron add "Disk monitor" "hourly" "navig host monitor disk"
navig cron add "Backup" "0 2 * * *" "navig backup export"

# 5. Start everything (gateway + bot)
navig start                  # Background (recommended)
navig start --foreground     # See live logs
```

### 23.7 Proactive Engagement System

NAVIG includes a proactive engagement subsystem that initiates context-aware interactions with the operator. Unlike health alerts, these are relationship-building interactions — greetings, feature discovery, check-ins, and self-improvement feedback loops.

**Architecture:**
- **UserStateTracker** (`navig/agent/proactive/user_state.py`): Observes interaction patterns and infers operator state (active, idle, deep work, away, just arrived, winding down)
- **EngagementCoordinator** (`navig/agent/proactive/engagement.py`): Decides when and what proactive actions to take based on state, cooldowns, and probability tuning
- **CapabilityPromoter** (`navig/agent/proactive/capability_promo.py`): Feature discovery engine that promotes underused NAVIG capabilities based on the operator's actual usage patterns

**Engagement Actions:**

| Action | Frequency | Description |
|--------|-----------|-------------|
| Greeting | 1×/day | Morning greeting or welcome-back message |
| Check-in | Every 4h | Periodic "need anything?" when operator is active |
| Feature Discovery | 1×/day | Promote underused features based on usage stats |
| Contextual Tip | Every 8h | Usage tips based on most-used commands |
| Evening Wrap-up | 1×/day | End-of-day summary offer (5-8 PM) |
| Feedback Request | Every 72h | Self-improvement dialogue (after 3+ days) |
| Idle Nudge | Every 6h | Gentle offer to run diagnostics during idle |

**Engagement Rules:**
- Max 5 proactive messages per day
- Quiet hours: 11 PM — 7 AM (no proactive messages)
- Never interrupts deep work (long session, low message rate)
- Each action type has cooldown enforcement
- Probabilistic scheduling (feels natural, not clockwork)
- All state persisted to `~/.navig/engagement/user_state.json`

**Integration Points:**
- Runs on the ProactiveEngine polling loop (every ~15 min)
- Also runs on the TelegramNotifier scheduler loop
- Fires `proactive:engagement` hooks for channel delivery
- Records interactions from message handlers for state tracking

**Configuration (in engagement code, customizable):**
```python
EngagementConfig(
    enabled=True,
    greeting_cooldown_hours=12.0,
    checkin_cooldown_hours=4.0,
    capability_promo_cooldown_hours=24.0,
    feedback_ask_cooldown_hours=72.0,
    max_proactive_per_day=5,
    quiet_hours=(23, 7),
    checkin_probability=0.3,
    capability_promo_probability=0.4,
)
```

**Alternative: Start Components Separately:**
```bash
# Start gateway only
navig gateway start

# Start bot only (in another terminal)
navig bot
```

#### Production Deployment with systemd

For production Linux servers, deploy NAVIG as a systemd service for automatic startup and crash recovery.

**Step 1: Create environment file with API keys:**
```bash
# /etc/navig/env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
OPENROUTER_API_KEY=your_openrouter_api_key
ANTHROPIC_API_KEY=your_anthropic_api_key
```

**Step 2: Create Gateway service unit:**
```ini
# /etc/systemd/system/navig-gateway.service
[Unit]
Description=NAVIG Gateway Server
Documentation=https://github.com/yourrepo/navig
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=navig
Group=navig
WorkingDirectory=/home/navig
EnvironmentFile=/etc/navig/env
ExecStart=/usr/local/bin/navig gateway start --host 127.0.0.1 --port 8789
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5
StartLimitBurst=5
StartLimitIntervalSec=60
# Security hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
ReadWritePaths=/home/navig/.navig

[Install]
WantedBy=multi-user.target
```

**Step 3: Create Telegram Bot service unit (optional):**
```ini
# /etc/systemd/system/navig-bot.service
[Unit]
Description=NAVIG Telegram Bot
After=navig-gateway.service
Requires=navig-gateway.service

[Service]
Type=simple
User=navig
Group=navig
WorkingDirectory=/home/navig
EnvironmentFile=/etc/navig/env
ExecStart=/usr/local/bin/python -m navig.daemon.telegram_worker --no-gateway
Restart=on-failure
RestartSec=10
StartLimitBurst=3
StartLimitIntervalSec=60

[Install]
WantedBy=multi-user.target
```

**Step 4: Enable and start services:**
```bash
# Reload systemd to recognize new units
sudo systemctl daemon-reload

# Enable auto-start on boot
sudo systemctl enable navig-gateway
sudo systemctl enable navig-bot

# Start services
sudo systemctl start navig-gateway
sudo systemctl start navig-bot

# Check status
sudo systemctl status navig-gateway
sudo systemctl status navig-bot

# View logs
sudo journalctl -u navig-gateway -f
sudo journalctl -u navig-bot -f
```

#### Windows Service Deployment

For Windows servers, use NSSM (Non-Sucking Service Manager):

```powershell
# Download NSSM from https://nssm.cc/download
# Install gateway as service
nssm install navig-gateway "C:\Python312\python.exe" "-m navig gateway start"
nssm set navig-gateway AppDirectory "C:\navig"
nssm set navig-gateway AppStdout "C:\navig\logs\gateway.log"
nssm set navig-gateway AppStderr "C:\navig\logs\gateway-error.log"

# Start service
nssm start navig-gateway
```

### 23.8 NAVIG Daemon (Recommended for Persistent Operation)

The NAVIG Daemon is a process supervisor that keeps subsystems (Telegram bot, gateway, scheduler) running permanently with auto-restart, health monitoring, and structured logging.

**Architecture:**
- Supervisor process manages child processes
- Auto-restart on crash with exponential back-off (2s → 4s → 8s → ... → 120s max)
- PID file management at `~/.navig/daemon/supervisor.pid`
- Rotating logs at `~/.navig/logs/daemon.log` (5 MB, 3 backups)
- Optional HTTP health-check endpoint
- Configuration at `~/.navig/daemon/config.json`

#### Quick Start

```bash
# Install as a persistent service (auto-detects best method)
navig service install

# Platform detection:
#   Linux:   systemd user service (or system-wide if root)
#   Windows: Task Scheduler (no admin) or NSSM (with admin)

# Manual lifecycle
navig service start           # Start daemon in background
navig service start -f        # Start in foreground (for debugging)
navig service stop            # Graceful shutdown
navig service restart         # Stop + start
navig service status          # Show daemon and child process health
navig service logs            # Last 50 lines
navig service logs -f         # Follow log output
navig service logs -n 200     # Last 200 lines
```

#### Installation Methods

```bash
# Auto-detect best method
navig service install

# Linux: systemd (auto-detected, user-level or system-wide)
navig service install --method systemd

# Windows: Task Scheduler (no admin needed, starts on login)
navig service install --method task

# Windows: NSSM (requires admin + NSSM installed)
navig service install --method nssm

# Include optional subsystems
navig service install --gateway --scheduler

# Install without starting
navig service install --no-start
```

#### Configuration

```bash
# View current config
navig service config

# Edit config file directly
navig service config --edit
```

Config file `~/.navig/daemon/config.json`:

```json
{
  "telegram_bot": true,
  "gateway": false,
  "gateway_port": 8765,
  "scheduler": false,
  "health_port": 0
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `telegram_bot` | `true` | Run the Telegram bot |
| `gateway` | `false` | Run the gateway WebSocket server |
| `gateway_port` | `8765` | Gateway listen port |
| `scheduler` | `false` | Run the cron scheduler |
| `health_port` | `0` | TCP health-check port (0 = disabled) |
| `bot_script` | unset | Optional explicit bot script override (legacy); default uses `navig.daemon.telegram_worker` |

#### Removal

```bash
navig service uninstall
navig service uninstall --method task
navig service uninstall --method systemd
```

### 23.9 NAVIG Stack (Local Docker Infrastructure)

The `navig stack` command manages the local Docker Compose infrastructure (Postgres+pgvector, Redis, Ollama) that powers NAVIG's agent capabilities.

**Prerequisites:** Docker Engine + Docker Compose plugin installed on the host.

#### Stack Commands

```bash
# Show container status
navig stack status
navig stack status --json

# Start the stack
navig stack up
navig stack up --foreground    # Attach to logs

# Stop the stack
navig stack down
navig stack down --volumes     # WARNING: removes all data

# View logs
navig stack logs               # All services, last 50 lines
navig stack logs ollama -f     # Follow ollama logs
navig stack logs postgres -n 100

# Health check
navig stack health             # 7-point check

# Show configuration & paths
navig stack info
```

#### Stack Directory

| Mode | Path |
|------|------|
| Server (systemd/root) | `/opt/navig/` |
| Local (user) | `~/.navig/stack/` |
| Custom | `NAVIG_STACK_DIR` env var |

#### Stack Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| postgres | pgvector/pgvector:pg16 | 127.0.0.1:5432 | Vector DB + relational storage |
| redis | redis:7-alpine | 127.0.0.1:6379 | Cache, queues, sessions |
| ollama | ollama/ollama:latest | 127.0.0.1:11434 | Local LLM inference |

All ports bind to `127.0.0.1` only — not exposed to the network.

### 23.10 Linux Server Bootstrap

For deploying NAVIG infrastructure on a Linux server, use the bootstrap script:

```bash
# Upload and run the bootstrap script
bash core/installers/bootstrap_navig_linux.sh
```

The bootstrap script performs:
1. **System validation** — hostname, swap, OS detection
2. **Security hardening** — SSH key-only, UFW firewall, fail2ban, auto-updates
3. **Kernel tuning** — swappiness, file descriptors, inotify
4. **Docker installation** — Engine + Compose, log rotation, BuildKit
5. **Filesystem setup** — `/opt/navig/` with proper permissions
6. **Stack deployment** — Postgres, Redis, Ollama containers
7. **systemd integration** — `navig.service` for boot persistence
8. **Healthcheck & backup** — Daily cron, 7-day rotation

### 23.11 Operational Factory (Drafts + Safe Actions + Approval)

Operational Factory is a safe-by-default, auditable MVP where role agents can draft and execute SAFE tools, while RESTRICTED actions require explicit approval.

#### Stack Path

`deploy/operational-factory/`

#### Services

- `ollama`
- `postgres` (+ `pgvector`)
- `redis`
- `tool-gateway`
- `navig-runtime`
- `worker`
- `dashboard`

#### Safety Model

- SAFE: read-only repo scan, draft creation, bounded log reads, sandbox lint/test
- RESTRICTED: send email, merge PR, deploy, network changes, payment, delete data, mass messaging
- RESTRICTED actions are queued in `proposed_actions` and cannot execute without approval

#### Run (Local / Server)

```bash
cd deploy/operational-factory
cp .env.example .env
./scripts.sh start
./scripts.sh pull-model
./scripts.sh status
```

Approval inbox UI:

```text
http://127.0.0.1:8088
```

#### Demo flows

```bash
# 1) Email intake -> classify -> 3 drafts -> approval queue
curl -X POST http://127.0.0.1:8091/flow/email/intake -H 'content-type: application/json' -d '{"limit":10}'

# 2) Repo scan -> PR plan draft -> merge action queued for approval
curl -X POST http://127.0.0.1:8091/flow/repo/propose -H 'content-type: application/json' -d '{}'

# 3) Daily multi-agent briefing draft
curl -X POST http://127.0.0.1:8091/flow/briefing/daily
```

#### Audit verification

```bash
docker compose -f deploy/operational-factory/docker-compose.yml exec -T postgres \
  psql -U navig -d navig_factory -c "select created_at,actor_id,action,status from audit_log order by id desc limit 30;"
```

### 23.7 Troubleshooting Autonomous Components

#### Gateway Issues

| Problem | Solution |
|---------|----------|
| Gateway won't start | Check port 8789 is free: `netstat -an \| grep 8789` |
| "Connection refused" | Ensure gateway is running: `navig gateway status` |
| Sessions not persisting | Check `~/.navig/session/` directory exists |
| High memory usage | Clear old sessions: `navig gateway session clear` |

**Debug Steps:**
```bash
# Check if gateway is running
navig gateway status

# Check gateway port
curl http://localhost:8789/health

# View gateway logs
cat ~/.navig/logs/gateway.log

# Restart gateway
navig gateway stop && navig gateway start
```

#### Heartbeat Issues

| Problem | Solution |
|---------|----------|
| Heartbeat shows "never" | Trigger manually: `navig heartbeat trigger` |
| Interval shows "?" | Configure interval: `navig heartbeat configure --interval 30` |
| Not sending notifications | Check `notifications.channel` in config |
| Hosts not being checked | Verify hosts exist: `navig host list` |

**Debug Steps:**
```bash
# Check heartbeat configuration
navig config show | grep -A5 heartbeat

# Manually trigger heartbeat
navig heartbeat trigger

# View heartbeat history
navig heartbeat history --limit 10
```

#### Cron Issues

| Problem | Solution |
|---------|----------|
| Jobs not executing | Ensure gateway is running |
| "Invalid schedule" | Use valid cron expression or natural language |
| Job stuck "running" | Check if command is blocking |
| Jobs disappear on restart | Jobs persist in gateway memory; restart gateway |

**Debug Steps:**
```bash
# List all jobs
navig cron list

# Check cron service status
navig cron status

# Run a job manually to test
navig cron run <job_id>

# Check job exists after gateway restart
navig gateway status && navig cron list
```

#### Common Errors

**Error: `TELEGRAM_BOT_TOKEN not set`**
```bash
# Set in environment
export TELEGRAM_BOT_TOKEN=your_token

# Or in .env file
echo "TELEGRAM_BOT_TOKEN=your_token" >> ~/.navig/.env
```

**Error: `Gateway not reachable`**
```bash
# Start gateway first
navig gateway start

# Then run commands that need gateway
navig heartbeat status
navig cron list
```

**Error: `Failed to connect to AI provider`**
```bash
# Check API key is set
echo $OPENROUTER_API_KEY

# Test AI connectivity
navig ai test

# Check provider configuration
navig config show | grep -A5 ai
```

### 23.8 Best Practices

1. **Always start gateway first**: Heartbeat and cron require gateway to be running
2. **Use `navig start` for production**: Combines gateway + bot with proper startup order
3. **Monitor disk space**: Gateway stores sessions in `~/.navig/session/`
4. **Set reasonable heartbeat intervals**: 30 minutes is a good default; too frequent wastes resources
5. **Use systemd for production**: Ensures automatic restart on crash and boot
6. **Secure API keys**: Use environment files, not command-line arguments
7. **Review cron jobs regularly**: `navig cron list` to audit scheduled tasks
8. **Test notifications**: Run `navig heartbeat trigger` after setup to verify alerts work

**See Also:**
- [Section 24: Memory & Context Management](#24-memory--context-management) - Conversation persistence
- [Section 25: Autonomous Agent Mode](#25-autonomous-agent-mode) - Full AI agent with personality

---

## 📚 Additional Resources

- **Configuration Files:** `~/.navig/hosts/*.yaml` (host configs)
- **App Configs:** `~/.navig/apps/*.yaml` (app configs)
- **Log Files:** `.navig/debug.log` (debug information)
- **Cache:** `~/.navig/cache/` (active host, tunnel PIDs)
- **Backups:** `~/.navig/backups/` (database backups)
- **Wiki:** `.navig/wiki/` (project knowledge base)
- **MCP Config:** `.vscode/mcp.json` (AI assistant config)
- **Credentials:** `~/.navig/credentials/` (API keys, OAuth tokens)
- **Memory:** `~/.navig/memory.db` (conversation history)
- **Knowledge:** `~/.navig/knowledge.db` (knowledge base)

---

## 23.5 Credentials Vault

NAVIG provides a secure, encrypted credentials vault for managing API keys, tokens, and passwords across all integrations.

### Vault Commands

```bash
# List all credentials
navig cred list
navig cred list --json              # Machine-readable JSON output
navig cred list --provider openai   # Filter by provider

# Add a credential
navig cred add openai --key sk-... --label "Work OpenAI"
navig cred add github --token ghp_... --profile work
navig cred add gmail --email user@gmail.com  # Interactive password prompt

# Show credential details
navig cred show <id>
navig cred show <id> --reveal       # Show secret values (use with caution)

# Full information panel (identity + keys + last validation + recent audit)
navig cred info <id>                # Cached validation result
navig cred info <id> --test         # Re-run live connection probe, refresh cache
navig cred info <id> --test --reveal  # Live test AND reveal secret values

# Edit a credential
navig cred edit <id> --key sk-new-key --label "Updated Label"

# Delete a credential
navig cred delete <id>
navig cred delete <id> --force      # Skip confirmation

# Test credential validity
navig cred test openai              # Test by provider name
navig cred test <id>                # Test by credential ID
navig cred test --provider deepgram # Force provider mode (avoids short-name ambiguity)
navig cred test --id 4d731848       # Force credential-ID mode

# Enable/disable without deleting
navig cred disable <id>
navig cred enable <id>

# Pin a credential as the active (preferred) one when a provider has multiple keys
navig cred activate <id>            # short 8-char ID works too

# Clone to another profile
navig cred clone <id> work --label "Work Copy"

# View audit log
navig cred audit                    # All entries
navig cred audit <id> --limit 20    # For specific credential

# List supported providers with validation
navig cred providers
```

### Credential IDs

`navig cred list` displays the first 8 characters of each credential's UUID.
These short IDs are accepted by all commands that take `<id>` as an argument
(`show`, `edit`, `delete`, `enable`, `disable`, `activate`, `audit`, `clone`).

```
┏━━━━━━━━━━┳━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ ID       ┃   ┃ Provider ┃ Profile ┃ Type    ┃
├──────────┼───┼──────────┼─────────┼─────────┤
│ d08ae821 │ ⭐│ openai   │ work    │ api_key │
│ e6d1ab63 │   │ openai   │ personal│ api_key │
└──────────┴───┴──────────┴─────────┴─────────┘
```

The ⭐ column marks the **active** credential for its provider (see below).

### Multiple Keys for the Same Provider

When you store more than one key for a provider (e.g. `openai` work + personal),
NAVIG must pick one when `get_api_key("openai")` is called without an explicit
profile.

**Resolution order:**

| Priority | Condition | Wins |
|----------|-----------|------|
| 1 | Credential has been activated with `navig cred activate` | ⭐ activated one |
| 2 | No activated credential | Most recently used |
| 3 | Never used | Most recently created |
| — | `--profile work` passed explicitly | That profile's key, ignores activate |

**Pin the preferred key:**

```bash
# See all openai keys and their short IDs
navig cred list --provider openai

# Activate the work key
navig cred activate d08ae821

# Confirm (⭐ marker should appear next to d08ae821)
navig cred list --provider openai
```

Once activated, `get_api_key("openai")` always returns the pinned key until
you activate a different one or delete it.

### Credential Profile Management

Credential profiles act as namespaces for organizing credentials by environment.

> **Note:** `navig profile` manages operating-mode profiles (node / builder / operator / architect).
> Use `navig cred-profile` for credential namespace management.

```bash
# List all credential profiles
navig cred-profile list

# Switch active credential profile
navig cred-profile use work

# Credentials resolve in order: active profile → default → any enabled
```

### Supported Providers (with API Validation)

OpenAI, Anthropic, OpenRouter, Groq, GitHub, GitLab, Gmail, Outlook, Fastmail, Jira.

### Programmatic Access

```python
from navig.vault import get_vault

vault = get_vault()

# Get API key with env var fallback
api_key = vault.get_api_key("openai")

# Get as SecretStr (prevents accidental logging)
secret = vault.get_secret("openai")
print(secret)        # Output: ***
actual = secret.reveal()  # Get real value
```

---

## 23.6 Windows System Tray Launcher

Run NAVIG services from the Windows system tray — no terminal needed.

### Quick Start

```bash
# Launch tray (appears near clock)
navig tray start

# Check status
navig tray status
navig tray status --json

# Stop tray
navig tray stop
```

### Install (Desktop Shortcut + Auto-Start)

```bash
# Create desktop shortcut
navig tray install

# With Windows auto-start
navig tray install --auto-start

# Remove everything
navig tray uninstall
```

Or use the PowerShell installer directly:

```powershell
.\scripts\install-tray.ps1 -AutoStart
```

### Tray Menu (Right-Click)

| Menu Item | Description |
|-----------|-------------|
| Gateway status | Shows running/stopped + PID |
| Agent status | Shows running/stopped + PID |
| Start/Restart Gateway | Launch or restart the gateway service |
| Stop Gateway | Stop the gateway |
| Start/Restart Agent | Launch or restart the agent |
| Stop Agent | Stop the agent |
| Quick Actions | Dashboard, Host Status, Vault List, Skills List |
| Auto-start with Windows | Toggle registry-based auto-start |
| Open Log Folder | Opens `~/.navig/logs/` in Explorer |
| Stop All & Exit | Stops services and exits tray |

### Icon Status Colors

| Color | Meaning |
|-------|---------|
| Green dot | All services running |
| Yellow dot | Services starting |
| Red dot | Error detected |
| Gray (no dot) | All stopped |

### Settings

Stored in `~/.navig/tray_settings.json`:

```json
{
  "auto_start": false,
  "start_gateway_on_launch": false,
  "start_agent_on_launch": false,
  "python_exe": "C:\\Server\\bin\\python\\python-3.12\\python.exe",
  "gateway_port": 8765
}
```

### Requirements

- Windows only (uses `pystray` + `Pillow`)
- Install deps: `pip install pystray Pillow`
- `navig tray install` handles dependency checks automatically

---

## 24. Memory & Context Management

NAVIG provides persistent memory for AI conversations and a knowledge base for storing project information.

### 24.1 Conversation Memory

Track conversation history across sessions for context-aware AI interactions.

```bash
# List all conversation sessions
navig memory sessions

# Show conversation history for a session
navig memory history my-task-123

# Show last 10 messages
navig memory history my-task-123 --limit 10

# Clear a specific session
navig memory clear --session my-task-123

# Clear all memory
navig memory clear --all --force

# Show memory statistics
navig memory stats
```

#### Searchable session memory (opt-in)

A long agent run eventually fills its context window and gets compacted — older turns are
summarised away, and the details of what a tool actually returned go with them. With this
enabled, each tool result is also indexed, and right after a compaction the agent is handed
back only the recorded events relevant to what you just asked.

```bash
# off by default; turn it on
navig config set memory.session_index.enabled true

# back off again
navig config set memory.session_index.enabled false
```

Everything stays on your machine (`<data dir>/session_index.db`). One session's events are
never visible to another, and if the index is unavailable the agent simply carries on without it.

It cleans up after itself, so leaving it on does not grow a file forever:

```bash
# keep at most this many events per session (0 disables the cap)
navig config set memory.session_index.max_events 2000

# drop anything older than this many days (0 keeps everything)
navig config set memory.session_index.retention_days 30

# and a ceiling across every session, for machines that start many short ones
navig config set memory.session_index.max_total_events 20000
```

Searching works in any language, including scripts written without spaces — a two-character
Chinese or Japanese query finds text inside a longer run.

### 24.2 Knowledge Base

Store persistent knowledge entries for project context.

```bash
# List all knowledge entries
navig memory knowledge list

# Add a knowledge entry
navig memory knowledge add --key "db-password" --content "Use prod_db_pass_v2" --tags "database,credentials"

# Search knowledge base
navig memory knowledge search --query "database"

# Clear knowledge base
navig memory knowledge clear
```

### 24.3 Gateway REST API

Memory is accessible via the Gateway REST API:

**Every memory route requires the gateway bearer token** — read it once and export it:

```bash
export NAVIG_TOKEN=$(navig config get gateway.auth.token)
```

```bash
# List sessions
curl -H "Authorization: Bearer $NAVIG_TOKEN" \
  http://localhost:8789/memory/sessions

# Get session history
curl -H "Authorization: Bearer $NAVIG_TOKEN" \
  http://localhost:8789/memory/history/my-task

# Add a message
curl -X POST http://localhost:8789/memory/messages \
  -H "Authorization: Bearer $NAVIG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"session_key": "my-task", "role": "user", "content": "Hello"}'

# Search knowledge
curl -H "Authorization: Bearer $NAVIG_TOKEN" \
  "http://localhost:8789/memory/knowledge/search?q=database"

# Add knowledge
curl -X POST http://localhost:8789/memory/knowledge \
  -H "Authorization: Bearer $NAVIG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key": "my-key", "content": "Important info", "tags": ["project"]}'

# Get memory stats
curl -H "Authorization: Bearer $NAVIG_TOKEN" \
  http://localhost:8789/memory/stats
```

> The NAVIG CLI sends this header for you — `navig memory …` needs no token.

### 24.4 RAG Pipeline (Python API)

For AI integrations, use the RAG pipeline to build context:

```python
from navig.memory import ConversationStore, KnowledgeBase, RAGPipeline

# Initialize stores
store = ConversationStore(Path.home() / '.navig' / 'memory.db')
kb = KnowledgeBase(Path.home() / '.navig' / 'knowledge.db')

# Create RAG pipeline
rag = RAGPipeline(
    conversation_store=store,
    knowledge_base=kb,
)

# Build context for AI prompt
result = rag.retrieve(
    query="How do I configure the database?",
    session_key="current-task",
)

# Use the context
prompt = f"Context:\n{result.context}\n\nQuestion: {query}"
```

### 24.5 Optional: Vector Embeddings

For semantic search, install optional dependencies:

```bash
pip install sentence-transformers chromadb tiktoken
```

Then use embedding-enabled search:

```python
from navig.memory import LocalEmbeddingProvider, KnowledgeBase

# Create embedding provider
embeddings = LocalEmbeddingProvider(model_name="all-MiniLM-L6-v2")

# Initialize KB with embeddings
kb = KnowledgeBase(db_path, embedding_provider=embeddings)

# Semantic search (finds similar concepts, not just keywords)
results = kb.search("database connection issues", min_similarity=0.5)
```

---

### 24.6 Knowledge Graph (`navig kg`)

Store and recall structured facts as subject→predicate→object triples.
Useful for remembering user preferences, project decisions, and routines.

```bash
# Remember a fact
navig kg remember user prefers dark_mode
navig kg remember user pays_bills_on "15th of month" --confidence 0.9

# Recall all facts about a subject
navig kg recall user

# Filter by predicate
navig kg recall user --predicate prefers

# Full-text search across all facts
navig kg search "payment"

# Delete a fact (shows confirmation prompt)
navig kg forget <fact-id>
navig kg forget <fact-id> --force

# Show database stats
navig kg status
```

Facts are automatically injected into every AI turn via the knowledge-graph enrichment
pipeline (see §24.8).

---

### 24.7 Project Code Index (`navig index`)

BM25 full-text index of your project's source code and documentation.
Enables fast code search and feeds search results into the AI context automatically
when the Gateway daemon detects a project index (§24.8).

```bash
# Full scan — index the current project
navig index scan

# Incremental scan — only changed files (faster)
navig index scan --incremental

# Index a specific project root
navig index scan /path/to/project

# Search indexed code (BM25 ranked)
navig index search "authentication middleware"
navig index search "database connection" --top 5
navig index search "login handler" --root /path/to/project

# Show index statistics
navig index stats

# Drop the index (forces full rescan next time)
navig index drop
```

The index is stored at `<project-root>/.navig/project_index.db` and is ignored by
git (added to `.gitignore` automatically on first scan).

---

### 24.8 Automatic AI Context Injection

Every AI turn — whether through `navig ask` (or deprecated `navig ai ask`), the Gateway REST API, Telegram, or any
other channel — automatically injects the following memory sources into the system prompt:

| Source | What is injected | CLI to manage |
|--------|-----------------|---------------|
| **Knowledge Base (KB)** | Top-5 keyword-matching entries from `knowledge.db` | `navig memory knowledge` |
| **Knowledge Graph (KG)** | Top-8 matching fact triples + active routines | `navig kg` |
| **Episodic Memory** | 3 most relevant past-session excerpts | `navig memory sessions/history` |
| **Project Code Index** | Top-3 BM25 code/doc snippets (when `.navig/project_index.db` exists) | `navig index scan/search` |
| **User Profile** | Profile context from `MemoryManager.get_user_context()` | auto-updated |

All four sources run concurrently (KB + KG + episodic in a `ThreadPoolExecutor(3)` in `ai.py`;
code index in `_build_agent_context` in `gateway/server.py`). Any source that fails
degrades silently — it never blocks the AI turn.

To build the project code index so it is available for injection:

```bash
cd /your/project
navig index scan   # one-time or run regularly
```

---

<a id="25-autonomous-agent-mode"></a>
## 25. ⭐ Autonomous Agent Mode

NAVIG Agent Mode transforms your CLI tool into a living, autonomous entity that monitors, thinks, and acts.

### 25.1 Quick Start

```bash
# Install agent mode
navig agent install --personality friendly

# Start an interactive foreground session
navig agent start

# Check status
navig agent status

# JSON output now includes speculative runtime telemetry
navig agent status --plain
```

`navig agent start` runs as a foreground process. If the `console` channel is active,
you can type directly into the terminal and the agent replies inline. Telegram does
not come from this foreground loop — it is managed by `navig service start`.

### 25.2 Architecture

The agent uses a human-body metaphor:

| Component | Role |
|-----------|------|
| **Heart** | Orchestrator - manages component lifecycles |
| **Brain** | AI reasoning, planning, decisions |
| **Eyes** | System monitoring (CPU, memory, logs) |
| **Ears** | Input listeners (Telegram, MCP, API) |
| **Hands** | Safe command execution with approvals |
| **Soul** | Personality and communication style |
| **NervousSystem** | Async event bus for messaging |

### 25.3 Agent Commands

```bash
# Installation & Lifecycle
navig agent install [--personality <name>] [--mode <mode>]
navig agent start [--foreground|--background]
navig agent stop
navig agent status [--plain]

# Configuration
navig agent config --show       # Show full config
navig agent config --edit       # Edit in editor
navig agent config --set mode --value autonomous

# Personality
navig agent personality list    # List available
navig agent personality set professional

# Logs
navig agent logs --follow       # Follow log output
navig agent logs --level error  # Filter by level

# Service Management
navig agent service install     # Install as systemd/launchd
navig agent service status
navig agent service uninstall
```

`navig agent start --background` is currently a guidance-only path: it exits cleanly and tells you to use `navig service start` for daemon-backed Telegram/gateway workers.

`navig agent status --plain` includes a `speculative` object with effective tuning values and live cache metrics (when a live speculative executor is initialized), plus `daemon_running` and `daemon_pid` fields so you can distinguish the foreground agent process from the background daemon.

### 25.4 Configuration

Located at `~/.navig/agent/config.yaml`:

```yaml
agent:
  enabled: true
  mode: supervised  # autonomous, supervised, observe-only

  personality:
    profile: friendly
    name: NAVIG
    proactive: true

  brain:
    model: openrouter:anthropic/claude-3.5-sonnet
    temperature: 0.7

  eyes:
    monitoring_interval: 60
    disk_threshold: 85

  ears:
    telegram:
      enabled: false
      bot_token: ${TELEGRAM_BOT_TOKEN}
    mcp:
      enabled: true
      port: 8765
    email_accounts:
      - enabled: true
        provider: gmail          # gmail, outlook, fastmail, imap
        address: user@gmail.com
        password: ${NAVIG_EMAIL_PASSWORD}  # Gmail App Password
        label: Personal
        category: personal
        check_interval: 120      # seconds between polls
      - enabled: true
        provider: gmail
        address: work@company.com
        password: ${NAVIG_EMAIL_WORK_PASSWORD}
        label: Work
        category: work
        check_interval: 60

  hands:
    safe_mode: true
    sudo_allowed: false
```

### 25.5 Personality Profiles

Built-in profiles:

| Profile | Description |
|---------|-------------|
| `friendly` | Casual, uses emojis, proactive |
| `professional` | Formal, business-like |
| `witty` | Humorous, creative |
| `paranoid` | Security-focused, cautious |
| `minimal` | Terse, facts only |

Create custom profiles:

```bash
navig agent personality create mycustom
# Edit ~/.navig/agent/personalities/mycustom.yaml
```

### 25.5.1 Email Integration

The agent can monitor multiple email accounts via IMAP and route incoming messages through the ears system.

**Supported Providers:** `gmail`, `outlook`, `fastmail`, `imap` (generic)

**Setup (Gmail):**

1. Enable 2-Step Verification on your Google account
2. Generate an App Password at https://myaccount.google.com/apppasswords
3. Set environment variables:

```powershell
# PowerShell (session)
$env:NAVIG_EMAIL_PERSONAL_PASSWORD = "xxxx xxxx xxxx xxxx"
$env:NAVIG_EMAIL_myhost_PASSWORD  = "xxxx xxxx xxxx xxxx"

# Persistent (user level)
[Environment]::SetEnvironmentVariable("NAVIG_EMAIL_PERSONAL_PASSWORD", "xxxx xxxx xxxx xxxx", "User")
[Environment]::SetEnvironmentVariable("NAVIG_EMAIL_myhost_PASSWORD", "xxxx xxxx xxxx xxxx", "User")
```

1. Add accounts in `~/.navig/agent/config.yaml` under `ears.email_accounts` (see Section 25.4)

**Verification:**

```bash
navig agent status          # Shows email listener status
navig agent config show     # Verify email_accounts parsed
```

**Fields:**

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `true` | Enable/disable this account |
| `provider` | `gmail` | Provider type |
| `address` | | Email address |
| `password` | | App password (use `${ENV_VAR}` syntax) |
| `label` | | Friendly name (e.g., "Personal") |
| `category` | | Category for routing (e.g., "work") |
| `check_interval` | `60` | Seconds between inbox polls |
| `imap_host` | auto | Override IMAP host (generic provider) |
| `imap_port` | `993` | IMAP port |

### 25.5.2 SOUL.md - Deep Personality

For complete control over your agent's identity and conversational style, create a `SOUL.md` file:

**Location:** `~/.navig/workspace/SOUL.md`

```bash
# Show current SOUL.md
navig agent soul show

# Create from template
navig agent soul create

# Edit in your editor
navig agent soul edit

# Check file paths
navig agent soul path
```

#### Where identity comes from — the resolution chain

`SOUL.md` is one of seven sources. The first one that exists wins, and every
surface (chat, the deep-agent path, the CLI) uses the same order:

| # | Source | Path |
|---|--------|------|
| 1 | Active persona | `~/.navig/personas/<name>/soul.md` |
| 2 | Active space | `~/.navig/spaces/<space>/SOUL.md` |
| 3 | Folder space | `<project>/.navig/SOUL.md` |
| 4 | Workspace identity | `~/.navig/workspace/IDENTITY.md` |
| 5 | Workspace soul (legacy) | `~/.navig/workspace/SOUL.md` |
| 6 | Package default | shipped `SOUL.default.md` |
| 7 | Minimal fallback | shipped context `SOUL.md` |

A source you wrote yourself (1–5) is injected **verbatim**, capped at 4,000
characters. The shipped default is condensed to a hand-tuned identity block.

Note: the package-shipped `default` persona is deliberately skipped — it is a
pointer stub, and every install that never chose a persona reports `default`.
A `default` persona **you** create in `~/.navig/personas/default/` is honoured.

#### Guardrails — rules no identity file can remove

Safety rules do not live in `SOUL.md`. They are compiled into NAVIG and emitted
as `## Operating Rules` **before** your identity, so replacing `SOUL.md` changes
the agent's voice without removing its guardrails.

This holds on **every** surface the agent speaks on — chat, the deep-agent path,
`navig agent start`, the Telegram voice bot, the Deck's Ask box, the Deck board's
autonomous executor, and the operator planner. Surfaces whose output contract is
strict JSON carry a one-line form of the floor rather than the full block.

To **add** rules of your own, create `~/.navig/workspace/GUARDRAILS.md` (or
`<project>/.navig/GUARDRAILS.md` for one space). Its content is appended under
`### Operator additions`. There is no way to remove the built-in floor — a
`GUARDRAILS.md` that says "ignore the operating rules" adds a sentence and
subtracts nothing.

#### Auditing what actually reaches the model

```bash
navig agent context                      # what won, what was shadowed, section sizes
navig agent context --persona tyler      # resolve as if that persona were active
navig agent context --show-prompt        # print the assembled system prompt
navig agent context --json               # machine-readable
```

`navig doctor` carries the same information as a health section:

```
Identity
  ✓ Identity source: persona soul.md · 1,204 chars · persona tyler · shadows workspace IDENTITY.md
  ✓ Guardrail floor: v1 · 1,136 chars · +1 operator file(s)
  ✓ Prompt prefix: 4,878 chars · ~1,220 tok
```

A `⚠ Prompt prefix: … NOT byte-stable` row means something volatile crept into
the system block — the symptom is a silent multiple on your input bill, never an
error message.

This is the command to reach for when an identity edit "does nothing": a file
that is being outranked looks identical to a file that is broken until you can
see the shadow table. It also reports whether the cached prompt prefix is
byte-stable and the cache hit rate of the daemon's last real turn — send one
message first, or the cache row reads `no turn recorded yet`.

When SOUL.md is present, it's injected into the AI system prompt, enabling personality-driven responses to conversational queries:

| User Says | Agent Response (with SOUL.md) |
|-----------|------------------------------|
| "Hello" | Warm greeting + system status |
| "How are you?" | Friendly response referencing system health |
| "What is your name?" | Identity introduction from SOUL.md |
| "Who are you?" | Purpose and role description |

**SOUL.md Structure:**
```markdown
# SOUL.md - NAVIG Agent Personality

I am **NAVIG** — your server guardian.

## Who I Am
[Agent identity and origin story]

## My Purpose
[What the agent does]

## Conversational Guidelines
[How to respond to greetings, questions, etc.]

## My Values
[Core principles]
```

See [AGENT_MODE.md](AGENT_MODE.md) for full SOUL.md documentation.

### 25.6 Safety Features

- **Dangerous command detection**: Auto-blocks `rm -rf`, `drop`, `shutdown`
- **Approval system**: Critical operations require human confirmation
- **Safe mode**: Blocks sudo, limits concurrent commands
- **Configurable patterns**: Add custom confirmation requirements

#### Gateway policy gate (approval + audit)

Every privileged gateway action (`mission.create`, `mission.advance.*`, `system.shutdown`,
`system.stop`, `formation.start`, `task.add`, `node.register`, …) passes through a single
policy gate before executing. The decision model is `allow` / `require_approval` / `deny`,
driven entirely by config — no hardcoded lists:

```yaml
# ~/.navig/config.yaml
gateway:
  policy:
    default: allow            # decision for unmatched actions
    rules:                    # first match wins (fnmatch patterns)
      - pattern: "system.shutdown"
        action: require_approval
      - pattern: "mission.*"
        action: require_approval
approval:
  enabled: true               # master switch for the approval flow
  timeout_seconds: 120        # how long a gated request waits for a human
  default_action: deny        # timeout resolution (dangerous ops always deny)
```

- `deny` → rejected immediately (HTTP 403, `policy_denied`).
- `require_approval` → the request **blocks** until you approve or deny it — from the
  deck Inbox (Requests card), Telegram, or `POST /approval/{id}/respond`. Timeout, an
  approval-flow error, or no approval channel at all → denied. The gate fails closed,
  never open.
- `allow` → proceeds immediately (still audited).

**Agent tool calls are gated the same way.** When the chat agent (Telegram / deck) wants
to run a destructive tool (`bash_exec`, `db_query`, `cdp_eval`, …), the gateway routes the
confirmation through the same approval flow: the prompt lands in the deck Inbox / Telegram,
timeout follows `approval.default_action`, and the decision is audited as
`tool.execute.<tool_name>`. If the approval subsystem is unavailable inside the gateway the
tool call is **denied** — never silently approved. A denied call returns a clean
`[Denied: …]` result the agent reads and adapts to. Headless CLI runs (no gateway) keep the
single-operator default — dangerous tools log a loud warning and proceed — and
`NAVIG_ALLOW_ALL_COMMANDS=1` bypasses the gate for pre-screened automation. To pin a
specific tool, add an `approval.levels` pattern matching `tool <name>` (e.g.
`"tool cdp_eval*"` under `dangerous:` makes its timeout always deny).

Every decision is appended to the structured audit log at
`~/.navig/runtime/audit.jsonl` — who / what / when / decision / matched rule; inputs
are stored as SHA-256 hashes, never verbatim. Inspect it from the terminal (reads the
file directly — no daemon required) or over the gateway API:

```bash
navig audit tail                        # latest 20 records, house table
navig audit tail -n 50 --status denied  # what was refused, and why
navig audit tail --action tool.execute  # gated agent tool calls only
navig audit tail --json                 # machine-readable (path, total, events)

GET  /audit?limit=50&action=mission&actor=telegram:123&status=denied
GET  /approval/pending                  # requests currently waiting for a human
POST /approval/{id}/respond             # body: {"approved": true|false}
```

`navig audit tail` filters mirror `GET /audit`: `--action` is a prefix match,
`--actor` and `--status` are exact; `--path` inspects a copied/rotated file. A missing
or empty log is an honest non-failure (exit 0). Pending records are actionable via
`navig approve list` / `navig approve yes|no <id>`.

Hard-deny patterns (`system.delete_all`, `*.drop_all`) can never be overridden by
config.

### 25.7 Self-Healing & Learning

NAVIG agent includes advanced autonomous capabilities:

#### Self-Healing Auto-Remediation

Automatically recovers from component failures:

```bash
# View remediation actions
navig agent remediation list

# Check specific action status
navig agent remediation status --id <action_id>

# Clear completed actions
navig agent remediation clear
```

**Features:**
- Exponential backoff retry (1s → 2s → 4s → 8s → 16s → 60s)
- Automatic component restart on failure
- Configuration rollback to last known good state
- Connection retry with intelligent delays
- Comprehensive logging to `~/.navig/logs/remediation.log`

**How It Works:**
1. Heart detects component failure during health check
2. Remediation engine schedules restart with backoff
3. Maximum 5 attempts before manual intervention required
4. All actions logged and trackable

See [AGENT_SELF_HEALING.md](AGENT_SELF_HEALING.md) for details.

#### Learning System

Analyzes logs to detect error patterns and provide recommendations:

```bash
# Analyze last 7 days of logs
navig agent learn

# Analyze custom time range
navig agent learn --days 30

# Export patterns to JSON
navig agent learn --export
```

**Detected Patterns:**
- Connection failures (SSH, network timeouts)
- Permission denied errors
- Configuration parsing errors
- Component startup failures
- Resource exhaustion (memory, disk, quota)

**Output:**
- Error count by category
- Example log entries
- Actionable recommendations
- Exported to `~/.navig/workspace/error-patterns.json`

See [AGENT_LEARNING.md](AGENT_LEARNING.md) for details.

### 25.8 Operating Modes

| Mode | Behavior |
|------|----------|
| `autonomous` | Acts independently, asks approval for destructive ops |
| `supervised` | Suggests actions, waits for human approval |
| `observe-only` | Monitors and reports, never executes |

### 25.9 Service Installation

Deploy NAVIG agent as a 24/7 system service:

```bash
# Install (auto-detects platform)
navig agent service install

# On Linux (systemd)
navig agent service install --user  # User service (recommended)
navig agent service install          # System service (requires sudo)

# Check status
navig agent service status

# Uninstall
navig agent service uninstall
```

**Features:**
- **Linux**: systemd user/system units, auto-restart on failure
- **macOS**: launchd LaunchAgent, auto-start on login
- **Windows**: Windows Service via nssm or sc.exe

**Service Management:**

```bash
# Linux
systemctl --user start navig-agent
systemctl --user enable navig-agent
journalctl --user -u navig-agent -f

# macOS
launchctl load ~/Library/LaunchAgents/com.navig.agent.plist
tail -f ~/.navig/agent.log

# Windows
Start-Service navig-agent
Get-Service navig-agent | Format-List *
Get-EventLog -LogName Application -Source "navig-agent"
```

See [AGENT_SERVICE.md](AGENT_SERVICE.md) for full installation guide, troubleshooting, and security considerations.

### 25.10 Goal Planning

Create high-level goals that the agent decomposes and executes:

```bash
# Add a goal
navig agent goal add --desc "Deploy application to production"

# List all goals
navig agent goal list

# Check goal progress
navig agent goal status --id <goal_id>

# Cancel a goal
navig agent goal cancel --id <goal_id>
```

**Example: Deployment Goal**

```bash
$ navig agent goal add --desc "Deploy v2.0 to production"

✓ Goal added: e78423a1

  Description: Deploy v2.0 to production
  ID: e78423a1

ℹ The agent will decompose this goal into subtasks
Check progress with: navig agent goal status --id e78423a1
```

**Goal Decomposition** (automatic with Brain AI):
1. Backup database
2. Run migrations (depends on 1)
3. Update configuration (depends on 2)
4. Restart services (depends on 3)
5. Run health checks (depends on 4)
6. Notify team (depends on 5)

**Goal States:**
- `PENDING` - Awaiting decomposition
- `DECOMPOSING` - AI breaking down into subtasks
- `IN_PROGRESS` - Executing subtasks
- `BLOCKED` - Waiting on dependency or manual intervention
- `COMPLETED` - All subtasks done
- `FAILED` - Cannot proceed
- `CANCELLED` - Manually stopped

**Dependency Tracking:**

Subtasks can depend on others completing first. Agent automatically determines execution order and only runs subtasks when dependencies are met.

See [AGENT_GOALS.md](AGENT_GOALS.md) for comprehensive guide on goal planning, dependencies, and advanced usage.

---

<a id="26-information-retrieval"></a>
## 26. ⭐ Information Retrieval (Web Search, Prices, Weather)

NAVIG now understands information retrieval queries beyond DevOps operations. When you ask about prices, weather, or general knowledge, NAVIG intelligently routes your query to the appropriate handler.

### 26.1 Web Search

**Natural Language Triggers:**
```
"Search the web for Python tutorials"
"Look up best practices for Docker"
"Google Kubernetes deployment"
"Find information about microservices"
"Go to the web and search for..."
```

**How It Works:**
1. NAVIG detects web search intent from your natural language
2. If `brave-search` MCP server is enabled, executes live web search
3. Returns formatted results with links
4. Falls back to AI knowledge + setup instructions if unavailable

**Enable Web Search:**
```bash
# Install MCP brave-search
navig mcp install brave-search

# Enable the server
navig mcp enable brave-search

# Inspect one server (type, command, enabled state); add --json for scripts
navig mcp info brave-search
navig mcp info brave-search --json   # secret-free JSON (env values never included)

# Set your API key (get from https://brave.com/search/api/)
navig config set mcp.brave-search.env.BRAVE_API_KEY=your-api-key
```

### 26.2 Price & Cryptocurrency Queries

**Natural Language Triggers:**
```
"Price of bitcoin"
"How much is ethereum?"
"What's the BTC value today?"
"Crypto prices"
"ETH price"
```

**Supported Assets:**
- Cryptocurrencies: bitcoin, ethereum, dogecoin, solana, cardano, ripple
- Aliases: btc, eth, doge, sol, ada, xrp
- Any asset (routed to web search)

**Response Format:**
- Live prices via web search (if enabled)
- Links to CoinGecko/CoinMarketCap (fallback)
- Instructions to enable live data

### 26.3 Weather Queries

**Natural Language Triggers:**
```
"Weather in New York"
"Temperature in London"
"What's the forecast for Tokyo?"
"Is it going to rain in Paris?"
```

**How It Works:**
1. Extracts location from your query
2. Routes to web search for live weather data
3. Provides weather.com links as fallback

### 26.4 Factual Questions

**Natural Language Triggers:**
```
"Who is Elon Musk?"
"What is quantum computing?"
"Explain machine learning"
"When did World War 2 end?"
"Where is the Eiffel Tower?"
```

**Smart Routing:**
- Questions WITHOUT DevOps keywords → Web search
- Questions WITH DevOps keywords → DevOps handlers
- DevOps keywords: server, container, docker, database, disk, memory, cpu, host, app, deploy, restart, log, ssh

**Example Distinction:**
```
"What is Docker?" → Web search (general knowledge)
"What is the Docker container status?" → DevOps (container list)
```

### 26.5 Configuration

No configuration required for intent detection. Web search requires MCP setup:

```yaml
# ~/.navig/mcp/servers.yaml
brave-search:
  enabled: true
  env:
    BRAVE_API_KEY: "your-key-here"
```

#### Trusting an MCP server (`mcp.trust`)

A third-party MCP server defines its own tools, so NAVIG cannot tell from a tool's name
whether it reads or writes. It therefore **holds every external tool call for your
approval by default**, and lifts that only where the server explicitly declares a tool
read-only (`readOnlyHint: true` in the MCP tool annotations).

```bash
# Vouch for one server's annotations — its declared reads then run unprompted
navig config set mcp.trust.servers.brave-search vetted

# Change the tier every other server gets (default: byo)
navig config set mcp.trust.default byo

# Paranoid: ignore readOnlyHint entirely, so EVERY external tool asks
navig config set mcp.trust.honor_read_only_hint false
```

Two tiers:

| Tier | Meaning |
|------|---------|
| `byo` (default) | You pasted a URL. `readOnlyHint` still marks reads, but nothing the server says can let a **write** through without you. |
| `vetted` | You assert this server's annotations are reliable. Only a vetted server's writes can ever become auto-approvable. |

Anything other than `vetted` or `byo` is read as `byo` — an unrecognised value never
resolves to the more permissive setting.

**Limiting which tools a server may offer.** Trust says how far a server's own claims
are believed; *scope* says which of its tools you will use at all. This matters for a
vetted server in particular: its declared reads run unprompted, so a tool it adds
tomorrow is unprompted the day it appears.

```bash
# only these two tools from this server; everything else is refused outright
navig config set mcp.trust.servers.brave-search.tools "brave_web_search,brave_local_search"
```

Or in `~/.navig/config.yaml`, where both settings sit together:

```yaml
mcp:
  trust:
    default: byo
    servers:
      brave-search:
        tier: vetted
        tools: [brave_web_search, brave_local_search]
```

Omit `tools` entirely for "every tool, now and later". A `tools` key that names nothing
denies everything from that server — a typo must not silently become full access.

NAVIG also fingerprints each server's tool catalog and warns when the claims a trust
setting was made against change (a new tool, or `readOnlyHint` flipping on an existing
one). Description edits do not trigger it.

**Letting a routine call stop asking.** Being prompted for the same harmless call fifty
times is how people end up disabling approvals entirely, so a tool can be pre-authorised
— but it takes **two independent agreements**, and neither party can do it alone:

```bash
navig config set mcp.trust.servers.acme vetted                 # 1. you trust its claims
navig config set mcp.trust.servers.acme.auto_approve "sync_issue"   # 2. you name the tool
```

The tool then runs without a prompt only if the **server** also declares it
non-destructive *and* idempotent in its annotations. A server saying so on its own is
marking its own homework; you naming a tool the server never made that claim about is a
blank cheque against a description that can change under you. Both, or it asks.

Nothing is pre-authorised by default, and there is deliberately no "allow everything"
spelling. Auto-approved calls are still written to the audit log, recording which config
key allowed them — an unprompted write nobody can account for afterwards is the thing
this is meant to avoid. `navig doctor` lists pre-authorised tools, and warns if you set
them on a server that is not `vetted` (where the setting does nothing).

First-party tools deliberately do not get this: there is no server-side claim to pair
your opt-in with, so it would just be a narrower `--yes`.

**Adding a server now asks first.** `POST /mcp/connect` registers a stdio server by
running a binary as you, and keeping it running — more powerful than the shell tool the
agent cannot use without asking. It is now gated the same way.

A denied call names the fix:

```
Operator did not approve MCP tool 'delete_repo' on server 'acme'. If this server's
tools are trustworthy, classify it with: navig config set mcp.trust.servers.acme vetted
```

> **Why the default is strict.** The approval gate used to match tool names against a
> list of NAVIG's *own* tools, so a name it had never seen was treated as safe —
> third-party MCP tools ran with no prompt and no audit line. External tools are now
> namespaced `mcp__<server>__<tool>`, which both marks them as externally defined and
> stops a server publishing a tool called `bash_exec` from shadowing yours.

### 26.6 Troubleshooting

**"I can't fetch real-time data"**
- Web search MCP server is not enabled
- Run: `navig mcp enable brave-search`
- Ensure API key is set

**Query misclassified as DevOps?**
- Add "search for" or "look up" prefix
- Example: "Search for bitcoin price" vs just "bitcoin price"

**No results returned?**
- Check all servers at a glance: `navig mcp list`
- Drill into one: `navig mcp info brave-search` (type · command · enabled) — `--json` for scripts
- Verify API key is valid

### 26.7 URL Investigation (Web Fetch)

NAVIG can fetch and analyze web pages when you share URLs. This is useful for:
- Reading article content without leaving the terminal
- Summarizing web pages
- Comparing information from multiple sources
- Extracting key data from websites

**Natural Language Triggers:**
```
"Check this: https://example.com/article"
"Investigate https://github.com/user/repo"
"What does this page say? https://..."
"Summarize https://blog.example.com/post"
"Read this URL and tell me what it's about"
"Compare https://site1.com and https://site2.com"
```

**How It Works:**
1. NAVIG detects URL in your message
2. Fetches page content via HTTP GET
3. Extracts main content (removes navigation, ads, scripts)
4. Converts to clean markdown/text
5. Returns content or AI summary based on your request

**MCP Tools (for AI integrations):**
```json
navig_web_fetch    - Fetch and extract content from a URL
navig_web_search   - Search the web (Brave/DuckDuckGo fallback)
navig_search_docs  - Search NAVIG documentation
```

**Python Usage:**
```python
from navig.tools.web import web_fetch, web_search, search_docs

# Fetch a URL
result = web_fetch("https://example.com/article", extract_mode="markdown")
if result.success:
    print(f"Title: {result.title}")
    print(f"Content: {result.text[:1000]}...")

# Search the web
results = web_search("Python best practices", count=5)
for r in results.results:
    print(f"- {r.title}: {r.url}")

# Search local docs
docs = search_docs("ssh tunnel", max_results=3)
for d in docs:
    print(f"- {d['title']}: {d['excerpt']}")
```

### 26.8 Documentation Search (navig docs)

Search NAVIG's own documentation from the command line.

**Commands:**
```bash
# List all documentation topics
navig docs

# Search for specific topics
navig docs "ssh tunnel"
navig docs "database backup"
navig docs "docker"

# JSON output (for automation)
navig docs --json "config"
```

**Output Includes:**
- File path and document title
- Relevant excerpt with matching content
- Relevance score

### 26.9 CLI Commands: `navig fetch` and `navig search`

Direct CLI commands for web content tools (no AI required).

#### `navig fetch <url>`

Fetch and extract content from any URL.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | required | URL to fetch |
| `--mode`, `-m` | string | markdown | Extraction mode: markdown, text, raw |
| `--max-chars`, `-c` | int | 50000 | Maximum characters to extract |
| `--timeout`, `-t` | int | 30 | Request timeout in seconds |
| `--json` | flag | false | Output in JSON format |
| `--plain` | flag | false | Plain text output |

**Examples:**
```bash
# Fetch and display as markdown
navig fetch https://example.com

# Fetch as plain text
navig fetch https://news.ycombinator.com --mode text

# JSON output for automation
navig fetch https://docs.python.org/3/ --json

# Limit content size
navig fetch https://github.com/user/repo --max-chars 10000
```

#### `navig search <query>`

Search the web for information.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | required | Search query |
| `--limit`, `-l` | int | 10 | Maximum results |
| `--provider`, `-p` | string | auto | Provider preference: auto (Firecrawl-first), firecrawl, brave, duckduckgo, tavily, perplexity, gemini, grok, kimi |
| `--json` | flag | false | Output in JSON format |
| `--plain` | flag | false | Plain text output |

**Examples:**
```bash
# Basic search
navig search "Python best practices"

# Limit results
navig search "Docker tutorial" --limit 5

# JSON output
navig search "kubernetes deployment" --json

# Force specific provider
navig search "nginx configuration" --provider duckduckgo
```

**Provider behavior (default):**

- `auto` uses Firecrawl first.
- If Firecrawl is unavailable/unconfigured at runtime, NAVIG gracefully falls back to legacy providers.

**Required Firecrawl key setup:**
```bash
# Firecrawl requests require an API key (even on free credits)
navig cred add firecrawl --key fc-xxxxxxxx --label "Firecrawl API Key"
```

**Legacy provider setup (optional):**
```bash
# 1. Get API key from https://brave.com/search/api/
# 2. Set in environment or config
export BRAVE_API_KEY="your-api-key"
# Or: navig config set web.search.api_key=YOUR_KEY
```

### 26.10 Firecrawl Scraping & Crawling (MCP + REST Fallback)

NAVIG now exposes Firecrawl as a first-class scraping/crawling capability via MCP tool `firecrawl_scrape`.

- Firecrawl requests require an API key.
- NAVIG resolves key automatically from `FIRECRAWL_API_KEY` or vault labels.
- MCP path is preferred when available; otherwise NAVIG logs and falls back to direct REST.

**API key setup (vault-validated):**
```bash
# Key is validated against Firecrawl /v1/account before save
navig cred add firecrawl --key fc-xxxxxxxx --label "Firecrawl API Key"
```

If validation fails, NAVIG blocks save and prints an inline error.

---

<a id="27-advanced-ai-features-new"></a>
## 27. ⭐ Advanced AI Features (NEW)

### 27.1 Perplexity Search Provider

Real-time web search with AI synthesis using Perplexity's Sonar API.

**Setup:**
```bash
# Direct Perplexity API (pplx-xxx keys)
export PERPLEXITY_API_KEY="pplx-xxxxxxxx"

# Or via OpenRouter (sk-or-xxx keys)
export OPENROUTER_API_KEY="sk-or-xxxxxxxx"
```

**Available Models:**
| Model | Description | Best For |
|-------|-------------|----------|
| `sonar` | Fast search | Quick lookups |
| `sonar-pro` | Comprehensive | Detailed research |
| `sonar-reasoning` | Deep analysis | Complex questions |

**Python Usage:**
```python
from navig.providers.perplexity import perplexity_search, is_perplexity_available

if is_perplexity_available():
    result = await perplexity_search("What is the latest Python version?")
    print(result.answer)
    print(f"Sources: {result.citations}")
```

### 27.2 Discord Integration

Full Discord bot integration for NAVIG Gateway.

**Setup:**
```bash
# Install discord.py
pip install discord.py

# Set bot token
export DISCORD_BOT_TOKEN="your-bot-token"
```

**Features:**
- Slash commands: `/navig <query>`, `/status`, `/help`
- @mention responses in server channels
- Direct message (DM) support
- Permission system (guild, user, channel restrictions)

**Configuration:**
```python
from navig.gateway.channels import get_discord_channel

discord_channel = get_discord_channel(
    token="your-bot-token",
    allowed_guilds=[123456789],      # Optional: restrict to guilds
    allowed_users=[987654321],       # Optional: restrict to users
    respond_to_mentions=True,
    respond_to_dms=True
)
```

### 27.3 WhatsApp Integration

WhatsApp Web integration via whatsapp-web.js bridge.

**Prerequisites:**
- External whatsapp-web.js bridge server running
- QR code authentication completed

**Setup:**
```bash
pip install aiohttp websockets
```

**Configuration:**
```python
from navig.gateway.channels import get_whatsapp_channel

whatsapp_channel = get_whatsapp_channel(
    bridge_url="http://localhost:3000",
    bridge_ws_url="ws://localhost:3000/ws",
    allowed_numbers=["+1234567890"],   # Optional: restrict numbers
    allowed_groups=["GROUP_ID@g.us"],  # Optional: restrict groups
    respond_to_groups=True
)
```

### 27.4 Image Generation

AI-powered image generation with multiple providers.

**Supported Providers:**
| Provider | Model | API Key Env Var |
|----------|-------|-----------------|
| OpenAI | DALL-E 3 | `OPENAI_API_KEY` |
| Stability AI | SDXL | `STABILITY_API_KEY` |
| Local | A1111/ComfyUI | `LOCAL_SD_URL` |

**Usage:**
```python
from navig.tools import get_image_generator

generator = get_image_generator()
result = await generator.generate(
    prompt="A serene mountain landscape at sunset",
    provider="openai",      # or "stability", "local"
    size="1024x1024",
    quality="hd"
)

print(f"Image saved to: {result.local_path}")
```

**CLI (coming soon):**
```bash
navig image generate "A serene mountain landscape" --provider openai
```

### 27.5 Docker Sandbox Execution

Secure, isolated command execution in Docker containers.

**Features:**
- Resource limits (memory, CPU, disk)
- Network isolation
- Security hardening (`--read-only`, `--cap-drop ALL`)
- Automatic cleanup

**Usage:**
```python
from navig.tools import get_sandbox

sandbox = get_sandbox(
    memory_limit="256m",
    cpu_limit=1.0,
    network_enabled=False,
    timeout=30
)

result = await sandbox.execute(
    code="print('Hello from sandbox!')",
    language="python"
)

print(f"Output: {result.stdout}")
print(f"Exit code: {result.exit_code}")
```

### 27.6 Agent-to-Agent Coordination

Multi-agent orchestration for complex workflows.

**Agent Roles:**
| Role | Description |
|------|-------------|
| `COORDINATOR` | Orchestrates tasks, delegates work |
| `SPECIALIST` | Domain expert (e.g., DevOps, database) |
| `WORKER` | Executes assigned tasks |
| `MONITOR` | Observes and reports system state |

**Usage:**
```python
from navig.agent.coordination import AgentCoordinator, AgentRegistry

# Create registry and coordinator
registry = AgentRegistry()
coordinator = AgentCoordinator(registry)

# Register agents
await registry.register(
    agent_id="devops-agent",
    role="specialist",
    capabilities=["docker", "kubernetes", "ssh"]
)

# Delegate task
result = await coordinator.delegate_task(
    task="Deploy application to production",
    required_capabilities=["docker", "ssh"]
)
```

---

<a id="28-operations-dashboard-tui"></a>
## 28. ⭐ Operations Dashboard TUI

Real-time terminal-based dashboard for infrastructure monitoring and operations overview.

### 28.1 Overview

The dashboard provides:
- **Host Health Panel**: Live SSH connectivity status with latency
- **Docker Panel**: Container status for active host
- **History Panel**: Recent operations from command history
- **Resources Panel**: CPU, memory, disk overview

### 28.2 Basic Usage

```bash
# Full live dashboard (auto-refresh every 5 seconds)
navig dashboard

# Single snapshot (no live updates)
navig dashboard --no-live

# Custom refresh interval
navig dashboard --refresh 10
navig dashboard -r 3
```

### 28.3 Dashboard Panels

#### Host Health Panel
Shows all configured hosts with:
- Connection status indicator (green = connected, red = failed)
- IP address
- Response latency in milliseconds
- Active host highlighted in green

#### Docker Panel
For the active host, displays:
- Running containers
- Container status
- Port mappings
- Image names (truncated)

#### History Panel
From the operation history system:
- Last 8 operations
- Timestamp
- Command (truncated)
- Target host
- Success/failure indicator

#### Resources Panel
For the active host:
- CPU usage percentage
- Memory usage
- Disk usage
- System load average

### 28.4 Keyboard Controls

| Key | Action |
|-----|--------|
| `Q` | Quit dashboard |
| `R` | Force refresh |
| `Ctrl+C` | Exit |

### 28.5 Requirements

- Interactive terminal (TTY)
- Rich library (included with NAVIG)
- Host connectivity for live status updates

### 28.6 Tips

- Use `--no-live` if your terminal doesn't support full-screen mode
- Increase `--refresh` interval on slow connections
- Use `navig status` for quick non-interactive status checks
- Dashboard integrates with the history system—run commands to see them appear

---

<a id="29-command-history--replay"></a>
## 29. ⭐ Command History & Replay

NAVIG records all operations for auditing, replay, and debugging. Track what was done, when, and by whom—then replay or undo operations as needed.

### 29.1 Overview

The history system provides:
- **Full Operation Recording**: Every CLI command is logged with context
- **Time Travel**: View historical operations with filtering
- **Replay**: Re-execute past operations safely
- **Undo**: Reverse operations where possible
- **Audit Trail**: Export history for compliance and debugging

**Storage Location:** `~/.navig/history/operations.jsonl`

### 29.2 History Commands

| Command | Description |
|---------|-------------|
| `navig history list` | List recent operations |
| `navig history show <id>` | Show operation details |
| `navig history replay <id>` | Re-execute an operation |
| `navig history undo <id>` | Reverse an operation |
| `navig history export` | Export to JSON/CSV |
| `navig history clear` | Clear history |
| `navig history stats` | Show statistics |

### 29.3 Listing History

The filters live on the `list` subcommand — `navig history` on its own is a command
group and takes none of them.

```bash
# List recent operations (default: last 20)
navig history list

# List more operations
navig history list --limit 50

# Filter by operation type
navig history list --type ssh
navig history list --type docker
navig history list --type database

# Filter by status
navig history list --status success
navig history list --status failed

# Filter by host
navig history list --host production

# Filter by time range (relative forms: 1h, 24h, 7d)
navig history list --since 1h
navig history list --since 7d

# Search the command text
navig history list --search "systemctl"

# Combine filters
navig history list --type ssh --status failed --since 24h
```

⚠ There is no `--until` — `--since` gives an open-ended window from a point in the past.
Output can be redirected with `--plain` or `--json`.

**Operation Types:**
| Type | Description |
|------|-------------|
| `ssh` | Remote command execution |
| `database` | Database queries/operations |
| `docker` | Container management |
| `file` | File transfers (upload/download) |
| `service` | Service start/stop/restart |
| `backup` | Backup operations |
| `config` | Configuration changes |
| `deploy` | Deployment operations |

### 29.4 Viewing Operation Details

```bash
# Show full details of an operation
navig history show abc123

# Output includes:
# - Full command with arguments
# - Execution timestamp
# - Duration
# - Host/context
# - Status and exit code
# - Output/error messages
# - Related operations
```

### 29.5 Replaying Operations

Replay allows you to re-execute a past operation:

```bash
# Replay an operation (with confirmation)
navig history replay abc123

# Replay without confirmation (dangerous!)
navig history replay abc123 --yes

# Dry-run to see what would happen
navig history replay abc123 --dry-run

# Replay with modifications
navig history replay abc123 --modify host=staging
navig history replay abc123 --modify timeout=60
```

**Safety Features:**
- Operations are replayed with current context
- Destructive operations require confirmation
- Dry-run mode shows the command without executing
- Modifications allow adapting the replay

### 29.6 Undoing Operations

Some operations support undo:

```bash
# Undo an operation (if reversible) — prompts before acting
navig history undo abc123

# Skip the confirmation prompt
navig history undo abc123 --yes
```

⚠ There is no `--force` or `--dry-run` on `undo`; `--yes` is the only option, and it
only skips the prompt. To see what an operation did before reversing it, read it first
with `navig history show abc123`.

**Undoable Operations:**
| Operation | Undo Action |
|-----------|-------------|
| File upload | Delete uploaded file |
| Service start | Service stop |
| Service stop | Service start |
| Docker container start | Container stop |
| Docker container stop | Container start |
| Config change | Restore previous config |

**Non-Undoable Operations:**
- Database DELETE/DROP queries
- File deletions
- Destructive remote commands

### 29.7 Exporting History

Export history for analysis or compliance:

`export` takes the destination as a required ARGUMENT, not a redirect.

```bash
# Export to JSON (default)
navig history export audit.json

# Export to CSV
navig history export audit.csv --format csv

# Cap how many entries are written (default 1000)
navig history export audit.json --limit 5000
```

⚠ `export` has no filters — its only options are `--format` and `--limit`. To narrow by
time, status or type, use `navig history list --status failed --json` and redirect that.

### 29.8 Statistics

View operation statistics:

```bash
# Show overall stats
navig history stats

# Output includes:
# - Total operations recorded
# - Operations by type
# - Operations by status
# - Operations by host
# - Most active time periods
# - Average operation duration
```

### 29.9 Managing History

```bash
# Clear all history (prompts first)
navig history clear

# Skip the confirmation prompt
navig history clear --yes
```

⚠ `clear` is all-or-nothing — there is no `--keep-days` and no `--status`. Export first
with `navig history export audit.json` if you need to keep a copy.

### 29.10 Integration with Other Commands

Operations are recorded automatically when you run NAVIG commands:

```bash
# This SSH command is automatically recorded
navig ssh production "systemctl restart nginx"

# View the recorded operation
navig history list --limit 1
```

The history system integrates with:
- **Context Management**: Operations tagged with current context
- **Workflows**: Multi-step workflows recorded as related operations
- **Agent Mode**: Agent actions fully audited
- **Memory System**: History informs AI suggestions

### 29.11 Ledger Integrity — `navig ledger verify`

Every entry written to `operations.jsonl` carries hash-chain fields: `prev`
(the previous entry's fingerprint) and `hash` (sha256 over the previous
fingerprint plus the entry's canonical JSON). Delete, edit, or reorder any
line and the fingerprints stop matching.

```bash
# Re-walk the whole ledger and re-check every link
navig ledger verify

# ✓ 12,431 operations, chain intact
# ✗ Chain broken at line 8,204: hash mismatch (entry rewritten or corrupted)

# Machine-readable report (status, counts, breaks by line, restarts, anchor)
navig ledger verify --json

# Verify a specific file (e.g. the rotated backup)
navig ledger verify --path ~/.navig/history/operations.jsonl.bak
```

**Exit codes:** `0` = intact (including honest non-failure states: no ledger
yet, empty ledger, legacy pre-chain file) · `1` = chain broken.

**Honest scope:** the chain is *tamper-evident, not tamper-proof* — an
attacker who can rewrite the whole file can recompute the whole chain. What
you get is integrity evidence: casual edits, lost lines, reordering, and
corruption are detected and named by line.

**Wrinkles handled:**
- History rotation keeps recent lines verbatim, so the chain survives it; the
  first surviving entry still names a rotated-out hash (reported as the
  *anchor*, not a break).
- `navig history clear` legitimately ends the chain; the next operation
  starts a fresh one (a clean restart, never a failure).
- Entries recorded before the chain existed are counted as *legacy
  (unchained)* — they cannot be verified retroactively.

**Inspecting the ledger — `navig ledger show`:**

```bash
# Recent operations with chain state + reversibility labels
navig ledger show
navig ledger show --tail 50
navig ledger show --json     # one machine-readable document
```

Per entry: hash-chain state (`✓` verified · `○` legacy pre-chain · `✗` broken
line), the green/yellow/red reversibility label (§29.12), status, and whether
it was already undone. `show` is a view, not a gate — it always exits 0
(`verify` carries the exit-code contract), it never appends to the ledger it
displays, and command text passes through secret redaction before display.

### 29.12 Reversibility & `navig undo`

Every recorded operation carries an honest reversibility label:

| Label | Meaning |
|-------|---------|
| `● green` | **Undoable** — the previous state (`undo_data`) was captured at execution time and `navig undo` can replay it |
| `● yellow` | **Compensable / conditional** — a manual counter-action exists (start the service again, delete the uploaded copy); NAVIG names it but cannot replay it |
| `● red` | **Irreversible** — data deleted, message sent, arbitrary command. Unknown operation types default to red |

`navig config set` is the first green seam: it records the old value (or
"did not exist before") so the change can be replayed backwards. Changes to
secret-bearing keys (API keys, tokens, passwords) are captured **without
plaintext** — a vault reference only — and are yellow: secrets never sit in
the ledger and never replay from it.

```bash
# Preview what could be undone (state: ready / undone / drift)
navig undo --list

# Undo the LAST green operation — shows exactly what will be restored, asks first
navig undo

# Undo a specific operation, skipping the prompt
navig undo op-20260716120000-abcd1234 --yes

# Machine mode (requires --yes; prompts would corrupt the JSON stream)
navig undo --json --yes
```

Safety rules (enforced, not advisory):

- **Green only.** Yellow refusals include the compensation hint; red is an
  honest "cannot be taken back".
- **Never twice.** Every undo is itself recorded on the hash chain (tagged
  `undo`, `args.undo_of = <target>`) and capped at yellow, so it never becomes
  an undo candidate; a target with a successful undo entry is refused forever
  after. Re-run the original command to redo.
- **Drift detection.** If the target changed again since the operation (the
  config key was re-set, the host switched again, the file was modified), the
  undo is refused with what/why instead of overwriting newer state.
- **The last green means the last green.** When the most recent green
  operation is already undone or drifted, `navig undo` refuses with the
  reason — it never silently skips to an older operation. Target older ones
  explicitly by id.

`navig history undo <id|index>` uses the same engine with the same rules.

### 29.13 Distilling a skill from history — `navig skill distill`

You do a real task through NAVIG — a dozen commands, two failed attempts before it
worked. `navig skill distill` reads that slice of the operations ledger and writes the
recipe: the successful path as ordered steps, the failed attempts as pitfall warnings,
and reversibility labels (§29.12) as danger annotations — turning work done once into a
reusable, shareable `SKILL.md`.

```bash
# Distill the last 2 hours into a draft skill (default window: 2h)
navig skill distill --last 2h

# Tighter window + an explicit name
navig skill distill --last 30m --name deploy-hotfix

# Distill exactly these operations (ids from `navig ledger show`)
navig skill distill --ops op-20260716120000-a1b2c3d4,op-20260716120500-e5f6a7b8

# Write next to your project instead of the user skill store
navig skill distill --last 1h --out ./skills

# Improve the prose with AI (needs a provider; commands stay verbatim)
navig skill distill --last 2h --ai

# Machine-readable summary (one JSON document)
navig skill distill --last 1h --json
```

**What ends up in the recipe:**

- **Steps** — only operations that *succeeded*. Failed, undone (§29.12), and the
  distill command's own invocations are excluded; consecutive identical commands
  collapse into one step with a run count.
- **Pitfalls** — the failed attempts, with their (redacted) error and exit code, under
  a "don't repeat these" heading — the "don't do X, it fails with Y" wisdom that only
  comes from the mistakes.
- **Danger annotations** — each step is tagged with its reversibility label; the
  skill's `safety` is the worst step's label (a `red` step ⇒ `destructive`).
- **Placeholders** — values that look instance-specific are replaced conservatively:
  `<host>`, `<user>` (home-directory usernames), `<email>`, `<ip>`. When unsure the
  literal stays, marked for you to review.

**Secrets never leave the machine.** Before anything is written (or sent to the optional
`--ai` drafter), the command sweeps every step for secrets — known token patterns,
`--password`/`--token` flag values, sensitive `key=value` pairs, and opaque long tokens
all become `<secret>` (never with an example value). This runs *first*, before
placeholder extraction.

**It's a draft, not a finished skill.** Review it, then lint it:

```bash
navig skill lint <path-it-printed>
```

The draft always writes real YAML frontmatter with a routing-rich description, so it
passes the authoring standard out of the box — but the *intent* (name, which steps
matter) is yours to refine. `--force` overwrites an existing skill of the same name;
without it, distill refuses rather than clobber. The distill run records its own ledger
line as a green, undoable `file_create`, so `navig undo` removes the draft if you don't
want it.

---

<a id="30-intelligent-suggestions--quick-actions"></a>
## 30. ⭐ Intelligent Suggestions & Quick Actions

NAVIG learns from your usage patterns and provides intelligent command suggestions plus quick action shortcuts.

### 30.1 Command Suggestions

Get smart recommendations based on history, context, and patterns:

```bash
# Show suggestions
navig suggest

# Filter by context
navig suggest --context docker
navig suggest --context database
navig suggest --context deployment
navig suggest --context monitoring

# Run a suggestion directly
navig suggest --run 1
navig suggest --run 2 --dry-run

# Output formats
navig suggest --plain
navig suggest --json
```

### 30.2 Suggestion Sources

| Icon | Source | Description |
|------|--------|-------------|
| H | History | Most frequently used commands |
| S | Sequence | Commands that typically follow your last action |
| T | Time | Typical commands for current time of day |
| C | Context | Commands relevant to detected project type |

### 30.3 Context Detection

NAVIG automatically detects project type from files in your directory:

| File/Directory | Detected Context |
|----------------|------------------|
| `docker-compose.yml`, `Dockerfile` | Docker |
| `*.sql`, `migrations/` | Database |
| `deploy/`, `ansible/` | Deployment |
| `prometheus.yml`, `grafana/` | Monitoring |

### 30.4 Quick Actions

Save frequently used commands as shortcuts:

```bash
# List all quick actions
navig quick
navig quick list

# Add a quick action
navig quick add deploy "run 'cd /var/www && git pull'"
navig quick add backup "db dump --output /tmp/backup.sql"
navig quick add status "dashboard --no-live"

# Run a quick action
navig quick run deploy
navig quick run backup --dry-run

# Remove a quick action
navig quick remove deploy
```

### 30.5 Quick Action Examples

```bash
# Common shortcuts
navig quick add ps "docker ps"
navig quick add df "run 'df -h'"
navig quick add mem "run 'free -h'"
navig quick add logs "docker logs -f app"

# Use them
navig quick run ps
navig q run df  # Short alias
```

### 30.6 Integration

Suggestions integrate with:
- **History System**: Learns from your command patterns
- **Context Management**: Uses current project context
- **Workflows**: Suggests workflow runs for complex tasks
- **Time Patterns**: Different suggestions morning vs evening

---

## 31. Event-Driven Automation (Triggers)

Triggers allow NAVIG to react automatically to system events, enabling powerful automation like auto-remediation, scheduled maintenance, and resource monitoring.

### 31.1 Overview

A trigger consists of:
- **Type**: What kind of event triggers it
- **Conditions**: Filters that must be met
- **Actions**: What to execute when fired
- **Settings**: Cooldown, rate limits

### 31.2 Trigger Types

| Type | Description | Example Use |
|------|-------------|-------------|
| `health` | Heartbeat detects issues | Auto-restart failed services |
| `schedule` | Time-based (cron) | Scheduled backups |
| `threshold` | Resource thresholds | Disk cleanup at 80% |
| `webhook` | Incoming HTTP | GitHub push -> deploy |
| `file` | File changes | Config reload |
| `command` | After commands | Post-deploy notification |
| `manual` | On-demand only | Testing |

### 31.3 Commands

```bash
# List all triggers
navig trigger
navig trigger list

# Show trigger details
navig trigger show disk-alert-abc123

# Create trigger (interactive)
navig trigger add

# Create trigger (quick mode)
navig trigger add "Disk Alert" \
  --action "notify:telegram" \
  --type threshold \
  --host prod \
  --condition "disk gte 80"

# Create scheduled trigger
navig trigger add "Daily Backup" \
  --action "workflow:backup" \
  --type schedule \
  --schedule "0 2 * * *"

# Enable/disable
navig trigger enable <id>
navig trigger disable <id>

# Test (dry run)
navig trigger test <id>

# Fire manually
navig trigger fire <id>

# View history
navig trigger history
navig trigger history <id>  # For specific trigger

# Statistics
navig trigger stats
```

### 31.4 Action Formats

```bash
# Run navig command
--action "host list"
--action "db dump --output /tmp/backup.sql"

# Run workflow
--action "workflow:deploy"
--action "workflow:backup"

# Send notification
--action "notify:telegram"
--action "notify:console"

# Call webhook
--action "webhook:https://example.com/hook"

# Run script
--action "script:/path/to/script.sh"
```

### 31.5 Conditions

Conditions use format: `target operator value`

**Operators:**
- `eq`, `ne` - equals, not equals
- `gt`, `lt` - greater/less than
- `gte`, `lte` - greater/less than or equal
- `contains`, `matches` - string/regex

**Examples:**
```bash
--condition "disk gte 80"    # Disk >= 80%
--condition "cpu gte 90"     # CPU >= 90%
--condition "status eq failed"
```

### 31.6 Example Workflows

**Auto-restart on failure:**
```bash
navig trigger add "Service Recovery" \
  --type health \
  --action "docker restart api" \
  --desc "Restart API when health check fails"
```

**Daily backup:**
```bash
navig trigger add "Nightly Backup" \
  --type schedule \
  --schedule "0 3 * * *" \
  --action "workflow:full-backup"
```

**Disk space alert:**
```bash
navig trigger add "Disk Alert" \
  --type threshold \
  --host production \
  --condition "disk gte 85" \
  --action "notify:telegram"
```

### 31.7 Settings

- **Cooldown**: Minimum 60s between fires (prevents flooding)
- **Rate Limit**: Max 10 fires/hour (prevents runaway)
- **Status**: enabled, disabled, firing, cooldown

### 31.8 Storage

- Triggers: `~/.navig/triggers/triggers.yaml`
- History: `~/.navig/triggers/history.jsonl`

---

<a id="32-operations-insights--analytics"></a>
## 32. ⭐ Operations Insights & Analytics

The insights system provides analytics and intelligence on your command patterns, helping you understand usage, detect anomalies, and optimize operations.

### 32.1 Quick Start

```bash
# View insights summary (default view)
navig insights

# Show host health scores
navig insights hosts

# See your most-used commands
navig insights commands

# Detect potential issues
navig insights anomalies

# Get personalized recommendations
navig insights recommend

# Generate full report
navig insights report
```

### 32.2 Insights Commands

| Command | Description |
|---------|-------------|
| `navig insights` | Quick summary with key metrics |
| `navig insights show` | Same as above with options |
| `navig insights hosts` | Host health scores (0-100) with trends |
| `navig insights commands` | Top commands with success rates |
| `navig insights time` | Hourly usage heatmap |
| `navig insights anomalies` | Unusual patterns and potential issues |
| `navig insights recommend` | Personalized optimization suggestions |
| `navig insights report` | Comprehensive analytics report |

### 32.3 Time Range Options

All commands support the `--range` option:

```bash
navig insights hosts --range today   # Last 24 hours
navig insights hosts --range week    # Last 7 days (default)
navig insights hosts --range month   # Last 30 days
navig insights hosts --range all     # All history
```

### 32.4 Output Formats

```bash
# Rich terminal output (default)
navig insights hosts

# Plain text (for scripting)
navig insights hosts --plain

# JSON output (for automation)
navig insights hosts --json
```

### 32.5 Host Health Scoring

Health scores are calculated on a 0-100 scale:

**Score Composition:**
- Success rate (60% weight)
- Latency score (40% weight)

**Latency Scoring:**
- < 1 second: 100 points
- 1-5 seconds: 70-100 points
- 5-30 seconds: 40-70 points
- > 30 seconds: 0-40 points

**Trend Indicators:**
- ↑ Improving (score increased vs. previous period)
- → Stable (within 5% of previous)
- ↓ Declining (score decreased vs. previous period)

### 32.6 Anomaly Detection

The system detects:

1. **Error Rate Spikes**
   - Compares current error rate to baseline
   - Triggers if current rate > baseline + 2σ

2. **Inactive Hosts**
   - Identifies hosts with no recent operations
   - Default threshold: 7 days

3. **Slow Commands**
   - Tracks command execution times
   - Alerts when latency increases significantly

4. **Unusual Activity**
   - Monitors for command count deviations
   - Detects off-hours activity

### 32.7 Recommendations Engine

The system generates personalized recommendations:

- **Quick Actions**: Suggests aliases for frequent commands
- **Health Checks**: Recommends heartbeat setup for active hosts
- **Automation**: Identifies opportunities for triggers/workflows
- **Best Practices**: General efficiency improvements

### 32.8 Example Workflows

**Daily Operations Check:**
```bash
navig insights              # Quick overview
navig insights anomalies    # Check for problems
navig insights recommend    # Review recommendations
```

**Weekly Review:**
```bash
navig insights report --range week --output weekly-report.json
navig insights hosts --range week
```

**Export for Dashboard:**
```bash
navig insights report --json > /var/reports/navig-metrics.json
```

### 32.9 Data Source

Insights derive from operations history:
- **Location**: `~/.navig/history/operations.jsonl`
- Populated by all NAVIG operations
- Use `navig history list` to view raw history data

---

## 33. � Packs System

> **Removed (2026-07).** The `pack` / `package` commands and the built-in pack
> system have been retired. Shareable, verifiable outcomes are now **Blocks** —
> `navig apply <id>` (see `docs/blocks-vs-workflows.md`); reusable capability bundles are
> **plugins** (`navig plugin …`). The examples in this section are historical. Migrate any
> on-disk packs under `~/.navig/packs` with `navig doctor migrate-packs`.

Packs are shareable operations bundles containing runbooks, checklists, workflows, and templates. Install community packs or create your own reusable operations.

### 33.1 What to use instead

```bash
# The outcomes packs used to carry are Blocks
navig block list
navig block show security-audit
navig apply security-audit
navig apply security-audit --dry-run

# The reusable capability bundles are plugins
navig plugin list
navig plugin show <name>

# On-disk packs under ~/.navig/packs migrate with
navig doctor migrate-packs
```

### 33.2 Pack Types

| Type | Description | Use Case |
|------|-------------|----------|
| `runbook` | Sequential steps with commands | Automated procedures |
| `checklist` | Interactive verification steps | Manual checklists |
| `workflow` | Multi-step automation | Complex automation |
| `template` | Configuration templates | Server/app setup |
| `quickactions` | Batch quick action imports | Shortcut bundles |
| `bundle` | Collection of multiple packs | Pack collections |

### 33.3 Command mapping

| Retired | Use instead |
|---------|-------------|
| `pack list` | `navig block list` (outcomes) · `navig plugin list` (bundles) |
| `pack show <name>` | `navig block show <name>` · `navig plugin show <name>` |
| `pack install <ref>` | `navig install add <ref>` (community assets) · `navig plugin add` |
| `pack uninstall <name>` | `navig install remove <name>` · `navig plugin remove` |
| `pack run <name>` | `navig apply <name>` |
| `pack run <name> --dry-run` | `navig apply <name> --dry-run` |
| `pack run <name> --var k=v` | `navig apply <name> --input k=v` |
| `pack create <name>` | `navig block new` (an outcome) · `navig plugin new` (a bundle) |
| `pack search <term>` | `navig install list` · `navig plugin marketplace` |

⚠ Variables are `--input k=v`, not `--var`, and a destructive step in a Block needs its own
`--approve <step>`.

### 33.4 Running a Block instead

```bash
# Preview without executing — resolves no secrets
navig apply db-snapshot --dry-run

# Execute with typed inputs
navig apply db-snapshot --input host=production

# Skip moderate confirmations (a destructive step still needs --approve)
navig apply db-snapshot --yes --approve backup
```

A Block's steps carry their own risk level, and `navig block show <id>` prints them before
you run anything. The guided-checklist packs became `kind: instruction` steps inside a
Block — `navig apply backup-essential` is one.

### 33.5 Creating a Block

```bash
navig block new
# Scaffolds a valid, runnable BLOCK.md you can edit
```

The retired pack YAML is kept below for reference only — it no longer loads.

**Pack YAML Format (historical):**
```yaml
name: "My Custom Pack"
description: "What this pack does"
author: "Your Name"
version: "1.0.0"
type: runbook  # runbook, checklist, quickactions, etc.

# Variables (override with --var key=value)
variables:
  host: production
  backup_path: /var/backups

# Steps to execute
steps:
  - description: "First step"
    command: "navig host test ${host}"

  - description: "Manual verification"
    notes: "Check this before continuing"

  - description: "Risky operation"
    command: "navig db backup"
    prompt: "Create backup?"  # Ask confirmation
    continue_on_error: true   # Don't stop on failure
```

### 33.6 Built-in Packs

NAVIG includes these starter packs:

| Pack | Type | Description |
|------|------|-------------|
| Database Backup Runbook | runbook | 6-step backup procedure |
| Docker Health Check | checklist | 7-step container verification |
| Security Audit | checklist | 9-step security checklist |
| Basic Deployment Checklist | checklist | Pre-deploy verification |
| Quick DevOps Actions | quickactions | 9 common shortcuts |
| Server Setup Template | template | Server configuration |

### 33.7 Pack Storage Locations

Packs are loaded from (in priority order):
1. **Installed**: `~/.navig/packs/installed/`
2. **Local**: `~/.navig/packs/local/`
3. **Built-in**: `<navig>/packs/`

---

## Help System

NAVIG includes a built-in help system accessible via `navig help`.

### Usage

```bash
navig help                    # List all help topics (organized by category)
navig help <topic>            # Show help for a specific topic
navig help <topic> --json     # Help output in JSON (for automation)
navig help <topic> --plain    # Plain text output (no rich formatting)
navig <command> --help        # Standard CLI help for any command
```

### Available Topics (44 total)

| Category | Topics |
|----------|--------|
| Infrastructure | `host`, `local`, `hosts`, `tunnel` |
| Services | `app`, `docker`, `web`, `gateway` |
| Data | `db`, `file`, `log`, `backup` |
| Automation | `flow`, `cron`, `trigger`, `task`, `scaffold`, `skills` |
| AI & Intelligence | `ai`, `ai-providers`, `memory`, `suggest`, `insights` |
| Tools | `config`, `context`, `history`, `mcp`, `wiki`, `pack` |
| Utilities | `search`, `fetch`, `docs`, `version`, `run`, `dashboard`, `quick` |
| Agent & Autonomous | `agent`, `heartbeat`, `approve`, `browser`, `ahk`, `calendar`, `email` |

### Adding Help Topics

Help topics are markdown files stored in `navig/help/`. Create `navig/help/<topic>.md` with:

```markdown
# topic-name

Brief description of the command group.

Common commands:
- `navig topic action` — description

Examples:
- `navig topic action --flag`
```

The help system also falls back to the `HELP_REGISTRY` in `navig/cli/__init__.py` if no markdown file exists.

---

## 34. Formation System (Agent Teams)

Formations are multi-agent team bundles that define specialized AI personas for different domains. Each formation contains agents with unique system prompts, roles, and council weights for collaborative decision-making.

### Key Concepts

| Concept | Description |
|---------|-------------|
| **Formation** | A team manifest (`formation.json`) defining agents, roles, and API connectors |
| **Agent** | An AI persona with system prompt, traits, personality, and council weight |
| **Profile** | A `.navig/profile.json` file that binds a project to a formation |
| **Council** | Multi-round deliberation where agents discuss a question collaboratively |

### Formation Commands

**CLI Commands:**
```bash
# List all available formations
navig formation list
navig formation list --json

# Show formation details and agents
navig formation show navig_app
navig formation show creative_studio --json

# Initialize project with a formation
navig formation init navig_app

# List agents in active formation
navig formation agents
navig formation agents --plain
```

**VS Code Extension Commands:**
> Enable in settings: `navig-copilot.formations.enabled` (default: `false`)

When enabled:
- **Auto-Detection**: On first activation in workspace without `.navig/profile.json`, NAVIG scans project files (package.json, pyproject.toml, Cargo.toml, etc.) and auto-selects the best-fit formation. The result is persisted so it only happens once per project.
- **Switch Formation**: `Ctrl+Shift+P` → "Switch Formation" — Available via Command Palette (not in sidebar). Use when you want to override the auto-detected formation.
- **List Agents**: `Ctrl+Shift+P` → "Formation: List Agents" — Shows agents from active formation, offers to run selected agent
- **Show Details**: `Ctrl+Shift+P` → "Formation: Show Details" — Full formation manifest displayed as markdown
- **Run Council**: `Ctrl+Shift+P` → "Formation: Run Council" — Enter a question, all agents deliberate, results opened as document
- **Run Agent**: `Ctrl+Shift+P` → "Formation: Run Agent" — Select agent, enter task, response displayed as markdown
- **Refresh**: `Ctrl+Shift+P` → "Formation: Refresh" — Reload formation details from CLI

**Sidebar Integration:**
When enabled, the sidebar "Command Deck" shows a **🎯 Formation** section with:
- Active formation name (auto-detected or from profile.json)
- Expandable agent list with names, roles, and council weights
- Default agent marked with ⭐
- Council, details, and refresh actions
- Auto-updates when `.navig/profile.json` changes (file watcher)

**Auto-Detection Signals** (checked in order):
| Files | Formation |
|-------|-----------|
| `.figma`, `figma.config.json`, Tailwind configs | `creative_studio` |
| `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `Gemfile`, etc. | `navig_app` |
| `tsconfig.json`, `.eslintrc.json`, Vite/Next configs | `navig_app` |
| `requirements.txt`, `setup.py`, `Pipfile`, `poetry.lock` | `navig_app` |
| `.git` directory | `navig_app` (medium confidence) |
| No signals detected | `app_project` (fallback) |

**Activation Log**: Extension logs active formation on startup: `[FORMATION] Active formation: <id> (<source>)` where source is `file`, `auto`, or `default`.

### Council Deliberation

```bash
# Run multi-agent discussion
navig council run "Should we migrate to microservices?"

# Control rounds and timeout
navig council run "Budget allocation" --rounds 2 --timeout 60

# JSON output for scripting
navig council run "Tech strategy" --json
```

### Agent Execution

```bash
# Run a single agent from active formation with a task
navig agent run <agent_id> --task "Analyze security risks"

# Control timeout and output format
navig agent run architect --task "Design microservices" --timeout 60 --json
navig agent run security --task "Review code" --plain
```

The agent's system prompt is loaded from the active formation and sent to the AI provider with the specified task.

### Built-in Formations

| Formation | Agents | Domain | Aliases |
|-----------|--------|--------|---------|
| `navig_app` | 5 | Software development | app_project, dev_team, software |
| `creative_studio` | 6 | Creative agency | creative, agency, studio |
| `football_club` | 6 | Sports management | football, soccer, club |
| `government` | 5 | Public sector | gov, public_sector, administration |

### Creating Custom Formations

Community formations are discovered dynamically from:
- **Project-level**: `formations/` directory in project root
- **Global**: `~/.navig/formations/` for user-wide formations

Directory structure:
```
formations/my_team/
  formation.json          # Team manifest (id, name, agents list, aliases)
  agents/
    leader.agent.json     # Agent persona (system_prompt, traits, weight)
    analyst.agent.json
```

Each agent requires a `system_prompt` of at least 100 characters. Agents declare `council_weight` (0.0-1.0) to influence deliberation outcomes.

See `formations/README.md` for the complete JSON schema reference.

---

## 34. ⭐ Platform & OS Integration (NEW)

### `navig paths` — Resolved Directory Layout

Shows all NAVIG directories on the current machine with live ✅/❌ status.

```bash
navig paths            # rich table output
navig paths --json     # machine-readable JSON
```

Output columns: Name · Path · Exists · Writable · Daemon WS

---

### `navig system` — OS Integration Modes

Integrates NAVIG into the OS at three depth levels:

```bash
navig system init                  # first-run: detect best mode and apply
navig system init --mode portable  # no OS writes (USB/external drive)
navig system init --mode standard  # PATH + shell completion (default)
navig system init --mode deep      # wallpaper + icons + theme + sounds + fonts

navig system wallpaper <path>      # set desktop wallpaper (Win32 / gsettings)
navig system icons <theme>         # change icon theme (Win shell / GTK)
navig system theme <name>          # change window theme (.msstyles / GTK)
navig system sounds <event> <wav>  # assign sound event (Win / aplay)
```

**Deep mode platform support:**

| Feature | Windows | Linux |
|---------|---------|-------|
| Wallpaper | `SystemParametersInfoW` | `gsettings org.gnome.desktop.background` |
| Icons | Registry/shell replacement | GTK icon theme via `gsettings` |
| Theme | `.msstyles` in `system/themes/` | `gsettings org.gnome.desktop.interface` |
| Sounds | `winsound.PlaySound` | `aplay` |
| Fonts | Copy to `C:\Windows\Fonts\` | Copy to `~/.fonts/`, `fc-cache -fv` |

---

### `navig migrate` — Path Migration

Migrates legacy `~/.navig` to the platform-native roaming dir and creates a backwards-compatible junction.

```bash
navig migrate status          # show current state: migrated / legacy / mixed
navig migrate run             # migrate ~/.navig → AppData\Roaming\NAVIG (Windows)
                              #                  → ~/.config/NAVIG (Linux/macOS)
                              # creates compat symjunction so old tools keep working
navig migrate rollback        # restore ~/.navig as primary
```

---

### `navig mcp install` — VS Code MCP Wiring

Registers the NAVIG daemon as an MCP server in VS Code:

```bash
navig mcp install             # write .vscode/mcp.json in current workspace
navig mcp install --global    # write to VS Code user settings
navig mcp install --url ws://127.0.0.1:7001/ws   # override WS URL
navig mcp uninstall           # remove navig key from mcp.json
navig mcp status              # show socket reachability + config state
navig mcp serve               # start WS server in foreground (for dev)
```

---

### `navig install` (v2) — Package Installer

```bash
navig install <package>          # install with SHA-256 cache + signature verify
navig install <package> --update # force re-download even if cached
navig install list               # list installed packages
navig install freeze             # pin all package versions to lock file
```

---

### Platform-Aware Config Paths

```python
from navig.config import ConfigManager
cfg = ConfigManager()
cfg.roaming_root   # AppData\Roaming\NAVIG (Win) | ~/.config/NAVIG (Linux/macOS)
cfg.identity_dir   # .../NAVIG/identity
cfg.store_dir      # .../NAVIG/store
cfg.system_dir     # .../NAVIG/system
cfg.logs_dir       # .../NAVIG/logs
cfg.cache_dir      # platform cache dir (AppData\Local\NAVIG\Cache on Win)
```

---

<a id="33-additional-documentation"></a>
## 35. Additional Documentation

For specialized topics, see these detailed guides:

| Document | Description |
|----------|-------------|
| [AUTONOMOUS_DEPLOYMENT.md](AUTONOMOUS_DEPLOYMENT.md) | **Complete guide for 24/7 Telegram bot deployment** - VPS, Docker, Windows service |
| [ARCHITECTURE_GAP_ANALYSIS.md](ARCHITECTURE_GAP_ANALYSIS.md) | NAVIG vs Reference Agent feature comparison & roadmap |
| [AGENT_MODE.md](AGENT_MODE.md) | Full agent architecture and component details |
| [AGENT_SERVICE.md](AGENT_SERVICE.md) | Service installation (systemd, launchd, Windows) |
| [AGENT_SELF_HEALING.md](AGENT_SELF_HEALING.md) | Auto-remediation and recovery system |
| [AGENT_LEARNING.md](AGENT_LEARNING.md) | Pattern detection and learning system |
| [AGENT_GOALS.md](AGENT_GOALS.md) | Goal planning and task decomposition |
| [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) | Production deployment checklist |

## 36. Finance (`navig finance`)

Personal and operational finance tracking using double-entry accounting via **beancount** (MIT).

### Install

```bash
pip install "navig[finance]"
```

### Quick start

```bash
# Create a new journal (default: ~/finance/main.beancount)
navig finance init

# Or specify a custom path
navig finance init --path ~/documents/money.beancount

# Set a permanent custom path in NAVIG config
navig config set finance.journal_path ~/documents/money.beancount
```

### Commands

| Command | Description |
|---------|-------------|
| `navig finance init` | Scaffold a new beancount journal with default accounts |
| `navig finance add "<entry>"` | Append a raw beancount transaction |
| `navig finance balance [account]` | Show current account balances |
| `navig finance income [--year N]` | Income vs expenses summary for a year |
| `navig finance check` | Validate the journal for errors |
| `navig finance accounts [filter]` | List all declared accounts |
| `navig finance import <file.csv>` | Import transactions from a CSV file |
| `navig finance hledger -- <args>` | Pass-through to hledger (if installed separately) |

### Adding a transaction

```bash
navig finance add '2024-03-01 * "Coffee" Expenses:Food  4.50 USD ; Assets:Checking'
```

Or edit the `.beancount` file directly — it is plain text.

### Importing from a CSV bank export

The CSV must have `Date`, `Description`, and `Amount` columns (any case):

```bash
navig finance import ~/downloads/statement-march.csv
navig finance import ~/downloads/statement-march.csv --dry-run   # preview only
```

### Checking for errors

```bash
navig finance check           # 0 exit code = clean journal
navig finance check --strict  # also exits 1 on warnings
```

### hledger pass-through

hledger (GPL-3.0) is **not bundled**. If you install it separately, NAVIG will
find it on PATH and forward commands:

```bash
navig finance hledger -- bal --cost
navig finance hledger -- reg Expenses
```

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `finance.journal_path` | `~/finance/main.beancount` | Path to the primary journal |

---

## 37. Work (`navig work`)

Work is NAVIG's lifecycle and stage tracker.  Use it to follow anything through
stages: client leads, active projects, one-off tasks, proposals, initiatives,
and more.

> **Mnemonic**: wiki = *what* (knowledge), finance = *money*, work = *state*

### Concepts

| Term | Meaning |
|------|---------|
| **item** | A trackable thing with a title, kind, and current stage |
| **kind** | What it is: `lead`, `client`, `project`, `task`, `proposal`, `initiative`, `other` |
| **stage** | Where it is: `inbox → planned → active → blocked → review → done → archived` |
| **slug** | Auto-generated URL-safe identifier derived from the title |
| **wiki note** | An optional linked Markdown file created automatically in `~/.navig/wiki/hub/` |

### Quick start

```bash
# Add items
navig work add "Acme Corp intro call"  --kind lead
navig work add "Redesign homepage"     --kind project --stage planned

# See what's active
navig work list --stage active

# Move something forward
navig work move redesign-homepage --to active

# Inspect one item
navig work show acme-corp-intro-call

# Update fields
navig work update acme-corp-intro-call --owner alice --tag q2 --tag priority

# Archive when done
navig work archive acme-corp-intro-call
```

### Commands

| Command | Description |
|---------|-------------|
| `navig work add <title>` | Create a new work item (wiki note created by default) |
| `navig work list` | List items (excludes archived unless `--stage archived`) |
| `navig work show <slug\|id>` | Full detail view including event history |
| `navig work move <slug\|id> --to <stage>` | Move item to a new stage |
| `navig work update <slug\|id>` | Update title, owner, tags, or external ref |
| `navig work archive <slug\|id>` | Archive item (shortcut for `move --to archived`) |
| `navig work stages` | Print valid stage names |
| `navig work kinds` | Print valid kind names |

### Options for `add`

| Option | Default | Description |
|--------|---------|-------------|
| `--kind` / `-k` | `task` | Item kind |
| `--stage` / `-s` | `inbox` | Initial stage |
| `--owner` / `-o` | — | Owner name |
| `--tag` / `-t` | — | Tag (repeatable) |
| `--no-wiki` | false | Skip creating the wiki note |
| `--json` | false | JSON output |

### Wiki integration

Every `navig work add` call creates a Markdown note in `~/.navig/wiki/hub/<slug>.md`
with YAML frontmatter (`title`, `kind`, `stage`, `work_slug`, `created`, `tags`).
`navig work move` and `navig work archive` update the `stage:` field in that note
automatically.

Run `navig wiki init` first to enable the wiki directory tree.

### Storage

Work items are stored in `~/.navig/store/work.db` (SQLite, two tables):

- **`work_items`** — one row per item
- **`work_events`** — append-only audit log of every state change

---

## 38. Browser Control (`navig cdp`)

Drive any **Chromium-family** surface — Chrome / Edge / Brave **and** Electron apps
(Discord, Notion, Slack, VS Code) — over the Chrome DevTools Protocol. Screenshot,
click, type, scroll, move the mouse, run JS, navigate, and read every open tab, from
the CLI or as agent-callable `cdp_*` MCP tools. Full guide:
[`docs/automation/cdp-connector.md`](../../../docs/automation/cdp-connector.md).

### Enable, use, disable

```bash
navig cdp launch chrome          # enable: a debuggable Chrome (dedicated profile)
navig cdp tabs                   # every open page
navig cdp snapshot --url github  # read a tab's structure (numeric refs)
navig cdp click --ref 8 --url github
navig cdp eval "document.title"  # run JS (confirm-gated)
navig cdp stop --all             # disable: close every debug browser NAVIG started
```

### Fresh, isolated browser

```bash
navig cdp new                    # throwaway profile, auto-allocated port
navig cdp new --profile research # reusable named profile (logins persist)
navig cdp new --headless --window-size 1440x900  # windowless + fixed viewport (CI / deterministic shots)
```

### Commands

| Command | Description |
|---------|-------------|
| `navig cdp status` / `targets` | Show launchable apps + live CDP targets (`kind: browser/node`) |
| `navig cdp launch <app>` | Start an app with a debug port (`--force-restart`, `--user-data-dir`) |
| `navig cdp new` | Fresh isolated browser — own profile + auto port (`--app`, `--profile`, `--headless`, `--window-size WxH`) |
| `navig cdp launched` | List browsers NAVIG started and whether each is live |
| `navig cdp stop` / `detach` | Close (`--port`/`--all`) or just disconnect a debug browser |
| `navig cdp tabs` / `switch` | List every open page / make one the active target |
| `navig cdp snapshot` / `screenshot` | a11y tree with refs / capture the page |
| `navig cdp click` / `type` / `key` / `scroll` / `move` | Interact (by `--ref`, `--xy`, or focused element) |
| `navig cdp eval` / `nav` | Run JavaScript (gated) / navigate a page |
| `navig cdp login <domain>` | Auto-login from a vaulted website credential (gated) |
| `navig cdp inject <script\|@file>` | Register a persistent userscript (runs at document-start) |

Every action targets a tab with `--tab <index>` or `--url <substring>`; all support
`--port` (default 9222) and `--json`.

### Website auto-login (`navig cdp login`)

Store a website login once, then sign in on any attached browser — session-first, so
after the first login NAVIG restores the saved session instead of retyping.

```bash
navig vault login add github.com -u you@example.com          # prompts for the password
navig vault login add github.com -u you@example.com --totp JBSWY3DPEHPK3PXP  # + 2FA
navig vault login list                                        # no passwords shown
navig cdp new                                                 # a visible browser
navig cdp login github.com --open https://github.com/login   # navigate + sign in
```

- **Session-first**: on success the authenticated session (cookies + localStorage) is
  captured to the vault; the next `login` **restores the session** — no password typed —
  and only re-fills the password when the session is stale.
- **Origin-bound + https-only**: a credential fills **only** on its registrable domain
  (eTLD+1, public-suffix aware) after IDN/punycode normalisation — never on a look-alike
  (`g00gle.com`), a suffix trick (`github.com.evil.com`), or a cross-origin iframe. The
  password is injected server-side; it never enters an AI's context or a log.
- **2FA**: if the login stored a `--totp` secret, the engine fills the one-time-code step
  itself (RFC 6238). SMS / app-approval 2FA (no stored secret) still needs you.
- Status values: `session_restored`, `logged_in`, `filled` (2FA/captcha pending),
  `no_credential`, `needs_disambiguation` (pass `--username`), `wrong_origin`.

### Browser profiles (different identities for different projects/cases)

A **profile** is a persistent, isolated, logged-in Chrome identity on a **stable port** — one per
project, client, or account. Sign in once; it stays logged in (no close/reopen).

```bash
navig cdp profile new cybesis --note "Cybesis work"   # create (fresh automation profile)
navig cdp profile list                                 # name · port · running? · active
navig cdp open cybesis                                 # open (or REUSE if already running)
navig cdp profile use cybesis                          # make it the active profile
navig cdp profile new client-acme --note "Acme"        # a second, isolated identity
navig cdp profile close cybesis  ·  navig cdp profile remove cybesis
```

- **Reuse, no reopen**: `open` attaches to the already-running profile on its stable port.
- **Active profile**: after `profile use`, `navig do` and `navig gmail` default to it (no `--port`).
- **Your real Chrome** (advanced): `navig cdp profile list --real` shows your actual Chrome
  profiles; `navig cdp profile new mine --real "Profile 3"` registers one. Opening it relaunches
  the real browser (quit your everyday Chrome first; newest Chrome may block the *default* profile)
  — the automation profile above is the smooth, recommended path.

### AI browser workflows (`navig do`)

Ask the AI to do a task in your logged-in browser, from the command line:

```bash
navig do "open gmail and write an email to bob@x.com about Friday's meeting"
navig do --profile cybesis "summarise my latest 3 GitHub notifications"
navig do --dry-run "post an update to the team channel"   # plan only, sends nothing
navig do --yes "reply 'on my way' to the last message"     # allow send/publish
```

The AI drives the **active profile's visible browser** (you watch it). **Safe by default**: it
prepares/composes but won't finally send, publish, pay, or delete without `--yes`; `--dry-run`
plans only. Every run is audited to `~/.navig/history/do.jsonl`.

### Compose Gmail reliably (`navig gmail`)

Deterministic compose+send (no LLM, no fragile clicking) via Gmail's compose deep-link — the
profile must be signed into Gmail (do that once with `navig cdp open <profile>`):

```bash
navig gmail compose --to bob@x.com --subject "Hi" --body "Let's meet Friday."   # opens compose
navig gmail compose --to bob@x.com -s "Hi" -b "…" --send                        # composes + sends
```

**Several Gmail accounts in one profile** — pick the account by **email** (order-independent) or index:
```bash
navig gmail compose --account work@company.com --to bob@x.com -s "Hi" -b "…"     # by email
navig gmail compose --account me@gmail.com --to bob@x.com -s "Hi" -b "…" --remember  # + save as default
navig gmail compose --to bob@x.com -s "Hi" -b "…"                                # uses the profile's saved default
```

**Or one profile per Gmail** (cleaner — a session expiry on one won't cascade to the others):
```bash
navig cdp profile new work     --gmail work@company.com --note "Work"
navig cdp profile new personal --gmail me@gmail.com     --note "Personal"
navig cdp profile use work                              # switch identity
navig gmail compose --to bob@x.com -s "Hi" -b "…"       # uses work's Gmail
```

### Add a Google account (the right way)

Google actively blocks automated password logins (bot checks, device verification, 2FA), so
**don't rely on filling your Google password**. Two robust paths instead:

1. **Browser tasks (Gmail UI, `navig do`, `navig gmail`)** → *session-first*: `navig cdp open
   cybesis`, **sign into Google once** in that window. The profile stays logged in; NAVIG reuses the
   session forever.
2. **Sending email headlessly** → *app-password + SMTP* (no browser): create a Google **app-password**
   (https://myaccount.google.com/apppasswords), then `navig email setup gmail` (or
   `export EMAIL_PASSWORD=<app-password>`) and `navig email send --to … --subject … --body …`.

You *can* still store a web login (`navig vault login add google.com -u you@gmail.com`) for
`navig cdp login`, but expect Google's bot-wall — prefer option 1 for the browser.

### Security

A debug port means **anything on your machine can drive that browser**. NAVIG binds
it to loopback only, launches browsers in a **dedicated debug profile** (not your
main one), tracks what it started (by PID), and routes the powerful verbs
(`eval`, `launch --force-restart`, `stop`, `login`, `inject`) through the approval
gate — over the CLI they confirm, over MCP they are classified, audited, and
deny-able by policy. **Close debug browsers with `navig cdp stop --all` when done**,
and use `navig cdp new` (throwaway) for untrusted work. Full threat model in the
guide above.

---

## 39. NAVIG Ecosystem Products

### Landing Page (`packages/landing`)
- **Stack**: Next.js 16, React 19, Tailwind v4, static export
- **Port**: 7003 (dev server)
- **Routes**: `/` (marketing site), `/deck` (NAVIG Deck demo), `/os` (NAVIG OS demo)
- **Components**: `components/marketing/` (12 section components), `components/shared-deck/` (unified Deck UI)
- **Content**: Externalized in `content/copy.ts` — all marketing copy, pricing tiers, FAQ
- **Fonts**: Geist + Geist Mono via `next/font/google` with CSS variable approach (`--font-geist-sans`, `--font-geist-mono`)
- **Animations**: CSS keyframe animations (`fade-in-up`, `fade-in`, `slide-in-left`, `pulse-glow`) with stagger delays. Respects `prefers-reduced-motion`.
- **SEO**: `robots.txt`, `sitemap.xml`, per-route metadata with title template (`%s | NAVIG`), OpenGraph + Twitter cards
- **Accessibility**: Skip-to-content link, `aria-label` on navigation, semantic HTML
- **Error Handling**: `not-found.tsx` (branded 404), `error.tsx` (global error boundary with retry)
- **Build**: `pnpm build` produces static HTML in `out/`
- **Dependencies**: 9 production deps (Next.js, React, clsx, tailwind-merge, lucide-react, class-variance-authority, @vercel/analytics, next-themes). All pinned, no `"latest"`.

### Shared Deck (`components/shared-deck/`)
Unified component library used by both NAVIG Deck (browser extension) and NAVIG OS (desktop overlay):
- **Context**: `NavigDeckProvider` + `useNavigDeck` hook — themes, workspaces, widgets, notes, settings
- **Bar**: Top bar with command input, workspace switcher, quick jump, pomodoro, clock, docked widgets
- **Panels**: CommandPalette, SettingsPanel, OptionsPanel (8-section settings), NotesPopup
- **Extras**: Marketplace (themes/widgets/apps), DesktopWidgets (draggable, freezable, dockable)
- **Widgets**: Weather, Calendar, QuickLinks, Stock, Clock, Pomodoro

### NAVIG Cloud (`packages/navig-cloud`)
- **Stack**: Laravel 12, Sanctum auth, Stripe Cashier, Filament 3 admin, Scramble API docs
- **API v1**: Auth, devices, workspaces, plugins marketplace, AI proxy (metered), sync, billing
- **Models**: User, Device, License, Plan, Plugin, PluginInstall, PluginVersion, Workspace

### NAVIG Ask (`packages/navig-ask`)
- **Type**: VS Code extension (v3.5.0)
- **AI Model**: GPT-5.2 Thinking (default), fallback chain to GPT-4, Claude, Copilot
- **Pipeline**: ChatMonitor -> PatternMatcher -> SessionManager -> Responder (auto-continue)
- **Sidebar Sections**: Infrastructure (Hosts, Apps, Tunnels, Files) | Docker | Database | Services (Agent, Telegram, Web, MCP) | Security | Monitoring | Backup | Automation (Workflows, Cron, Triggers, AHK) | Evolution (Evolve, Packs, Skills) | DevOps Lifecycle | Life Ops | Autonomous System | System Operations
- **Settings Categories**: General, Smart AI, Quick Continue, Detection & Timing, Notifications, OCR, Session & Limits, Planner, NAVIG Integration, DevOps & System Config, Avatar
- **Dashboard**: Blue-tinted glassmorphism UI with grid background, real-time metrics, connection status
- **Avatar Companion**: Tamagotchi-style animated sidebar avatar that reacts to extension state (idle, thinking, speaking, working, success, error). Uses 24 Chappy firmware sprite frames with CSS transition animations. See [AVATAR_INTEGRATION.md](../packages/navig-copilot/docs/AVATAR_INTEGRATION.md).
- **Settings**: `navig-copilot.avatar.enabled`, `navig-copilot.avatar.animationSpeed`, `navig-copilot.avatar.idleTimeout`

### NAVIG Voice (`navig/voice/`)
- **TTS Providers**: Edge TTS (free default), OpenAI, ElevenLabs, Google Cloud TTS, Deepgram Aura
- **STT Providers**: OpenAI Whisper API (default), Deepgram Nova-2, local Whisper (offline)
- **Wake Word**: `WakeWordDetector` in `navig/voice/wake_word.py` — openwakeword-based, lazy-loaded
  - Built-in keyword: `"echo"` (ships in `navig/voice/assets/`)
  - `WakeWordDetector(keyword="echo", on_wake=callback, threshold=0.5)`
  - `detector.start_background()` → daemon thread; `await detector.start()` → async
  - Tauri commands (navig-echo): `start_wake_word` / `stop_wake_word` → emits `"wake-word-detected"` event
  - Install extras: `pip install openwakeword pyaudio`
- **Audio Playback**: Cross-platform (Windows/macOS/Linux) with 14 built-in notification sounds from Chappy firmware
- **Usage**: `from navig.voice import speak, transcribe, play_notification`
- **See**: [voice-services.md](voice-services.md)

### NAVIG Matrix Bridges (`navig bridge matrix`)

| Command | Description |
|---------|-------------|
| `navig bridge matrix setup [name]` | Interactive wizard — configure + deploy one or all bridges |
| `navig bridge matrix status` | Show running state of all bridges |
| `navig bridge matrix deploy <name>` | Deploy bridge container to remote host |
| `navig bridge matrix register <name>` | Register appservice with Conduit homeserver |
| `navig bridge matrix login <name>` | Initiate login (QR / cookie / token depending on bridge) |
| `navig bridge matrix bench <name>` | Latency benchmark + 256 MB / 50% CPU hard-limit check |
| `navig bridge matrix vault-set <name>` | Store bridge credentials in encrypted vault |
| `navig bridge matrix generate-config <name>` | Render config.yaml from vault-injected template |

**14 GA bridges**: whatsapp · discord · telegram · messenger · instagram · linkedin · twitter · sms · email · nextcloud · line · wechat · tox · xmpp
**Resource limits**: 256 MB RAM / 50% CPU per bridge container (enforced in docker-compose + bench command)
**See**: [`docs/MATRIX_BRIDGE_SETUP.md`](MATRIX_BRIDGE_SETUP.md)

---

### NAVIG Task Completion — ATLE Primitive (`navig task complete`)

> **ATLE** = Automated Task Lifecycle Event. Agents call this at the end of every
> non-trivial work session to record what shipped, close the loop in the plan
> docs, and fire the Inbox Router event so navig-bridge can react.

#### CLI

```bash
navig task complete <task-title> <task-slug> <summary> <phase-name> [--dry-run] [--now-date YYYY-MM-DD]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `task-title` | ✅ | Human-readable title (wrap in quotes if it contains spaces) |
| `task-slug` | ✅ | `kebab-case` identifier — used as filename |
| `summary` | ✅ | One-sentence description of what was done |
| `phase-name` | ✅ | Active phase name (must match a `## …` heading in `CURRENT_PHASE.md`) |
| `--dry-run` | | Validate all inputs + files, print what would happen, exit 0 |
| `--now-date` | | Override timestamp (ISO date, default = today) |

**Example:**
```bash
navig task complete "Fix daemon health" fix-daemon-health "Resolved /health 500 via middleware fix" "Phase 4"
```

#### What it does (11 steps)

1. Resolves project root by walking up from `cwd` looking for `.navig/`
2. Validates all required arguments are non-empty
3. Checks for duplicate slug in `.navig/plans/CURRENT_PHASE.md` (idempotency)
4. Acquires a `.navig/.complete-task.lock` directory lock (atomic, POSIX + NTFS)
5. Reads `.navig/plans/CURRENT_PHASE.md` and locates `## <phaseName>` heading
6. Appends `- [x] YYYY-MM-DD — <taskTitle>: <summary>` under the phase heading (atomic write)
7. Appends the same line to `.navig/plans/DEV_PLAN.md` under `## Completed Tasks`
8. Creates `.navig/plans/completed/<today>__<slug>.md` (standup-style artifact) — atomic write
9. Archives a copy to `.navig/plans/archive/<today>__<slug>.md`
10. Releases lock
11. Emits exit 0; prints `ATLE:COMPLETE` to stdout

#### Outputs

| File | Purpose |
|------|---------|
| `.navig/plans/CURRENT_PHASE.md` | Phase checklist updated |
| `.navig/plans/DEV_PLAN.md` | `## Completed Tasks` section updated |
| `.navig/plans/completed/<date>__<slug>.md` | Standup artifact (live copy) |
| `.navig/plans/archive/<date>__<slug>.md` | Permanent archive copy |

#### Safety guarantees

- All writes are **atomic** (temp file + rename — never partial writes)
- **Idempotent** — duplicate slug is silently skipped on rerun
- **Lock-protected** — concurrent agent calls serialize safely
- `--dry-run` runs all validation but **writes nothing**
- Lock is always released via `trap`/`finally`, even on crash

#### Cross-platform scripts

The CLI delegates to shell scripts in `.navig/scripts/`:

| Platform | Script |
|----------|--------|
| macOS / Linux | `.navig/scripts/complete-task.sh` (bash, `set -euo pipefail`) |
| Windows | `.navig/scripts/complete-task.ps1` (PowerShell 5.1+) |

See `.navig/scripts/README.md` for direct invocation docs.

#### VS Code / navig-bridge integration

The Inbox Router in navig-bridge handles the `task.completed` event:

```typescript
// Programmatic dispatch
await vscode.commands.executeCommand(
    'navig-bridge.inboxRouterDispatchEvent',
    'task.completed',
    { taskTitle, taskSlug, summary, phaseName, cwd, source: 'bridge' },
);

// Slash command in NAVIG chat
/complete-task "My Task" my-task "Summary text" "Phase 4"
```

The handler resolves the correct script, spawns it, streams output to the
`NAVIG Inbox Router` output channel, and shows a VS Code notification on
completion or failure.

### NAVIG Plans (`navig plans`)

Space-aware planning commands for `.navig/plans`.

| Command | Description |
|---------|-------------|
| `navig plans status` | Show resolved spaces progress (project/global) |
| `navig plans add "Goal" [--space <name>]` | Add a goal entry to `.navig/plans/DEV_PLAN.md` |
| `navig plans run "Goal"` | Deprecated alias for `plans add` |
| `navig plans sync [--dry-run] [--space <name>]` | Process `.navig/plans/inbox/` through inbox routing |
| `navig plans update [file]` | Recompute `completion_pct` and `last_updated` frontmatter |
| `navig plans next [--space <name>]` | Show the next highest-impact actionable task from spaces |

**Examples:**
```bash
navig plans add "Ship onboarding wizard" --space finance
navig plans sync --dry-run --space finance
navig plans update CURRENT_PHASE.md
navig plans next --space health
```

### Telegram Continuation Controls

For premium low-friction autonomous chat flow, Telegram now supports:

- `/continue [profile] [space]` — enable autonomous continuation; profiles: `conservative` (20s/2 turns), `balanced` (10s/3), `aggressive` (5s/5)
- Profile also controls busy suppression windows used after classifier wait/blocked signals:
  - `conservative`: wait 45s, blocked 120s
  - `balanced`: wait 30s, blocked 90s
  - `aggressive`: wait 15s, blocked 60s
- Profile controls decision sensitivity too:
  - `conservative` = strict (only explicit continue prompts)
  - `balanced` = standard
  - `aggressive` = eager (allows softer continue prompts like “Proceed with next step?”)
- `/pause` — pause continuation while keeping auto mode active
- `/skip` — skip the next continuation trigger only
- `/auto_status` — includes continuation policy, classifier state, and suppression metadata (`busy_until`, `last_skip`)
- Detection is classifier-assisted: continuation triggers on high-confidence continue intent and avoids auto-trigger on choice/wait/blocked phrasing.

### Telegram Space Control (Low-Friction)

You can switch planning space directly from Telegram and get immediate direction:

- `/spaces` — list available spaces and the currently active one
- `/space <name>` — switch active space and print top 3 next actions

Operational spaces are first-class and supported out of the box:

- `devops`
- `sysops`

If a selected space has no docs yet, NAVIG bootstraps baseline files automatically:

- `VISION.md`
- `ROADMAP.md`
- `CURRENT_PHASE.md`

### Telegram Guided Intake

Run `/intake [space]` to start a short guided planning interview (4 questions).

The intake writes structured updates into the target space:

- `VISION.md` (goal, constraints, assumption to challenge)
- `ROADMAP.md` (short-horizon target)
- `CURRENT_PHASE.md` (actionable checklist)

Cancel anytime with `/intake cancel` (or `/intake stop`).

Natural-language routing is also supported for common intents:

- "please work for 1 day and make me a money plan" → starts intake in `finance`
- "tell me how to improve my health currently" → starts intake in `health`

Safety confirmation is enabled for NL routing:

- NAVIG sends: "Detected `<intent>` for `<space>`. Auto-starting in 3s."
- Reply `cancel` to stop, or `yes` to run immediately.
- One-tap buttons are also shown: `✅ Yes now` and `🛑 Cancel`.

You can still use explicit commands at any time for deterministic control.

### CLI Continuation Controls

CLI parity is available via `navig agent continuation`:

- `navig agent continuation continue --profile <conservative|balanced|aggressive> [--space <name>]`
- `navig agent continuation start --profile <conservative|balanced|aggressive> [--space <name>]` (friendlier alias)
- `navig agent continuation pause`
- `navig agent continuation skip`
- `navig agent continuation status`

`continue` and `status` print effective policy telemetry, including cooldown/turn limits, suppression windows, and decision sensitivity.

Top-level alias is also available:

- `navig continuation start|continue|pause|skip|status`

### Space Kickoff (Simplified UX)

Use one command to begin work in a space with immediate direction:

- `navig start <space>` — switches to the space and prints top 3 next actions.
- `navig space switch <space>` now also prints top 3 next actions automatically.

### Telegram Provider Picker UX

Provider/model assignment in Telegram is optimized for readability and low friction:

- `/providers` now shows cleaner bridge status wording (bridge is optional and not tied to VS Code only).
- Current routing is visible at a glance: `Small`, `Big`, and `Code` model selections.
- Hybrid routing is explicitly explained in UI: each tier (`Small` / `Big` / `Code`) can point to a different provider:model slot.
- Hybrid assignment controls are enabled when `routing.enabled: true` is set in config and NAVIG is restarted.
- Model assignment is tier-first: pick `⚡ Small`, `🧠 Big`, or `💻 Code`, then choose from a readable model list.
- Model lists are shown in a single page (no pagination) to reduce button churn and callback friction.
- Activating a provider now applies curated defaults for `Small` / `Big` / `Code` and persists them to global config.
- Unconfigured cloud providers show provider label + key action (`🔑`) so key setup is one tap from the same row.

### Messaging Provider Validation

- Startup now validates `NAVIG_MESSAGING_PROVIDER` / `messaging.provider` strictly.
- Unsupported provider names fail fast with an actionable error and a list of supported values.

### No-LLM Fallback Behavior

- If no provider/model is configured, chat no longer hard-fails with provider config errors.
- NAVIG now falls back to deterministic no-AI response behavior automatically where conversational chat paths support it.
- Existing explicit no-AI behavior (`No AI` / raw mode) is unchanged.

Kickoff actions are synthesized from:

- `<space>/CURRENT_PHASE.md`
- `.navig/plans/DEV_PLAN.md`
- `.navig/plans/ROADMAP.md`

This keeps startup flow minimal: pick a space → get next actions instantly.

---

**Remember:** NAVIG is the secure, unified way to interact with remote servers. Direct SSH/database connections bypass security, tunnel management, and error handling. Always use NAVIG commands.

---

## 39. Device Identity (`navig node`)

> ⚠ **Not implemented — verified against the CLI.** These commands are stubs: they print
> a notice and **exit 1**. The chapter documents the intended design, not shipped behaviour.
> `navig node` currently belongs to the mesh (`navig mesh peers`); the device-identity design below has no shipped equivalent — `navig whoami` is the nearest thing that runs.


Manage local device fingerprint and identity files.

| Command | Description |
|---------|-------------|
| `navig node init` | Generate `DEVICE.md` under `<device_dir>/` (fingerprint, hostname, OS, hardware) |
| `navig node show` | Display device info, key NAVIG paths, and fingerprint hash |
| `navig node fp` | Print device fingerprint hash only (scripting-friendly) |
| `navig node edit [device\|soul\|rules]` | Open `DEVICE.md`, `SOUL.device.md`, or `RULES.md` in `$EDITOR` |

**Examples:**
```bash
navig node init        # First-time device registration
navig node show        # Inspect paths and device summary
navig node fp          # Get fingerprint for cross-device comparisons
navig node edit soul   # Edit device-local SOUL personality file
```

---

## 40. Identity & Persona Management (`navig agent personality`)

> ⚠ There is no `origin` command — it is a group with no subcommands. Identity management
> already ships under `navig agent personality` and `navig agent soul`; the table below maps
> the old names.

Manage named personalities used by the autonomous agent and Telegram bot.

| Command | Description |
|---------|-------------|
| `navig agent personality list` | List available personalities |
| `navig agent personality show <name>` | Show personality details |
| `navig agent personality set <name>` | Set the active personality |
| `navig agent personality create` | Create a custom personality |
| `navig agent soul show` | Display the current `SOUL.md` |
| `navig agent soul create` | Create a user `SOUL.md` from the default template |
| `navig agent soul edit` | Open `SOUL.md` in `$EDITOR` |
| `navig agent soul path` | Show the `SOUL.md` file paths |

| Retired name | Use instead |
|--------------|-------------|
| `origin list` | `navig agent personality list` |
| `origin show` | `navig agent personality show` · `navig agent soul show` |
| `origin use <name>` | `navig agent personality set <name>` |
| `origin init` | `navig agent soul create` |
| `origin set-path` | no equivalent — `navig agent soul path` reports where they live |
| `origin clear` | no equivalent — set a different personality instead |

**Examples:**
```bash
navig agent personality list          # See all personalities
navig agent personality set witty     # Activate one
navig agent soul show                 # Verify the active identity
```

---

## 41. Session Boot & Diary (`navig boot`)

Bootstrap daily sessions and manage the device + session diary system.

Session diaries are stored at `<session_dir>/YYYY-MM-DD.md`.
Device context lives at `<device_dir>/DEVICE.md`.

| Command | Description |
|---------|-------------|
| `navig boot init` | Create boot dirs, scaffold today's session diary, generate `DEVICE.md` if missing |
| `navig boot show` | Print `DEVICE.md` summary and today's session file content |
| `navig boot edit [device\|session\|boot]` | Open device, today's session, or boot config in `$EDITOR` |
| `navig boot log` | List recent session diary files |

**Examples:**
```bash
navig boot init            # Morning setup: creates today's YYYY-MM-DD.md
navig boot show            # Quick device + session overview
navig boot edit            # Edit today's session diary
navig boot log             # Browse past sessions
```

---

## 42. Contextual Namespaces (`navig space`)

Manage *spaces* — contextual namespace bundles that group workspace settings, tools, and overlays for a specific project or role.

| Command | Description |
|---------|-------------|
| `navig space list` | List all available spaces |
| `navig space init <name>` | Create a new space |
| `navig space use <name>` | Activate a space |
| `navig space books [name]` | Show or set the finance BOOK this space keeps its ledger in (`--clear` for the default) |
| `navig space show [name]` | Show space details |
| `navig space jump <name>` | Switch to space and `cd` to its root |
| `navig space clear` | Deactivate the current space |
| `navig space pack <name>` | Bundle space into a portable archive |
| `navig space install <archive>` | Install a packed space |
| `navig space validate [name]` | Validate space configuration |
| `navig space apply [name]` | Apply space overlays to working directory |
| `navig space unapply [name]` | Remove applied overlays |
| `navig space diff [name]` | Show diff of pending space changes |
| `navig space publish <name>` | Publish space to the registry |
| `navig space workspace generate` | Generate workspace config from active space |

**Examples:**
```bash
navig space list
navig space init devops-prod    # Create "devops-prod" space
navig space use devops-prod     # Activate it
navig space show                # Inspect the active space
navig space pack devops-prod    # Archive for sharing
```

---

## 43. Agent Loadout Blueprints (`navig blueprint`)

> ⚠ **Not implemented — verified against the CLI.** These commands are stubs: they print
> a notice and **exit 1**. The chapter documents the intended design, not shipped behaviour.
> For outcomes that DO run, see Blocks: `navig block list` · `navig apply <id>`.


Blueprints are YAML loadout definitions that specify which skills, tools, prompts, and personas the agent should use for a specific role or project.

YAML files live at `<store_dir>/blueprints/*.yaml`.

| Command | Description |
|---------|-------------|
| `navig blueprint list` | List all available blueprints |
| `navig blueprint show <name>` | Display a blueprint's contents |
| `navig blueprint apply <name>` | Apply a blueprint to the active agent session |
| `navig blueprint create <name>` | Create a new blueprint from a wizard |

**Examples:**
```bash
navig blueprint list
navig blueprint show ops-lead       # Inspect before applying
navig blueprint apply ops-lead      # Switch agent to ops-lead loadout
navig blueprint create my-loadout   # Scaffold a custom blueprint
```

---

## 44. Loadout Snapshots (`navig deck`)

Decks are saved snapshots of an applied Blueprint — a concrete timestamp of what was loaded, which can be restored later.

| Command | Description |
|---------|-------------|
| `navig deck list` | List all saved deck snapshots |
| `navig deck show <name>` | View snapshot details |
| `navig deck apply <name>` | Restore agent state from a snapshot |
| `navig deck save [name]` | Save current agent loadout as a new deck |
| `navig deck remove <name>` | Delete a deck snapshot |

**Examples:**
```bash
navig deck save sprint-42-loadout   # Save current state
navig deck list                     # Browse saved decks
navig deck apply sprint-42-loadout  # Restore it later
navig deck remove old-deck          # Housekeeping
```

---

## 45. Portable / encrypted config

> ⚠ There is no `portable` command — its two verbs are stubs that exit 1. The capability
> already ships, split across an existing command and an environment variable, so building a
> second one would duplicate it.

Carry a self-contained, encrypted NAVIG config on a USB drive or an external path.

| Want | Use |
|------|-----|
| Export config **with secrets**, encrypted | `navig backup export --include-secrets --encrypt -o <dest>` |
| Inspect what a bundle contains | `navig backup show` |
| Restore from one | `navig backup import <file>` |
| Run against a config on another drive | `NAVIG_CONFIG_DIR=<path> navig …` |
| Confirm which config is active | `navig paths` (the `config` row) |

`NAVIG_CONFIG_DIR` is what "mounting" a portable vault actually means — every path NAVIG
resolves hangs off it, so pointing it at a drive switches the whole install for that process.
There is no persistent enable/disable: set the variable for the session, or don't.

| Retired name | Use instead |
|--------------|-------------|
| `portable export <dest>` | `navig backup export --include-secrets --encrypt -o <dest>` |
| `portable status` | `navig paths` |
| `portable enable <path>` | `NAVIG_CONFIG_DIR=<path>` |
| `portable disable` | unset `NAVIG_CONFIG_DIR` |
| `portable init <path>` | no equivalent — export into the path instead |

**Examples:**
```bash
# Make an encrypted, secret-bearing copy
navig backup export --include-secrets --encrypt -o /media/usb/navig-config.tar.gz

# Work against a config carried on that drive
NAVIG_CONFIG_DIR=/media/usb/navig navig doctor
```

**Security:** `--include-secrets` writes unredacted credentials. Always pair it with
`--encrypt`, and keep the passphrase separate from the drive.

---

## 46. Package Runtime Notes (the `package` command) — removed

The legacy `package` runtime (handler.py packs, `navig.package.json`,
`packages_autoload.json`) and the built-in `core/packages/` tree were **removed** in 2026-07.
Every capability now lives natively in `core/navig/` or in a first-party `plugins/navig-*`
package (pyproject entry points).

- The one unique pack capability — music-link conversion — is now
  `navig download music-links <url>` (song.link / Odesli).
- Migrate any on-disk **user** packs under `~/.navig/packs` with `navig doctor migrate-packs`.
- Full migration ledger: `docs/legacy-packages-retirement.md`.

---

## 47. Universal Import Engine (`navig import`)

Import external data (servers, contacts, bookmarks) into a normalized schema.

| Command | Description |
|---------|-------------|
| `navig import --source all` | Run every built-in importer with default paths |
| `navig import --source <name> --path <file>` | Run one importer against a custom path |
| `navig import --output results.json` | Write full normalized output to JSON |
| `navig import list-sources` | List available importers |

Validation behavior:

- Unknown `--source` values return an explicit error.
- `--path` must exist; missing paths return an explicit error.
- `--path` cannot be combined with `--source all`.

Built-in sources:

- `winscp` → `WinSCP.ini` / `.reg` server imports
- `telegram` → Telegram Desktop `contacts.json` or export ZIP
- `chrome` / `edge` / `firefox` / `safari` → browser bookmarks

Bookmark imports are persisted into the existing links database by default.

**Examples:**
```bash
navig import --source all --output results.json
navig import --source chrome --path /custom/path/Bookmarks
navig import list-sources
```

### Related Commands

- `navig links import <file> --source auto` now supports native browser bookmark files.
- `navig contacts import <contacts.json|export.zip>` imports Telegram contacts into NAVIG contacts storage.

---

## 48. Telegram Operations (`navig telegram` / `navig gateway test`)

Telegram management commands now include direct message sending and target resolution.

| Command | Description |
|---------|-------------|
| `navig telegram status` | Show Telegram bot configuration + active session count |
| `navig telegram sessions list` | List active Telegram sessions |
| `navig telegram send <chat_id|@username> --message "..."` | Send message using configured bot token |
| `navig telegram send @username --message "..." --resolve-only` | Resolve target without sending |
| `navig gateway test telegram --target <chat_id|@username>` | Run Telegram smoke-test through gateway test flow |
| `navig gateway test telegram --target <chat_id|@username> --strict` | Fail with non-zero exit code if channel test fails |
| `navig gateway test telegram --target <chat_id|@username> --json` | Emit machine-readable JSON summary for automation |
| `navig telegram extensions list` | Show every bot feature bundle and whether it is on (`--json` for scripts) |
| `navig telegram extensions enable <name>` | Switch a feature bundle on |
| `navig telegram extensions disable <name>` | Switch a feature bundle off |
| `navig telegram extensions info <name>` | What one bundle owns, and what switching it off does |
| `navig contacts import <path>` | Import Telegram contacts from `contacts.json` or export ZIP |

**Examples:**
```bash
navig telegram status
navig telegram sessions list
navig telegram send 123456789 --message "Gateway online"
navig telegram send @myuser --message "ping" --resolve-only
navig gateway test telegram --target 123456789 --message "health-check"
```

Notes:

- `@username` resolution depends on recent updates seen by the bot.
- If username resolution fails, use numeric `chat_id` or have the user message the bot first.
- `navig gateway test telegram` requires `--target`.
- `navig gateway test all` tests Telegram and Matrix in one run.

### Extensions — switching bot features off

The bot's ~108 commands, its inline buttons and its scheduled messages are grouped
into **extensions** you can switch off individually. When one is off its commands
leave `/help` and Telegram's `/` autocomplete, its buttons stop answering (a stale
one explains itself and clears), and anything it sends on a schedule is not
delivered. Nothing is deleted — switching it back on restores it exactly.

Three surfaces, one state (`modules.overrides`, the same store the module registry
uses), so they cannot disagree:

```bash
navig telegram extensions list                 # the table
navig telegram extensions disable habits       # by id, label, or any command it owns
navig telegram extensions info habits          # what it owns and what "off" means
```

- In Telegram: **`/extensions`** — a tap-to-flip card. Also reachable from `/help`
  and `/settings`. It is a locked command: it is the switch for every other
  switch, so it can never be switched off itself (nor can `/start`, `/help`,
  `/status`, or plain chat).
- In the Deck: **Social → Telegram → Extensions**.

Two switches exist and they compose as AND: an extension covers a whole feature,
while Deck → Social → Telegram → **Commands** disables one command inside it. A
command whose extension is off shows an `extension off` chip there rather than a
toggle that would do nothing.

**Habits specifically.** Switching Habits off stops the daily check-in cards and
reminders from being delivered, but deliberately does **not** rewrite your
schedule — so habits you paused yourself with `navig habit pause` stay paused when
you switch it back on. Because the schedule is untouched, `navig habit list`,
`/habits` and `/health` show a banner saying the reminders are scheduled but not
delivered, rather than reporting a healthy count into a void.

---

## 49. Maintainer Release Shortcuts

For maintainers, NAVIG includes a helper to bump package version and publish a git tag quickly.

Python helper (no npm required):

```bash
python tools/version_bump.py show
python tools/version_bump.py bump patch --commit --tag --push
python tools/version_bump.py bump minor --commit --tag --push
python tools/version_bump.py bump major --commit --tag --push
```

Optional npm-style aliases:

```bash
npm run release:dry
npm run release:normal
npm run release:minor
npm run release:big
```

Mapping:

- `release:normal` = patch bump (`X.Y.Z` -> `X.Y.(Z+1)`)
- `release:minor` = minor bump (`X.Y.Z` -> `X.(Y+1).0`)
- `release:big` = major bump (`X.Y.Z` -> `(X+1).0.0`)
- `release:dry` = preview next patch bump only (no file/git changes)

---

## 50. Multi-Agent Repo Guard (`navig repo`)

When several agents (Claude Code sessions, humans) work on one repository in
parallel, two hazards appear: merge conflicts that only surface at merge time,
and a shared main checkout where one agent's `git checkout`/`rebase` can
destroy another agent's uncommitted edits. `navig repo` surfaces both early —
read-only, nothing is modified.

```bash
navig repo new <slug>           # create .dev/worktrees/<slug> on feat/<slug> (based on latest origin)
navig repo new <slug> --type fix    # fix/<slug> instead; --from <ref> to override the base
navig repo remove <slug>        # reliably remove that worktree (unregister + delete, no leak)
navig repo remove <slug> --force    # discard the worktree's uncommitted changes too
navig repo conflicts            # simulate merges between EVERY pair of worktrees
navig repo conflicts --json     # machine-readable; exit 2 when any pair conflicts
navig repo conflicts --no-dirty # committed state only (default includes dirty)
navig repo stale                # leftover worktrees / unmerged branches / stashes / orphan dirs
navig repo stale --json
navig repo lock                 # who holds the main-checkout agent lock
navig repo lock release [--force]
navig repo prune                # dry-run: list orphaned .dev/worktrees dirs git no longer tracks
navig repo prune --yes          # delete them (safe: skips live worktrees + locked dirs, says why)
navig repo prune --yes --force  # also delete live worktrees (may hold uncommitted work) — rare
```

All of these (`new` / `remove` / `conflicts` / `stale` / `lock` / `prune`) accept `--repo
<path>`, and fall back to `NAVIG_REPO` / `CLAUDE_PROJECT_DIR` when the process cwd
is not the repo — so an agent can drive them reliably from a subshell (a launched
`navig.exe` does not always inherit the shell's directory).

Details:

- **`new`** creates an isolated worktree for a parallel session — the sanctioned
  way to run a second/third agent. It bases the branch on the latest
  `origin/<default-branch>` (fetched first), not your possibly-behind local
  checkout, then prints the folder to open. Refuses unsafe slugs, existing dirs,
  and existing branches; never creates a sibling folder outside the repo.
- **`remove`** reliably removes a `.dev/worktrees/<slug>` worktree — unregister
  **and** delete. `git worktree remove` alone often can't delete the folder on
  Windows (a scanner briefly holds the fresh checkout), leaving an orphan; this
  unregisters via git (which refuses a *dirty* worktree without `--force`, so
  uncommitted work is protected) then retries the physical delete with backoff,
  so a finished worktree doesn't leak. Use it instead of raw `git worktree remove`.
- **`conflicts`** runs an in-memory three-way merge (`git merge-tree
  --write-tree`, requires git >= 2.38) across all worktree pairs. Uncommitted
  *tracked* changes are included via `git stash create` (writes objects only —
  the working tree is never touched). Untracked files are not compared.
- **`stale`** flags worktrees left behind (including forbidden sibling
  checkouts outside the repo), branches not merged into the default branch
  (with ahead counts and gone-upstream hints), stashes, the agent lock, and
  **orphaned `.dev/worktrees/` dirs** git no longer tracks (see `prune`).
- **`lock`** inspects `.dev/agent.lock`, written by the Claude Code hook
  `scripts/agent-hooks/agent_lock.py` — one session at a time may mutate the
  main checkout; parallel sessions work under `.dev/worktrees/` (always
  exempt). The lock auto-expires after 60 minutes without activity. Hook
  wiring instructions: `scripts/agent-hooks/README.md`.
- **`prune`** removes orphaned worktree directories — physical dirs under
  `.dev/worktrees/` that git no longer tracks. `git worktree remove` often
  can't delete a worktree's folder on Windows (a live handle blocks it), so git
  unregisters it and the directory lingers, invisible to `git worktree list` and
  piling up (GBs) across parallel sessions. Runs `git worktree prune` (metadata)
  then deletes the leftover dirs. Dry-run by default (`--yes` to delete); it
  never touches a registered worktree and never deletes outside `.dev/worktrees/`.
  **Safe by default: committed work is never at risk** (prune deletes directories,
  not branch refs), and a dir that is still a *live* worktree/repo of its own —
  one that could hold uncommitted work — is skipped unless `--force`. The dry-run
  marks each dir "dead leftover" (safe) vs "LIVE — needs --force". A briefly
  "locked" dir is a scanner (antivirus/indexer) holding a fresh checkout — the
  delete retries with backoff, and it clears within a minute (verified: not the
  daemon), so just re-run; a reboot clears any stubborn one.

### Install the guard into any repo

```bash
navig repo guard install [--repo <path>]    # hooks + Claude Code wiring + .dev/ gitignore
navig repo guard status [--repo <path>] [--json]
navig repo guard uninstall [--repo <path>]
```

`install` writes the two hook scripts (shipped inside navig as the
`navig.guard` package) to `<repo>/.claude/hooks/`, merges the wiring into
`<repo>/.claude/settings.json` — idempotent, existing user hooks preserved,
machine-absolute script paths — and ensures `.dev/` (lock + worktrees home)
is gitignored. Run once per machine per repo; new Claude Code sessions pick
it up automatically.

The lock hook also enforces the sibling-worktree rule for every session:
`git worktree add` targeting a path **outside** the repo is blocked with
instructions to create it under `.dev/worktrees/` instead.

Full concept page (why it exists, architecture, hard-won rules):
`docs/repo-guard.md` at the repo root.

The session briefing also runs the cross-worktree merge radar: when two or
more worktrees exist, every new session starts with either
`cross-worktree merge check: N pair(s), all clean` or a
`merge conflict brewing: <a> <-> <b> (files...)` line per colliding pair —
collisions surface at session start, not at merge time.
