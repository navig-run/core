---
name: tmux-sessions
description: Manage tmux sessions on remote Linux servers for persistent terminal sessions
user-invocable: true
os: [linux]
navig-commands:
  - navig run "tmux list-sessions"
  - navig run "tmux new -d -s {name}"
  - navig run "tmux send-keys -t {session} '{command}' Enter"
  - navig run "tmux capture-pane -p -t {session}"
  - navig run "tmux kill-session -t {session}"
examples:
  - "List tmux sessions on the server"
  - "Start a long-running script in tmux"
  - "Check what's running in my tmux session"
  - "Kill the backup tmux session"
  - "Create a new tmux session for monitoring"
---

# tmux Session Management

Manage persistent terminal sessions on remote Linux servers using tmux. Perfect for long-running tasks that should survive SSH disconnects.

## When to Use

- Running long tasks (backups, migrations, builds) that shouldn't die when SSH drops
- Monitoring server output over time
- Running multiple tasks in parallel on a server

## Common Tasks

### List Active Sessions

**User says:** "What tmux sessions are running?"

```bash
navig run "tmux list-sessions 2>/dev/null || echo 'No tmux sessions running'"
```

**Response format:**
```
🧵 tmux Sessions on {host}:

• backup: 1 window (created 2h ago) - attached
• monitor: 2 windows (created 1d ago) - detached
• deploy: 1 window (created 30m ago) - detached
```

### Create a New Session

**User says:** "Start a background backup in tmux"

```bash
navig run "tmux new -d -s backup"
navig run "tmux send-keys -t backup 'mysqldump -u root mydb | gzip > /backup/db.sql.gz' Enter"
```

**Response:**
```
🧵 Created tmux session 'backup'

Running: mysqldump -u root mydb | gzip > /backup/db.sql.gz
Status: ▶️ In progress

Check output: Ask me "check backup session"
```

### Check Session Output

**User says:** "What's happening in the backup session?"

```bash
navig run "tmux capture-pane -p -t backup -S -50"
```

Shows the last 50 lines of output from that session.

### Run a Monitoring Session

**User says:** "Set up monitoring on the server"

```bash
navig run "tmux new -d -s monitor"
navig run "tmux send-keys -t monitor 'htop' Enter"
```

### Kill a Session

**User says:** "Stop the backup session"

```bash
navig run "tmux kill-session -t backup"
```

**Response:**
```
🧵 Session 'backup' terminated ✅
```

### Send a Command to Running Session

```bash
navig run "tmux send-keys -t {session} '{command}' Enter"
```

### Send Ctrl+C to Stop a Process

```bash
navig run "tmux send-keys -t {session} C-c"
```

## Common Patterns

### Long-Running Backup

```bash
navig run "tmux new -d -s backup-job 'tar czf /backup/full-$(date +%F).tar.gz /var/www && echo DONE'"
```

### Log Tailing

```bash
navig run "tmux new -d -s logs 'tail -f /var/log/nginx/access.log'"
```

### Database Migration

```bash
navig run "tmux new -d -s migrate 'cd /var/www/app && php artisan migrate --force'"
```

## Proactive Suggestions

- **No tmux installed**: "tmux is not installed. Install with: `navig run 'apt install -y tmux'`"
- **Session still running**: "The 'backup' session from 2 hours ago is still active. Want to check its output?"
- **Many sessions**: "You have 5 tmux sessions. Want me to list them or clean up old ones?"

## Error Handling

- **tmux not installed**: "Install tmux: `navig run 'sudo apt install -y tmux'`"
- **Session not found**: "Session '{name}' doesn't exist. Active sessions: {list}"
- **No sessions**: "No tmux sessions running on {host}."

## Notes

- tmux is Linux/macOS only (not native on Windows)
- Sessions persist across SSH disconnects
- Use `tmux attach -t {name}` for interactive access (via `navig shell`)
