---
applyTo: '**'
---

# NAVIG CLI — AI Agent Directive Rules

> Behavioral directives for AI agents interacting with remote hosts and applications via NAVIG CLI.

## Priority Rules (Read First)

- Check `CHANGELOG.md` first before implementing related changes.
- Use `.dev/` for AI scripts/logs/outputs/scratch.
- Use `.local/` only for backups/moved files and compatibility temp artifacts.
- Keep repo root clean and avoid ad-hoc files.
- When command syntax is uncertain, prefer `navig help --schema` before guessing.

## 🚨 QUICK REFERENCE: Most Common Mistakes

### Mistake 1: Using `--b64` flag incorrectly
```powershell
# ❌ WRONG
navig run --b64 "php artisan tinker --execute='echo User::count();'"

# ✅ CORRECT
$cmd = "php artisan tinker --execute='echo User::count();'"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($cmd))
navig run --b64 $b64
```
**Remember**: `--b64` expects BASE64-ENCODED string, NOT raw command!

### Mistake 2: Using `--force` flag on file commands
```bash
# ❌ WRONG
navig file add myfile.txt /tmp/ --force  # Flag doesn't exist!

# ✅ CORRECT
navig file add myfile.txt /tmp/  # No confirmation needed
```
**Remember**: `navig file add` uploads directly without prompts

### Mistake 3: Using `--compress` flag on database dumps
```powershell
# ❌ WRONG
navig db dump mydb -o backup.sql --compress gzip  # Flag doesn't exist!

# ✅ CORRECT
navig db dump mydb -o backup.sql.gz  # Compression detected from .gz extension
# OR manually compress after:
navig db dump mydb -o backup.sql
gzip backup.sql
```
**Remember**: `navig db dump` only has: `-o`, `-c`, `-u`, `-p`, `-t` flags

### Mistake 4: Laravel - using DB queries instead of Tinker
```powershell
# ❌ LESS IDEAL
navig db query "SELECT * FROM users LIMIT 10" -d myapp_db

# ✅ BETTER (for Laravel apps)
$cmd = "cd /path/to/laravel && php artisan tinker --execute='echo User::limit(10)->get();'"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($cmd))
navig run --b64 $b64
```
**Remember**: Tinker gives you Eloquent models, relationships, and business logic

---


## CRITICAL: Always Use NAVIG for Remote Operations
- **MANDATORY**: When user asks to perform ANY operation on a remote server, database, or application, you MUST use NAVIG CLI commands
- **NEVER** attempt to SSH manually or run raw remote commands without NAVIG
- **NEVER** hardcode credentials or connection details - NAVIG manages all host configurations
- **WHY THIS MATTERS**: NAVIG provides secure tunneling, credential management, and consistent error handling


## Host Context Management (navig_host_context)
- **WHEN TO RUN**: BEFORE any remote operation, verify or set the active host context
- **CHECK FIRST**: Run `navig host show` to see active host (or `navig status`)
- **SET CONTEXT**: Use `navig host use <hostname>` to switch hosts
- **VERIFICATION**: After switching, confirm with `navig host test` to verify SSH connectivity
- **CONFIG HEALTH CHECK**: If commands fail unexpectedly, run `navig config validate` (use `--scope project|global|both` as appropriate)
- **TRIGGER ACTIONS**:
  - User mentions a server name/hostname
  - User asks to "connect to", "switch to", or "use" a host
  - User asks about a specific remote server
  - Before running any `navig run`, `navig db`, `navig docker` commands
- **ENFORCEMENT**: NEVER run remote commands without first confirming the target host is correct
- **AVAILABLE COMMANDS**:
  | Command | Purpose |
  |---------|---------|
  | `navig host list` | List all configured hosts |
  | `navig host use <name>` | Switch active host context |
  | `navig host add` | Add new host (interactive wizard) |
  | `navig host show` | Show current host information |
  | `navig host test` | Test SSH connection |
  | `navig host monitor show` | Server monitoring (resources, disk) |
  | `navig host security show` | Security status (firewall, SSH) |
  | `navig host maintenance` | System maintenance (updates, cleanup) |
  | `navig host discover-local` | Discover local development environment |
