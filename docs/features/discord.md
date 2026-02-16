# 🦑 NAVIG Discord Integration

> **Your Kraken in Discord.** NAVIG appears as a distinct persona in your community server—posting alerts, responding to commands, and maintaining recognizable 🦑 identity.

## Architecture Overview

NAVIG uses a **hybrid approach** for Discord integration:

| Component | Purpose | Authentication |
|-----------|---------|----------------|
| **Interactive Bot** | Commands, DMs, request handling | `DISCORD_BOT_TOKEN` |
| **Webhooks** | Announcements, alerts, status updates | Per-channel webhook URLs |

### Why Both?

- **Webhooks**: Simple, no bot permissions needed, perfect for one-way announcements
- **Bot**: Required for interactive commands, DMs, reactions, and bi-directional communication

---

## 🛰️ Channel Structure

### Recommended Channels

| Channel | Purpose | Who Posts | Permissions |
|---------|---------|-----------|-------------|
| `#navig-bridge` | Help & onboarding | Everyone + NAVIG | All can read/post |
| `#navig-ops` | Infrastructure tasks | `@NAVIG-User` + NAVIG | Users can chat, Operators execute |
| `#navig-lifeops` | Personal workflows | `@NAVIG-User` + NAVIG | Users can chat, Operators execute |
| `#navig-alerts` | High-signal alerts only | Webhook only | Read-only for users |
| `#navig-changelog` | Releases, roadmap updates | Webhook only | Read-only for users |

### Roles

| Role | Description | Permissions |
|------|-------------|-------------|
| `@NAVIG-Operator` | Configure integrations, execute privileged commands | Full NAVIG access |
| `@NAVIG-User` | Request actions, query status | Read-only + safe commands |
| `@NAVIG-Alerts` | Subscribe to alert notifications | Mentioned on alerts |

---

## ⚓ Configuration

### Environment Variables (Required)

```bash
# Bot token - from Discord Developer Portal
DISCORD_BOT_TOKEN=your_bot_token_here

# Webhook URLs - from each channel's integrations settings
DISCORD_WEBHOOK_OPS=https://discord.com/api/webhooks/...
DISCORD_WEBHOOK_ALERTS=https://discord.com/api/webhooks/...
DISCORD_WEBHOOK_CHANGELOG=https://discord.com/api/webhooks/...
```

### Config File (`~/.navig/config.yaml`)

```yaml
discord:
  enabled: true
  safe_mode: true  # Require @NAVIG-Operator for write operations
  
  bot:
    token: ${DISCORD_BOT_TOKEN}  # From environment
    prefix: "!"  # Command prefix (e.g., !navig status)
    
  webhooks:
    ops: ${DISCORD_WEBHOOK_OPS}
    alerts: ${DISCORD_WEBHOOK_ALERTS}
    changelog: ${DISCORD_WEBHOOK_CHANGELOG}
    
  channels:
    bridge: "navig-bridge"
    ops: "navig-ops"
    lifeops: "navig-lifeops"
    alerts: "navig-alerts"
    changelog: "navig-changelog"
    
  roles:
    operator: "NAVIG-Operator"
    user: "NAVIG-User"
    alerts: "NAVIG-Alerts"
    
  command_allowlist:  # When safe_mode: true, only these are allowed
    - status
    - health
    - list
    - show
    - query
```

---

## 🛞 Message Style Guide

NAVIG maintains a **consistent voice** across all Discord messages:

### Voice Characteristics

- **Concise**: Short sentences, no fluff
- **Direct**: "Done." not "Successfully completed."
- **Impact-first**: Lead with what happened, not how
- **No hedging**: "Failed" not "Unfortunately encountered an issue"

### Message Templates

#### 🛰️ Alert

```
🛰️ Signal: <issue description>
Impact: <severity/scope>
Action: <recommended fix>
Owner: <responsible party or "auto-resolved">
```

**Example:**
```
🛰️ Signal: Disk usage at 92% on production
Impact: High - services may fail within 24h
Action: Clear old logs or expand volume
Owner: @ops-team
```

#### 🦑 Action Result

```
🦑 Done. <what changed>
⚓ State preserved: <backup/config location>
```

**Example:**
```
🦑 Done. Restarted nginx container.
⚓ State preserved: ~/.navig/backups/nginx-config-20260207.tar.gz
```

#### 🛞 Guidance

```
🛞 Course correction:
1) <step one>
2) <step two>
3) <step three>
```

