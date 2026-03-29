# 🤖 NAVIG Telegram Bot Guide

Command your infrastructure from anywhere using natural language.

## Overview

The NAVIG Telegram bot connects your server infrastructure to your mobile device, allowing you to:
- Monitor server health and resources
- Execute commands and manage Docker containers
- Receive alerts and notifications
- Maintain context-aware conversations (per-user sessions)

## Setup

### 1. Create a Bot
1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Follow instructions to get your **Bot Token**

### 2. Configure NAVIG
Run the interactive wizard:
```bash
navig init
```
Token resolution is vault-first at runtime, with compatibility fallback to
`TELEGRAM_BOT_TOKEN` and `telegram.bot_token` config when vault is unavailable.
Or edit `~/.navig/config.yaml`:
```yaml
telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN}"
  allowed_users: [123456789]      # Your User ID
  allowed_groups: [-100123456789] # Optional Group IDs
  session_isolation: true
  group_activation_mode: "mention" # mention | always | admin_only
```

### 3. Start the Bot
```bash
# Start gateway and bot in background
navig start

# Or standalone
navig bot
```

## Features

### Session Isolation & Context
The bot maintains separate conversation history for each user.
- **Direct Messages**: Always private context.
- **Groups**: By default, the bot only responds when @mentioned.
- **Context**: Remembers previous questions for follow-ups.

**Example:**
> User 1: "Check disk space on prod"
> Bot: "Prod has 50GB free."
> User 1: "What about memory?" (Bot knows context is 'prod')
> Bot: "Prod memory usage is 60%."

### Group Chat Modes
Configure `group_activation_mode` in `config.yaml`:
- `mention` (Default): Responds only when @mentioned or replied to.
- `always`: Responds to every message (noisy!).
- `admin_only`: Responds only to users in `allowed_users`.

### Supported Commands

| Command | Description |
|---------|-------------|
| `/start` | Wake up the bot |
| `/help` | Show available commands |
| `/hosts` | List configured servers |
| `/use <host>` | Switch active host context |
| `/status` | Check system health |
| `/docker` | List running containers |
| `/logs <name>` | Fetch container logs |
| `/restart <name>` | Restart a container |

### Management CLI

Manage the bot sessions from your terminal:

```bash
# List active conversations
navig telegram sessions list

# View details/history
navig telegram sessions show telegram:user:123456

# Clear history (if AI gets confused)
navig telegram sessions clear telegram:user:123456

# Prune old sessions (>7 days inactive)
navig telegram sessions prune
```
