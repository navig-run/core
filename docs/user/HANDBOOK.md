---
applyTo: '**'
---

# NAVIG - AI-Optimized Command Reference Guide

> **Primary Knowledge Base for AI Assistants**
> Version: 2.4.14 | Last Updated: 2026-03-20

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
navig init --tui                  # opt-in full-screen TUI onboarding
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

Verification and completion:

- CLI onboarding now prints step progress (`[n/N %]`) and a verification summary before completion
- TUI onboarding now shows a dedicated verification dashboard before final write/activation

You can always configure deferred integrations later from CLI (`navig init`, `navig matrix`, `navig help email`, etc.).

**Profile overview**

| Profile | Modules applied |
|---------|----------------|
| `node` | config dirs, CLI verify, legacy migration |
| `operator` | + shell PATH, vault init, Telegram token |
| `architect` | + MCP config file |
| `system_standard` | + system service registration |
| `system_deep` | + Windows tray install |

**Telegram token** — set `NAVIG_TELEGRAM_BOT_TOKEN` before running to have it
stored automatically (vault + `.env` + `config.yaml`). If absent, the step is
silently skipped; reconfigure later with `navig init` (interactive).

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
navig menu                         # Interactive menu

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
navig host security firewall       # Firewall status
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
navig ai ask "question"            # Ask AI
navig ai analyze                   # Analyze host
navig wiki show <topic>            # Show wiki page
navig wiki list                    # List wiki pages
```

### Deprecated Commands (Migration)

Old commands continue to work but show warnings:
```bash
# Old → New
navig monitor      → navig host monitor
navig security     → navig host security
navig system       → navig host maintenance
navig server       → navig host
navig workflow     → navig flow
navig task         → navig flow
navig template     → navig flow template
navig addon        → navig flow template
navig hestia       → navig web hestia
```

---

## 1.6 Interactive Menu Command Center

The **Interactive Menu** (`navig menu`) provides a comprehensive, visual command center for navigating all NAVIG capabilities. It's organized into three pillars for intuitive access.

### Launch the Menu

```bash
navig menu          # Start interactive menu
```

### Three-Pillar Organization

The menu is organized into three main categories:

#### SYSOPS (Infrastructure)
| Key | Menu Item | Description |
|-----|-----------|-------------|
| 1 | Host Management | Servers, SSH, discovery |
| 2 | File Operations | Upload, download, browse |
| 3 | Database Operations | SQL, backup, restore |
| 4 | Webserver Control | Nginx, Apache, vhosts |
| 5 | Docker Management | Containers, images, compose |
| 6 | System Maintenance | Updates, health, services |
| 7 | Monitoring & Security | Resources, firewall, audit |

#### DEVOPS (Applications)
| Key | Menu Item | Description |
|-----|-----------|-------------|
| A | Application Management | Apps, configs, deploy |
| R | Remote Execution | Run commands via SSH |
| T | Tunnel Management | SSH tunnels, port forward |
| F | Flow Automation | Workflows, templates |
| L | Local Operations | System info, network |

#### LIFEOPS (Automation)
| Key | Menu Item | Description |
|-----|-----------|-------------|
| G | Agent & Gateway | Autonomous mode, 24/7 operation |
| M | MCP Server Management | AI tool integrations |
| P | AI Assistant | Insights, recommendations |
| W | Wiki & Knowledge | Docs, search, RAG |
| B | Backup & Config | Export, import, settings |

#### System
| Key | Menu Item | Description |
|-----|-----------|-------------|
| C | Configuration | Settings, context |
| H | Command History | Recent commands |
| ? | Quick Help | Keyboard shortcuts |
| I | Initialize | Project setup (if not initialized) |
| 0 | Exit | Quit menu |

### Status Dashboard

The menu displays a compact status dashboard showing:
- **Host Status**: Active host with IP (● active / ○ not set)
- **App Status**: Active application (● active / ○ not set)
- **Last Command**: Most recent command with success/failure indicator

### Keyboard Navigation

| Key | Action |
|-----|--------|
| ↑/↓ | Navigate menu options |
| Enter | Select highlighted option |
| 0/ESC | Go back / Exit menu |
| Ctrl+C | Quick exit (any menu) |

### Standalone Submenus

Each menu can also be launched directly from CLI:

```bash
navig host          # Host management menu
navig app           # App management menu
navig db            # Database operations menu
navig tunnel        # Tunnel management menu
navig flow          # Flow automation menu
navig local         # Local operations menu
```

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

Renders the full Markdown topic file for that resource group.

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

### Per-Command Help

Every command and subcommand also exposes `--help` via Typer:

```bash
navig db --help
navig db list --help
navig host monitor show --help
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

