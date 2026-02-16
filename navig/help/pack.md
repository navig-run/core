# Packs System

Packs are shareable operations bundles containing runbooks, checklists, workflows,
and templates. Install community packs or create your own reusable operations.

## Quick Start

```bash
# List available packs
navig pack list

# Show pack details
navig pack show deployment-checklist

# Install a pack
navig pack install starter/deployment-checklist

# Run a pack
navig pack run deployment-checklist

# Create your own pack
navig pack create my-runbook --type runbook
```

## Pack Types

| Type | Description | Use Case |
|------|-------------|----------|
| **runbook** | Sequential steps with optional commands | Automated procedures |
| **checklist** | Interactive verification steps | Manual checklists |
| **workflow** | Multi-step automation | Complex automation |
| **template** | Configuration templates | Server/app setup |
| **quickactions** | Batch quick action imports | Shortcut bundles |
| **bundle** | Collection of multiple packs | Pack collections |

## Commands

### `navig pack list`
List all available packs.

Options:
- `--type, -t`: Filter by pack type
- `--tag`: Filter by tag
- `--installed, -i`: Show only installed packs
- `--json`: JSON output

### `navig pack show <name>`
Display detailed pack information including steps and metadata.

### `navig pack install <source>`
Install a pack from various sources:

```bash
# Install built-in pack
navig pack install starter/deployment-checklist

# Install from file
navig pack install ./my-pack.yaml

# Force reinstall
navig pack install starter/backup-runbook --force
```

### `navig pack uninstall <name>`
Remove an installed pack.

```bash
navig pack uninstall deployment-checklist
navig pack uninstall my-pack --force  # Skip confirmation
```

### `navig pack run <name>`
Execute a pack's steps.

```bash
# Interactive run (prompts for each step)
navig pack run deployment-checklist

# With variables
navig pack run backup-runbook --var host=production --var db=mydb

# Dry run (preview only)
navig pack run deployment-checklist --dry-run

# Non-interactive (auto-confirm)
navig pack run backup-runbook --yes
```

### `navig pack create <name>`
Create a new pack in your local packs directory.

```bash
navig pack create my-runbook --type runbook -d "My custom runbook"
navig pack create pre-deploy --type checklist
```

### `navig pack search <query>`
Search packs by name, description, or tags.

```bash
navig pack search deploy
navig pack search database --json
```

## Pack Format

Packs are YAML files with this structure:

```yaml
name: "My Pack"
description: "What this pack does"
author: "Your Name"
version: "1.0.0"
type: runbook  # runbook, checklist, workflow, template

# Variables (can be overridden with --var)
variables:
  host: production
  backup_path: /var/backups

# Steps to execute
steps:
  - description: "First step"
    command: "navig host test ${host}"
    
  - description: "Manual step"
    notes: "Verify this manually before continuing"
    
  - description: "Risky step"
    command: "navig db backup"
    prompt: "Run database backup?"  # Ask for confirmation
    continue_on_error: true  # Don't stop on failure

# Optional metadata
tags:
  - deployment
  - production
homepage: "https://github.com/..."
license: MIT
```

## Step Options

| Option | Description |
|--------|-------------|
| `description` | Step description (required) |
| `command` | Command to execute (optional) |
| `notes` | Additional notes for manual steps |
| `prompt` | Confirmation prompt before execution |
| `continue_on_error` | Don't stop pack on step failure |
| `skip_if` | Condition to skip step |

## Pack Locations

Packs are loaded from these directories in priority order:

1. **Installed**: `~/.navig/packs/installed/`
2. **Local**: `~/.navig/packs/local/`
3. **Built-in**: `<navig>/packs/`

## Creating Packs

### Runbook (Automated)
For procedures that should auto-execute:

```yaml
name: "Database Backup"
type: runbook
steps:
  - description: "Stop application"
    command: "navig docker stop myapp"
  - description: "Dump database"
    command: "navig db backup --verify"
  - description: "Start application"
    command: "navig docker start myapp"
```

### Checklist (Interactive)
For manual verification procedures:

```yaml
name: "Pre-Deploy Checklist"
type: checklist
steps:
  - description: "Code reviewed?"
    notes: "Ensure PR has been approved"
  - description: "Tests passing?"
    command: "pytest tests/ -v"
  - description: "Changelog updated?"
    notes: "Document all changes"
```

### Quick Actions Bundle
Import multiple quick actions at once:

```yaml
name: "DevOps Shortcuts"
type: quickactions
quick_actions:
  - name: logs
    command: "navig logs --follow"
    description: "Follow logs"
  - name: deploy
    command: "navig workflow run deploy"
    description: "Run deployment"
```

## Examples

### Run deployment checklist
```bash
navig pack run deployment-checklist
```

### Create and run custom runbook
```bash
# Create
navig pack create nightly-backup --type runbook

# Edit ~/.navig/packs/local/nightly-backup/pack.yaml

# Run
navig pack run nightly-backup --var host=production
```

### Search and install
```bash
navig pack search security
navig pack install security-audit
navig pack run security-audit
```

## Built-in Packs

NAVIG includes starter packs in `packs/starter/`:

| Pack | Type | Description |
|------|------|-------------|
| deployment-checklist | checklist | Pre-deploy verification |
| backup-runbook | runbook | Database backup procedure |

## Tips

1. **Start with dry-run**: Always preview with `--dry-run` first
2. **Use variables**: Make packs reusable with `${var}` placeholders
3. **Add notes**: Document manual steps clearly
4. **Test locally**: Create packs in local dir before sharing
5. **Use prompts**: Add confirmation for destructive operations


