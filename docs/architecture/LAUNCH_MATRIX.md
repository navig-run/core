# NAVIG Launch Matrix

Canonical runtime entrypoints for production and local operation.

## Supported launch modes

| Mode | Command | Starts | Use when |
|------|---------|--------|----------|
| Gateway only | `navig gateway start --host 127.0.0.1 --port 8789` | HTTP/WebSocket gateway | Integrations, health/status APIs, cron/heartbeat services |
| Telegram worker + gateway | `python -m navig.daemon.telegram_worker --port 8789` | Gateway (if needed) + Telegram worker | Default Telegram runtime path |
| Telegram worker only | `python -m navig.daemon.telegram_worker --no-gateway` | Telegram worker only | Bot-only runtime where gateway is managed elsewhere |
| Unified CLI launch | `navig start` | Gateway + Telegram worker in background | Operator-friendly single command startup |
| Unified foreground launch | `navig start --foreground` | Gateway + Telegram worker in foreground | Debugging and live logs |
| Supervisor launch | `navig service start` | Supervisor + configured child processes | Long-running production service mode |

## Legacy path policy

- `python navig_bot.py` is deprecated and not a canonical entrypoint.
- Supervisor no longer auto-discovers `navig_bot.py`.
- If a legacy script must be used temporarily, pass an explicit `bot_script` path in daemon config.

## Verification checklist

- `python -m navig.daemon.telegram_worker --help` returns successfully.
- `python -m navig --help` lists `start`.
- `navig gateway start --help` returns successfully.
- `navig bot --help` returns successfully.
- `navig service --help` returns successfully.
