#!/usr/bin/env bash
# scripts/navig_ubuntu_reinstall.sh
#
# Phase 4: Clean Navig reinstall on Ubuntu from PyPI.
# Installs, initializes, registers the systemd service, and verifies.
#
# Usage:
#   bash navig_ubuntu_reinstall.sh [--from-source /path/to/navig-core]
#
# Options:
#   --from-source <path>   Install from a local source tree instead of PyPI
#   --version <ver>        Install a specific PyPI version (default: latest)
#
# Run as the navig user (or any user with sudo access).
set -euo pipefail

NAVIG_VERSION="${NAVIG_VERSION:-}"   # pin version, e.g. "2.4.14"; empty = latest
FROM_SOURCE=""
SERVICE_FILE="/etc/systemd/system/navig.service"
REPO_SERVICE_FILE="$(dirname "$0")/../deploy/navig.service"
LOG_FILE="/tmp/navig_reinstall_$(date +%Y%m%d_%H%M%S).log"

# Argument parsing
while [[ $# -gt 0 ]]; do
  case "$1" in
    --from-source) FROM_SOURCE="$2"; shift 2 ;;
    --version)     NAVIG_VERSION="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

exec > >(tee -a "${LOG_FILE}") 2>&1
echo "════════════════════════════════════════════"
echo "  NAVIG — Ubuntu Reinstall"
echo "  Log: ${LOG_FILE}"
echo "════════════════════════════════════════════"

# ── Dependency check ──────────────────────────────────────────────────────────
echo ""
echo "── Checking prerequisites ──"
command -v python3 >/dev/null || { echo "❌ python3 not found. Run: sudo apt install python3 python3-pip"; exit 1; }
command -v pip3 >/dev/null   || command -v pip >/dev/null || {
  echo "Installing pip..."
  sudo apt-get install -y python3-pip
}
PIP=$(command -v pip3 || command -v pip)
echo "  ✓ Python: $(python3 --version)"
echo "  ✓ pip: $($PIP --version)"

# ── Install ───────────────────────────────────────────────────────────────────
echo ""
echo "── Installing Navig ──"

if [[ -n "${FROM_SOURCE}" ]]; then
  echo "  Installing from source: ${FROM_SOURCE}"
  $PIP install --quiet "${FROM_SOURCE}"
elif [[ -n "${NAVIG_VERSION}" ]]; then
  echo "  Installing navig==${NAVIG_VERSION} from PyPI"
  $PIP install --quiet "navig==${NAVIG_VERSION}"
else
  echo "  Installing latest navig from PyPI"
  $PIP install --quiet navig
fi

# Reload shell PATH for newly installed scripts
export PATH="$($PIP show navig | grep Location | awk '{print $2}')/../../../bin:${PATH}"
hash -r 2>/dev/null || true

# Verify install
if ! command -v navig &>/dev/null; then
  # Try common pip script locations
  for candidate in ~/.local/bin/navig /usr/local/bin/navig /usr/bin/navig; do
    if [ -x "${candidate}" ]; then
      export PATH="$(dirname ${candidate}):${PATH}"
      break
    fi
  done
fi

command -v navig >/dev/null || {
  echo "❌ 'navig' command not found after install. Add pip scripts dir to PATH."
  echo "   Likely: export PATH=~/.local/bin:\$PATH"
  echo "   Then run: navig --version"
  exit 1
}

INSTALLED_VER=$(navig --version 2>/dev/null || echo "unknown")
echo "  ✓ Installed: ${INSTALLED_VER}"

# ── Initialize ────────────────────────────────────────────────────────────────
echo ""
echo "── Running navig init ──"
navig init --yes 2>/dev/null || navig init || true

# ── Install systemd service ───────────────────────────────────────────────────
echo ""
echo "── Installing systemd service ──"

if [ -f "${REPO_SERVICE_FILE}" ]; then
  echo "  Using service file from repo: ${REPO_SERVICE_FILE}"
  sudo cp "${REPO_SERVICE_FILE}" "${SERVICE_FILE}"
else
  # Generate a minimal service file inline
  echo "  Generating minimal service file"
  NAVIG_BIN=$(command -v navig)
  sudo tee "${SERVICE_FILE}" > /dev/null <<EOF
[Unit]
Description=NAVIG Daemon
After=network.target

[Service]
Type=simple
User=$(whoami)
ExecStart=${NAVIG_BIN} daemon start --foreground
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=navig

[Install]
WantedBy=multi-user.target
EOF
fi

sudo systemctl daemon-reload
sudo systemctl enable navig
sudo systemctl start navig
sleep 2

echo ""
echo "── Verification ──"
ERRORS=0

# Version check
if navig --version &>/dev/null; then
  echo "  ✓ navig --version: $(navig --version)"
else
  echo "  ⚠  navig --version failed"
  ERRORS=$((ERRORS+1))
fi

# Service check
if systemctl is-active --quiet navig; then
  echo "  ✓ systemd service: active (running)"
else
  echo "  ⚠  systemd service: not active"
  echo "     Check: journalctl -u navig -n 30"
  ERRORS=$((ERRORS+1))
fi

# navig status
navig status 2>/dev/null || true

echo ""
if [ "${ERRORS}" -gt 0 ]; then
  echo "⚠️  Reinstall completed with ${ERRORS} issue(s). See above."
else
  echo "✅ Navig reinstall complete and verified."
fi
echo "   Log saved to: ${LOG_FILE}"
