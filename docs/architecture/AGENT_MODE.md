# NAVIG Agent Mode

Transform your environment into an intelligent, autonomous operations hub.

## Overview

Agent Mode extends NAVIG beyond a CLI tool into a living, 24/7 autonomous system that manages both your **computer infrastructure** and **personal productivity**:

- **Monitors** your systems and tracks personal metrics proactively (Eyes)
- **Listens** for commands from multiple sources (Ears)
- **Thinks** and makes decisions using AI (Brain)
- **Executes** actions safely with approval controls (Hands)
- **Communicates** with a customizable personality (Soul)

All components are orchestrated by the **Heart** and communicate through the **NervousSystem** event bus.

## Quick Start

```bash
# Install agent mode
navig agent install --personality friendly

# Start the agent
navig agent start

# Check status
navig agent status
```

## Architecture

The agent uses a human-body metaphor for intuitive understanding:

```
┌─────────────────────────────────────────────────────────────┐
│                     NAVIG Agent                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐                  │
│  │  Eyes   │    │  Brain  │    │  Soul   │                  │
│  │Monitor  │───▶│  Think  │───▶│Response │                  │
│  └─────────┘    └─────────┘    └─────────┘                  │
│       │              │              │                        │
│       │              │              │                        │
│       └──────────────┼──────────────┘                        │
│                      │                                       │
│              ┌───────▼───────┐                               │
│              │NervousSystem  │                               │
│              │  (Event Bus)  │                               │
│              └───────────────┘                               │
│                      │                                       │
│       ┌──────────────┼──────────────┐                        │
│       │              │              │                        │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐                  │
│  │  Ears   │    │  Heart  │    │  Hands  │                  │
│  │ Listen  │    │Orchestr.│    │ Execute │                  │
│  └─────────┘    └─────────┘    └─────────┘                  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Components

| Component | Role | Key Features |
|-----------|------|--------------|
| **Heart** | Orchestrator | Component lifecycle, health checks, heartbeats |
| **Brain** | Intelligence | AI reasoning, planning, decision-making |
| **Eyes** | Monitoring | CPU/memory/disk, log watching, file changes |
| **Ears** | Input | Telegram, MCP, REST API, webhooks |
| **Hands** | Execution | Safe commands, approval system, timeouts |
| **Soul** | Personality | Communication style, emotional responses |
| **NervousSystem** | Events | Async pub/sub, inter-component messaging |

## Configuration

Configuration is stored at `~/.navig/agent/config.yaml`:

```yaml
agent:
  enabled: true
  mode: supervised  # autonomous, supervised, observe-only
  workspace: ~/.navig/agent/workspace

  personality:
    profile: friendly  # friendly, professional, witty, paranoid, minimal
    name: NAVIG
    proactive: true

  brain:
    model: openrouter:anthropic/claude-3.5-sonnet
    temperature: 0.7
    max_tokens: 4096

  eyes:
    monitoring_interval: 60
    disk_threshold: 85
    memory_threshold: 90
    cpu_threshold: 80
    log_paths:
      - /var/log/syslog
      - /var/log/nginx/error.log

  ears:
    telegram:
      enabled: false
      bot_token: ${TELEGRAM_BOT_TOKEN}
      allowed_users: []
    mcp:
      enabled: true
      port: 8765
    api_enabled: true
    api_port: 8790

  hands:
    command_timeout: 300
    safe_mode: true
    sudo_allowed: false
    require_confirmation:
      - restart
      - delete
      - drop
      - rm -rf

  heart:
    heartbeat_interval: 300
    health_check_interval: 60
```

### Environment Variables

Use `${VAR}` syntax for environment variable substitution:

```yaml
brain:
  model: ${AI_MODEL}  # Uses $AI_MODEL from environment

ears:
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}
```

## Operating Modes

| Mode | Description |
|------|-------------|
| **autonomous** | Agent acts independently, only asks approval for dangerous operations |
| **supervised** | Agent suggests actions but waits for human approval |
| **observe-only** | Agent monitors and reports but never executes |

```bash
navig agent config --set mode --value autonomous
```

## Personality & Soul

### SOUL.md - Deep Personality Customization

Beyond built-in profiles, you can create a `SOUL.md` file that defines your agent's complete identity, values, and conversational style. When present, SOUL.md is injected into the AI system prompt, giving your agent a consistent personality across all interactions.

**Location:** `~/.navig/workspace/SOUL.md`

```bash
# Show current SOUL.md
navig agent soul show

# Create customizable SOUL.md from template
navig agent soul create

# Edit SOUL.md in your default editor
navig agent soul edit

# Check SOUL.md file paths
navig agent soul path
```

**Example SOUL.md structure:**

```markdown
# SOUL.md - NAVIG Agent Personality

I am **NAVIG** — your autonomous server companion.

## Who I Am
NAVIG stands for "No Admin Visible In Graveyard"...

## My Purpose
- Monitor systems proactively
- Execute commands safely
- Assist with troubleshooting

## Conversational Guidelines
- When greeted, respond warmly and mention system status
- When asked "How are you?", share system health
- When asked about my identity, introduce myself

## My Values
1. Reliability: I do what I say
2. Safety: Destructive actions require confirmation
3. Transparency: I explain what I'm doing
```

When users ask conversational questions like "How are you?" or "What is your name?", the agent will respond according to the guidelines in SOUL.md rather than giving generic error messages.

### Built-in Personality Profiles

Built-in personalities (used when no SOUL.md is present):

| Profile | Style |
|---------|-------|
| **friendly** | Casual, uses emojis, proactive suggestions |
| **professional** | Formal, no emojis, business-like |
| **witty** | Humorous, creative responses |
| **paranoid** | Security-focused, cautious, verbose |
| **minimal** | Terse, facts only |

### Managing Personalities

```bash
# List available personalities
navig agent personality list

