#!/usr/bin/env python3
"""Fix docker-compose.yml: restore from backup and add Conduit service."""
import os
import glob
import subprocess

COMPOSE_FILE = "/opt/navig/docker-compose.yml"

# Step 1: Restore from backup
backups = sorted(glob.glob(f"{COMPOSE_FILE}.bak-*"), reverse=True)
if backups:
    latest = backups[0]
    subprocess.run(["sudo", "cp", latest, COMPOSE_FILE], check=True)
    print(f"Restored from: {latest}")
else:
    print("No backup found, using current file")

# Step 2: Read the file
with open(COMPOSE_FILE, "r") as f:
    content = f.read()

# Step 3: Add conduit_data volume after ollama_models
lines = content.split("\n")
new_lines = []
volume_added = False

for line in lines:
    new_lines.append(line)
    # Add conduit_data right after ollama_models in the volumes section
    if "ollama_models:" in line and not volume_added and "  ollama_models" in line:
        new_lines.append("  conduit_data:")
        volume_added = True

# Step 4: Append conduit service at the end
conduit_block = """
  # -- Conduit Matrix Homeserver --
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

result = "\n".join(new_lines).rstrip() + "\n" + conduit_block

# Step 5: Write the fixed file
with open("/tmp/docker-compose-fixed.yml", "w") as f:
    f.write(result)

subprocess.run(["sudo", "cp", "/tmp/docker-compose-fixed.yml", COMPOSE_FILE], check=True)
print("Compose file updated with Conduit")

# Step 6: Validate
r = subprocess.run(
    ["sudo", "docker", "compose", "-f", COMPOSE_FILE, "--env-file", "/opt/navig/.env", "config", "--quiet"],
    capture_output=True, text=True
)
if r.returncode == 0:
    print("COMPOSE_VALID")
else:
    print(f"COMPOSE_INVALID: {r.stderr}")
