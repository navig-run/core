<p align="center">
  <img src="logo.svg" alt="NAVIG" width="100" />
</p>

<h1 align="center">NAVIG</h1>

<p align="center">
  <strong>No Admin Visible In Graveyard</strong><br/>
  The terminal was never the problem. The chaos around it was.
</p>

<p align="center">
  <a href="https://github.com/navig-run/core/actions"><img src="https://img.shields.io/github/actions/workflow/status/navig-run/navig-core/ci.yml?branch=main&style=flat-square&label=CI" alt="CI"></a>
  <a href="https://pypi.org/project/navig/"><img src="https://img.shields.io/pypi/v/navig?style=flat-square&color=blue" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square" alt="Python 3.10+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green?style=flat-square" alt="License"></a>
  <a href="https://github.com/sponsors/navig-run"><img src="https://img.shields.io/badge/GitHub%20Sponsors-support-pink?style=flat-square" alt="Sponsor"></a>
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey?style=flat-square" alt="Platform">
</p>

> [!WARNING]
> **NAVIG is experimental software under active development.**
> APIs, CLI commands, and config formats may change between releases. Not yet recommended for production-critical environments without review.

---

## What is NAVIG?

NAVIG is a **terminal-first infrastructure CLI and runtime** for people who are tired of juggling SSH sessions, scattered scripts, disconnected dashboards, and ad-hoc credentials just to keep their systems running.

It was built by one person — a solo founder managing a growing swarm of remote machines, projects, and operational overhead with no platform team to fall back on. The problem wasn't the terminal. The problem was everything fragmented around it: SSH in one place, SFTP in another, databases behind dashboards, secrets pasted into notes, logs spread across providers, and no single coherent surface to operate from.

NAVIG is the result of refusing to accept that reality.

It gives operators direct control over remote hosts, databases, containers, files, tunnels, and workflows — from one place, with structure, memory, and optional AI assistance that stays out of the way until it's actually useful.

**One operator surface. For operators.**

---

## Features

| Capability | Description |
|---|---|
| **Multi-host management** | Add, switch, test, and operate remote hosts over SSH |
| **Remote execution** | Run commands, pipe stdin, use base64 encoding for complex scripts |
| **Database operations** | Query, dump, restore, and maintain MySQL, MariaDB, and PostgreSQL |
| **File operations** | Upload, download, read, edit, and list remote files safely |
| **Docker & containers** | Inspect containers, restart services, tail logs, manage compose stacks |
| **Web server control** | Manage nginx/apache configs, test changes, reload safely |
| **Encrypted vault** | Store credentials without leaving secrets in plaintext config files |
| **Workflows** | Define repeatable multi-step flows with dry-run and preview support |
| **AI operator layer** | Natural language assistance for infra tasks — CLI, Telegram, or MCP |
| **Mesh networking** | LAN peer-to-peer node discovery and command delegation |
| **MCP integration** | Expose tools and resources to AI editors and compatible clients |
| **Daemon & gateway** | Background service with Telegram and Matrix channel support |

---

## Install

### pipx (recommended)

```bash
pipx install navig
```

### Linux / macOS / WSL

```bash
curl -fsSL https://navig.run/install.sh | bash
```

### Windows (PowerShell)

```powershell
irm https://navig.run/install.ps1 | iex
```

### pip

```bash
pip install navig
```

### Install options

| Option | Example | Description |
|---|---|---|
| `--version` | `bash -s -- --version 2.4.14` | Pin a specific version |
| `--dev` | `bash -s -- --dev` | Include development extras |
| `--method git` | `bash -s -- --method git` | Install from source instead of PyPI |
| `NAVIG_EXTRAS` | `NAVIG_EXTRAS=voice,keyring` | Enable optional extras |
| `NAVIG_TELEGRAM_BOT_TOKEN` | `export NAVIG_TELEGRAM_BOT_TOKEN=...` | Pre-configure Telegram during install |

### Development Install

```bash
git clone https://github.com/navig-run/core.git navig
cd navig
pip install -e ".[dev]"
```

With a virtual environment (optional but recommended):

```bash
git clone https://github.com/navig-run/core.git navig
cd navig
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### Verify

```bash
navig --version
navig --help
```

---

## Quick start

```bash
# 1. Initialize — creates ~/.navig/, runs setup wizard
navig init

# 2. Add a remote host
navig host add

# 3. Test the connection
navig host test

# 4. Set it as active
navig host use <name>

