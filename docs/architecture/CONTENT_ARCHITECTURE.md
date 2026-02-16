# NAVIG Content Architecture

This document explains the three content systems in NAVIG and when to use each one.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      NAVIG Content Systems                       │
├─────────────────┬─────────────────┬─────────────────────────────┤
│   TEMPLATES     │     SKILLS      │           PACKS             │
│   (Data Layer)  │   (AI Layer)    │    (Operations Layer)       │
├─────────────────┼─────────────────┼─────────────────────────────┤
│ WHERE things    │ HOW AI under-   │ WHAT steps to follow        │
│ are on servers  │ stands requests │ for workflows               │
├─────────────────┼─────────────────┼─────────────────────────────┤
│ template.yaml   │ SKILL.md        │ *.yml files                 │
├─────────────────┼─────────────────┼─────────────────────────────┤
│ NAVIG CLI       │ AI/Telegram Bot │ Humans & Automation         │
└─────────────────┴─────────────────┴─────────────────────────────┘

                          Data Flow:
    
    Skills ──reference──> Templates
    Packs  ──reference──> Templates (for paths/commands)
    Packs  ──can trigger──> Skills (via AI agent)
```

## Decision Matrix

### "Where should I add this?"

| I want to add... | Add it to... | Directory | File Format |
|------------------|--------------|-----------|-------------|
| Support for new app (Grafana, n8n) | **Templates** | `templates/grafana/` | `template.yaml` |
| AI understanding of "restart Grafana" | **Skills** | `skills/monitoring/grafana/` | `SKILL.md` |
| Step-by-step backup procedure | **Packs** | `packs/runbooks/` | `backup.yml` |
| App paths, ports, service names | **Templates** | `templates/<app>/` | `template.yaml` |
| Voice command for Telegram bot | **Skills** | `skills/<category>/<name>/` | `SKILL.md` |
| Deployment checklist | **Packs** | `packs/checklists/` | `deploy.yml` |
| Database connection details | **Templates** | `templates/postgresql/` | `template.yaml` |
| "How to query database" for AI | **Skills** | `skills/database/` | `SKILL.md` |
| Migration workflow | **Packs** | `packs/workflows/` | `migration.yml` |

### Quick Reference

```
Is it about WHERE things are?     → Templates
Is it about HOW AI understands?   → Skills  
Is it about WHAT steps to follow? → Packs
```

## System Details

### Templates (`templates/`)

**Purpose**: Define server application configurations — paths, services, commands, ports.

**Format**: `template.yaml`

```yaml
name: nginx
version: 1.0.0
description: Nginx web server
paths:
  config_dir: /etc/nginx
  log_dir: /var/log/nginx
services:
  main_service: nginx.service
commands:
  - name: reload
    command: systemctl reload nginx
```

**When to use**:
- Adding support for a new application
- Defining paths and services for an existing app
- Storing port numbers, environment variables
- CLI needs to know where to find things

**Commands**:
```bash
navig template list          # List all templates
navig template info nginx    # Show template details
navig template enable nginx  # Enable a template
```

**See**: [templates/README.md](../templates/README.md)

---

### Skills (`skills/`)

**Purpose**: Teach the AI agent how to understand natural language and execute commands.

**Format**: `SKILL.md` with YAML frontmatter

```markdown
---
name: disk-space
description: Check disk usage on servers
user-invocable: true
navig-commands:
  - navig run "df -h"
examples:
  - "How much space on my server?"
  - "Check disk usage"
---

# Disk Space Skill

When user asks about disk space, run `df -h` and format output...
```

**When to use**:
- Adding new voice/chat commands for Telegram bot
- Teaching AI to understand new phrases
- Mapping natural language to NAVIG commands
- Adding intelligent responses to queries

**Auto-discovery**: Skills are discovered on bot startup. Just add `SKILL.md` and restart.

**See**: [skills/README.md](../skills/README.md)

---

### Packs (`packs/`)

**Purpose**: Operational runbooks, checklists, and workflows — step-by-step procedures.

**Format**: `.yml` files

```yaml
name: database-backup
description: Full database backup procedure
type: runbook
steps:
  - description: Check disk space
    command: navig run "df -h"
  - description: Create backup
    command: navig db backup mydb
  - description: Verify backup
    command: navig db verify-backup mydb
