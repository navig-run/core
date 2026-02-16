# Event-Driven Automation (Triggers)

Triggers allow NAVIG to react automatically to system events, enabling powerful automation scenarios like auto-remediation, scheduled maintenance, and resource monitoring.

## Overview

A **trigger** consists of:
- **Type**: What kind of event triggers it (health, schedule, threshold, etc.)
- **Conditions**: Optional filters that must be met
- **Actions**: Commands, workflows, notifications, or webhooks to execute
- **Settings**: Cooldown, rate limits, and status

## Trigger Types

| Type | Description | Use Case |
|------|-------------|----------|
| `health` | Fires when heartbeat detects issues | Auto-restart failed services |
| `schedule` | Time-based (cron-like) | Scheduled backups, maintenance |
| `threshold` | Resource thresholds (CPU, memory, disk) | Disk cleanup at 80% |
| `webhook` | Incoming HTTP webhooks | GitHub push -> deploy |
| `file` | File system changes | Config change -> reload |
| `command` | After specific commands | Post-deploy notifications |
| `manual` | Manual trigger only | Testing, on-demand tasks |

## Quick Start

### Create a trigger interactively
```bash
navig trigger add
```

### Create a trigger in one line
```bash
# Alert when disk > 80%
navig trigger add "Disk Alert" \
  --action "notify:telegram" \
  --type threshold \
  --host prod \
  --condition "disk gte 80"

# Daily backup at 2am
navig trigger add "Daily Backup" \
  --action "workflow:backup" \
  --type schedule \
  --schedule "0 2 * * *"

# Auto-restart on health failure
navig trigger add "Auto Restart" \
  --action "docker restart api" \
  --type health
```

## Commands

| Command | Description |
|---------|-------------|
| `navig trigger` | List all triggers |
| `navig trigger list` | List triggers with filtering |
| `navig trigger show <id>` | Show trigger details |
| `navig trigger add` | Create new trigger (interactive) |
| `navig trigger add <name> --action ...` | Create trigger (quick mode) |
| `navig trigger remove <id>` | Delete a trigger |
| `navig trigger enable <id>` | Enable a trigger |
| `navig trigger disable <id>` | Disable a trigger |
| `navig trigger test <id>` | Dry run (show actions without executing) |
| `navig trigger fire <id>` | Manually fire a trigger |
| `navig trigger history` | Show execution history |
| `navig trigger stats` | Show trigger statistics |

## Action Formats

When specifying `--action`, use these formats:

| Format | Description | Example |
|--------|-------------|---------|
| `<command>` | Run navig command | `"host list"`, `"db dump"` |
| `workflow:<name>` | Run a workflow | `"workflow:deploy"` |
| `notify:<channel>` | Send notification | `"notify:telegram"` |
| `webhook:<url>` | Call external webhook | `"webhook:https://..."` |
| `script:<path>` | Run a script file | `"script:/path/to/script.sh"` |

## Conditions

Conditions use the format: `target operator value`

### Operators
- `eq` - equals
- `ne` - not equals
- `gt` - greater than
- `lt` - less than
- `gte` - greater than or equal
- `lte` - less than or equal
- `contains` - string contains
- `matches` - regex match

### Examples
```bash
# Trigger when CPU > 90%
--condition "cpu gte 90"

# Trigger when disk > 80%
--condition "disk gte 80"

# Trigger when memory > 85%
--condition "memory gte 85"

# Trigger on specific status
--condition "status eq failed"
```

## Examples

### Health Monitoring
```bash
# Auto-restart failed service
navig trigger add "Service Recovery" \
  --type health \
  --action "run 'systemctl restart myapp'" \
  --desc "Restart myapp when health check fails"

# Send alert on failure
navig trigger add "Health Alert" \
  --type health \
  --action "notify:telegram" \
  --desc "Alert when any service fails"
```

### Scheduled Tasks
```bash
# Daily backup at 3am
navig trigger add "Nightly Backup" \
  --type schedule \
  --schedule "0 3 * * *" \
  --action "workflow:full-backup"

# Weekly maintenance on Sunday 2am
navig trigger add "Weekly Maintenance" \
  --type schedule \
  --schedule "0 2 * * 0" \
  --action "workflow:maintenance"
```

### Resource Monitoring
```bash
# Clean temp files when disk > 85%
navig trigger add "Disk Cleanup" \
  --type threshold \
  --host production \
  --condition "disk gte 85" \
  --action "run 'rm -rf /tmp/*'"

# Alert on high CPU
navig trigger add "CPU Alert" \
  --type threshold \
  --host production \
  --condition "cpu gte 95" \
  --action "notify:telegram"
```

## Trigger Settings

### Cooldown
Minimum time between trigger fires (prevents flooding):
- Default: 60 seconds
- Configure in trigger definition

### Rate Limiting
Maximum fires per hour:
- Default: 10 fires/hour
- Prevents runaway triggers

### Status
- `enabled` - Trigger is active
- `disabled` - Trigger won't fire
- `firing` - Currently executing
- `cooldown` - In cooldown period

## Storage

Triggers are stored in: `~/.navig/triggers/triggers.yaml`

Execution history is logged to: `~/.navig/triggers/history.jsonl`

## Integration with Other NAVIG Features

### With Workflows
```bash
# Trigger a workflow
navig trigger add "Deploy on Push" \
  --type webhook \
  --action "workflow:deploy"
```

### With Heartbeat
Health triggers automatically integrate with NAVIG's heartbeat system.

### With Notifications
```bash
# Send to Telegram
--action "notify:telegram"

# Log to console
--action "notify:console"

# Write to log file
--action "notify:log"
```

## Tips

1. **Start with manual triggers** for testing before enabling automatic ones
2. **Use dry runs** (`navig trigger test <id>`) to verify actions
3. **Set appropriate cooldowns** to prevent alert fatigue
4. **Combine with workflows** for complex multi-step responses
5. **Check history** regularly to monitor trigger activity

## See Also

- `navig help flow` - Workflows for multi-step automation
- `navig help cron` - Scheduled jobs
- `navig help heartbeat` - Health monitoring
- `navig help history` - Command history


