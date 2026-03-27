---
name: systemd-services
description: Manage systemd services on Linux servers - start, stop, restart, enable, check logs
user-invocable: true
os: [linux]
navig-commands:
  - navig run "systemctl status {service}"
  - navig run "systemctl start {service}"
  - navig run "systemctl stop {service}"
  - navig run "systemctl restart {service}"
  - navig run "systemctl enable {service}"
  - navig run "journalctl -u {service} --no-pager -n 50"
examples:
  - "Is nginx running?"
  - "Restart the MySQL service"
  - "Show me service logs"
  - "What services are failed?"
  - "Enable the service to start on boot"
---

# Systemd Service Management

Manage services on Linux servers using systemd — start, stop, restart, check status, view logs.

## Common Tasks

### Check Service Status

**User says:** "Is nginx running?" / "Check MySQL status"

```bash
navig run "systemctl status {service} --no-pager"
```

**Response format:**
```
🔧 Service: nginx

Status: ✅ Active (running)
PID: 1234
Uptime: 15 days
Memory: 32MB

Last log entries:
[OK] Started Nginx web server
```

### Start a Service

```bash
navig run "systemctl start {service}"
```

### Stop a Service

⚠️ Confirm first: "This will stop {service}. Continue?"

```bash
navig run "systemctl stop {service}"
```

### Restart a Service

```bash
navig run "systemctl restart {service}"
```

**Response:**
```
🔄 Restarting {service}...
✅ Service restarted successfully!
```

### Reload Configuration (no downtime)

```bash
navig run "systemctl reload {service}"
```

### Enable on Boot

```bash
navig run "systemctl enable {service}"
```

### Disable on Boot

```bash
navig run "systemctl disable {service}"
```

## Diagnostics

### View Service Logs

```bash
navig run "journalctl -u {service} --no-pager -n 50"
```

### View Logs Since Last Boot

```bash
navig run "journalctl -u {service} --no-pager -b"
```

### View Logs from Time Range

```bash
navig run "journalctl -u {service} --since '1 hour ago' --no-pager"
```

### List All Failed Services

**User says:** "Are any services failing?"

```bash
navig run "systemctl --failed --no-pager"
```

**Response format:**
```
🔧 Service Health on {host}:

❌ Failed services:
• php8.2-fpm.service - PHP FastCGI Process Manager
• certbot.timer - Certificate renewal timer

✅ All other services running normally

💡 Fix php-fpm: check `journalctl -u php8.2-fpm -n 30`
```

### List Running Services

```bash
navig run "systemctl list-units --type=service --state=running --no-pager"
```

### Check If Service Exists

```bash
navig run "systemctl list-unit-files | grep {service}"
```

## Common Services

| Service | Package | Default Port |
|---------|---------|-------------|
| `nginx` | nginx | 80, 443 |
| `apache2` | apache2 | 80, 443 |
| `mysql` | mysql-server | 3306 |
| `postgresql` | postgresql | 5432 |
| `redis-server` | redis | 6379 |
| `docker` | docker-ce | - |
| `ssh` / `sshd` | openssh-server | 22 |
| `ufw` | ufw | - |
| `fail2ban` | fail2ban | - |
| `cron` | cron | - |

## Safety Rules

- **Safe**: `status`, `list-units`, `journalctl` (read-only)
- **Confirm**: `start`, `restart`, `reload` (service disruption)
- **Double confirm**: `stop`, `disable` (takes service offline)

## Proactive Suggestions

- **Service crashed**: "💡 {service} has crashed {n} times today. Check logs with `journalctl -u {service}`"
- **Not enabled**: "⚠️ {service} is running but not enabled on boot. Enable it?"
- **High restart count**: "⚠️ {service} has restarted 5 times. Something might be wrong."

## Error Handling

- **Service not found**: "Service '{service}' not found. Did you mean '{suggestion}'?"
- **Permission denied**: "Need sudo. Try: `navig run --sudo 'systemctl restart {service}'`"
- **Already running**: "{service} is already running. Nothing to do."
