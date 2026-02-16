# NAVIG Command Reference

## Command Categories

NAVIG commands are organized into logical categories for better discoverability:

| Category | Commands | Description |
|----------|----------|-------------|
| **QUICK START** | `init`, `run`, `menu`, `install` | Common entry points |
| **PILLAR 1: Infrastructure** | `host`, `host monitor`, `host security`, `host maintenance` | Server management |
| **PILLAR 2: Services** | `app`, `docker`, `web` | Application & container management |
| **PILLAR 3: Data** | `db`, `file`, `log`, `backup` | Data & file operations |
| **PILLAR 4: Automation** | `flow`, `ai`, `wiki` | Workflows & AI assistance |
| **LOCAL MACHINE** | `local`, `hosts`, `software` | Local system operations |
| **NETWORKING** | `tunnel` | SSH tunnel management |
| **CONFIGURATION** | `config`, `migrate`, `server-template`, `mcp` | Setup & configuration |

Run `navig --help` to see the full categorized command list.

---

## Global Options

All commands support these global options:

| Option | Short | Description |
|--------|-------|-------------|
| `--host` | `-h` | Override active host for this command |
| `--app` | `-p` | Override active app for this command |
| `--verbose` | | Enable detailed logging output |
| `--quiet` | `-q` | Minimal output |
| `--dry-run` | | Show what would be done without executing |
| `--yes` | `-y` | Auto-confirm all prompts |
| `--raw` | | Output raw data (no formatting) |
| `--json` | | Output data in JSON format |
| `--plain` | | Machine-readable plain text output |

---

## Host Management (`navig host`)

Manage remote server configurations.

| Command | Description |
|---------|-------------|
| `host list [--plain]` | List all configured hosts |
| `host add <name>` | Add new host interactively |
| `host edit <name>` | Edit host configuration |
| `host remove <name>` | Remove host configuration |
| `host use <name>` | Set active host |
| `host test` | Test SSH connection |
| `host show` | Show current host details |

---

## Database Operations (`navig db`)

Unified database management for MySQL/MariaDB/PostgreSQL.

| Command | Description |
|---------|-------------|
| `db list [--plain]` | List all databases with sizes |
| `db tables <database>` | List tables in a database |
| `db query "<sql>"` | Execute SQL query |
| `db file <file.sql>` | Execute SQL file |
| `db dump <database> [-o file]` | Backup database to file |
| `db restore <database> <file>` | Restore database from backup |
| `db shell` | Open interactive database shell |
| `db users` | List database users |
| `db containers` | List Docker database containers |
| `db optimize <db> <table>` | Optimize table |
| `db repair <db> <table>` | Repair table |

---

## Server Monitoring (`navig monitor`)

Real-time server monitoring and health checks.

| Command | Description |
|---------|-------------|
| `monitor resources` | CPU, RAM, disk, network usage |
| `monitor disk [--threshold N]` | Disk space with alerts |
| `monitor services` | Check critical service status |
| `monitor network` | Network statistics |
| `monitor health` | Comprehensive health check |
| `monitor report` | Generate monitoring report |

---

## Security Management (`navig security`)

Firewall, intrusion detection, and security auditing.

| Command | Description |
|---------|-------------|
| `security firewall` | Show UFW firewall status |
| `security firewall-add <port>` | Add firewall rule |
| `security firewall-remove <port>` | Remove firewall rule |
| `security firewall-enable` | Enable UFW firewall |
| `security firewall-disable` | Disable UFW firewall |
| `security fail2ban` | Show Fail2Ban status |
| `security unban <ip>` | Unban IP from Fail2Ban |
| `security ssh` | Audit SSH configuration |
| `security updates` | Check security updates |
| `security connections` | Audit network connections |
| `security scan` | Run security scan |

---

## Web Server (`navig web`)

Nginx and Apache management.

| Command | Description |
|---------|-------------|
| `web vhosts` | List virtual hosts |
| `web test` | Test configuration syntax |
| `web enable <site>` | Enable site |
| `web disable <site>` | Disable site |
| `web module-enable <mod>` | Enable Apache module |
| `web module-disable <mod>` | Disable Apache module |
| `web reload` | Reload web server |
| `web recommend` | Performance recommendations |

---

## SSH Tunnels (`navig tunnel`)

Secure tunnel management for database access.

| Command | Description |
|---------|-------------|
| `tunnel start` | Start SSH tunnel |
| `tunnel stop` | Stop SSH tunnel |
| `tunnel status [--plain]` | Show tunnel status |
| `tunnel restart` | Restart tunnel |

---

## File Operations

Direct file management on remote servers.

| Command | Description |
|---------|-------------|
| `upload <local> [remote]` | Upload file/directory |
| `download <remote> [local]` | Download file/directory |
| `cat <file>` | Display file contents |
| `ls [path]` | List directory |
| `tree [path]` | Show directory tree |
| `mkdir <path>` | Create directory |
| `delete <path>` | Delete file/directory |
| `chmod <mode> <path>` | Change permissions |
| `chown <owner> <path>` | Change ownership |
| `write-file <path> <content>` | Write content to file |

---

## Remote Execution

### `navig run`

Execute shell command on remote server with multiple input methods.

**Syntax:**
```bash
navig run "<command>"           # Standard execution
navig run --b64 "<command>"     # Base64-encoded (escape-proof)
navig run @-                    # Read from stdin
navig run @filename             # Read from file
navig run -i                    # Interactive multi-line mode
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--b64` | `-b` | Encode command as Base64 (escape-proof for JSON/special chars) |
| `--stdin` | `-s` | Read command from stdin |
| `--file` | `-f` | Read command from file |
| `--interactive` | `-i` | Open editor for multi-line input |
| `--yes` | `-y` | Auto-confirm prompts |

**When to use `--b64`:**
- Commands with JSON payloads: `curl -d '{"key":"value"}'`
- Commands with special characters: `$`, `!`, `(`, `)`, `[`, `]`, `{`, `}`
- Commands with nested quotes: `sh -c 'echo "nested"'`

**Examples:**

```bash
# Simple command
navig run "ls -la"

# JSON payload (use --b64)
navig run --b64 "curl -X POST -d '{\"user\":\"john\"}' api.com"

# Read from stdin
echo "df -h" | navig run "@-"

# Read from file
navig run "@scripts/deploy.sh"

# Interactive editor
navig run -i
```

### `navig install`

| Command | Description |
|---------|-------------|
| `install <package>` | Auto-detect package manager and install |

---

## Docker (`navig docker`)

Container management.

| Command | Description |
|---------|-------------|
| `docker ps` | List containers |
| `docker logs <container>` | View container logs |
| `docker exec <container> <cmd>` | Execute in container |
| `docker restart <container>` | Restart container |
| `docker stop <container>` | Stop container |
| `docker start <container>` | Start container |

---

## HestiaCP (`navig hestia`)

HestiaCP control panel integration.

| Command | Description |
|---------|-------------|
| `hestia users [--plain]` | List HestiaCP users |
| `hestia domains [--plain]` | List domains |
| `hestia user-info <user>` | User details |
| `hestia domain-info <domain>` | Domain details |



