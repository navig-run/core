# NAVIG Skills

Skills are AI instruction files (`SKILL.md`) that teach the NAVIG Telegram bot and AI agent how to understand natural language and execute the right NAVIG commands.

## When to Use Skills

| I want to... | Use Skills? | Why |
|--------------|-------------|-----|
| Teach AI to understand "check disk space" | ✅ Yes | Skills map natural language to commands |
| Define where nginx stores logs | ❌ No | Use [templates/](../templates/) |
| Document a backup procedure | ❌ No | Use [packs/](../packs/) |
| Add voice command for "restart Docker" | ✅ Yes | Skills are AI instruction files |
| Create deployment checklist | ❌ No | Use [packs/](../packs/) |

> **See also**: [Content Architecture Guide](../docs/CONTENT_ARCHITECTURE.md) for full decision matrix.

## How Skills Work

1. **User asks**: "How much space on my server?"
2. **AI reads skills**: Finds `disk-space/SKILL.md`
3. **AI executes**: `navig host use example-vps && navig run "df -h"`
4. **AI formats**: "🟢 /: 117G free / 150G (21% used)"

Skills are **auto-discovered** on startup — just add a `SKILL.md` file and restart.

## Directory Structure

```
skills/
├── README.md                                    # This file
│
├── server-management/                           # Remote server operations
│   ├── disk-space/SKILL.md                     # Check disk usage
│   ├── hestiacp-manage/SKILL.md                # HestiaCP panel management
│   ├── system-status/SKILL.md                  # Uptime, memory, CPU, processes
│   └── ssh-tunnel/SKILL.md                     # SSH tunnel management
│
├── database/                                    # Database operations
│   └── database-query/SKILL.md                 # Query, backup, manage databases
│
├── docker/                                      # Container management
│   └── docker-manage/SKILL.md                  # Docker ps, logs, restart, stats
│
├── development/                                 # Development workflow
│   └── github-status/SKILL.md                  # CI/CD, PRs, issues via gh CLI
│
├── cross-platform/                              # Works on any OS
│   ├── file-transfer/SKILL.md                  # Upload, download, sync files
│   └── summarize-url/SKILL.md                  # Summarize URLs and content via AI
│
├── linux/                                       # Linux-specific operations
│   ├── tmux-sessions/SKILL.md                  # Persistent terminal sessions
│   ├── systemd-services/SKILL.md               # systemctl, journalctl
│   └── package-manager/SKILL.md                # apt, dnf, yum
│
├── macos/                                       # macOS-specific operations
│   ├── homebrew/SKILL.md                       # Homebrew package management
│   └── macos-services/SKILL.md                 # launchctl, system info
│
└── meta/                                        # Skills about skills
    └── create-skill/SKILL.md                   # How to create new skills
```

## Categories

| Category | Description | OS |
|----------|-------------|----|
| `server-management` | Remote server ops: disk, health, hosting panels, tunnels | Any |
| `database` | Query, backup, and manage databases | Any |
| `docker` | Container lifecycle, logs, stats | Any |
| `development` | GitHub CI/CD, PRs, code workflow | Any |
| `cross-platform` | Utilities that work on any OS | Any |
| `linux` | systemd, apt/dnf, tmux — Linux-specific tools | Linux |
| `macos` | Homebrew, launchctl — macOS-specific tools | macOS |
| `meta` | Skills about creating and managing skills | Any |

## Skills vs Templates vs Packs

| | Skills | Templates | Packs |
|--|--------|-----------|-------|
| **Purpose** | HOW AI understands requests | WHERE things are on servers | WHAT steps to follow |
| **Format** | `SKILL.md` (Markdown + YAML) | `template.yaml` (YAML) | `.yml` files |
| **Location** | `skills/` | `templates/` | `packs/` |
| **Used by** | AI agent / Telegram bot | NAVIG CLI | Humans & automation |
| **Example** | "Check disk space" → `navig run "df -h"` | nginx paths, ports, commands | Backup procedure runbook |

Skills can **reference templates**: e.g., the `hestiacp-manage` skill uses the `templates/hestiacp/` template config.

## Creating a New Skill

1. Pick a category (or create one)
2. Create a directory: `skills/{category}/{skill-name}/`
3. Add `SKILL.md` with YAML frontmatter and instructions
4. Restart the bot — skills are auto-discovered

See `meta/create-skill/SKILL.md` for the full format guide.

### Minimal Example

```markdown
---
name: my-skill
description: What this skill does in one line
user-invocable: true
navig-commands:
  - navig run "some-command"
examples:
  - "Trigger phrase from user"
---

# My Skill

Instructions for the AI agent...
```

## Skill Format Reference

### Required Frontmatter

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique identifier (kebab-case) |
| `description` | string | One-line description |
| `user-invocable` | boolean | Always `true` |
| `navig-commands` | list | NAVIG commands this skill uses |
| `examples` | list | Natural language trigger phrases |

### Optional Frontmatter

| Field | Type | Description |
|-------|------|-------------|
| `os` | list | Limit to OS: `[linux]`, `[darwin]`, `[linux, darwin]` |
| `requires` | list | External tools needed (e.g., `gh`, `tmux`) |


