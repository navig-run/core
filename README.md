<p align="center">
  <img src="logo.svg" alt="NAVIG" width="100" />
</p>

<h1 align="center">NAVIG</h1>

<p align="center">
  <strong>No Admin Visible In Graveyard</strong><br/>
  The terminal was never the problem. The chaos around it was.
</p>

<p align="center">
  <a href="https://github.com/navig-run/core/actions"><img src="https://img.shields.io/github/actions/workflow/status/navig-run/core/ci.yml?branch=main&style=flat-square&label=CI" alt="CI"></a>
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

## Prerequisites

NAVIG CLI uses [Nerd Font](https://www.nerdfonts.com/) glyphs for Powerline-style status bars and icons. Install once per machine:

```powershell
pwsh scripts/Install-NerdFont.ps1
```

| Platform | Recommended terminal | Additional step |
|---|---|---|
| Windows | Windows Terminal | Script patches automatically |
| macOS | iTerm2 or Kitty | Set font to **JetBrainsMono Nerd Font Mono** after script runs |
| Linux | Kitty, Alacritty, GNOME Terminal | Set font manually after script runs |

VS Code is patched automatically on all platforms. Restart VS Code and your terminal after running the script.

> **Offline install**: set `$env:NAVIG_FONT_ZIP` to a local zip path, or pass `-LocalZip <path>`.

---

## What is NAVIG?

NAVIG is a **terminal-first infrastructure CLI and runtime** for people who are tired of juggling SSH sessions, scattered scripts, disconnected dashboards, and ad-hoc credentials just to keep their systems running.

It was built by one person — a solo founder managing a growing swarm of remote machines, projects, and operational overhead with no platform team to fall back on. The problem wasn't the terminal. The problem was everything fragmented around it: SSH in one place, SFTP in another, databases behind dashboards, secrets pasted into notes, logs spread across providers, and no single coherent surface to operate from.

NAVIG is the result of refusing to accept that reality.

It gives operators direct control over remote hosts, databases, containers, files, tunnels, and workflows — from one place, with structure, memory, and optional AI assistance that stays out of the way until it's actually useful.

**One operator surface. For operators.**

---

## NAVIG Core (free) vs NAVIG Deck (paid)

NAVIG ships in two layers with a deliberate split:

- **NAVIG Core** (this repo) — Apache-2.0 open source. The daemon, CLI,
  agent runtime, vault, scheduler, mesh, MCP server, Telegram text bot,
  gateway. **Free forever, no host limits via the CLI, no telemetry.**
- **NAVIG Deck** — the polished UI cockpit (proprietary; signed PyPI
  wheel installed via `pip install navig[deck]`). Hosts inventory,
  mesh topology, vault UI, mission queue, workflow runner, Telegram
  Mini App. Free Solo tier (1 host); paid tiers and modules at
  [navig.run/deck](https://navig.run/deck).

The split is honest: the daemon is open and audit-able; the polished
GUI is the paid product. Pack owners get the local app forever,
self-hosted; subscribers add host scale and the hosted cloud relay we
run on `relay.navig.run`. See [docs/BILLING.md](docs/BILLING.md) for the
full pricing model.

**What's free, forever, no matter what**:
- The entire CLI — unlimited `navig ssh`, unlimited hosts you connect to
- Telegram text bot (`/start`, `/run`, `/status`, all commands)
- Agent runtime (`navig ask`, conversation memory, missions)
- VS Code MCP integration
- Vault, scheduler, mesh peer discovery
- All updates, including major versions

The Deck UI is what you pay for if you want it; **the daemon never
will be**.

---

## Why NAVIG?

Most operators already have SSH. What they don't have is everything that should live around it:

| You probably have | NAVIG adds |
|---|---|
| SSH client | Multi-host management with one active context |
| Ad-hoc shell scripts | Named workflows with dry-run and preview |
| Secrets in `.env` files | Encrypted vault with context-aware resolution |
| `grep` in log files | Structured log tailing across hosts |
| Copy-paste from Stack Overflow | AI operator layer with your infra as context |
| One terminal per machine | Mesh networking and command delegation |

NAVIG is not a configuration management tool (not Ansible). It is not a deployment platform (not Kubernetes). It is a **control plane for humans** — the thing you reach for when you need to do something to a real machine, right now, without writing a playbook.

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
| **Telegram Manager** | Full-account Telegram organizer (MTProto): search/move/forward/dedupe + a business-conversation AI catcher, emoji-AI, deletion alerts, and rich-message replies — owner-only ([docs](docs/TELEGRAM_MANAGER.md) · [cheatsheet](docs/TELEGRAM_CHEATSHEET.md)) |
| **GitHub mirroring** | `navig github` — search, backup, and clone GitHub repos (flat or nested) via [farmore](https://github.com/miztizm/farmore), token from the vault |
| **TikTok** | `navig tiktok` — download videos, extract metadata + top comments, and AI-brief any link (via [rapidok](https://github.com/miztizm/rapidok) + yt-dlp) |

---

## Install

### Linux / macOS / WSL

```bash
curl -fsSL https://navig.run/install.sh | bash
```

### Windows (PowerShell)

```powershell
iwr -useb https://navig.run/install.ps1 | iex
```

### pipx

```bash
pipx install navig
```

### pip

```bash
pip install navig
```

### Install options

| Option | Example | Description |
|---|---|---|
| `--version` | `bash -s -- --version <release>` | Pin a specific version |
| `--dev` | `bash -s -- --dev` | Include development extras |
| `--method git` | `bash -s -- --method git` | Install from source instead of PyPI |
| `NAVIG_EXTRAS` | `NAVIG_EXTRAS=voice,keyring` | Enable optional extras |
| `NAVIG_INSTALL_PROFILE` | `NAVIG_INSTALL_PROFILE=operator` | Choose the first-run bootstrap profile |

Telegram bootstrap is available through the installer pipeline after install:

```bash
NAVIG_TELEGRAM_BOT_TOKEN="<your-bot-token>" navig init --profile operator
```

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

## No server? Start here (local mode)

If you don't have a remote server available yet, NAVIG works on localhost too:

```bash
# Install
pip install navig

# Discover your local machine as a host
navig host discover-local

# Run your first command (on this machine)
navig run "echo hello from NAVIG && uname -a"

# Explore files
navig file list ~ --tree --depth 2

# Ask the AI operator layer something
navig ask "what process is using the most CPU right now?"
```

That's a complete first run — no remote server required.

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

# 6. Ask the AI operator layer something about it
navig ask "what is consuming the most memory on this host?"
```

That's it. Everything else builds from here.

**Where to go next** — after that first `navig run`:

| Command | What it does |
|---|---|
| `navig host add` | Add more hosts |
| `navig vault set KEY=value` | Store secrets encrypted |
| `navig db query "SELECT 1"` | Connect to a remote database |
| `navig tunnel 5432` | Open an SSH tunnel to a port |
| `navig flow run deploy.yaml` | Run a multi-step workflow |
| `navig ask "..."` | Ask the AI operator layer anything |

---

## Command reference

The pattern is consistent across all resources:

```bash
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
| `navig ask` | AI-assisted troubleshooting and command guidance |
| `navig flow` | Multi-step automation workflows |
| `navig mcp` | MCP server for AI editor and tool integration |
| `navig gateway` | Start and manage chat gateway (Telegram, Matrix) |
| `navig agent` | Autonomous agent runtime — install, start, configure, manage |
| `navig telegram` | Telegram Manager — login, search, organize, business catcher, emoji rights |
| `navig github` | Search / backup / clone GitHub repos (flat by default) — token from the vault |
| `navig tiktok` | Download TikTok content + AI briefings (description + top comments) |

### Organisation

| Command | What it does |
|---|---|
| `navig config` | View, validate, and manage configuration |
| `navig vault` | Encrypted credential storage |
| `navig space` | Multi-context operator environments |
| `navig mesh` | LAN peer discovery and command delegation |

Run `navig help` or `navig help <topic>` for detailed usage. Every command also supports `--help`.

---

## Agent runtime

NAVIG ships an autonomous agent layer that runs as a persistent background process.

```bash
# One-time setup: write ~/.navig/agent/config.yaml and create runtime directories
navig agent install

# Optional: register as an OS service (starts on boot)
navig service install
navig service start

# Check state
navig agent status
```

Operating modes: `supervised` (default, confirms actions), `autonomous` (executes without prompts), `observe-only` (reads and reports, no writes).
Personality profiles: `friendly`, `professional`, `witty`, `paranoid`, `minimal`.

See [`docs/agent/install.md`](docs/agent/install.md) for the full reference.

---

## AI and chat interfaces

NAVIG can expose its operator layer through:

- CLI (`navig ask`)
- Telegram (via `navig gateway`)
- Matrix
- Any MCP-compatible editor (Cursor, Claude Desktop, VS Code)

```bash
navig ask "Why does this container keep restarting?"
```

The AI layer assists with reasoning, context, and workflow execution. It does not replace direct operator control — the operator stays responsible, the operator stays in command.

---

## Cloud routing (relay.navig.run)

NAVIG's hosted Deck lives at [`relay.navig.run`](https://relay.navig.run). It's a static React app + a tiny **broker** (Cloudflare Pages Functions + D1) that maps your daemon's API key (and your Telegram user_id) to its current public URL. Your daemon registers itself with the broker on every boot; the Deck and the Telegram Mini App resolve "where is my daemon" via the broker. **Daemon data never leaves your machine** — the broker only stores routing metadata (a SHA-256 of your api_key, your current public URL, and the Telegram bindings).

### Modes — pick one

| Mode | Use when | How to enable | Subscription needed? |
|---|---|---|---|
| **Lighthouse** *(recommended)* | You want always-on access (Telegram / SMS / remote Deck) with no tunnel and no domain. | `navig lighthouse deploy` deploys a tiny edge to **your own** Cloudflare account (no Node/wrangler); the brain dials out to it over one WebSocket. See [docs/LIGHTHOUSE.md](docs/LIGHTHOUSE.md). | None — self-hosted on your Cloudflare, free for everyone. |
| **cloudflared / hosted relay** | Laptop, home machine, anything behind NAT. No public IP, no domain. | The daemon spawns `cloudflared` to create a free `*.trycloudflare.com` quick tunnel. Manual: `navig cloud connect`. | Free for new Solo users + active subscribers. Lapsed subscribers get a 30-day grace; perpetual-only buyers self-host via Lighthouse/Tailscale instead. |
| **Tailscale Funnel** | You want a free, stable, subscription-free public URL for your Mini App. | `navig cloud tailscale --enable` — bring up a `*.ts.net` URL via Tailscale Funnel, then `navig miniapp register` to push it to BotFather. | None — direct mode, broker not involved. |
| **direct (your domain)** | VPS with a public domain and a reverse proxy (nginx, Caddy, Traefik). | `navig cloud direct https://navig.example.com` — daemon skips `cloudflared` entirely and registers your URL with the broker. Also picks up `$NAVIG_PUBLIC_URL` from systemd `Environment=` lines. | None — direct mode, broker not involved. |
| **off** | Fully local-only. Don't talk to the broker at all. | `navig cloud disconnect` (or `cloud.enabled: false` in `~/.navig/config.yaml`). | None. |

> **The subscription-vs-perpetual split**: the hosted cloud relay is
> the one feature that costs us money per month. So it's a subscription
> feature. Perpetual-pack buyers get the full local app forever and
> self-host the Mini App with a single Tailscale command — free,
> stable, costs us nothing. See [docs/CLOUD.md](docs/CLOUD.md) for the
> full topology.

### Always-on in 3 commands (your own edge + your own Deck)

NAVIG self-hosts entirely on **your** Cloudflare account — two tiny deployments,
each with its own stable URL, plus your brain dialing out:

```bash
navig lighthouse login     # 1. EDGE: API uplink (Worker + Durable Object). Opens
                           #    Cloudflare → click Authorize → deploys + sets the
                           #    Telegram webhook. (Or: navig lighthouse deploy --token <t>)
navig miniapp deploy       # 2. DECK: builds the UI (static export) → your own
                           #    Cloudflare Pages, bakes the edge URL in (auto-targets
                           #    your brain) → sets the bot's Mini App button.
navig gateway start        # 3. BRAIN: your daemon dials OUT to the edge. Nothing inbound.
```

Open your bot → tap the menu button → **your Deck**, talking to your brain through
**your edge** — reachable from anywhere, no tunnel, no port-forwarding, no domain.
`navig lighthouse url` prints every inbound hook (Telegram is set automatically;
paste the SMS hook into Twilio only if you use SMS; social publishers are outbound
— token only, no webhook).

> **One Cloudflare credential for both deploys, no wrangler:** both
> `lighthouse deploy`/`login` and `miniapp deploy` upload via the Cloudflare REST
> API and reuse the same credential (an API token with the **"Edit Cloudflare
> Workers"** template, or the OAuth token from `lighthouse login`). **Node 18+** is
> only needed to *build* the Deck (`miniapp deploy`); the *upload* — like the edge —
> needs neither Node nor wrangler. (`miniapp deploy --wrangler` is a Cloudflare-Pages
> fallback if you prefer it.)

### Ports & firewall (cheat sheet)

| Mode | Inbound | Outbound | Daemon bind |
|---|---|---|---|
| cloudflared | none | 443 → `*.cloudflare.com` (cloudflared opens an outbound WebSocket) | `127.0.0.1:8765` (loopback only) |
| direct | 443 → your VPS (terminated by nginx/Caddy) | 443 → `relay.navig.run` (heartbeat every 60s) | `127.0.0.1:8765` (your proxy forwards from 443 → here) |
| off | none | none | `127.0.0.1:8765` (machine-local) |

The daemon itself **never** binds to a public interface — your reverse proxy is what listens on `0.0.0.0:443` in direct mode. Defense in depth: even if you misconfigure the proxy, the daemon socket stays on loopback.

### Quick commands

```bash
navig lighthouse login        # recommended: your own Cloudflare edge (browser auth)
navig lighthouse url          # show every inbound hook + the one stable edge URL
navig miniapp deploy          # deploy your own Deck UI → bot's Mini App button
navig cloud connect           # turn cloudflared mode on (default)
navig cloud direct https://navig.example.com   # switch to VPS direct mode
navig cloud direct --clear    # revert direct mode back to cloudflared
navig cloud status            # show mode, public URL, last heartbeat
navig cloud disconnect        # full off (no broker, no tunnel)
navig cloud key --reveal      # print the api_key (sensitive)
```

For the full operator guide — including ready-made nginx/Caddy configs, a systemd unit template with `Environment=NAVIG_PUBLIC_URL=...`, the security model, and troubleshooting — see [`docs/CLOUD.md`](docs/CLOUD.md).

---

## Configuration

Global config lives in `~/.navig/`. Override with `NAVIG_CONFIG_DIR`.

```text
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
| Agent runtime (`agent install`) | [`docs/agent/install.md`](docs/agent/install.md) |
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

```text
navig/
├── navig/               # Main Python package
│   ├── cli/             # CLI app, commands registry, help system
│   ├── commands/        # CLI command modules (one file per resource)
│   ├── agents/          # Specialist agents (inbox router, etc.)
│   ├── memory/          # Conversation store, knowledge base, RAG, embeddings
│   ├── providers/       # AI provider clients, fallback manager, OAuth
│   ├── gateway/         # Chat gateway (Telegram, Matrix) integrations
│   ├── onboarding/      # First-run setup engine and wizard steps
│   ├── daemon/          # Background service entry point
│   ├── core/            # Config loader, migrations, crash handler
│   └── resources/       # Default prompts, personas, assets
├── sdk/                 # Python SDK package
├── scripts/             # Install and deployment scripts
├── deploy/              # Docker, systemd, hardening configs
├── docs/                # Documentation
├── packages/            # Optional add-on packages
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
