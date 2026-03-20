#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# navig-tunnel.sh — Start cloudflared quick tunnel, extract URL,
# update deck_url in NAVIG config, and restart daemon.
# Called by navig-tunnel.service
# ──────────────────────────────────────────────────────────────
set -euo pipefail

CONFIG="~/.navig/config.yaml"
LOG="/tmp/cloudflared.log"
PORT="${NAVIG_GATEWAY_PORT:-8765}"

cleanup() {
    pkill -f "cloudflared tunnel --url" 2>/dev/null || true
}
trap cleanup EXIT

# Kill any existing tunnel
cleanup
sleep 1

# Start cloudflared in background and pipe output to log
cloudflared tunnel --url "http://127.0.0.1:${PORT}" > "$LOG" 2>&1 &
CF_PID=$!

# Wait for URL to appear (up to 30s)
TUNNEL_URL=""
for i in $(seq 1 30); do
    TUNNEL_URL=$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | head -1 || true)
    if [[ -n "$TUNNEL_URL" ]]; then
        break
    fi
    sleep 1
done

if [[ -z "$TUNNEL_URL" ]]; then
    echo "ERROR: cloudflared did not produce a tunnel URL in 30s"
    cat "$LOG"
    exit 1
fi

echo "Tunnel URL: ${TUNNEL_URL}"
DECK_URL="${TUNNEL_URL}/deck/"

# Update deck_url in config.yaml using python (safe YAML manipulation)
python3 -c "
import yaml, sys
config_path = '${CONFIG}'
with open(config_path) as f:
    cfg = yaml.safe_load(f) or {}
if 'telegram' not in cfg:
    cfg['telegram'] = {}
cfg['telegram']['deck_url'] = '${DECK_URL}'
with open(config_path, 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
print(f'Set telegram.deck_url = ${DECK_URL}')
"

# Restart navig-daemon so it picks up the new deck_url for menu button
systemctl restart navig-daemon 2>/dev/null || true
echo "Daemon restarted with new deck_url"

# Keep running (foreground for systemd)
wait $CF_PID
