# NAVIG Common Workflows

Recipes for the most frequent operations. Each workflow shows the exact
commands to run in order. Use `--dry-run` where supported to preview actions
before executing.

---

## Workflow 1: Database Backup

Back up a remote MySQL/MariaDB/PostgreSQL database to a local file.

```bash
# 1. Confirm the active host
navig host show

# 2. List available databases (so you know the exact name)
navig db list --plain

# 3. Dump the database to a timestamped local file
navig db dump myapp_db -o backup-$(date +%Y%m%d).sql

# 4. Verify the file was created and check its size
ls -lh backup-*.sql
```

**Windows PowerShell variant:**

```powershell
navig host show
navig db list --plain

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
navig db dump myapp_db -o "backup-$ts.sql"

Get-ChildItem backup-*.sql | Select-Object Name, Length
```

**Compress after dumping (all platforms):**

```bash
gzip backup-20240101.sql           # creates backup-20240101.sql.gz
```

---

## Workflow 2: Deploy a Flow (Automated Deployment)

Run a named deployment workflow with preview first.

```bash
# 1. List available flows to confirm the name
navig flow list

# 2. Preview what the flow will do (dry run)
navig flow run deploy-production --dry-run

# 3. Execute the deployment
navig flow run deploy-production

# 4. Check the result in the command history
navig history list --limit 5
```

---

## Workflow 3: Bulk File Transfer

Upload a directory to a remote server and verify the result.

```bash
# 1. Upload entire directory
navig file add ./build/ /var/www/myapp/

# 2. Verify remote structure
navig file list /var/www/myapp/ --tree --depth 2

# 3. Check permissions on the new files
navig run "ls -la /var/www/myapp/"

# 4. Fix permissions if needed (example: set 644 on PHP files)
navig run "find /var/www/myapp/ -name '*.php' -exec chmod 644 {} \\;"
```

---

## Workflow 4: Server Health Check

Quick health assessment before making server changes.

```bash
# 1. Full health overview
navig host monitor show

# 2. Drill down into specific areas if problems found
navig host monitor show --disk          # disk space details
navig host monitor show --resources     # CPU / RAM / load
navig host monitor show --services      # service status
navig host security show --firewall     # firewall rules

# 3. View recent logs if any service is failing
navig file show /var/log/syslog --tail --lines 50

# 4. Check running containers (if Docker is in use)
navig docker ps -a
```

---

## Workflow 5: Telegram Bot Setup

Configure the NAVIG Telegram bot so you can run operations from your phone.

### Step 1 — Create a bot via BotFather

1. Open Telegram → search **@BotFather** → `/newbot`
2. Follow the prompts and copy the **API token** (e.g. `7412…:AAF…`)

### Step 2 — Configure NAVIG

```bash
# Interactive wizard (recommended for first-time setup)
navig init

# Or set the token directly in the config
navig config set telegram.bot_token "<your-token>"
navig config set telegram.chat_id   "<your-chat-id>"
```

To find your `chat_id`, message **@userinfobot** in Telegram.

### Step 3 — Start the bot

```bash
# Start gateway + bot in background
navig start

# Verify bot is running
navig status

# Check bot service logs
navig file show ~/.navig/logs/gateway.log --tail --lines 30
```

### Step 4 — Test from Telegram

Send `/status` to the bot. You should see the active host and connection
state.

---

## Workflow 6: Restore a Database Backup

Restore a previously dumped SQL backup to a remote database.

```bash
# 1. Confirm host and target database name
navig host show
navig db list --plain

# 2. (Optional) Create target if it doesn't exist yet
navig db run "CREATE DATABASE myapp_restored;" -d mysql

# 3. Restore
navig db restore myapp_restored backup-20240101.sql

# 4. Spot-check with a quick count
navig db run "SELECT COUNT(*) FROM users;" -d myapp_restored
```

---

## Workflow 7: SSH Key Rotation

Rotate SSH keys for the active host.

```bash
# 1. Generate a new key pair locally (if needed)
ssh-keygen -t ed25519 -C "navig-$(date +%Y%m)" -f ~/.ssh/id_ed25519_new

# 2. Upload the new public key to the server
navig run "mkdir -p ~/.ssh && echo '$(cat ~/.ssh/id_ed25519_new.pub)' >> ~/.ssh/authorized_keys"

# 3. Update the host config to use the new key
navig host edit myserver   # set key_file to the new key path

# 4. Test the new key
navig host test

# 5. (After confirming it works) revoke the old key
navig run "grep -v '<old-key-fingerprint>' ~/.ssh/authorized_keys > /tmp/ak && mv /tmp/ak ~/.ssh/authorized_keys"
```

---

## Tips for All Workflows

| Need | Solution |
|------|----------|
| Override host for one command | `navig --host staging run "..."` |
| Suppress confirmation prompts | Add `--yes` / `-y` |
| Machine-readable output | Add `--plain` or `--json` |
| Preview without executing | Add `--dry-run` (where supported) |
| Complex commands (Windows) | Use `--b64` (see `navig help run`) |
| Check what just ran | `navig history list --limit 5` |
| Get help on any topic | `navig help <topic>` |
