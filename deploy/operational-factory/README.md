# NAVIG Operational Factory (MVP)

Safe-by-default multi-agent operations stack with approval gating and full audit trail.

## Included services
- ollama
- postgres (pgvector)
- redis
- tool-gateway
- sandbox-runner
- navig-runtime
- worker
- dashboard

## Quick start
1. Copy env file:
   cp .env.example .env
2. Start:
   ./scripts.sh start
3. Pull model:
   ./scripts.sh pull-model
4. Open approval inbox:
   http://127.0.0.1:8088

## One-line commands
- Start: ./scripts.sh start
- Stop: ./scripts.sh stop
- Status: ./scripts.sh status
- Logs: ./scripts.sh logs
- Migrate: ./scripts.sh migrate
- Seed agents: ./scripts.sh seed

## Acceptance demo
1. Trigger email flow:
   curl -X POST http://127.0.0.1:8091/flow/email/intake -H 'content-type: application/json' -d '{"limit":10}'
2. Trigger repo flow:
   curl -X POST http://127.0.0.1:8091/flow/repo/propose -H 'content-type: application/json' -d '{}'
3. Open dashboard, approve/reject pending actions.
4. Verify audit:
   docker compose exec -T postgres psql -U navig -d navig_factory -c "select created_at,actor_id,action,status from audit_log order by id desc limit 20;"

## Security posture
- Restricted actions are never auto-executed.
- Secrets loaded only from .env.
- Every gateway action is audited (sanitized inputs/outputs).
- Sandbox operations run in dedicated `sandbox-runner` service and use isolated repo copies.

## Cross-platform NAVIG installers

- Windows: `navig-core/scripts/install_navig_windows.ps1`
- Linux: `navig-core/scripts/install_navig_linux.sh`
- macOS: `navig-core/scripts/install_navig_macos.sh`
