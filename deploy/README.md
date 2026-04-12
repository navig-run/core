# NAVIG Deployment

Stack deployment files for NAVIG infrastructure.

## Quick Start

### User Install (CLI only)

**Linux / macOS:**

```bash
curl -fsSL https://navig.run/install.sh | bash
```

**Windows (PowerShell):**

```powershell
iwr -useb https://navig.run/install.ps1 | iex
```

### Server Bootstrap (Full Stack)

Deploy NAVIG + PostgreSQL (pgvector) + Redis + Ollama on Ubuntu:

```bash
curl -fsSL https://navig.run/bootstrap.sh | sudo bash
```

Or clone and run:

```bash
git clone https://github.com/navig-run/core.git
cd navig/navig-core
sudo ./bootstrap_navig_linux.sh
```

## Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Stack definition (Postgres + Redis + Ollama) |
| `.env.example` | Environment template — copy to `.env` and edit |
| `init-db.sql` | Database initialization (pgvector, tables, indexes) |
| `navig.service` | systemd unit for NAVIG daemon |
| `navig-stack.service` | systemd unit for Docker Compose stack |
| `navig_healthcheck.sh` | Health check script for all services |
| `harden/00-harden.sh` | 8-phase Ubuntu server hardening script |
| `harden/validate.sh` | Post-hardening validation report |
| `harden/conf/` | Standalone config files for manual deployment |

## Docker Compose Stack

### Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| PostgreSQL | `pgvector/pgvector:pg16` | 5432 (localhost) | Database + vector search |
| Redis | `redis:7-alpine` | 6379 (localhost) | Cache + queues |
| Ollama | `ollama/ollama:latest` | 11434 (localhost) | LLM inference |

### Setup

```bash
cd deploy/
cp .env.example .env
# Edit .env with strong passwords
docker compose up -d
```

### GPU Support (Ollama)

Uncomment the `deploy` section in `docker-compose.yml`:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

Requires [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html).

## systemd Services

Install the services:

```bash
sudo cp navig.service /etc/systemd/system/
sudo cp navig-stack.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable navig-stack navig
sudo systemctl start navig-stack
```

## Health Check

```bash
# Full output
./navig_healthcheck.sh

# Quiet (exit code only, for cron)
./navig_healthcheck.sh --quiet

# JSON (for monitoring APIs)
./navig_healthcheck.sh --json
```

## Directory Layout (Server)

```text
/opt/navig/          # Home directory
/opt/navig/stack/    # Docker Compose files
/etc/navig/          # Configuration
/var/lib/navig/      # Persistent data (postgres, redis, ollama)
/var/log/navig/      # Logs
```

## Server Hardening (Ubuntu 22.04 / 24.04)

Production-grade hardening covering 8 phases. Run **before** deploying the stack.

### Full Run

```bash
sudo ./harden/00-harden.sh
```

### Selective Phase

```bash
sudo ./harden/00-harden.sh --phase 2    # Security baseline only
sudo ./harden/00-harden.sh --dry-run    # Preview without changes
```

### Phases

| # | Phase | What it does |
|---|-------|-------------|
| 1 | Base Validation | OS check, hostname, swap, NTP, full-upgrade |
| 2 | Security Baseline | navig user, SSH hardening, UFW, fail2ban, unattended-upgrades |
| 3 | System Tuning | sysctl (swappiness, file-max), limits.conf, journald, tools |
| 4 | Docker Environment | Official APT install, daemon.json, user permissions |
| 5 | Filesystem | /opt/navig/{runtime,models,logs,backups,stack,config} |
| 6 | Logging & Monitoring | Persistent journald, node_exporter, daily backup timer |
| 7 | Network Sanity | Port audit, localhost binding verification, reverse proxy guide |
| 8 | Final Report | Structured summary table, risks, next steps |

### Post-Hardening Validation

```bash
sudo ./harden/validate.sh              # Human-readable
sudo ./harden/validate.sh --json       # Machine-readable
sudo ./harden/validate.sh --quiet      # Exit code only (0=pass)
```

### Standalone Config Files

Individual configs in `harden/conf/` for manual deployment:

| File | Deploy To |
|------|-----------|
| `99-navig-sysctl.conf` | `/etc/sysctl.d/` |
| `99-navig-limits.conf` | `/etc/security/limits.d/` |
| `99-navig-journald.conf` | `/etc/systemd/journald.conf.d/` |
| `90-navig-ssh.conf` | `/etc/ssh/sshd_config.d/` |
| `fail2ban-jail.local` | `/etc/fail2ban/jail.local` |
| `docker-daemon.json` | `/etc/docker/daemon.json` |

---

## Operational Factory (Draft + Approval Stack)

The Operational Factory is an autonomous agent execution environment with
human-in-the-loop approval gates. Located in `deploy/operational-factory/`.

### Services (8 containers)

| Service | Port | Purpose |
|---------|------|---------|
| **postgres** (pgvector:pg16) | 5432 | Persistent state, audit log, vector search |
| **redis** (7-alpine) | 6379 | Task queue, caching, pub/sub |
| **ollama** | 11434 | Local LLM inference |
| **tool-gateway** | 8090 | Safe/restricted tool dispatch, sandbox proxy |
| **sandbox-runner** | 8092 | Isolated code execution (repo mounted rw) |
| **navig-runtime** | 8091 | Agent orchestration, flow execution |
| **worker** | — | Background task processing (Redis queue) |
| **dashboard** | 8088 | Approval UI, audit viewer |

### Quick Start (Operational Factory)

```bash
cd deploy/operational-factory
cp .env.example .env          # Edit credentials
./scripts.sh start            # docker compose up -d --build
./scripts.sh status           # Health check all services
./scripts.sh logs             # Tail combined logs
./scripts.sh stop             # Shutdown
```

### Approval-First Rule

- **SAFE** actions execute automatically through `tool-gateway`
- **RESTRICTED** actions queue into `proposed_actions` table
- Human must approve via dashboard before execution proceeds

### Demo Flows

```bash
# Email intake processing
curl -X POST http://127.0.0.1:8091/flow/email/intake \
  -H 'content-type: application/json' -d '{"limit":10}'

# Repository change proposal
curl -X POST http://127.0.0.1:8091/flow/repo/propose \
  -H 'content-type: application/json' -d '{}'

# Daily briefing
curl -X POST http://127.0.0.1:8091/flow/briefing/daily
```
