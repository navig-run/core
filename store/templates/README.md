# NAVIG Templates

Templates are modular server configuration definitions that can be applied to hosts. They define paths, services, commands, and environment variables for common server applications.

## When to Use Templates

| I want to... | Use Templates? | Why |
|--------------|----------------|-----|
| Define where nginx stores configs | ✅ Yes | Templates define app paths & services |
| Teach AI "restart nginx" command | ❌ No | Use [skills/](../skills/) |
| Document a backup procedure | ❌ No | Use [packs/](../packs/) |
| Add a new app (Grafana, n8n) | ✅ Yes | Templates define app configurations |
| Create deployment checklist | ❌ No | Use [packs/](../packs/) |

> **See also**: [Content Architecture Guide](../docs/CONTENT_ARCHITECTURE.md) for full decision matrix.

## Directory Structure

```
templates/
├── README.md              # This file
├── caddy/                 # Caddy reverse proxy & web server
├── docker/                # Docker engine management
├── duplicati/             # Duplicati backup solution
├── gitea/                 # Gitea Git server
├── gitlab-runner/         # GitLab CI runner
├── grafana/               # Grafana monitoring dashboards
├── hestiacp/              # HestiaCP hosting control panel
├── jellyfin/              # Jellyfin media server
├── matomo/                # Matomo web analytics
├── mattermost/            # Mattermost team chat
├── n8n/                   # n8n workflow automation
├── netdata/               # Netdata real-time monitoring
├── nextcloud/             # Nextcloud file sync & share
├── nginx/                 # Nginx web server & reverse proxy
├── plausible/             # Plausible privacy-friendly analytics
├── portainer/             # Portainer Docker management UI
├── postgresql/            # PostgreSQL database server
├── prometheus/            # Prometheus metrics & alerting
├── redis/                 # Redis in-memory cache & store
├── traefik/               # Traefik reverse proxy & load balancer
├── uptime-kuma/           # Uptime Kuma monitoring
├── vaultwarden/           # Vaultwarden password manager
└── wikijs/                # Wiki.js documentation platform
```

Each template directory contains `template.yaml` + `README.md`.

## Template Format

Each template lives in its own directory with a `template.yaml` file:

```yaml
# Template identification
name: template-name
version: 1.0.0
description: Brief description of the template
author: author-name
enabled: false

# Dependencies on other templates
dependencies: []

# Server paths for this application
paths:
  app_root: /var/lib/app
  config_dir: /etc/app
  log_dir: /var/log/app

# Service names for systemctl
services:
  main_service: app.service

# Common commands
commands:
  - name: start
    description: Start the service
    command: systemctl start app

  - name: status
    description: Check service status
    command: systemctl status app

# Environment variables
env_vars:
  APP_PORT: "8080"
  APP_HOST: "0.0.0.0"

# API configuration (optional)
api:
  endpoint: http://localhost:8080/api/
  auth_method: api_key
  doc_url: https://docs.example.com
```

## Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique template identifier |
| `version` | string | Semantic version (e.g., "1.0.0") |
| `description` | string | Brief description |
| `author` | string | Template author |

## Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | boolean | Whether template is active (default: false) |
| `dependencies` | list | Other templates this depends on |
| `paths` | dict | Application directory paths |
| `services` | dict | Systemd service names |
| `commands` | list | Common shell commands |
| `env_vars` | dict | Environment variables |
| `api` | dict | API endpoint configuration |

## Usage

### List available templates

```bash
navig template list
```

### Show template details

```bash
navig template info n8n
```

### Enable a template

```bash
navig template enable n8n
```

### Disable a template

```bash
navig template disable n8n
```

### Validate all templates

```bash
navig template validate
```

## Per-Host Configuration

Templates can be customized per-host using overrides:

```bash
# Initialize a template for a host
navig server-template init n8n --server production

# Set custom values
navig server-template set n8n paths.log_dir /custom/logs --server production

# View merged configuration
navig server-template show n8n --server production
```

## Deep Merge Strategy

When combining template defaults with host-specific overrides:

1. **Nested dicts**: Merged recursively (host values override template)
2. **Lists**: Host completely replaces template list
3. **Scalars**: Host value wins
4. **None values**: Template default is preserved

## Creating New Templates

1. Create a new directory in `templates/`:
   ```bash
   mkdir templates/myapp
   ```

2. Create `template.yaml` with required fields:
   ```yaml
   name: myapp
   version: 1.0.0
   description: My custom application
   author: your-name
   ```

3. Add paths, services, commands as needed

4. Validate your template:
   ```bash
   navig template validate
   ```

## Format Support

NAVIG supports both YAML and JSON for templates:

- `template.yaml` (preferred)
- `template.json` (legacy support)

YAML is recommended for better readability and comment support.

## Templates vs Skills vs Packs

| | Templates | Skills | Packs |
|--|-----------|--------|-------|
| **Purpose** | WHERE things are on servers | HOW AI understands requests | WHAT steps to follow |
| **Format** | `template.yaml` (YAML) | `SKILL.md` (Markdown + YAML) | `.yml` files |
| **Location** | `templates/` | `skills/` | `packs/` |
| **Used by** | NAVIG CLI | AI agent / Telegram bot | Humans & automation |
| **Example** | nginx paths, ports, commands | "Check disk space" → df -h | Backup procedure runbook |

Templates are **referenced by skills**: e.g., the `hestiacp-manage` skill uses the `templates/hestiacp/` template config.
