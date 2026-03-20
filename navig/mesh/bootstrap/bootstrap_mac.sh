#!/usr/bin/env bash
# bootstrap_mac.sh — Bootstrap Navig Mesh on macOS
# Usage: bash bootstrap_mac.sh [--mesh-secret <secret>] [--formation <name>]
set -euo pipefail

NAVIG_HOME="${NAVIG_HOME:-$HOME/.navig}"
MESH_SECRET=""
FORMATION="default"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mesh-secret) MESH_SECRET="$2"; shift 2 ;;
    --formation)   FORMATION="$2";   shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo "=== Navig Mesh bootstrap (macOS) ==="

echo "[1/5] Checking navig-core..."
if ! command -v navig &>/dev/null; then
  echo "  Installing navig-core..."
  python3 -m pip install --user --quiet navig-core
else
  echo "  navig already installed — skipping"
fi

echo "[2/5] Creating config directories..."
mkdir -p "$NAVIG_HOME"/{vault,workspace,daemon,wiki}

echo "[3/5] Writing mesh config..."
CFG="$NAVIG_HOME/config.yaml"
if [[ ! -f "$CFG" ]]; then
  cat > "$CFG" <<YAML
mesh:
  enabled: true
  formation: "${FORMATION}"
  multicast_group: "224.0.0.251"
  multicast_port: 5354
  heartbeat_interval_s: 5
  election_ttl_s: 15
  sync_interval_s: 10
  collective_enabled: false
YAML
  echo "  Written: $CFG"
else
  echo "  Config exists — skipping"
fi

if [[ -n "$MESH_SECRET" ]]; then
  echo "[4/5] Writing mesh secret..."
  echo -n "$MESH_SECRET" > "$NAVIG_HOME/vault/mesh_secret"
  chmod 600 "$NAVIG_HOME/vault/mesh_secret"
else
  echo "[4/5] No --mesh-secret — auto-generated key will be used"
fi

echo "[5/5] Announcing on LAN..."
python3 - <<'PYEOF'
import sys, os
try:
    from navig.mesh.discovery import announce_once
    announce_once()
    print("  UDP HELLO sent")
except Exception as e:
    print(f"  Note: {e}")
PYEOF

echo ""
echo "=== Bootstrap complete ==="
echo "Start: navig service start"
echo "Peers: navig mesh status"
