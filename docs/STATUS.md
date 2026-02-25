# navig-core Documentation Status

> Updated: 2026-02-20 by NAVIG agent (full scan)

## Status: Active — Contracts Shipped, Mesh Phase 1 Complete

navig-core is the most mature component in the ecosystem with the richest documentation.

## Component Status

| Component | Status | Notes |
|-----------|--------|-------|
| CLI (~60 modules) | Stable | `navig <resource> <action>` pattern |
| Daemon supervisor | Stable | asyncio loop, systemd service |
| MCP server | Active | 6 new tools + 3 resources for contracts |
| Gateway API | Stable | aiohttp :8765, REST + channels |
| Agent brain/soul | Stable | `navig/agent/` isolated LLM layer |
| Formation system | Stable | `.navig/plans/` per project |
| Runtime contracts | Shipped | Node, Mission, ExecutionReceipt, Capability, TrustScore |
| JSON schemas | Shipped | 3 versioned schemas in `navig/schemas/` |
| Flux Mesh Phase 1 | Shipped | UDP multicast, NodeRegistry, MeshRouter |
| mesh_token auto-gen | Shipped | `_ensure_mesh_token()` in NavigGateway.start() |
| Test coverage | 90% (56 tests) | `tests/test_contracts.py` |
| SQLite persistence | Planned | Needed for restart recovery |
| Approval policy gates | Planned | Dangerous CLI action control |
| Audit log | Planned | Privileged action tracing |

## Documentation Files Present

- `docs/architecture/` — 14 architecture documents (ARCHITECTURE.md, AGENT_GOALS.md, etc.)
- `docs/CLI_STARTUP_PERFORMANCE.md`
- `docs/forge-llm-bridge.md`
- `docs/heartbeat.md`
- `docs/WORKSPACE_OWNERSHIP.md`
- `docs/dev/`, `docs/features/`, `docs/user/` — Feature and user guide docs

## Active Blockers

1. UDP multicast real-device E2E test not run
2. Forge nodes panel not tested against live daemon
3. `navig host monitor show` unicode bug on Windows (charmap error on emoji chars)
4. SQLite durable persistence not yet implemented
5. Approval policy gates not implemented

## Next Priority Actions

1. Run `$env:PYTHONUTF8="1"; navig host use ubuntu-node; navig host monitor show --resources`
2. Fix unicode encoding bug in monitor output formatter
3. Run mesh E2E test with two daemon instances
4. Implement SQLite RuntimeStore backend

---
*See `.navig/plans/CURRENT_PHASE.md` and `PROJECT_PLAN.md` for full detail.*
