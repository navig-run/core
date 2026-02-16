#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  start)
    docker compose --env-file .env up -d --build
    docker compose --env-file .env run --rm navig-runtime python -m app.migrate
    docker compose --env-file .env run --rm navig-runtime python -m app.seed
    ;;
  stop)
    docker compose --env-file .env down
    ;;
  status)
    docker compose --env-file .env ps
    ;;
  logs)
    docker compose --env-file .env logs -f --tail=200
    ;;
  pull-model)
    docker compose --env-file .env exec -T ollama ollama pull qwen2.5:7b-instruct
    ;;
  migrate)
    docker compose --env-file .env run --rm navig-runtime python -m app.migrate
    ;;
  seed)
    docker compose --env-file .env run --rm navig-runtime python -m app.seed
    ;;
  *)
    echo "Usage: $0 {start|stop|status|logs|pull-model|migrate|seed}" >&2
    exit 1
    ;;
esac