```

**When to use**:
- Documenting operational procedures
- Creating deployment checklists
- Standardizing workflows across team
- Automating multi-step processes

**Pack types**:
- `runbook` — Step-by-step procedures
- `checklist` — Pre-flight verification lists
- `workflow` — Automated sequences
- `snippet` — Reusable command groups

**See**: [packs/README.md](../packs/README.md)

---

## How Systems Work Together

### Example: Adding Grafana Support

1. **Template** (`templates/grafana/template.yaml`):
   ```yaml
   name: grafana
   paths:
     config_dir: /etc/grafana
     data_dir: /var/lib/grafana
   services:
     main_service: grafana-server.service
   commands:
     - name: restart
       command: systemctl restart grafana-server
   ```

2. **Skill** (`skills/monitoring/grafana-manage/SKILL.md`):
   ```markdown
   ---
   name: grafana-manage
   description: Manage Grafana dashboards and server
   examples:
     - "Restart Grafana"
     - "Check Grafana status"
   ---
   
   Use `navig template info grafana` to get paths and commands...
   ```

3. **Pack** (`packs/runbooks/grafana-upgrade.yml`):
   ```yaml
   name: grafana-upgrade
   type: runbook
   steps:
     - description: Stop Grafana
       command: navig run "systemctl stop grafana-server"
     - description: Backup config
       command: navig download /etc/grafana ./backup/
     - description: Upgrade package
       command: navig run "apt upgrade grafana"
   ```

### Data Flow

```
User: "What's the Grafana status?"
         │
         ▼
┌─────────────────┐
│  AI reads Skill │  → grafana-manage/SKILL.md
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Skill references│  → templates/grafana/template.yaml
│    Template     │     (gets service name, paths)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  AI executes    │  → navig run "systemctl status grafana-server"
└────────┬────────┘
         │
         ▼
    AI formats response
```

## Directory Structure

```
navig/
├── templates/              # WHERE things are
│   ├── nginx/
│   │   └── template.yaml
│   ├── grafana/
│   │   └── template.yaml
│   └── ...
│
├── skills/                 # HOW AI understands
│   ├── server-management/
│   │   ├── disk-space/SKILL.md
│   │   └── system-status/SKILL.md
│   ├── database/
│   │   └── database-query/SKILL.md
│   └── ...
│
└── packs/                  # WHAT steps to follow
    ├── community/          # Example/starter packs
    ├── runbooks/           # Step-by-step procedures
    ├── checklists/         # Pre-flight checks
    └── workflows/          # Automated sequences
```

## Common Mistakes

### ❌ Wrong: Putting paths in Skills
```markdown
# SKILL.md
config_dir: /etc/nginx    # Wrong! This belongs in template.yaml
```

### ✅ Right: Reference Template from Skill
```markdown
# SKILL.md
Use `navig template info nginx` to get the config path...
```

### ❌ Wrong: Putting procedures in Templates
```yaml
# template.yaml
backup_steps:             # Wrong! This belongs in packs/
  - stop service
  - copy files
```

### ✅ Right: Keep Templates as data, Packs as procedures
```yaml
# template.yaml
paths:
  backup_dir: /var/backups/nginx

# packs/runbooks/nginx-backup.yml
steps:
  - command: navig run "cp -r /etc/nginx /var/backups/"
```

## Migration Notes

- **addons/** — Deprecated and removed. Content migrated to `templates/`.
- **packs/starter/** — Renamed to `packs/community/` for clarity.

---

**Last Updated**: Content architecture documented and cross-referenced across all READMEs.


