# Execution Modes and Confirmation Levels

## Overview

NAVIG provides configurable execution modes that control when confirmations are prompted before executing potentially dangerous operations.

## Execution Modes

### Interactive Mode (default)

In interactive mode, NAVIG prompts for confirmation based on the configured confirmation level. This is the safest option for daily use.

```bash
navig config set-mode interactive
```

### Auto Mode

Auto mode skips all confirmation prompts. Use this for:
- Automated scripts
- CI/CD pipelines
- Batch operations where you've pre-validated commands

```bash
navig config set-mode auto
```

**⚠️ Warning:** Auto mode bypasses all safety confirmations. Use with caution.

## Confirmation Levels

Confirmation levels determine which types of operations require user confirmation in interactive mode.

### Critical Level

Only confirms operations that are destructive and potentially irreversible:
- `rm -rf`, `DROP TABLE/DATABASE`
- `TRUNCATE`, `DELETE` (without WHERE in some cases)
- System-wide commands like `reboot`, `shutdown`

```bash
navig config set-confirmation-level critical
```

### Standard Level (default)

Confirms critical operations plus operations that modify data:
- All critical operations
- `UPDATE`, `INSERT` SQL statements
- File uploads and modifications
- Service restarts

```bash
navig config set-confirmation-level standard
```

### Verbose Level

Confirms all remote operations, including read-only ones:
- All standard operations
- `SELECT` queries
- File listings
- Remote command execution

```bash
navig config set-confirmation-level verbose
```

## CLI Flags

### --yes, -y (Auto-confirm)

Skip confirmation for a single command:

```bash
# Skip confirmation for this command only
navig -y run "systemctl restart nginx"

# Works with any command
navig -y sql "DROP TABLE old_logs"
```

### --confirm, -c (Force confirmation)

Force confirmation prompt even in auto mode:

```bash
# Force confirmation even if auto mode is set
navig -c run "rm -rf /var/cache/*"
```

## Operation Classification

NAVIG automatically classifies operations into three categories:

| Category | Examples | Confirmation Threshold |
|----------|----------|----------------------|
| **Critical** | `rm -rf`, `DROP`, `TRUNCATE`, `reboot` | All levels |
| **Standard** | `UPDATE`, uploads, service restart | Standard and Verbose |
| **Verbose** | `SELECT`, `ls`, `cat`, `grep` | Verbose only |

### Command Classification Examples

```bash
# Critical - always prompts in interactive mode
navig run "rm -rf /var/www/old-site"
navig sql "DROP TABLE users"

# Standard - prompts at standard/verbose levels
navig upload config.json /var/www/config.json
navig sql "UPDATE users SET active=false WHERE id=5"

# Verbose - only prompts at verbose level
navig run "ls -la /var/www"
navig sql "SELECT * FROM users"
```

## Viewing Current Settings

```bash
navig config settings
```

Output:
```
╭──────────────────────────────────────────╮
│           NAVIG Settings                  │
├──────────────────────────────────────────┤
│  Execution Mode: interactive              │
│  Confirmation Level: standard             │
│                                          │
│  Confirmation Behavior:                   │
│  • Critical operations: Confirm           │
│  • Standard operations: Confirm           │
│  • Verbose operations: Skip               │
╰──────────────────────────────────────────╯
```

## Configuration Storage

Settings are stored in `~/.navig/config.yaml`:

```yaml
execution:
  mode: interactive
  confirmation_level: standard
```

## Best Practices

1. **For daily interactive use:** Keep `interactive` mode with `standard` level
2. **For scripts/automation:** Use `auto` mode or pass `-y` flag
3. **For learning/auditing:** Use `verbose` level to see all operations
4. **For production systems:** Consider `critical` level to reduce prompt fatigue while maintaining safety for destructive ops

## Integration with Existing Flags

The `-y` (--yes) flag that already existed in NAVIG now integrates with this system:
- In interactive mode: `-y` bypasses the configured confirmation level
- In auto mode: `-y` has no additional effect (already bypassed)
- The new `-c` flag forces confirmation regardless of mode/level


