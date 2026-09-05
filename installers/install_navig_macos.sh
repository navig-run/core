#!/usr/bin/env bash
set -euo pipefail

SRC_PATH="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
TELEGRAM_TOKEN="${2:-${NAVIG_TELEGRAM_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}}"
VENV_PATH="${HOME}/.navig/venv"
BIN_PATH="${HOME}/.local/bin"

configure_telegram() {
  local token="$1"
  [[ -z "$token" ]] && return 0

  local config_dir="${HOME}/.navig"
  local env_file="${config_dir}/.env"
  local config_file="${config_dir}/config.yaml"

  mkdir -p "$config_dir"

  if [[ -f "$env_file" ]]; then
    grep -v '^TELEGRAM_BOT_TOKEN=' "$env_file" > "${env_file}.tmp" || true
    mv "${env_file}.tmp" "$env_file"
  fi
  printf 'TELEGRAM_BOT_TOKEN=%s\n' "$token" >> "$env_file"
  chmod 600 "$env_file"

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
  local token="$1"
  [[ -z "$token" ]] && return 0

  "$BIN_PATH/navig" service install --bot --gateway --scheduler --no-start >/dev/null 2>&1 || true
  "$BIN_PATH/navig" service start >/dev/null 2>&1 || true
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python3 is required. Install via: brew install python" >&2
  exit 1
fi

# Install iperf3 for navig net speedtest (iperf3 method)
if ! command -v iperf3 >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "Installing iperf3 via Homebrew..."
    brew install iperf3 2>/dev/null || echo "Warning: iperf3 install failed — install manually: brew install iperf3"
  else
    echo "Warning: Homebrew not found. Install iperf3 manually: brew install iperf3"
  fi
fi

python3 -m venv "$VENV_PATH"
"$VENV_PATH/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV_PATH/bin/python" -m pip install -e "$SRC_PATH"
"$VENV_PATH/bin/python" -m pip install --quiet speedtest-cli 2>/dev/null || echo "Warning: speedtest-cli install failed"

mkdir -p "$BIN_PATH"
cat > "$BIN_PATH/navig" <<EOF
#!/usr/bin/env bash
if [[ -f "$HOME/.navig/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$HOME/.navig/.env"
  set +a
fi
exec "$VENV_PATH/bin/python" -m navig.main "\$@"
EOF
chmod +x "$BIN_PATH/navig"

configure_telegram "$TELEGRAM_TOKEN"
start_telegram_daemon "$TELEGRAM_TOKEN"

SHELL_NAME=$(basename "${SHELL:-zsh}")
if [[ "$SHELL_NAME" == "zsh" ]]; then
  RC_FILE="$HOME/.zshrc"
else
  RC_FILE="$HOME/.bashrc"
fi

if ! grep -q "$BIN_PATH" "$RC_FILE" 2>/dev/null; then
  echo "export PATH=\"$BIN_PATH:\$PATH\"" >> "$RC_FILE"
  echo "Added PATH update to $RC_FILE"
fi

echo "NAVIG installed on macOS."
echo "Restart terminal or: source $RC_FILE"
echo "Verify: navig --help"
if [[ -n "$TELEGRAM_TOKEN" ]]; then
  echo "Telegram bot auto-configured and daemon start attempted."
fi
