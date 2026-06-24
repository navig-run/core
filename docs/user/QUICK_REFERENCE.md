# NAVIG CLI — Quick Reference

> Daily cheat sheet for remote server operations. All commands target the **active host** unless
> noted. Set active host with `navig host use <name>`.

---

## Host and Context

```bash
navig host list                    # List all configured hosts
navig host add                     # Add a new host (interactive wizard)
navig host use staging-01          # Switch active host
navig host show                    # Show current host details
navig host test                    # Test SSH connectivity
navig host test production         # Test a specific host

navig app list                     # List apps on active host
navig app use myapp                # Set active application context
navig app show                     # Show current app config
```

---

## Remote Execution

```bash
navig run "ls -la /var/www"               # Simple command
navig run "df -h && free -m"              # Chained command

# Complex commands with special characters — encode first (PowerShell):
$cmd = "php artisan tinker --execute='echo User::count();'"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($cmd))
navig run --b64 $b64

navig run @script.sh                      # From file
navig run -i                              # Interactive multi-line editor
echo "uptime" | navig run --stdin        # From stdin
```

---

## Database

```bash
navig db list                             # List all databases
navig db tables myapp_db                  # List tables
navig db query "SELECT COUNT(*) FROM users" -d myapp_db
navig db query "SELECT..." -d mydb --plain  # Pipe-friendly output

# Backup
navig db dump myapp_db -o backup.sql
navig db dump myapp_db -o backup.sql.gz   # Auto-compressed by extension

# Restore
navig db restore backup.sql -d myapp_db

# Laravel: prefer Tinker over raw SQL
$cmd = "cd /var/www/myapp && php artisan tinker --execute='App\Models\User::count()'"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($cmd))
navig run --b64 $b64
```

---

## File Operations

```bash
navig file list /var/www                  # List directory
navig file list /var/www --tree --depth 3 # Tree view
navig file list /var/www --all            # Include hidden files

navig file show /var/log/nginx/error.log --tail --lines 50
navig file show /var/log/app.log --lines 800-850

navig file add local.txt /tmp/remote.txt  # Upload file
navig file add ./dist/ /var/www/app/      # Upload directory
navig file get /var/log/app.log ./        # Download

navig file edit /etc/nginx/nginx.conf --content "..."
navig file edit /tmp/script.sh --mode 755
navig file remove /tmp/old --recursive
```

---

## Docker

```bash
navig docker ps                           # Running containers
navig docker ps -a                        # All containers
navig docker logs nginx -n 100            # Last 100 log lines
navig docker exec app "ls -la /app"       # Exec in container
navig docker restart nginx
navig docker stats                        # Resource usage
navig docker compose up -d -f docker-compose.yml
```

---

## SSH Tunnels

```bash
navig tunnel show                         # Active tunnel status
navig tunnel run                          # Start tunnel
navig tunnel stop                         # Stop tunnel
```

---

## Web Server

```bash
navig web vhosts                          # List virtual hosts
navig web test                            # Test nginx/apache config
navig web reload                          # Reload (tests config first)
navig web enable mysite.com
navig web disable mysite.com
```

---

## Host Health and Security

```bash
navig host monitor show                   # Full health overview
navig host monitor show --disk            # Disk usage
navig host monitor show --resources       # CPU/memory/load
navig host security show                  # Security overview
navig host security show --firewall       # Firewall rules
navig host maintenance                    # Run system maintenance
```

---

## Backup and Restore

```bash
navig backup show                         # List backups
navig backup export                       # Export NAVIG config
navig backup import backup.json           # Import NAVIG config
navig backup run --all                    # Full server backup
navig backup run --db-all --compress gzip # All databases, compressed
navig backup restore <name>               # Restore from backup
navig backup remove <name>                # Delete backup
```

---

## Flows (Workflows)

```bash
navig flow list                           # List saved flows
navig flow show deploy-app                # View flow steps
navig flow run deploy-app --dry-run       # Preview
navig flow run deploy-app                 # Execute
navig flow add my-flow                    # Create new flow
navig flow edit my-flow                   # Edit flow YAML
navig flow test my-flow                   # Validate syntax
```

---

## Wiki / Knowledge Base

```bash
navig wiki init                           # Create project wiki
navig wiki list                           # List all pages
navig wiki show architecture/overview     # View a page
navig wiki add notes.md                   # Add file to inbox
navig wiki search "database schema"       # Full-text search
navig wiki inbox process                  # AI-categorise inbox
```

---

## Plans and Briefing

```bash
navig plans status                        # Current progress
navig plans next                          # Next recommended actions
navig plans briefing                      # Today's summary
navig plans sync                          # Sync across spaces
```

---

## Configuration and Daemon

```bash
navig config validate                     # Validate config
navig config show                         # Display config
navig config set <key> <value>            # Set a value
navig service status                      # Daemon status
navig service start                       # Start daemon
navig service stop                        # Stop daemon
navig service logs -n 30                  # Last 30 log lines
```

---

## Common Flags

| Flag | Short | Effect |
|------|-------|--------|
| `--yes` | `-y` | Skip confirmation prompts |
| `--plain` / `--raw` | | No formatting (pipe-friendly) |
| `--json` | | Structured JSON output |
| `--dry-run` | | Preview — no changes made |
| `--b64` | `-b` | Decode base64 before exec |
| `--host <name>` | `-h` | Target a specific host |

---

## One-Liners

```powershell
# Timestamped database backup
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
navig db dump myapp_db -o "backup-$ts.sql"

# Tail app logs
navig file show /var/log/myapp/app.log --tail --lines 100

# Restart container and verify
navig docker restart app; navig docker logs app -n 20

# Validate config then restart daemon
navig config validate; navig service stop; navig service start
```

---

## Getting Help

```bash
navig help                                # Browse in-app help topics
navig help db                             # Help for a specific group
navig help --schema                       # Machine-readable command registry
navig <group> --help                      # Full flags for any group
```

---

> **See also:**
> - Full command reference: [`docs/user/HANDBOOK.md`](HANDBOOK.md)
> - Desktop automation: [`docs/user/CLI_COMMANDS.md`](CLI_COMMANDS.md)
> - Common recipes: [`docs/user/workflows.md`](workflows.md)
> - Telegram bot reference: [`docs/features/TELEGRAM.md`](../features/TELEGRAM.md)
