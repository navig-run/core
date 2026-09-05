#!/usr/bin/env bash
set -euo pipefail

# NAVIG Operational Factory installer for Ubuntu/Debian
# Usage:
#   sudo bash install_navig_factory_server.sh

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi

APP_USER="${APP_USER:-navig}"
APP_GROUP="${APP_GROUP:-navig}"
APP_HOME="${APP_HOME:-/opt/navig/factory}"
SRC_DIR="${SRC_DIR:-$(pwd)/deploy/operational-factory}"
CLI_SRC_DIR="${CLI_SRC_DIR:-$(pwd)}"
TELEGRAM_TOKEN="${NAVIG_TELEGRAM_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}"
SERVICE_NAME="navig-factory"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "Source dir not found: $SRC_DIR" >&2
  echo "Run this from navig-core root or set SRC_DIR=/path/to/deploy/operational-factory" >&2
  exit 1
fi

echo "[1/8] Installing OS dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl gnupg lsb-release git jq python3 python3-pip python3-venv autossh

if ! command -v docker >/dev/null 2>&1; then
  echo "[2/8] Installing Docker Engine..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" \
    | tee /etc/apt/sources.list.d/docker.list >/dev/null
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

echo "[3/8] Creating service user..."
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/navig --shell /usr/sbin/nologin "$APP_USER"
fi
usermod -aG docker "$APP_USER" || true

echo "[4/8] Deploying app files to $APP_HOME..."
mkdir -p "$APP_HOME"
rsync -a --delete "$SRC_DIR/" "$APP_HOME/"
chown -R "$APP_USER:$APP_GROUP" "$APP_HOME"

if [[ ! -f "$APP_HOME/.env" ]]; then
  cp "$APP_HOME/.env.example" "$APP_HOME/.env"
  sed -i "s/CHANGE_ME_STRONG/$(openssl rand -hex 16)/g" "$APP_HOME/.env"
  sed -i "s/CHANGE_ME_REDIS/$(openssl rand -hex 12)/g" "$APP_HOME/.env"
fi

echo "[5/8] Preparing systemd unit..."
cat >/etc/systemd/system/${SERVICE_NAME}.service <<UNIT
[Unit]
Description=NAVIG Operational Factory (Docker Compose)
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${APP_HOME}
ExecStart=/usr/bin/docker compose --env-file ${APP_HOME}/.env up -d --build
ExecStop=/usr/bin/docker compose --env-file ${APP_HOME}/.env down
TimeoutStartSec=0
User=${APP_USER}
Group=${APP_GROUP}

[Install]
WantedBy=multi-user.target
UNIT

echo "[6/8] Starting service..."
systemctl daemon-reload
systemctl enable --now ${SERVICE_NAME}

echo "[7/8] Pulling primary model..."
/usr/bin/docker compose --env-file ${APP_HOME}/.env -f ${APP_HOME}/docker-compose.yml exec -T ollama ollama pull qwen2.5:7b-instruct || true

echo "[7.5/8] Applying migrations + seed data..."
/usr/bin/docker compose --env-file ${APP_HOME}/.env -f ${APP_HOME}/docker-compose.yml run --rm navig-runtime python -m app.migrate
/usr/bin/docker compose --env-file ${APP_HOME}/.env -f ${APP_HOME}/docker-compose.yml run --rm navig-runtime python -m app.seed

echo "[8/8] Validation..."
/usr/bin/docker compose --env-file ${APP_HOME}/.env -f ${APP_HOME}/docker-compose.yml ps
systemctl --no-pager status ${SERVICE_NAME} || true

if [[ -n "$TELEGRAM_TOKEN" ]]; then
  echo "[8.5/8] Configuring Telegram token for NAVIG CLI daemon..."
  if [[ -d "$CLI_SRC_DIR" && -f "$CLI_SRC_DIR/pyproject.toml" ]]; then
    sudo -u "$APP_USER" bash -lc "python3 -m pip install --user --upgrade '$CLI_SRC_DIR'"
  else
    sudo -u "$APP_USER" bash -lc "python3 -m pip install --user --upgrade navig"
  fi

  sudo -u "$APP_USER" bash -lc "mkdir -p ~/.navig && cat > ~/.navig/.env <<'EOF'
TELEGRAM_BOT_TOKEN=$TELEGRAM_TOKEN
EOF
chmod 600 ~/.navig/.env"

  sudo -u "$APP_USER" bash -lc "if [[ ! -f ~/.navig/config.yaml ]] || ! grep -q 'bot_token:' ~/.navig/config.yaml 2>/dev/null; then
cat > ~/.navig/config.yaml <<'EOF'
telegram:
  bot_token: \"$TELEGRAM_TOKEN\"
  allowed_users: []
  allowed_groups: []
  session_isolation: true
  group_activation_mode: \"mention\"
EOF
fi"

  sudo -u "$APP_USER" bash -lc "export PATH=\$HOME/.local/bin:\$PATH; export TELEGRAM_BOT_TOKEN='$TELEGRAM_TOKEN'; navig service install --bot --gateway --scheduler --no-start || true; navig service start || true"
fi

echo
cat <<EOF
Install complete.

Service: ${SERVICE_NAME}
App dir: ${APP_HOME}
Commands:
  systemctl status ${SERVICE_NAME}
  systemctl restart ${SERVICE_NAME}
  docker compose --env-file ${APP_HOME}/.env -f ${APP_HOME}/docker-compose.yml logs -f --tail=200

Dashboard: http://127.0.0.1:8088
EOF
