# NAVIG

<p align="center">
  <img src="navig-icons/navig-logo.svg" alt="NAVIG" width="120" />
</p>

<h1 align="center">NAVIG</h1>

<p align="center">
  <strong>No Admin Visible In Graveyard</strong><br/>
  Keep your servers alive. Forever.
</p>

<p align="center">
  <a href="https://github.com/navig-run/core/actions"><img src="https://img.shields.io/github/actions/workflow/status/navig-run/core/ci.yml?branch=main&style=flat-square&label=CI" alt="CI"></a>
  <a href="https://pypi.org/project/navig/"><img src="https://img.shields.io/pypi/v/navig?style=flat-square&color=blue" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square" alt="Python 3.10+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green?style=flat-square" alt="License"></a>
  <a href="https://github.com/sponsors/navig-run"><img src="https://img.shields.io/badge/GitHub%20Sponsors-support-pink?style=flat-square" alt="GitHub Sponsors"></a>
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey?style=flat-square" alt="Platform">
</p>

> [!WARNING]
> **Experimental software**
> NAVIG is under active development and not yet stable.
> APIs, CLI commands, and config formats may change between releases.

> 🧵 Not vibe-coded. ⚒️ Built by [@navig-run](https://github.com/navig-run) · solo founder · 



---

NAVIG is an infrastructure CLI and runtime for managing remote servers, databases, containers, tunnels, and operator workflows from one place.

It is built for people who are tired of juggling SSH sessions, scattered scripts, pasted commands, and fragile dashboards just to keep systems running.

NAVIG combines direct infrastructure control with an AI-assisted operator layer, so you can inspect, operate, troubleshoot, and automate across multiple hosts from the terminal.

---

## What NAVIG does

| Feature | Description |
|---|---|
| **Multi-host management** | Add, switch, test, and operate remote hosts over SSH |
| **Database operations** | Query, dump, restore, and maintain MySQL, MariaDB, and PostgreSQL |
| **Docker control** | Inspect containers, restart services, tail logs, and work with compose stacks |
| **Remote execution** | Run commands, pass stdin, transfer files, and script repeatable operations |
| **Web server management** | Manage nginx/apache configs, test changes, and reload safely |
| **Encrypted vault** | Store credentials without leaving secrets in plaintext config files |
| **Workflows** | Define repeatable multi-step flows with preview and dry-run support |
| **AI operator layer** | Use natural language in CLI or chat channels to assist with infra tasks |
| **Mesh networking** | Peer-to-peer node discovery and command delegation over LAN |
| **MCP integration** | Expose tools and resources to AI editors and compatible clients |

---

## Why it exists

Most infrastructure tools either stop at raw command execution or try to trap everything behind a web panel.

NAVIG takes a different approach:

- **terminal-first**
- **operator-focused**
- **remote-friendly**
- **automation-ready**
- **AI-assisted, not AI-dependent**

You stay close to the real machine, but with better structure, safer workflows, and less repeated manual work.

---

## Install

### Linux / macOS / WSL

```bash
curl -fsSL https://raw.githubusercontent.com/navig-run/core/main/install.sh | bash
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/navig-run/core/main/install.ps1 | iex
```

### From PyPI

```bash
pip install navig
```

### Install options

| Parameter | Example | Description |
|---|---|---|
| `--version` | `bash -s -- --version 2.1.0` | Install a specific version |
| `--dev` | `bash -s -- --dev` | Install development extras |
| `--method git` | `bash -s -- --method git` | Install from source instead of PyPI |
| `NAVIG_EXTRAS` | `NAVIG_EXTRAS=voice,keyring` | Enable optional extras |
| `NAVIG_TELEGRAM_BOT_TOKEN` | `export NAVIG_TELEGRAM_BOT_TOKEN=...` | Preconfigure Telegram bot support during install |

### From source

```bash
git clone https://github.com/navig-run/core.git
cd core
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

Initialize NAVIG:

```bash
navig init
```

This creates the local `.navig/` directory, runs the setup wizard, and helps you test your first host connection.

Example flow:

```bash
navig host add
navig host test
navig host use <name>
navig run "uname -a"
```

---

## Command reference

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
| `navig mcp` | MCP server for tool and AI editor integration |
| `navig gateway` | Start and manage chat gateway (Telegram, Matrix) |

### Organisation

| Command | What it does |
|---|---|
| `navig config` | View, validate, and manage configuration |
| `navig vault` | Encrypted credential storage |
| `navig workspace` | Multi-context operator workspace |
| `navig mesh` | LAN peer-to-peer node discovery and delegation |

Run `navig help` or `navig help <topic>` for usage details.

---

## AI and chat interfaces

NAVIG can expose its operator layer through multiple interfaces:

- CLI
- Telegram
- Matrix
- MCP-compatible editors and tools

Example:

```bash
navig copilot ask "Explain why this container keeps restarting"
```

The AI layer is there to assist with reasoning and workflow execution. It does not replace direct operator control.

---

## Configuration

Global config lives in `~/.navig/` by default. Use `NAVIG_CONFIG_DIR` to override.

```
~/.navig/
├── config.yaml
├── vault/
├── sessions/
├── workspace/
│   ├── SOUL.md
│   └── HEARTBEAT.md
└── logs/
```

Project-specific overrides can live in a local `.navig/` directory at repository root.

See [`docs/user/CONFIG_SCHEMA.md`](docs/user/CONFIG_SCHEMA.md) and [`docs/user/USAGE_GUIDE.md`](docs/user/USAGE_GUIDE.md).

---

## Documentation

| Guide | Path |
|---|---|
| **Installation** | [`docs/user/INSTALLATION.md`](docs/user/INSTALLATION.md) |
| **Quick reference** | [`docs/user/QUICK_REFERENCE.md`](docs/user/QUICK_REFERENCE.md) |
| **CLI commands** | [`docs/user/CLI_COMMANDS.md`](docs/user/CLI_COMMANDS.md) |
| **Handbook** | [`docs/user/HANDBOOK.md`](docs/user/HANDBOOK.md) |
| **Troubleshooting** | [`docs/user/troubleshooting.md`](docs/user/troubleshooting.md) |
| **Telegram** | [`docs/features/TELEGRAM.md`](docs/features/TELEGRAM.md) |
| **Plugin / pack development** | [navig-community](https://github.com/navig-run/community) |
| **Production deployment** | [`docs/dev/PRODUCTION_DEPLOYMENT.md`](docs/dev/PRODUCTION_DEPLOYMENT.md) |
| **Migration guide** | [`docs/dev/MIGRATION_GUIDE.md`](docs/dev/MIGRATION_GUIDE.md) |

---

## Development

```bash
ruff check navig tests
ruff format --check navig tests
pytest
python -m build
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full contribution workflow.

---

## Community & support

| Channel | |
|---|---|
| Bug reports & features | [GitHub Issues](https://github.com/navig-run/core/issues) |
| Ideas & discussion | [GitHub Discussions](https://github.com/navig-run/core/discussions) |
| Telegram channel | [t.me/navigrun](https://t.me/navigrun) — news & announcements |
| Telegram community | [t.me/+OyFMwN66c3M0NTk0](https://t.me/+OyFMwN66c3M0NTk0) — group for people running NAVIG |
| Security reports | [SECURITY.md](SECURITY.md) |

---

## Project structure

```
core/
├── navig/               # Main Python package
│   ├── agent/           # AI client, providers, model routing
│   ├── commands/        # CLI command modules
│   ├── gateway/         # Chat and gateway integrations
│   ├── daemon/          # Background service entry point
│   └── resources/       # Default prompts, personas, assets
├── skills/              # Skill packs
├── templates/           # Scaffolding templates
├── scripts/             # Install and deployment scripts
├── deploy/              # Docker, systemd, hardening configs
├── docs/                # Documentation
└── tests/               # Test suite
```

---

## Sponsor NAVIG

NAVIG is built by one person and released as open-source software.

If it saves you time, prevents mistakes, or becomes part of your workflow, consider backing it:

<p align="center">
  <a href="https://github.com/sponsors/navig-run"><img src="https://img.shields.io/badge/❤️_GitHub_Sponsors-EA4AAA?style=for-the-badge&logo=githubsponsors&logoColor=white" alt="GitHub Sponsors" height="40"></a>
  &nbsp;&nbsp;
  <a href="https://buymeacoffee.com/navig-run"><img src="https://img.shields.io/badge/☕_Buy_Me_a_Coffee-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black" alt="Buy Me a Coffee" height="40"></a>
</p>

| Tier | | What you're doing |
|---|---|---|
| 📡 **Watcher** | $5/mo | Simple backing. Badge + optional name in supporters. |
| ⚡ **Node Operator** | $10/mo | You run this in real workflows. Early betas + dev updates. |
| 🏗️ **System Architect** | $25/mo | Long-term backing. Roadmap input + private changelogs. |
| ⚡ **Signal Boost** | $10 one-time | Small direct contribution. |
| 🚀 **Feature Sprint** | $50 one-time | Funds a focused development push. |
| 🌐 **Founding Node** | $100 one-time | Permanent credit in `FOUNDERS.md`. Limited availability. |

See [FUNDING.md](FUNDING.md) for full details.

---

---

## Security

If you found a vulnerability, please report it privately. See [`SECURITY.md`](SECURITY.md).

---

## License

Apache-2.0 — see [`LICENSE`](LICENSE).

Brand usage and official build identity are governed separately: [`TRADEMARK.md`](TRADEMARK.md) · [`GOVERNANCE.md`](GOVERNANCE.md)