**Related Commands:** `navig host list`, `navig host use`, `navig host inspect`

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

**Related Commands:** `navig host list`, `navig host current`, `navig context set`

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

**Related Commands:** `navig host use`, `navig host current`

---

### `navig host current`

Show the currently active host with source information.

**Examples:**
```bash
navig host current
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

### `navig host inspect`

Auto-discover host details (OS, PHP, databases, web servers, paths).

**Examples:**
```bash
# Inspect active host
navig host inspect

# Output shows detected:
# - Operating System (Ubuntu 24.04)
# - PHP version (8.4.8)
# - MySQL/PostgreSQL version
# - Nginx/Apache version
# - Web root paths
```

**💡 Tip:** Run this after adding a new host to auto-populate configuration.

**Related Commands:** `navig host add`, `navig host info`

---

### `navig host info [name]`

Show detailed host information.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | No | Host name (uses active host if omitted) |

**Examples:**
```bash
# Show info for active host
navig host info

# Show info for specific host
navig host info production
```

**Related Commands:** `navig host list`, `navig host inspect`

---

### `navig host test [name]`

Test SSH connection to a host.

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

**Related Commands:** `navig host add`, `navig host info`

---

### `navig host clone <source> <new_name>`

Clone a host configuration.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source` | string | Yes | Source host name to clone |
| `new_name` | string | Yes | New host name |

**Examples:**
```bash
# Clone production to create staging
navig host clone production staging
```

**Related Commands:** `navig host add`, `navig host edit`

---

### `navig host edit <name>`

Open host configuration in default editor (YAML file).

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Host name to edit |

**Examples:**
```bash
# Edit production host config
navig host edit production
```

**Related Commands:** `navig host info`, `navig host inspect`

---

### `navig host default <name>`

Set the default host (used when no active host is set).

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Host name to set as default |

**Examples:**
```bash
# Set production as default
navig host default production
```

**Related Commands:** `navig host use`, `navig host current`

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

Execute a shell command on the remote server.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | string | No* | Shell command to execute |
| `--stdin`, `-s` | flag | No | Read command from stdin (bypasses escaping) |
| `--file`, `-f` | path | No | Read command from file (bypasses escaping) |

*One of `command`, `--stdin`, or `--file` is required.

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

Legacy compatibility: `navig upload ...` still works, but `navig file add ...` is the canonical form.

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
                  ├── YES → Use: navig upload (RECOMMENDED)
                  └── NO → Use: navig run --file script.sh

(Canonical form: `navig file add ...`.)
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

Legacy compatibility: `navig upload <local> [remote]` still works, but `navig file add ...` is the canonical form.

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

Legacy compatibility: `navig download <remote> [local]` still works, but `navig file get ...` is the canonical form.

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

**Related Commands:** `navig chmod`, `navig chown`

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

**Related Commands:** `navig list`, `navig mkdir`

---

### `navig chmod <path> <mode>`

Change file/directory permissions.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Remote file/directory path |
| `mode` | string | Yes | Permission mode (e.g., 755, 644) |
| `--recursive`, `-r` | flag | No | Apply recursively |

**Examples:**
```bash
# Set file permissions
navig chmod /var/www/html/storage 775

# Set permissions recursively
navig chmod /var/www/html/storage --recursive 775
```

**Related Commands:** `navig chown`, `navig mkdir`

