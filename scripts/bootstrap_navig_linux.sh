#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# NAVIG Bootstrap — Full Ubuntu Server Hardening + Stack Deploy
# Target: Ubuntu 25.10 (ubuntu-node)
# Created: 2026-02-13
# Run as: void (with sudo NOPASSWD)
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
LOG="/var/log/navig_bootstrap.log"
NAVIG_USER="navig"
NAVIG_HOME="/opt/navig"

# ── Colors ────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YEL='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
info() { echo -e "${CYAN}[i]${NC} $*"; }
warn() { echo -e "${YEL}[!]${NC} $*"; }
fail() { echo -e "${RED}[✗]${NC} $*"; exit 1; }
step() { echo -e "\n${CYAN}═══ $* ═══${NC}"; }

sudo tee "$LOG" > /dev/null <<< "=== NAVIG Bootstrap $(date -Is) ==="
exec > >(tee -a "$LOG") 2>&1

# ═══════════════════════════════════════════════════════════════
# PHASE 1 — BASE SYSTEM VALIDATION
# ═══════════════════════════════════════════════════════════════
step "PHASE 1 — Base System Validation"

info "OS: $(lsb_release -d -s 2>/dev/null || cat /etc/os-release | grep PRETTY | cut -d= -f2)"
info "Kernel: $(uname -r)"
info "CPU: $(lscpu | grep 'Model name' | sed 's/.*: *//')"
info "Cores: $(nproc)"
info "RAM: $(free -h | awk '/Mem/{print $2}')"
info "Disk /: $(df -h / | awk 'NR==2{print $2" total, "$4" free, "$5" used"}')"
info "Swap: $(free -h | awk '/Swap/{print $2}')"

# Clock
info "Time: $(timedatectl show --property=Timezone --value) — Synced: $(timedatectl show --property=NTPSynchronized --value)"

# Hostname
CURRENT_HOSTNAME=$(hostname)
if [ "$CURRENT_HOSTNAME" != "navig-server" ]; then
    info "Setting hostname from '$CURRENT_HOSTNAME' to 'navig-server'"
    sudo hostnamectl set-hostname navig-server
    # Update /etc/hosts
    if ! grep -q "navig-server" /etc/hosts; then
        sudo sed -i "s/127.0.1.1.*/127.0.1.1 navig-server/" /etc/hosts 2>/dev/null || \
        echo "127.0.1.1 navig-server" | sudo tee -a /etc/hosts > /dev/null
    fi
    ok "Hostname set to navig-server"
else
    ok "Hostname already navig-server"
fi

# Swap check (already 4GB)
SWAP_SIZE=$(free -m | awk '/Swap/{print $2}')
if [ "$SWAP_SIZE" -lt 1000 ]; then
    warn "Swap is ${SWAP_SIZE}MB — creating 4GB swapfile"
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
    ok "4GB swap created"
else
    ok "Swap: ${SWAP_SIZE}MB — sufficient"
fi

# Full system update
step "System Update"
sudo apt-get update -qq
sudo apt-get upgrade -y -qq
sudo apt-get dist-upgrade -y -qq
sudo apt-get autoremove -y -qq
ok "System fully updated"

# ═══════════════════════════════════════════════════════════════
# PHASE 2 — SECURITY BASELINE
# ═══════════════════════════════════════════════════════════════
step "PHASE 2 — Security Baseline"

# Create navig system user
if ! id "$NAVIG_USER" &>/dev/null; then
    sudo useradd -r -m -d "$NAVIG_HOME" -s /bin/bash -G docker "$NAVIG_USER" 2>/dev/null || \
    sudo useradd -r -m -d "$NAVIG_HOME" -s /bin/bash "$NAVIG_USER"
    ok "User '$NAVIG_USER' created"
else
    ok "User '$NAVIG_USER' already exists"
fi

# SSH hardening
step "SSH Hardening"
SSHD_CONF="/etc/ssh/sshd_config"
SSHD_DROP="/etc/ssh/sshd_config.d/99-navig-hardening.conf"

