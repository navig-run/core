#!/usr/bin/env bash
# scripts/navig_ubuntu_backup_uninstall.sh
#
# Phase 1 + 3: Pre-removal backup and full Navig uninstall on Ubuntu.
#
# Usage:
#   bash navig_ubuntu_backup_uninstall.sh
#
# Run as the current navig/service user (with sudo access).
# After completion, run navig_ubuntu_reinstall.sh to bring Navig back up.
set -euo pipefail

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${HOME}/navig_backup_${TIMESTAMP}"
LOG="${BACKUP_DIR}/backup.log"

echo "════════════════════════════════════════════"
echo "  NAVIG — Backup & Uninstall"
echo "  Backup target: ${BACKUP_DIR}"
echo "════════════════════════════════════════════"

# ── Phase 1 — Backup ─────────────────────────────────────────────────────────

mkdir -p "${BACKUP_DIR}"
exec > >(tee -a "${LOG}") 2>&1
echo "[$(date)] Backup started"

backup_if_exists() {
  local src="$1"
  local dest_name="$2"
  if [ -e "${src}" ]; then
    echo "  ✓ Backing up: ${src}"
    cp -a "${src}" "${BACKUP_DIR}/${dest_name}"
  else
    echo "  — Skipping (not found): ${src}"
  fi
}

echo ""
echo "── Backing up config and data files ──"
backup_if_exists "${HOME}/.navig"              "dot-navig"
backup_if_exists "/etc/navig"                  "etc-navig"
backup_if_exists "/var/lib/navig"              "var-lib-navig"
backup_if_exists "/var/log/navig"              "var-log-navig"
backup_if_exists "/opt/navig"                  "opt-navig"

echo ""
echo "── Backing up systemd unit files ──"
mkdir -p "${BACKUP_DIR}/systemd"
for f in /etc/systemd/system/navig*.service /lib/systemd/system/navig*.service; do
  [ -f "$f" ] && cp "$f" "${BACKUP_DIR}/systemd/" && echo "  ✓ $f" || true
done

echo ""
echo "── Capturing crontab ──"
crontab -l 2>/dev/null > "${BACKUP_DIR}/crontab.txt" && \
  echo "  ✓ crontab saved" || \
  echo "  — No crontab found"

echo ""
echo "── Capturing pip package info ──"
pip show navig 2>/dev/null > "${BACKUP_DIR}/pip-show.txt" && \
  echo "  ✓ pip info saved" || \
  echo "  — navig not installed via pip"

# Mandatory gate: verify backup is non-empty
FILE_COUNT=$(find "${BACKUP_DIR}" -type f | wc -l)
echo ""
echo "  Files captured: ${FILE_COUNT}"
if [ "${FILE_COUNT}" -lt 1 ]; then
  echo "❌ ABORT: Backup directory is empty — nothing was backed up."
  echo "   Nothing will be removed. Investigate and retry."
  rmdir "${BACKUP_DIR}" 2>/dev/null || true
  exit 1
fi

cat > "${BACKUP_DIR}/README.txt" <<EOF
NAVIG Backup
============
Created:  $(date)
Host:     $(hostname)
User:     $(whoami)
Source:   navig_ubuntu_backup_uninstall.sh

Files captured: ${FILE_COUNT}

Restore notes:
  - Restore ~/.navig  : cp -a dot-navig ~/.navig
  - Restore /etc/navig: sudo cp -a etc-navig /etc/navig
  - Restore systemd   : sudo cp systemd/*.service /etc/systemd/system/ && sudo systemctl daemon-reload
EOF

echo ""
echo "✅ Backup complete → ${BACKUP_DIR}"
echo "   (${FILE_COUNT} files preserved)"

# ── Phase 3 — Full Removal ────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════════════"
echo "  Starting full Navig removal…"
echo "════════════════════════════════════════════"

echo ""
echo "── Stopping and disabling services ──"
for svc in navig navig-stack navig-factory; do
  if systemctl list-units --full -all | grep -q "${svc}.service"; then
    sudo systemctl stop "${svc}" 2>/dev/null  && echo "  stopped: ${svc}" || true
    sudo systemctl disable "${svc}" 2>/dev/null && echo "  disabled: ${svc}" || true
  fi
done

echo ""
echo "── Removing pip package ──"
pip uninstall navig -y 2>/dev/null && echo "  ✓ pip: navig removed" || echo "  — pip: navig was not installed"
# Also check pip3 and system pip
pip3 uninstall navig -y 2>/dev/null || true
sudo pip uninstall navig -y 2>/dev/null || true
sudo pip3 uninstall navig -y 2>/dev/null || true

echo ""
echo "── Removing data and config directories ──"
for path in /etc/navig /var/lib/navig /var/log/navig /opt/navig; do
  if [ -e "${path}" ]; then
    sudo rm -rf "${path}" && echo "  ✓ removed: ${path}"
  fi
done

# Remove user's navig config dir (only if inside home, not system)
if [ -d "${HOME}/.navig" ]; then
  rm -rf "${HOME}/.navig" && echo "  ✓ removed: ~/.navig"
fi

echo ""
echo "── Removing systemd unit files ──"
for f in /etc/systemd/system/navig*.service /lib/systemd/system/navig*.service; do
  if [ -f "$f" ]; then
    sudo rm -f "$f" && echo "  ✓ removed: $f"
  fi
done
sudo systemctl daemon-reload && echo "  ✓ systemctl daemon-reload"

echo ""
echo "── Removal verification ──"
ERRORS=0

if command -v navig &>/dev/null; then
  echo "  ⚠  'navig' binary still found at: $(which navig)"
  ERRORS=$((ERRORS+1))
else
  echo "  ✓ 'which navig' — not found"
fi

if pip show navig 2>/dev/null | grep -q "Name:"; then
  echo "  ⚠  pip still shows navig installed"
  ERRORS=$((ERRORS+1))
else
  echo "  ✓ 'pip show navig' — nothing returned"
fi

if systemctl status navig 2>&1 | grep -q "could not be found\|Unit navig.service could not"; then
  echo "  ✓ systemctl status navig — unit not found"
else
  if systemctl is-active navig 2>/dev/null | grep -q "^active"; then
    echo "  ⚠  navig service still active"
    ERRORS=$((ERRORS+1))
  else
    echo "  ✓ systemctl status navig — not active"
  fi
fi

echo ""
if [ "${ERRORS}" -gt 0 ]; then
  echo "⚠️  Removal completed with ${ERRORS} warning(s). Review above before reinstalling."
else
  echo "✅ Full removal verified — Navig is completely uninstalled."
fi

echo ""
echo "Backup is preserved at: ${BACKUP_DIR}"
echo "To reinstall: bash scripts/navig_ubuntu_reinstall.sh"