---

### `navig chown <path> <owner>`

Change file/directory ownership.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Remote file/directory path |
| `owner` | string | Yes | New owner (user or user:group) |
| `--recursive`, `-r` | flag | No | Apply recursively |

**Examples:**
```bash
# Change owner
navig chown /var/www/html www-data

# Change owner and group
navig chown /var/www/html www-data:www-data

# Change recursively
navig chown /var/www/html www-data:www-data --recursive
```

**Related Commands:** `navig chmod`, `navig mkdir`

---

### 5.1 File Shortcut Commands ⭐ (NEW - AI-Optimized)

These commands provide direct access to common file operations, eliminating the need for complex `navig run` commands with shell escaping.

#### `navig cat <path>`

Read remote file content directly. **Use this instead of** `navig run "cat /path/file"`.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Remote file path |
| `--head`, `-h` | int | No | Show only first N lines |
| `--tail`, `-t` | int | No | Show only last N lines |

**Examples:**
```bash
# Read entire file
navig cat /var/www/html/.env

# Read first 20 lines
navig cat /var/log/nginx/error.log --head 20

# Read last 50 lines
navig cat /var/log/nginx/access.log --tail 50
```

**💡 AI Tip:** Use `navig cat` instead of `navig run "cat ..."` for simpler syntax and proper output handling.

**Related Commands:** `navig download`, `navig ls`

---

#### `navig write-file <path>`

Write content to a remote file. **Use this instead of** complex heredoc patterns like `navig run "cat > file << 'EOF' ... EOF"`.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Remote file path to create/overwrite |
| `--content`, `-c` | string | No | Content to write (for simple strings) |
| `--from-file`, `-f` | path | No | Local file to upload as content |
| `--mode`, `-m` | string | No | Permission mode (default: 644) |

**Examples:**
```bash
# Write simple content
navig write-file /var/www/html/test.txt --content "Hello World"

# Write JSON config from local file (RECOMMENDED for complex content)
navig write-file /var/www/app/config.json --from-file ./config.json

# Write with specific permissions
navig write-file /etc/nginx/conf.d/app.conf --from-file nginx-app.conf --mode 644
```

**💡 AI Tip:** For JSON, YAML, or multi-line content, ALWAYS use `--from-file` to avoid shell escaping issues. Create the file locally first, then upload.

**⚠️ CRITICAL:** This command solves the heredoc escaping problem documented in Section 4.2.

**Related Commands:** `navig upload`, `navig cat`

---

#### `navig ls <path>`

List directory contents with enhanced options. **Use this instead of** `navig run "ls -la /path"`.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Remote directory path |
| `--all`, `-a` | flag | No | Show hidden files (default: true) |
| `--long`, `-l` | flag | No | Long listing format (default: true) |
| `--human`, `-h` | flag | No | Human-readable sizes (default: true) |

**Examples:**
```bash
# List directory (default: long format with hidden files)
navig ls /var/www/html

# List with all defaults
navig ls /var/log/nginx

# Simple listing (no details)
navig ls /home/user --long false
```

**💡 AI Tip:** Use `navig ls` instead of `navig run "ls -la ..."` for cleaner syntax.

**Related Commands:** `navig tree`, `navig cat`

---

#### `navig tree <path>`

Display directory tree structure. **Use this instead of** `navig run "find /path -type f"` for structure visualization.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Remote directory path |
| `--depth`, `-d` | int | No | Maximum depth level (default: 3) |
| `--dirs-only` | flag | No | Show only directories |

**Examples:**
```bash
# Show tree with default depth (3 levels)
navig tree /var/www/html

# Show tree with 2 levels only
navig tree /var/www/html --depth 2

# Show only directories
navig tree /var/www --dirs-only
```

**💡 Note:** Falls back to `find` command if `tree` is not installed on remote server.

**Related Commands:** `navig ls`, `navig list`

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
navig cat /var/www/config.json
navig write-file /var/www/config.json --from-file config.json
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

Run comprehensive health check (resources, services, disk, network).

