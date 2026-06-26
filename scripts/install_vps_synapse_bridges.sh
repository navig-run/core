#!/usr/bin/env bash
# ==============================================================================
# NAVIG — VPS Synapse + mautrix bridges installer
# Ubuntu 22.04 / 24.04 LTS
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/navig/navig-core/main/scripts/install_vps_synapse_bridges.sh | bash
# or locally:
#   bash scripts/install_vps_synapse_bridges.sh
#
# What this does:
#   1. Installs Docker + Certbot
#   2. Generates Postgres password + Synapse shared secret
#   3. Runs Synapse to auto-generate homeserver.yaml
#   4. Patches homeserver.yaml for Postgres + bridge app_service entries
#   5. Generates bridge configs from templates (fills in domain + Matrix user)
#   6. Generates registration.yaml for each bridge
#   7. Starts full stack
#   8. Sets up nginx reverse proxy with Let's Encrypt TLS
#   9. Creates your NAVIG Matrix bot user
#   10. Prints navig config commands to run on your local machine
# ==============================================================================

set -euo pipefail
IFS=$'\n\t'

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Prerequisites check ────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && die "Run as root: sudo bash $0"
command -v apt-get >/dev/null 2>&1 || die "This installer requires Ubuntu/Debian (apt-get)."

# ── Interactive prompts ────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}╔═══════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  NAVIG — Matrix Synapse + Bridge Installer           ║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${CYAN}─── TLS / Domain setup ────────────────────────────────────${NC}"
echo -e "  Option A: ${GREEN}Cloudflare tunnel${NC} — no domain, no open ports needed"
echo -e "            (You already use Cloudflare for navig-deck? Use this.)"
echo -e "  Option B: ${GREEN}Domain + Let's Encrypt${NC} — public domain, nginx + certbot"
echo ""
read -rp "$(echo -e "${YELLOW}Use Cloudflare tunnel? [Y/n]:${NC} ")" _CF_CHOICE
_CF_CHOICE="${_CF_CHOICE:-Y}"
USE_CF_TUNNEL=false
if [[ "${_CF_CHOICE,,}" == "y" || "${_CF_CHOICE,,}" == "yes" ]]; then
  USE_CF_TUNNEL=true
  echo ""
  info "Cloudflare tunnel mode selected."
  info "After this installer finishes, expose Synapse with:"
  info "  cloudflared tunnel --url http://localhost:8008"
  info "Then save the tunnel URL: navig config set comms.matrix.homeserver_url <url>"
  echo ""
  read -rp "$(echo -e "${YELLOW}Server name for Matrix IDs (e.g. navig.local or yourdomain.com):${NC} ")" DOMAIN
  [[ -z "$DOMAIN" ]] && DOMAIN="navig.local"
  DOMAIN="${DOMAIN,,}"
  LE_EMAIL=""
else
  read -rp "$(echo -e "${YELLOW}Your domain (e.g. yourdomain.com):${NC} ")" DOMAIN
  [[ -z "$DOMAIN" ]] && die "Domain is required."
  DOMAIN="${DOMAIN,,}"  # lowercase

  read -rp "$(echo -e "${YELLOW}Email for Let's Encrypt TLS cert:${NC} ")" LE_EMAIL
  [[ -z "$LE_EMAIL" ]] && die "Email is required for TLS cert."
fi

read -rp "$(echo -e "${YELLOW}Matrix username for your NAVIG bot (default: navig):${NC} ")" NAVIG_MATRIX_USER
NAVIG_MATRIX_USER="${NAVIG_MATRIX_USER:-navig}"

read -rsp "$(echo -e "${YELLOW}Password for @${NAVIG_MATRIX_USER}:${DOMAIN}:${NC} ")" NAVIG_MATRIX_PASS
echo ""
[[ -z "$NAVIG_MATRIX_PASS" ]] && die "Password cannot be empty."

# Ask about Telegram bridge API credentials
echo ""
echo -e "${CYAN}─── Telegram bridge (personal account) ───────────────────${NC}"
echo -e "  Get api_id and api_hash from: ${YELLOW}https://my.telegram.org/apps${NC}"
read -rp "$(echo -e "${YELLOW}Telegram API ID (leave blank to skip for now):${NC} ")" TG_API_ID
read -rp "$(echo -e "${YELLOW}Telegram API Hash (leave blank to skip):${NC} ")" TG_API_HASH