sudo tee "$SSHD_DROP" > /dev/null <<'SSHEOF'
# NAVIG SSH Hardening — 2026-02-13
# Disable root login
PermitRootLogin no
# Key-only authentication
PasswordAuthentication no
PubkeyAuthentication yes
# Disable empty passwords
PermitEmptyPasswords no
# Disable X11
X11Forwarding no
# Max auth tries
MaxAuthTries 4
# Alive interval
ClientAliveInterval 300
ClientAliveCountMax 2
SSHEOF
sudo chmod 644 "$SSHD_DROP"

# Validate sshd config before restarting
if sudo sshd -t 2>&1; then
    sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd 2>/dev/null || true
    ok "SSH hardened (key-only, no root login)"
else
    warn "SSH config validation failed — rolling back"
    sudo rm -f "$SSHD_DROP"
fi

# UFW
step "Firewall (UFW)"
sudo apt-get install -y -qq ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh comment "SSH"
# Allow from local network for dev access
sudo ufw allow from 10.0.0.0/24 to any port 22 comment "SSH LAN"
sudo ufw --force enable
ok "UFW enabled — SSH allowed"
sudo ufw status verbose

# Fail2ban
step "Fail2ban"
sudo apt-get install -y -qq fail2ban
sudo tee /etc/fail2ban/jail.local > /dev/null <<'F2BEOF'
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5
backend = systemd

[sshd]
enabled = true
port = ssh
filter = sshd
maxretry = 3
bantime = 2h
F2BEOF
sudo systemctl enable --now fail2ban
sudo systemctl restart fail2ban
ok "Fail2ban configured (SSH: 3 tries, 2h ban)"

# Unattended upgrades
step "Automatic Security Updates"
sudo apt-get install -y -qq unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades 2>/dev/null || true
# Enable automatic security updates
sudo tee /etc/apt/apt.conf.d/50unattended-upgrades-navig > /dev/null <<'UUEOF'
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
    "${distro_id}ESMApps:${distro_codename}-apps-security";
};
Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
UUEOF
ok "Unattended security upgrades enabled"

# ═══════════════════════════════════════════════════════════════
# PHASE 3 — SYSTEM TUNING FOR LLM + AGENTS
# ═══════════════════════════════════════════════════════════════
step "PHASE 3 — System Tuning"

# Sysctl tuning
sudo tee /etc/sysctl.d/99-navig.conf > /dev/null <<'SYSEOF'
# NAVIG Tuning — 2026-02-13

# Reduce swap aggressiveness (prefer RAM for LLM inference)
vm.swappiness = 10

# Increase max file descriptors (containers + many connections)
fs.file-max = 2097152

# Increase inotify limits (file watching in containers)
fs.inotify.max_user_watches = 524288
fs.inotify.max_user_instances = 1024

# Network tuning
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.core.netdev_max_backlog = 65535

# TCP keepalive (faster dead connection detection)
net.ipv4.tcp_keepalive_time = 600
net.ipv4.tcp_keepalive_intvl = 60
net.ipv4.tcp_keepalive_probes = 3
SYSEOF
sudo sysctl -p /etc/sysctl.d/99-navig.conf
ok "Sysctl tuned (swappiness=10, file-max=2M, inotify raised)"

# File descriptor limits
sudo tee /etc/security/limits.d/99-navig.conf > /dev/null <<'LIMEOF'
# NAVIG — raise file descriptor limits
*       soft    nofile      65536
*       hard    nofile      1048576
root    soft    nofile      65536
root    hard    nofile      1048576
LIMEOF
ok "File descriptor limits raised (65536 soft, 1M hard)"

# Journald retention
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/navig.conf > /dev/null <<'JDEOF'
[Journal]
SystemMaxUse=500M
SystemKeepFree=1G
MaxRetentionSec=30day
Compress=yes
Storage=persistent
JDEOF
sudo systemctl restart systemd-journald
ok "Journald: 500MB max, 30d retention, persistent"

# Install essential tools
step "Install Essential Packages"
sudo apt-get install -y -qq \
    htop curl wget jq git unzip \
    build-essential ca-certificates \
    gnupg lsb-release software-properties-common \
    net-tools iotop ncdu tmux