- **EXAMPLE FLOW**:
  1. User: "Check disk space on production"
  2. Agent: Run `navig host show` → verify it's "production"
  3. If not: Run `navig host use production`
  4. Then: Run `navig run "df -h"`


## Remote Command Execution (navig_run)
- **WHEN TO RUN**: Whenever user needs to execute commands on a remote server
- **CRITICAL ENCODING RULES**:
  - **Simple commands**: `navig run "ls -la"`
  - **Complex commands with special chars**: Encode with base64 FIRST, then pass the encoded string
  - **Multi-line scripts**: Use `navig run @script.sh` or `navig run -i` for editor
  - **From stdin**: `echo "command" | navig run --stdin` or `navig run @-`

### 🚨 POWERSHELL BASE64 WORKFLOW (MANDATORY for complex commands)

**For any command containing these characters: `$ ! ( ) { } " ' \` [ ]`**

**YOU MUST USE THIS 3-STEP PROCESS:**

```powershell
# Step 1: Define command in a variable
$cmd = "php artisan tinker --execute='echo ExampleImport::pending()->whereNull(\"scheduled_at\")->count();'"

# Step 2: Encode to base64
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($cmd))

# Step 3: Pass the ENCODED variable (not the raw command!)
navig run --b64 $b64
```

**CRITICAL UNDERSTANDING:**
- `--b64` flag expects a BASE64-ENCODED STRING
- It does NOT encode the string for you
- If you pass raw text to `--b64`, PowerShell will parse it and break it

**FORBIDDEN (WILL FAIL):**
```powershell
# ❌ WRONG - passing raw command to --b64
navig run --b64 "cd /path && php artisan tinker --execute='echo User::count();'"
# Error: Got unexpected extra argument

# ❌ WRONG - using --b64 without encoding first
navig run --b64 $cmd
# Error: bash will try to decode the raw command as base64 and fail
```

**CORRECT:**
```powershell
# ✅ RIGHT - encode first, then pass the variable
$cmd = "cd /path && php artisan tinker --execute='echo User::count();'"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($cmd))
navig run --b64 $b64
```

### Fast In-App Help (Use Before Searching Docs)
- `navig help` lists topics; `navig help <topic>` shows subcommands
- `navig help --schema` outputs the authoritative machine-readable command registry (preferred for AI/tooling)
- Use `--plain` for scripting and `--json` for structured output
- Prefer this to discover the canonical noun-verb actions quickly

### Skills Inventory (AI Skill Files)
- List skills: `navig skills list` (add `--plain` or `--json` for automation)
- Grouped view: `navig skills tree` to see categories
- If skills are missing, pass `--dir` with the skills/ path

- **AVAILABLE OPTIONS**:
  | Flag | Short | Purpose |
  |------|-------|---------|
  | `--b64` | `-b` | Decode BASE64-encoded string before execution |
  | `--stdin` | `-s` | Read command from stdin |
  | `--file` | `-f` | Read command from file |
  | `--interactive` | `-i` | Open editor for multi-line input |
  | `--yes` | `-y` | Auto-confirm prompts |
  | `--confirm` | `-c` | Force confirmation prompt |
  | `--json` | | Output JSON (captures stdout/stderr) |
- **ALTERNATIVE METHODS** (if base64 feels complex):
  - Save to file: `echo 'command' > cmd.sh; navig run @cmd.sh`
  - Use stdin: `@' command '@ | navig run --stdin` (PowerShell here-string)
  - Use editor: `navig run -i` (opens text editor for multi-line input)
- **CORRECT PATTERNS**:
  - ✅ Encode first: `$b64 = [Convert]::ToBase64String(...); navig run --b64 $b64`
  - ✅ Simple file: `navig run @script.sh`
  - ✅ Interactive: `navig run -i`
  - ✅ From stdin: `echo "command" | navig run --stdin`
- **ENFORCEMENT**: Complex commands with special chars → ALWAYS encode to base64 BEFORE passing to `--b64` flag


## Database Operations (navig_db)
- **WHEN TO RUN**: Any database-related user request
- **TRIGGER PHRASES**:
  - "show databases", "list tables", "query", "dump", "backup database"
  - "run SQL", "execute query", "database size"