# ── Paths ─────────────────────────────────────────────────────────────────────
DEPLOY_DIR="/opt/navig/synapse"
BRIDGES_DIR="${DEPLOY_DIR}/bridges"
DATA_DIR="${DEPLOY_DIR}/data"
ENV_FILE="${DEPLOY_DIR}/.env"

mkdir -p "${DEPLOY_DIR}" "${DATA_DIR}" \
  "${BRIDGES_DIR}/telegram" \
  "${BRIDGES_DIR}/whatsapp" \
  "${BRIDGES_DIR}/meta"

# ── Generate secrets ──────────────────────────────────────────────────────────
gen_secret() { python3 -c "import secrets; print(secrets.token_hex(32))"; }
POSTGRES_PASSWORD="$(gen_secret)"
SYNAPSE_SHARED_SECRET="$(gen_secret)"

# ── Write .env (restricted permissions from creation) ────────────────────────
(umask 077; cat > "${ENV_FILE}" <<EOF
DOMAIN=${DOMAIN}
SYNAPSE_SERVER_NAME=${DOMAIN}
SYNAPSE_PORT=8008
SYNAPSE_REPORT_STATS=no
SYNAPSE_REGISTRATION_SHARED_SECRET=${SYNAPSE_SHARED_SECRET}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
LETSENCRYPT_EMAIL=${LE_EMAIL}
EOF
)
success "Secrets written to ${ENV_FILE} (mode 600)"

# ── Install Docker ─────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  info "Installing Docker..."
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg lsb-release
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
  systemctl enable --now docker
  success "Docker installed."
else
  success "Docker already installed."
fi

# ── Install nginx + certbot (domain mode only) ────────────────────────────────
if [[ "$USE_CF_TUNNEL" == "false" ]]; then
  if ! command -v nginx >/dev/null 2>&1; then
    info "Installing nginx..."
    apt-get install -y -qq nginx
  fi
  if ! command -v certbot >/dev/null 2>&1; then
    info "Installing certbot..."
    apt-get install -y -qq certbot python3-certbot-nginx
  fi
else
  info "Cloudflare tunnel mode — skipping nginx/certbot."
fi

# ── Copy docker-compose.yml ───────────────────────────────────────────────────
# Determine source — monorepo checkout or downloaded
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_SRC="${SCRIPT_DIR}/../deploy/synapse/docker-compose.yml"
TMPL_DIR="${SCRIPT_DIR}/../deploy/synapse/bridges"

if [[ -f "${COMPOSE_SRC}" ]]; then
  cp "${COMPOSE_SRC}" "${DEPLOY_DIR}/docker-compose.yml"
  info "Using docker-compose.yml from repo."
else
  # Download from GitHub
  curl -fsSL "https://raw.githubusercontent.com/navig/navig-core/main/deploy/synapse/docker-compose.yml" \
    -o "${DEPLOY_DIR}/docker-compose.yml"
  info "Downloaded docker-compose.yml from GitHub."
fi

# ── Generate Synapse homeserver.yaml ──────────────────────────────────────────
if [[ ! -f "${DATA_DIR}/homeserver.yaml" ]]; then
  info "Generating Synapse homeserver.yaml..."
  docker run --rm \
    -e "SYNAPSE_SERVER_NAME=${DOMAIN}" \
    -e "SYNAPSE_REPORT_STATS=no" \
    -v "${DATA_DIR}:/data" \
    matrixdotorg/synapse:latest generate
fi

# Patch homeserver.yaml: Postgres, registration off, shared secret, bridges
HOMESERVER="${DATA_DIR}/homeserver.yaml"
python3 - <<PYEOF
import re, pathlib

path = pathlib.Path("${HOMESERVER}")
txt = path.read_text()

patches = [
    # Postgres connection instead of SQLite
    (r'(?m)^#?\s*database:.*\n(.*\n)*?.*name: .*\n', """database:
  name: psycopg2
  args:
    user: synapse
    password: ${POSTGRES_PASSWORD}
    database: synapse
    host: postgres
    cp_min: 5
    cp_max: 10
"""),
    # Disable public registration
    (r'(?m)^enable_registration:.*$', 'enable_registration: false'),
    # Set shared secret
    (r'(?m)^#?registration_shared_secret:.*$',
     'registration_shared_secret: "${SYNAPSE_SHARED_SECRET}"'),
]

for pattern, replacement in patches:
    if re.search(pattern, txt):
        txt = re.sub(pattern, replacement, txt, count=1)
    else:
        txt += '\n' + replacement.split('\n')[0] + '\n'

