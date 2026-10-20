- **The MCP catalogue is audited rather than assumed: 152 tools scored, every definition
  fixed where it lied.** `resources/templates/list` now answers (a spec-following client
  called it because `resources` was advertised, and got `-32601`), and the connector
  generator stopped emitting one description for every verb — `connector_gmail_search`
  no longer advertises "send and manage", its annotation is `readOnlyHint: true`, and
  `action_type` is pinned to the `ActionType` enum with `limit` clamped to the 1–50 the
  schema promises. `navig_run_command` accepts `command="host list"` as written (it used
  to run one argv token and die with `No such command`) and matches its read-only
  allowlist by command PATH instead of string prefix. A Linux server no longer offers the
  24 Windows UI-automation tools it could not run (152 → 128 advertised in a container,
  `desktop_filesystem` stays). Reads are annotated as reads: `navig_space_*` and
  `dock_status/tabs/text/snapshot` were `destructiveHint: true` while gated `safe`.
  New evidence tooling, all runnable in CI: `core/tools/mcp_inventory.py` (census),
  `mcp_verdicts.py` (PASS/PARTIAL/BLOCKED/UNSUPPORTED per tool), `mcp_selection_benchmark.py`
  (40 requests, BM25, top-1 0.775 → 0.800 / top-3 0.875 → 0.950), `mcp_tdqs.py` (0–5
  definition score with its calibration disclosed: 3.58 → 4.14 on the same ruler) and
  `tools/mcp-instance-check.mjs`, which probes a DEPLOYED endpoint so a stale container is
  visible instead of assumed.
