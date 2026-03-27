#!/usr/bin/env bash
# scripts/_lib/installer_common.sh
# Shared functions for all NAVIG bash/zsh installers.
# Source this file; do not execute directly.
#
# Usage in installer scripts:
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "${SCRIPT_DIR}/_lib/installer_common.sh"

# ── Telegram configuration ────────────────────────────────────────────────────

configure_telegram() {
  # Write TELEGRAM_BOT_TOKEN to ~/.navig/.env and bootstrap config.yaml.
  # Usage: configure_telegram "$TOKEN"
  local token="$1"
  [[ -z "$token" ]] && return 0

  local config_dir="${HOME}/.navig"
  local env_file="${config_dir}/.env"
  local config_file="${config_dir}/config.yaml"

  mkdir -p "$config_dir"

  # Replace any existing token line, then append the new one.
  if [[ -f "$env_file" ]]; then
    grep -v '^TELEGRAM_BOT_TOKEN=' "$env_file" > "${env_file}.tmp" || true
    mv "${env_file}.tmp" "$env_file"
  fi
  printf 'TELEGRAM_BOT_TOKEN=%s\n' "$token" >> "$env_file"
  chmod 600 "$env_file"

  # Write minimal config.yaml if the token block is absent.
  if [[ ! -f "$config_file" ]] || ! grep -q 'bot_token:' "$config_file"; then
    cat > "$config_file" <<EOF
telegram:
  bot_token: "$token"
  allowed_users: []
  allowed_groups: []
  session_isolation: true
  group_activation_mode: "mention"
EOF
  fi
}

start_telegram_daemon() {
  # Install and start NAVIG daemon services for Telegram.
  # Usage: start_telegram_daemon "$TOKEN" "$BIN_PATH"
  local token="$1"
  local bin_path="${2:-${HOME}/.local/bin}"
  [[ -z "$token" ]] && return 0

  "${bin_path}/navig" service install --bot --gateway --scheduler --no-start >/dev/null 2>&1 || true
  "${bin_path}/navig" service start >/dev/null 2>&1 || true
}

# ── Python / venv helpers ─────────────────────────────────────────────────────

require_python3() {
  # Exit with a message if Python 3.10+ is not available.
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Python3 is required (3.10+). Please install it first." >&2
    exit 1
  fi
}

install_venv() {
  # Create a venv at $1, upgrade pip, and install editable package from $2.
  # Usage: install_venv "$VENV_PATH" "$SRC_PATH"
  local venv_path="$1"
  local src_path="$2"
  python3 -m venv "$venv_path"
  "${venv_path}/bin/python" -m pip install --upgrade pip setuptools wheel
  "${venv_path}/bin/python" -m pip install -e "$src_path"
}

write_navig_shim() {
  # Write the navig launcher shim to BIN_PATH.
  # Usage: write_navig_shim "$VENV_PATH" "$BIN_PATH"
  local venv_path="$1"
  local bin_path="$2"
  mkdir -p "$bin_path"
  cat > "${bin_path}/navig" <<EOF
#!/usr/bin/env bash
if [[ -f "\$HOME/.navig/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "\$HOME/.navig/.env"
  set +a
fi
exec "${venv_path}/bin/python" -m navig.main "\$@"
EOF
  chmod +x "${bin_path}/navig"
}

ensure_on_path() {
  # Print a PATH export hint if BIN_PATH is not on PATH.
  # Usage: ensure_on_path "$BIN_PATH"
  local bin_path="$1"
  if [[ ":${PATH}:" != *":${bin_path}:"* ]]; then
    echo "Add to PATH: export PATH=\"${bin_path}:\$PATH\""
  fi
}