- **WORKFLOW**:
  1. First: `navig db list` to see available databases
  2. Query: `navig db query "SELECT..." -d <database>`
  3. Backup: `navig db dump <database> -o backup.sql`
- **AVAILABLE COMMANDS**:
  | Command | Purpose |
  |---------|---------|
  | `navig db list` | List all databases |
  | `navig db tables <database>` | List tables in database |
  | `navig db query "SQL" -d <db>` | Execute SQL query |
  | `navig db file <sqlfile>` | Execute SQL file |
  | `navig db dump <database> -o file.sql` | Dump/backup database (no --compress flag!) |
  | `navig db restore <file> -d <db>` | Restore database |
  | `navig db optimize <table> -d <db>` | Optimize table |
  | `navig db repair <table> -d <db>` | Repair table |
  | `navig db show` | Show database information |
  | `navig db run` | Run SQL query/file or open shell |
- **DUMP OPTIONS** (for `navig db dump`):
  | Flag | Short | Purpose |
  |------|-------|---------|
  | `--output` | `-o` | Output file path (e.g., `backup.sql.gz`) |
  | `--container` | `-c` | Docker container name |
  | `--user` | `-u` | Database user (default: root) |
  | `--password` | `-p` | Database password |
  | `--type` | `-t` | Database type: mysql, mariadb, postgresql |
  **NOTE**: There is NO `--compress` flag! Use `.gz` extension or compress manually after.
- **OUTPUT FORMATS**:
  - **(default)**: Tab-separated output - **USE THIS FOR USER DISPLAY** (compact AND readable)
  - `--plain` or `--raw`: Same as default but guaranteed no colors - use only for piping/scripting
  - `--json`: Structured JSON - **AVOID** (uses 5-10x more tokens due to envelope metadata)
- **CRITICAL OUTPUT RULE**:
  - **For showing results to user**: Use default (no flags) - it's already token-efficient
  - **For parsing/scripting**: Use `--plain`
  - **NEVER use `--json`** for database queries shown to users (wastes tokens)
- **BASE64 AUTO-DETECTION** (complex queries just work):
  - **Base64 is automatically detected** - no flags needed!
  - For complex queries with special characters, just encode and pass:
    ```powershell
    $sql = "INSERT INTO table VALUES ('json', '[\"value\"]');"
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($sql))
    navig db query $b64 -d database_name --plain
    ```
  - Tool detects base64, decodes automatically, executes query
  - Use `--b64` only to force decoding if auto-detection fails
- **CRITICAL RULES**:
  - Always specify database with `-d` flag using the **actual database name**
  - Database name ≠ app name (check app config `database.name` field)
  - Use `--plain` or `--raw` flag for script-friendly output (both are aliases)
- **COMMON MISTAKES TO AVOID**:
  - ❌ Using app name as database: `navig db query "..." -d example-app`
  - ✅ Using actual database name: `navig db query "..." -d myapp_db`
  - ❌ Using non-existent flags: `--format raw`, `--compress gzip`
  - ✅ Using correct flags: `--plain` or `--raw` (both work)
  - ❌ Trying to compress during dump: `navig db dump mydb -o backup.sql --compress gzip`
  - ✅ Compress with file extension: `navig db dump mydb -o backup.sql.gz` or compress manually after
- **EXAMPLE CORRECT FLOW**:
  - User: "Show all users from myapp database"
  - Agent: First check app config for actual database name
  - Agent: `navig db query "SELECT * FROM users LIMIT 100" -d myapp_production --plain`
- **BACKUP EXAMPLES**:
  ```powershell
  # Simple backup
  navig db dump mydb -o backup.sql

  # Backup with timestamp
  $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
  navig db dump mydb -o "backup-$timestamp.sql"

  # Compress after dumping (manual)
  navig db dump mydb -o backup.sql
  gzip backup.sql  # Creates backup.sql.gz
  ```

### 🔥 LARAVEL APPLICATIONS: Use Tinker Instead of Direct DB Queries

**CRITICAL**: For Laravel applications, prefer `php artisan tinker` over `navig db query`:

