# Telegram Bot (Complete Command Reference)

Use NAVIG from Telegram with low-friction command flows, context-aware routing, and inline controls.

## Scope

This file is the canonical Telegram command reference.

Authoritative runtime sources:
- `navig/gateway/channels/telegram_commands.py` (`_SLASH_REGISTRY`)
- `navig/gateway/channels/telegram.py` (step-5 fast paths + dynamic dispatch)
- `navig/gateway/channels/telegram_keyboards.py` (inline callback actions)

## Setup

1) Create a bot via `@BotFather` and get a token.
2) Run onboarding:

```bash
navig init
```

3) Start Telegram gateway/daemon:

```bash
navig start
# or
navig bot
```

## Runtime Behavior

- Private chats: commands and natural language are processed directly.
- Group chats: command permissions and business-chat checks apply to moderation commands.
- Home UX: `/start` shows a context card; `nav:home` returns to that card.
- Mentions: commands like `/big@YourBot` are supported through dynamic slash dispatch.

## Natural Language = Commands

Telegram now supports command-equivalent natural language for visible slash commands.

- Safe/read-style intents run immediately (example: "show status" → `/status`).
- Risky intents require explicit confirmation (`yes` / `cancel`) before execution.
- Missing-argument intents return command usage help instead of guessing.

Risky commands requiring confirmation include remote execution or state-changing actions such as:
- `/run`, `/restart`, `/docker` mutating operations
- `/use`, `/space`, `/intake`, `/plan`
- moderation commands (`/kick`, `/mute`, `/unmute`, `/search`)

This preserves speed for common checks while preventing accidental destructive actions.

## Command Reference

Legend:
- `Visible`: appears in Telegram command menu and `/help`
- `Alias/Hidden`: callable, but hidden from command list

### Core

| Command | Visible | Usage | Description |
|---|---|---|---|
| `/start` | Yes | `/start` | Wake up greeting |
| `/help` | Yes | `/help` | Full command reference |
| `/helpme` | Alias/Hidden | `/helpme` | Command reference alias |
| `/status` | Yes | `/status` | System and spaces status |
| `/models` | Yes | `/models [big|small|coder|auto]` | Active model routing table |
| `/model` | Alias/Hidden | `/model [big|small|coder|auto]` | Alias for models |
| `/routing` | Alias/Hidden | `/routing` | Alias for models |
| `/router` | Alias/Hidden | `/router` | Alias for models |
| `/briefing` | Yes | `/briefing` | Today’s summary |
| `/deck` | Alias/Hidden | `/deck` | Open command deck |
| `/ping` | Yes | `/ping` | Heartbeat/status card |
| `/skill` | Yes | `/skill list` or `/skill <name>` | Run NAVIG skills |
| `/about` | Yes | `/about` | Bot/about info |

### Monitoring

| Command | Visible | Usage | Description |
|---|---|---|---|
| `/disk` | Yes | `/disk` | Disk usage |
| `/memory` | Yes | `/memory` | RAM status |
| `/cpu` | Yes | `/cpu` | CPU/load info |
| `/uptime` | Yes | `/uptime` | Server uptime |
| `/services` | Yes | `/services` | Running services |
| `/ports` | Yes | `/ports` | Open ports |
| `/top` | Alias/Hidden | `/top` | Process list |
| `/df` | Alias/Hidden | `/df` | Disk usage (df) |
| `/cron` | Alias/Hidden | `/cron` | Crontab |

### Docker

| Command | Visible | Usage | Description |
|---|---|---|---|
| `/docker` | Yes | `/docker [ps|logs <name>|restart <name>|all]` | Container actions |
| `/logs` | Yes | `/logs <container-name>` | Container logs |
| `/restart` | Yes | `/restart [daemon|<container-name>]` | Restart daemon/container |

### Database

| Command | Visible | Usage | Description |
|---|---|---|---|
| `/db` | Yes | `/db` | List databases |
| `/tables` | Yes | `/tables <database-name>` | List DB tables |

### Tools

| Command | Visible | Usage | Description |
|---|---|---|---|
| `/hosts` | Yes | `/hosts` | Configured servers |
| `/use` | Yes | `/use <hostname>` | Switch active host |
| `/run` | Yes | `/run <shell command>` | Execute remote command |
| `/backup` | Yes | `/backup` | Backup status |
| `/plans` | Yes | `/plans` | Plans/spaces progress |
| `/plan` | Yes | `/plan <goal text>` | Add plan goal |
| `/space` | Yes | `/space <name>` | Switch active space |
| `/spaces` | Yes | `/spaces [name]` | List spaces or quick-switch |
| `/intake` | Yes | `/intake` | Guided planning intake |
| `/format` | Yes | `/format <text>` | Telegram-safe formatting |
| `/fmt` | Alias/Hidden | `/fmt` | Alias for format |
| `/think` | Yes | `/think <topic or question>` | Reasoning cards |
| `/refine` | Yes | `/refine <idea or text>` | Clarify/refine idea |

### Utilities

