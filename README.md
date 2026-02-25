<p align="center">
  <img src="navig-icons/navig-logo.svg" alt="NAVIG" width="120" />
</p>

<h1 align="center">NAVIG</h1>

<p align="center">
  <strong>No Admin Visible In Graveyard</strong><br/>
  Keep your servers alive. Forever.
</p>

<p align="center">
  <a href="https://github.com/navigrun/navig-core/actions"><img src="https://img.shields.io/github/actions/workflow/status/navigrun/navig-core/ci.yml?branch=main&style=flat-square&label=CI" alt="CI"></a>
  <a href="https://pypi.org/project/navig/"><img src="https://img.shields.io/pypi/v/navig?style=flat-square&color=blue" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square" alt="Python 3.10+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green?style=flat-square" alt="License"></a>
  <a href="https://buymeacoffee.com/navig-run"><img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-yellow?style=flat-square&logo=buymeacoffee" alt="Buy Me a Coffee"></a>
</p>

---

NAVIG is an autonomous infrastructure runtime and CLI that manages your servers, databases, containers, tunnels, and workflows from a single command line — or through Telegram, Matrix, and VS Code.

It combines a powerful CLI with an AI agent layer that can reason about your infrastructure, self-heal services, and execute multi-step operations across multiple hosts.

## Key Features

| Feature | Description |
|---|---|
| **Multi-Host Management** | SSH into any server. Switch hosts with `navig host use`. Run commands remotely. |
| **Database Operations** | Query, dump, restore, optimize — MySQL, MariaDB, PostgreSQL. |
| **Docker Control** | List, inspect, restart, and tail logs for containers and compose stacks. |
| **AI Chat Agent** | Conversational assistant via Telegram, Matrix, or CLI with multi-model routing. |
| **Credential Vault** | Encrypted secret storage. No plaintext tokens in config files. |
| **MCP Server** | Model Context Protocol integration for tool/resource sharing with AI editors. |
| **Forge Bridge** | Proxy VS Code Copilot models (GPT-4o, Claude, Gemini) to your server via SSH tunnel. |
| **Workflows** | Define multi-step automation flows. Preview with `--dry-run`. |
| **Skills & Templates** | Extensible skill packs and scaffolding templates for common server setups. |
| **Web Server Mgmt** | Nginx/Apache vhost management, config testing, reload. |

---

## Quick Install

### Linux / macOS / WSL

```bash
curl -fsSL https://raw.githubusercontent.com/navigrun/navig-core/main/install.sh | bash
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/navigrun/navig-core/main/install.ps1 | iex
```

### Install Options

| Parameter | Example | Description |
|---|---|---|
| `--version` | `bash -s -- --version 2.1.0` | Pin a specific version |
| `--dev` | `bash -s -- --dev` | Install with development extras |
| `--method git` | `bash -s -- --method git` | Clone from source instead of pip |
| `NAVIG_EXTRAS` | `NAVIG_EXTRAS=voice,keyring` | Comma-separated optional extras |
| `NAVIG_TELEGRAM_BOT_TOKEN` | `export NAVIG_TELEGRAM_BOT_TOKEN=...` | Auto-configure Telegram bot during install |

### From Source (Contributors)

```bash
git clone https://github.com/navigrun/navig-core.git
cd navig-core
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

## Getting Started

```bash
# 1. Initialize your workspace
navig init

# 2. Add a remote host
navig host add

# 3. Test the connection
navig host test

# 4. Run your first command
navig run "uname -a"

