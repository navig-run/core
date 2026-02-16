# NAVIG Gateway Architecture

The NAVIG Gateway is a persistent HTTP/WebSocket server providing 24/7 operation for the autonomous agent system.

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      NAVIG Gateway                          │
│                    (port 18789)                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ Telegram │  │  HTTP    │  │WebSocket │  │ Webhooks │    │
│  │ Channel  │  │  API     │  │  Clients │  │ Receiver │    │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘    │
│       │             │             │             │          │
│       └─────────────┴─────────────┴─────────────┘          │
│                          │                                  │
│                   ┌──────┴───────┐                         │
│                   │   Channel    │                         │
│                   │   Router     │                         │
│                   └──────┬───────┘                         │
│                          │                                  │
│       ┌──────────────────┼──────────────────┐              │
│       │                  │                  │              │
│  ┌────┴────┐       ┌────┴────┐       ┌────┴────┐          │
│  │ Session │       │   AI    │       │  Event  │          │
│  │ Manager │       │ Router  │       │  Queue  │          │
│  └─────────┘       └─────────┘       └─────────┘          │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Autonomous Modules                      │   │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐       │   │
│  │  │Heartbeat│ │  Cron  │ │Approval│ │ Tasks  │       │   │
│  │  └────────┘ └────────┘ └────────┘ └────────┘       │   │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐       │   │
│  │  │Browser │ │  MCP   │ │ Memory │ │Proactive│       │   │
│  │  └────────┘ └────────┘ └────────┘ └────────┘       │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Starting the Gateway

```bash
# Foreground (for development)
navig gateway start

# As daemon (production)
navig gateway start --daemon

# Check status
navig gateway status

# View logs
navig gateway logs
```

## HTTP API Endpoints

### Core Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/status` | Detailed status with uptime, config, sessions |
| POST | `/shutdown` | Graceful shutdown |
| POST | `/message` | Send message to agent |
| POST | `/event` | Inject system event |
| GET | `/sessions` | List active sessions |
| GET | `/ws` | WebSocket connection |

### Heartbeat Management

| Method | Path | Description |
|--------|------|-------------|
| POST | `/heartbeat/trigger` | Trigger immediate heartbeat |
| GET | `/heartbeat/history` | Get heartbeat history |
| GET | `/heartbeat/status` | Get heartbeat status |

### Cron Job Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/cron/jobs` | List all jobs |
| POST | `/cron/jobs` | Create new job |
| GET | `/cron/jobs/{id}` | Get job details |
| DELETE | `/cron/jobs/{id}` | Delete job |
| POST | `/cron/jobs/{id}/enable` | Enable job |
| POST | `/cron/jobs/{id}/disable` | Disable job |
| POST | `/cron/jobs/{id}/run` | Run job now |

### Approval System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/approval/pending` | List pending requests |
| POST | `/approval/request` | Create approval request |
| POST | `/approval/{id}/respond` | Approve/deny request |

### Browser Automation

| Method | Path | Description |
|--------|------|-------------|
| GET | `/browser/status` | Browser controller status |
| POST | `/browser/navigate` | Navigate to URL |
| POST | `/browser/click` | Click element |
| POST | `/browser/fill` | Fill form field |
| POST | `/browser/screenshot` | Take screenshot |
| POST | `/browser/stop` | Stop browser |

### MCP (Model Context Protocol)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/mcp/clients` | List MCP clients |
| GET | `/mcp/tools` | List available tools |
| POST | `/mcp/tools/{name}/call` | Call MCP tool |
| POST | `/mcp/connect` | Connect to MCP server |
| POST | `/mcp/disconnect` | Disconnect client |

### Task Queue

| Method | Path | Description |
|--------|------|-------------|
| GET | `/tasks` | List tasks |
| POST | `/tasks` | Add new task |
| GET | `/tasks/stats` | Queue statistics |
| GET | `/tasks/{id}` | Get task details |
| POST | `/tasks/{id}/cancel` | Cancel task |

### Memory/Context

| Method | Path | Description |
|--------|------|-------------|
| GET | `/memory/sessions` | List memory sessions |
| GET | `/memory/sessions/{key}/history` | Get session history |
| DELETE | `/memory/sessions/{key}` | Delete session |
| POST | `/memory/messages` | Add message to context |
| GET | `/memory/knowledge` | List knowledge base |
| POST | `/memory/knowledge` | Add to knowledge base |
| GET | `/memory/knowledge/search` | Search knowledge |
| GET | `/memory/stats` | Memory statistics |

## WebSocket Protocol