# 5. Run your first remote command
navig run "uname -a"
```

That's it. Everything else builds from here.

---

## Command reference

The pattern is consistent across all resources:

```
navig <resource> <action> [options]
```

### Infrastructure

| Command | What it does |
|---|---|
| `navig host` | Add, switch, test, and inspect remote hosts |
| `navig run` | Execute commands on remote or local hosts |
| `navig file` | Upload, download, read, and edit remote files |
| `navig db` | Query, dump, restore, and maintain databases |
| `navig docker` | Container and compose operations |
| `navig web` | Web server config, test, and reload |
| `navig tunnel` | SSH tunnel management |
| `navig backup` | Config and data backup / restore |

### AI & Automation

| Command | What it does |
|---|---|
| `navig copilot` | AI-assisted troubleshooting and command guidance |
| `navig flow` | Multi-step automation workflows |
| `navig mcp` | MCP server for AI editor and tool integration |
| `navig gateway` | Start and manage chat gateway (Telegram, Matrix) |

### Organisation

| Command | What it does |
|---|---|
| `navig config` | View, validate, and manage configuration |
| `navig vault` | Encrypted credential storage |
| `navig workspace` | Multi-context operator workspace |
| `navig mesh` | LAN peer discovery and command delegation |

Run `navig help` or `navig help <topic>` for detailed usage. Every command also supports `--help`.

---

## AI and chat interfaces

NAVIG can expose its operator layer through:

- CLI (`navig copilot`)
- Telegram (via `navig gateway`)
- Matrix
- Any MCP-compatible editor (Cursor, Claude Desktop, VS Code)

```bash
navig copilot ask "Why does this container keep restarting?"
```

The AI layer assists with reasoning, context, and workflow execution. It does not replace direct operator control — the operator stays responsible, the operator stays in command.

---

## Configuration

Global config lives in `~/.navig/`. Override with `NAVIG_CONFIG_DIR`.

```
~/.navig/
├── config.yaml       ← main config
├── vault/            ← encrypted credentials
├── sessions/
├── workspace/
│   ├── SOUL.md       ← persistent operator identity
│   └── HEARTBEAT.md
└── logs/
```

Project-specific overrides: place a `.navig/` directory at your repository root. Project config takes precedence over global config.

See [`docs/user/CONFIG_SCHEMA.md`](docs/user/CONFIG_SCHEMA.md) and [`docs/user/USAGE_GUIDE.md`](docs/user/USAGE_GUIDE.md).

---

## Documentation

| Guide | |
|---|---|
| Installation | [`docs/user/INSTALLATION.md`](docs/user/INSTALLATION.md) |
| Quick reference | [`docs/user/QUICK_REFERENCE.md`](docs/user/QUICK_REFERENCE.md) |
| CLI commands | [`docs/user/CLI_COMMANDS.md`](docs/user/CLI_COMMANDS.md) |
| Handbook | [`docs/user/HANDBOOK.md`](docs/user/HANDBOOK.md) |
| Troubleshooting | [`docs/user/troubleshooting.md`](docs/user/troubleshooting.md) |
| Telegram setup | [`docs/features/TELEGRAM.md`](docs/features/TELEGRAM.md) |
| Production deployment | [`docs/dev/PRODUCTION_DEPLOYMENT.md`](docs/dev/PRODUCTION_DEPLOYMENT.md) |
| Migration guide | [`docs/dev/MIGRATION_GUIDE.md`](docs/dev/MIGRATION_GUIDE.md) |
| Plugin / pack development | [navig-community](https://github.com/navig-run/community) |

---

## Development

```bash
# Lint
ruff check navig tests
ruff format --check navig tests

# Test
pytest

# Build
python -m build
```

**Quick checks before a PR:**

```bash
python -c "import navig"             # no import errors
navig --help                         # CLI loads in < 1s
pytest tests/ -q                     # all green
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full contribution workflow, branch model, and commit conventions.

---

## Community & support

| Channel | |
|---|---|
| Bug reports & feature requests | [GitHub Issues](https://github.com/navig-run/core/issues) |
| Ideas & discussion | [GitHub Discussions](https://github.com/navig-run/core/discussions) |
| Announcements | [t.me/navigrun](https://t.me/navigrun) |
| Community group | [t.me/+OyFMwN66c3M0NTk0](https://t.me/+OyFMwN66c3M0NTk0) |
| Security reports | [SECURITY.md](SECURITY.md) |

---

## Project structure

```
navig/
├── navig/               # Main Python package
│   ├── agent/           # AI client, providers, model routing
│   ├── commands/        # CLI command modules (one file per group)
│   ├── gateway/         # Chat and gateway integrations
│   ├── daemon/          # Background service entry point
│   └── resources/       # Default prompts, personas, assets
├── skills/              # Skill packs
├── templates/           # Scaffolding templates
├── scripts/             # Install and deployment scripts
├── deploy/              # Docker, systemd, hardening configs
├── docs/                # Documentation
└── tests/               # pytest test suite
```

---

## Sponsor

NAVIG is built by one person and released as open-source. If it saves you time, prevents mistakes, or earns a place in your workflow:

<p align="center">
  <a href="https://github.com/sponsors/navig-run"><img src="https://img.shields.io/badge/❤️_GitHub_Sponsors-EA4AAA?style=for-the-badge&logo=githubsponsors&logoColor=white" alt="GitHub Sponsors" height="40"></a>
  &nbsp;&nbsp;
  <a href="https://buymeacoffee.com/navig-run"><img src="https://img.shields.io/badge/☕_Buy_Me_a_Coffee-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black" alt="Buy Me a Coffee" height="40"></a>
</p>

| Tier | | |
|---|---|---|
| 📡 **Watcher** | $5/mo | Simple backing. Badge + optional name in supporters. |
| ⚡ **Node Operator** | $10/mo | You run this in real workflows. Early betas + dev updates. |
| 🏗️ **System Architect** | $25/mo | Long-term support. Roadmap input + private changelogs. |
| ⚡ **Signal Boost** | $10 one-time | Small direct contribution. |
| 🚀 **Feature Sprint** | $50 one-time | Funds a focused development push. |
| 🌐 **Founding Node** | $100 one-time | Permanent credit in `FOUNDERS.md`. Limited. |

See [FUNDING.md](FUNDING.md) for full details.

---

## Security

Report vulnerabilities privately. See [`SECURITY.md`](SECURITY.md).

---

## License

Apache-2.0 — see [`LICENSE`](LICENSE).

Brand usage and official build identity: [`TRADEMARK.md`](TRADEMARK.md) · [`GOVERNANCE.md`](GOVERNANCE.md)

---

<p align="center">
  Forge-coded by <a href="https://github.com/miztizm">@miztizm</a>
</p>