**Examples:**
```bash
navig health-check
```

**Related Commands:** `navig health`, `navig monitor-resources`

---

### `navig monitor-resources`

Monitor real-time resource usage (CPU, RAM, disk, network).

**Examples:**
```bash
navig monitor-resources
```

**Related Commands:** `navig monitor-disk`, `navig monitor-services`

---

### `navig monitor-disk`

Monitor disk space with threshold alerts.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `--threshold`, `-t` | int | No | Alert threshold percentage (default: 80) |

**Examples:**
```bash
# Monitor with default 80% threshold
navig monitor-disk

# Monitor with custom 90% threshold
navig monitor-disk --threshold 90
```

**Related Commands:** `navig monitor-resources`, `navig health`

---

### `navig monitor-services`

Check health status of critical services.

**Examples:**
```bash
navig monitor-services
```

**Related Commands:** `navig health`, `navig restart`

---

### `navig monitor-network`

Monitor network statistics and connections.

**Examples:**
```bash
navig monitor-network
```

**Related Commands:** `navig monitor-resources`, `navig audit-connections`

---

### `navig monitoring-report`

Generate comprehensive monitoring report and save to file.

**Examples:**
```bash
navig monitoring-report
```

**Related Commands:** `navig health-check`, `navig monitor-resources`

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

**Related Commands:** `navig fail2ban-unban`, `navig security-scan`

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

**Related Commands:** `navig security-scan`, `navig security-updates`

---

### `navig security-updates`

Check for available security updates.

**Examples:**
```bash
navig security-updates
```

**Related Commands:** `navig update-packages`, `navig security-scan`

---

### `navig audit-connections`

Audit active network connections.

**Examples:**
```bash
navig audit-connections
```

**Related Commands:** `navig monitor-network`, `navig security-scan`

---

### `navig security-scan`

Run comprehensive security scan.

**Examples:**
```bash
navig security-scan
```

**Related Commands:** `navig ssh-audit`, `navig fail2ban-status`, `navig firewall-status`

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

#### `navig security audit`

Run an AI-powered security analysis of installed software.

**Examples:**
```bash
navig security audit
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
│ Issue: CVE-2023-XXXX - Remote code execution            │
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

**Related Commands:** `navig clean-packages`, `navig security-updates`

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

**Related Commands:** `navig monitor-disk`, `navig cleanup-temp`

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

### `navig template list`

List all available templates.

**Examples:**
```bash
navig template list
```

**Available templates:** nginx, docker, postgresql, mysql, redis, caddy, traefik, nextcloud, gitea, portainer, grafana, prometheus, etc.

**Related Commands:** `navig template enable`, `navig template info`

---

### `navig template enable <name>`

Enable a template.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Template name to enable |

**Examples:**
```bash
navig template enable nginx
navig template enable postgresql
```

**Related Commands:** `navig template disable`, `navig template list`

---

### `navig template disable <name>`

Disable a template.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Template name to disable |

**Examples:**
```bash
navig template disable redis
```

**Related Commands:** `navig template enable`, `navig template list`

---

### `navig template info <name>`

Show detailed information about a template.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Template name |

**Examples:**
```bash
navig template info nginx
```

**Related Commands:** `navig template list`, `navig template edit`

---

### `navig template edit <name>`

Edit template configuration.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Template name to edit |
| `--server`, `-s` | string | No | Server name (uses active if omitted) |

**Examples:**
```bash
navig template edit nginx
```

**Related Commands:** `navig template info`, `navig template validate`

---

### `navig template validate`

Validate all template configurations.

**Examples:**
```bash
navig template validate
```

**Related Commands:** `navig template list`, `navig template info`

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
navig host edit production
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
navig host inspect

# 4. Test connection
navig host test

# 5. View discovered info
navig host info
```

---

### Workflow: Deploy Application Files