ok "Essential packages installed"

# CPU governor
GOV=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "unknown")
info "CPU governor: $GOV (keeping default — safe for mixed workloads)"

# ═══════════════════════════════════════════════════════════════
# PHASE 4 — DOCKER
# ═══════════════════════════════════════════════════════════════
step "PHASE 4 — Docker Environment"

if ! command -v docker &>/dev/null; then
    info "Installing Docker..."
    # Add Docker GPG key
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    # Add Docker repo (use noble for 25.10 compatibility)
    CODENAME=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-noble}")
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $CODENAME stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update -qq
    sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    ok "Docker installed"
else
    ok "Docker already installed: $(docker --version)"
fi

# Add users to docker group
sudo usermod -aG docker void 2>/dev/null || true
sudo usermod -aG docker "$NAVIG_USER" 2>/dev/null || true

# Docker daemon config
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json > /dev/null <<'DKEOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "20m",
    "max-file": "5"
  },
  "default-address-pools": [
    {"base": "172.20.0.0/16", "size": 24}
  ],
  "features": {
    "buildkit": true
  },
  "live-restore": true,
  "default-ulimits": {
    "nofile": {
      "Name": "nofile",
      "Hard": 65536,
      "Soft": 65536
    }
  }
}
DKEOF

sudo systemctl enable docker
sudo systemctl restart docker
ok "Docker daemon configured (log rotation, buildkit, live-restore)"

# Verify
info "Docker: $(docker --version)"
info "Compose: $(docker compose version)"

# Ensure docker is NOT on public IPs
if sudo ss -tlnp | grep -E ':2375|:2376' | grep -v '127.0.0.1' | grep -q .; then
    warn "Docker API exposed on public interface! Fixing..."
    sudo systemctl stop docker.socket 2>/dev/null || true
else
    ok "Docker API not publicly exposed"
fi

# ═══════════════════════════════════════════════════════════════
# PHASE 5 — NAVIG FILESYSTEM STRUCTURE
# ═══════════════════════════════════════════════════════════════
step "PHASE 5 — NAVIG Filesystem"

for dir in "$NAVIG_HOME" "$NAVIG_HOME/runtime" "$NAVIG_HOME/models" \
           "$NAVIG_HOME/logs" "$NAVIG_HOME/backups" "$NAVIG_HOME/data" \
           "$NAVIG_HOME/config"; do
    sudo mkdir -p "$dir"
done
sudo chown -R "$NAVIG_USER":"$NAVIG_USER" "$NAVIG_HOME"
sudo chmod 750 "$NAVIG_HOME"
ok "Filesystem structure created at $NAVIG_HOME"
ls -la "$NAVIG_HOME/"

# ═══════════════════════════════════════════════════════════════
# PHASE 6 — DEPLOY STACK (DOCKER COMPOSE)
# ═══════════════════════════════════════════════════════════════
step "PHASE 6 — Deploy NAVIG Stack"

# .env file
sudo -u "$NAVIG_USER" tee "$NAVIG_HOME/.env" > /dev/null <<'ENVEOF'
# NAVIG Stack Configuration
# Generated: 2026-02-13

# Postgres
POSTGRES_USER=navig
POSTGRES_PASSWORD=CHANGE_ME
POSTGRES_DB=navig

# Redis
REDIS_PASSWORD=CHANGE_ME

# Ollama
OLLAMA_HOST=0.0.0.0
OLLAMA_MODELS=/root/.ollama/models

# Gateway
GATEWAY_PORT=8789
GATEWAY_SECRET=CHANGE_ME

# Ports (all bind 127.0.0.1 only)
DASHBOARD_PORT=3000
ENVEOF
sudo chmod 600 "$NAVIG_HOME/.env"

# .env.example
sudo -u "$NAVIG_USER" tee "$NAVIG_HOME/.env.example" > /dev/null <<'ENVXEOF'
POSTGRES_USER=navig
POSTGRES_PASSWORD=CHANGE_ME
POSTGRES_DB=navig
REDIS_PASSWORD=CHANGE_ME
OLLAMA_HOST=0.0.0.0
GATEWAY_PORT=8789
GATEWAY_SECRET=CHANGE_ME
DASHBOARD_PORT=3000
ENVXEOF