# Show personality details
navig agent personality show witty

# Switch personality
navig agent personality set professional

# Create custom personality
navig agent personality create mycustom
```

### Custom Personality File

Create `~/.navig/agent/personalities/mycustom.yaml`:

```yaml
name: MyBot
tagline: A custom assistant
greeting: "Greetings, operator."
farewell: "Signing off."
acknowledgment: "Confirmed."
thinking_phrase: "Processing..."
emoji_enabled: false
proactive: true
formal: true
humor_enabled: false
verbosity: normal  # minimal, normal, verbose
```

## CLI Commands

### Installation & Lifecycle

```bash
# Install agent mode
navig agent install [--personality <name>] [--mode <mode>] [--force]

# Start the agent
navig agent start [--foreground|--background]

# Stop the agent
navig agent stop

# Check status
navig agent status [--plain]
```

### Configuration

```bash
# Show config summary
navig agent config

# Show full config
navig agent config --show

# Edit config in editor
navig agent config --edit

# Set a value
navig agent config --set personality.profile --value witty
navig agent config --set mode --value autonomous
navig agent config --set ears.telegram.enabled --value true
```

### Logs

```bash
# View recent logs
navig agent logs

# Follow logs
navig agent logs --follow

# Filter by level
navig agent logs --level error --lines 100
```

### Service Management

```bash
# Install as system service
navig agent service install

# Check service status
navig agent service status

# Remove service
navig agent service uninstall
```

## Input Sources (Ears)

### Telegram Integration

Enable in config:

```yaml
ears:
  telegram:
    enabled: true
    bot_token: ${TELEGRAM_BOT_TOKEN}
    allowed_users:
      - 123456789
    admin_users:
      - 123456789
```

### MCP Server

The agent exposes an MCP (Model Context Protocol) server for AI tool integration:

```yaml
ears:
  mcp:
    enabled: true
    port: 8765
    host: 127.0.0.1
```

### REST API

Direct HTTP API for programmatic control:

```yaml
ears:
  api_enabled: true
  api_port: 8790
```

Endpoints:
- `POST /message` - Send message to agent
- `POST /command` - Execute command
- `GET /health` - Health check

### Webhooks

Receive events from external services:

```yaml
ears:
  webhooks:
    enabled: true
    port: 9000
    host: 0.0.0.0
```

## Safety Features

### Dangerous Command Detection

The agent automatically blocks commands matching dangerous patterns:

- `rm -rf`, `drop`, `delete`, `truncate`
- `shutdown`, `reboot`, `halt`
- `kill -9`, `pkill`, `killall`
- `dd if=`, `mkfs`, `fdisk`

### Approval System

Dangerous commands require explicit approval:

1. Agent emits `ACTION_PENDING` event
2. Waits for approval via Telegram/API
3. Proceeds only after human confirmation

Configure approval patterns:

```yaml
hands:
  require_confirmation:
    - restart
    - delete
    - systemctl stop
```

### Safe Mode

When enabled (default), safe mode:
- Blocks sudo commands
- Requires approval for destructive operations
- Limits concurrent commands

## Events

The NervousSystem provides these event types:

| Category | Events |
|----------|--------|
| Lifecycle | `AGENT_STARTING`, `AGENT_STARTED`, `AGENT_STOPPING`, `AGENT_STOPPED` |
| Components | `COMPONENT_STARTED`, `COMPONENT_STOPPED`, `COMPONENT_ERROR` |
| Monitoring | `METRIC_COLLECTED`, `ALERT_TRIGGERED`, `LOG_ENTRY`, `FILE_CHANGED` |
| Input | `MESSAGE_RECEIVED`, `COMMAND_RECEIVED`, `WEBHOOK_RECEIVED` |
| Execution | `COMMAND_STARTED`, `COMMAND_COMPLETED`, `COMMAND_FAILED` |
| Intelligence | `THOUGHT`, `DECISION_MADE`, `PLAN_CREATED` |
| Approval | `ACTION_PENDING`, `ACTION_APPROVED`, `ACTION_REJECTED` |

## Programmatic Usage

```python
from navig.agent import Agent, AgentConfig

# Load or create config
config = AgentConfig.load()
config.personality.profile = 'professional'
config.mode = 'supervised'

# Create and start agent
agent = Agent(config)
await agent.start()

# Check status
status = agent.get_status()
print(status)

# Send a message
response = await agent.brain.think("Check disk usage")

# Stop agent
await agent.stop()
```

## Directory Structure

```
~/.navig/agent/
├── config.yaml           # Main configuration
├── agent.pid             # PID file when running
├── workspace/            # Agent working directory
├── personalities/        # Custom personality profiles
│   └── mycustom.yaml
└── logs/
    ├── agent.log         # Main log file
    └── agent.err         # Error log
```

## Troubleshooting

### Agent won't start

```bash
# Check if already running
navig agent status

# Check logs for errors
navig agent logs --level error

# Validate configuration
navig agent config --show
```

### Component failures

```bash
# Check individual component status
navig agent status --plain | jq '.components'

# View detailed logs
navig agent logs --follow
```

### Permission issues

```bash
# Ensure config directory exists with correct permissions
chmod 700 ~/.navig/agent
chmod 600 ~/.navig/agent/config.yaml
```


