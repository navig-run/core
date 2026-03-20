#!/usr/bin/env bash
# bootstrap_ubuntu.sh — Bootstrap Navig Mesh on Ubuntu
# Usage: bash bootstrap_ubuntu.sh [--mesh-secret <secret>] [--formation <name>]
set -euo pipefail

NAVIG_HOME="${NAVIG_HOME:-$HOME/.navig}"
MESH_SECRET=""
FORMATION="default"

# ── Arg parsing ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mesh-secret) MESH_SECRET="$2"; shift 2 ;;
    --formation)   FORMATION="$2";   shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo "=== Navig Mesh bootstrap (Ubuntu) ==="

# ── 1. Install navig-core ────────────────────────────────────────────────────
if ! command -v navig &>/dev/null; then
  echo "[1/5] Installing navig-core..."
  python3 -m pip install --user --quiet navig-core || pip3 install --user --quiet navig-core
else
  echo "[1/5] navig-core already installed — skipping"
fi

# ── 2. Create ~/.navig structure ──────────────────────────────────────────────
echo "[2/5] Creating config directories..."
mkdir -p "$NAVIG_HOME"/{vault,workspace,daemon,wiki}

# ── 3. Write mesh config ──────────────────────────────────────────────────────
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
  echo "  Config exists — skipping (edit $CFG to change mesh settings)"
fi

# ── 4. Write mesh secret to vault ─────────────────────────────────────────────
if [[ -n "$MESH_SECRET" ]]; then
  echo "[4/5] Writing mesh secret to vault..."
  echo -n "$MESH_SECRET" > "$NAVIG_HOME/vault/mesh_secret"
  chmod 600 "$NAVIG_HOME/vault/mesh_secret"
  echo "  Vault: $NAVIG_HOME/vault/mesh_secret"
else
  echo "[4/5] No --mesh-secret provided — mesh auth will use auto-generated key"
fi

# ── 5. Announce on LAN (one-shot) ─────────────────────────────────────────────
echo "[5/5] Announcing presence on LAN..."
python3 - <<'PYEOF'
import sys, os
sys.path.insert(0, os.path.expanduser("~/.local/lib/python3.12/site-packages"))
try:
    from navig.mesh.discovery import announce_once
    announce_once()
    print("  UDP HELLO sent on 224.0.0.251:5354")
except Exception as e:
    print(f"  Note: {e} (safe to ignore if daemon not running yet)")
PYEOF

echo ""
echo "=== Bootstrap complete ==="
echo "Start the daemon: navig service start"
echo "Peer status:      navig mesh status"