```bash
# 1. Ensure correct host is active
navig host use production

# 2. Backup current files (optional)
navig run "cp -r /var/www/html /var/www/html.backup.$(date +%Y%m%d)"

# 3. Upload new files
navig upload ./dist /var/www/html

# 4. Set permissions
navig chown /var/www/html www-data:www-data --recursive

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
navig upload config.json /var/www/app/config.json

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
navig monitor-services

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
navig host info

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
navig host info

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
navig upload config.json /var/www/config.json
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
navig chown /path/to/file www-data:www-data

# Fix permissions
navig chmod /path/to/directory 755
navig chmod /path/to/file 644
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
navig upload local.txt /full/remote/path/local.txt
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

### 20.1 Workflow Management Commands

| Command | Description |
|---------|-------------|
| `navig workflow list` | List all available workflows |
| `navig workflow show <name>` | Display workflow definition |
| `navig workflow run <name>` | Execute a workflow |
| `navig workflow run <name> --dry-run` | Preview without executing |
| `navig workflow validate <name>` | Validate workflow syntax |
| `navig workflow create <name>` | Create new workflow from template |
| `navig workflow delete <name>` | Delete a workflow |
| `navig workflow edit <name>` | Open workflow in editor |

**Examples:**
```bash
# List available workflows
navig workflow list

# Preview a workflow
navig workflow run safe-deployment --dry-run

# Execute with variable overrides
navig workflow run db-snapshot --var host=staging --var db_name=mydb

# Skip all prompts
navig workflow run server-health --yes
```

### 20.2 Workflow Locations

| Location | Type | Priority |
|----------|------|----------|
| `.navig/workflows/` | Project-local | Highest |
| `~/.navig/workflows/` | Global | Medium |
| `navig/resources/workflows/` | Built-in | Lowest |

### 20.3 Built-in Workflows

NAVIG includes several example workflows:

| Workflow | Description |
|----------|-------------|
| `safe-deployment` | Deploy with health checks and rollback safety |
| `db-snapshot` | Export production database for local development |
| `emergency-debug` | Rapid diagnostics for failing services |
| `server-health` | Comprehensive server health check |

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
navig workflow run my-workflow --var host=staging --var db_name=testdb
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

**Interactive Menu:**
```bash
navig menu
# Navigate to: Agent & Gateway (G)
# Select: T - Start Telegram Bot (with Gateway)
# Or: B - Start Telegram Bot (standalone)
```

**Manual Setup:**
```bash
# Copy config template
cp .env.telegram.example .env

# Edit .env with your credentials:
# TELEGRAM_BOT_TOKEN=your_bot_token
# ALLOWED_TELEGRAM_USERS=your_user_id
# NAVIG_AI_MODEL=openrouter

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
| CLI      | Active    | `navig ai ask` for one-shot queries |
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
bash navig-core/scripts/bootstrap_navig_linux.sh
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

# Edit a credential
navig cred edit <id> --key sk-new-key --label "Updated Label"

# Delete a credential
navig cred delete <id>
navig cred delete <id> --force      # Skip confirmation

# Test credential validity
navig cred test openai              # Test by provider name
navig cred test <id>                # Test by credential ID

# Enable/disable without deleting
navig cred disable <id>
navig cred enable <id>

# Clone to another profile
navig cred clone <id> work --label "Work Copy"

# View audit log
navig cred audit                    # All entries
navig cred audit <id> --limit 20    # For specific credential

# List supported providers with validation
navig cred providers
```

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

```bash
# List sessions
curl http://localhost:8789/memory/sessions

# Get session history
curl http://localhost:8789/memory/sessions/my-task/history

# Add a message
curl -X POST http://localhost:8789/memory/messages \
  -H "Content-Type: application/json" \
  -d '{"session_key": "my-task", "role": "user", "content": "Hello"}'

# Search knowledge
curl "http://localhost:8789/memory/knowledge/search?q=database"

# Add knowledge
curl -X POST http://localhost:8789/memory/knowledge \
  -H "Content-Type: application/json" \
  -d '{"key": "my-key", "content": "Important info", "tags": ["project"]}'

