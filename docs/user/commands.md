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

## Server Monitoring (`navig host monitor`)

Real-time server monitoring and health checks, nested under `navig host`.

| Command | Description |
|---------|-------------|
| `host monitor show` | Comprehensive health overview |
| `host monitor show --resources` | CPU, RAM, disk, network usage |
| `host monitor show --disk [--threshold N]` | Disk space with alerts |
| `host monitor show --services` | Check critical service status |
| `host monitor show --network` | Network statistics |
| `host monitor show --process` | Top processes by CPU/memory |

> **Note:** The deprecated `navig monitor` alias still works but will be removed in v3.0.
> Use `navig host monitor show` going forward.

---

## Security Management (`navig host security`)

Firewall, intrusion detection, and security auditing, nested under `navig host`.

| Command | Description |
|---------|-------------|
| `host security show` | Security overview |
| `host security show --firewall` | Show UFW firewall rules |
| `host security show --ssh` | Audit SSH configuration |
| `host security show --fail2ban` | Show Fail2Ban status |
| `host security show --updates` | Check security updates |

> **Note:** The deprecated `navig security` alias still works but will be removed in v3.0.
> Use `navig host security show` going forward.

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
| `tunnel run` | Start SSH tunnel for active host |
| `tunnel remove` | Stop and tear down tunnel |
| `tunnel show [--plain]` | Show tunnel status |
| `tunnel update` | Restart (stop + start) tunnel |
| `tunnel auto` | Auto-detect and create tunnel |

> **Note:** The old `tunnel start / stop / status / restart` aliases are deprecated.
> Use `run / remove / show / update` instead.

---

## File Operations (`navig file`)

Direct file management on remote servers. All operations are under `navig file`.

| Command | Description |
|---------|-------------|
| `file add <local> [remote]` | Upload file or directory |
| `file get <remote> [local]` | Download file |
| `file show <remote>` | View file contents |
| `file show <remote> --tail --lines 50` | View last 50 lines |
| `file show <remote> --lines 100-200` | View line range |
| `file list <dir>` | List directory |
| `file list <dir> --tree --depth 3` | Tree view |
| `file list <dir> --all` | Include hidden files |
| `file edit <remote> --content "..."` | Write content to file |
| `file edit <remote> --mode 755` | Change permissions |
| `file edit <remote> --owner user:group` | Change ownership |
| `file remove <remote>` | Delete file/directory |
| `file remove <remote> --recursive` | Recursive delete |

> **Note:** The legacy flat commands `navig upload`, `navig download`, `navig ls`,
> `navig cat` still work for backward compatibility but are deprecated.
> Use `navig file add / get / show / list` instead.

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

## HestiaCP (`navig web hestia`)

HestiaCP control panel integration, nested under `navig web`.

| Command | Description |
|---------|-------------|
| `web hestia list` | List HestiaCP virtual hosts |
| `web hestia add <domain>` | Add domain to HestiaCP |
| `web hestia remove <domain>` | Remove domain from HestiaCP |

> **Note:** The deprecated `navig hestia` top-level alias still works but will be removed in v3.0.
> Use `navig web hestia` going forward.