**Why Tinker is Better:**
- ✅ Access to Eloquent models and relationships
- ✅ Uses application's database connection (no need to specify `-d` flag)
- ✅ Access to model methods, scopes, accessors, and business logic
- ✅ Respects model events, observers, and middleware
- ✅ Type safety and IDE-friendly syntax

**How to Use Tinker:**
```powershell
# Step 1: Define tinker command
$cmd = "cd /path/to/laravel && php artisan tinker --execute='echo App\Models\User::count();'"

# Step 2: Encode to base64
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($cmd))

# Step 3: Run with navig
navig run --b64 $b64
```

**Common Tinker Patterns:**
```php
# Count records
echo App\Models\User::count();

# Get recent records with relationships
App\Models\Order::with('user')->latest()->take(10)->get();

# Complex queries with Eloquent
App\Models\User::where('status', 'active')->whereHas('orders')->count();

# Access config/env
echo config('services.stripe.key');
echo env('APP_ENV');
```

**When to Use Direct DB Queries Instead:**
- ❌ Non-Laravel applications
- ❌ Need raw SQL performance optimization
- ❌ Working with views, stored procedures, or complex joins not mapped to models
- ❌ Database administration tasks (ALTER TABLE, CREATE INDEX, etc.)


## File Operations (navig_file)
- **WHEN TO RUN**: File transfer or remote file manipulation
- **AVAILABLE COMMANDS**:
  | Command | Purpose |
  |---------|---------|
  | `navig file add <local> [remote]` | Upload file/directory (no confirmation prompt) |
  | `navig file get <remote> [local]` | Download file |
  | `navig file show <remote>` | View file contents |
  | `navig file list <dir>` | List directory contents |
  | `navig file edit <path>` | Edit remote file |
  | `navig file remove <path>` | Delete remote file/directory |
- **COMMON OPTIONS**:
  | Flag | Purpose | Available On |
  |------|---------|--------------|
  | `--tail` | Show end of file | `show` |
  | `--lines N` | Show N lines | `show` |
  | `--lines 100-200` | Show line range (dash or colon) | `show` |
  | `--tree` | Tree view for directories | `list` |
  | `--depth N` | Tree depth limit | `list` |
  | `--all` | Include hidden files | `list` |
  | `--content "..."` | Write content directly | `edit` |
  | `--mode 644` | Set file permissions | `add` (directories), `edit` |
  | `--owner user:group` | Set ownership | `edit` |
  | `--recursive` | Recursive operations | `remove` |
  | `--dir` / `-d` | Create directory instead of upload | `add` |
  | `--parents` / `-p` | Create parent directories | `add` |
- **IMPORTANT**: `navig file add` does NOT have `--yes` or `--force` flags - it uploads directly without prompting

## Scaffolding & Templating (navig_scaffold)
- **WHEN TO RUN**: Generating project structures from YAML templates
- **AVAILABLE COMMANDS**:
  | Command | Purpose |
  |---------|---------|
  | `navig scaffold apply <template> [options]` | Generate structure from template |
  | `navig scaffold validate <template>` | Validate template syntax |
- **COMMON OPTIONS**:
  | Flag | Purpose |
  |------|---------|
  | `--host`, `-h` | Target remote host (generates local -> uploads -> extracts) |
  | `--target-dir`, `-d` | Destination directory (default .) |
  | `--set key=value` | Override template variables |
  | `--dry-run` | Preview actions without execution |
- **TEMPLATE FORMAT**:
  - YAML file defining `meta` (variables) and `structure` (nested dirs/files)
  - Supports Jinja2 logic (`condition`, variables in paths/content)

- **EXAMPLES**:
  - Upload: `navig file add local.txt /tmp/remote.txt`
  - Download: `navig file get /var/log/app.log ./local.log`
  - Read tail: `navig file show /var/log/nginx/error.log --tail --lines 50`
  - Read range: `navig file show /var/log/app.log --lines 800-850`
  - List dir: `navig file list /var/www --all`
  - Tree view: `navig file list /var/www --tree --depth 3`
  - Write: `navig file edit /tmp/config.txt --content "new content"`
  - Chmod: `navig file edit /tmp/script.sh --mode 755`
  - Delete: `navig file remove /tmp/old --recursive`
