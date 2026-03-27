# navig quick

Quick action shortcuts for frequently used operations.

## Overview

Quick actions let you save and run common command patterns with short names.
Instead of typing long commands, create a shortcut once and use it forever.

## Usage

```bash
# List all quick actions
navig quick
navig quick list

# Run a quick action
navig quick deploy
navig quick backup

# Add a new quick action
navig quick add <name> "<command>"

# Remove a quick action
navig quick remove <name>

# Preview without executing
navig quick deploy --dry-run
```

## Adding Quick Actions

```bash
# Basic examples
navig quick add deploy "run 'cd /var/www && git pull && systemctl restart app'"
navig quick add backup "db dump --output /tmp/backup.sql"
navig quick add status "dashboard --no-live"
navig quick add logs "docker logs -f app"

# With descriptions
navig quick add deploy "run 'deploy.sh'" --desc "Deploy to production"
navig quick add health "host test" --desc "Check SSH connectivity"
```

## Examples

```bash
# Create common shortcuts
navig quick add ps "docker ps"
navig quick add df "run 'df -h'"
navig quick add mem "run 'free -h'"
navig quick add top "run 'htop'"

# Then use them
navig quick ps     # Much shorter than 'navig docker ps'
navig q df         # Even shorter with 'q' alias
```

## Storage

Quick actions are stored in `~/.navig/quick_actions.yaml`:

```yaml
deploy:
  command: navig run 'cd /var/www && git pull'
  description: Deploy to production
  created: 2026-02-08T12:00:00
```

## Tips

- Use short, memorable names
- Include the full `navig` prefix in commands
- Use `--dry-run` to verify before running
- Commands with placeholders are shown but not executed

## Alias

The `q` alias works the same as `quick`:

```bash
navig q            # List actions
navig q deploy     # Run 'deploy' action
navig q add x "y"  # Add action
```

## See Also

- `navig suggest` — AI-powered command suggestions
- `navig history` — Command history and replay
- `navig flow` — More complex workflow automation