| Command | Visible | Usage | Description |
|---|---|---|---|
| `/ip` | Yes | `/ip` | Public IP |
| `/time` | Yes | `/time` | Server time |
| `/weather` | Yes | `/weather [city]` | Weather report |
| `/dns` | Yes | `/dns <domain>` | DNS lookup |
| `/ssl` | Yes | `/ssl <domain>` | SSL cert check |
| `/whois` | Yes | `/whois <domain>` | Whois lookup |
| `/netstat` | Alias/Hidden | `/netstat` | Network stats |
| `/currency` | Yes | `/currency <query>` | Currency conversion (beta) |
| `/crypto_list` | Yes | `/crypto_list` | Crypto list |
| `/remindme` | Yes | `/remindme <text> in <time>` or `at <time>` | Set reminder |
| `/myreminders` | Yes | `/myreminders` | List reminders |
| `/cancelreminder` | Yes | `/cancelreminder <id>|all` | Cancel reminder(s) |
| `/stats_global` | Yes | `/stats_global` | Chat stats (beta) |
| `/choice` | Yes | `/choice <a> or <b> [or <c>...]` | Random choice (`or`, `,`, `|`) |

### Model Control

| Command | Visible | Usage | Description |
|---|---|---|---|
| `/settings` | Yes | `/settings` | Settings hub |
| `/providers` | Yes | `/providers [provider-name]` | Provider hub |
| `/provider` | Alias/Hidden | `/provider [provider-name]` | Alias for providers |
| `/mode` | Yes | `/mode [work|deep-focus|coder|auto|list]` | Focus mode |
| `/big` | Yes | `/big` then send message | Force big tier |
| `/small` | Yes | `/small` then send message | Force small tier |
| `/coder` | Yes | `/coder` then send message | Force coder tier |
| `/auto` | Yes | `/auto` then send message | Reset tier to automatic |

### Voice

| Command | Visible | Usage | Description |
|---|---|---|---|
| `/voice` | Yes | `/voice` | Voice/TTS settings |
| `/audio` | Alias/Hidden | `/audio` | Alias for voice |
| `/voicereply` | Yes | `/voicereply` | Toggle voice replies |
| `/voiceon` | Yes | `/voiceon` | Enable voice replies |
| `/voiceoff` | Yes | `/voiceoff` | Disable voice replies |

### Diagnostics

| Command | Visible | Usage | Description |
|---|---|---|---|
| `/user` | Yes | `/user` | Profile/tier/session snapshot |
| `/version` | Yes | `/version` | Version/runtime info |
| `/debug` | Yes | `/debug` | Debug environment info |
| `/trace` | Yes | `/trace` or `/trace debug on|off` | Trace + debug footer toggle |
| `/autoheal` | Yes | `/autoheal [on|off|status|hive on|hive off]` | Auto-heal controls |

### AI

| Command | Visible | Usage | Description |
|---|---|---|---|
| `/auto_start` | Yes | `/auto_start [persona]` | Enable AI auto-replies |
| `/auto_stop` | Yes | `/auto_stop` | Disable AI auto-replies |
| `/auto_status` | Yes | `/auto_status` | Show AI auto-reply status |
| `/continue` | Yes | `/continue [conservative|balanced|aggressive] [space]` | Enable continuation policy |
| `/pause` | Yes | `/pause` | Pause continuation |
| `/skip` | Yes | `/skip` | Skip next continuation turn |
| `/auto_roles` | Yes | `/auto_roles` | Deprecated alias (use `/personas`) |
| `/persona` | Yes | `/persona <name>` | Switch active persona |
| `/personas` | Yes | `/personas` | List personas |
| `/explain_ai` | Yes | `/explain_ai <topic>` | Explain a topic |

### Media

| Command | Visible | Usage | Description |
|---|---|---|---|
| `/music` | Yes | `/music` | Music conversion (beta) |
| `/imagegen` | Yes | `/imagegen <prompt>` | Image generation (beta) |

### Social

| Command | Visible | Usage | Description |
|---|---|---|---|
| `/profile` | Yes | `/profile` | User profile |
| `/quote` | Yes | `/quote` | Quote system (beta) |
| `/respect` | Yes | `/respect` | Respect system (beta) |

### Admin (Business/Group context)

| Command | Visible | Usage | Description |
|---|---|---|---|
| `/kick` | Yes | `/kick <@user|id>` | Remove user |
| `/mute` | Yes | `/mute <@user|id>` | Restrict user |
| `/unmute` | Yes | `/unmute <@user|id>` | Restore user |
| `/search` | Yes | `/search <query>` | User search |

## Inline Callback Actions (Non-Slash)

These are button-driven actions, not slash commands.

- `helpme` → open command reference from context card
- `nav:*` → screen navigation (`open`, `back`, `home`, `cancel`)
- `ms_*` → model switcher controls
- `pm_*` → provider/model picker controls
- `prov_*` → provider hub actions
- `st_*` → settings hub actions
- `dbg_*` → debug panel actions
- `trace_*` → trace quick actions
- `heard_*` → heard/reaction actions
- `nl_*` → natural-language confirm/cancel actions
- `audio:*` and `audmsg:*` → deep audio controls
- `task:*` → task-card controls (safe fallback if unavailable)

## UX Notes

- Prefer slash commands for discoverability; use callbacks for in-context adjustments.
- Keep command responses concise and actionable; follow-up actions should require one tap or one command.
- Use `/help` as the primary command map and this file for exhaustive details.
