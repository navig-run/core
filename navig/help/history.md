# `navig history`

Command history, replay, and audit trail for all NAVIG operations.

Features:
- View all past commands with filtering
- Replay previous operations (with modifications)
- Undo reversible operations
- Export audit logs for compliance

Typical flow:
- View recent history: `navig history`
- Search for a command: `navig history list --search "docker"`
- Replay a command: `navig history replay 1`
- Export for audit: `navig history export audit.json`

Commands:
- `navig history list` — list operations with filters
- `navig history show <id>` — show operation details
- `navig history replay <id>` — re-run a previous operation
- `navig history undo <id>` — undo reversible operation
- `navig history export <file>` — export to JSON/CSV
- `navig history clear` — clear all history
- `navig history stats` — show statistics

Examples:
- `navig history` — show last 20 operations
- `navig history list --host production --since 24h`
- `navig history list --status failed`
- `navig history show 1` — details of last operation
- `navig history replay 1 --dry-run` — preview replay
- `navig history replay 1 --modify "--host staging"`
- `navig history export audit.csv --format csv`

Filtering options:
- `--host <name>` — filter by target host
- `--status <success|failed>` — filter by outcome
- `--type <type>` — filter by operation type
- `--since <1h|24h|7d>` — filter by time
- `--search <text>` — search in command text

Automation:
- Use `--json` for structured output
- Use `--plain` for one-line-per-operation format
- Combine with `--yes` to skip confirmations

Storage: `~/.navig/history/operations.jsonl`
