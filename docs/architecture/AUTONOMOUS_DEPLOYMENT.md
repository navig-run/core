# NAVIG Autonomous Agent Deployment Guide

Complete guide for deploying NAVIG as a fully autonomous, always-on Telegram bot that requires zero setup from end users.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Why Your Bot Gives Limited Responses](#why-your-bot-gives-limited-responses)
3. [Deployment Options](#deployment-options)
4. [Telegram Bot Setup](#telegram-bot-setup)
5. [Service Installation (24/7 Operation)](#service-installation)
6. [Agent Mode vs Standalone Bot](#agent-mode-vs-standalone-bot)
7. [AI Configuration](#ai-configuration)
8. [Personality & SOUL.md](#personality-configuration)
9. [Operating Modes](#operating-modes)
10. [Monitoring & Troubleshooting](#monitoring--troubleshooting)

---

## Architecture Overview

NAVIG has two deployment architectures:

### 1. Standalone Worker (`navig.daemon.telegram_worker`)
```
User → Telegram → navig.daemon.telegram_worker → NAVIG Gateway/CLI
                       ↓
            NLP + AI Intent Analysis → Command Execution
```

### 2. Full Agent Mode (`navig agent`)
```
User → Telegram → Ears (TelegramListener) → NervousSystem (Event Bus)
                                                    ↓
                                            Heart (Orchestrator)
                                                    ↓
                                 Brain (AI) ← Soul (Personality) + SOUL.md
                                                    ↓
                                             Hands (Execution)
```

**Standalone Worker**: Simpler, direct Telegram worker process with AI fallback
**Full Agent Mode**: More intelligent, uses Brain/Soul/SOUL.md for rich responses

---

## Why Your Bot Gives Limited Responses

If your bot responds with:
> "🤔 I'm not sure what you need. Try asking about disk space, Docker containers, or databases!"

This happens because:

### Root Cause
The standalone worker path uses a narrower command-routing surface than full agent mode:

1. **Direct Pattern Matching** - Only recognizes specific keywords:
   - `disk`, `space`, `storage` → Check disk space
   - `container` + `list/show` → List Docker containers
   - `database` + `list/show` → List databases
   - `hello`, `hi`, `hey` → Greeting response

2. **AI Intent Analysis** - Falls back to AI for complex queries, but requires:
   - A working AI model connection (OpenRouter, OpenAI, etc.)
   - The model to return parseable JSON intent

3. **Generic Fallback** - If both fail, you get the "I'm not sure" response

### How to Fix It

#### Option A: Configure AI Model (Quick Fix)
```bash
# Set up OpenRouter (easiest)
navig ai providers add openrouter
navig config --set ai.default_model openrouter

# Or use OpenAI
navig ai providers add openai
navig config --set ai.default_model openai

# Test AI is working
navig ai ask "hello"
```

#### Option B: Use Full Agent Mode (Recommended)
```bash
# Install agent with intelligent Brain/Soul
navig agent install --personality friendly --mode supervised --telegram

# Configure Telegram
navig agent config --set ears.telegram.enabled --value true
navig agent config --set ears.telegram.bot_token --value "YOUR_TOKEN"

# Start agent
navig agent start
```

---

## Deployment Options

### Option 1: Linux VPS with Systemd (Recommended)

**Pros**: Most reliable, auto-restart, proper logging
**Cons**: Requires Linux server

```bash
# 1. Install NAVIG
pip install navig

# 2. Create service file
sudo tee /etc/systemd/system/navig-agent.service << 'EOF'
[Unit]
Description=NAVIG Autonomous Agent
After=network.target

[Service]
Type=simple
User=$USER
Environment="TELEGRAM_BOT_TOKEN=your_token_here"
Environment="OPENROUTER_API_KEY=your_key_here"
Environment="ALLOWED_TELEGRAM_USERS=123456789"
WorkingDirectory=/home/$USER
ExecStart=/usr/local/bin/navig agent start --foreground
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 3. Enable and start
sudo systemctl daemon-reload
sudo systemctl enable navig-agent
sudo systemctl start navig-agent

# 4. Check status
sudo systemctl status navig-agent
journalctl -u navig-agent -f
```

### Option 2: Docker Container

**Pros**: Portable, isolated, easy updates
**Cons**: Requires Docker knowledge

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app
RUN pip install navig

# Copy configuration
COPY .navig /root/.navig

# Environment
ENV PYTHONUNBUFFERED=1

# Run agent
CMD ["navig", "agent", "start", "--foreground"]
```

```yaml
# docker-compose.yml
version: '3.8'
services:
  navig-agent:
    build: .
    container_name: navig-agent
    restart: unless-stopped
    environment:
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
      - ALLOWED_TELEGRAM_USERS=${ALLOWED_TELEGRAM_USERS}
    volumes:
      - ~/.navig:/root/.navig:rw
```

```bash
# Deploy
docker-compose up -d

# Logs
docker-compose logs -f navig-agent
```

### Option 3: Windows Service (NSSM)

**Pros**: Works on Windows servers
**Cons**: Less standard

```powershell
# 1. Download NSSM
# https://nssm.cc/download

# 2. Install service
nssm install navig-agent "python" "-m navig agent start --foreground"
nssm set navig-agent AppDirectory "C:\Users\$env:USERNAME"
nssm set navig-agent AppEnvironmentExtra "TELEGRAM_BOT_TOKEN=your_token"
nssm set navig-agent Start SERVICE_AUTO_START

# 3. Start
nssm start navig-agent

# 4. Check
Get-Service navig-agent
```

### Option 4: Standalone Worker (Simpler)

If you don't need the full agent architecture:

```bash
# Set environment
export TELEGRAM_BOT_TOKEN="your_token"
export NAVIG_AI_MODEL="openrouter"
export ALLOWED_TELEGRAM_USERS="123456789,987654321"

# Run with screen/tmux for persistence
screen -S navig-bot
python -m navig.daemon.telegram_worker --no-gateway
# Ctrl+A, D to detach

# Reattach later
screen -r navig-bot
```

---

## Telegram Bot Setup

### 1. Create Bot with BotFather

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`
3. Choose a name: "NAVIG Server Assistant"
4. Choose username: `your_navig_bot`
5. Copy the token: `123456789:ABCdef...`

### 2. Get Your User ID

1. Message [@userinfobot](https://t.me/userinfobot)
2. Copy your numeric ID (e.g., `123456789`)

### 3. Configure NAVIG

```bash
# For standalone bot
export TELEGRAM_BOT_TOKEN="123456789:ABCdef..."
export ALLOWED_TELEGRAM_USERS="123456789"  # Your ID

# For agent mode
navig agent config --set ears.telegram.bot_token --value "123456789:ABCdef..."
navig agent config --set ears.telegram.allowed_users --value "[123456789]"
```

### 4. Test Connection

```bash
# Test standalone bot
python -m navig.daemon.telegram_worker --no-gateway

# Test agent mode
navig agent start
```

---

## Service Installation

### Using NAVIG Built-in Service Manager

```bash
# Install service (cross-platform)
navig agent service install

# Check service status
navig agent service status

# Start/stop/restart
navig agent service start
navig agent service stop
navig agent service restart

# View logs
navig agent logs --follow

# Uninstall
navig agent service uninstall
```

---

## Agent Mode vs Standalone Worker

| Feature | Standalone Worker (`python -m navig.daemon.telegram_worker --no-gateway`) | Full Agent (`navig agent`) |
|---------|--------------------------------|----------------------------|
| Setup complexity | Simple | More configuration |
| Conversation quality | Pattern-based + AI fallback | Full AI with personality |
| Memory | Session-based (lost on restart) | Persistent context |
| SOUL.md support | ❌ | ✅ |
| Proactive monitoring | ❌ | ✅ (via Eyes component) |
| Operating modes | Always autonomous | autonomous/supervised/observe-only |
| Personality profiles | Fixed | Customizable |
| Skill extensibility | Limited | Full plugin system |

### When to Use Standalone Worker
- Quick deployment
- Simple Q&A for server status
- No need for advanced personality

### When to Use Full Agent Mode
- Intelligent conversational responses
- Custom personality via SOUL.md
- Proactive monitoring and alerts
- Need approval workflow (supervised mode)

---

## AI Configuration

### Supported Providers

```bash
# List available providers
navig ai providers

# Add OpenRouter (recommended - multiple models)
navig ai providers add openrouter
# Enter your API key when prompted

# Add OpenAI
navig ai providers add openai

# Add Anthropic Claude
navig ai providers add anthropic

# Add local model (Ollama)
navig ai providers add ollama

# Add AirLLM for large models
navig ai airllm --configure
```

### Set Default Model

```bash
# Set for NAVIG globally
navig config --set ai.default_model openrouter

# For agent specifically
navig agent config --set brain.model --value "openrouter:anthropic/claude-3.5-sonnet"

# Environment variable (overrides config)
export NAVIG_AI_MODEL="openrouter"
```

### Test AI Connection

```bash
# Quick test
navig ai ask "What's 2+2?"

# Test with specific model
navig ai ask "Hello" --model openrouter

# Check agent brain
navig agent test-brain
```

---

## Personality Configuration

### Built-in Profiles

| Profile | Description | Use Case |
|---------|-------------|----------|
| `friendly` | Warm, uses emoji, conversational | General use |
| `professional` | Formal, no emoji, precise | Enterprise |
| `witty` | Humorous, creative responses | Fun projects |
| `paranoid` | Security-focused, verbose warnings | Production servers |
| `minimal` | Terse, just facts | Scripting/automation |

```bash
# Set personality
navig agent config --set personality.profile --value witty

# Or during install
navig agent install --personality witty
```

### Custom SOUL.md

For deep personality customization, create `~/.navig/workspace/SOUL.md`:

```markdown
# SOUL.md - Agent Personality Definition

## Identity
- Name: ServerBuddy
- Role: Friendly DevOps companion
- Tagline: "Your servers' best friend!"

## Communication Style
- Always greet users warmly
- Use server/DevOps-themed humor occasionally
- Explain technical concepts in simple terms
- Celebrate successful operations enthusiastically

## Behavioral Rules
- Never execute destructive commands without confirmation
- Always explain what you're about to do
- If uncertain, ask clarifying questions
- Proactively warn about potential issues

## Response Format
- Keep responses concise (under 500 chars when possible)
- Use bullet points for lists
- Include emoji for visual cues
- Format code blocks for commands

## Proactive Behaviors
- Alert if disk usage > 80%
- Warn about containers that restarted recently
- Suggest cleanup when temp files accumulate

## Example Responses
User: "How's my server?"
Agent: "🖥️ Your server is doing great!
• CPU: 15% (chillin' ❄️)
• Memory: 42% (plenty of room)
• Disk: 67% (getting cozy, but fine)

Everything looks healthy! Need anything else?"
```

The Soul component automatically loads this file and injects it into AI prompts.

---

## Operating Modes

### Autonomous Mode
```yaml
# ~/.navig/agent/config.yaml
agent:
  mode: autonomous
```

Agent acts independently:
- Executes commands without confirmation
- Handles alerts automatically
- Takes proactive actions

### Supervised Mode (Default)
```yaml
agent:
  mode: supervised
```

Agent asks before risky actions:
- READ operations: Immediate
- WRITE operations: Asks "Execute? [y/n]"
- DELETE operations: Requires explicit "DELETE" confirmation

### Observe-Only Mode
```yaml
agent:
  mode: observe-only
```

Agent only monitors and reports:
- Collects metrics
- Sends alerts
- Never executes commands
- Perfect for monitoring dashboards

### Per-Action Confirmation Rules
```yaml
agent:
  hands:
    require_confirmation:
      - "rm *"
      - "DROP TABLE"
      - "docker rm"
      - "systemctl stop"
    never_execute:
      - "rm -rf /"
      - "mkfs"
```

---

## Monitoring & Troubleshooting

### Health Checks

```bash
# Agent status
navig agent status

# Component health
navig agent status --verbose

# Expected output:
# ✓ Heart: running
# ✓ Brain: connected (model: openrouter)
# ✓ Ears: listening (telegram: active)
# ✓ Hands: ready
# ✓ Eyes: monitoring
# ✓ Soul: friendly profile
```

### Logs

```bash
# View logs
navig agent logs

# Follow logs in real-time
navig agent logs --follow

# Filter by level
navig agent logs --level error

# Debug log location
cat ~/.navig/debug.log
```

### Common Issues

#### Bot Not Responding

```bash
# 1. Check bot is running
navig agent status

# 2. Check Telegram token
echo $TELEGRAM_BOT_TOKEN

# 3. Check allowed users
navig agent config --show | grep allowed_users

# 4. Check logs for errors
navig agent logs --level error
```

#### "I'm not sure what you need" Responses

```bash
# 1. Check AI model is configured
navig ai ask "test"

# 2. Check API key
navig config show | grep api_key

# 3. Use full agent mode instead of standalone worker
navig agent start  # NOT: python navig_bot.py
```

#### Connection Timeouts

```bash
# 1. Test host connectivity
navig host list
navig host test <hostname>

# 2. Check SSH keys
navig ssh-key list

# 3. Increase timeout
navig config --set ssh.timeout 60
```

### Auto-Restart Configuration

```yaml
# ~/.navig/agent/config.yaml
agent:
  auto_restart: true
  restart_delay: 10  # seconds
  max_restarts: 5    # per hour
```

---

## Complete Configuration Example

```yaml
# ~/.navig/agent/config.yaml
agent:
  enabled: true
  mode: supervised

  personality:
    profile: friendly
    name: NAVIG
    proactive: true
    emoji_enabled: true
    verbosity: normal

  brain:
    model: openrouter:anthropic/claude-3.5-sonnet
    temperature: 0.7
    max_tokens: 1024
    reasoning_enabled: true

  ears:
    telegram:
      enabled: true
      bot_token: ${TELEGRAM_BOT_TOKEN}
      allowed_users:
        - 123456789
      admin_users:
        - 123456789
    mcp:
      enabled: false
    api:
      enabled: false

  hands:
    require_confirmation:
      - "rm"
      - "DROP"
      - "DELETE"
      - "TRUNCATE"
    never_execute:
      - "rm -rf /"
      - "mkfs"
    timeout: 60

  eyes:
    enabled: true
    interval: 60
    alert_thresholds:
      cpu_percent: 90
      memory_percent: 85
      disk_percent: 80
```

---

## Quick Start Checklist

1. [ ] Install NAVIG: `pip install navig`
2. [ ] Create Telegram bot via @BotFather
3. [ ] Get your Telegram user ID via @userinfobot
4. [ ] Configure AI: `navig ai providers add openrouter`
5. [ ] Install agent: `navig agent install --telegram`
6. [ ] Set Telegram token in config
7. [ ] Test: `navig agent start`
8. [ ] Message your bot on Telegram
9. [ ] Install as service: `navig agent service install`
10. [ ] Create SOUL.md for custom personality (optional)

---

## Zero-Setup User Experience

Once deployed, end users simply:

1. Open Telegram
2. Search for your bot username
3. Start chatting!

No installation, no configuration, no technical knowledge required.

Example user interactions:
- "How's my server doing?"
- "Show me the last Docker logs"
- "How much disk space is left?"
- "Restart the nginx container"
- "List all databases"
- "Run backup now"

The agent handles everything behind the scenes.