# Get memory stats
curl http://localhost:8789/memory/stats
```

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

Every AI turn — whether through `navig ai ask`, the Gateway REST API, Telegram, or any
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

# Start the agent
navig agent start

# Check status
navig agent status
```

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

### 26.6 Troubleshooting

**"I can't fetch real-time data"**
- Web search MCP server is not enabled
- Run: `navig mcp enable brave-search`
- Ensure API key is set

**Query misclassified as DevOps?**
- Add "search for" or "look up" prefix
- Example: "Search for bitcoin price" vs just "bitcoin price"

**No results returned?**
- Check MCP server status: `navig mcp status`
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
| `--provider`, `-p` | string | auto | Provider: auto, brave, duckduckgo |
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

**Setup Brave Search (Recommended):**
```bash
# 1. Get API key from https://brave.com/search/api/
# 2. Set in environment or config
export BRAVE_API_KEY="your-api-key"
# Or: navig config set web.search.api_key=YOUR_KEY
```

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
| `navig history` | List recent operations |
| `navig history show <id>` | Show operation details |
| `navig history replay <id>` | Re-execute an operation |
| `navig history undo <id>` | Reverse an operation |
| `navig history export` | Export to JSON/CSV |
| `navig history clear` | Clear history |
| `navig history stats` | Show statistics |

### 29.3 Listing History

```bash
# List recent operations (default: last 20)
navig history

# List more operations
navig history --limit 50

# Filter by operation type
navig history --type ssh
navig history --type docker
navig history --type database

# Filter by status
navig history --status success
navig history --status failed

# Filter by host
navig history --host production

# Filter by time range
navig history --since "1 hour ago"
navig history --since "2024-01-15"
navig history --until "yesterday"

# Combine filters
navig history --type ssh --status failed --since "24 hours ago"
```

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
# Undo an operation (if reversible)
navig history undo abc123

# Force undo (even if risky)
navig history undo abc123 --force

# Dry-run undo
navig history undo abc123 --dry-run
```

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

```bash
# Export to JSON (default)
navig history export > audit.json

# Export to CSV
navig history export --format csv > audit.csv

# Export filtered range
navig history export --since "2024-01-01" --until "2024-01-31"

# Export only failed operations
navig history export --status failed
```

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
# Clear all history (requires confirmation)
navig history clear

# Clear old entries (keep last 30 days)
navig history clear --keep-days 30

# Clear only failed operations
navig history clear --status failed
```

### 29.10 Integration with Other Commands

Operations are recorded automatically when you run NAVIG commands:

```bash
# This SSH command is automatically recorded
navig ssh production "systemctl restart nginx"

# View the recorded operation
navig history --limit 1
```

The history system integrates with:
- **Context Management**: Operations tagged with current context
- **Workflows**: Multi-step workflows recorded as related operations
- **Agent Mode**: Agent actions fully audited
- **Memory System**: History informs AI suggestions

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
- Use `navig history` to view raw history data

---

## 33. � Packs System

Packs are shareable operations bundles containing runbooks, checklists, workflows, and templates. Install community packs or create your own reusable operations.

### 33.1 Quick Start

```bash
# List available packs
navig pack list

# Show pack details
navig pack show "Security Audit"

# Run a checklist interactively
navig pack run "Security Audit"

# Dry-run a runbook to preview
navig pack run "Database Backup Runbook" --dry-run
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

### 33.3 Pack Commands

```bash
# List all available packs
navig pack list
navig pack list --type runbook
navig pack list --installed

# Show pack details
navig pack show <name>

# Install a pack
navig pack install starter/deployment-checklist
navig pack install ./my-pack.yaml

# Uninstall a pack
navig pack uninstall <name>

# Run a pack
navig pack run <name>
navig pack run <name> --dry-run
navig pack run <name> --var host=prod --var db=mydb

# Create a new pack
navig pack create my-runbook --type runbook

# Search packs
navig pack search deploy
```

### 33.4 Running Packs

**Runbook (Auto-execute):**
```bash
# Preview without executing
navig pack run "Database Backup Runbook" --dry-run

