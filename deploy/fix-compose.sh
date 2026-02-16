#!/bin/bash
set -euo pipefail

COMPOSE_FILE="/opt/navig/docker-compose.yml"

# Restore from backup
echo "Restoring from backup..."
LATEST_BACKUP=$(sudo ls -t /opt/navig/docker-compose.yml.bak-* 2>/dev/null | head -1)
if [ -n "$LATEST_BACKUP" ]; then
    sudo cp "$LATEST_BACKUP" "$COMPOSE_FILE"
    echo "  Restored from: $LATEST_BACKUP"
else
    echo "  ERROR: No backup found"
    exit 1
fi

# Use Python to safely add conduit to YAML
sudo python3 << 'PYEOF'
import re

with open("/opt/navig/docker-compose.yml", "r") as f:
    content = f.read()

# Add conduit_data to volumes section (after last volume in the volumes block)
# Find the volumes section and add conduit_data after the last volume entry
# The volumes section is: "volumes:\n  postgres_data:\n  redis_data:\n  ollama_models:\n"
content = content.replace(
    "  ollama_models:\n\nservices:",
    "  ollama_models:\n  conduit_data:\n\nservices:"
)

# If the above didn't match (no blank line between volumes and services), try another pattern
if "conduit_data:" not in content:
    content = content.replace(
        "  ollama_models:\nservices:",
        "  ollama_models:\n  conduit_data:\nservices:"
    )

# Append conduit service at the end
conduit_service = """
  # ── Conduit Matrix Homeserver ─────────────────────
  conduit:
    image: matrixconduit/matrix-conduit:latest
    container_name: navig-conduit
    restart: unless-stopped
    environment:
      CONDUIT_SERVER_NAME: "navig.local"
      CONDUIT_DATABASE_BACKEND: "rocksdb"
      CONDUIT_ALLOW_REGISTRATION: "true"
      CONDUIT_ALLOW_FEDERATION: "false"
      CONDUIT_PORT: "6167"
      CONDUIT_ADDRESS: "0.0.0.0"
      CONDUIT_MAX_REQUEST_SIZE: "20000000"
      CONDUIT_TRUSTED_SERVERS: '["matrix.org"]'
      CONDUIT_LOG: "warn,rocket::launch=info"
      CONDUIT_ALLOW_CHECK_FOR_UPDATES: "false"
    volumes:
      - conduit_data:/var/lib/matrix-conduit
    ports:
      - "127.0.0.1:6167:6167"
    networks:
      - navig-net
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://localhost:6167/_matrix/client/versions || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 15s
    deploy:
      resources:
        limits:
          memory: 256M
"""

content = content.rstrip() + "\n" + conduit_service

with open("/opt/navig/docker-compose.yml", "w") as f:
    f.write(content)

print("Docker-compose updated successfully")
PYEOF

# Validate
echo "Validating..."
sudo docker compose -f "$COMPOSE_FILE" config --quiet 2>&1 && echo "COMPOSE_VALID" || echo "COMPOSE_INVALID"