# Add app_service_config_files block if missing
if 'app_service_config_files' not in txt:
    txt += """
app_service_config_files:
  - /data/bridges/telegram.yaml
  - /data/bridges/whatsapp.yaml
  - /data/bridges/meta.yaml
"""

path.write_text(txt)
print("homeserver.yaml patched.")
PYEOF

success "homeserver.yaml configured."

# ── Write bridge configs from templates ──────────────────────────────────────
fill_template() {
  local src="$1" dst="$2"
  sed \
    -e "s|PLACEHOLDER_DOMAIN|${DOMAIN}|g" \
    -e "s|PLACEHOLDER_MATRIX_USER|${NAVIG_MATRIX_USER}|g" \
    -e "s|PLACEHOLDER_TG_API_ID|${TG_API_ID:-YOUR_API_ID}|g" \
    -e "s|PLACEHOLDER_TG_API_HASH|${TG_API_HASH:-YOUR_API_HASH}|g" \
    "$src" > "$dst"
}

for bridge in telegram whatsapp meta; do
  SRC_TMPL=""
  if [[ -f "${TMPL_DIR}/${bridge}/config.yaml" ]]; then
    SRC_TMPL="${TMPL_DIR}/${bridge}/config.yaml"
  else
    # Download template
    SRC_TMPL="/tmp/navig-bridge-${bridge}-config.yaml"
    curl -fsSL \
      "https://raw.githubusercontent.com/navig/navig-core/main/deploy/synapse/bridges/${bridge}/config.yaml" \
      -o "${SRC_TMPL}"
  fi
  fill_template "${SRC_TMPL}" "${BRIDGES_DIR}/${bridge}/config.yaml"
  info "Bridge config written: ${bridge}"
done

# ── Generate registration.yaml for each bridge ────────────────────────────────
info "Generating bridge registration files..."

# Telegram bridge generates its own registration on first run
# We start it briefly to generate, then stop it
for bridge in telegram whatsapp meta; do
  if [[ ! -f "${BRIDGES_DIR}/${bridge}/registration.yaml" ]]; then
    info "Generating registration for bridge: ${bridge}..."
    docker run --rm \
      -v "${BRIDGES_DIR}/${bridge}:/data" \
      "dock.mau.dev/mautrix/${bridge}:latest" \
      generate-registration 2>/dev/null || true
    # Fallback: create minimal placeholder so Synapse doesn't fail to start
    if [[ ! -f "${BRIDGES_DIR}/${bridge}/registration.yaml" ]]; then
      BRIDGE_TOKEN="$(gen_secret)"
      AS_TOKEN="$(gen_secret)"
      cat > "${BRIDGES_DIR}/${bridge}/registration.yaml" <<REGEOF
id: ${bridge}
url: http://bridge-${bridge}:293$((17 + $(echo "telegram whatsapp meta" | tr ' ' '\n' | grep -n "^${bridge}$" | cut -d: -f1) - 1))
as_token: ${AS_TOKEN}
hs_token: ${BRIDGE_TOKEN}
sender_localpart: ${bridge}bot
rate_limited: false
namespaces:
  users:
    - exclusive: true
      regex: "@${bridge}_.*:${DOMAIN}"
  aliases:
    - exclusive: true
      regex: "#${bridge}_.*:${DOMAIN}"
REGEOF
    fi
  fi
done

success "Bridge registrations ready."

# ── Start the full stack ───────────────────────────────────────────────────────
info "Starting Synapse + bridges..."
cd "${DEPLOY_DIR}"
docker compose --env-file .env up -d

# Wait for Synapse health
info "Waiting for Synapse to be healthy (up to 60s)..."
for i in $(seq 1 30); do
  if curl -fs "http://127.0.0.1:8008/_matrix/client/versions" >/dev/null 2>&1; then
    success "Synapse is up."
    break
  fi
  sleep 2
  if [[ $i -eq 30 ]]; then
    warn "Synapse health check timed out. Check: docker compose -f ${DEPLOY_DIR}/docker-compose.yml logs synapse"
  fi
done

# ── Create NAVIG bot user ──────────────────────────────────────────────────────
info "Creating Matrix user @${NAVIG_MATRIX_USER}:${DOMAIN}..."
docker compose --env-file .env exec -T synapse \
  register_new_matrix_user \
    -u "${NAVIG_MATRIX_USER}" \
    -p "${NAVIG_MATRIX_PASS}" \
    -a \
    --no-admin \
    -c /data/homeserver.yaml \
    "http://localhost:8008" 2>/dev/null || warn "User may already exist — continuing."