- **LEGACY NOTE**: Flat commands like `navig upload`, `navig cat`, `navig ls` exist but are deprecated; prefer the `navig file ...` group
- **FORBIDDEN**: For file reads, don't shell out via `navig run "cat ..."`; use `navig file show` (safer, supports limiting)
- **LOG FILES**: Always limit output (e.g., `--tail --lines 50`) to avoid overwhelming output


## Docker Operations (navig_docker)
- **WHEN TO RUN**: Container management on remote hosts
- **TRIGGER PHRASES**:
  - "list containers", "docker ps", "container logs", "restart container"
  - "docker compose", "container stats"
- **AVAILABLE COMMANDS**:
  | Command | Purpose |
  |---------|---------|
  | `navig docker ps` | List containers (add `-a` for all) |
  | `navig docker logs <container>` | View container logs |
  | `navig docker exec <container> "cmd"` | Execute in container |
  | `navig docker compose <action>` | Docker compose operations |
  | `navig docker restart <container>` | Restart container |
  | `navig docker stop <container>` | Stop container |
  | `navig docker start <container>` | Start container |
  | `navig docker stats` | Container resource usage |
  | `navig docker inspect <container>` | Inspect container |
- **CRITICAL**: Always limit log output with `-n` to avoid context overflow
- **EXAMPLES**:
  - List all: `navig docker ps -a`
  - Logs: `navig docker logs nginx -n 100`
  - Exec: `navig docker exec app "ls -la /app"`
  - Compose: `navig docker compose up -d -f docker-compose.yml`


## Web Server Operations (navig_web)
- **WHEN TO RUN**: Web server configuration and management
- **TRIGGER PHRASES**:
  - "list vhosts", "enable site", "disable site", "reload nginx/apache"
  - "test config", "web performance"
- **AVAILABLE COMMANDS**:
  | Command | Purpose |
  |---------|---------|
  | `navig web vhosts` | List virtual hosts |
  | `navig web test` | Test configuration syntax |
  | `navig web enable <site>` | Enable site |
  | `navig web disable <site>` | Disable site |
  | `navig web reload` | Safely reload (tests first) |
  | `navig web recommend` | Performance tuning recommendations |
  | `navig web module-enable <mod>` | Enable Apache module |
  | `navig web module-disable <mod>` | Disable Apache module |
  | `navig web hestia` | HestiaCP control panel management |


## Monitoring & Security (navig_monitoring)
- **WHEN TO RUN**: Health checks, resource monitoring, security audits
- **TRIGGER PHRASES**:
  - "check server health", "disk space", "memory usage", "CPU"
  - "security scan", "firewall status", "check updates"
- **AVAILABLE COMMANDS**:
  | Command | Purpose |
  |---------|---------|
  | `navig host monitor show` | Overall health check |
  | `navig host monitor show --disk` | Disk usage |
  | `navig host monitor show --resources` | CPU/memory/load |
  | `navig host security show` | Security overview |
  | `navig host security show --firewall` | Firewall rules |
  | `navig host security show --ssh` | SSH security status |
  | `navig host maintenance` | System maintenance tasks |
- **DEFAULT BEHAVIOR**: For "health/status" run monitoring first; add security scan when explicitly asked or when diagnosing suspicious behavior


## Backup Operations (navig_backup)
- **WHEN TO RUN**: Backup or restore operations
- **TRIGGER PHRASES**:
  - "backup database", "backup config", "export settings"
  - "restore from backup", "import configuration"
- **AVAILABLE COMMANDS**:
  | Command | Purpose |
  |---------|---------|
  | `navig backup export` | Export NAVIG configuration |
  | `navig backup import <file>` | Import NAVIG configuration |
  | `navig backup show` | Show/list backups |
  | `navig backup run` | Run server backup operations |
  | `navig backup restore <name>` | Restore from backup |
  | `navig backup remove <name>` | Delete backup |
- **BACKUP RUN OPTIONS**:
  | Flag | Purpose |
  |------|---------|
  | `--config` | Backup system configuration |
  | `--db-all` | Backup all databases |
  | `--hestia` | Backup HestiaCP config |
  | `--web` | Backup web server config |
  | `--all` | Full backup |
  | `--compress gzip` | Compress backups |