# Docker Compose
sudo -u "$NAVIG_USER" tee "$NAVIG_HOME/docker-compose.yml" > /dev/null <<'DCEOF'
# NAVIG Stack — Docker Compose
# Generated: 2026-02-13
version: "3.9"

networks:
  navig-net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.1.0/24

volumes:
  postgres_data:
  redis_data:
  ollama_models:

services:
  # ── Postgres + pgvector ──────────────────────────
  postgres:
    image: pgvector/pgvector:pg16
    container_name: navig-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init-db.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
    ports:
      - "127.0.0.1:5432:5432"
    networks:
      - navig-net
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          memory: 512M

  # ── Redis ────────────────────────────────────────
  redis:
    image: redis:7-alpine
    container_name: navig-redis
    restart: unless-stopped
    command: >
      redis-server
      --requirepass ${REDIS_PASSWORD}
      --maxmemory 256mb
      --maxmemory-policy allkeys-lru
      --appendonly yes
    volumes:
      - redis_data:/data
    ports:
      - "127.0.0.1:6379:6379"
    networks:
      - navig-net
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          memory: 300M

  # ── Ollama (CPU) ─────────────────────────────────
  ollama:
    image: ollama/ollama:latest
    container_name: navig-ollama
    restart: unless-stopped
    environment:
      OLLAMA_HOST: "0.0.0.0"
      OLLAMA_NUM_PARALLEL: "2"
      OLLAMA_MAX_LOADED_MODELS: "1"
    volumes:
      - ollama_models:/root/.ollama
    ports:
      - "127.0.0.1:11434:11434"
    networks:
      - navig-net
    healthcheck:
      test: ["CMD-SHELL", "ollama list >/dev/null 2>&1 || exit 1"]
      interval: 15s
      timeout: 10s
      retries: 3
      start_period: 30s
    deploy:
      resources:
        limits:
          memory: 10G
        reservations:
          memory: 4G
DCEOF

# Init SQL for pgvector
sudo -u "$NAVIG_USER" tee "$NAVIG_HOME/init-db.sql" > /dev/null <<'SQLEOF'
-- NAVIG Database Initialization
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Audit log table for tool-gateway
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agent_id TEXT,
    tool_name TEXT NOT NULL,
    action TEXT NOT NULL,
    input_hash TEXT,
    result_status TEXT,
    duration_ms INTEGER,
    metadata JSONB
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_tool ON audit_log(tool_name);

