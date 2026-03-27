#!/usr/bin/env python3
"""Fix Conduit: add config file and CONDUIT_CONFIG env var."""
import subprocess

# Step 1: Create the Conduit config directory and file
conduit_config = """[global]
server_name = "navig.local"
database_path = "/var/lib/matrix-conduit/"
database_backend = "rocksdb"
port = 6167
address = "0.0.0.0"
max_request_size = 20_000_000
allow_registration = true
allow_federation = false
allow_check_for_updates = false
trusted_servers = ["matrix.org"]
log = "warn,rocket::launch=info"
"""

# Write conduit config
subprocess.run(["sudo", "mkdir", "-p", "/opt/navig/config"], check=True)
with open("/tmp/conduit.toml", "w") as f:
    f.write(conduit_config)
subprocess.run(
    ["sudo", "cp", "/tmp/conduit.toml", "/opt/navig/config/conduit.toml"], check=True
)
subprocess.run(
    ["sudo", "chown", "navig:navig", "/opt/navig/config/conduit.toml"], check=True
)
print("Conduit config written to /opt/navig/config/conduit.toml")

# Step 2: Update docker-compose.yml to mount config and add CONDUIT_CONFIG env
with open("/opt/navig/docker-compose.yml", "r") as f:
    content = f.read()

# Replace the conduit service environment and volumes sections
old_conduit = """  # -- Conduit Matrix Homeserver --
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
          memory: 256M"""

new_conduit = """  # -- Conduit Matrix Homeserver --
  conduit:
    image: matrixconduit/matrix-conduit:latest
    container_name: navig-conduit
    restart: unless-stopped
    environment:
      CONDUIT_CONFIG: "/etc/conduit/conduit.toml"
    volumes:
      - conduit_data:/var/lib/matrix-conduit
      - ./config/conduit.toml:/etc/conduit/conduit.toml:ro
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
          memory: 256M"""

if old_conduit in content:
    content = content.replace(old_conduit, new_conduit)
    with open("/tmp/docker-compose-fixed2.yml", "w") as f:
        f.write(content)
    subprocess.run(
        [
            "sudo",
            "cp",
            "/tmp/docker-compose-fixed2.yml",
            "/opt/navig/docker-compose.yml",
        ],
        check=True,
    )
    print("Docker-compose updated with CONDUIT_CONFIG mount")
else:
    print("WARNING: Could not find conduit block to replace, trying manual fix...")
    # Fallback: just replace the environment section
    content = content.replace(
        'CONDUIT_SERVER_NAME: "navig.local"',
        'CONDUIT_CONFIG: "/etc/conduit/conduit.toml"',
    )
    # Remove redundant env vars
    for env_line in [
        '      CONDUIT_DATABASE_BACKEND: "rocksdb"\n',
        '      CONDUIT_ALLOW_REGISTRATION: "true"\n',
        '      CONDUIT_ALLOW_FEDERATION: "false"\n',
        '      CONDUIT_PORT: "6167"\n',
        '      CONDUIT_ADDRESS: "0.0.0.0"\n',
        '      CONDUIT_MAX_REQUEST_SIZE: "20000000"\n',
        "      CONDUIT_TRUSTED_SERVERS: '[\"matrix.org\"]'\n",
        '      CONDUIT_LOG: "warn,rocket::launch=info"\n',
        '      CONDUIT_ALLOW_CHECK_FOR_UPDATES: "false"\n',
    ]:
        content = content.replace(env_line, "")
    # Add config mount
    content = content.replace(
        "      - conduit_data:/var/lib/matrix-conduit",
        "      - conduit_data:/var/lib/matrix-conduit\n      - ./config/conduit.toml:/etc/conduit/conduit.toml:ro",
    )
    with open("/tmp/docker-compose-fixed2.yml", "w") as f:
        f.write(content)
    subprocess.run(
        [
            "sudo",
            "cp",
            "/tmp/docker-compose-fixed2.yml",
            "/opt/navig/docker-compose.yml",
        ],
        check=True,
    )
    print("Docker-compose updated (fallback method)")

# Validate
r = subprocess.run(
    [
        "sudo",
        "docker",
        "compose",
        "-f",
        "/opt/navig/docker-compose.yml",
        "--env-file",
        "/opt/navig/.env",
        "config",
        "--quiet",
    ],
    capture_output=True,
    text=True,
)
if r.returncode == 0:
    print("COMPOSE_VALID")
else:
    print(f"COMPOSE_INVALID: {r.stderr}")