- **EXAMPLES**:
  - DB backup: `navig db dump mydb -o backup.sql.gz`
  - Config backup: `navig backup run --config`
  - All DBs: `navig backup run --db-all --compress gzip`
  - Full: `navig backup run --all`
  - Restore: `navig backup restore backup_name --force`
  - NAVIG export: `navig backup export -o navig-config.yaml`
- **CRITICAL**: Always confirm backup destination with user before destructive restore operations


## Configuration Management (navig_config)
- **WHEN TO RUN**: Troubleshooting, onboarding, or after editing YAML
- **AVAILABLE COMMANDS**:
  | Command | Purpose |
  |---------|---------|
  | `navig config validate` | Validate configuration |
  | `navig config show` | Display configuration |
  | `navig config settings` | Show current settings |
  | `navig config set <key> <value>` | Set configuration value |
  | `navig config get <key>` | Get configuration value |
  | `navig config edit` | Open config in editor |
  | `navig config set-mode` | Set execution mode |
  | `navig config set-confirmation-level` | Set confirmation level |
  | `navig config schema` | JSON schema tools (VS Code) |
  | `navig config migrate` | Migrate legacy configs |
- **VALIDATION OPTIONS**:
  | Flag | Purpose |
  |------|---------|
  | `--scope project` | Validate project config only |
  | `--scope global` | Validate global config only |
  | `--scope both` | Validate both |
  | `--strict` | Strict validation mode |
  | `--json` | Machine-readable output |
- **VS CODE INTEGRATION**:
  - Install schemas: `navig config schema install --scope global`
  - Write VS Code settings: `navig config schema install --write-vscode-settings`


## Application Context (navig_app_context)
- **WHEN TO RUN**: When working with specific applications
- **CHECK FIRST**: `navig app show` to see active app
- **SET CONTEXT**: `navig app use <appname>` to switch
- **AVAILABLE COMMANDS**:
  | Command | Purpose |
  |---------|---------|
  | `navig app list` | List all apps on host |
  | `navig app use <name>` | Set active app |
  | `navig app add` | Add new app |
  | `navig app remove <name>` | Remove app |
  | `navig app show` | Show app configuration |
  | `navig app edit` | Edit app config |
  | `navig app search <query>` | Search apps across hosts |
  | `navig app migrate` | Migrate legacy app format |
- **TRIGGER ACTIONS**:
  - User mentions specific app name
  - User asks about "the app", "this application"
  - Before app-specific operations (deploys, configs)


## Tunnel Management (navig_tunnel)
- **WHEN TO RUN**: SSH tunnel operations
- **TRIGGER PHRASES**:
  - "create tunnel", "tunnel status", "close tunnel"
  - "port forward", "local port"
- **USAGE**: `navig help tunnel` for detailed commands
- **CHECK STATUS**: `navig status` shows active tunnel state


## Workflow Execution (navig_flow)
- **WHEN TO RUN**: Multi-step automated tasks
- **TRIGGER PHRASES**:
  - "run workflow", "deploy", "execute flow"
  - "automated task", "run template"
- **COMMANDS**:
  - List: `navig flow list`
  - Run: `navig flow run <name>` (add `--dry-run` first!)
  - Templates: `navig flow template list`
- **CRITICAL**: Always run with `--dry-run` first to preview actions


## Output Format Rules
- **FOR DATABASE QUERIES**: Use default output (no flags) - already compact, human-readable
- **FOR SCRIPTING/PARSING**: Use `--plain` or `--raw`
- **AVOID `--json`**: It adds metadata envelope that wastes 5-10x tokens
- **SUPPRESS PROMPTS**: Use `--yes` or `-y` to auto-confirm
- **WHEN TO USE EACH**:
  | Scenario | Format | Example |
  |----------|--------|---------|
  | Show user DB results | (default) | `navig db query "SELECT..." -d mydb` |
  | Parse output in script | `--plain` | `navig db list --plain \| grep mydb` |
  | API/automation | `--json` | Only when JSON structure required |
  | List hosts for user | (default) | `navig host list` |