Connect to `ws://localhost:18789/ws` for real-time updates.

### Message Format

```json
{
  "action": "command|subscribe|ping|message",
  "id": "optional-request-id",
  "data": { ... }
}
```

### Actions

#### ping
```json
{"action": "ping"}
// Response: {"action": "pong"}
```

#### subscribe
```json
{"action": "subscribe", "events": ["heartbeat", "cron", "alerts"]}
// Response: {"action": "subscribed"}
```

#### message
```json
{
  "action": "message",
  "channel": "ws",
  "user_id": "user123",
  "message": "What's the server status?"
}
// Response: {"action": "response", "response": "All systems operational..."}
```

## Configuration

Gateway configuration in `~/.navig/config.yaml`:

```yaml
gateway:
  enabled: true
  port: 18789
  host: "localhost"
  auth_required: false  # Enable for production
  
heartbeat:
  enabled: true
  interval: 300  # seconds
  
cron:
  enabled: true
  
telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN}"
  allowed_users: [123456789]
  session_isolation: true
  group_activation_mode: "mention"
```

## Authentication

For production deployments, enable authentication:

```yaml
gateway:
  auth_required: true
  auth_token: "${GATEWAY_AUTH_TOKEN}"
```

Then include in requests:
```
Authorization: Bearer <token>
```

## Security Considerations

1. **Bind to localhost** by default (use reverse proxy for external access)
2. **Enable authentication** for production
3. **Task queue** only allows `navig` prefixed commands
4. **Approval system** requires human confirmation for destructive actions
5. **WebSocket** connections tracked by client ID

## Monitoring

### Health Checks

```bash
curl http://localhost:18789/health
```

### Status

```bash
curl http://localhost:18789/status
```

### Prometheus Metrics (coming soon)

```
GET /metrics
```

## Integration with VS Code Extension

The NAVIG VS Code extension connects to the gateway for:

- Real-time command execution
- Session management
- Status monitoring
- Log streaming

Configure in extension settings:
```json
{
  "navig.gatewayUrl": "http://localhost:18789"
}
```

## Gateway Startup Flow

The gateway has a specific initialization sequence. Understanding this is critical
for debugging startup issues.

```
navig gateway start [--daemon]
       │
       ▼
daemon/entry.py
  └─ Forks process if --daemon
       │
       ▼
daemon/telegram_worker.py :: _run()
  │
  ├── 1. _load_env()          Load .env from cwd / project root / ~/.navig
  ├── 2. _telegram_config()    Read bot_token, allowed_users/groups from config
  ├── 3. _deck_config()        Read deck settings (port, bind, static_dir)
  │
  ├── 4. NavigGateway()        Instantiate gateway (gateway/server.py)
  │      ├── GatewayConfig     Parse gateway section from config.yaml
  │      ├── SessionManager    Session persistence (gateway/session_manager.py)
  │      ├── ChannelRouter     Message routing (gateway/channel_router.py)
  │      ├── SystemEventQueue  Event processing (gateway/system_events.py)
  │      ├── ConfigWatcher     Hot-reload config changes
  │      └── ProactiveEngine   Proactive behaviors (agent/proactive/engine.py)
  │
  ├── 5. create_telegram_channel(gateway, config)
  │      └── TelegramChannel   (gateway/channels/telegram.py)
  │          ├── SessionManager (telegram_sessions.py)
  │          ├── MentionGate   (telegram_sessions.py)
  │          ├── KeyboardBuilder (telegram_keyboards.py)
  │          └── Message templates (telegram_templates.py)
  │
  ├── 6. gateway.channels["telegram"] = channel
  │
  ├── 7. _start_gateway_http(gateway, config, deck_cfg)
  │      ├── Create aiohttp Application with CORS middleware
  │      ├── Register routes: /health, /status, /message
  │      └── Register Deck routes: /api/deck/* (if enabled)
  │          └── deck_api.py::register_deck_routes()
  │              └── Auth via Telegram WebApp initData (HMAC-SHA256)
  │
  ├── 8. channel.start()       Start Telegram long-polling
  │
  └── 9. Event loop            Wait for SIGINT/SIGTERM
         └── Shutdown: channel.stop() → _stop_gateway_http()
```

### Key Facts

- **Deck is tightly coupled to Telegram**: The Deck Mini App uses the bot token
  for HMAC auth. No bot = no Deck.
- **Single event loop**: Both Telegram polling and HTTP server share one asyncio loop.
- **Gateway port**: Configurable via `gateway.port` in config.yaml (default: 8789).
