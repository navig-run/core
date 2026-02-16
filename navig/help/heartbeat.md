# heartbeat

Periodic health check system for monitored hosts and VS Code formation activity.

## Formation Heartbeat (VS Code → Telegram)

The VS Code extension writes `~/.navig/heartbeat.json` every 30 seconds while a formation session is active. The Telegram bot reads this file and sends at most one proactive "next action" message per heartbeat window when there are actionable items.

**How it works:**
1. Extension writes heartbeat with `active`, `formation_id`, `agents`, `workspace_path`, `timestamp`
2. Bot checks the file every `HEARTBEAT_INTERVAL` seconds (default 60)
3. If active + fresh: scans for inbox briefs, next-step markers, or TODO items
4. If actionable: sends ONE short message, then waits `HEARTBEAT_WINDOW` seconds (default 300)
5. If idle/closed: stays completely silent

**Telegram commands:**
- `/formation` — check current formation status (active/stale/offline, agents, workspace)

**Environment variables:**
- `HEARTBEAT_ENABLED` — enable/disable heartbeat monitor (default: true)
- `HEARTBEAT_INTERVAL` — seconds between heartbeat checks (default: 60)
- `HEARTBEAT_WINDOW` — minimum seconds between proactive messages (default: 300)

**Actionable item sources** (checked in order):
1. `.navig/plans/inbox/*.md` — unprocessed briefs
2. `.navig/plans/next-step.md` — explicit next-step marker
3. `.navig/plans/todo.md` — unchecked `- [ ]` items

## Host Heartbeat (CLI)

Common commands:
- `navig heartbeat status` — show heartbeat status
- `navig heartbeat trigger` — trigger immediate heartbeat
- `navig heartbeat history` — show heartbeat history
- `navig heartbeat configure` — configure heartbeat settings

Examples:
- `navig heartbeat status --json`
- `navig heartbeat trigger`
