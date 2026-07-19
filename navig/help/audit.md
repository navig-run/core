# `navig audit`

The privileged-action audit trail: every action that passes the gateway's
policy and approval gates (policy allow/deny, approval requests and their
outcomes, gated agent tool calls) leaves one JSONL record in
`runtime/audit.jsonl`. This command reads that file directly from disk —
no daemon required, works offline.

What lands here (who / what / when / decision):
- policy-gated actions (`mission.create`, `run.shell`, …) — allow, deny,
  pending_approval, approved
- approval responses (`approval.respond`) from the deck Inbox / Telegram
- gated agent tool executions (`tool.execute.<tool_name>`) — the agent
  wanted to run a dangerous tool; the record carries the decision
- inputs are hashed (`input_hash`), never stored verbatim — no secrets

Commands:
- `navig audit tail` — recent records: time, actor, action, status
  (✓ approved/success · ✗ denied · … pending · ! error), short input
  hash, and the recorded reason

Options:
- `--tail <n>` / `-n <n>` — how many recent records to show (default 20)
- `--action <prefix>` — filter by action prefix (e.g. `tool.execute`,
  `approval.`)
- `--actor <actor>` — filter by exact actor (e.g. `telegram:user:123`)
- `--status <s>` — filter by status: `success`, `approved`, `denied`,
  `error`, `pending_approval`
- `--path <file>` — inspect a specific audit file (e.g. a copied log);
  defaults to the live `runtime/audit.jsonl`
- `--json` — machine-readable output (path, total, count, events)

Examples:
- `navig audit tail`
- `navig audit tail -n 50 --status denied`
- `navig audit tail --action tool.execute`
- `navig audit tail --actor telegram:user:123 --json`

Exit codes:
- `0` — always (a view, not a gate; a missing or empty log is an honest
  non-failure state, reported plainly)

Notes:
- The same data is queryable from a running gateway via `GET /audit`
  (filters: `limit`, `action`, `actor`, `status`).
- Pending approvals are actionable: `navig approve list` / `navig approve
  yes <id>` / `navig approve no <id>`.
- For integrity of the *operations* history (the hash-chained
  `operations.jsonl`), see `navig ledger`.

Storage: `~/.navig/runtime/audit.jsonl` (honours `NAVIG_CONFIG_DIR`)