# Execute with variables
navig pack run "Database Backup Runbook" --var host=production

# Non-interactive mode
navig pack run "Database Backup Runbook" --yes
```

**Checklist (Interactive):**
```bash
# Step through each item
navig pack run "Security Audit"

# Each step shows: [p]ass, [f]ail, [s]kip options
```

**Quick Actions Bundle:**
```bash
# View quick actions in pack
navig pack show "Quick DevOps Actions"

# Install quick actions to your shortcuts
navig pack run "Quick DevOps Actions"
```

### 33.5 Creating Packs

**Create a new local pack:**
```bash
navig pack create my-deployment --type checklist
# Creates: ~/.navig/packs/local/my-deployment/pack.yaml
```

**Pack YAML Format:**
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

## 38. NAVIG Ecosystem Products

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

Kickoff actions are synthesized from:

- `<space>/CURRENT_PHASE.md`
- `.navig/plans/DEV_PLAN.md`
- `.navig/plans/ROADMAP.md`

This keeps startup flow minimal: pick a space → get next actions instantly.

---

**Remember:** NAVIG is the secure, unified way to interact with remote servers. Direct SSH/database connections bypass security, tunnel management, and error handling. Always use NAVIG commands.

---

## 39. Device Identity (`navig node`)

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

## 40. Identity & Persona Management (`navig origin`)

Manage named origin identities used by the autonomous agent and Telegram bot.

| Command | Description |
|---------|-------------|
| `navig origin init` | Scaffold default identity at `~/.navig/identity/` |
| `navig origin show` | Display current active identity (default action) |
| `navig origin use <name>` | Switch active identity |
| `navig origin clear` | Remove active identity selection |
| `navig origin set-path <path>` | Point NAVIG at a non-default identity directory |
| `navig origin list` | List all registered identities |

**Storage:** `~/.navig/identity/` and `~/.navig/registry/formations/`

**Examples:**
```bash
navig origin list           # See all identities
navig origin use deepwatch  # Activate "deepwatch" persona
navig origin show           # Verify active identity
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

## 45. Portable Vault (`navig portable`)

Manage a *portable vault* — a self-contained, encrypted NAVIG config that can be carried on a USB drive or synced to an external path.

| Command | Description |
|---------|-------------|
| `navig portable status` | Show whether a portable vault is active and its path |
| `navig portable init <path>` | Initialise a new portable vault at `<path>` |
| `navig portable export <dest>` | Export current config into a portable vault archive |
| `navig portable enable <path>` | Mount a portable vault (overrides `~/.navig/`) |
| `navig portable disable` | Unmount portable vault and return to local config |

**Examples:**
```bash
navig portable status            # Is a portable vault active?
navig portable init /media/usb   # Set up vault on USB drive
navig portable enable /media/usb # Use it for this session
navig portable export ./backup   # Archive current config to portable format
navig portable disable           # Switch back to local ~/.navig/
```

**Security:** Portable vaults use the same AES-256 encryption as `navig backup export --encrypt`. Always keep the vault passphrase separate from the drive.

---

## 46. Package Runtime Notes (`navig package`)

`navig package load <id>` and startup autoload run dependency preflight before `on_load()`.

- Package dependencies in `depends_on.packages` must already be loaded.
- Missing pip dependencies in `depends_on.pip` are auto-installed before load.
- Autoload order is preserved exactly as listed in `packages_autoload.json`.
- Canonical Telegram package is `navig-telegram`.
- Older Telegram package IDs are auto-normalized to `navig-telegram` by `navig package load` and `navig package autoload`.
- New packages can be scaffolded directly from CLI: `navig package init <id> --type <commands|workflows|telegram|tools>`.
- Package quality can be checked across all manifests: `navig package audit` (use `--strict` to fail on warnings).

**Examples:**
```bash
navig package load navig-commands
navig package load navig-telegram
navig package autoload add navig-commands
navig package autoload add navig-telegram
navig package init my-new-pack --type workflows
navig package audit --strict
```

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