# 5. Check server health
navig host monitor show
```

That's it — you're managing a server. Everything else builds on these five commands.

---

## Command Overview

```
navig <resource> <action> [options]
```

| Command Group | What It Does |
|---|---|
| `navig host` | Add, switch, test, monitor remote hosts |
| `navig app` | Manage applications on hosts |
| `navig run` | Execute remote commands (supports base64, stdin, files) |
| `navig db` | Query, dump, restore, optimize databases |
| `navig docker` | List, restart, logs, exec, compose |
| `navig file` | Upload, download, view, edit remote files |
| `navig web` | Virtual hosts, config test, reload nginx/apache |
| `navig backup` | Export/import config, run server backups |
| `navig tunnel` | SSH tunnel management |
| `navig flow` | Multi-step workflow automation |
| `navig config` | Validate, edit, migrate configuration |
| `navig forge` | VS Code Copilot LLM bridge |
| `navig copilot` | Chat with Copilot models from CLI |
| `navig mcp` | MCP server for AI tool integration |
| `navig vault` | Encrypted credential management |

> Run `navig help <topic>` for detailed usage on any command group.

---

## AI Agent & Chat Channels

NAVIG includes a conversational AI agent that can be reached through:

- **Telegram** — `@your_bot` with natural language commands
- **Matrix** — federated chat protocol support
- **CLI** — `navig copilot ask "explain this error log"`
- **VS Code** — via the [NAVIG Forge](https://github.com/navigrun/navig-bridge) extension

The agent uses intelligent model routing across multiple LLM providers (GitHub Models, OpenAI, Ollama, OpenRouter) with automatic fallback chains and rate-limit handling.

---

## Configuration

All configuration lives in `~/.navig/`:

```
~/.navig/
├── config.yaml          # Main configuration
├── vault/               # Encrypted credentials
├── sessions/            # Chat session history
├── workspace/           # Identity & context files
│   ├── SOUL.md          # Agent personality
│   └── HEARTBEAT.md     # State tracking
└── logs/                # Runtime logs
```

Project-level overrides go in `.navig/` at the repository root.

> See [Configuration Schema](docs/user/CONFIG_SCHEMA.md) and [Usage Guide](docs/user/USAGE_GUIDE.md) for details.

---

## Documentation

| Guide | Path |
|---|---|
| **Installation** | [`docs/user/INSTALLATION.md`](docs/user/INSTALLATION.md) |
| **Quick Reference** | [`docs/user/QUICK_REFERENCE.md`](docs/user/QUICK_REFERENCE.md) |
| **CLI Commands** | [`docs/user/CLI_COMMANDS.md`](docs/user/CLI_COMMANDS.md) |
| **Handbook** | [`docs/user/HANDBOOK.md`](docs/user/HANDBOOK.md) |
| **Troubleshooting** | [`docs/user/troubleshooting.md`](docs/user/troubleshooting.md) |
| **Telegram Bot** | [`docs/features/TELEGRAM.md`](docs/features/TELEGRAM.md) |
| **Forge LLM Bridge** | [`docs/forge-llm-bridge.md`](docs/forge-llm-bridge.md) |
| **Architecture** | [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md) |
| **Plugin Development** | [`docs/dev/PLUGIN_DEVELOPMENT.md`](docs/dev/PLUGIN_DEVELOPMENT.md) |
| **Production Deploy** | [`docs/dev/PRODUCTION_DEPLOYMENT.md`](docs/dev/PRODUCTION_DEPLOYMENT.md) |
| **Migration Guide** | [`docs/dev/MIGRATION_GUIDE.md`](docs/dev/MIGRATION_GUIDE.md) |

---

## Development

```bash
# Lint
ruff check navig tests

# Format
ruff format --check navig tests

# Test (65% coverage gate)
pytest

# Build wheel
python -m build
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full contribution workflow.

---

## Project Structure

```
navig-core/
├── navig/               # Main Python package
│   ├── agent/           # AI client, providers, model routing
│   ├── commands/        # CLI command modules
│   ├── gateway/         # Multi-channel gateway (Telegram, Matrix, web)
│   ├── daemon/          # Background service entry point
│   └── resources/       # Default SOUL, prompts, personas
├── skills/              # Extensible skill packs (JSON/YAML)
├── templates/           # Server scaffolding templates
├── scripts/             # Install & deployment scripts
├── deploy/              # Docker, systemd, hardening configs
├── docs/                # Full documentation tree
└── tests/               # Test suite
```

---

## Ecosystem

| Repository | Purpose |
|---|---|
| [navig-core](https://github.com/navigrun/navig-core) | CLI runtime, agent, daemon (this repo) |
| [navig-bridge](https://github.com/navigrun/navig-bridge) | VS Code extension — Copilot LLM bridge, chat UI |
| [navig-deck](https://github.com/navigrun/navig-deck) | Web dashboard for remote management |
| [navig-os](https://github.com/navigrun/navig-os) | Operating system UI layer |

---

## Support the Project

NAVIG is built and maintained independently. If it saves you time, consider supporting development:

<p align="center">
  <a href="https://buymeacoffee.com/navig-run"><img src="https://img.shields.io/badge/☕_Buy_Me_a_Coffee-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black" alt="Buy Me a Coffee" height="36"></a>
  &nbsp;&nbsp;
  <a href="https://patreon.com/c/navig-run"><img src="https://img.shields.io/badge/🧪_Alchemist_Lab_(Patreon)-FF424D?style=for-the-badge&logo=patreon&logoColor=white" alt="Patreon" height="36"></a>
</p>

- **Buy Me a Coffee** — One-time support → [buymeacoffee.com/navig-run](https://buymeacoffee.com/navig-run)
- **Alchemist Lab (Patreon)** — Monthly support, early access, roadmap input → [patreon.com/c/navig-run](https://patreon.com/c/navig-run)

---

## Security

Found a vulnerability? Please report it privately — see [`SECURITY.md`](SECURITY.md) for instructions and response SLA.

## License

Apache-2.0 — see [`LICENSE`](LICENSE).

Brand usage, official build identity, and the hosted ecosystem are governed separately:
[`TRADEMARK.md`](TRADEMARK.md) · [`OFFICIAL_BUILDS.md`](OFFICIAL_BUILDS.md) · [`GOVERNANCE.md`](GOVERNANCE.md)


---

> **2026-02-18 — Monorepo consolidation:**
> `navig-core/host/_rust_legacy/` has been consolidated into `navig-core/host/_rust_legacy/`.
> The host supervisor is being rewritten in Go under `navig-core/host/`.

