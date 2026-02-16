# NAVIG Telegram Bot - Natural Language Processing Guide

This guide explains the NLP (Natural Language Processing) layer that enables conversational command interactions with the NAVIG Telegram bot.

## Overview

The NLP layer translates natural language messages into bot commands, allowing users to interact conversationally without memorizing exact slash command syntax.

```
User: "show me docker containers"
  ↓
IntentParser (AI + Patterns)
  ↓
Command: /docker
  ↓
Bot executes and responds
```

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    User Message                          │
│              "show me docker containers"                 │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│                  Intent Parser                           │
│  ┌─────────────────┐   ┌─────────────────────────────┐  │
│  │   AI Function   │   │   Pattern Matching          │  │
│  │   Calling       │   │   (Regex + Keywords)        │  │
│  │   (if enabled)  │   │   (Fast Fallback)           │  │
│  └────────┬────────┘   └─────────────┬───────────────┘  │
│           │                          │                   │
│           └──────────┬───────────────┘                   │
│                      ▼                                   │
│           IntentParseResult                              │
│           - command: "docker_ps"                         │
│           - args: {}                                     │
│           - confidence: 0.85                             │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│              Confidence Check                            │
│  ┌───────────────────────────────────────────────────┐  │
│  │ ≥ 0.7  → Execute command directly                 │  │
│  │ 0.4-0.7 → Ask for confirmation                    │  │
│  │ < 0.4  → Fall through to AI chat                  │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│              Command Execution                           │
│              /docker → docker_command()                  │
└─────────────────────────────────────────────────────────┘
```

### Two Detection Modes

1. **AI-Powered Intent Detection** (Recommended)
   - Uses AI function calling to understand complex queries
   - Higher accuracy for ambiguous requests
   - Costs ~$0.0001-0.001 per query
   - Adds 200-800ms latency

2. **Pattern Matching** (Fallback)
   - Uses regex patterns and keywords
   - Zero API cost, <1ms latency
   - Works offline
   - Best for common, unambiguous queries

## Configuration

Add to your `~/.navig/config.yaml`:

```yaml
telegram:
  bot_token: "YOUR_TOKEN"
  allowed_users: [123456789]
  
  # NLP Settings
  nlp_enabled: true           # Enable/disable NLP intent parsing
  nlp_use_ai: true            # Use AI for intent detection (requires API key)
  nlp_confidence_threshold: 0.7  # Auto-execute above this confidence
  nlp_confirmation_threshold: 0.4  # Ask for confirmation above this
```

## Supported Natural Language Patterns

### Server Management

| Natural Language | Command |
|-----------------|---------|
| "show docker containers" | `/docker` |
| "list all containers" | `/docker` |
| "docker ps" | `/docker` |
| "switch to production" | `/use production` |
| "use staging server" | `/use staging` |
| "list all hosts" | `/hosts` |
| "show servers" | `/hosts` |

### System Monitoring

| Natural Language | Command |
|-----------------|---------|
| "check disk space" | `/disk` |
| "how much space is left" | `/disk` |
| "check memory usage" | `/memory` |
| "how much ram is used" | `/memory` |
| "check cpu load" | `/cpu` |
| "server uptime" | `/uptime` |
| "show server ip" | `/ip` |
| "list open ports" | `/ports` |
| "show running services" | `/services` |

### Docker Operations

| Natural Language | Command |
|-----------------|---------|
| "logs from nginx" | `/logs nginx` |
| "show container logs" | `/logs` |
| "restart the nginx container" | `/restart nginx` |

### Database

| Natural Language | Command |
|-----------------|---------|
| "list databases" | `/db` |
| "show tables in wordpress" | `/tables wordpress` |

### Utilities

| Natural Language | Command |
|-----------------|---------|
| "weather in London" | `/weather London` |
| "btc price" | `/crypto BTC` |
| "convert 100 usd to eur" | `/convert 100 USD EUR` |
| "what time is it in tokyo" | `/time JST` |
| "flip a coin" | `/flip` |
| "roll d20" | `/roll 20` |
| "tell me a joke" | `/joke` |

### Reminders

| Natural Language | Command |
|-----------------|---------|
| "remind me in 30 minutes to check logs" | `/remind 30m check logs` |
| "set reminder for 2 hours" | `/remind 2h` |
| "list my reminders" | `/reminders` |

### SSL & DNS

| Natural Language | Command |
|-----------------|---------|
| "check ssl for example.com" | `/ssl example.com` |
| "dns lookup google.com" | `/dns google.com` |
| "whois example.com" | `/whois example.com` |

## Confidence Levels

The parser assigns a confidence score (0.0-1.0) to each detected intent:

| Confidence | Behavior |
|------------|----------|
| **≥ 0.7** | Command executed immediately |
| **0.4 - 0.7** | Bot asks for confirmation |
| **< 0.4** | Falls through to AI chat |

### Example Confirmation Flow

```
User: "restart it"
Bot: "Did you mean: `/restart nginx`?
     Reply 'yes' to confirm, or just continue chatting."