success "Matrix user ready."

# ── nginx reverse proxy + TLS (domain mode only) ─────────────────────────────
if [[ "$USE_CF_TUNNEL" == "true" ]]; then
  info "Cloudflare tunnel mode: skipping nginx setup."
  info "Expose Synapse with: cloudflared tunnel --url http://localhost:8008"
  success "Stack is running. Synapse reachable at http://127.0.0.1:8008"
else
info "Configuring nginx for matrix.${DOMAIN}..."

cat > "/etc/nginx/sites-available/matrix" <<NGINXEOF
server {
    listen 80;
    server_name matrix.${DOMAIN};

    location /.well-known/acme-challenge/ { root /var/www/certbot; }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name matrix.${DOMAIN};

    ssl_certificate /etc/letsencrypt/live/matrix.${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/matrix.${DOMAIN}/privkey.pem;

    # Matrix federation port forwarding
    location /_matrix/ {
        proxy_pass http://127.0.0.1:8008;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Host \$host;
        client_max_body_size 100m;
    }

    location /_synapse/ {
        proxy_pass http://127.0.0.1:8008;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Host \$host;
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/matrix /etc/nginx/sites-enabled/matrix
nginx -t && systemctl reload nginx

# Obtain TLS cert
certbot --nginx \
  -d "matrix.${DOMAIN}" \
  --non-interactive \
  --agree-tos \
  --email "${LE_EMAIL}" \
  --redirect || warn "TLS cert failed — run manually: certbot --nginx -d matrix.${DOMAIN}"

systemctl reload nginx
success "nginx + TLS configured for matrix.${DOMAIN}"

# ── Federation well-known ──────────────────────────────────────────────────────
mkdir -p "/var/www/${DOMAIN}/.well-known/matrix"
echo '{"m.server": "matrix.'${DOMAIN}':443"}' \
  > "/var/www/${DOMAIN}/.well-known/matrix/server"
echo '{"m.homeserver": {"base_url": "https://matrix.'${DOMAIN}'"}}' \
  > "/var/www/${DOMAIN}/.well-known/matrix/client"
fi  # end USE_CF_TUNNEL==false block

# ── Final summary ──────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Installation complete!                                      ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

if [[ "$USE_CF_TUNNEL" == "true" ]]; then
  echo -e "${CYAN}Mode:${NC}              Cloudflare tunnel"
  echo -e "${CYAN}Synapse (local):${NC}   http://127.0.0.1:8008"
  echo -e "${CYAN}Your Matrix ID:${NC}    @${NAVIG_MATRIX_USER}:${DOMAIN}"
  echo ""
  echo -e "${YELLOW}Next — expose Synapse via Cloudflare:${NC}"
  echo -e "  cloudflared tunnel --url http://localhost:8008"
  echo -e "  # copy the tunnel URL, then run on your local machine:"
  echo -e "  navig config set comms.matrix.homeserver_url <tunnel-url>"
else
  echo -e "${CYAN}Synapse:${NC}           https://matrix.${DOMAIN}"
  echo -e "${CYAN}Your Matrix ID:${NC}    @${NAVIG_MATRIX_USER}:${DOMAIN}"
  echo ""
  echo -e "${CYAN}Run these on your LOCAL machine to connect NAVIG:${NC}"
  echo -e "  navig config set comms.matrix.enabled true"
  echo -e "  navig config set comms.matrix.homeserver_url https://matrix.${DOMAIN}"
fi

echo ""
echo -e "${CYAN}Common steps (both modes):${NC}"
echo -e "  navig config set comms.matrix.user_id @${NAVIG_MATRIX_USER}:${DOMAIN}"
echo -e "  navig vault add matrix  # paste your Matrix password"
echo ""
echo -e "${CYAN}Bridge status:${NC}"
echo -e "  docker compose -f ${DEPLOY_DIR}/docker-compose.yml ps"
echo ""
echo -e "${CYAN}Link each bridge:${NC}"
echo -e "  navig matrix bridge setup telegram"
echo -e "  navig matrix bridge setup whatsapp    # scan QR with your phone"
echo -e "  navig matrix bridge setup messenger   # Facebook / Instagram DMs"
echo ""
echo -e "${YELLOW}⚠  WhatsApp and Messenger require one-time login from your phone.${NC}"
echo -e "${YELLOW}   Keep the VPS running — bridges maintain the session 24/7.${NC}"
echo ""