## In-App Help (navig_help)
- **WHEN TO RUN**: When user asks "what commands exist", "how do I…", "show options", or when unsure of exact syntax
- **CANONICAL DISCOVERY**: Run `navig help --schema` first for exact command paths/options source-of-truth (same as `navig --schema`)
- **TOPICS LIST**: `navig help`
- **TOPIC DETAILS**: `navig help <topic>` (e.g., `navig help db`, `navig help file`, `navig help backup`)
- **DEEP HELP**: Prefer `navig <resource> --help` for full flags and arguments
- **AVAILABLE TOPICS**: ai, app, backup, config, db, docker, file, flow, host, index, local, log, mcp, run, tunnel, web, wiki
- **OUTPUT CONTROL**: Use `--plain` for machine-friendly help


## Telegram Space & Intake Shortcuts (navig_telegram_space)
- **WHEN TO USE**: When user wants ultra-fast planning flow directly in Telegram without jumping back to CLI.
- **COMMANDS**:
  - `/spaces` — list available spaces and show active space
  - `/space <name>` — switch active space and return top 3 next actions immediately
  - `/intake [space]` — guided 4-question planning intake that updates `VISION.md`, `ROADMAP.md`, `CURRENT_PHASE.md`
  - `/intake cancel` — stop active intake session
- **OPS SPACES**: `devops` and `sysops` are first-class spaces and should be offered as standard choices.


## Error Handling Rules
- **ON CONNECTION ERROR**:
  1. Run `navig host test` to verify connectivity
  2. Check `navig status` for tunnel state
  3. Suggest `navig host show` for diagnostics
- **ON PERMISSION ERROR**:
  1. Check if command needs sudo (NAVIG handles this transparently)
  2. Verify user has access to the resource
- **ON CONFIG ERROR**:
  1. Run `navig config validate` to check configuration
  2. Suggest `navig config show` to review settings
- **ALWAYS**: Provide actionable error messages - what went wrong and how to fix it
- **DEBUG LOG**: Check `~/.navig/debug.log` for detailed errors


## Configuration Scope
- **GLOBAL CONFIG**: `~/.navig` — user settings across projects
- **PROJECT CONFIG**: `.navig/` in project root — project-specific settings
- **CHECK CONFIG**: `navig config show` to see current settings
- **MODIFY CONFIG**: `navig config set <key> <value>`


---

# NAVIG Command Quick Reference

## Core Pattern
`navig <resource> <action> [options]`

## Resource Groups

| Group | Commands | Purpose |
|-------|----------|---------|
| `host` | `list`, `add`, `use`, `test`, `show`, `monitor`, `security`, `maintenance` | Host management |
| `app` | `list`, `add`, `use`, `show`, `edit`, `remove`, `search` | Application management |
| `run` | `"command"`, `--b64`, `@file`, `-i` | Remote execution |
| `db` | `list`, `tables`, `query`, `dump`, `restore`, `optimize`, `repair` | Database ops |
| `docker` | `ps`, `logs`, `exec`, `compose`, `restart`, `stop`, `start`, `stats` | Container ops |
| `telegram` | `status`, `send`, `sessions list/show/clear/delete/prune` | Telegram bot operations |
| `gateway` | `start`, `stop`, `status`, `session`, `test` | Gateway runtime + smoke testing |
| `file` | `add`, `list`, `show`, `edit`, `get`, `remove` | File operations |
| `web` | `vhosts`, `test`, `enable`, `disable`, `reload`, `hestia` | Web server |
| `backup` | `export`, `import`, `show`, `run`, `restore`, `remove` | Backup/restore |
| `config` | `validate`, `show`, `settings`, `set`, `get`, `edit`, `schema` | Configuration |
| `import` | `--source`, `--path`, `--output`, `list-sources` | Universal data import |
| `contacts` | `list`, `add`, `import` | Contact address book operations |
| `tunnel` | (see `navig help tunnel`) | SSH tunnels |
| `flow` | `list`, `run`, `template` | Workflows |
| `agent continuation` | `status`, `continue`, `pause`, `skip` | CLI continuation policy controls |
| `plans` | `status`, `add`, `run` (alias), `sync`, `update`, `next`, `briefing` | Space-aware planning |
| `auto` | `status`, `click`, `type`, `open`, `windows`, `snap` | UI Automation |
| `ahk` | `clipboard`, `screenshot`, `ocr`, `listen` | Windows Automation |
| `script` | `list`, `run`, `edit`, `new` | Script Management |
| `evolve` | `script`, `fix` | AI Code Evolution |
| `help` | `<topic>` | In-app help |