User: "yes"
Bot: "✅ Executing: `/restart nginx`"
     [Container restarted]
```

## Extending NLP Patterns

### Adding New Patterns

Edit `navig/bot/intent_parser.py` and add to the `_compile_patterns` method:

```python
# Pattern format: (regex, command_name, args_extractor, confidence)
pattern_defs = [
    # ... existing patterns ...
    
    # Add your new pattern
    (r'\bmy\s+custom\s+command\s+(.+)\b', 'custom_cmd', 
     lambda m: {'arg': m.group(1)}, 0.85),
]
```

### Adding New Commands

1. Add the command tool schema in `navig/bot/command_tools.py`:

```python
COMMAND_TOOLS.append({
    "type": "function",
    "function": {
        "name": "custom_cmd",
        "description": "Description for AI to understand",
        "parameters": {
            "type": "object",
            "properties": {
                "arg": {"type": "string", "description": "Argument description"}
            },
            "required": ["arg"]
        }
    }
})
```

2. Add the handler mapping:

```python
COMMAND_HANDLER_MAP["custom_cmd"] = lambda args: f"/custom {args.get('arg', '')}"
```

3. Add the command handler in `navig/gateway/channels/telegram.py` and register in the NLP routing path.

## Troubleshooting

### NLP Not Working

1. Check if NLP is enabled:
   ```
   /status
   ```
   Look for "NLP Intent Parser: Enabled"

2. Verify config:
   ```yaml
   telegram:
     nlp_enabled: true
   ```

### Low Confidence Scores

- Be more specific in your queries
- Use command keywords (e.g., "docker", "disk", "memory")
- Try the exact command phrase (e.g., "docker ps")

### AI Mode Not Working

- Ensure you have an API key configured:
  ```yaml
  openrouter_api_key: "your-key-here"
  ```
- Check that `nlp_use_ai: true` in config
- Verify network connectivity to AI provider

### Commands Not Executing

1. Check authorization:
   - Your user ID must be in `allowed_users`
   
2. Check confidence threshold:
   - Lower `nlp_confidence_threshold` for more lenient matching
   
3. Check logs:
   ```bash
   # Look for NLP parsing logs
   grep "NLP" ~/.navig/bot.log
   ```

## Best Practices

1. **Be Specific**: "show docker containers" is better than "containers"

2. **Use Action Words**: Include verbs like "show", "list", "check", "restart"

3. **Include Target**: Mention what you want to act on (docker, disk, memory)

4. **For Servers**: Say "switch to X" or "use X" to change hosts

5. **For Reminders**: Include time unit ("30 minutes", "2 hours", "1 day")

## Performance

| Mode | Latency | Cost |
|------|---------|------|
| Pattern Only | <1ms | Free |
| AI + Pattern Fallback | 200-800ms | ~$0.0001-0.001/query |

The bot automatically falls back to pattern matching if AI is unavailable or times out.

## Security Notes

- NLP commands go through the same authorization checks as slash commands
- Destructive commands (restart, run) may require additional confirmation
- User IDs are validated before any command execution
- AI queries don't expose sensitive server data (only intent parsing)


