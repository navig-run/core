#!/usr/bin/env bash
# suspend_node.sh — Gracefully yield leadership and stop mesh heartbeating.
# Safe to run before system sleep/hibernate so the leader transfers before
# the node goes dark.
set -euo pipefail

GATEWAY="${NAVIG_GATEWAY_URL:-http://127.0.0.1:8090}"

echo "[mesh] Requesting graceful yield + suspend..."
HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "$GATEWAY/mesh/suspend" \
  -H "Content-Type: application/json" \
  -d '{}' \
  --max-time 5 2>/dev/null || true)

if [[ "$HTTP" == "200" ]]; then
  echo "[mesh] Node suspended — leadership yielded to best standby"
else
  echo "[mesh] Suspend call returned HTTP $HTTP (gateway may be stopped already)"
fi
