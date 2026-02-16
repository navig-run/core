# Configuration Backup & Export System

## Overview

NAVIG includes a comprehensive backup and export system for your configuration files. This allows you to:
- Backup your NAVIG configuration before making changes
- Export configuration to share with team members
- Transfer configuration between machines
- Safely share configs with secrets automatically redacted

## Commands

### `navig backup export`

Export your NAVIG configuration to a backup file.

**Options:**
| Option | Description |
|--------|-------------|
| `--output`, `-o` | Output file path (default: auto-generated with timestamp) |
| `--format`, `-f` | Export format: `json` (default) or `archive` (.tar.gz) |
| `--encrypt`, `-e` | Encrypt the backup with a password |
| `--include-secrets` | Include actual secrets (default: redacted for safety) |
| `--hosts-only` | Export only host configurations |
| `--apps-only` | Export only application configurations |

**Examples:**

```bash
# Basic export to JSON (secrets redacted)
navig backup export

# Export to specific location
navig backup export --output ~/backups/navig-prod.json

# Export as compressed archive
navig backup export --format archive

# Export with encryption
navig backup export --encrypt --format archive
# You'll be prompted for a password

# Export with secrets (use with caution!)
navig backup export --include-secrets --encrypt

# Export only hosts
navig backup export --hosts-only
```

### `navig backup import`

Import configuration from a backup file.

**Options:**
| Option | Description |
|--------|-------------|
| `file` | Path to the backup file (required) |
| `--overwrite` | Overwrite existing configurations (default: merge) |
| `--password`, `-p` | Password for encrypted backups |
| `--dry-run` | Preview import without making changes |
| `--hosts-only` | Import only host configurations |
| `--apps-only` | Import only application configurations |

**Examples:**

```bash
# Import with merge (keeps existing, adds new)
navig backup import navig-export-2025-01-06.json

# Import with overwrite (replaces existing)
navig backup import navig-export.json --overwrite

# Import encrypted backup
navig backup import navig-export.tar.gz.enc --password mypassword

# Preview what would be imported
navig backup import backup.json --dry-run

# Import only hosts from backup
navig backup import backup.json --hosts-only
```

### `navig backup list`

List all available backup files in the default export directory.

```bash
navig backup list
```

Output:
```
╭───────────────────────────────────────────────────────────────╮
│                    Available Backups                           │
├───────────────────────────────────────────────────────────────┤
│ navig-export-2025-01-06-143022.json          2.3 KB  Today    │
│ navig-export-2025-01-05-091545.tar.gz        1.8 KB  Yesterday│
│ navig-export-2025-01-04-160302.tar.gz.enc    2.1 KB  3 days   │
╰───────────────────────────────────────────────────────────────╯
```

### `navig backup inspect`

Preview the contents of a backup file without importing it.

**Options:**
| Option | Description |
|--------|-------------|
| `file` | Path to the backup file (required) |
| `--password`, `-p` | Password for encrypted backups |
| `--json` | Output in JSON format |

```bash
# Inspect backup contents
navig backup inspect navig-export-2025-01-06.json

# Inspect encrypted backup
navig backup inspect backup.enc --password mypassword

# Output as JSON (for scripting)
navig backup inspect backup.json --json
```

### `navig backup delete`

Delete a backup file.

```bash
navig backup delete navig-export-2025-01-06.json
```

## Export Formats

### JSON Format (default)

Human-readable JSON file. Good for:
- Manual inspection and editing
- Version control (git)
- Smaller configurations

```json
{
  "version": "1.0",
  "exported_at": "2025-01-06T14:30:22Z",
  "hosts": {
    "production": {
      "name": "production",
      "host": "10.0.0.10",
      "port": 22,
      "user": "deploy",
      "database": {
        "password": "[REDACTED]"
      }
    }
  },
  "apps": {},
  "global_config": {}
}
```

### Archive Format (.tar.gz)

Compressed tarball containing the configuration. Good for:
- Large configurations
- Including multiple files
- Better compression

## Security Features

### Automatic Secret Redaction

By default, sensitive values are automatically redacted:
- `password`, `secret`, `key`, `token`
- `api_key`, `apikey`, `api_secret`
- `private_key`, `ssh_key`
- Any field containing "credential"

Redacted values appear as `[REDACTED]` in exports.

### Encryption

When using `--encrypt`, backups are encrypted using:
- AES-256 encryption via Fernet (from cryptography library)
- Password-derived key using PBKDF2
- Encrypted files have `.enc` extension

**Important:** Store your encryption password securely. Lost passwords cannot be recovered.

## Import Modes

### Merge Mode (default)

- Keeps existing configurations
- Adds new hosts/apps from backup
- Does NOT overwrite existing hosts/apps with same name
- Safe for adding to existing setup

### Overwrite Mode

- Replaces existing configurations with backup versions
- New hosts/apps are added
- Existing hosts/apps are overwritten
- Use when restoring from backup or syncing exactly

## File Locations

| Type | Location |
|------|----------|
| Default export directory | `~/.navig/exports/` |
| Host configs | `~/.navig/hosts/*.yaml` |
| App configs | `~/.navig/apps/*.yaml` |
| Global config | `~/.navig/config.yaml` |

## Common Workflows

### Backup Before Major Changes

```bash
# Create timestamped backup
navig backup export

# Make changes...
navig host edit production

# If something goes wrong, restore
navig backup import ~/.navig/exports/navig-export-*.json --overwrite
```

### Share Configuration with Team

```bash
# Export without secrets (safe to share)
navig backup export --output ~/shared-config.json

# Team member imports
navig backup import ~/shared-config.json

# They need to add their own secrets
navig host edit production
```

### Migrate to New Machine

```bash
# On old machine: export with encryption and secrets
navig backup export --encrypt --include-secrets --output navig-backup.tar.gz.enc

# Transfer the file to new machine...

# On new machine: import
navig backup import navig-backup.tar.gz.enc --password mypassword
```

### Version Control Your Config

```bash
# Export to git-tracked location (secrets redacted)
navig backup export --output ~/dotfiles/navig/config.json

# Commit and push
cd ~/dotfiles && git add . && git commit -m "Update NAVIG config"
```

## Troubleshooting

### "Decryption failed" error

- Ensure you're using the correct password
- Verify the file wasn't corrupted during transfer
- Check the file extension matches the encryption state

### "File format not recognized"

- Ensure the file is a valid JSON or tar.gz
- Check if the file is encrypted (look for `.enc` extension)
- Try `navig backup inspect` to diagnose

### Import doesn't show new hosts

- Check if hosts with same names already exist (merge mode skips)
- Use `--overwrite` to replace existing
- Verify the backup contains expected data with `navig backup inspect`