## Global Flags
| Flag | Short | Purpose |
|------|-------|---------|
| `--host <name>` | `-h` | Target specific host |
| `--yes` | `-y` | Auto-confirm prompts |
| `--raw` / `--plain` | | Raw output (no formatting) |
| `--json` | | JSON output format |
| `--b64` | `-b` | Base64 encode commands |
| `--dry-run` | | Preview without executing |

## Short Aliases
| Alias | Command |
|-------|---------|
| `h` | `host` |
| `a` | `app` |
| `f` | `file` |
| `t` | `tunnel` |
| `r` | `run` |


---

# Enforcement Summary


- `navig gateway test ... --strict` returns non-zero when any tested channel fails (CI-friendly).
- `navig gateway test ... --json` prints machine-readable results for automation.
- Default `navig gateway test ...` remains human-friendly and compatibility-first.

## BEFORE Remote Operations
- Unknown `navig import --source` values are rejected with explicit errors.
- `--path` must point to an existing file/directory.
- `--path` cannot be used with `--source all`.
1. ✅ Verify host context with `navig host show` or `navig status`
2. ✅ Test connectivity with `navig host test` if uncertain
3. ✅ Use `--b64` for complex commands with special characters

## DURING Execution
1. ✅ Use appropriate NAVIG command (not raw SSH)
2. ✅ Limit output (use `--tail`, `-n` flags for logs)
3. ✅ Prefer `--json` / `--raw`; use `--plain` when supported by the subcommand

## ON ERRORS
1. ✅ Run diagnostics (`navig host test`, `navig config validate`)
2. ✅ Provide actionable fix suggestions
3. ✅ Check debug log: `~/.navig/debug.log`

## FORBIDDEN Actions
- ❌ Raw SSH commands bypassing NAVIG
- ❌ Hardcoded credentials in commands
- ❌ `navig run` with JSON without `--b64`
- ❌ Unlimited log output (always limit with `-n`)
- ❌ Destructive operations without `--dry-run` preview

---


## Operational Factory (Draft + Approval) Commands

When user asks for autonomous ops with safety gates, use the Operational Factory stack in:

`deploy/operational-factory/`

### Lifecycle

```bash
cd deploy/operational-factory
cp .env.example .env
./scripts.sh start
./scripts.sh status
./scripts.sh logs
./scripts.sh stop
```

### Ubuntu Normal Installer (systemd)

```bash
cd navig-core
sudo bash scripts/install_navig_factory_server.sh
sudo systemctl status navig-factory
```

### Cross-Platform CLI Installers

Use these from `navig-core/`:

```bash
# Linux
bash scripts/install_navig_linux.sh

# macOS
bash scripts/install_navig_macos.sh
```

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\scripts\install_navig_windows.ps1
```

### Installer Token Automation (MANDATORY)

- Installers must support `NAVIG_TELEGRAM_BOT_TOKEN` (or `TELEGRAM_BOT_TOKEN`) for automatic Telegram setup.
- During install, write token config to `~/.navig/.env` and configure bot settings if missing.
- After token setup, installers should attempt daemon start (`navig service install ...` then `navig service start`) so Telegram commands are ready immediately.

### Approval-First Rule (MANDATORY)

- SAFE actions can execute automatically through `tool-gateway`
- RESTRICTED actions MUST only be queued in `proposed_actions`
- Human approval is required via dashboard before execution state changes

### Required Audit Coverage

Every tool call must write an audit row with:
- actor (agent/human)
- action
- reason
- sanitized input/output
- status + timestamp

### Demo Flow Triggers

```bash
curl -X POST http://127.0.0.1:8091/flow/email/intake -H 'content-type: application/json' -d '{"limit":10}'
curl -X POST http://127.0.0.1:8091/flow/repo/propose -H 'content-type: application/json' -d '{}'
curl -X POST http://127.0.0.1:8091/flow/briefing/daily
```
