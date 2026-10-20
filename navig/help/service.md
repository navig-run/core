# Service — Persistent Daemon Management

Run NAVIG as a persistent background service. The daemon supervisor keeps the
Telegram bot (and optionally gateway) alive with auto-restart. Cron runs inside
the gateway, not as a supervised child.

## Quick Start

```bash
navig service install          # Install as service (auto-detects method)
navig service status           # Check daemon health
navig service logs -f          # Follow live logs
navig service stop             # Graceful shutdown
```

## Commands

| Command | Description |
|---------|-------------|
| `navig service install` | Install as Windows service or scheduled task |
| `navig service start` | Start daemon (background) |
| `navig service start -f` | Start in foreground (debug) |
| `navig service stop` | Graceful shutdown |
| `navig service restart` | Stop + start |
| `navig service status` | Show daemon and child process health |
| `navig service logs` | Show last 50 log lines |
| `navig service logs -f` | Follow log output |
| `navig service config` | View daemon configuration |
| `navig service uninstall` | Remove service registration |

## Installation Methods

| Method | Admin? | Starts On | Notes |
|--------|--------|-----------|-------|
| `task` | No | Login | Windows Task Scheduler, auto-restart |
| `nssm` | Yes | Boot | True Windows service via NSSM |

```bash
navig service install --method task     # No admin needed
navig service install --method nssm     # Requires admin + NSSM
navig service install --gateway         # Include gateway server
```

## Configuration

Config: `~/.navig/daemon/config.json`

```json
{
  "telegram_bot": true,
  "gateway": false,
  "health_port": 0
}
```

Cron runs inside the gateway (not a daemon child); the legacy `scheduler`
key and `--scheduler` flag are deprecated no-ops, kept for compatibility.

## Tips

- Daemon logs: `~/.navig/logs/daemon.log` (rotates at 5 MB)
- Child output: `~/.navig/logs/children.log`
- State file: `~/.navig/daemon/state.json` (JSON, machine-readable)
- PID file: `~/.navig/daemon/supervisor.pid`