**Example:**
```
🛞 Course correction:
1) SSH into production: `navig run "bash"`
2) Check logs: `tail -f /var/log/nginx/error.log`
3) Restart if needed: `navig docker restart nginx`
```

#### 🛰️ Status Update

```
🛰️ Status: <component> | Health: <OK/WARN/CRIT> | Uptime: <duration>
```

**Example:**
```
🛰️ Status: Gateway | Health: OK | Uptime: 3d 14h 22m
```

---

## ⚓ Security Model

### Threat Model

NAVIG's Discord integration follows security lessons from the ClawdBot ecosystem:

1. **Third-party extensions are untrusted by default**
2. **Safe mode is ON by default**
3. **Least-privilege bot permissions**
4. **No credentials in logs or messages**

### Safe Mode Behavior

When `safe_mode: true` (default):

| Command Type | Allowed? | Requirements |
|--------------|----------|--------------|
| Read-only (`status`, `list`, `show`) | ✅ Yes | Any `@NAVIG-User` |
| Write operations (`run`, `restart`) | ❌ No | Requires `@NAVIG-Operator` |
| Destructive (`delete`, `drop`) | ❌ No | Operator + explicit confirmation |

### Command Allowlist

When safe mode is enabled, only these commands are allowed:

```yaml
command_allowlist:
  - status    # Show status of hosts/apps
  - health    # Health checks
  - list      # List resources (hosts, apps, containers)
  - show      # Show details of a resource
  - query     # Read-only database queries
```

### Blocked in Safe Mode

These require `@NAVIG-Operator` role or safe_mode disabled:

- `run` - Execute arbitrary commands
- `execute` - Same as run
- `delete` - Remove resources
- `restart` - Restart services
- `backup-restore` - Restore from backup

### Input Validation Requirements

All Discord message content MUST be sanitized before execution:

```python
# Shell commands - use shlex.quote()
import shlex
safe_cmd = shlex.quote(user_input)

# SQL queries - use parameterized queries
# NEVER concatenate user input into SQL strings

# File paths - validate against allowlist
# NEVER allow .. traversal or absolute paths from user input
```

### Credential Protection

**NEVER expose in Discord:**
- Webhook URLs
- Bot tokens
- Database passwords
- SSH keys or passphrases

**Environment Variables Only:**
```bash
# These MUST be in environment, never in config files
DISCORD_BOT_TOKEN=...
DISCORD_WEBHOOK_OPS=...
DISCORD_WEBHOOK_ALERTS=...
```

**Add to `.gitignore`:**
```gitignore
.env*
discord-webhooks.json
*.token
```

---

## Bot Permissions

### Required Permissions (Minimal)

| Permission | Why Needed |
|------------|------------|
| Read Messages | See commands and mentions |
| Send Messages | Respond to commands |
| Embed Links | Format rich responses |
| Add Reactions | Confirmations (✅/❌) |

### NOT Required (Do Not Enable)

| Permission | Risk |
|------------|------|
| Administrator | Never - excessive access |
| Manage Server | Never - can break server |
| Manage Channels | Never - not needed |
| Manage Roles | Never - privilege escalation |
| Kick/Ban Members | Never - not our job |

---

## Implementation Phases

### Phase 1: Documentation & Branding ✅
- Discord plan document (this file)
- Branding consistency (🦑 across codebase)
- Config schema defined

### Phase 2: Webhook Posting
- Implement alert webhook posting
- Implement changelog webhook posting
- Add `navig discord post` command

### Phase 3: Interactive Bot
- Basic command handling
- Safe mode enforcement
- Role-based permissions

### Phase 4: Advanced Features
- Per-agent webhook routing
- Custom slash commands
- Session management via Discord

---

## Quick Reference

### Emoji System

| Emoji | Meaning | Use When |
|-------|---------|----------|
| 🦑 | NAVIG identity | Referring to NAVIG as character/agent |
| 🛰️ | Telemetry/signals | Monitoring, status, alerts |
| 🛞 | Navigation/steering | Commands, guidance, workflows |
| ⚓ | Stability/persistence | Config, backups, safe mode |

### Commands (When Bot Active)

```
!navig status           # Show system status
!navig health           # Run health checks
!navig list hosts       # List configured hosts
!navig show prod        # Show host details
```

### Webhook Posting (Manual)

```bash
# Post to ops channel
navig discord post ops "Deployment complete"

# Post alert
navig discord alert "Disk space critical on production"

# Post changelog entry
navig discord changelog "v2.1.0 released with Discord support"
```


