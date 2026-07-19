---
name: navig
description: Core NAVIG CLI wrapper for host management, file operations, and remote execution.
metadata:
  navig:
    emoji: 🚀
    requires:
      bins: [navig, python]
---

# NAVIG Core Skill

This skill provides the fundamental interface to the NAVIG system, allowing agents to manage remote hosts, transfer files, and execute commands.

## Host Management

### List & Select Hosts
```bash
# List all configured hosts to see what's available
navig host list

# Switch context to a specific host (REQUIRED before remote ops)
navig host use <hostname>

# Verify current connection
navig host show
navig host test
```

### Monitoring & Security
```bash
# Check server health (CPU, RAM, Disk)
navig host monitor show

# Check security status (Firewall, SSH)
navig host security show
```

## Remote Execution

### Run Commands
```bash
# Simple command
navig run "df -h"

# Complex command (automatically handles base64 if needed by the tool, but explicit is safer)
$cmd = "docker ps | grep my-container"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($cmd))
navig run --b64 $b64
```

### Run Scripts
```bash
# Run a local script on the remote host
navig run @myscript.sh
```

## File Operations

### Transfer Files
```bash
# Upload file (No confirmation needed)
navig file add ./local-config.yml /etc/myapp/config.yml

# Download file
navig file get /var/log/syslog ./logs/syslog.txt
```

### View & Edit
```bash
# View file content (use --tail for logs)
navig file show /var/log/nginx/error.log --tail --lines 50

# List directory
navig file list /var/www/html

# Edit file content directly
navig file edit /etc/hosts --content "127.0.0.1 localhost"
```

## Database Operations (Basic)

For advanced DB ops, use the `postgres` or `mysql` specific skills, but `navig db` is the primitive.

```bash
# List databases
navig db list

# Run query (Use --plain for parsing)
navig db query "SELECT count(*) FROM users" -d myapp_db --plain
```

## Best Practices
1. **Always set context**: Run `navig host use` before any operation sequence.
2. **Limit Output**: Use `--tail`, `--lines`, or `grep` to avoid overwhelming the context window.
3. **Check First**: Use `navig host test` if a command fails to distinguish network issues from config errors.