-- Agent profiles
CREATE TABLE IF NOT EXISTS agent_profiles (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    config JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Knowledge embeddings
CREATE TABLE IF NOT EXISTS knowledge (
    id BIGSERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    embedding vector(1024),
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_knowledge_embedding ON knowledge USING ivfflat (embedding vector_cosine_ops);
SQLEOF

ok "Docker Compose + .env + DB init created"

# Start the stack
step "Starting NAVIG Stack"
cd "$NAVIG_HOME"
sudo -u "$NAVIG_USER" docker compose --env-file .env up -d
ok "Stack started"

# Wait for health
info "Waiting for services to become healthy..."
sleep 10
for svc in navig-postgres navig-redis navig-ollama; do
    TRIES=0
    while [ $TRIES -lt 30 ]; do
        STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "missing")
        if [ "$STATUS" = "healthy" ]; then
            ok "$svc is healthy"
            break
        fi
        TRIES=$((TRIES+1))
        sleep 3
    done
    if [ "$STATUS" != "healthy" ]; then
        warn "$svc not healthy after 90s (status: $STATUS)"
    fi
done

# Pull a model
step "Pulling Ollama Model"
info "Pulling qwen2.5:7b-instruct (this may take a while on first run)..."
docker exec navig-ollama ollama pull qwen2.5:7b-instruct 2>&1 || \
    docker exec navig-ollama ollama pull qwen2.5:3b-instruct 2>&1 || \
    warn "Model pull failed — try manually: docker exec navig-ollama ollama pull qwen2.5:7b-instruct"

# Quick inference test
info "Testing inference..."
INFER=$(docker exec navig-ollama ollama run qwen2.5:7b-instruct "Say hello in one word" 2>&1 | head -1) || true
if [ -n "$INFER" ]; then
    ok "Inference works: $INFER"
else
    warn "Inference test returned empty — model may still be loading"
fi

# ═══════════════════════════════════════════════════════════════
# PHASE 7 — SYSTEMD INTEGRATION
# ═══════════════════════════════════════════════════════════════
step "PHASE 7 — Systemd Integration"

sudo tee /etc/systemd/system/navig.service > /dev/null <<'SVCEOF'
[Unit]
Description=NAVIG Agent Infrastructure Stack
Documentation=https://github.com/navig-run/core
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=navig
Group=navig
WorkingDirectory=/opt/navig
ExecStart=/usr/bin/docker compose --env-file /opt/navig/.env up -d --remove-orphans
ExecStop=/usr/bin/docker compose --env-file /opt/navig/.env down
ExecReload=/usr/bin/docker compose --env-file /opt/navig/.env up -d --remove-orphans
TimeoutStartSec=120
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable navig.service
ok "navig.service created and enabled"

# ═══════════════════════════════════════════════════════════════
# HEALTHCHECK SCRIPT
# ═══════════════════════════════════════════════════════════════
step "Healthcheck Script"

sudo -u "$NAVIG_USER" tee "$NAVIG_HOME/navig_healthcheck.sh" > /dev/null <<'HCEOF'
#!/usr/bin/env bash
# NAVIG Stack Healthcheck
set -euo pipefail

# Load credentials from stack .env
ENVFILE="$(dirname "$0")/.env"
if [ -f "$ENVFILE" ]; then
  set -a; . "$ENVFILE"; set +a
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
PASS=0; FAIL=0

check() {
    local name="$1" cmd="$2"
    if eval "$cmd" &>/dev/null; then
        echo -e "${GREEN}[✓]${NC} $name"
        PASS=$((PASS+1))
    else
        echo -e "${RED}[✗]${NC} $name"
        FAIL=$((FAIL+1))
    fi
}

echo "═══ NAVIG Healthcheck $(date) ═══"
check "Docker running"       "systemctl is-active docker"
check "Postgres healthy"     "docker exec navig-postgres pg_isready -U navig"
check "Redis responds"       "docker exec navig-redis redis-cli -a \"${REDIS_PASSWORD:-CHANGE_ME}\" ping | grep -q PONG"
check "Ollama reachable"     "curl -sf http://127.0.0.1:11434/api/tags"
check "Postgres port 5432"   "ss -tlnp | grep -q ':5432'"
check "Redis port 6379"      "ss -tlnp | grep -q ':6379'"
check "Ollama port 11434"    "ss -tlnp | grep -q ':11434'"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
HCEOF
sudo chmod +x "$NAVIG_HOME/navig_healthcheck.sh"
ok "Healthcheck script created at $NAVIG_HOME/navig_healthcheck.sh"

# ═══════════════════════════════════════════════════════════════
# LOGGING + MONITORING BASELINE
# ═══════════════════════════════════════════════════════════════
step "Logging & Monitoring"

# Ensure persistent journal
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal 2>/dev/null || true
ok "Persistent journald logging enabled"

# Daily backup script (placeholder)
sudo -u "$NAVIG_USER" tee "$NAVIG_HOME/backup_daily.sh" > /dev/null <<'BKEOF'
#!/usr/bin/env bash
# NAVIG Daily Backup
set -euo pipefail
BACKUP_DIR="/opt/navig/backups"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

# Postgres dump
docker exec navig-postgres pg_dump -U navig navig | gzip > "$BACKUP_DIR/navig_db_$DATE.sql.gz"

# Config backup
tar czf "$BACKUP_DIR/navig_config_$DATE.tar.gz" \
    /opt/navig/.env \
    /opt/navig/docker-compose.yml \
    /opt/navig/init-db.sql \
    2>/dev/null || true

# Rotate: keep last 7 days
find "$BACKUP_DIR" -name "*.gz" -mtime +7 -delete

echo "[$(date)] Backup complete: $BACKUP_DIR/navig_db_$DATE.sql.gz"
BKEOF
sudo chmod +x "$NAVIG_HOME/backup_daily.sh"

# Add cron job
(sudo -u "$NAVIG_USER" crontab -l 2>/dev/null; echo "0 3 * * * /opt/navig/backup_daily.sh >> /opt/navig/logs/backup.log 2>&1") | \
    sudo -u "$NAVIG_USER" crontab -
ok "Daily backup cron set (3:00 AM)"

# ═══════════════════════════════════════════════════════════════
# PHASE 8 — NETWORK SANITY
# ═══════════════════════════════════════════════════════════════
step "PHASE 8 — Network Sanity"

info "Listening ports:"
sudo ss -tlnp | grep -E '(5432|6379|11434|8789|22)' || true

info "Docker port bindings:"
docker ps --format "table {{.Names}}\t{{.Ports}}" 2>/dev/null || true

# Verify nothing is on 0.0.0.0
EXPOSED=$(sudo ss -tlnp | grep -E '(5432|6379|11434)' | grep '0.0.0.0' || true)
if [ -n "$EXPOSED" ]; then
    warn "Some services may be on 0.0.0.0 — UFW still blocks external access"
else
    ok "All NAVIG services bound to 127.0.0.1"
fi
ok "Network sanity check complete"

# ═══════════════════════════════════════════════════════════════
# FINAL VALIDATION REPORT
# ═══════════════════════════════════════════════════════════════
step "FINAL VALIDATION REPORT"

echo ""
echo "═══════════════════════════════════════════════"
echo " NAVIG Server Bootstrap Complete"
echo "═══════════════════════════════════════════════"
echo ""
echo "OS:       $(lsb_release -d -s 2>/dev/null)"
echo "Kernel:   $(uname -r)"
echo "Hostname: $(hostname)"
echo "Docker:   $(docker --version 2>/dev/null | head -1)"
echo "Compose:  $(docker compose version 2>/dev/null)"
echo ""
echo "── Services ──"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || true
echo ""
echo "── Firewall ──"
sudo ufw status numbered 2>/dev/null || true
echo ""
echo "── Security ──"
echo "SSH: key-only, no root login"
echo "Fail2ban: active (SSH: 3 tries, 2h ban)"
echo "Auto-updates: security patches"
echo ""
echo "── Commands ──"
echo "Start:    sudo systemctl start navig"
echo "Stop:     sudo systemctl stop navig"
echo "Status:   sudo systemctl status navig"
echo "Logs:     journalctl -u navig -f"
echo "Health:   /opt/navig/navig_healthcheck.sh"
echo "Backup:   /opt/navig/backup_daily.sh"
echo ""
echo "── Ports (127.0.0.1 only) ──"
echo "5432  — Postgres (pgvector)"
echo "6379  — Redis"
echo "11434 — Ollama"
echo "22    — SSH (public)"
echo ""
echo "── Model ──"
docker exec navig-ollama ollama list 2>/dev/null || echo "(check manually)"
echo ""
echo "── Files Created ──"
echo "/opt/navig/docker-compose.yml"
echo "/opt/navig/.env"
echo "/opt/navig/.env.example"
echo "/opt/navig/init-db.sql"
echo "/opt/navig/navig_healthcheck.sh"
echo "/opt/navig/backup_daily.sh"
echo "/etc/systemd/system/navig.service"
echo "/etc/ssh/sshd_config.d/99-navig-hardening.conf"
echo "/etc/sysctl.d/99-navig.conf"
echo "/etc/security/limits.d/99-navig.conf"
echo "/etc/docker/daemon.json"
echo "/etc/fail2ban/jail.local"
echo ""
echo "═══════════════════════════════════════════════"
echo " Bootstrap complete — $(date)"
echo "═══════════════════════════════════════════════"
