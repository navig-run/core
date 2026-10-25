> **Exhaustive reference (2026-10-09):** this is the full command reference (it ships with the package). For the friendly human entry point, start at [docs/guides/HANDBOOK.md](../../../docs/guides/HANDBOOK.md).

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
27b. [NAVIG Reflex — `navig endpoints` + `navig ask` plan/commit](#27b-navig-reflex)
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

- `CHANGELOG.md` is tracked and remains the release/public history source of truth. New core entries
  are written as fragments under `changelog.d/` (one file per change, so concurrent branches never
  conflict) and folded in with `npm run changelog:assemble` — see `changelog.d/README.md`.
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

The installers apply a named installation profile for you with `-p/--profile`
(validated by `navig init`, which exits non-zero and lists the valid names for
anything unknown):

```bash
curl -fsSL https://navig.run/install.sh | bash -s -- -p server   # Linux/macOS
```

```powershell
# Windows (the `iwr | iex` one-liner takes no args — set the env var first)
$env:NAVIG_PROFILE = "server"; iwr -useb https://navig.run/install.ps1 | iex
```

Profiles are assembled from existing levers only (init modules, pip extras,
`navig service install` flags, a one-time mode suggestion) — they are
**installation presets, not security boundaries**. Applying one records
`install.profile` in the config (visible in `navig config settings` and
`navig doctor`), and `navig init --profile <name> --dry-run` prints an
effective-profile report (modules, extras, daemon flags, mode) without changing
anything.

After running `install.sh` / `install.ps1`, finalize setup with:

```bash
navig init                        # interactive CLI onboarding (default)
navig init --status               # show compact init/setup status summary
navig init --tui                  # opt-in full-screen TUI onboarding
navig init --profile cli          # public: CLI only — config dirs + CLI check, no service
navig init --profile minimal      # public: CLI + shell PATH, no service
navig init --profile server       # public: headless stack + MCP; daemon (gateway + scheduler)
navig init --profile full         # public: everything the default install runs today
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
| `cli` | **public** — config dirs, CLI verify; registers **no** service |
| `minimal` | **public** — `cli` + shell PATH; registers **no** service |
| `server` | **public** — headless: dirs, CLI, migration, shell, vault, Telegram token, MCP; registers the daemon with `--bot --gateway --scheduler` |
| `full` | **public** — everything the default install runs today (`operator` modules + daemon with platform defaults) |
| `quickstart` | alias of `operator` + terminal-to-Telegram onboarding handoff |
| `node` | config dirs, CLI verify, legacy migration |
| `operator` | + shell PATH, vault init, Telegram token |
| `architect` | + MCP config file |
| `system_standard` | + system service registration |
| `system_deep` | + Windows tray install |

**`NAVIG_NO_DAEMON=1` only skips daemon registration** — it does not make the
machine a monitoring server. Resident monitoring (heartbeats, scheduled checks,
alert delivery) runs in the daemon: use the **`server`** profile when a box
should be monitored continuously.

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
navig start                        # Start gateway + bot (background) — through the installed
                                   # service when there is one (a living parent, autostart),
                                   # else a detached worker that writes ~/.navig/worker.pid
navig start --foreground           # Start with live logs
navig bot status                   # Check if bot is running (names the daemon when it owns it)
navig bot stop                     # Stop all services (stops the daemon gracefully when it owns the bot)
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

# ═══ YOUR TASK LIST (the PIM) ═══
navig todo                         # what's open, soonest first
navig todo add "Dentist tomorrow 10:30"   # the date can be part of the sentence
navig todo add "Rent every month" -c life # ...and so can the repeat
navig todo add "Visio Sarah oct 26 10:30" -r 3d -r 1d -r 1h   # ping 3 days, 1 day, 1 hour before
navig todo add "Call the clinic back" --at "2026-10-12 09:00" -s health   # exact time, text kept as typed
navig todo when <id> next friday   # give an inbox item a date
navig todo done <id>               # tick it off (a repeating one rolls forward)
navig todo rm <id>                 # delete it for good
navig todo categories              # your categories, with open counts
navig todo scan                    # find `- [ ]` work in your spaces
navig todo scan --add              # ...and add them as suggestions to confirm
navig todo list --json             # machine-readable, for scripts and agents

# Same list as `/todo` in Telegram. Reminders are delivered through the bot, so they
# need the `todo` extension switched on (`/extensions`); the tasks are kept either way.
# An appointment sent to the bot as a plain message — a day plus a time, in English,
# French or Russian ("lundi 26 octobre 2026 à 10h30", "demain à 10h", "lundi prochain
# 9h15", "завтра в 15:00", "через 2 дня в 9:00") — is saved to this list with reminders
# 3 days, 1 day and 1 hour before. The reply has 🔔 toggles and Undo.
#
# /agenda lists APPOINTMENTS by day — they are their own thing, never mixed into the
# to-do list. Proposals (a relative-day message with no appointment word) wait there
# for ✓/✕ and ping nothing until accepted. Tasks take a category inline: `#work`.
# NAVIG Desktop and the Mini App read the same data over /api/deck/planner/*.
#
# /reminders (or 🔔 on the /todo card) lists every upcoming ping by day with its exact
# time: a task's pings (📋, tap to open the task and switch them) and one-off
# /remindme pings (✕ cancels). Todo = the things; reminders = when you get pinged.
#
# /remindme reads the same dates, anywhere in the line: "/remindme in 30 min check logs",
# "/remindme demain 10h appeler le notaire", "/remindme 26 октября 10:30 врач". So does
# "rappelle-moi / напомни / remind me …" typed as a plain message — one ping at that time.
# A relative day with no appointment word ("on se voit demain à 10h") is OFFERED with a
# Save button instead of being saved silently.

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
navig skill list                  # List AI skills
navig skill tree                  # Show skills by category
navig skill show <name>           # Show skill details and commands
navig skill run <skill>:<cmd>     # Run a skill command
navig skill run <skill> [args]    # Run skill entrypoint (py/js)
navig ask "question"               # Ask AI (canonical)
navig wiki show <topic>            # Show wiki page
navig wiki list                    # List wiki pages
```

### Removed and renamed commands (migration)

⚠ Most of these no longer exist at all — they error rather than warn. Verified against the
CLI:

| Removed name | Use instead |
|--------------|-------------|
| `monitor` | `navig host monitor` |
| `security` | `navig host security` for the remote audits. The name exists again as a NEW group — `navig security`, whose command today is `migrate-host-secrets` (host YAML passwords → vault). |
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

> **Not your body.** `navig health` is an alias for `navig stack` and checks
> *machines*. Weight, sleep and mood live under **[`navig body`](#navig-body)**.

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

### `navig body`

Track body metrics — weight, sleep, mood — and see the trend. The counterpart to
`navig habit`: habits are things you *do*, body metrics are things you *measure*.

Measurements live in **`metrics.csv` in the space**, the same way habits live in
`habits.csv` — so the file stays readable, editable by hand, and yours. A column
you add yourself is preserved; logging twice on one day replaces rather than
duplicates; the write is atomic and refuses to lose rows.

**Examples:**
```bash
navig body                                  # today, plus where the week stands
navig body log --weight 87.4                # a comma decimal (87,4) works too
navig body log --sleep 7.5 --mood 8
navig body trend --days 14
navig body export                           # a dated summary into the space's out/
navig body today --json                     # every subcommand takes --json
```

**The 7-day average is the headline, not the latest reading.** Day-to-day weight
is dominated by water, food in transit and time of day; a single pair of readings
a week apart cannot tell a real change from noise. An unknown trend prints as
`no prior week`, never as `0.0` — those are different claims.

**Check-ins.** `navig body checkin --send` asks for this morning's weight in
Telegram; `--weekly` sends the Sunday card (trend, mood, sleep, treatment).
Schedule them like habits:

```bash
navig body checkin --send --space <space>            # e.g. 08:10 daily
navig body checkin --weekly --send --space <space>   # e.g. 18:45 Sunday
```

**How often it asks — `navig body cadence`.** The scheduled weigh-in never asks
twice in one day (a daemon restart or a timed-out retry used to re-ask), skips a
day you already weighed, and skips a catch-up run after the latest hour (default
noon) — a machine that was off at 08:10 does not ask at dinner. Each skip exits 0
and says why; `--force` sends anyway. `/weigh` in Telegram is never affected.

```bash
navig body cadence                                    # settings, last weighed, next day it asks
navig body cadence --every 3 --latest-hour 12         # every 3 days, before noon
navig body cadence --days mon,thu                     # only on these days ("any" clears)
navig body cadence --off                              # stop the scheduled question (--on)
```

| Key | Default | Meaning |
|-----|---------|---------|
| `health.weigh_in.enabled` | `true` | Scheduled weigh-in on/off |
| `health.weigh_in.every_days` | `1` | Ask only when the last recorded weight is at least N days old (1-30) |
| `health.weigh_in.weekdays` | `[]` (any) | Only on these days, e.g. `[mon, thu]` |
| `health.weigh_in.latest_hour` | `24` (no cutoff) | Do not ask from this local hour on (1-24; 24 = any time) |

Logging a weight with `/weigh 87.4` or `navig body log --weight` also clears the
morning question, so Telegram's reply box stops re-appearing.

In Telegram: **`/health`** (where the body stands), **`/weigh`** (log now, or
`/weigh 87.4`), **`/body`** (the weekly card). All three belong to the **Health**
extension, switched independently of Habits. Messages follow your global output
language — change it with `/lang`.

**It never diagnoses.** A fast rate of loss produces one neutral line suggesting
you mention it at your next appointment, and nothing else. Anything you write
about a treatment is recorded verbatim and never interpreted.

**Related Commands:** `navig habit`, `navig life`, `navig health` (that one checks
*machines*, not you), `navig checkin`

---

### `navig checkin`

Scheduled check-ins: the bot asks, you answer with one tap. Three kinds share one
engine — the **weigh-in** (a number, recorded in `metrics.csv`), **pills** (a dose:
Taken · Skip · Snooze 15 min · Taken earlier…) and **water** (a count against a
daily target, `+1` / `+2`). Not habits on purpose: a habit is done-or-not, a
check-in is a measurement or a dose.

**Examples:**
```bash
navig checkin                                             # = list: every check-in + today
navig checkin add pills --name "Metformin" --dose "500 mg" --at 08:00,20:00
navig checkin add water --target 8 --every 2h --from 9 --to 21
navig checkin pause metformin                             # resume metformin brings it back
navig checkin log metformin --slot 08:00 --at 07:40       # record a dose by hand
navig checkin stats --days 30                             # adherence %, water on-target days
navig checkin remove metformin                            # records are kept
navig checkin migrate --dry-run                           # old weigh-in jobs → checkin:weigh
```

Every subcommand takes `--json`. Pills and water are recorded append-only in
**`checkins.jsonl` beside `metrics.csv`** (UTC timestamp + the local day it
belongs to); the project indexer never ingests it, so it stays out of AI context.

**Pills — the safety rules.** Nothing is ever marked *taken* without your tap or a
typed time: a prompt left unanswered for 24 h is closed as *missed*, and chat tidy
never deletes an open pill prompt. A second *Taken* for the same dose asks you to
confirm first (`navig checkin log` needs `--force` for the same reason). One
"still open" nudge goes out `--missed-after` minutes after the dose (default 120),
never twice. The dose is shown exactly as you typed it — the bot never gives dose
advice. A daemon that comes back hours late does not ask an old dose as if it were
due now.

**Schedule.** Each check-in owns cron jobs named `checkin:<id>[:<HHMM>]` running
`NAVIG_CHECKIN:<id>:<chat>[:<HHMM>]` inside the scheduler (no CLI cold start; 180 s
timeout; "asked" is recorded before the send, so a retry never asks twice). Times
are your local wall clock, DST included. `navig checkin migrate` converts the
legacy `habit:weigh` / `habit:bodyreview` jobs to `checkin:weigh[:weekly]` —
same schedule, same chat — after writing a timestamped backup of the job store;
running it twice changes nothing. The weigh-in's cadence stays in
`health.weigh_in.*` (`checkins.weigh.*` is accepted as an alias).

In Telegram: **`/checkins`** (today, with a `+1` water button). Pills and water
belong to the **Check-ins** extension; the weigh-in keeps following **Health**.
In the Mini App: Settings → Telegram → Check-ins.

**Related Commands:** `navig body`, `navig habit`

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

### `navig security migrate-host-secrets`

Move plaintext host secrets (`ssh_password`, `database.root_password`,
`database.password`) out of `~/.navig/hosts/*.yaml` and into the vault. **Dry run
by default** — the report shows Host · Key · Source file · Target ref · Value
length and never the value.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `--apply` | flag | No | Vault each secret, write `<host>.yaml.bak.<timestamp>`, rewrite the YAML with `*_ref` only |
| `--rollback` | flag | No | Restore the newest `*.yaml.bak.*` per host (refuses when none) |
| `--purge-backups` | flag | No | Delete the `*.yaml.bak.*` backups (the last plaintext copies) of hosts whose refs decrypt from the vault; keeps any other backup and exits 1 |

**Examples:**
```bash
navig security migrate-host-secrets              # dry run — nothing written
navig security migrate-host-secrets --apply      # migrate (keeps a .bak. per host)
navig security migrate-host-secrets --rollback   # restore the newest backups
navig security migrate-host-secrets --purge-backups  # after `navig host test`: delete the plaintext backups
```

**Notes:** legacy plaintext files keep working without migration (loading
resolves refs back to the original keys). If the vault is unavailable the
command fails with a clear error and writes nothing — never plaintext.
`--apply`, `--rollback` and `--purge-backups` are mutually exclusive (exit 2). The backups are never
included in `navig backup export` archives.

**Related Commands:** `navig host security`, `navig vault`

---

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
| `--format`, `-f` | string | No | Format: `archive` (.tar.gz, default) or `json` |
| `--encrypt`, `-e` | flag | No | Encrypt with a password |
| `--password`, `-p` | string | No | Encryption password (prompted when omitted) |
| `--include-secrets` | flag | No | Include actual secrets (default: redacted) |
| `--brain` | flag | No | Also carry this install's state — databases, memory, sessions, cron jobs, agent workspace, approvals, license, `.env`. Implies `--include-secrets`, **requires** `--encrypt`, archive only |

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

# Move this whole brain to another machine
navig backup export --brain --encrypt --output ~/backups/brain.tar.gz
```

**💡 Tip:** Secrets are automatically redacted by default for safe sharing. A `--brain`
bundle deliberately does **not** include the vault (its key is bound to this machine) —
move that separately with `navig vault backup` / `navig vault restore`.

---

### `navig backup import`

Import NAVIG configuration from a backup file.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | path | Yes | Path to backup file |
| `--merge` / `--replace` | flag | No | `merge` (default): keep existing entries, add only new ones. `--replace`: overwrite entries the backup contains. Neither mode deletes anything. |
| `--password`, `-p` | string | No | Password for encrypted backups (prompted when omitted) |
| `--brain` | flag | No | Also restore state from a `--brain` export. **Refuses while the daemon is running.** |

**Examples:**
```bash
# Import with merge (keeps existing, adds new)
navig backup import navig-export-2025-01-06.json

# Import, overwriting the entries the backup contains
navig backup import backup.json --replace

# Import encrypted backup
navig backup import backup.tar.gz.enc --password mypassword

# Restore a brain on a new machine (daemon stopped)
navig backup import brain.tar.gz --brain --password mypassword
```

**💡 Tip:** every archive carries `config.yaml`, and it is applied at last: always
for `--brain` or `--replace`, and under the default `merge` only when the target has
no settings (merge fills a gap — it never takes settings over). The previous file is
kept as `config.yaml.pre-import`. A settings file that will not parse refuses the
whole import *before* anything is written.

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

### `navig move` — move this NAVIG to another machine

`backup` copies settings. `move` moves the whole NAVIG: config, spaces, personas, plans,
memory and stores, every vault secret, and NAVIG Desktop's settings and workspaces. It also
moves the **identity** (the deck key your Telegram bot and relay answer to), so the old
machine stands down and only one machine ever answers as this NAVIG.

```bash
# On the old machine
navig move out                  # shows what moves, stops NAVIG, writes navig-<host>-<date>.navigmove
navig move out -o ~/Desktop     # write it somewhere else

# Copy the file across (USB stick, scp, a shared folder), then on the new machine
navig move in navig-pc-20261010-0530.navigmove      # asks for the passphrase
navig service start
```

- **One file, one passphrase.** The bundle is AES-256-GCM, with the key derived from a
  one-time passphrase shown **once** by `move out` (write it down). Nothing decrypted is
  written to disk on either machine. A wrong passphrase, a damaged file, or a file cut
  off while copying is refused before anything changes.
- **Secrets are re-sealed, not copied.** The vault's key is bound to the machine, so its
  files would not open anywhere else. `move out` decrypts each secret into the bundle,
  and `move in` seals it again for the new machine. If your vault is protected by a
  passphrase, `move out` asks for it (run it in a terminal).
- **The old machine stands down.** `move out` stops the daemon *before* packing (so the
  databases are consistent), checks that the file opens, and only then marks this NAVIG
  as moved. While that mark is there, the daemon, `navig gateway start` and
  `navig service start` refuse to run. Telegram messages wait at your relay until the new
  machine connects. If packing fails, nothing is marked.
- **The new machine never loses anything.** Whatever NAVIG it already had is renamed to
  `~/.navig.before-move-<time>` (and `~/.navig-os.before-move-<time>`). A machine that
  already has its own identity is refused unless you pass `--replace`.
- **Paths follow the new home.** Spaces, the space registry and NAVIG Desktop workspaces that
  pointed under the old home point under the new one, even across Windows and Linux.
- **NAVIG Desktop sign-ins travel too.** Like the vault, NAVIG Desktop seals them to the machine,
  so NAVIG Desktop itself decrypts them on the old machine and seals them again on the new
  one. When NAVIG Desktop is not installed on one side, they stay behind: `move out` and
  `move in` say so and name the connections to sign in to again.
- **Not carried:** this machine's runtime state (logs, cache, pid files, the bundled
  Python), regenerable folders (`node_modules`, `.venv`), and the brain link of a NAVIG Desktop
  that was linked to another machine.
- `navig move status` says whether this NAVIG has moved away; `navig move undo` brings it
  back on the old machine (stop the new one first: two machines must never answer as
  the same NAVIG).

| Command | Description |
|---------|-------------|
| `navig move out [-o PATH] [--yes] [--json]` | Pack this NAVIG into one encrypted file and stand it down |
| `navig move in FILE [--replace] [--yes] [--json]` | Unpack it here (the passphrase is asked, or read from `NAVIG_MOVE_PASSPHRASE`) |
| `navig move status [--json]` | Has this NAVIG moved to another machine? |
| `navig move undo [--yes]` | Bring it back on the old machine |

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

**Desktop Apps (Windows only).** When NAVIG Anchor is installed, three rows say which
binary is installed (path + build stamp — every rebuild reports the same version, the
stamp is what tells them apart), whether the running Anchor *is* that binary (matched by
path, not process name), and what **Launch at login** points at. A `Run` entry that names
a different binary — typically a development build under `target/debug`, whose frontend
is a dev server that is never up at boot, so every window is a browser error page — is a
⚠ with the fix (re-enable Launch at login from the installed Anchor's Settings). An
unquoted path is also a ⚠: under a directory with spaces, `C:\Program.exe` can win the
race. Off is a green "off"; absent from the machine is a green "not installed".

**Release (development checkout only).** Three rows compare what the tree says
(`pyproject.toml` vs `latest.json`), the newest `v*` tag this checkout knows, and what PyPI
serves — the three places that each answer "what version is navig?" and used to disagree
unseen (3.25.0 was published by hand and never tagged; nothing said so for two weeks). A
tree ahead of PyPI or of its newest tag is a ⚠ naming the command (`bash tools/release.sh
X.Y.Z --publish`); PyPI ahead of the tree, or a tag pointing at a tree of another version,
is a ✗. The PyPI row also counts the `[Unreleased]` entries and `changelog.d/` fragments
waiting for a release. PyPI unreachable (or `NAVIG_VERSION_SYNC_OFFLINE=1`) is a ⚠ "not
checked", never a tick.

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

> **⚠️ DEPRECATED** — The Typer-based plugin model (`navig.plugins.base` + its old `plugin install` verbs,
> `navig.plugins.base.PluginAPI`) has been **retired**.
>
> The new decoupled pack model uses `plugin.json` + `handler.py` + plain `handle()` functions
> and lives in the **[navig-registry](https://github.com/navig-run/registry)** repository.
>
> SDK: `pip install navig-sdk` (Python) or `npm install navig-sdk` (TypeScript).
> See [navig-registry/examples/](https://github.com/navig-run/registry/tree/main/examples) for current examples.
>
> The content below is preserved for reference only and describes the **retired system**.

NAVIG supported a modular plugin architecture that allowed extending functionality without modifying core code.

### 19.1 Plugin Management Commands

| Command | Description |
|---------|-------------|
| `navig plugin list` | List all discovered plugins and their status |
| `navig plugin info <name>` | Show detailed information about a plugin |
| `navig store enable plugin:<name>` | Enable a disabled plugin |
| `navig store disable plugin:<name>` | Disable a plugin |
| `navig store install <path>` | Install a plugin from a directory |
| `navig store remove <name>` | Remove an installed plugin |

**Examples:**
```bash
# List all plugins
navig plugin list

# Get detailed info about a specific plugin
navig plugin info hello

# Disable a plugin temporarily
navig store disable plugin:hello

# Re-enable a plugin
navig store enable plugin:hello
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
- Search past work, read/write durable facts, inspect browser sessions and connected providers

**Start MCP Server:**
```bash
navig mcp serve                       # compact 15-tool surface (default)
navig mcp serve --profile full        # the complete legacy catalogue
navig mcp serve --transport stdio     # stdio for editors / Claude Desktop
navig mcp serve --port 3000 --token "$TOKEN"
```

The default is a **routed surface**: fifteen domain tools, each taking an `action`
(`navig_fleet`, `navig_knowledge`, `navig_recall`, `navig_memory`, `navig_runtime`,
`navig_automation`, `navig_web`, `navig_browser_debug`, `navig_browser_user`,
`navig_connectors`, plus separate read/act domains for memory, runtime actions,
Bay/blocks, browser actions and connector writes). This keeps tool selection cheap and
annotations honest; the full catalogue is unchanged behind `--profile full`.

`--only <bundle[,bundle]>` keeps its historical meaning: a restricted **full** catalogue
of those bundles (it overrides `--profile default`). For example `navig mcp serve
--transport stdio --only recall` is the Recall server Claude Code uses (§56.1).

**Exposure and security:**
- A network bind without `--token` is refused; a loopback bind without a token serves
  read-only tools only (`--insecure-local` is the explicit local opt-out).
- Declare `--exposure remote` for a stdio server behind a proxy. Dangerous tools are then
  denied unless `navig config set mcp.remote_dangerous approve` is an explicit decision.
- `desktop_filesystem` is contained to `mcp.files.roots` whenever remotely exposed; no
  roots means deny-all. The file itself is never reachable outside those roots.

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
- `github_models` — GitHub Models (free tier on a GitHub token: `GITHUB_TOKEN`, or `github_models.token` in config.yaml)

**Manage Providers:**
```bash
navig ai providers                # List all providers and status
navig ai providers --add openai   # Add API key for OpenAI
navig ai providers --test anthropic  # Test Anthropic connection
navig ai providers --test xai        # 1-token call; walks the known model ids and skips retired ones,
                                     # so a working key is never reported broken by a dead first model
navig ai providers --remove groq  # Remove Groq API key

# List all available models
navig ai models
navig ai models --provider airllm

# Audit the catalog: call every listed model once and report what the provider has RETIRED.
# `mode doctor` probes what you ROUTE to; this probes what can be SUBSTITUTED IN (models[0] is
# the credential probe and the routing default). Prints a paste-ready RETIRED_MODELS entry per
# finding; exits 1 only for a retired id — never for a slow model or a provider you hold no key for.
navig ai models --check
navig ai models --check --provider groq --json
```

**Model fallback — keep answering when a model fails, and say so:**

When a mode's model fails (a rejected key, a billing cap, a retired model) NAVIG
switches the reply to another model instead of ending it: sibling accounts of the
same model first, then the mode's configured fallback, `agent.fallback_chain`,
your `prefer` list, and — with `auto` on — models the health checks recently saw
answering (`navig ai models --check`, `navig mode doctor` and the daily heartbeat
fill that cache). Each switch is written to `<config_dir>/perf/model_fallbacks.jsonl`,
and the first one for a model sends a **Model fallbacks** notification (Telegram +
deck; repeated at most every 6 h while it stays down). When the model answers
again — a live call, the heartbeat re-check (every beat, 30 min by default) or a
credential change such as re-connecting Claude — you get one "it's back" message.

```bash
navig ai fallback                          # per mode: primary · health · falls back to · last switch
navig ai fallback --json
navig ai fallback set big_tasks --to nvidia:nvidia/nemotron-3-super-120b-a12b
navig ai fallback set big_tasks --clear
navig ai fallback --never-from coding      # coding never switches model — it fails loudly
navig ai fallback --allow coding           # undo
navig ai fallback --auto off               # only use fallbacks you configured
navig ai fallback history -n 50            # recent switches and recoveries
```

Policy lives under `llm.fallback` (`auto`, `prefer`, `never_from`, `never_to`);
`never_to` takes a provider (`groq`) or an exact `provider:model`. The same page is in
the Mini App: Settings → AI & Models → Fallbacks. `navig doctor` shows a ⚠ "Model
fallback" row while any primary is being stood in for.

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
navig ask "Explain this code" --model airllm:deepseek-ai/deepseek-coder-33b-instruct

# Add to fallback chain
# ~/.navig/config.yaml
ai_model_preference:
  - openai:gpt-4o-mini
  - airllm:meta-llama/Llama-3.3-70B-Instruct  # Local fallback
```

See AirLLM Documentation (`providers/airllm.md` — not in this tree) for full details.

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

See full NLP guide: `TELEGRAM_NLP_GUIDE.md` (not in this tree)

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
| CLI      | Active    | `navig ask` for one-shot queries (the `ai ask` form is deprecated) |
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
| `/doctor` | Engine health check, with Heal / Re-check / Restart buttons |
| `/os` | Is NAVIG Desktop running, and its paired devices |
| `/restart` | Restart the NAVIG engine (asks first via the /doctor button; on Windows uses the restart task — run `navig service install` once) |
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

### 22.7b Discord — the NAVIG bot and server manager (`navig discord`)

Manage Discord servers from the terminal, and run the **NAVIG Discord bot** that
server admins add to their own servers to moderate from inside Discord — the
Discord sibling of the Telegram bot. Engine: `navig/discord/` (tokens in the vault,
one moderation policy shared by the CLI, the deck and the bot).

**Set up the bot (once):**

```bash
navig discord setup --owner <your-discord-user-id>   # token → vault, installs discord.py,
                                                     # Server Members intent, owners, invite link
navig service restart                                # the daemon starts the bot
```

Two identities, picked per command with `--as auto|bot|account`:

| Identity | Set up | Can do |
|----------|--------|--------|
| **bot** (official Bot API, ToS-safe) | `navig discord setup` | everything below, in servers the bot was added to |
| **account** (your user token, opt-in) | `navig discord login` | list, read and **leave** your own servers |

⚠ Automating a user account is against Discord's Terms of Service, so account mode
is opt-in, behind a warning, and deliberately narrow: it never lists other people
and never joins by API. `navig discord join` previews an invite and opens it in
your browser, where you accept it yourself.

```bash
navig discord status                      # both identities, live-checked (exit 1 if a token is rejected)
navig discord servers                     # every server: members, online, owner/admin, where the bot is
navig discord server "My Lab"             # details (name, id or unique prefix)
navig discord channels "My Lab"           # grouped by category
navig discord roles "My Lab"
navig discord members "My Lab" -s alex    # bot only — needs the Server Members intent (setup turns it on)
navig discord leave "Some Server"         # you leave (asks; --yes to skip) · --as bot removes the bot
navig discord join discord.gg/python      # preview + open in browser
navig discord channel create "My Lab" deploys --category Ops --topic "ship it"
navig discord channel edit <channel-id> --name alerts --slowmode 10
navig discord channel delete <channel-id> --yes
navig discord mod kick "My Lab" <user-id> -r "spam" --yes
navig discord mod ban "My Lab" <user-id> --delete 1d -r "raid"
navig discord mod timeout "My Lab" <user-id> 2h -r "cool down"   # max 28d
navig discord mod unban "My Lab" <user-id>
navig discord mod bans "My Lab"
navig discord mod log                     # NAVIG's own record of every action
navig discord role create "My Lab" Helpers -p kick_members,manage_messages --color "#33aa55"
navig discord role edit "My Lab" Helpers --name Helper --hoist
navig discord role add "My Lab" <user-id> Helpers
navig discord role delete "My Lab" Helpers --yes
navig discord bot profile --name NAVIG --avatar ./logo.png --about "Server ops by NAVIG"
navig discord settings                    # every discord.* key and what it does
```

Every list command takes `--json`. Every write lands in the server's audit log as
`<who> via NAVIG: <reason>` and in NAVIG's own log (`navig discord mod log`).

**Inside Discord** (slash commands, replies only visible to you):
`/kick` `/ban` `/unban` `/timeout` `/untimeout` `/role add|remove|create|delete`
for members holding the matching Discord permission, plus `/navig`, `/status`, `/help`.

**Security model — fail-closed:**

- **The agent** (`/navig`, DMs, @mentions) answers only `discord.owners`. Empty = nobody:
  a bot in a public server is never a remote shell into your machine.
- **Moderation** needs the Discord permission **and** a higher role than the target, for
  the member **and** for the bot. Nobody acts on the server owner, themselves or the bot.
  Administrators can't be timed out.
- **No escalation:** a role can only get permissions its creator already holds; giving a
  role Administrator, Manage Server, Manage Roles, Manage Webhooks, Ban Members or
  Mention Everyone needs the server owner or an Administrator.
- Kick / ban / role delete ask for a **Confirm button only the asker can press** (30 s),
  and the rules are re-checked when it is pressed. Each member gets
  `discord.moderation.cooldown` actions per minute. Replies never ping anyone.
- The bot asks for **no privileged intents** and the invite requests only the permissions
  it uses (never Administrator). `navig discord bot profile` warns when the bot is
  **Public** (anyone could add it) — turn that off in the Developer Portal.
- `discord.allowed_guilds` pins the bot to your servers: it leaves any other it is added to.

**Settings** (`navig config set discord.<key> <value>`):

| Key | Default | Meaning |
|-----|---------|---------|
| `discord.enabled` | false | the daemon starts the bot (`setup` turns it on) |
| `discord.owners` | [] | user ids allowed to use `/navig` — empty = nobody |
| `discord.allowed_guilds` | [] | if set, the bot works only there and leaves others |
| `discord.agent.enabled` | true | owners may use `/navig` |
| `discord.moderation.enabled` | true | register the moderation commands |
| `discord.moderation.confirm` | true | confirm button for kick / ban / role delete |
| `discord.moderation.cooldown` | 5 | moderation commands per member per minute |
| `discord.sync` | guild | `guild` = commands appear instantly per server · `global` = everywhere, up to an hour |

Secrets: `discord_bot_token` (shared with `navig init --messaging` and the messaging
adapter) and `discord_user_token` (account mode) live in the vault only. The bot needs
the optional `discord.py` library (`pip install 'navig[discord]'`, or let `setup` install it).

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

### 22.9 Connector catalog — every service, ranked into batches

`navig connector list` shows the connectors that run today. `navig connector catalog` shows the
wider map: ~4,800 services worth connecting, each with a category, home country, tier
(`navig` · `official` · `desktop` · `community`) and status (`planned` → `verified` → `live`),
ranked into batches of 25, most important first.

```bash
navig connector catalog --summary              # totals + the next batch to resolve
navig connector catalog --batch 1              # the 25 most important services
navig connector catalog --category developer   # one category (see --summary for ids)
navig connector catalog --country FR           # or --region europe
navig connector catalog --status live --json   # what is usable right now, for scripts
```

The deck serves the same data at `GET /api/deck/connectors/catalog` (same filters as query
parameters, plus `limit`/`offset`). The data and the batch workflow: `docs/connectors-catalog.md`.

### 22.10 Capability index — one row per thing NAVIG can do (`navig store capabilities`)

`navig store list` shows every *item* (plugin, module, connector, MCP server, skill). The
capability index folds those rows into logical **capabilities**: Perplexity appears ONCE,
with its desktop app (CDP), its Sonar API connector and its skills listed as backends —
each with its state, auth requirement (field names, never values), CLI commands and MCP
tools.

```bash
navig store capabilities                 # every capability, one table
navig store capabilities perplexity      # one capability in full detail
navig store capabilities --multi         # only capabilities with 2+ backends
navig store capabilities --json          # machine-readable (also lists reader errors)
navig store capabilities --no-mcp        # skip MCP tool derivation (~1s faster)
```

State is the best available across backends (`wired` if any backend is up) — but a broken
backend stays visible under `health`, never swallowed. Same-index HTTP surface for NAVIG
OS: `GET /api/deck/store?capabilities=1` (additive; the plain endpoint is unchanged).
Implementation: `core/navig/hub/capabilities.py` — a pure fold over the Store, no second
registry and no cache.

### NAVIG in Chrome — the Dock side panel

The NAVIG Dock extension (`apps/dock/extension`, load `dist/` unpacked) puts NAVIG next to any
page: **Ask NAVIG** on the bar, `Ctrl+Shift+E`, the palette (type anything) or right-click →
*Ask NAVIG about this page*. **Ask** answers about the page; **Act** lets NAVIG click, type,
choose and navigate with real input while a glowing edge, cursor and Stop pill show it is in
control (`Esc` stops). It uses the same AI connections as NAVIG Desktop (`navig connect …`, the
panel's *Connect a provider* sheet), so keys stay in the vault. Browser pages (settings, the
Web Store) are off limits, and password and card values are never sent to the model.

The panel calls `POST /api/deck/agent/browser/turn` (server-sent events: `route`, `delta`,
`actions`, `done`, `error`), local requests only. Each turn takes the chat so far plus the page
outline; the extension runs the actions it returns and sends the results back on the next turn.

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

**"Remediate health issues" — what runs without asking you**

When `missions.autonomous_enabled` is on, a heartbeat finding can raise a
`Remediate health issues` mission. Two rules keep that from ever paging you:

- **Provider and model findings never raise a mission.** An expired API key, a retired
  model, an endpoint returning 503, a rate limit — no shell agent can fix those, the LLM
  router already falls back at request time, and the heartbeat alert has told you (once
  per issue set, deduped for 6 hours). `navig mode doctor` shows the fix.
- **A mission that does run is quiet by construction.** It runs without a prompt
  (`missions.remediate.autonomy: auto` — set `approval` to be asked first), and inside it
  the agent may only run **read-only diagnostics** on its own (`navig doctor`, `git
  status`, `tail`, `ping`, …); every such command is audited as auto-approved. Anything
  that would change state is refused with a reason the agent reads, so it *reports* the
  command it wanted instead of asking you — the result reaches you as a `mission_complete`
  notification. To let missions run a specific write on their own:

```yaml
missions:
  allowed_commands:               # glob patterns, matched against the whole command
    - "navig mode set *"
    - "navig service restart"
```

When you *are* asked (interactive chat, a `dangerous` tool), the prompt shows the command
text, not just the tool name.

Some issues re-appear on every heartbeat, and a daemon restart triggers a check within
10-60 seconds. Suppression keeps the actionable ones from becoming a stream of missions:

```yaml
missions:
  remediate_backoff_base_secs: 3600    # 1h — first wait before the SAME issues re-ask
  remediate_backoff_max_secs: 259200   # 72h — ceiling on the doubling (0 = uncapped)
  remediate_min_interval_secs: 1800    # 30m — floor between ANY two remediate missions
```

Identical issues back off **exponentially** — 1h, 2h, 4h, 8h … up to the ceiling — so a
problem is reported promptly and then goes quiet if nothing changes. A genuinely new
issue set starts again at the base, so nothing is ever swallowed. The ceiling means a
long-standing issue still resurfaces every few days rather than never again.

The floor is a backstop for issues whose text wobbles between checks (a transient `503`
from a provider reads as a different issue each time, and so as a new issue set) — it
delays a new issue by at most one window, it never drops one.

Every suppressed prompt is logged with the prior mission id, how many times it has been
raised, and the next window, so quiet is always explainable.

If the heartbeat *alerts* keep coming, run `navig mode doctor` — a dead model or an expired
provider key is the usual cause, and only a new key or a new model clears it.

**Reading `navig mode doctor`**

It probes every configured route with a real 1-token call: each mode's primary, its
fallback, and the hybrid routing tiers (`--modes-only` skips the last two). A model
reached by several routes is probed once. Statuses, and what each one asks of you:

| Status | Meaning | Action |
|---|---|---|
| `● live` | Answered | none |
| `✗ DEAD` | Provider retired it (410) or the id is gone (404) | `navig mode set <mode> --provider <p> --model <m>` (tiers: `navig mode route set`) |
| `⚠ auth` | 401/403 — credential invalid or lapsed | `navig connect login <template>` or `navig connect add` |
| `○ no key` | Nothing configured for that provider | `navig connect add` |
| `✗ unreachable` | Refused / timed out **twice** | Check the endpoint; for ollama, that it is running |
| `↻ busy` | 429/5xx **twice** — the provider, not your config | Nothing; re-run later |

Two rules keep this honest. **A destructive verdict is earned twice**: a bare 404, a
timeout and a 5xx are each confirmed by a second call before being believed, because
providers have been measured answering 404 once and 200 five times running for the same
model. A real failure survives the retry. Only **410 is believed on sight** — it carries
an explicit end-of-life message and has been stable every time it was checked.

And **a dead fallback is reported but does not fail the command**: it breaks nothing
today, and a machine that simply does not run ollama would otherwise be permanently red.
A dead primary or tier is live breakage and does fail (exit 1), which is what makes the
command usable as a CI gate. `↻ busy` never fails it either — a red build over someone
else's rate limit teaches people to ignore the gate.

The heartbeat runs the same probe once a day over primaries and tiers (not fallbacks),
so a model a provider retires is reported without anyone running a command — that is
how `minimaxai/minimax-m3` was caught the day after it was chosen.

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

# A local program, run directly (no shell, no AI), with room to finish
navig cron add "Nightly report" "30 3 * * *" "node C:/jobs/nightly-report.js" --exec --timeout 3600
```

A job is a `navig …` command, a local program (`--exec`), or otherwise an instruction for the AI.
Every job stops at its timeout (default 300 s; `--timeout SECONDS`, up to a day), taking any
processes it started with it.

**Not for one-off reminders.** Cron has no year, so a schedule pinned to one date
(`0 11 9 10 *` — 9 October, 11:00) fires on that date *every* year. `navig cron add` refuses
such a schedule unless you pass `--yearly`. For a one-off reminder use a task:
`navig todo add "<text>" --at "2026-10-12 09:00"` — it pings once, shows in `navig todo`,
and ticking it off cancels the ping.

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

#### Standing goals (`navig goal`)

A standing goal is an objective NAVIG keeps working on, on a schedule, until it is met.
You state it once; each scheduled **cycle** is one unattended mission that checks where
things stand, reports, and ends with a device-signed receipt. Progress arrives as a
"Goal progress" notification (deck + Telegram by default).

```bash
navig goal add "Keep the backup drive under 80% full" --every 6h
navig goal list                 # what's in progress (--all includes finished ones)
navig goal show <id>            # objective, state, latest report, receipt
navig goal run <id>             # run one cycle now and print the report
navig goal pause <id>           # stop scheduling; history kept
navig goal resume <id>
navig goal remove <id> --yes
navig block verify-receipt <receipt-id>   # prove a cycle's receipt is untampered
```

- **`--every`** takes `30m`, `6h`, `1d`, or any cron schedule format above.
- **What a cycle may do:** only read-only work (`--grant readonly`, the only grant today).
  A cycle that needs a change tells you what to do instead of doing it; cycles never ask
  for approval, so a goal can't flood you with prompts.
- **How it ends:** the agent closes each cycle with `STATUS: DONE` or `STATUS: CONTINUE`.
  The goal finishes on DONE, stops at `--max-cycles` (default 24), or stops after three
  failed cycles in a row. Its schedule is removed automatically when it ends.
- Different from `navig agent goal`, which plans one request into subtasks once.

#### Session goals (`navig goal session`)

A session goal is an objective **one NAVIG Desktop session** works until done — no schedule,
no missions, no receipts: the session itself keeps going. When the session goes idle and
a goal is armed, NAVIG Desktop appends a `<goal_round>` message (the objective quoted, `Round:
N/max`) telling the agent to keep working in this same session; the loop stops when the
agent marks the goal complete, reports a blocker, or the round budget runs out (then the
goal is reported `blocked: round-limit` and the session keeps running — it never dies
with the goal).

```bash
navig goal session set <session> "Ship the release notes"   # arm it (unique id prefix or exact name works)
navig goal session show <session> --json   # objective, phase, rounds, loop arming
navig goal session list                    # sessions that have a goal
navig goal session pause <session>         # stop injecting rounds; history kept
navig goal session resume <session>        # re-arm (a restarted OS loads disarmed — resume is explicit)
navig goal session clear <session> --yes   # drop the goal; injected rounds stay in chat
navig goal session complete <session> -e "…"  # operator closes it WITH evidence (human override)
```

- **Requires NAVIG Desktop to be running** (the CLI talks to the announced OS server); it
  fails with an honest message otherwise.
- **Config** (`navig config set …`): `goal.enabled` (default `true`), `goal.max_rounds`
  (default 12 — the per-goal round budget), `goal.blocked_after_rounds` (default 3 —
  consecutive identical blocker reports before `blocked` is accepted), `goal.tool_policy`
  (`model` = the agent may complete/block the goal via its `get_goal` /
  `set_session_goal` tools; `human` = read-only for the agent).
- **Completion is evidence-gated:** `set_session_goal action: "complete"` must carry an
  `evidence` string (commands run and their output, files changed and inspected, tests
  observed passing). A completion without evidence is refused; accepted evidence is
  recorded on the goal and shown in the UI strip, in `navig goal session show --json`
  (`completionNote`) and from `get_goal`.
- **Resolving a blocked goal:** a goal in `blocked` is terminal — the round budget
  (`round-limit`) is spent or the model's blocker streak closed it. Read the reason
  (`navig goal session show <session>` → `Blocked`), fix the underlying condition, then
  `navig goal session clear <session> --yes` and `set` the objective again (with a fresh
  budget if you need one). `resume` refuses a spent budget on purpose.
- In the NAVIG Desktop UI the same goal lives on a composer strip (set / edit / pause /
  resume / clear) and each injected round shows as a collapsed **Context · Goal · Round
  N** chip; `/goal <objective>` in the chat input is a shortcut for `set`.
- Different from standing goals (`navig goal add`) above: those are daemon-scheduled
  missions; a session goal is the live session working round after round until the
  objective is met.

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

#### Text NAVIG by SMS (two-way)

With a Twilio number configured (`adapters.sms`), texts to that number land in the
deck's inbox. To have NAVIG **answer** texts from your own phone:

```bash
navig vault set twilio_auth_token <auth-token>   # proves a text came from Twilio
navig config set sms.allowed_numbers "+15550100100"   # comma-separate several
```

- Only numbers on `sms.allowed_numbers` get answers, and only when the request carries a
  valid Twilio signature. Everyone else's texts still just land in the inbox.
- The answer is texted back (up to three messages; longer answers point you to the deck).
- A text that starts with `navig …` is treated as a question for the assistant, never run
  directly as a command; anything risky still asks for approval on the deck or Telegram.
- With Lighthouse, point Twilio at the edge URL `navig lighthouse status` prints
  (`<edge>/sms/<id>`); signatures are checked against that URL.

### 23.6 Full Autonomous Setup

Complete setup for 24/7 autonomous operation:

```bash
# 1. Configure hosts
navig host add production
navig host add staging

# 2. Notifications need no setup — heartbeat findings are delivered as a
#    "system_alert", which fans out to the Deck and Telegram automatically.
#    (`notifications.recipient` is no longer read: it was a second copy of
#    "who is the operator", which the notify router already resolves.)

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

**Calendar and email alerts.** Once a calendar (`calendar.provider: google`) or a mailbox
(`email.provider`, or an agent email account) is configured, the gateway watches them on
its own, every 60 seconds:

- an event starting within the next two hours → a **Reminders** notification (deck +
  Telegram by default);
- a new unread email → a **New email** notification (deck only by default — turn Telegram
  on for it in Settings → Notifications).

Each event or email is announced once, even across restarts. To keep the watcher off:
`navig config set proactive.enabled false`.

**Give a notification type its own Telegram chat (and sound).** Telegram sets the sound per
chat on your phone, so a type routed to its own channel can ring differently — sales with a
cash register, reminders with a chime. Add the bot to the channel as an admin with "Post
messages", then:

```bash
navig notify route set sale 4466964318        # the id the Telegram app shows, -100… or @name
navig notify route set reminder 4425457788
navig notify route test sale                  # posts a test there — set the chat's sound now
navig notify route sound sales_digest off     # off = silent (the default for the daily summary)
navig notify route list                       # where each type lands, and whether it rings
navig notify route clear sale                 # back to your DM
```

Or in the Mini App: Notifications → **Telegram chats**. If a routed chat stops accepting
posts, the message comes to your DM with a note saying why, and the deck feed records it. A
post in the reminder channel (`dentist friday 9am #health`) becomes a task with a reminder;
the bot replies silently with what it scheduled.

**Alerts that must wake you: Pushover.** Telegram can't ring on silent or repeat. Pushover's
emergency priority does both — it re-alerts every minute until you tap Acknowledge. Create an
app at pushover.net/apps/build (name it NAVIG), copy your User Key from the dashboard, then:

```bash
navig notify pushover setup              # prompts for both keys, validates, stores in the vault
navig notify pushover test --emergency   # rings on silent; tap Acknowledge to stop it
navig notify pushover emergency critical # only critical alerts repeat (default: high + critical)
navig notify pushover status
```

Setup turns Pushover on for security alerts and a failing sales check only; add others in
Mini App → Notifications → "What goes where". Keep it to a few types — an alarm that rings
often stops being one. On iPhone, allow **Critical Alerts** in the Pushover app's settings.

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

The NAVIG Daemon is a process supervisor that keeps subsystems (Telegram bot, gateway) running permanently with auto-restart, health monitoring, and structured logging. Cron scheduling is not a subsystem of the supervisor — it runs inside the gateway.

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
navig service restart         # Stop + start (on Windows: relaunched THROUGH the scheduled
                              # task when installed, so the daemon keeps a living parent —
                              # a detached spawn is orphan-shaped and process sweeps kill it)
navig service status          # Daemon + children (pids verified NOW, not the boot snapshot),
                              # Since/uptime, and the heartbeat — STALE = a wedged supervisor
navig service logs            # Last 50 lines
navig service logs -f         # Follow log output
navig service logs -n 200     # Last 200 lines
navig service pids            # Every navig-owned process tree — what a sweeper must spare
navig service pids --plain    # One pid per line (an exclusion list)
```

#### Process sweepers: what to spare

To a cleanup script a navig daemon and a leaked helper look the same — python, parent
gone — and an hourly "kill orphaned dev processes" sweep will take the daemon down at
the next :01 (it did, twice, on 2026-09-14). `navig doctor` → **Daemon parent** warns when
the running daemon is orphan-shaped; `navig service start|restart` relaunch through the
scheduled task on Windows so the parent is the service host. For the sweeper itself:

```powershell
# Spare everything navig owns (verified: a recycled pid is never listed)
$spare = navig service pids --plain
```

A sweeper that must not depend on navig being runnable may read the pid files directly —
each names the ROOT of a tree; spare it **and its descendants**:

| Pid file (under `~/.navig`)  | Tree                                                   |
|------------------------------|--------------------------------------------------------|
| `daemon/supervisor.pid`      | the service supervisor → gateway + telegram worker     |
| `gateway.pid`                | a standalone `navig gateway start`                     |
| `agent/agent.pid`            | a standalone `navig agent start`                       |
| `worker.pid`                 | a standalone bot worker (`navig bot start --background` with no service installed) |

A pid file is a *number*, not proof: trust it only if the process is **older than the
file** (`navig service pids` does this check; a hand-rolled reader should too).

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
navig service install --gateway

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
  "health_port": 0
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `telegram_bot` | `true` | Run the Telegram bot |
| `gateway` | `false` | Run the gateway WebSocket server |
| `gateway_port` | `8765` | Gateway listen port |
| `health_port` | `0` | TCP health-check port (0 = disabled) |
| `bot_script` | unset | Optional explicit bot script override (legacy); default uses `navig.daemon.telegram_worker` |

Cron scheduling is not a daemon child: it runs **inside the gateway**
(`NavigGateway._start_cron`). The legacy `scheduler` key (and the
`--scheduler` install flag) is deprecated — still accepted for compatibility,
read by nothing. A stale `"scheduler": true` in an existing `config.json` is
ignored.

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

## 23.4 Prompt Library (`navig prompts`)

Your own reusable prompts in one library: import a folder, find the right one, fill in its
placeholders, copy it, or get it on Telegram. The same library backs the CLI, NAVIG Desktop
(**Knowledge → Prompts**) and the bot's `/prompt` command. Stored in
`<data_dir>/prompts.db` (`NAVIG_DATA_DIR`); after import it is the source of truth.

```bash
navig prompts import C:\prompts --dry-run   # preview: folders become domain/category
navig prompts import C:\prompts             # safe to re-run — your edits are never overwritten
navig prompts categories                    # domains, categories, workflows, counts
navig prompts list --domain dev --category coding
navig prompts list --collection app-quality # a workflow's steps, in order
navig prompts search poster brand           # full-text: title, when-to-use, body, tags
navig prompts show debug-root-cause         # --raw prints only the body
navig prompts use marketing-poster --var HEADLINE="Ship faster" --ask   # fill + copy
navig prompts new "Weekly Review" -d human -c productivity --use-when "Sunday reset"
navig prompts edit <id>                     # body in $EDITOR
navig prompts fav <id>                      # favourites lead NAVIG Desktop and /prompt
navig prompts remove <id>                   # archives; --purge deletes; restore <id> undoes
navig prompts backup C:\prompt-backup       # every prompt back out as <id>.md
```

**Folder convention:** `domain/category/name.md` with a `# Title`. A `**Use when:**` line
becomes the "when to launch" text (else the folder README's `[name](file.md) — text` row,
else the first paragraph). `workflows/<name>/NN-step.md` (also `bootstrap|polish|runbooks`)
become an ordered workflow. Placeholders `[UPPER CASE]`, `{{NAME}}` and `‹name›` become
fill-in fields. A file whose prompt sits in one fenced block uses that block as the body.

**Telegram:** `/prompt debug root cause` sends the prompt as a rich message (one-tap copy
block, native "Show More" for long text, `.md` file when it will not fit), with ☆ Favorite ·
📄 .md file · ▶ Next step buttons. `/prompt` alone shows favourites. Free text works too —
"send me the marketing poster prompt" — but only when the library has a match; anything
else ("write me a prompt for…") still goes to the AI. Switch it off with `/extensions`
(Prompt library). The agent can also search the library to recommend which prompt to use.

## 23.4.1 Universal Presets (`navig presets`)

One reusable preset system for every AI tool: **Text**, **Image**, **Video**, **Audio
generation** and **Audio enhancement**. A preset is a named, versioned configuration:
reusable instructions, typed settings for its tool (an image preset's aspect, lighting
and negative constraints; an enhancement preset's mode and loudness target), optional
variables and, for text, references to approved context files. NAVIG Desktop (Studio's
preset picker), `/api/deck/presets` and this command read the same catalog.

```bash
navig presets list --tool image              # ★ favorite · ● the tool's default
navig presets show image/style/cinematic-realism
navig presets resolve text/context/my-book text/style/dark-literary text/format/book-chapter \
  --tool text --task "Write the scene where she enters the underground archive."   # what would be sent
navig presets resolve image/style/schema-red-cyberpunk --tool image --task "A courier on a bridge" \
  --provider openai_gpt_image                # shows what that provider actually receives
navig presets duplicate text/context/book-template --name "Book — SCHEMA Lore"
navig presets edit user/book-schema-lore -i "World rules: …" --set language=English
navig presets new --name "My Look" --tool image --set aspect=4:3 --set mood="quiet dusk"
navig presets default image user/my-look     # the tool starts with it
navig presets fav <id> · rename <id> "New name" · rm <id> · reset <builtin-id>
navig presets export <id> -o look.json       # secrets + local paths removed; files not included
navig presets import look.json               # stays "unreviewed" until: navig presets trust <id>
navig text gen "Write the next scene." --preset user/book-schema-lore --preset text/format/book-chapter
```

**How presets combine.** Each preset has a *layer*. Several presets apply in a fixed
order no matter how you list them: **context** (what the work is about) → **style** →
**format** → **config** → your one-off `--set` overrides → **your request**, which always
wins. Later layers win for settings; instructions accumulate in that order. Text presets
add instructions *around* your request; image, video and audio presets add a separate
**style clause after** your prompt, so a style never rewrites the subject. Settings a
provider can't take are folded into the prompt or dropped, and every such change is listed.

**Where presets live.** Builtin presets ship with NAVIG; plugins ship their own (navig-audio
ships the enhancement modes). Your own presets, edits and imports are stored in the
prompt library database (`<data_dir>/prompts.db`, schema v2; the first upgrade writes
`prompts.v1-backup.db` next to it). You can also drop `*.preset.yaml` files in
`<store>/presets/`, a project's `.navig/presets/` or the active space's `.navig/presets/`.
Editing a builtin saves your own override; `navig presets reset <id>` brings the original back.

**Context references** are paths relative to the active space (or project) root, such
as `lore/` or `characters/ana.md`. They are read when a text preset is used, within a size
budget. Each one is reported as included, truncated or missing, and the model is told
it only has the documents that were actually read.

**Imported presets are untrusted.** Lines that try to steer tools, permissions, the
system prompt or secrets are removed, and the rest is quoted as reference guidance until
you review it and run `navig presets trust <id>`.

## 23.4.2 Chat Library (`navig chats`)

Your AI conversations on this computer, searchable down to the message. Plugin
`navig-chats`. Stored in `<data_dir>/chats.db` (`NAVIG_CHATS_DB` overrides it), separate
from the agent's memory.

```bash
navig chats import ~/Downloads/chatgpt-export.zip   # ChatGPT → Settings → Data controls → Export data
navig chats list --collection recent --sort visited
navig chats search '"borrow checker" -python'        # phrases, -exclude, prefix*, a OR b
navig chats search deploy --role assistant --code    # only answers that contain code
navig chats show 67f1c2d3                            # --all-branches shows regenerated answers
navig chats folders add Work                         # NAVIG folders: local, never ChatGPT Projects
navig chats move 67f1c2d3 --to <folder-id>           # --keep adds without leaving other folders
navig chats export --all -o chats.zip                # .md/.txt/.html/.json, or .zip with a manifest
navig chats restore chats.zip                        # back into the library (not into ChatGPT)
navig chats stats                                    # counts + search-index health; reindex rebuilds it
```

**In the browser (NAVIG Dock 1.8):** on chatgpt.com, the 💬 chat button in the bar shows
whether this chat is in your library and which folder it is in. It also saves, refreshes,
renames and moves the chat. The palette (**Ctrl+Shift+P**; Ctrl+K stays ChatGPT's own
search) gets a **Chats** section:

- **Enter** opens the match in ChatGPT, at the exact message.
- **Shift+Enter** opens it in a new tab.
- **Alt+Enter** opens the library copy.

The full **Chat Library** page holds the folder tree, search, a reader and import/export.
Open it with `/chats` or with the Lens's *Open Library* button.

**Completeness** is labelled:

| Label | Meaning |
|---|---|
| `complete` | Imported from your export |
| `rendered` | Saved from the page: the branch you saw |
| `partial` | Some messages are missing |
| `url_only` | Only the link is saved |

**Privacy:** nothing is captured until you save a chat, unless you turn on *Save every
chat I open*. NAVIG reads no session tokens and calls no ChatGPT endpoints. The
`/api/deck/chats/*` routes answer only this computer.

**Sharing with NAVIG Brain** is off for every chat until you choose a level, on a folder
(subfolders inherit) or on one chat (its own choice wins):

| Level | Who can read the chats |
|---|---|
| `off` | Nobody. This is the default. |
| `local` | `navig recall` on this computer. Never sent anywhere. |
| `agent` | Also the project context pack that agents get. That pack is sent to your configured AI provider. |

A chat filed in several folders follows the most private one. Map a folder to a NAVIG
project so its chats only appear in that project.

```bash
navig chats share --folder <id> --level local --project navig   # recall reads it (this computer only)
navig chats share 67f1c2d3 --level agent                        # may reach your AI provider in agent context
navig chats shared                                             # what is shared, at which level
navig recall ingest --source chats                             # index the shared chats
navig chats handoff --folder <id> --goal "finish the cache" -o next.md   # Continue this work
navig chats handoff 67f1c2d3 --into C:\projects\shop          # → <space>/.navig/plans/briefs/
```

Withdrawing a share (level `off`) makes the next `recall ingest` forget everything Recall derived from
those chats: their archived turns, their index rows and any record that only they supported.

**Continue this work** (`handoff`, or the Library's *Continue…* button) builds a Markdown
context pack. It contains:

- the sources, with links and how complete each copy is;
- decisions you confirmed, and the superseded ones, so an abandoned choice isn't revived;
- open questions and tasks, and proposals nobody confirmed;
- code and references, and where each chat left off;
- a **Missing or incomplete context** list.

Every item links to its exact message. Decisions are read only from explicit markers
(`Decision: …`, `Decision (subject): …`, `TODO: …`, `- [ ]`, `Open question: …`, in English,
French or Russian) and from your bookmarks. The assistant's suggestions stay *proposed*.

## 23.4.3 Web Notes (`navig notes`)

The sticky notes you keep on web pages with the NAVIG Dock (on one page, a whole site, or
everywhere), on this computer. Plugin `navig-notes`. Stored in `<data_dir>/notes.db`
(`NAVIG_NOTES_DB` overrides it). Nothing is uploaded.

```bash
navig notes list                                     # --site github.com · --scope page|site|global · --tag work
navig notes search retry path                        # every word must match; "quote a phrase"
navig notes show 3f2a                                # an id prefix is enough
navig notes add "call the bank"                      # an Everywhere note
navig notes add "check CI" --url https://github.com/org/repo/issues/12   # a note on that page
navig notes rm 3f2a                                  # deleted everywhere; the Dock moves it to Trash
navig notes export notes.json                        # re-importable in the Dock's Notes Library (or .md)
navig notes status                                   # counts, the store, the last Dock sync
navig notes share push --space <name-or-path>        # every note → <space>/.navig/notes/*.md
navig notes share pull --space <name-or-path>        # back again — ids kept, newer revision wins
navig notes share status --space <name-or-path>      # which side is ahead; orphans are never deleted
```

**Sync.** The browser is where notes are written, and it works without NAVIG. With NAVIG
running, the Dock syncs a few seconds after any change, every 5 minutes and when Chrome
starts: it pushes its edits and deletions, and pulls what changed here (a note added with
`navig notes add`, by the agent, from a Space). Every note carries a revision; the higher one
wins, and an older copy never overwrites a newer one. The `/api/deck/notes/*` routes answer
only this computer.

**Sharing with a Space** (`share push|pull|status`): a Space folder is how a team shares
things, so shared notes are plain `.navig/notes/<id>.md` files — frontmatter carries id,
revision, scope, geometry, tags and the sharing level; the body travels byte for-byte; only
live notes are written and an orphan file is reported, never deleted. Default space: `--space`
(name or path), else the active Space, else the nearest `.navig` above you (never the global
config dir). Reminders are personal and do not travel; a pulled note shows up in the Dock as
*from NAVIG*.

**What NAVIG may do with them** is the Notes Library's Settings → *NAVIG on this computer*:

| Setting | What happens |
|---|---|
| Keep a copy in NAVIG (default) | Synced here; `navig notes` and `navig recall` find them. Never sent to an AI. |
| Copy + the agent may use them | The same, and agent context may include them (sent to your AI provider). |
| Off | Nothing leaves the browser. |

`navig recall ingest --source notes` reads synced notes: your words as yours, lines you
quoted from a page (`> …`) as page captures, and checkboxes (`- [ ]` / `- [x]`) as your open /
done tasks. A note deleted in the browser is forgotten at the next ingest.

**VS Code (NAVIG Prompts extension 0.3.0+):** run **NAVIG Prompts: Use a Prompt from the NAVIG
Library…** from the Command Palette — a searchable list of the same library (favourites, then
most used), one input box per blank, then insert into the NAVIG Prompts chat, copy, or open in
an editor. In chat, `@navig-prompts /prompts debug root cause` lists the best matches with a
**Use** button each. It reads the running daemon (`/api/deck/prompts`), so the daemon must be up;
the extension's own editor packs (Open Prompt Library) are unchanged.

## 23.4.4 Review debt (`navig github reviews`)

Review comments outlive their PRs: a merged PR keeps its unresolved Codex/Copilot threads
forever. `navig github reviews` pulls every PR's review surfaces (inline threads, review bodies,
conversation comments) into a ledger, one finding per actionable comment, each linked to its
source thread, and records a verified verdict per finding. Plugin `navig-github`. Stored in
`<config_dir>/github/reviews/<owner>__<repo>.json`; only read calls reach GitHub.

```bash
navig github reviews scan --repo navig-run/labs     # first run: every PR, paged; resumes if interrupted
navig github reviews scan                           # later: incremental (catches late reviews on old PRs)
navig github reviews status                         # status × severity, provider, unresolved threads
navig github reviews list --severity P1 --open      # --status · --provider codex|copilot|human · --pr N
navig github reviews show <id-or-url>               # the claim, the verdict, its history
navig github reviews set <id> --status already_fixed --evidence "fixed in 1a2b3c, test_x"
navig github reviews import verdicts.json           # [{id|url, status, evidence, ...}] from a verification run
navig github reviews report --format md --out review-debt.md     # or --format json
navig github reviews check --pr 2035                # exit 1 on a confirmed-open P0/P1 (a landing gate)
```

Statuses: `new` · `triaging` · `reproduced` · `confirmed_open` · `already_fixed` · `superseded` ·
`false_positive` · `not_applicable` · `needs_human_review` · `fix_in_progress` · `fix_pr_open` ·
`verified_fixed` · `blocked` · `reopened`. A closing status needs `--evidence`. GitHub's
*resolved* flag is recorded but is never a verdict, and `check` blocks only on findings proven
present (`confirmed_open`, `reproduced`, `reopened`), not on unverified or refuted ones. A
finding marked fixed that is confirmed again becomes `reopened`.

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

### Organise the vault — names, categories, issuers, tags

```bash
navig vault classify                    # dry-run: what every item is and where it comes from
navig vault classify --apply            # write it (metadata only — no secret is rewritten)
navig vault classify --category social  # one category
navig vault list --category ai          # filter by category …
navig vault list --tag duplicate-of:groq/api_key   # … or by tag
navig vault list --search "digital ghost"           # … or text in name/issuer/website/tags/notes
navig vault edit <id> --category infra --issuer "Hetzner" --website console.hetzner.cloud
navig vault edit <id> --tag ci --untag old --note "used by the nightly deploy"
```

`classify` names the issuer and website from the value's **shape** (`EAA…` → Meta Graph,
`ya29.` → Google OAuth, `gho_` → GitHub, `123456:AA…` → Telegram bot, …) and the storage path,
puts each item in one category (ai · search · social · messaging · infra · payments · devtools
· internal · hosts · logins · sessions · personal), gives an unnamed item a unique
`"<Product> — <field>"` name, and tags byte-identical values `duplicate-of:<other>`. It never
overwrites a name, category, issuer or website you set. Logins, sessions, host passwords and
personal items are classified by path alone — their values are never read. The Hint column
shows at most the first 4 characters of a value. Renames and organisation edits never touch
the secret itself.

### Key custody — status, recovery key, backups, rotation

```bash
navig vault status [--json]          # keyslots, recovery key, items, federation role
navig vault recovery init            # create the OFFLINE recovery key (shown once); migrates a legacy vault
navig vault recovery verify          # prove a key still opens the vault (hidden prompt / stdin)
navig vault backup [--to FILE]       # encrypted backup — opens ONLY with the recovery key
navig vault restore FILE --into DIR  # rebuild into an EMPTY dir on any machine (asks for the key)
navig vault rotate [--yes]           # new master key; every item + keyslot re-wrapped atomically
```

The vault's master key is random and stored only wrapped, once per **keyslot**: `dpapi`
(Windows, this user), `keyfile` (Linux/macOS), `fingerprint` (the legacy key, kept for
compatibility), `passphrase`, and `recovery` (wrapped to a public key whose private half is
printed once and lives off the machine). A vault created before keyslots keeps working
unchanged until `recovery init` migrates it — no item is re-encrypted by that migration.
`navig doctor` shows ⚠ until a recovery key exists.

### `vault://` references, safe input and `vault run`

```bash
navig vault set vault://hosts/cybesis-vps/ssh          # hidden prompt (or --stdin / --from-env / --from-file)
navig vault set vault://ci/token --stdin --expect-version 3   # fails if someone changed it meanwhile
navig vault get vault://hosts/cybesis-vps/ssh          # masked; --reveal / --raw to print
navig vault run -e PGPASSWORD=vault://hosts/db1/db -- psql -h db1 -U app
navig vault run -f KEY=vault://deploy/key -- ssh -i "$KEY" host     # 0600 temp file, deleted on exit
navig vault run --host cybesis-vps -e TOKEN=vault://ci/token -- ./deploy.sh   # over SSH stdin
```

`vault://hosts/<h>/ssh` · `/db` · `/db-root` name a host's `ssh_password`, `database.password`
and `database.root_password`; any other path is a vault label; `#field` picks a JSON key and
`?v=N` an earlier version. A value given positionally to `vault set` still works but warns:
it lands in shell history. `vault run` never puts a secret on a command line.

### Secret Resolution — references in config files

Keep secrets in the vault and only *references* in your config files:

```dotenv
OPENAI_API_KEY=vault://openai/api_key
DATABASE_URL=postgres://app:${VAULT:schema/db_password}@localhost/app
NODE_ENV=development
PORT=3000
```

```bash
navig vault run --env-file .env -- npm run dev      # the app gets real env vars; .env is never written
navig vault run --env-file .env --env-file .env.local -- python app.py   # later files win
navig vault resolve vault://openai/api_key          # does it resolve? metadata only, never the value
navig vault resolve --labels --json                 # the labels a reference can name here (no values)
navig vault doctor . -r                             # read-only check of a project (--json, --strict for CI)
navig vault adopt .env --dry-run                    # plan: which plaintext secrets would move
navig vault adopt . -r                              # move them (asks first; --yes without a terminal)
navig vault inject -t config.yaml -- ./server --config {file}   # for programs that need a FILE
navig vault inject -t config.yaml -o /run/app/config.yaml       # owner-only output, refused in an un-ignored git tree
```

**Reference forms.** A whole value `vault://<label>[#field][?v=N]` works with any quoting.
`${VAULT:<label>…}` (also spelled `${BLACKBOX:…}` / `${CRED:…}`) works inside unquoted or
double-quoted values; single quotes are literal. `${HOME}`, `${PORT}` and other placeholders are
ordinary dotenv interpolation (`${NAME:-default}` supported), never vault lookups. Expansion runs
once: a resolved secret is never scanned again, so a reference cannot loop.
`vault://<provider>/api_key` (or `token` / `password` / `secret`) also finds the field of a
credential stored with `navig vault set <provider>`.

**Where a value comes from.** This machine's vault first; a label it does not hold is asked of the
vault authority it joined (grants, TTL cache and revocation apply). A local copy always wins — if
it is older than the authority's, `vault doctor` reports `LOCAL_SHADOW`.

**`vault run` precedence** (highest first): `-e` / `-f` flags → file values holding a reference
(they always replace an inherited variable, with a warning naming the key) → the file's ordinary
values (they replace inherited ones unless `--no-override`) → the inherited environment. On
Windows keys match case-insensitively. The child runs in the directory you called `navig` from.

**Fail closed.** Every reference resolves before anything starts. A missing, malformed, denied or
locked reference lists every failure and starts nothing — never an empty value, never the literal
`vault://` text. Exit codes: `2` usage / parse / malformed reference, `1` resolution failure,
otherwise the child's own code (a POSIX death by signal N → 128+N). `resolve`/`doctor` use 0 / 1 / 2
the same way; `doctor --strict` also fails on warnings.

**How each delivery mode protects the value:**

| Mode | Where the plaintext lives | Who can read it |
|---|---|---|
| `vault run --env-file` / `-e` | the child's environment only | the child, its descendants, your user and administrators |
| `vault run -f` / `inject -- cmd` | a 0600 file in a private temp dir, deleted on exit (left-overs of a crash are swept on the next run) | any process running as you while it exists |
| `inject -o PATH` | the file you named, owner-only, until you delete it | as above, for as long as it exists |
| `vault run --host` | the remote command's environment, sent over SSH stdin | the remote user; nothing is written on the server |

A file is never as private as memory. NAVIG cannot stop the program itself from logging or saving
the values it was given.

**`vault adopt`** finds secrets by known token formats (confirmed), secret-like names with a real
value (likely) or high entropy (possible — only with `--include-possible`), and proposes
`vault://projects/<project>[/<dir>][/<env>]/<key>`. Every secret is stored and read back *before*
any file changes; files are then rewritten in place (comments, order, line endings kept; no backup
copy), atomically, keeping their permissions. A label that already holds the same value is reused
(re-running is a no-op); a different value is a conflict and that key is left alone — pick another
with `--label KEY=vault://…`. Plaintext in JSON/YAML/TOML/INI is reported for manual moving.
Replacing a value does **not** remove it from git history or revoke it: when the file was ever
committed, rotate the key at the provider.

**After a rotation** (`navig vault set <ref>`), the next `vault run` gets the new value with no
file edit. A program that is already running keeps the value it started with — restart it.

**`vault doctor` codes:** `RESOLVED` · `UNRESOLVED` · `MALFORMED` · `ACCESS_DENIED` · `VAULT_LOCKED` ·
`AUTHORITY_UNAVAILABLE` · `CACHE_EXPIRED` · `PLAINTEXT` · `DUPLICATE_PLAINTEXT` · `ENV_SHADOW` (your
shell exports a different value — a plain dotenv loader, which never overrides, would use it) ·
`LOCAL_SHADOW` · `PINNED_OLD` · `NEEDS_LAUNCHER` (references in JSON/YAML/TOML/INI only `vault inject`
resolves) · `COMPOSE_ENV_FILE` · `FILE_PERMISSIONS` (POSIX only) · `DUPLICATE_KEY` · `PARSE_ERROR`.

**Docker Compose.** Compose passes `env_file:` values to the container literally. Name the keys
under `environment:` instead and let Compose interpolate them from the environment `vault run`
builds (the shell environment takes precedence over Compose's own `.env` for interpolation):

```yaml
services:
  web:
    environment:
      OPENAI_API_KEY: ${OPENAI_API_KEY}
```

```bash
navig vault run --env-file .env -- docker compose up
```

**CI.** Use the CI platform's own secrets for the CI job itself; never put a vault credential in a
workflow file or on a command line. A runner enrolled as a vault device (`navig vault join`) with
a narrow grant can use `navig vault run --env-file .env -- <step>` like any other machine.

### Federation — one vault, scoped per machine

On the machine that holds the vault (the **authority**, `vault.authority.enabled: true`):

```bash
navig vault devices enroll neuron-pc [--kind client|server|mini] [--expires 90d]
navig vault grant neuron-pc 'vault://hosts/cybesis-vps/ssh' [--cache-ttl 12h] [--expires 30d]
navig vault devices list [--json]
navig vault revoke neuron-pc ['host/cybesis-vps/*']    # remove grants
navig vault devices revoke neuron-pc                   # refuse the machine from its next request
```

On the other machine (a **device**):

```bash
navig vault join http://127.0.0.1:8790     # paste the one-time code; needs a DIRECT path (e.g. ssh -L)
navig vault sync [--status]                # what is cached, until when; drop what is no longer granted
navig vault leave
```

A device reads with the same `navig vault get/run vault://…` and host `*_ref` fields; a label it
does not hold locally is asked of the authority — default deny, every read and denial audited
there, values sealed to that device's own key. Nothing is cached unless the grant sets
`--cache-ttl`; a cached value is used only while the authority is unreachable and never past
its TTL. **Revocation cannot reach a machine that stays offline** — its cached copies last until
their TTL. Design: `docs/architecture/vault-federation.md`.

### Export to SVG — back up the vault, or hand someone a password

The vault is sealed to *this* machine, so it cannot simply be copied. `export --svg` seals the
chosen secrets into an ordinary SVG picture (it renders as a small pixel grid). The file is
encrypted with AES-256-GCM, using a key derived from a passphrase (PBKDF2-SHA256, 600,000
rounds). Without the passphrase it reveals no labels, names or counts.

```bash
navig vault export --svg github             # one secret → vault-<random>.svg + a generated passphrase
navig vault export --svg github openai      # several in one file (labels, ids or provider names)
navig vault export --svg --all              # the whole vault — asks first (--yes to skip)
navig vault export --svg --all --each       # one file per secret, each with its OWN passphrase
navig vault export --svg github --opener    # also write open-vault-svg.html for the recipient
navig vault export --svg github --choose-passphrase   # your own (≥14 chars), or NAVIG_VAULT_SVG_PASSPHRASE

navig vault open file.svg                   # what it holds — secrets masked; --reveal to show
navig vault import file.svg [--dry-run]     # add to this vault — never overwrites a secret you have
navig vault opener                          # write the offline opener page on its own
```

- **The passphrase is the lock.** A generated one (~125 bits, e.g. `k7mq4-x3vtb-…`) is shown
  **once**. Whoever holds the file can guess offline with no lockout, so a chosen passphrase
  needs at least 14 characters. Send it by a **different channel** than the file.
- **Opening it elsewhere:** `navig vault open|import`, `nv open|import` (just
  `pip install navig-vault`), or **https://navig.run/open** (the same page as
  `open-vault-svg.html`) in any browser. That page decrypts
  locally with WebCrypto and makes no network requests. The decoder is never put *inside* the
  SVG: script-in-SVG is how "SVG smuggling" malware works, so mail filters strip it.
- **Sending it:** attach it as a *file/document*, not a photo. Chat apps re-encode images,
  which destroys the payload. Zip it if a service blocks `.svg`.
- Old unencrypted *SVG Vault* files still open: `navig vault open old.svg --save <folder>`.

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

## 23.5.1 Documents: Cabinet and Paperwork (`navig cabinet`, `navig paperwork`)

The vault keeps **secrets**. The cabinet keeps **files**: ID and passport scans, medical records, contracts, diplomas, photos, recordings. It takes any type and any size, encrypts everything including titles, tags and OCR text, and makes text search local-only. `navig paperwork` files business paperwork (invoices, quotes, contracts, tax) into a space as plain files for your accountant, and encrypts the ID and medical documents it finds into the cabinet. Both come from one plugin: `navig-cabinet` (`pip install navig[cabinet]`; `navig[paperwork]` is an alias).

```bash
navig cabinet add passport.pdf --expires 2031-05-01   # encrypt + local OCR
navig cabinet add ~/Scans/medical/                    # a whole folder
navig cabinet search blood test                       # words inside the documents
navig cabinet expiring                                # due within 90 days
navig cabinet open <id>                               # temporary decrypted copy → its app
navig cabinet export --all --by-category -o <dir>     # original names, checksum-verified
navig cabinet backup -o <file>                        # portable .ncab, own passphrase
navig cabinet backup -o <file> --with-vault           # …plus every vault secret (the vault has no export of its own)
navig cabinet restore <file.ncab>
navig cabinet import-paperwork --space <space>        # ID/medical docs a paperwork scan set aside
navig cabinet passphrase set | clear                  # machine key (default) ↔ passphrase
navig cabinet dates [--apply]                         # read expiry dates from documents that lack one
navig cabinet remind [--dry-run]                      # expiry reminders now (the daemon sends them daily)
navig cabinet verify · status · edit · remove · undelete · close

navig paperwork scan <folder>... --space <space>       # read + classify → a reviewable plan
navig paperwork apply --yes --space <space>           # copy, verify, quarantine (undo-able)
navig paperwork handoff --yes --space <space>         # ID/medical → cabinet; letters → their space
navig paperwork echeances --space <space>             # deadlines from the letters
```

- **Location:** `~/.navig/cabinet/` (override with `NAVIG_CABINET_DIR`).
- **Passphrase in scripts:** `NAVIG_CABINET_PASSPHRASE`.
- **Desktop app:** NAVIG Desktop → Apps → **Cabinet** (search, expiring, add, open). Its `/api/deck/cabinet/*` routes answer only this computer, never the tunnel or the Mini App.
- **Expiry reminders:** the daemon sends them by itself, 90/30/7 days before a document expires and once it has, naming the category, never the title. Turn them off with `navig config set cabinet.reminders.enabled false`; `cabinet.reminders.hour` sets the daily hour.
- **Machine-key caveat:** a machine-key cabinet does not survive an OS reinstall, so back it up.
- **Recovering without navig:** `navig_cabinet/recover_backup.py` restores a backup with only Python and `cryptography`.

---

## 23.5.2 Audio Enhance (`navig audio enhance`)

Restore and master an existing recording (speech, sound effects, music, ambience) without
changing what it is. Provided by the navig-audio plugin; NAVIG Desktop has the same flow in
**Media Studio → Enhance** (drop a file, read the findings, pick a preset, compare A/B
at matched loudness, keep or export).

```bash
navig audio enhance take.wav --analyze          # findings + measurements; writes nothing
navig audio enhance take.wav                    # Preserve Original — fixes only measured defects
navig audio enhance take.wav --dry-run          # the plan and why each step is there
navig audio enhance take.wav --mode studio --format wav,flac --out C:\clean
navig audio enhance take.wav --preset audio-enhance/clean-sfx
navig audio enhance take.wav --mode custom --set denoise=strong --set eq=gentle
navig audio enhance-presets                     # Preserve Original · Clean SFX · Studio Polish · …
```

- **Modes:** `preserve` (default; strict restoration), `studio` (plus gentle tone, light
  compression where it suits, category loudness), `cinematic` (creative, labelled so),
  `custom`. Enhancement presets are universal presets (tool `audio.enhance`, §23.4.1), so
  `navig presets duplicate audio-enhance/studio-polish` makes your own.
- **Verified:** every output is checked against the original (length, alignment, events
  kept and none invented, pitch, tone, new content, clipping, true peak). A failure is
  retried without its riskiest step, then rejected. The original is never modified.
- **Output:** `<name>.enhanced.wav` (+ `.enhance.json` report) next to the source, or in
  `--out`; never overwrites (`-take2` …). `--json` for scripts; exit 1 when rejected.
- **DeepFilterNet (optional):** with `deep-filter.exe` in `~/.navig/bin/` (or `NAVIG_DEEPFILTER`),
  speech noise is cleaned by it locally; `--set engine=local` forces ffmpeg.
- **Cloud:** only the ElevenLabs voice isolator, only for speech, only with `--allow-cloud`.

## 23.5.3 AI regeneration (`navig audio regenerate`)

New audio made FROM a recording — the opposite of `navig audio enhance`, and labelled as
generated everywhere. NAVIG Desktop: Media Studio → Enhance → **New versions with AI**.

```bash
navig audio regenerate take.wav --op voice --voice <voice-id> --allow-cloud     # same words, new voice
navig audio regenerate fx.wav --op variations -d "heavier" -n 3 --allow-cloud   # new takes, text only sent
navig audio regenerate song.mp3 --op replace --stem drums --with "brushed kit" --allow-cloud
navig audio regenerate fx.wav --op variations --dry-run                         # what is sent and billed
```

- Runs on ElevenLabs with the account's key; `--allow-cloud` is required for every real run
  (`voice` and `replace` upload the recording; `variations` sends only text).
- `--describe-with-ai` lets an audio-capable model (Gemini via OpenRouter) name what it hears —
  it uploads the clip; without it the description comes from local measurements.
- Outputs: `<name>.regen-<op>.wav` + `.regen.json` (prompt, provider, closeness reading). The
  original is never modified.

## 23.5.4 Audio Lab — delivery, restoration, podcast edit, stems, dubbing, score

Ten jobs on top of Enhance and Regenerate. Every command leaves the original untouched;
local commands need only ffmpeg; anything billed or uploaded needs `--allow-cloud` for that run.

```bash
# Local
navig audio delivery mix.wav --target all                    # pass/fail for every platform spec
navig audio delivery mix.wav --target spotify --fix -f wav   # a levelled copy that fits
navig audio clean-video interview.mp4                        # clean dialogue, picture copied untouched
navig audio match mix.wav --ref reference.mp3                # tonal balance + loudness like a reference
navig audio loop rain --seconds 600                          # a seamless loopable bed
navig audio enhance tape.wav --preset audio-enhance/cassette # also: vinyl · phone-call · voice-memo
navig audio podcast-edit episode.mp3                         # propose cuts → episode.cuts.json
navig audio podcast-edit episode.cuts.json --apply           # apply the reviewed cut list
navig audio stems song.stems --preset karaoke                # remix stems already split
navig audio similar door.wav --in D:/sfx                     # sounds that sound like this one

# ElevenLabs (billed)
navig audio loop "night café, distant chatter" --seconds 300 --generate --allow-cloud
navig audio stems song.mp3 --allow-cloud                     # split once: six parts (--two: vocals + rest)
navig audio dub talk.mp4 --to fr --to es --allow-cloud       # same voices, other languages
navig audio score trailer.mp4 --style "dark synth" --dry-run # the cuts and the music plan, free
navig audio score trailer.mp4 --style "dark synth" --allow-cloud
```

- **delivery** — targets: youtube · spotify · tidal (−14 LUFS / −1 dBTP), apple-music · podcast
  (−16), podcast-mono (−19), ebu-r128 (−23, strict) and atsc-a85 (−24 / −2, strict). `--fix`
  re-measures the exported file and tightens until the true peak is under the ceiling.
- **clean-video** — the sound is enhanced (Voice Restoration preset by default) and muxed back
  with the video stream copied bit for bit; sync drift is measured, and a rejected cleanup
  writes nothing.
- **podcast-edit** — local Whisper word timestamps (`--model tiny…large-v3`). It proposes
  filler words (en/fr/ru/de/es), long pauses (shortened to `--keep-pause`, never removed) and
  repeated words. Set `"keep": true` on any cut in the JSON before applying; joins use 15 ms
  crossfades. A filler the transcript did not write down cannot be cut — listen to the result.
- **stems** — the split is billed once and kept in `<name>.stems/` (`stems.json` lists the
  parts); every remix after that (`--mute`, `--solo`, `--gain vocals=-3`, presets karaoke ·
  instrumental · acapella · drumless · bassless) is local, into `<name>.stems/mixes/`.
- **similar** — a timbre and rhythm fingerprint per file, cached per folder under the data dir;
  only new or changed files are fingerprinted again. For exact duplicates use dedupe instead.
- **dub** — only the sound is uploaded; a dubbed video keeps its picture and gets the dub as
  the default track with the original as a second one (`--replace-track` drops it). A dub whose
  length drifts more than 2 % is flagged. Labelled AI-generated in `<name>.dub.json`.
- **score** — scene cuts become `composition_plan` sections of exactly the scene's length
  (shots under 3 s merge); the music ducks under existing dialogue (`--no-duck` replaces it).
  10 s – 5 min videos. Outputs `<name>.scored.<ext>`, `<name>.score.wav` and `<name>.score.json`.
## 23.5.5 NAVIG Voice — profiles, policy, budget (`navig voice`)

One voice setup for every surface: the Dock's **Read aloud** (Chrome), `navig voice speak`
and NAVIG Desktop. Provided by the navig-audio plugin.

```bash
navig voice speak "Bonjour" --profile speech/french-narrator   # a saved voice profile
navig voice speak "Привет" --profile speech/russian-deep-narrator -o hi.mp3 --no-play
navig presets list --tool speech                                # the profiles (builtin + yours)
navig presets duplicate speech/english-reader --name "My reader"
navig voice policy                     # show: auto · free · premium · offline
navig voice policy free                # never a paid voice, even when picked
navig voice policy auto --paid-fallback   # let Auto retry a failed free voice on a paid one
navig voice budget 20000               # paid read-aloud characters per day (0 = paid off)
navig voice usage                      # paid characters per day, ≈ USD, per provider
navig voice packs list                 # offline packs: status, size, license (add --json)
navig voice packs install en_US-ljspeech-medium   # download + SHA-256 verify (~67 MB, asks first)
navig voice packs remove ru_RU-denis-medium --yes # delete one installed pack
navig voice speak "Bonjour" -p local   # speak from an installed pack (on-device only)
```

- **Profiles** are universal presets (tool `speech`, §23.4.1): provider, model, voice,
  language, speed, pitch (Hz, Edge), ElevenLabs stability / similarity, a delivery note
  (MiMo) and pronunciation rules (`pronounce: ["NAVIG=навиг"]`). A field the provider cannot
  do is reported as ignored, never silently "applied". `navig presets default speech <id>`
  makes one the default for read-aloud.
- **Policy** (`voice.policy`, enforced by the daemon for every client): **auto** — free
  voices; a paid one only when named (by you or a profile) · **free** — never paid ·
  **premium** — your keyed paid voice first · **offline** — text never leaves the machine:
  it speaks with an installed on-device pack (`local`) or refuses with what is missing —
  never a cloud provider, and Auto never picks the local engine implicitly.
- **Offline packs** (`navig voice packs`): pre-converted Piper voices (fr_FR-siwis-medium,
  ru_RU-denis-medium, en_US-ljspeech-medium) from a pinned sherpa-onnx release, downloaded
  once, SHA-256 verified, stored in `<config>/voice/packs/`. The engine itself is the
  optional extra `pip install "navig-audio[local-tts]"` — sherpa-onnx runs CPU-only, no
  GPU; about 67 MB download ≈ 81 MB on disk per pack, and a measured 430–560 MB of RAM
  peak while a voice is loaded (warm synthesis runs at ~0.14× real time). Licenses differ
  per voice (CC-BY 4.0, CC0, public domain — the model card's own license) and are
  printed before every install. Under offline, synthesis makes
  no network call at all — a missing pack is an error with the install command, never a
  silent download or fallback.
- **Budget** (`voice.daily_char_budget`, default 20,000): a paid read-aloud request that
  would pass it is refused (HTTP 429) before the provider is called; a cached clip costs
  nothing and is always served. Usage is recorded in `<config>/voice/usage.json`. Explicit
  `navig voice speak` runs are not metered — the budget guards read-aloud.
- **Prices** shown in Options → Voice and `GET /api/deck/voice/catalog` come from the
  providers' pricing pages with the date they were checked (ElevenLabs v2 Multilingual
  $0.08 / 1k characters, OpenAI tts-1 $0.015 / 1k).
## 23.5.6 Content production (`navig content`)

The assembly line that composes the media engines — topic → script → narration → cut → pack —
provided by the **navig-pipeline** plugin. `navig content` is the canonical verb;
**`navig pipeline` is the same command under its old name** (kept registered, hidden from
`--help`, one notice on stderr) and `navig-content` is its standalone script. The
distribution name does not change, and nothing moved: data stays in `~/.navig/pipeline`
([ADR-0002](../../docs/decisions/0002-media-modular-ownership.md)).

```bash
navig content status                # which stages are wired (their plugin installed) — --json
navig content run --topic "…"       # script → narrate → cut → fanned-out post
navig content reel --lang fr        # vertical reel per language: capture, narrate, caption, score
navig content clip --audio bed.mp3  # cut a clip to a track you already have (no narration)
navig content captions track.mp3 -o out.srt --language en
navig content hooks track.mp3 --top 5       # which passages are worth cutting
navig content episode build ./ep01  # one folder: narrate → check → render → pack
navig content publish ./ep01/out --youtube   # render → post pack (spec check, cover, caption)
navig content localize ./ep01/out --to fr    # the same picture in another language
navig content schedule --every 1d --topic "…"   # autopilot the line on a cadence
navig content telegram-import --mode link --preview
```

- **Orchestrator, not a workflow engine.** Each stage calls the owning plugin's public
  command — `navig text` (script), `navig audio` (narration, captions), `navig video`
  (render), `navig download`, `navig social`. A stage whose plugin is missing is skipped and
  reported, never a crash. `status --json` shows the wiring before you start.
- **Standalone:** `navig-content run …` runs without the daemon; inside NAVIG an integrated
  run goes through Blocks (`navig apply`) and gets a receipt.
- `--json` on the verbs above except `localize` and `schedule`.
- **As a Block:** `navig apply content-reel --input topic="…"` runs the same line under
  approval with a signed receipt, and verifies the drafted file actually exists
  (`navig block verify content-reel` lints the spec).

## 23.5.7 NAVIG Glass viewer (`navig glass`)

[NAVIG Glass](https://github.com/navig-run/glass) is the standalone instant file viewer —
images, PDF, video and audio (source: `apps/glass`) — that runs with NAVIG uninstalled and
keeps every one of its standalone functions if NAVIG disappears mid-session. The
`navig-glass` plugin adds discovery and launch only: no socket in the viewer, no NAVIG
secret on disk, no always-online anything. The `glass` module is free and toggleable —
switching it off disables the routes and leaves the app untouched.

```bash
navig glass status          # verified install: path, contract version, kinds (--json)
navig glass where           # resolved executable, or every path that was searched (--json)
navig glass open <file>     # open a file in Glass — the same binary a double-click runs (--json)
```

Discovery order: `$NAVIG_GLASS_EXE` → `PATH` (winget / MSIX app-execution aliases) →
`%LOCALAPPDATA%\Programs\NAVIG Glass\`. An executable that cannot answer the
`--navig-info` contract is reported as such, never assumed healthy.

NAVIG surfaces reach the same launch through the daemon: `POST /api/deck/glass/open`
(standard deck auth, module-gated). The reuse model for the next standalone product —
one contract flag, discovery, launch, one route, one module — is documented in
[`docs/guides/standalone-integration.md`](../../../docs/guides/standalone-integration.md).

## 23.5.5 Podcaster tools — chapters, levelling, one-word fixes, QA, bleed, highlights, ads, stings

```bash
navig audio chapters episode.mp3                       # chapters + show notes; a copy with chapters embedded
navig audio qa episode.mp3                             # pass/fail page before you publish (exit 1 on fail)
navig audio level-speakers episode.wav                 # every speaker to one loudness (local speaker split)
navig audio level-speakers episode.wav --allow-cloud   # …with ElevenLabs Scribe finding the speakers
navig audio debleed host.wav guest.wav                 # turn each mic down while the other person talks
navig audio podcast-edit episode.mp3 --breaths --clicks             # breaths ducked, clicks repaired
navig audio podcast-edit episode.cuts.json --apply --keep-timing    # cuts filled with room tone (video sync)
navig audio highlights episode.mp3 -n 5                # five captioned 1080x1920 clips (audiogram for audio)
navig audio ad-slot episode.wav                        # list natural pauses for a sponsor read
navig audio ad-slot episode.wav --ad read.wav --bed music.wav   # insert it, level-matched, bed ducked
navig audio fix-word episode.wav --word forty --say fifty --own-voice --allow-cloud
navig audio sting "warm analog synth, quiet confidence" --seconds 8 --allow-cloud
```

- **chapters** — local transcript; with an AI configured it titles the chapters and writes the
  notes (`--no-ai` for pause-placed chapters titled with their opening words). Chapter starts
  are always snapped onto a real sentence start. MP3/M4A copies keep their audio untouched.
- **qa** — loudness and true peak for a `--target`, clipping, silences over 3 s (measured
  against the room's own noise floor), harsh S sounds per minute, cut-off start/ending, noise
  between words.
- **level-speakers** — the local speaker split uses voice pitch and spectral shape on the
  audio's own pauses and is labelled approximate; gains are capped at ±12 dB and change only
  between turns, then a latency-compensated -1 dBTP limiter.
- **fix-word** — finds the word (digits and number words match each other: "40" = "forty"),
  clones only that speaker's turns (up to 90 s), speaks the replacement with the surrounding
  words as context, level-matches it, lays room tone under it and splices it in. The clone is
  deleted afterwards unless `--keep-voice`. Needs `--own-voice` (consent) and `--allow-cloud`;
  the replaced span is marked AI-generated in the sidecar.
- **ad-slot** — a slot is a sentence end followed by at least 0.5 s of pause, picked near
  25/50/75 % (or `--at`); the output is M4A with "Sponsor" chapters.
- **sting** — two-section composition plan, composed at 10 s and cut to the exact length with a
  fade, levelled to -16 LUFS. `--dry-run` shows the plan for free.

## 23.5.6 Beatmaker tools — comp, fit, chop, doubles, drums, arrangement, scratch vocals

```bash
navig audio comp take1.wav take2.wav take3.wav --beat beat.wav   # best take per bar, joined
navig audio vocal-fit part.wav --beat beat.wav --fix beat        # tempo + key gap, and the move that closes it
navig audio chop record.wav                                      # bar slices + loop.wav + slices.mid
navig audio doubles lead.wav --voices 2 --harmony third          # doubles that stay solid in mono
navig audio enhance vocal.wav --preset audio-enhance/radio-vocal # de-plosive, de-ess, breath control
navig audio match mix.wav --ref ref.wav --check --mono-bass      # balance, width, low end vs a reference
navig audio stems beat.mp3 --allow-cloud                         # split once …
navig audio drum-replace beat.stems/drums.mp3 --kick k.wav --snare s.wav --blend 0.3
navig audio tap-beat taps.wav --bars 4                           # tapped rhythm → pattern + MIDI
navig audio arrange beat.mp3 --stems beat.stems                  # intro / breakdown / drop
navig audio beat demo beat.mp3 --lyrics hook.txt                 # spoken guide, one line per bar
navig audio beat demo beat.mp3 --lyrics hook.txt --sing --allow-cloud   # sung guide
```

- **comp** — takes are aligned to the first; with `--beat` each bar is scored (otherwise each
  phrase): timing = lag against the takes' consensus in that bar, noise = quiet frames inside it,
  clipping, pitch steadiness. The winner per bar is joined with 20 ms crossfades.
- **vocal-fit** — tempo ratio (half/double time count as a match) and the pitch move from lining
  up the two chroma profiles, tie-broken by the bass root (key names alone confuse a key with its
  dominant). `--fix beat|vocal` writes a rubberband-moved copy (formants kept).
- **chop** — bars from the beat tracker; the loop is the 1/2/4/8-bar span whose next occurrence
  matches best; `slices.mid` plays slice *n* on note 36+*n*.
- **doubles** — ±8–14 cent, 15–35 ms copies panned L/R; delays are re-drawn until the mono sum
  stays within 1.5 dB of the lead in third-octave bands. Harmony is a fixed interval for the whole
  take (not note-by-note).
- **drum-replace / tap-beat** — hits by spectral flux, timed to 1 ms; each hit can be several
  drums (kick < 150 Hz, snare 200 Hz–5 kHz, hat > 5 kHz, each judged against its own band); MIDI
  on GM drum notes. Tap-beat renders a kit synthesised locally (no samples needed).
- **radio-vocal** — new enhance params `deplosive`, `deess` (light/strong), `breath_control`
  (light/strong); the verifier treats a darker result as intended when the de-esser ran.
- **match --check** — six-band balance and stereo width (side/mid, a mono file reads -60 dB),
  low-end L/R correlation (anti-phase lows are reported, never folded), loudness, crest; `--mono-bass`
  folds < 120 Hz to mono with a Linkwitz-Riley crossover.
- **arrange** — cut from the beat on its own bar grid, so key and tempo match; with `--stems`
  "drums out" is exact, otherwise a band-split pulls them back (reported).
- **beat demo --lyrics** — builds the demo sheet from a plain lyrics file (one line per bar, a
  stock voice, `--voice` to pick another); `--sing` composes the hook with sung lyrics at the beat's
  tempo and key, splits out the vocal and lays it on the beat. Guides are tagged
  `GUIDE — NOT FOR RELEASE`.

## 23.5.7 Video Lab — tighten, captions, reframe, cut on the beat, b-roll, grade, recap, export

```bash
navig video tighten talk.mp4                         # fillers + long pauses out of picture and sound
navig video captions talk.mp4 --style box --translate fr   # word-timed SRT/VTT + burned captions
navig video reframe talk.mp4 --to 9:16               # vertical that follows whoever is speaking
navig video beat-cut clips/ --music track.mp3 --vertical   # montage cut on the beat
navig video broll talk.mp4                           # cutaway ideas where the narration names one (free)
navig video broll talk.mp4 --generate --allow-cloud  # …and the generated shots laid in
navig video color-match b.mp4 --ref a.mp4            # one shot's colour on another (+ a .cube LUT)
navig video recap long.mp4 --seconds 60              # the liveliest minute, joined
navig video stabilize shaky.mp4 [--deflicker]        # steadier picture, shake measured before/after
navig video export-check clip.mp4 --for tiktok --fix [--safe-areas]   # platform spec, pass/fail
navig video lipsync dubbed.mp4 --allow-cloud         # mouth re-timed to the dub (LatentSync)
```

- **tighten** — the podcast cut list (fillers, pauses, repeats) applied at identical timestamps
  to picture and sound, so sync cannot drift; 10 ms audio fades hide the joins.
  `--dry-run` lists the cuts.
- **captions** — local transcript; cues end at sentences or ~42 characters; SRT + VTT for every
  track, optionally burned in (`plain` · `box` · `kinetic`). `--translate fr` asks your AI to
  translate the cues as a batch with the original timings (a wrong count is refused, not written).
- **reframe** — faces five times a second (YuNet if available, else OpenCV's cascade), the
  speaker chosen by mouth-region motion with a one-second hold before switching, the crop
  gliding at a speed limit. `9:16` · `1:1` · `4:5`. A clip with no faces holds its frame.
- **beat-cut** — a shot every `--beats` beats of the music's grid, each taken from the most
  active stretch of its clip; the music is the sound.
- **broll** — listing is free (`--json`/table); `--generate` is billed (Replicate) and lays the
  shots over the picture with the original sound, then labels them AI-generated.
- **color-match** — Reinhard Lab transfer to a 33-point `.cube` LUT (usable in any editor),
  ΔE reported before and after; works on clips and stills.
- **recap** — windows scored on sound energy, frame motion and shot changes, joined in order.
- **stabilize** — two-pass vid.stab (`optzoom` hides the moving edges) with shake measured by
  tracking corners and flicker by brightness jitter; pairs whose motion could not be measured
  are counted, never guessed.
- **export-check** — platform table (tiktok · reels · shorts · youtube · instagram-feed ·
  square · broadcast): size, fps, codecs, length, loudness, true peak. `--fix` writes a passing
  copy (crop or `--pad`, rate, codecs, levelled sound); `--safe-areas` saves a frame with the
  platform's UI zones drawn on it.
- **lipsync** — LatentSync on Replicate (`bytedance/latentsync`, version pinned in the module).
  For `navig audio dub` output: the picture and the dub are uploaded, the mouth is redrawn to
  match, and the result reports whether the lengths agree. Billed; needs `--allow-cloud`.

Everything else in the list is local. Sources are never written; every output gets a JSON
sidecar.

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

#### Memory controls — on/off, incognito, secrets

```bash
# The global switch (default on). Off: the agent neither recalls nor saves facts,
# and its memory tools refuse. Deleting a memory always works.
navig config set memory.enabled false
navig config set memory.enabled true
```

- **Incognito chat** — on Telegram, `/incognito` (or `/incognito on|off`). An incognito
  chat starts fresh, reads and saves no memories, and its messages are not kept in the chat
  history, the session store or the Telegram catalog. Over HTTP, send
  `"flags": {"incognito": true}` to `POST /llm/chat`; nothing goes to the Synapse sync log.
  Custom instructions (`USER.md`) still apply.
- **Secrets never become memories.** Every saved fact passes one filter: API keys, tokens,
  connection-string passwords, spoken secrets (`password is …`, `PIN: …`, 2FA codes), card
  numbers (Luhn-checked) and IBANs are replaced with `***REDACTED***` before the fact, its
  search index or its embedding is written; the fact is flagged `redacted`. A fact that is
  *only* a secret is not saved. Keep the value itself in `navig vault`.

```bash
# Facts saved before the filter existed: report (never prints the original text)…
navig memory scan-secrets
# …then redact them in place (pending, deleted and superseded facts included)
navig memory scan-secrets --apply
```

#### Personalization — answer style and sources

All of these take effect on the next message, also in a running daemon (they are read
live from `config.yaml`). The OS **Settings → Personalization** page writes the same keys
through `GET|POST /api/deck/personalization`.

```bash
# Style dials — each less | default | more. They extend your persona's tone.
navig config set style.warmth more
navig config set style.enthusiasm less
navig config set style.emoji less          # "less" = never use emoji
navig config set style.headers_lists less  # plain prose instead of headers/bullets
navig config set style.visual more         # tables / diagrams when they help

# Web search: auto (default — when the turn calls for it) | always | off (no web at all)
navig config set ai.web_search.mode off

# Fast answers: simple questions go to the fast model (default off)
navig config set ai.fast_answers true

# Which of your own sources the agent may search (default on)
navig config set ai.sources.library false
navig config set ai.sources.connectors false
```

Custom instructions are `USER.md` in your workspace (`/api/deck/customize/instructions/USER.md`).

#### Managing saved memories

```bash
navig memory facts                      # saved memories (📌 = pinned)
navig memory pin <id>                   # keep it in mind in EVERY conversation
navig memory unpin <id>                 # back to "recalled when relevant"
navig memory edit <id> "corrected text" # fix a memory in place
navig memory forget <id>                # remove it
navig memory summary                    # "What NAVIG knows about you" (cached)
navig memory summary --refresh          # rewrite it now (calls the model)
```

Pinned memories are injected on every turn (at most 10), but only once approved — a
pending proposal is never used, pinned or not. The summary is written from approved
memories only and is rewritten when they change. The deck serves the same operations at
`GET /api/deck/memory/facts`, `PATCH|DELETE /api/deck/memory/facts/{id}` and
`GET|POST /api/deck/memory/summary` (deck auth only — not on the token-less
`/api/memory` mirror).

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

Every AI turn — whether through `navig ask`, the Gateway REST API, Telegram, or any
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

#### Mailroom outbox — queued replies (`navig email outbox`, navig-email ≥ 0.4.0)

Every message the mailroom sends is one job in `<navig data dir>/mailroom.db`:
`DRAFT → PENDING_APPROVAL → SCHEDULED → SENDING → SENT` (plus `CANCELLED`, `FAILED`,
`NEEDS_REVIEW`, `OUTCOME_UNKNOWN`). Approving binds the exact text you saw; editing drops the
approval. The daemon's mailroom tick sends due jobs **while this computer is online**; a job
more than 24 h late goes back to you instead. A provider timeout is never retried blindly — the
job's own `Message-ID` is looked up in Sent first.

```bash
navig email reply <thread> --space <space> --queue                 # generate → awaiting approval
navig email reply <thread> --space <space> --queue --yes --send-in 15m
navig email outbox list [--open] [--json]
navig email outbox show <job>                                      # text, quality check, history
navig email outbox approve <job> --version <hash> [--in 15m | --at 2026-10-20T09:00]
navig email outbox edit <job> --body "…"                           # resets approval
navig email outbox delay <job> --by 1h | cancel <job> | send-now <job> --yes
navig email outbox reopen <job> | confirm-sent <job>               # FAILED / NEEDS_REVIEW
navig email outbox pause [--all] | resume                          # emergency stop
navig email outbox history                                         # audit trail, no bodies
navig email send --to … --subject … --body … --yes [--schedule 30m]
```

Generated replies pass a quality gate first (empty or one-word output, placeholders, a header or
"as an AI" leak, the wrong language, another identity's signature → rejected and regenerated once;
unexplained numbers, money or promises → flagged for you). Set `mailroom.ai.fallback_model` in the
space config to retry with a stronger local model.

**Auto-reply policies** (`navig email policy …`) — all ship off; each is enabled for one account:

```bash
navig email policy list
navig email policy enable business-ack --account you@gmail.com --alias hello@yourdomain.com --yes
navig email policy enable trusted-repetitive --account you@gmail.com --sender partner.com --fact "We ship on Mondays." --yes
navig email policy enable draft-replies --account you@gmail.com            # drafts only, never sends
navig email policy test <message-id> --space <space>                        # what would happen (dry run)
navig email policy disable business-ack                                     # queued replies stop too
```

Machine mail is ignored; contracts, payments, bank details, job offers, government mail, account
recovery, credentials, security incidents, sensitive attachments and irreversible requests are
always drafted for review — no policy can send them.

**Newsletter briefing** (`navig email brief …`) — products, events and jobs extracted from
newsletters, merged across emails, delivered to Telegram, sources archived only after delivery:

```bash
navig email brief run --space <space> --dry-run       # build and show, send nothing
navig email brief run --space <space>                 # deliver now
navig email brief schedule --space <space> [--apply]  # one cron job; replaces the weekly template digest
navig email brief items [--kind job] | save <item> | dismiss <item>
navig email brief extract <message-id>                # what one email yields
navig email brief restore <message-id>                # undo an archive / Trash
```

Configure under `mailroom.brief` in the space's `.navig/config.yaml`: `frequency` (daily ·
every_2_days · every_3_days · weekly · every_2_weeks · monthly · adaptive · manual), `weekday`,
`time`, `timezone`, `max_sources` (6), `min_items` (3), `max_postpone_days` (14), `quiet_hours`,
`alert_days` (3), `interests: {label: [keywords]}`, `jobs: {keywords, locations, min_day_rate}`,
`cleanup: {archive, trash_senders, trash_after_days}`.

**Labels, filters and attachments:**

```bash
navig email organize audit                                 # read-only
navig email organize preview --space <space>               # proposed taxonomy, counts
navig email organize apply --space <space> --yes           # additive + snapshot
navig email organize rollback <snapshot.json>
navig email organize retire --space <space> --yes          # old labels off migrated mail
navig config set connectors.gmail.filters true             # then reconnect Gmail to include filters
navig email attachments save <message-id> --space <space>  # documents → <space>/inbox/ for paperwork
```

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

See `AGENT_MODE.md` (not in this tree) for full SOUL.md documentation.

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

#### Security levels — one knob, three postures (router-firewall style)

`approval.security_level` is the single switch an operator touches day to day —
the same High / Medium / Low concept as a router firewall:

| Level | Posture | What pages |
|---|---|---|
| `high` | nothing trusted | **every** gated call, including proven read-only shell commands; **any** approval timeout denies (fail-closed, whatever `default_action` says) |
| `medium` | the default | proven reads run unprompted (audited); writes page; dangerous always denies on timeout |
| `low` | terminal exempt | `bash_exec` / `run_command` / `navig_run` never page (reads **and** writes, still audited); `db_query`, `cdp_eval`, connector writes, missions, shutdown — everything else still pages |

```bash
navig approve status            # level, what it means, configured vs. the running gateway
navig approve level low         # set it: high | medium | low (aliases strict/balanced/permissive)
navig approve policy            # the CONFIGURED safe/confirm/dangerous/never patterns
navig approve list              # requests waiting for you right now
```

`navig approve status` is the one place that tells **configured** from **running**: the
policy is built once when the gateway starts, so setting a level without
`navig service restart` leaves the daemon on the old one — status shows `⚠ differs —
restart pending`, and a gateway too old to answer reads as restart pending too. `level`
refuses a typo (it never stores some other level) and reads the value back before it
prints a tick. `navig config set approval.security_level <x>` still works, but without
the validation or the read-back.

Rules: explicit granular keys beat the level (`auto_approve_read_only: false`
wins even under `medium`); aliases `strict`/`balanced`/`permissive` normalize to
`high`/`medium`/`low`; an unknown value logs a warning and lands on `medium` —
a typo never kills the gateway at boot and never fails open. `approval.enabled:
false` stays the master kill-switch **outside** the three levels (the "unplug"
position) and, being read at gateway startup, like every key here it takes
effect on `navig service restart`.

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
  security_level: medium      # high | medium | low — the one knob (see table above)
  enabled: true               # master switch, outside the three levels
  timeout_seconds: 900        # how long a gated request waits for a human
                              # (120 s was the old value — a notification takes
                              # minutes to notice; longer only delays an auto-deny)
  default_action: deny        # timeout resolution (dangerous ops always deny;
                              # high denies on timeout regardless)
  auto_approve_read_only: true  # OPTIONAL override of the level's read bypass —
                              # only set this if you want it to differ from what
                              # security_level derives (explicit key wins)
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
`[Denied: …]` result the agent reads and adapts to (and is told not to retry). Headless CLI runs (no gateway) keep the
single-operator default — dangerous tools log a loud warning and proceed — and
`NAVIG_ALLOW_ALL_COMMANDS=1` bypasses the gate for pre-screened automation. To pin a
specific tool, add an `approval.levels` pattern matching `tool <name>` (e.g.
`"tool cdp_eval*"` under `dangerous:` makes its timeout always deny).

**Read-only shell commands never prompt** (chat path, `approval.auto_approve_read_only`,
default on): a `bash_exec`/`run_command`/`navig_run` whose command is provably a read —
the `shell_readonly` classifier: verb allow-list, no write flags, pipelines and `;`
checked segment by segment, `2>/dev/null` and Windows `2>nul` allowed, `git` global
options (`-C <path>`) skipped when locating the sub-command, a second-level read table
for `git`/`gh`, and wrappers (`powershell -Command` / `cmd /c` / `bash -c`) unwrapped so
the payload is classified by the same rules — runs immediately and is audited as
auto-approved naming the key. Anything that writes, any unknown verb, and every other
gated tool still pages. Set the key to `false` to prompt for everything.

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

See `AGENT_SELF_HEALING.md` (not in this tree) for details.

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

See `AGENT_LEARNING.md` (not in this tree) for details.

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

See `AGENT_SERVICE.md` (not in this tree) for full installation guide, troubleshooting, and security considerations.

### 25.10 Goal Planning

> **Superseded by `navig goal`** — standing goals worked on a schedule until met
> (`navig goal add "<objective>"` · `list` · `show <id>` · `remove <id>`). `navig agent goal`
> still works, prints that pointer, and keeps its own goals (they are not copied over).

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

See `AGENT_GOALS.md` (not in this tree) for comprehensive guide on goal planning, dependencies, and advanced usage.

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
| `--provider`, `-p` | string | auto | Engine for this call: auto, deepseek_search, tavily, brave, serpapi, firecrawl, mojeek, duckduckgo |
| `--json` | flag | false | Output in JSON format (includes the answering `provider`) |
| `--plain` | flag | false | Plain text output |

**Examples:**
```bash
# Basic search
navig search "Python best practices"

# Limit results
navig search "Docker tutorial" --limit 5

# JSON output (prints the provider that actually answered)
navig search "kubernetes deployment" --json

# Force one engine for this call
navig search "nginx configuration" --provider duckduckgo
```

**Provider behavior:**

- The engine is `web.search.provider` (one config key shared by the CLI, agent
  tools, MCP `navig_web_search`, Telegram and NAVIG OS — set it once, every
  surface follows). Precedence: `--provider` (non-`auto`) > `NAVIG_WEB_SEARCH_PROVIDER`
  env > config > `auto`.
- `auto` uses Firecrawl first (when keyed), then the keyed cascade
  **DeepSeek Search → Tavily → Brave → SerpApi**, then keyless
  **Mojeek → DuckDuckGo**. Cascades on *failure*, not just missing keys.
- Every surface reports the provider that actually answered (`--json`
  `provider`, tool output, the OS Search-engine test, Telegram's
  `[Web context — via …]` header).

**DeepSeek Search key setup (recommended engine):**
```bash
# Key from https://platform.deepseek.com — search API, not the LLM key namespace
printf %s "$KEY" | navig vault set deepseek_search/api_key --stdin
# Pick the engine (or use NAVIG OS → Settings → Personalization → Search engine)
navig config set web.search.provider deepseek_search
```

**Other provider keys (optional):**
```bash
navig vault set tavily/api_key <key>
navig vault set brave/api_key <key>       # or: export BRAVE_API_KEY=…
# Firecrawl (paid tier first in auto): navig cred add firecrawl --key fc-… 
```

**Search control plane (NAVIG OS + API):**

| Route | Purpose |
|-------|---------|
| `GET /api/deck/search/status` | active engine, available engines with key presence (booleans only), fallback cascade |
| `POST /api/deck/search/config` | `{provider}` — validate + write `web.search.provider` (the OS settings picker) |
| `POST /api/deck/search/test` | live 3-result probe → `{provider_used, result_count, latency_ms}` |
| `POST /api/deck/search` | web-search proxy for NAVIG OS agent sessions (kill switch → 403) |

NAVIG OS → Settings → Personalization → **Search engine** renders this status
document: active engine, per-engine key chips ("key ✓" / "key missing" / "no key
required"), fallback order, and a Test button showing which engine answered.

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

### 27.7 Decision Model (`navig decider`)

A **decision model** answers fixed questions about a message or document (yes/no, one of
named options, or a level on a scale) with a probability for each option, in ~40–200 ms. It
cannot answer outside the list you give it and it never writes prose. NAVIG uses Cloudflare's
open-weights **Clef** (`@cf/cloudflare/clef`; `clef-flash` is fast, `clef` is stronger) for
decisions it used to ask a full LLM or a keyword list.

| Use id | What it decides |
|---|---|
| `telegram_triage` | how complex a Telegram message is (picks the model tier) |
| `inbox_sort` | which wiki / hub section a dropped file belongs in |
| `inbox_space` | which of your spaces a file belongs in |
| `guard_tool_call` | NAVIG Desktop **Guarded** mode: is a tool call hard to undo, reaching outside the project, or reaching other people? |

**Guarded mode (NAVIG Desktop).** A permission mode between Ask to Edit and Execute: the agent
works without prompts, but before a command or tool call that is not read-only runs, the
decision model is asked the three questions above; a likely yes (probability ≥ 0.5) turns
the call into an ordinary permission prompt that says why. A file write outside the project
always asks, without a model call. It can only **add** a prompt — never approve one — so
when the model cannot answer (off, shadow mode, no credential, a timeout of ~2.5–4 s) the
call asks, saying it could not be checked. Clef-flash answers in ~0.8–1.5 s; if checks
often come back unchecked, raise `decide.timeout_ms` (default 1500) to 2500. The mode is offered only while
`decide.enabled` is on and `guard_tool_call` is `on`; otherwise a Guarded session behaves as
Ask to Edit. What is sent: the tool name, the command or clipped arguments and the project
directory, redacted when `decide.redact` is on (the default). Unlike the uses below it is
not compare-only: in shadow mode every call would ask.

It runs on **your own Cloudflare account**, the one connected for Lighthouse, and it is
**off by default**. Whenever it is off, unsure (below `decide.min_confidence`) or
unavailable, NAVIG decides exactly as it did before. Workers AI bills Clef-flash at $0.09 per
million input tokens, and the free daily allowance covers a few thousand decisions.

```bash
navig decider                         # status: switches, credential, today's usage, last call
navig decider test "Checkout is failing for every customer" \
  --yesno "urgent=Is this urgent?" --choice "team=billing,technical,sales"
navig decider on                      # switch it on (every use)
navig decider off --use telegram      # switch one use off (id or prefix)
navig decider use clef                # pick the model: clef-flash | clef
navig decider shadow on               # log its answers, keep the old logic deciding
navig decider off                     # everything back to the previous method
```

**Shadow mode: proving a use before it decides.** Each use runs in one of three modes:
`on` (Clef decides), `shadow` (Clef answers and is compared with the logic that decides
today, which still decides) and `off`. Every use added after the first three starts in
**shadow** and is **compare-only**:

| Use id | Compared with |
|---|---|
| `telegram_brief` | ONE call per Telegram message answering mode, depth, system intent, space intent, reminder and settings intent, each compared with its own classifier |
| `judge_confirm` | the exact-English yes/cancel word lists for a pending action (so "да" or "oui" counts as "other" today) |
| `judge_reply_quality` | the length/hedge rule that decides whether to retry on a stronger model |
| `judge_continuation` | the keyword scorer behind auto-continue |
| `judge_action_safety` | the verifier's self-reported verdict (only while the verifier is on) |
| `judge_goal` | the model's own "did I achieve the goal?" |

```bash
navig decider status                  # "Agrees (7d)" per use
navig decider review                  # the recent disagreements, with redacted text
navig decider shadow on --use inbox   # put a live use back under comparison
navig decider off --use judge         # stop comparing (every judge_* use)
```

None of these comparisons makes the bot wait: they run in the background, and Telegram
keeps handling one message at a time exactly as before.

**Permission:** Clef needs Workers AI access on your Cloudflare credential. A
`navig lighthouse login` made before this feature lacks it, so re-run the login once. If you
use an API token instead, add **Workers AI: Read** to it. `navig decider status` and
`navig doctor` (the "Decision model (Clef)" row) tell you which applies.

Settings (`navig config set decide.<key> <value>`): `enabled` (false), `model` (clef-flash),
`use.<id>` (true), `min_confidence` (0.5), `timeout_ms` (1500), `daily_cap` (2000 calls per
UTC day, 0 = no cap), `shadow` (false), `redact` (true; strips emails, phone numbers and
secrets before sending), and `account_id` (defaults to Lighthouse's). For example,
`navig config set decide.enabled false` switches it off from anywhere. The deck exposes the
same switches under **Settings → Decision model**.

---

<a id="27b-navig-reflex"></a>
## 27b. NAVIG Reflex — `navig endpoints` + `navig ask` plan/commit

One typed device model, one confirm-first router and one Answer object, layered over the shipped
home/voice plugins. Full contract: [`docs/reference/aria-unified.md`](../../../docs/reference/aria-unified.md).
Every command emits an **Answer** envelope (`ok, speech, display_text, table, json, directives,
suggestions, data, plan, intent`); `--json` prints exactly one document.

### 27b.1 `navig endpoints` — typed projection of the home inventory

`~/.navig/home/devices.yaml` stays the only inventory; endpoints is a read-through view (no second
store, core parses no YAML, no provider imports).

| Command | What it does |
|---|---|
| `navig endpoints list [--provider P] [--room R] [--json]` | One row per controllable thing: `id` (`provider:device`), `type`, `room`, `online`, typed capabilities + live state. `--json` carries the records. |
| `navig endpoints show <id> [--json]` | One endpoint: live state, parameters and the directives it supports. |
| `navig endpoints set <id> <directive> [value] [--json]` | Apply one directive through the home plugin. **Idempotent**: a replay reports `changed:false` and does not call the provider twice. |

Directives: `on_off`, `set_range`, `set_mode`, `set_color`, `toggle`. Values: `on|off`, a number
(percent / Kelvin / Celsius), `#rrggbb`, or a mode name.

```powershell
navig endpoints list --json
navig endpoints list --provider govee --room office
navig endpoints show govee:desk-lamp
navig endpoints set govee:desk-lamp on_off off
navig endpoints set govee:desk-lamp set_range 40
navig endpoints set govee:desk-lamp set_color "#ff8800"
```

Vocabulary is deliberately small (5 capability types + 3 read-only properties): `vacuum`, `media`,
`volume`, `ir`, `dp` are reported as `data.unmapped`, not modelled.

**Exit codes:** `0` success · `2` usage error (unknown directive, or a capability the device does
not declare) · `1` home plugin missing/not ready (the error carries the install hint).

### 27b.2 `navig ask` — deterministic plan → commit

The existing `navig ask` gains four additive flags; with no matching tool the question falls
through to the assistant exactly as before (no dead end, `--json` is deterministic-router only).

| Flag | Behaviour |
|---|---|
| `--plan` | Preclassify, resolve the target, print **tool · target · blast radius · profile · commit_token**, perform nothing. |
| `--commit <token>` (`-c`) | Finish a planned action: runs **exactly once**, replay returns the stored Answer and exits 0. |
| `--profile safe\|fast\|campaign` (`-p`) | Routing preset (default `fast`). Unknown name → exit 2. |
| `--json` | One Answer document (plan metadata in `Answer.plan`). |
| `--execute` | Kept for compatibility: with `--plan`, preview then execute the planned tool. |

```powershell
navig ask "turn off the desk lamp" --plan              # nothing changes
navig ask "turn off the desk lamp" --commit <token>    # exactly once; replay = no-op
navig ask "set the desk lamp brightness to 40" --profile fast
navig ask "turn off everything" --plan                 # destructive batch: plan only
navig ask "status" --json                              # one Answer, no assistant call
```

**Profiles** are approval presets, not quality weights: `safe` and `campaign` plan every mutation
(`--commit` needed); `fast` auto-commits one reversible action and plans destructive batches;
`approval.security_level = high` forces plan-only even for single mutations. Destructive/batch
actions are plan-only in **every** profile.

**Token store:** tokens live in the existing RuntimeStore (`~/.navig/runtime.db`, table
`commit_plans`, TTL 300 s, claim-once, stored Answer result); no new database. Unknown/expired
token → exit **2** with an actionable hint.

Shipped tools (10): `home.on`, `home.off`, `home.toggle`, `home.set`, `home.status`, `tv.power`,
`tv.volume`, `tv.play`, `assistant.status`, `campaigns.status` — each bound to a real `navig …`
command, so the router can never shell a command that does not exist.

### 27b.3 Campaign integration (read-only)

`tasks.db` stays the single campaign writer. To turn a routed result into campaign evidence, write
the Answer inside the campaign workspace and cite it with the existing checkpoint verb:

```powershell
navig tasks campaigns checkpoint <campaign-id> --evidence <workspace>/answer-<token>.json
```

No second policy language, no second store; the live campaign lane stays off until the owner
enables `automations.campaigns.auto_run`.

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
yet, empty ledger, legacy pre-chain file) · `1` = chain broken, or a `--path`
file that does not exist (nothing was verified). A relative `--path` resolves
in the directory you ran the command from.

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

`notify` actions go through the notification pipeline — routing matrix, quiet hours,
fan-out and delivery verification — like every other alert. The Telegram chat is
resolved by the pipeline (the type's route → notify target → `telegram.allowed_users[0]`);
action params `target`/`chat_id` no longer choose the destination (a fire logs a warning
if they are set). To pick the chat: `navig notify route set system_alert <chat>`.

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
| `pack install <ref>` | `navig install add <ref>` (community assets) · `navig store install` |
| `pack uninstall <name>` | `navig install remove <name>` · `navig store remove` |
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
navig formation council "Should we migrate to microservices?"

# Control rounds and timeout
navig formation council "Budget allocation" --rounds 2 --timeout 60

# JSON output for scripting
navig formation council "Tech strategy" --json
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

For specialized topics, see these detailed guides (names kept for search; only PRODUCTION_DEPLOYMENT lives in this tree):

| Document | Description |
|----------|-------------|
| `AUTONOMOUS_DEPLOYMENT.md` | **Complete guide for 24/7 Telegram bot deployment** - VPS, Docker, Windows service |
| `ARCHITECTURE_GAP_ANALYSIS.md` | NAVIG vs Reference Agent feature comparison & roadmap |
| `AGENT_MODE.md` | Full agent architecture and component details |
| `AGENT_SERVICE.md` | Service installation (systemd, launchd, Windows) |
| `AGENT_SELF_HEALING.md` | Auto-remediation and recovery system |
| `AGENT_LEARNING.md` | Pattern detection and learning system |
| `AGENT_GOALS.md` | Goal planning and task decomposition |
| [PRODUCTION_DEPLOYMENT.md](../dev/PRODUCTION_DEPLOYMENT.md) | Production deployment checklist |

## 36. Finance (`navig finance`)

There is no finance CLI. Finance lives in the **NAVIG Desktop Finance app** (the `finance`
module — money, invoices & bizops, gated by the Harbor tier):

```bash
navig store info module:finance    # the module, its tier lock and its surfaces
```

`navig finance status` / `navig finance balance` were never implemented (the beancount
journal verbs this chapter used to list never shipped); they now print the pointer above and
exit 1.

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
navig cdp new                    # throwaway profile, auto-allocated port — WINDOWLESS
navig cdp new --profile research # reusable named profile (logins persist)
navig cdp new --headed           # opt in to a visible window (when you want to watch)
navig cdp new --window-size 1440x900  # fixed viewport (CI / deterministic shots)
```

**Windows are opt-in.** A browser launched by an agent or a script is windowless; the flows
where a person signs in (`navig cdp open <profile>`, `navig do`, `navig gmail`) keep their
window. `--headless`/`--headed` override per call, `browser.headless` overrides globally.
Idle automation browsers are closed by the `browser_reaper` monitor — never a named profile,
and never one with a visible window. See `docs/automation/cdp-connector.md`.

### Commands

| Command | Description |
|---------|-------------|
| `navig cdp status` / `targets` | Show launchable apps + live CDP targets (`kind: browser/node`) |
| `navig cdp launch <app>` | Start an app with a debug port (`--force-restart`, `--user-data-dir`) |
| `navig cdp new` | Fresh isolated browser — own profile + auto port, **windowless by default** (`--app`, `--profile`, `--headed`, `--headless`, `--window-size WxH`) |
| `navig cdp launched` | List browsers NAVIG started and whether each is live |
| `navig cdp profile usage` | Disk used by each browser profile (named, orphaned, throwaway) |
| `navig cdp profile prune [name…]` | Reclaim disk — throwaway sessions, plus any profile you name |
| `navig cdp profile vacuum [name…] \| --all` | Reclaim disk **without losing logins** — caches and Chrome's on-device AI model |
| `navig cdp stop` / `detach` | Close (`--port`/`--all`) or just disconnect a debug browser |
| `navig cdp tabs` / `switch` | List every open page / make one the active target |
| `navig cdp snapshot` / `screenshot` | a11y tree with refs / capture the page |
| `navig cdp click` / `type` / `key` / `scroll` / `move` | Interact (by `--ref`, `--xy`, or focused element) |
| `navig cdp eval` / `nav` | Run JavaScript (gated) / navigate a page |
| `navig cdp download [--ref N \| --selector CSS] [--out-dir DIR]` | Capture a file download from the page (name sanitised, never overwrites, `--max-mb` cap) and print its path, size and SHA-256 |
| `navig cdp login <domain>` | Auto-login from a vaulted website credential (gated) |
| `navig cdp inject <script\|@file>` | Register a persistent userscript; a `.user.js` header (`@match`, `@run-at`) is honoured |

Every action targets a tab with `--tab <index>` or `--url <substring>`; all support
`--port` (default 9222) and `--json`.

### Lenses — your userscripts, in every NAVIG browser (`navig lens`)

A **Lens** is a Tampermonkey-compatible `.user.js`: a `==UserScript==` header (`@match`,
`@include`/`@exclude`, `@run-at`, `@noframes`, `@require`, `@grant`) plus code. Install it
once and it runs in every NAVIG browser host. Any script from Greasy Fork or OpenUserJS is a
valid Lens; NAVIG adds optional `@navig-id` / `@navig-capability` keys. Full spec:
`docs/lenses.md`.

| Command | Purpose |
|---------|---------|
| `navig lens list [--json]` | Installed Lenses: state, run-at, origin, sites |
| `navig lens add <file.user.js \| https://…> [--disabled]` | Install or update (same `@name` + `@namespace` = update) |
| `navig lens show <id> [--url <url>] [--source]` | Details, warnings, and whether it runs on a URL |
| `navig lens enable <id>` / `disable <id>` | Turn a Lens on / off (stays installed) |
| `navig lens export <id> [-o file]` | The original `.user.js`, byte-for-byte |
| `navig lens remove <id> [--yes]` | Delete it and its cached `@require` files |
| `navig install add lens:<owner>/<repo>/<path>` | Install from GitHub (a `<path>/lens.user.js` or a `.user.js` path) |
| `navig lens review [<id>] [--json]` | What waits on you; with an id, the diff and what the change would newly be allowed to do (▲) |
| `navig lens approve <id>` / `reject <id>` | Apply a staged change or turn on a proposal / discard it (a rejected update is not offered again) |
| `navig lens update [<id>] [--json]` | Check install links now. Stages new versions for review — applies nothing; exit 1 if a link could not be checked |

```bash
navig lens add https://greasyfork.org/scripts/12345/code/script.user.js
navig lens show my-lens --url https://www.youtube.com/
navig install add lens:navig-run/registry/lenses/youtube-hide-shorts
```

- A Lens with **no `@match`/`@include` runs nowhere** (with a warning) — never everywhere.
- **Nothing reaches a page unseen.** A new Lens written by the **agent** is saved switched off;
  an agent's edit of a Lens you approved is **staged** beside it, and your version keeps
  running. Updates from a Lens's install link are checked **daily** (the `lens_updates`
  monitor, on by default) and staged the same way. Each pushes a **Lens reviews**
  notification; decide with `navig lens review` or NAVIG Desktop → Settings → Lenses.
- Starter Lenses: `registry/lenses/` (YouTube without Shorts, GitHub full width, Readable
  Hacker News, Wikipedia reading width).
- Supported grants: `GM_addStyle`, `GM_get/set/delete/listValue`, `GM_info`, `GM_openInTab`,
  `GM_setClipboard`, `GM_notification`, `GM_registerMenuCommand`, `GM_log`, `unsafeWindow`
  (and the `GM.*` forms). `GM_xmlhttpRequest` and `@resource` are not supported yet — the
  script sees them as `undefined`.
- Store: `<config dir>/lenses/`. API for hosts: `GET /api/deck/lenses/compiled?host=…`.

### Website auto-login (`navig cdp login`)

Store a website login once, then sign in on any attached browser — session-first, so
after the first login NAVIG restores the saved session instead of retyping.

```bash
navig vault login add github.com -u you@example.com          # prompts for the password
navig vault login add github.com -u you@example.com --totp JBSWY3DPEHPK3PXP  # + 2FA
navig vault login list                                        # no passwords shown
navig cdp new --headed                                        # a VISIBLE browser (--headed:
                                                              # you may have to solve a 2FA
                                                              # prompt or a captcha yourself)
navig cdp login github.com --open https://github.com/login   # navigate + sign in
navig vault login capture notion.so --idp-user you@gmail.com # SSO site (see below)
navig vault login discover                                   # sites you're ALREADY signed into
navig vault login capture notion.so --from-chrome            # import that sign-in, no re-login
```

- **Already signed in in your browser?** `navig vault login discover` lists the sites you look
  signed into in Chrome/Edge/Brave (it reads only cookie *names* and hosts from a throwaway copy
  of the cookie database — never cookie values, never your live profile) and prints a ready
  `capture … --from-chrome` line for each. `--from-chrome` imports that sign-in with no new login,
  by letting the real browser decrypt its own cookies for that one site. "Looks signed in" is a
  heuristic (an unexpired session cookie), and on some machines Chrome's App-Bound Encryption
  blocks the import — then it says so and points you at the interactive `capture`. Add `--browser
  edge|brave` or `--profile "Profile 4"` to target a specific browser/profile.

- **Session-first**: on success the authenticated session (cookies + localStorage) is
  captured to the vault; the next `login` **restores the session** — no password typed —
  and only re-fills the password when the session is stale.
- **SSO sites ("sign in with Google/GitHub")**: there is no password to store — only a
  browser session. `navig vault login capture <domain>` opens a persistent per-site
  profile, you complete the sign-in yourself (Google/GitHub buttons, 2FA, captcha), then
  Enter captures the session and records which IdP owns the account (`--idp` optional —
  inferred from the session's cookies; `--idp-user` is your address at the IdP, stored as
  linkage, never a password). `login list` shows a `Sign-in` column (`password` / `google`
  / …), and an existing password login is never overwritten by a capture. When the session
  later dies, `navig cdp login` reports `session_expired` with the re-capture command
  instead of trying to fill a password that doesn't exist. The same flow is driveable
  from the GUI (NAVIG Desktop → Vault → Website logins) over `/api/deck/vault/logins/capture`
  with `--capture-only`; cookies are only ever saved while the page is on the target's
  registrable domain, so confirming while parked on the IdP saves nothing. A capture also
  links a matching stored API credential (`navig vault add <provider> …`) onto the record
  (`api_ref`, shown as `API access` in `login get`) — when the session later expires the
  error says so: agent-side automation keeps working without the browser.
- **Origin-bound + https-only**: a credential fills **only** on its registrable domain
  (eTLD+1, public-suffix aware) after IDN/punycode normalisation — never on a look-alike
  (`g00gle.com`), a suffix trick (`github.com.evil.com`), or a cross-origin iframe. The
  password is injected server-side; it never enters an AI's context or a log.
- **2FA**: if the login stored a `--totp` secret, the engine fills the one-time-code step
  itself (RFC 6238). SMS / app-approval 2FA (no stored secret) still needs you.
- Status values: `session_restored`, `logged_in`, `filled` (2FA/captcha pending),
  `session_expired` (SSO-linked account, dead session — re-capture),
  `no_credential`, `needs_disambiguation` (pass `--username`), `wrong_origin`.

### Breach checks (`navig vault breach`) — is a vault email in a leak dump?

Checks the email usernames stored as website logins against leak dumps through the
**CyberAIGen** connector. Opt-in twice: nothing is scanned until a token is connected,
and the automatic monitor starts only when you enable it. No vault identifier leaves
the machine before both are true.

```bash
navig connector connect cyberaigen          # paste the personal API token (vault key
                                             # `cyberaigen/token`, env CYBERAIGEN_TOKEN)
navig vault breach check                    # scan every email login now (stores findings)
navig vault breach check --email you@x.com  # one address
navig vault breach list                     # stored findings — passwords masked
navig vault breach list --reveal            # …unmasked (also: --json for scripts)
navig vault breach status                   # token, monitor, last cycle, counts
navig config set monitors.breach.enabled true   # turn on the daily monitor
```

- **Notifications never carry the record list.** The monitor raises one `breach_alert`
  per cycle with counts and *masked* passwords (`a******9`); the full list is the CLI.
- **Findings live in the vault** (`breach/state`), encrypted like every other secret —
  they include leaked passwords, so plaintext on disk would be a downgrade.
- **Credits**: each hit costs one CyberAIGen scan credit; clean emails are free. The
  monitor scans a rotating batch (`monitors.breach.batch_size`, default 25) every
  `monitors.breach.interval_hours` (default 24) so a big vault stays bounded.
- A failing monitor tells you once per outage (`system_alert` after 3 failed cycles),
  not once per cycle; `navig vault breach status` shows the last error.

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
navig cdp profile usage                                # disk used by each profile
navig cdp profile prune                                # reclaim throwaway session dirs
navig cdp profile prune gaze-books                     # delete a named profile you're done with
navig cdp profile vacuum --all                         # caches + Chrome's 4 GB AI model go; logins stay
```

- **Reuse, no reopen**: `open` attaches to the already-running profile on its stable port.
- **Disk**: an automation profile is a full Chrome user-data dir (caches, service workers,
  IndexedDB) and grows without bound — `usage` shows where the gigabytes went, including
  *orphaned* dirs no profile points at any more. **Reach for `vacuum` first**: Chrome
  downloads its on-device AI model (~4 GB) into *every* profile it is launched with — on the
  machine this was written for, 8.4 GB of an 11.7 GB footprint was that one model, twice,
  against 7 MB of actual logins. `vacuum` deletes only what Chrome rebuilds (the model,
  caches, shader and extension caches) and keeps every cookie and login; new launches no
  longer download the model at all. `prune` is the destructive one: it only ever deletes
  what you name plus throwaway sessions. Both refuse a profile that is **running**, one
  another session's browser is using, and — always — a `--real` profile, since that
  directory is your actual Chrome data.
- **Closing is a request, then a kill**: `close`, `stop`, `stop --all` and the idle reaper
  first ask the browser to shut down cleanly (CDP `Browser.close`) and wait for it, and only
  then kill. A kill drops whatever Chrome has not flushed yet — measured: a cookie set one
  second before a kill was gone, before a clean close it survived — so a `cdp login`
  followed by `stop` keeps the login it just made.
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

### Your own browser, for agents (`navig dock`, `dock_*` MCP tools)

`navig cdp` drives a **separate** debug browser. To let Claude Code, Codex or any MCP client
work in **your everyday Chrome** — your logins, your tabs — use the NAVIG Dock extension:

1. In the Dock: **Settings → NAVIG daemon → Let AI agents use this browser**. The Dock pairs
   itself with NAVIG on this computer (the first extension to pair is trusted; any other
   extension is then refused by the daemon) and keeps one connection open.
2. Point your agent at NAVIG's MCP server, e.g. Claude Code:
   `claude mcp add navig -- py -3.13 -m navig.mcp_server`
3. The agent uses `dock_status`, `dock_tabs`, `dock_open`, `dock_snapshot` (with `since`
   for a diff), `dock_text`, `dock_act` (up to 12 steps in one call: click · type · fill · key ·
   scroll · hover · select · navigate · wait · wait_for · extract · upload · get_text — each
   aimed at a `[ref_N]` or a locator: role + name, testid, css, has_text, xpath), `dock_screenshot`,
   `dock_console`, `dock_network` and `dock_release`.

Agents only touch tabs in the **NAVIG agents** tab group — the ones they open, plus any tab
you drag into it. While one works, the page glows and names it ("Claude Code clicking …");
**Stop** or **Esc** halts it and pauses all agents until you press **Resume** in Settings.
Typing into a password / card field and uploading a file ask you on the page (**Allow /
Deny**). Two agents never act in the same tab at once: the first one to act holds it until it
releases it (or 10 idle minutes pass).

```bash
navig dock status          # paired extension, connected/paused, latency
navig dock status --json
navig dock unpair --yes    # forget the pairing (the Dock pairs again when switched on)
```

## 39. NAVIG Ecosystem Products

### Landing Page (`packages/landing`)
- **Stack**: Next.js 16, React 19, Tailwind v4, static export
- **Port**: 7003 (dev server)
- **Routes**: `/` (marketing site), `/deck` (NAVIG Deck demo), `/os` (NAVIG Desktop demo)
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
Unified component library used by both NAVIG Deck (browser extension) and NAVIG Desktop (desktop overlay):
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
- **Avatar Companion**: Tamagotchi-style animated sidebar avatar that reacts to extension state (idle, thinking, speaking, working, success, error). Uses 24 Chappy firmware sprite frames with CSS transition animations. See `AVATAR_INTEGRATION.md` (not in this tree).
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
- **See**: `voice-services.md` (not in this tree)

#### Read aloud — a text read to you like a book (`navig audio read` · Telegram `/read`)

In bed, send a text and it is read to you: numbered parts of about ten minutes that arrive as
Telegram's own audio player — the first within about a minute, the rest as they are made, so
you can fall asleep to part 1 while part 4 is still being generated. Quiet and unhurried
(pace 0.9, steadier delivery, loudness well below the podcast level). Provided by the
`navig-audio` plugin; ElevenLabs, **one credit per character**.

| Where | Gesture |
|---|---|
| Telegram | `/read <text>` — a short text (a message is capped at 4096 characters) |
| Telegram | send a **`.txt` / `.md` file with the caption `/read`** — the way in for a long text (any code page: UTF-8, UTF-16, cp1251, cp1252) |
| Telegram | `/read stop` cancel · `/read go` confirm a quoted big one · `/read status` · `/read go` as the file's caption confirms up front |
| Agent | "read this to me" / "почитай мне" / "lis-moi ça" → the `read_aloud` tool (short texts); `action='stop'` cancels |
| Terminal | `navig audio read --file chapter.txt` · `... -` (stdin) · `--dry-run` (cost, spends nothing) · `--voice` · `--speed` · `--part-min` · `--out` · `--json` |

**Cost rules.** The quote is shown before anything is spent and is *after the cache* (a chapter
already read quotes 0). A reading the account cannot afford is refused outright. Above
**15 000 credits** nothing starts until you say yes (`/read go`, `--yes`, or `confirmed` from the
agent — which needs no text re-sent). One reading per chat; every chunk is cached by content, so
a retry, a re-send or a resume re-buys nothing. Stop works between chunks: nothing is generated
(or charged) after it.

**Config** (`navig config set …`): `audio.read.voice` (an id, or the *name* of one of your
voices — `navig audio voices`), `audio.read.lang` (`en|ru|fr`; default detected),
`audio.read.speed` (0.7–1.2), `audio.read.part_minutes` (default 10).

Files land in `<audio dir>/read/<id>/` as `read-<id>-part-01-of-04.mp3`. The `/read` command can
be switched off from `/extensions` ("Read aloud"). None of this goes through the approval gate —
`/read` is a command you typed and `read_aloud` is not a destructive tool — so the cost rules above
are the only brake: a small reading starts at once, a big one waits for your yes.

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
**See**: `docs/MATRIX_BRIDGE_SETUP.md` (not in this tree)

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

There is no boot sequence to configure: NAVIG starts as the background service.

```bash
navig service status     # is it running, and how was it started
navig service install    # start NAVIG at login
navig service start
```

`navig boot show` / `navig boot run` were never implemented (the session-diary verbs this
chapter used to list never shipped); they now print the `navig service` pointer and exit 1.

---

## 42. Contextual Namespaces (`navig space`)

Manage *spaces* — contextual namespace bundles that group workspace settings, tools, and overlays for a specific project or role.

| Command | Description |
|---------|-------------|
| `navig space list` | List spaces across every root, with scope and enabled/active markers |
| `navig space init [name]` | Create a new space. With no name it uses the folder you are standing in — for both the name and the location (`create`/`new` are aliases) |
| `navig space use <name>` | Activate a space (`switch` is the canonical name; `use` is its alias) |
| `navig space current` | Show the active space (`NAVIG_SPACE` override respected) |
| `navig space rename <space> <new-id>` | Change a space's id everywhere it lives — manifest, registry row, active pointer. The folder stays unless you pass `--move-folder`, which matters under `~/.navig/spaces`: a folder there is addressed BY NAME, so the old name keeps resolving to the space until it is moved too (`--dry-run` previews) |
| `navig space doctor [path]` | Check a space and report what's present vs missing, then offer the next step (`check` is an alias; `--fix` repairs) |
| `navig space audit` | Audit the whole spaces collection for structural drift (`lint` is an alias) |
| `navig space books [name]` | Show or set the finance BOOK this space keeps its ledger in (`--clear` for the default) |
| `navig space register <path>` | Register an external `.navig/` folder so it shows in the deck |
| `navig space forget <name>` | Remove a space from the registry (the folder stays) |
| `navig space enable / disable <name>` | Show or hide a space in the deck and switcher (a hidden folder still works when you are in it) |
| `navig space fold / unfold <path>` | Demote a sub-space so it never claims a top-level id, and undo that |
| `navig space link <a> <b> [--include …] [--exclude …]` | Authorize another Space's knowledge into this one — a logical read edge recorded in `<a>/.navig/space.json`, nothing copied and no folder moves (`--profile` names a reading profile; `unlink` removes the edge; both take `--json`) |
| `navig space parent <a> <b>` | Set `<a>`'s logical parent — hierarchy without relocation. `navig space parent <a> --clear` removes it; self-parenting and cycles (a chain that would reach `<a>` again) are refused with the chain named |
| `navig space related [space]` | One table of a Space's parent, links, children and inbound links, with availability (`●`/`○` + reason for deleted or unregistered targets); default is the active space |
| `navig space context [space] [--for <query>]` | Exactly what an agent may read from the linked Spaces — per-file provenance (`space · path · mtime`), parent first then links, under include/exclude globs, the deny list (vault/env/credentials/sessions/state/databases/logs), budgets and content dedupe (`--json` for machine output). `--brief` is the SessionStart-hook shape: ≤10 lines with `●`/`○` per edge and a file count — and it never fails (degrades to the active-space line) |
| `navig space wire` | Wire this folder into the agent ecosystem (alias of `navig wire`) |
| `navig space install <source>` | Install a space bundle from the community registry (GitHub-backed) |
| `navig space delete <name>` | Delete a space and all its contents (confirm-gated) |

**Examples:**
```bash
navig space list
cd /srv/devops-prod && navig space init   # Turn THIS folder into a space named after it
navig space init devops-prod    # Or: create "devops-prod" under ~/.navig/spaces
navig space init devops-prod --path .     # A chosen name, in the current folder
navig space use devops-prod     # Activate it
navig space current             # Which space is active
navig space rename devops-prod prod   # Re-id it; `navig space use prod` from now on
navig space rename prod prod --move-folder   # also rename the folder under ~/.navig/spaces
navig space doctor              # Is this folder a healthy space? (--fix repairs)
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

Bookmark imports are persisted into the legacy links database by default (kept for one
release); the bookmark manager itself is now `navig bookmarks` — see §54, which copies that
database in automatically.

**Examples:**
```bash
navig import --source all --output results.json
navig import --source chrome --path /custom/path/Bookmarks
navig import list-sources
```

### Related Commands

- `navig bookmarks import <file>` imports native browser bookmark files and HTML exports with
  their folders (§54). `navig links …` is a deprecated alias that forwards there.
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
| `navig telegram tidy` | Daily chat tidy status — clears yesterday's routine bot messages overnight, incl. sales rings, the daily sales summary and briefings (`telegram.daily_clean.enabled/hour/scope/routine_types`) |
| `navig telegram tidy --now [--dry-run]` | Sweep everything recorded before today right away (`--json` for scripts) |
| `navig telegram tidy --kinds` | Per kind (greeting · tip · spark · reminder · habit_card · body · checkin · briefing · sales · deploy · deletion · notice): swept or kept, and how many wait |
| `navig telegram tidy --keep <kind>` / `--sweep <kind>` | Leave a kind in the chat for good / sweep it again (`telegram.daily_clean.keep_kinds`) — also in the deck and NAVIG Desktop → Settings → Messaging |
| `navig telegram tidy --hour 6 --on` | Run after 06:00; `--off` stops the nightly sweep |
| `navig telegram tidy --system` / `--no-system` | Daily sweep of SYSTEM cards — approval prompts, config-health notices, System Briefing, `/status`, `/plans`, OCR bubbles (`telegram.system_cleanup.enabled`, **on by default**; `--system-hour N` sets its hour, default 04:00). Never touches your messages or the agent's replies |
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
you switch it back on. Because the schedule is untouched, `navig habit list` and
`/habits` show a banner saying the reminders are scheduled but not delivered,
rather than reporting a healthy count into a void.

**The evening journal.** When the day closes, Habits sends a card inviting you to
write about your day — free form, as much or as little as you like. Reply to that
card (typing or a voice note) and it lands in `journal/<date>.md` in your space;
send `skip` to leave the day without an entry. There is no fixed form: it used to
ask three numbered questions, which reliably collected three sentences and not a
day.

**Reading the journal back (off by default).** NAVIG can follow your entry with a
short reflection — what the day's emotional thread was, phrases of yours worth
noticing, what you returned to or stepped around, and at most one question. It is
**opt-in**, because it sends a private journal entry to your configured model:

```bash
navig config set journal.reflect true     # off by default
navig config set journal.reflect false    # back off
```

It never diagnoses, never uses clinical or disorder language, and never advises
unless you asked — it reflects what is on the page. Short entries are left alone,
and if the model is unavailable you are told so: the entry is written and
confirmed *before* any of this runs, so a reflection can never cost you the
record of your day.

**The week, read together.** With the same switch on, Sunday's read-back is
followed by a second one over the whole week — what you kept coming back to, what
shifted from Monday to Sunday, phrases of yours from different days set side by
side, and at most one question about the week ahead. It needs at least two
entries in the week to say anything. `navig habit review` carries the same
weekly reflection under **Reading the week back**, and `--write` keeps it in
the journal. Reviews written into the journal are never fed back in as if you
had written them.

**Which model reads it.** By default the read-backs use your `big_tasks` mode
(the quality tier, not the fast chat one). To keep the journal off hosted APIs
entirely, pin it to a local model — this affects the journal and nothing else:

```bash
navig config set journal.model ollama:llama3      # any provider:model spec
navig config set journal.model ""                 # back to the big_tasks mode
```

**Health is a separate switch.** `/health`, `/weigh` and `/body` belong to the
**Health** extension, not Habits — so turning Habits off leaves the body check-in
running, and vice versa. Health behaves the same way when switched off: the
morning weigh-in and the Sunday card stop being delivered, the schedule and every
measurement already in `metrics.csv` are left exactly as they are, and the
surfaces show the same "scheduled but not delivered" banner.

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

Every bump also folds `changelog.d/` fragments and rotates `[Unreleased]` under
`## [X.Y.Z] — <date>` in the same release commit (the consumed fragments' deletions
included). Then `bash tools/release.sh X.Y.Z --publish` verifies the wheel (real-install
smoke), tags, publishes to PyPI (the tag workflow cannot run — org Actions is
billing-blocked), creates the GitHub Release with the artifacts attached, and records the
verified asset in `latest.json`. It refuses a version `pyproject.toml` does not carry. An empty `[Unreleased]` with no fragments refuses the bump — a version heading
over nothing is a lie — and `--no-changelog` is the deliberate hatch for a release that
genuinely carries no user-facing entries. `release:dry` shows the changelog plan too.

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
#   a lock left by a `worktree add` that died (`locked: initializing`) is cleared for you;
#   any other lock is named — `git worktree unlock <path>` if it is yours
navig repo gates                # every CI gate on this machine: queue # · project · since · ETA · step
navig repo gates schema         # only that project's gates — a NAME, not a path (also: gates navig)
navig repo gates --watch 5      # redraw every 5 s until Ctrl-C
navig repo gates --json         # machine-readable (lane holders + queued gates, with health state)
navig repo gates --prs          # THIS repo: each open PR's gate-result, the land queue, the last landing, why one is blocked
navig repo gates --no-github    # local lanes only (offline) · --deep also samples the disk queue
#   health: running · slow · reused (pid recycled — lane blocked) · dead/expired (lane free) · queued
#   a job owned by another account (the runner service) shows `running (other user)`; lanes: main · overflow · express · push
#   ETA is an estimate from each label's typical lane time; a narrow terminal drops PID, then
#   Since, then Gate, rather than truncating every column
#   Fleet block (default view): self-hosted runners (idle/busy/offline + local service state, free
#   workers), Actions jobs running/queued with their wait, the bottleneck (lane-starved · cpu-bound ·
#   io-bound · github-queued · hung-step · none), a typical-gate-time shown ONLY from >= 5 agreeing
#   runs (otherwise `—` and why), and this month's Actions spend vs github.ci_budget_usd (default 10)
npm run land -- --queue         # push · open the PR · label `land` · return; the lander integrates it (docs/dev/ci-local.md)
navig vm push <vm> <files...> --to <dir>   # scp into a VM with its own key + known_hosts
navig repo conflicts            # simulate merges between EVERY pair of worktrees
navig repo conflicts --json     # machine-readable; exit 2 when any pair conflicts
navig repo conflicts --no-dirty # committed state only (default includes dirty)
navig repo stale                # leftover worktrees / unmerged branches / stashes / orphan dirs
#   an unmerged branch carrying changelog.d/ fragments is marked: user-facing work waiting
#   a worktree still locked `initializing` is an add that DIED (partial tree) - flagged, with the fix
navig repo stale --json
navig repo lock                 # who holds the main-checkout agent lock
navig repo lock release [--force]
navig repo prune                # dry-run: list orphaned .dev/worktrees dirs git no longer tracks
navig repo prune --yes          # delete them (safe: skips live worktrees + locked dirs, says why)
navig repo prune --yes --force  # also delete live worktrees (may hold uncommitted work) — rare
navig repo sweep                # dry-run: local branches PROVABLY already on origin/main
navig repo sweep --yes          # delete them (prints each sha; the reflog keeps the commit)
navig repo sweep --no-github    # ancestry + tree proofs only (skip the gh PR lookup)
navig repo land <branch>        # dry-run: the teardown plan for a MERGED branch
navig repo land <branch> --yes  # delete it locally, on origin, and its worktree (refuses if unmerged)
```

All of these (`new` / `remove` / `conflicts` / `stale` / `lock` / `prune` / `sweep` / `land`) accept `--repo
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
- **`sweep`** deletes local branches whose work is **provably** already on the
  remote default branch — the debris that `stale` reports but nobody clears.
  Measured in one repo: 8 → 20 branches in eleven days, 37 at the worst, 28 of
  them already merged. Three proofs, any one sufficient: the tip is an
  **ancestor** of `origin/main`; the tip **tree** is identical to it (a
  squash-merged branch has commits main lacks and contents main already has);
  or GitHub records a **merged PR** that landed exactly this tip (`gh`, optional
  — without it that class is simply not claimed, and the command says so).
  Everything unproven is **kept** and listed with its ahead-count and PR state
  (`#1094 CLOSED`, `—`), so the decision left is a human one about real work.
  It judges against `origin/main` after a best-effort fetch, **not** local
  `main` — which is exactly the ref that goes stale in a shared checkout
  (measured 107 PRs behind). Dry-run by default (`--yes` to delete); the default
  branch and any branch checked out in a worktree are never touched; each
  deletion prints the sha it removed, and git's reflog keeps the commit. A failed
  fetch or a missing `gh` can only make it keep **more**, never delete more.
- **`land`** finishes a MERGED branch completely — the merge-and-delete contract's
  other three steps as one verb. `gh pr merge --delete-branch` run from a worktree
  merges, then FAILS its remote delete (it can't check out the default branch a
  worktree holds), leaving the branch on `origin` and its worktree behind — measured
  on four consecutive PRs. `land` deletes the origin ref, removes the worktree, then
  deletes the local branch. It does **not** merge (that is the reviewed step) and
  **refuses** a branch not provably on `origin/main` (ancestor tip, identical tree, or
  a MERGED PR that landed exactly this tip — the same proofs `sweep` uses), so it can
  never delete unmerged work. Dry-run by default; the reflog and `refs/pull/N/head`
  keep both sides recoverable.

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

---

## 51. NAVIG Desktop in a browser (`navig os`)

The desktop build is not released yet, but the same server already serves the whole NAVIG
OS interface to a browser. `navig os serve` turns that into one command instead of four
environment variables.

**The server runs on your machine and the browser is a window onto it.** That is why it can
open your files, spaces and sessions at all — and why nothing of yours travels through
`navig.run`.

**Use it from your phone or any browser: pair once.** In NAVIG Desktop open
**Settings → Remote access → Pair** (or run `navig os pair`) and scan the QR code with the
phone's camera. The phone opens NAVIG Desktop on this computer at `os.navig.run`, with no IP
address, no ports and nothing to copy. It goes through your **own** Lighthouse relay on
Cloudflare (set it up once with `navig lighthouse login`), which NAVIG reaches with an
outbound connection, so nothing on this computer is exposed and nothing passes through
`navig.run`.

- **Each device gets its own key.** It is not the deck key, is stored here only as a hash,
  and one device can be removed without touching the others (Settings → Remote access →
  Remove, or `navig os devices revoke <id>`). Removing a device disconnects it immediately.
- **New access is only created at the computer.** Pairing codes work once, for two minutes,
  and can only be made here. The Deck or Telegram can list and remove devices, never add one.
- Before the first connection the phone asks **"Connect to <this computer>?"**. If the name
  is not your computer, choose "Not my computer".
- **The off switch:** `navig config set os.remote.enabled false` (or the toggle in Remote
  access) disconnects every device at once.
- **Cost:** Cloudflare's free plan bills messages into the relay, not bandwidth (20 messages =
  one of its 100,000 free daily requests). Measured on a real phone session: opening the app
  ≈ 700 messages, leaving it open ≈ 12 a minute, one short chat turn ≈ 70 — a heavy day is
  around 20,000. NAVIG batches them and caps a day at `os.remote.daily_msg_budget` (default
  300,000 messages = 15,000 requests, 15% of the free plan, so the Telegram bot always keeps
  its share). Past it, new sessions are refused until 00:00 UTC and `navig doctor` says so;
  raise it with `navig config set os.remote.daily_msg_budget 600000` if you need more.
- A paired device can do anything you can do at this computer. The traffic is TLS to your own
  Cloudflare account, not end-to-end encrypted.

**On a new device, sign in instead of scanning.** Link this computer to your navig.run email
once — **Settings → Remote access → Account** or `navig os account link you@example.com` — and
click the confirmation link navig.run emails you. After that, on any phone or browser: open
`os.navig.run`, enter the email, open the sign-in link, and pick this computer. The computer
then **asks you** — a toast in NAVIG Desktop and an **Allow / Deny** message in Telegram (only the
owner's tap counts) — and only after you allow it does that device get its own key, exactly
like a paired one.

- **navig.run never grants access.** It stores only your email, this computer's name and its
  relay address, and signs a five-minute ticket saying "this email just signed in". The
  computer checks that ticket against the key built into NAVIG and against the email *it* was
  linked to, and then still waits for you.
- Sign-in links work once, for 15 minutes; a request you do not answer expires after 10.
- Approving works only at the computer (or in Telegram as its owner) — a paired phone cannot
  approve other devices.
- `navig os account unlink` stops new sign-ins at once; devices already paired keep working.

**A second machine running NAVIG Desktop can use this computer as its brain.** Run `navig os link`
here (or **Settings → Remote access → Brain → Make a link**), and paste the link into the other
machine's NAVIG Desktop under **Settings → Remote access → Brain → Connect to my NAVIG**. From then
on that machine shows this computer's spaces, apps and data, through your relay, and nothing
is copied to it.

- **It gets its own key.** Never the deck key: it is listed with your phones (`navig os
  devices`, "uses this brain") and removed the same way. **Disconnect** on that machine
  removes it here too.
- **It can do what you can do here,** like the deck key, except create new access: making
  links and pairing codes, approving devices and the remote-access switch stay at this
  computer. Turning remote access off cuts linked machines as well.
- It works even with `deck.telegram_only` on: that setting retires the shared deck key as a
  remote credential, and a link key is per machine and revocable.
- Links work once, for 10 minutes. Your relay must be 1.4.0 or newer
  (`navig lighthouse redeploy`).
- **Live updates arrive within about 15 seconds:** the linked machine asks this computer for
  new events every 15 s (about 5,800 relay requests a day, ~6% of Cloudflare's free plan).
- **Not over a link yet:** uploading files, and console commands longer than 30 seconds.
- **On the linked machine,** Settings → Remote access shows only whose NAVIG it uses and
  **Disconnect**: phones, sign-in and the remote-access switch are managed on this computer.

**On the same network,** `navig os serve` also prints a direct `os.navig.run` link that
carries this server's address and token (in the part of the URL browsers never send). For a
server on the same machine your browser may ask once whether the page can reach apps on your
device; allow it.

| Command | Description |
|---------|-------------|
| `navig os serve` | Build the interface and serve it, by default on `127.0.0.1:9100` |
| `navig os serve --print-env` | Show exactly what would run, secrets redacted, and start nothing |
| `navig os serve --password <pw>` | Set the web login password (stored in the vault) |
| `navig os serve --new-password` | Generate and store a fresh password |
| `navig os status [--json]` | Is it up, where is the source, are credentials stored |
| `navig os pair [--json]` | Pair a phone or browser: prints a one-time link (and a QR when `qrcode` is installed) |
| `navig os link [--json]` | A one-time link that lets another NAVIG Desktop use this computer as its brain |
| `navig os devices [--json]` | Paired devices and linked machines: last seen, connected now, messages today |
| `navig os devices revoke <id>` | Remove a device; its live session ends at once |
| `navig os account [--json]` | Which navig.run account this computer is linked to |
| `navig os account link <email>` | Link it (navig.run emails a confirmation link; nothing is linked until you click) |
| `navig os account unlink` | Stop new sign-ins through navig.run; paired devices keep working |
| `navig os requests [--json]` | Devices that signed in and wait for your approval |
| `navig os requests allow <id>` / `deny <id>` | Answer one (the same as the NAVIG Desktop toast or the Telegram buttons) |

**First run:**

```bash
navig os serve          # prints a generated password ONCE, then starts
# open http://127.0.0.1:9100 and log in
```

The token that signs your session cookie and the login password are kept in the **vault**,
not in config, and are reused on every later start — so a bookmark keeps working. If the
vault cannot be written, the command says so rather than starting with credentials that
would silently change next time.

**From your phone, or any other machine:**

```bash
cloudflared tunnel --url http://127.0.0.1:9100
```

The server reads the proxy's forwarded host and protocol and hands the browser the right
`wss://` URL by itself — no extra configuration. Prefer a tunnel to a self-signed
certificate on the LAN: browsers offer no way to accept a bad certificate *for a WebSocket*,
so the page loads and then fails to connect with nothing to click.

⚠ **A tunnel is a public door.** The login password is the only thing in front of it. Note
this exposes the *OS server* only — never bind the `navig` daemon itself to a network
interface, because it grants loopback callers an auth bypass.

**Requirements:** the `apps/os` source (this is the pre-release path — point at it with
`--dir` or `$NAVIG_OS_DIR` if it is not above your working directory) and
[bun](https://bun.sh). An explicitly named directory is authoritative: if it is not an
`apps/os` checkout the command stops, rather than quietly running a different tree.

Licensed features behave exactly as on the desktop — they are decided by your own daemon,
offline. Your licence is never checked through the website.

---

## 52. Signals — website events and Stripe sales (`navig signals`)

Turn events from sites you run into NAVIG notifications (deck, NAVIG Desktop, Telegram, …).
Full guide: [`docs/signals.md`](../../../docs/signals.md). Manage visually in
**NAVIG Desktop → Settings → Signals**.

| Command | What it does |
|---|---|
| `navig signals connect <name> --key <env-file>::<VAR> [--host <h>] [--events checkout\|elements]` | One step: uses the project's own Stripe key to create (or reuse) the webhook endpoint and stores its secret. `--host` reads a production env file on that server; the key never leaves it. `elements` = Payment Element / PaymentIntents. |
| `navig signals add <name> --provider stripe [--secret whsec_…]` | A Stripe webhook endpoint — paste the printed address into Stripe → Developers → Webhooks. No code in the site; sales arrive as **Sales**, refunds/disputes as **Finance alerts**. |
| `navig signals add <name> [--preset payment_success]` | A source your own server signs (prints its secret once). |
| `navig signals list [--json]` | Every source: on/off, kind, where it shows, hits, last event. |
| `navig signals edit <name> [--type …] [--priority …] [--title-tmpl …] [--body-tmpl …]` | Change routing/priority/templates; the address and secret stay. |
| `navig signals test <name>` | Sends a signed sample through the real path; exits 1 if nothing delivered it. |
| `navig signals rotate <name> [--secret whsec_…]` | New secret (Stripe: the one you rolled in the dashboard). |
| `navig signals remove <name>` | Delete; the address stops accepting events at once. |

A Stripe sale made while this computer is off still arrives: the edge answers 503 and Stripe
retries for up to three days. Each Stripe event is announced once, even across retries and
restarts.

## 53. Projects (`navig project`)

Every folder with a `.navig/` is a **project** — the same thing NAVIG Desktop lists under **Projects**.
A project is a registered space (no second registry): its plans, todos, docs, scripts and a
budgeted **context pack** that tells an agent what the project is.

| Command | What it does |
|---|---|
| `navig project list [--refresh] [--json]` | Every project, pinned first, then most recently active: phase, open todos, stack, git branch (`●n` = uncommitted). |
| `navig project show [id] [--json]` | One project: phase, next action, todo counts, scripts, docs (and which plan docs are missing), git, linked finance/store cards. Defaults to the project you are standing in. |
| `navig project add <path> [--scaffold]` | Register a folder. One without `.navig/` gets an empty one; `--scaffold` lays down the plans skeleton (never overwrites a file). |
| `navig project scan <dir> [--add] [--json]` | Look one level inside a folder (e.g. `C:\projects`). Lists what is there; `--add` registers the children that already have `.navig/`. A folder with only `package.json` is suggested, never written to. |
| `navig project todos [id] [--all] [--json]` | The `- [ ]` checkboxes across every plan doc as one list (`[~]` / `*(in progress)*` = in progress). |
| `navig project context [id] [--report] [--pin] [--rebuild] [--json]` | The context pack: identity, plans, open todos, agent files, README, scripts, a code outline and git — each on its own budget, with a report of what was cut. `--pin` writes it to `.navig/context.pack.md` for other agents; `--json` is how tools read it. |
| `navig project scripts [id] [--json]` | Every runnable script — root and workspace packages, pyproject, Makefile, justfile. `⚠` deploys/publishes/deletes, `↻` long-running. |
| `navig project run <script> [-p id] [--follow] [--yes]` | Start a script in the background. It keeps running if NAVIG restarts; `⚠` scripts ask first. A name that exists in several packages needs its key (`npm:apps/web:dev`). |
| `navig project ps [--all] [--json]` | What is running, with the ports it listens on. |
| `navig project logs <run> [--follow]` | A run's output. Ctrl-C detaches; the run keeps going. |
| `navig project stop <run>` | Stop a run and every process it started; exits 1 if any survived. |
| `navig project analyze [id] [--doc <path> \| --todos] [--apply] [--into <doc>] [--json]` | AI re-analysis. No flag: a brief (state, next 3 actions, risks, stale docs — staleness is measured from edit dates and commits). `--doc`: a proposed update shown as a diff, written only with `--apply` and refused if the file changed meanwhile. `--todos`: work your briefs/inbox/specs call for that you don't track yet; `--apply --into` adds it. Needs a connected AI provider. |

| `navig project ideas [id] [--add "…" [--stage inbox\|exploring\|parked]] [--json]` | Ideas not planned yet, by stage (Inbox · Exploring · Parked · Promoted · Dropped), kept in the project's `IDEAS.md` beside its plans. `#tags` work inline. Nothing is deleted — "Dropped" is a stage. |
| `navig project fabric [pattern] [-p id] [--idea "words"] [--doc path] [--sessions N] [--focus "…"] [--apply]` | The **Idea Fabric**: AI patterns over the project. No pattern lists them. `brainstorm` (new ideas, `--focus` steers), `harvest` (ideas the N newest AI work sessions about this project mentioned and never captured), `expand` (an idea → a brief in `ideas/`), `critique`, `plan` (an idea → a numbered plan doc whose tasks show up in todos). Results are proposals; `--apply` keeps them. A project adds its own patterns as `.navig/patterns/<name>.md` (frontmatter `title`, `description`, `input: project\|idea\|doc\|sources`, `output: ideas\|doc\|plan\|todos`). |

Sessions are read locally and only when a harvest runs: Claude Code plan files that name the
project's folder (`<claude home>/plans`) and its memory notes (`<claude home>/projects/<path-slug>/memory`).
Chat transcripts are counted, never read, here; searching them is opt-in through `navig recall` (§56). `CLAUDE_CONFIG_DIR` relocates the Claude home.

Folder conventions the hub understands (Schema is the reference layout): numbered plans
(`00-MASTER-ROADMAP.md`, `07-…`) keep their phase order; `plans/ideas/` is the idea bank;
`plans/briefs/` shaped work; `plans/archive/` history (shown, but never counted as todos or fed
to agents); `refs/` reference material; `proposed/` AI output awaiting review. A root `plans/`
that links to `.navig/plans/` is read once, not twice.

A project's Links show what other parts of NAVIG know about it: **Finance** (what it earned —
matched by its Microsoft Store product id, its folder, or a manual link; a name match is only
a suggestion; `navig config set finance.link.auto false` to match manually only) and the
**Microsoft Store** app in `store/identity.json` with its last 30 days of sales.

Where things live: the project's files stay its own — NAVIG only writes when you edit a doc,
tick a todo, `--pin`, or `add`. Its own state (preferences, the cached context pack, AI
analyses, run history) is in `projects.db` under the data dir; run logs are under the log dir
(`navig paths`) in `projects/<id>/`.

Edits are exact: a todo toggle or doc save changes only the bytes it means to (line endings
and encoding are kept), and an edit against a file that changed since you loaded it is
refused rather than overwritten.

Starting and stopping scripts works only from this computer (NAVIG Desktop or the CLI) — the Mini
App can see runs and their logs, not start them.

---

## 54. Bookmarks (`navig bookmarks`)

The bookmark manager lives in the **navig-bookmarks** plugin (`pip install navig-bookmarks`,
or `pip install "navig[bookmarks]"`). It also runs on its own as `navig-bookmarks` — same
commands, same store (`<data dir>/bookmarks.db`). It replaces `navig links`: that name still
works for one release, prints a deprecation notice, and forwards here (without the plugin it
prints the install hint and exits 1). Bookmarks saved with `navig links` are copied in
automatically on first use — read-only on the old `links.db`, safe to repeat, and a bookmark
deleted here is never copied back.

| Command | What it does |
|---|---|
| `navig bookmarks <url>` | Save a bookmark (short for `add`; `--title`, `--notes`, `--tags a,b`, `--folder "Dev/Python"`, `--cred <vault id>`). |
| `navig bookmarks list` / `search <words>` / `show <id>` | Browse (`--tag`, `--folder`, `--untagged`), full-text search, details. |
| `navig bookmarks open <id>` | Open it; with a vault credential the login is filled in through navig's browser (`--profile`, `--headless`). |
| `navig bookmarks import [file]` | Netscape HTML, JSON, Chrome/Edge `Bookmarks`, Firefox `places.sqlite`, Safari `Bookmarks.plist`; `--source chrome` reads the default profile. Folders are kept. |
| `navig bookmarks export [file]` | Netscape HTML with nested folders (any browser imports it) or `--format json`. |
| `navig bookmarks organize` | AI folder plan — **dry run**. `--apply` writes it (a plan moving > 20 % of bookmarks or making > 1 000 changes also needs `--force`); `--undo <file>` restores; `--export plan.html`; `--provider/--model` pin the AI. |
| `navig bookmarks tag --ai` | AI tags (`--untagged`, `--all`, `--curated`, `--dry-run`); a re-run replaces only AI tags, never yours. |
| `navig bookmarks check` | Dead links and redirects from headers only; every redirect hop is SSRF-checked; archived copies suggested. `--fail-on-dead` exits 1 on a dead link. |
| `navig bookmarks dedupe` | Same page saved twice (tracking parameters, `www.`, http/https ignored); `--apply` merges. |
| `navig bookmarks rules add <pattern> <tags…>` | Offline tagging rules (`github.com dev`), applied on add/import; `rules apply` re-tags. |

`organize` and `tag --ai` use navig's AI routing. Standalone, they use any provider key, an
OpenAI-compatible server (`NAVIG_AI_BASE_URL` + `NAVIG_AI_MODEL`), or a local Ollama. With no AI
configured they say how to set one up and exit 2. Every list command takes `--json`.

```bash
navig bookmarks import ~/Downloads/bookmarks.html
navig bookmarks organize --limit 50          # look at the plan first
navig bookmarks organize --apply --force
navig bookmarks check --folder "Dev"
navig bookmarks export cleaned.html
```

## 55. Agent Control Center (`navig agentctl`)

The Agent Control Center lives in the **navig-agentctl** plugin (`pip install "navig[agentctl]"`,
or `pip install -e plugins/navig-agentctl`). Phase 1 is the **Observer** — it shows what is
already true (live agent sessions, git worktrees, task records) and changes nothing. Launching
and recovery arrive in later phases. The desktop tile (**Control Center**) and the VS Code
extension read the same `/api/deck/agents/*` routes this CLI wraps.

| Command | What it does |
|---|---|
| `navig agentctl sessions` | Live agent sessions — project, owner, idle/active verdict (`--project <substr>`, `--json`). |
| `navig agentctl worktrees` | Git worktrees of a repo: branch, ahead/behind, dirty, last commit (`--project <path>`, `--conflicts` adds the pairwise merge radar, `--json`). |
| `navig agentctl tasks` | Unified tasks from the board + work queue (`--source board\|work`, `--json`). |
| `navig agentctl status` | One-line overview: sessions, worktrees (this repo), tasks, active claims. |
| `navig agentctl bindings` | Active task ↔ session claims — one claim per task, the no-duplicate-execution ledger (`--all` shows released history, `--json`). |
| `navig agentctl bind <task-ref> --session <id>` | Claim `board:<id>` or `work:<slug>` for a session (`--note`); rebinding moves the owner. |
| `navig agentctl unbind <task-ref>` | Release a claim; the ledger keeps the history. |
| `navig agentctl runs` | **Agent Monitor** — every observed run (Claude Code sessions, subagents, commands, lands): state, elapsed, idle, declared progress, exit code (`--active`, `--project`, `--state`, `--kind`, `--json`). |
| `navig agentctl show <run-id>` | One run in detail — stages, history and the redacted log tail (`--log`, `--json`). |
| `navig agentctl ack <run-id>` | Acknowledge a failed run so it stops lighting the Dock chip (`--all` for every one). |
| `navig agentctl run -- <command>` | Run a command as a monitored process: real PID, exit code, redacted log, optional `--verify "<cmd>"`, `--stall-after <s>`, `--detach`. `-- npm run land` is recognised and tracked milestone by milestone. |
| `navig agentctl hooks install` | Wire the monitor into Claude Code (shows the diff; `--yes` writes; `--scope user\|local`). |
| `navig agentctl hooks status` | Which Claude Code settings files carry the monitor hooks. |
| `navig agentctl hooks uninstall` | Remove only the monitor's hooks; every other hook is left untouched. |

Sessions reuse `navig procs` verdicts, so "idle" means the same thing in both. The persistent
ledger (`~/.navig/data/store/agentctl.db`) remembers sessions across restarts and records
state-change history — Phase 2 reads it to recover interrupted work. Claims bind a task ref to
one session at a time: two sessions cannot both hold the same task without rebinding it.

### Agent Monitor — what is my agent actually doing?

The run ledger answers "is it still running, did it really succeed, does it need me" for
every Claude Code session, subagent, long command and `npm run land`, across projects. It is
a lightweight observer: it never blocks an agent, never restarts anything, and redacts
tokens, passwords and key-shaped strings before anything is stored or shown.

- **Where to see it:** the **Runs** tab of Control Center in NAVIG Desktop (open it from the Dock
  chip, or `navig://apps/agentctl`), the compact chip in the Dock bar (hidden when nothing is
  live), and `navig agentctl runs` in a terminal.
- **Claude Code:** `navig agentctl hooks install` adds the documented lifecycle hooks
  (PreToolUse, PostToolUse, PostToolUseFailure, Stop, SubagentStop, TaskCompleted,
  Notification, SessionStart/End, UserPromptSubmit). Hooks load when a session *starts* —
  sessions already running are not touched. `Stop` means "the conversation turn finished",
  never "the work finished": a run only completes on a real exit code or an explicit end.
- **Background Bash** started by an agent is shown as **launched · completion unknown** —
  hooks cannot see when it ends. Wrap long commands with `navig agentctl run -- <cmd>` to get
  the exit code, a log and stall detection.
- **A stall is only claimed with proof.** Runs are flagged *stalled* only while their own
  process is verified **alive** but silent (that combination means a hang, not a queue wait);
  a hook-only run that goes quiet simply shows its idle time — silence alone never becomes a
  stall claim, and one that can no longer be proven is retracted.
- **Progress is never invented.** A run shows `3/7 stages` only when it declared its stages
  (`npm run land` does); otherwise you see elapsed time, last activity and the log — no
  percentage, no ETA.
- **`npm run land`** reports preflight, queue, rebase, gate (with attempt n/5 and retries),
  push, PR close and branch delete from the land script itself, and a land only counts as
  *verified* after git confirms the commit is on `origin/main` and the branch is gone.
- **Notifications:** four types — *Agent run finished*, *failed*, *needs you*, *stalled* —
  default to the Deck only. Opt Telegram or the new **Desktop** (Windows toast) channel in per
  type from Settings → Notifications. If Windows has app notifications turned off, the Desktop
  channel says so instead of reporting a toast that never appeared.

```bash
navig agentctl hooks install --scope user          # shows the diff first
navig agentctl hooks install --scope user --yes    # writes it
navig agentctl run --label nightly -- npm run build
navig agentctl run --detach --stall-after 600 -- npm run land
navig agentctl runs --active
navig agentctl show <run-id> --log
```

```bash
navig agentctl status
navig agentctl sessions --json | jq '.[] | select(.state=="active")'
navig agentctl worktrees --project . --conflicts
navig agentctl bind work:agent-control-center --session 9d69d3f1 --note "picked up"
```

---

## 56. Recall (`navig recall`)

**Recall** is the knowledge bank built from your past AI conversations. It reads Claude Code
and Codex transcripts and earlier session audits, and turns them into records that always
cite their source: decisions you answered, plans and the verdict you gave them, tables
(verbatim), agent research reports, and the audited features, decisions (with reversals),
recovered ideas and conflicts. Everything stays on this computer, and nothing is read until
you run `navig recall enable`.

| Command | What it does |
|---|---|
| `navig recall enable [--auto]` / `disable` | Allow or stop local reading (`recall.read_local`). `--auto` also turns on the daemon monitor that reads new conversations every 5 minutes (§56.2). Disabling keeps the bank. |
| `navig recall status [--json]` | What is in the bank, where it lives, and whether reading is on. |
| `navig recall ingest [-s claude-code\|codex\|audit\|chats\|capture\|notes] [-p project] [-n N] [--json]` | Read new or changed conversations. `chats` reads only the chat-library chats you shared (§23.4.2), and forgets those you stopped sharing. `capture` reads what you sent from the browser (below). `notes` reads the web notes the NAVIG Dock synced (§23.4.3), and forgets deleted ones. Incremental and safe to re-run: unchanged files are skipped, and appended turns are read from where the last run stopped. Exits 1 if any source failed (`sources --errors` lists them). |
| `navig recall search <words> [-p project \| --here] [-t type] [--status s] [--raw] [--json]` | Search records. `"quoted words"` match an exact phrase. `--raw` searches the conversations themselves, and it is also the fallback when no record matches. Accents are ignored; English, Russian and French all work. |
| `navig recall ask "<question>" [--here]` | Have I asked this before? Earlier matching questions you typed, the answer that followed, and matching decisions. |
| `navig recall show <rk-id> [--full] [--json]` | One record: status (and what set it), endorsement, implementation state, body, evidence with citations, timeline, related records, and its Markdown file. |
| `navig recall excerpt <source> <msg-id> [-B n] [-A n]` | The original conversation around a cited turn. |
| `navig recall decisions\|plans\|ideas\|tables\|findings\|features\|questions [-p\|--here] [--status s]` | Views by record type, newest first. `questions` defaults to unresolved. |
| `navig recall sources [-a adapter] [-p project] [--errors]` | Every source read, with its project, turn count and last error. |
| `navig recall reindex` | Rebuild the search index from the archive and the vault (neither is changed). |
| `navig recall sessions [--file P \| --plan P \| --commit SHA] [-p project \| --here] [--json]` | The Claude Code sessions that edited, planned, committed or were linked to a file, plan or commit — or every session of a project. Newest first, with state (`active` = written in the last 30 min, `recent` = 7 days, else `old`), branch and why it is linked. |
| `navig recall link <session-id> <file\|plan\|commit>` / `unlink …` | Link a session to a target by hand, or remove a link (any relation). Both survive `reindex`. |
| `navig recall history <file\|plan\|sha> [-k word] [-p project \| --here] [--json]` | What happened to one target: the sessions, commits and records behind it, merged into one timeline (worktree paths fold to the project they belong to). |
| `navig recall trace <sha\|session-uuid> [--json]` | One commit or one session → the other side of its edges: sessions ↔ commits ↔ files ↔ records. A sha git cannot see reports `known: false` instead of pretending. |
| `navig recall changes [-p project \| --here] [--since DAYS] [--refresh] [--json]` | Recent commits of a project, each joined to the sessions and records it explains. `--refresh` asks git first and fills the metadata-only commits cache (never diffs). |
| `navig recall unfinished [-p project \| --here] [--json]` | Plans and tasks nothing has verified yet, oldest first. Display-only — it never spawns, resumes or schedules anything. |
| `navig recall conflicts [-p project \| --here] [--refresh] [--json]` | Recorded contradictions: opposed user verdicts on one subject, or claims verification disproved (`navig recall verify` records them too). Deterministic, idempotent, zero inference. |
| `navig recall migrate <file> -p project [--apply] [--retire] [--root <folder>] [--delete-backup] [--json]` | Import a legacy changelog. **Dry-run by default** (zero writes): parsed entries, projected record ids, sha256, consumer scan. `--apply` backs up to `vault/.migrations/`, imports, then re-reads the original to verify digest + ids + turns before reporting. `--retire` additionally moves a **private dev** changelog to its backup — release notes are never retired, and any consumer of the file's name blocks it (`.navig/history/**`, the operation log, is not a consumer). `--root` scans a specific tree when a worktree and the registry disagree. `--delete-backup` is the one irreversible step: interactive, typed-path confirmation, never `--json`. |

**Who said it is kept separate from whether it is true.** User-role text is split before
anything is stored: what you typed, pasted material, editor selections and background-agent
reports each get their own speaker. Each record has a separate status basis (`user`,
`audit`, `assistant` or `verify`). A plan is `approved` only through your own verdict, and
an audit's "in force" is shown as `confirmed·audit`, never as your endorsement. Evidence
with a weaker basis never overrides a stronger status.

**What is read.** Claude Code and Codex transcripts keep your typed text, the assistant's
visible replies, your answers to structured questions, plans with your approve/decline
verdict, and agent reports. Shell output, file reads, thinking, attachments and compaction
summaries are skipped. Subagent transcripts are skipped too, because their final reports
already reach the parent session. A session reachable through two junctioned project
folders is read once. Session audits (`<claude home>/session-audit*`) are imported read-only.

**Where it lives** (`<data dir>/recall/`):
- `archive/` holds immutable, secret-redacted segments with checksums. Evidence survives
  deletion of the original transcript.
- `vault/<project>/<type>/rk-….md` is the canonical record: YAML front matter plus a body.
  You may edit the body between its `recall:body` markers.
- `vault/.events/` and `vault/.links.jsonl` hold the append-only history and links.
- `index.db` is a rebuildable FTS5 cache.

Secrets are redacted before anything is stored. Transcript text never goes to logs.

**Session links.** While reading a Claude Code transcript, Recall also records which files the
session edited (successful `Edit`/`Write` only), which plan file it wrote or approved, and which
commits it made (`git commit` output) — paths and short commit ids only, never file contents.
A file edited in a worktree (`.dev/worktrees/<slug>/…`, `~/.codex/worktrees/…`) is the same
target as in the main checkout, and a nested space (e.g. `core` inside `navig`) owns its files.
Sessions read before this existed are re-read once, automatically, on the next `ingest`.
NAVIG Dev's **Sessions for this File** view and NAVIG Desktop → Projects → **Sessions** show the same
links (`GET /api/deck/recall/sessions`, local only — never through Lighthouse or a browser
extension), and open a session in Claude Code with `vscode://anthropic.claude-code/open?session=<id>`.
Removing a link with `navig recall unlink` is remembered in the index *and* the vault, so it
survives both `reindex` **and** the transcript's next full re-read — derivation never brings it
back; `navig recall link` re-opens the pair when you change your mind.

### Extraction with a model (`navig recall extract`)

Pass 0 is deterministic. `extract` adds the knowledge only a reader can find: ideas,
alternatives that were weighed, names coined or rejected, decisions with their reasons, plans,
answered and open questions, findings, risks, architecture and lore. It is off until
`navig recall enable --remote`, and `navig recall disable` turns it off again.

| Command | What it does |
|---|---|
| `navig recall extract [-p project \| --here] [-s source] [-n N] [--budget USD] [--day-cap USD] [--focus all\|split] [--workers N] [--dry-run]` | Sends **secret- and PII-redacted** conversation prose, one window at a time, to `recall.extract.provider` / `recall.extract.model` (default `mimo` / `mimo-v2.6-flash`). It stops before a call would pass the run budget (default $5, `recall.extract.budget_usd_run`) or the daily cap (`recall.extract.budget_usd_day`, default $20). `--dry-run` sends nothing and prints the window count and the cost. Resumable: a window already extracted is never sent again, even after `reindex`, because the ledger lives in `vault/.extract_runs.jsonl`. |
| `navig recall eval [-n 6] [--budget 1]` | Quality check against the session audit: for audited sessions, how many of the audit's features and ideas the extraction recovers, plus quote validity and attribution errors (which must be 0). |
| `navig recall review [list \| accept <id> \| reject <id>]` | Possible duplicates (accepting merges them and keeps the other as `superseded`) and model items with no verifiable quote (never turned into records). |
| `navig recall verify -p <project> [--root <repo>]` | Checks ideas and plans against the project's code: `likely` (code and tests), `partial` (code only), `not_found`, or `unknown`. It never sets `verified`. Afterwards it re-derives each record's **delivery lifecycle** (`proposed · implemented_uncommitted · committed · merged · verified · deployed · failed · superseded · abandoned · unknown`) from git verdicts and explicit evidence — deterministic, zero inference; merged never implies deployed, and an assistant claim never yields an `observed` basis. |
| `navig recall terms` · `navig recall forgotten` | Vocabulary (coined and rejected names, with reasons), and ideas that were never rejected and have no implementation found, oldest first. |

**Every extracted record quotes its source verbatim.** The quote must appear in the message
it cites, or the item goes to review instead. A status of `confirmed`, `approved`, `rejected`
or `deferred` sticks only when the cited line is yours (typed, an answer, or a plan verdict).
Otherwise it is capped at `proposed` or `exploring`, with status basis `assistant`. An
assistant saying "done" never sets an implementation state. Items merge into an existing
record only when the model names that record or the canonical name matches exactly in the
same project. Near-duplicates are created and queued for review, never merged silently.

### 56.1 Browser capture (NAVIG Dock)

What you send from the browser lands in Recall under the project you picked, and is
searchable at once (when Recall is enabled).

- **Selection → Knowledge.** Select text on any page: a small **🧠 Knowledge** button
  appears beside it (or right-click → NAVIG Dock → **Send … to NAVIG Knowledge…**). The
  sheet shows what will be sent, as Markdown — tables, lists and code survive — and asks
  for a **project** (remembered per site, and per ChatGPT conversation), a **kind** (auto ·
  prompt · idea · decision · term · table · note) and an optional note. Kind *prompt* also
  saves it to the prompt library. "Hide the floating button" turns the button off; the
  right-click item stays.
- **ChatGPT Lens → Prompts (N).** Lists the prompts in the conversation — your messages
  with role framing ("You are…"), placeholders (`{x}`, `[X]`, `<x>`) or sections, long
  instructions, prompt-labelled blocks in answers — ticked, plus your other long messages
  unticked. Pick a project, then **Import selected**: they go to NAVIG Desktop → Knowledge →
  Prompts with the project and a link to the exact message; importing again reports
  "already there" (dedupe by content, whitespace ignored). **Export** now writes YAML
  frontmatter (conversation id, project, message ids) and keeps tables, code and image
  links.
- **Offline.** If NAVIG is not running, captures wait in the browser (the toolbar badge
  counts them) and are sent, in order, as soon as NAVIG answers again. Nothing is dropped;
  a full queue (200) refuses new captures instead.
- **What Recall stores.** Each capture is a note in `<data dir>/inbox/` with Navig Capture
  v1 frontmatter (`navig_capture: 1`, kind, project, url, conversation and message ids,
  client). Recall reads it as source `capture:<id>`, speaker **web_capture** — page text is
  never attributed to you as something you typed or decided. A project that is not
  registered falls back to `shared`. A captured table becomes a table record. A whole
  conversation sent with "→ NAVIG inbox" stays a note in the inbox only: Recall reads
  conversations from the chat library (`navig chats`, source `chats`), with the share level
  you chose there, never a second copy from these notes.
- **Lenses never read Recall.** A Lens can send captures, but any command it asks NAVIG to
  run that names `recall` is refused, by the Lens host and again by the Dock's service worker.

```bash
navig recall ingest -s capture            # re-read every capture note (the Dock indexes each one on arrival)
navig recall search "pricing" -p navig --raw
navig recall sources -a capture
```

```bash
navig recall enable && navig recall ingest
navig recall search "telegram controller" --here
navig recall ask "did we decide on tauri"
navig recall show rk-xxxxxxxxxxxx
```

### 56.1 Recall in Claude Code (MCP)

`navig mcp serve --transport stdio --only recall` serves the Recall tools and nothing else.
Register it with Claude Code once:

```bash
navig mcp install-config --client claude-code --dry-run     # print the command, change nothing
navig mcp install-config --client claude-code               # shows it, asks, then runs it
navig mcp install-config --client claude-code --scope project --yes
```

That runs `claude mcp add navig-recall --scope user -- <navig> mcp serve --transport stdio --only recall`
(`--scope user|project|local`, default `user`, which covers every Claude Code session). It
asks first because a user-scope server is global; without a terminal it needs `--yes`. The
server reads the bank in-process, so it works without the daemon.

| Tool | Level | What it returns |
|---|---|---|
| `recall_search` | safe | Records matching the words; the conversation turns when nothing matches (`raw=true` for turns directly). |
| `recall_ask` | safe | Questions you typed before, the reply that followed each, and matching decisions. |
| `recall_get` · `recall_related` · `recall_timeline` | safe | One record with cited evidence; its links; its event history. |
| `recall_decisions` · `recall_open_questions` · `recall_forgotten_ideas` · `recall_tables` | safe | The views. Forgotten ideas are ideas with no sign of being built (`impl` not_found/unknown), not rejected, the longest-untouched first. Tables come verbatim. |
| `recall_source_excerpt` | safe | The original turns around one cited message. |
| `recall_ingest` | moderate | An incremental ingest. Refused until `navig recall enable`. |

**Scope.** A call reads the project the client is working in (from the directory Claude Code
starts the server in, worktrees folded into their repo) plus `shared`. `project=<id>` reads
another project; `all_projects=true` reads them all and has to be asked for.

**Authority.** Every result is cited (`source_id`, `msg_id`, `ts`, `speaker`, and `owner`,
which is true only for your own words), and every record carries `status` with
`status_basis`. Only `user` means you decided it, so a model cannot pass an assistant reply or
an audit row off as your decision. Shared chats below `agent` level never leave through MCP:
their turns, their excerpts and every record they support are withheld.

`--only` takes any bundle names, comma-separated (`--only recall,memory`). A misspelt name is
an error, never an empty server.

### 56.2 Automatic reading (monitor `recall_ingest`)

```bash
navig recall enable --auto        # reading on + the daemon monitor on
navig service restart             # the monitor starts with the daemon
```

Every 5 minutes the daemon runs one incremental ingest in a worker thread. Unchanged files
cost a stat, a live session is read from its cursor, and a half-written last line waits for
the next pass. So a new session is searchable within one poll. The monitor is off by default
(`monitors.recall_ingest.enabled`). It also checks `recall.read_local` on every pass, so
`navig recall disable` stops it without a restart. If a pass is still running, the next one is
skipped. After three failed passes in a row it records one incident (`navig doctor` → Config
Health). You can switch it on or off live in NAVIG Desktop → Settings → Notifications → Monitors →
**Recall reading**.

### 56.3 NAVIG Desktop → Knowledge → Recall

The **Recall** tab has three panes:
- **The rail** holds the views (Forgotten ideas, Open questions, Decisions, Plans, Tables,
  Findings, Features) and the projects.
- **The list** shows search results with the matched words highlighted. Each row has a type
  chip and a `status·basis` chip. Green means you set it yourself; for example `confirmed·user`.
- **The detail pane** shows the status, endorsement, implementation and confidence chips, then
  the summary and the body. Tables are shown verbatim, with **Copy as Markdown**. Then comes the
  evidence, each item with a speaker badge (yours are marked **you**) and **Open source**, which
  shows the conversation ±3 turns around it. Last are the timeline and related records.

Keys: `/` search · ↑/↓ move · Enter open · `e` open the first source · Esc back.

The tab is read-only: `/api/deck/recall/status|search|ask|views/<name>|record|excerpt`, all
GET, all behind deck auth. When Recall is off, the tab shows the command to turn it on.

## 57. Distribution (`navig distribution`)

**What it is:** the multi-project distribution workspace (plugin `navig-distribution`,
NAVIG OS → Distribution). Channel research and eligibility, campaigns, a block-level
content studio, identities, a fifteen-check publication gate, approvals and an
append-only journal — NAVIG Social carries the actual platform posts, NAVIG's CDP
drives operator-led submissions.

- **`navig distribution status`** — cross-project health: channels, campaigns,
  publications and the active policy. Start here.
- **`navig distribution seed`** — import the bundled research (SCHEMA's 139 channel
  records with evidence, campaigns, identities). Runs itself once on first use.
- **`navig distribution plan` / `channels` / `channel <id>`** — ranked, evidence-backed
  destinations per project and the full record behind one channel.
- **`navig distribution content new|show|block|regenerate|review|translate|import`** —
  the studio: one block per edit, every save a recoverable version, regeneration
  scoped to the selected block (the rest of the article is untouched), quality/canon/
  locale review, and `content import --source <dir> [--campaign <id>]` for a written
  package (idempotent, brings platform adaptations along).
- **`navig distribution publish prepare|approve|reject|run|record|verify|journal`** —
  the publication flow: `prepare` runs the gate and creates the row; `approve` binds a
  human to the exact content hash + identity + destination (30-day expiry, revocable);
  `run` executes through the only transport the destination allows — a NAVIG Social
  post, a manual package, a reviewed form in a named automation browser profile
  (`run <id> --profile <name>`), or an honest refusal; `record` + `verify` handle what
  you submitted by hand; `journal` is the ledger. A Social post goes out as the approved
  identity's account or not at all (`BLOCKED`, never the default token by accident).
- **`navig distribution adapters`** — reviewed form maps and whether each is usable.
  **`navig distribution draft-map <channel> --profile <name> [--out <file>]`** reads
  the live form read-only and writes a review draft (selectors, labels, suggested
  slots) that never validates until you sign the review.
- **`navig distribution publish due [--dry-run]`** — submit the `SCHEDULED` rows whose
  approved time has passed (`prepare` with `scheduled_for`; the approval binds the
  time). `navig distribution schedule --publish-due` runs it every 15 minutes.
- **`navig distribution campaign list|show|new|update|delete`** — campaigns; `delete`
  archives a campaign that documents or publications still reference.
- **`navig distribution research import|validate|stale`** — bring in researched channel
  records (contract-checked) and list verifications past the window.
- **`navig distribution maintain`** — recompute eligibility, list stale verifications,
  park expired scheduled posts, report the policy. `navig distribution schedule
  --cron '<expr>'` wires it into NAVIG's scheduler (it never publishes). The
  verification window is the module's `stale_days` setting (NAVIG OS → Distribution →
  Settings).
- **`navig distribution analytics`** — measured outcomes with explicit "not measured"
  gaps; publication links carry `ref=<channel>` so visits map back to their channel.

Safety rules the code enforces: an already-submitted row is never posted twice; an
uncertain (retryable) result must be verified before any retry; approvals die at
their expiry or when revoked; every status change walks the published state machine;
project-scoped reads refuse another project's rows; transport errors are redacted
before they reach the journal. Full runbook:
`plugins/navig-distribution/docs/RUNBOOK.md`.

## 58. Visual presentations (`navig debrief`)

Turns a finished work session into an interactive slide deck — the result,
what changed, how it works, the measured impact, what's next — from transcript
facts plus an optional `story.json`. The scrolling report is unchanged behind
its own verbs.

| Command | What it does |
|---|---|
| `navig debrief` | Presentation of the newest session (`presentation.html` + `presentation.json`) |
| `navig debrief --session <id>` | A specific session (substring / `last` / transcript path) |
| `navig debrief --story <path>` | Enrich it with an authored `story.json` |
| `navig debrief --presentation <path>` | Render an authored `presentation.json` |
| `navig debrief --profile board` | 4–6 slide board brief (results, risks, priorities) |
| `navig debrief --technical` | Append the developer evidence appendix |
| `navig debrief --export pdf` | Print the deck to `presentation.pdf` (Edge/Chrome) |
| `navig debrief --plan "<text>"` | Proposal deck — always labeled *proposed*, never as done (`-` reads stdin) |
| `navig debrief --open` / `--json` | Open it / machine-readable manifest |
| `navig debrief build · list · open · shot` | The scrolling technical report (unchanged) |

Decks never invent: metrics come from the transcript (each with a source),
plan decks wear a persistent *Proposed* band and the validator rejects a
completed status on them. Inside NAVIG, the `debrief_build` agent tool takes
`presentation` / `profile` / `technical`; the **Debrief** tile in NAVIG OS
previews deck or report; the `session-debrief` skill routes chat requests.
Authoring recipes: the `debrief` and `debrief-plan` prompts.

## 59. Creative Mode (`navig creative`)

Proactive capability intelligence: understand what NAVIG already has, detect real friction in
your own operation history, and improve NAVIG through **reuse → configure → compose → extend →
create → do nothing** — in that order, so a wrapper is never generated for a tool NAVIG ships.

Two agent modes (owner decision 2026-10-20) replace the six retired focus modes:

| Mode | Meaning |
|---|---|
| `standard` | Everyday assistance, coding, operations, research, execution (default). Discovery and reuse advice stay available; proactive scans, builds and publishes are off. |
| `creative` | Standard plus proactive discovery, grounded proposals, authoring and authorized publishing. Never grants extra execution authority — tool schemas are identical in both modes. |

All six legacy values (`work`, `deep-focus`, `planning`, `creative`-tone, `relax`, `sleep`) migrate
to `standard` on load; deep-focus/sleep users keep notification suppression via the new
`dnd_enabled` preference (DND no longer hangs off the mode).

```powershell
navig creative status            # mode · index freshness · scan pace · proposals · publish policy
navig creative index --q docker  # capability search — options: kind filter, limit, rebuild, json
navig creative list              # proposals the 6-hour scan and the agent surfaced
navig creative show <id>         # full card: evidence, decision, benefit, risk, assumptions
navig creative build <id>        # write the skill (Creative mode only) through the standard
                                 #   skill contracts — lint + parser checks, rollback on failure
navig creative dismiss <id>      # permanent: it will never be re-proposed
navig creative publish <id>      # authorized-destination checklist (see below)
navig creative publish-enable --repo <path-or-github:owner/name>   # owner-run; default stays local
navig creative publish-disable   # back to local-only
```

**Publishing** is local-only until `publish-enable` writes an allow-listed destination; the
checklist (allow-list · private visibility · branch · unchanged HEAD · no force · fresh foreign
lock · credentials · secret scan) is deterministic — blocked is a list of failed checks, never a
prompt. Public repositories and force-push are refused unconditionally.

Surfaces: the **Dashboard Suggestions card** (evidence, Build/Dismiss, empty state first-class),
**Settings → Brain → Agent Mode** (the one selector) and **Creative publishing**, the
`creative_scan` monitor (default on, idle unless Creative), Telegram `/mode standard|creative`
(retired names answer with a "retired" notice and behave as Standard), and the `creative` agent
tool (`lookup`/`decide` in both modes, `propose` Creative-only). Data lives under
`data_dir()/creative/` (rebuildable index + proposal store); config under `creative.*`.

## 60. NAVIG MIND (`navig mind`)

The owner's personal cognitive/language preferences — how NAVIG talks to you and drafts personal
correspondence. MIND never changes product, repository, or artifact language: the
**artifact-language firewall** is non-negotiable and is reported on every status view.

```powershell
navig mind status                 # effective profile: languages, lexicon, DIGITALGHOST, firewall
navig mind on | off               # enable/disable the whole MIND layer
navig mind lexicon <density>      # off | minimal | balanced | detailed | intensive (default: balanced)
navig mind digitalghost <mode>    # on | off | rare | balanced | deep | retrospective (default: balanced)
```

Defaults: conversation follows the active chat (`auto`); creative exploration `ru`; engineering `en`;
professional correspondence to French-speaking recipients `fr`; DIGITALGHOST commentary `ru`. Input
language never dictates every output language. Preference hierarchy: session > project
(`.navig/config.yaml`) > global (`~/.navig/config.yaml`) > default. Config lives under `mind.*`; the
resolved profile is `navig.mind.get_effective_mind()`.
