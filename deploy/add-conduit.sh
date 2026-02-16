#!/bin/bash
set -euo pipefail

COMPOSE_FILE="/opt/navig/docker-compose.yml"

# Check if conduit already exists
if sudo grep -q "conduit" "$COMPOSE_FILE" 2>/dev/null; then
    echo "Conduit already in docker-compose.yml - skipping"
    exit 0
fi

echo "Backing up docker-compose.yml..."
sudo cp "$COMPOSE_FILE" "${COMPOSE_FILE}.bak-$(date +%Y%m%d_%H%M%S)"

# Add conduit_data volume
if ! sudo grep -q "conduit_data" "$COMPOSE_FILE"; then
    sudo sed -i '/ollama_models:/a\  conduit_data:' "$COMPOSE_FILE"
    echo "Added conduit_data volume"
fi

# Append conduit service at the end of the file
sudo tee -a "$COMPOSE_FILE" > /dev/null << 'EOF'

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
EOF

echo "Conduit service added to docker-compose.yml"
echo "CONDUIT_ADDED_OK"
