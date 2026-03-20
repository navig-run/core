#!/usr/bin/env bash
set -euo pipefail

SRC_PATH="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
TELEGRAM_TOKEN="${2:-${NAVIG_TELEGRAM_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}}"
VENV_PATH="${HOME}/.navig/venv"
BIN_PATH="${HOME}/.local/bin"
DECK_STATIC="${SRC_PATH}/deck-static"

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

build_deck() {
  # Look for navig-deck source in monorepo layout
  local deck_src=""
  for candidate in \
      "${SRC_PATH}/../navig-deck" \
      "${SRC_PATH}/navig-deck" \
      "${HOME}/navig/navig-deck"; do
    if [[ -f "${candidate}/package.json" ]]; then
      deck_src="$(cd "$candidate" && pwd)"
      break
    fi
  done

  if [[ -z "$deck_src" ]]; then
    echo "Deck source not found — skipping Deck build."
    echo "Place navig-deck/ next to navig-core/ for auto-build."
    return 0
  fi

  # Check Node.js
  if ! command -v node >/dev/null 2>&1; then
    echo "Node.js not found — installing Node.js 20 LTS..."
    if command -v apt-get >/dev/null 2>&1; then
      curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
      sudo apt-get install -y nodejs
    else
      echo "Cannot auto-install Node.js. Install Node.js 20+ manually."
      return 0
    fi
  fi

  local node_ver
  node_ver=$(node -v | sed 's/v//' | cut -d. -f1)
  if [[ "$node_ver" -lt 18 ]]; then
    echo "Node.js ${node_ver} too old — need 18+. Skipping Deck build."
    return 0
  fi

  echo "Building Deck SPA from ${deck_src}..."
  cd "$deck_src"
  npm install --no-audit --no-fund 2>&1 | tail -3
  npm run build 2>&1 | tail -5

  # Copy built files to deck-static
  if [[ -d "${deck_src}/dist" ]]; then
    rm -rf "$DECK_STATIC"
    cp -r "${deck_src}/dist" "$DECK_STATIC"
    echo "Deck built and installed to ${DECK_STATIC}"
  else
    echo "Deck build failed — dist/ not found."
  fi
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python3 is required (3.10+)." >&2
  exit 1
fi

# Install iperf3 for navig net speedtest (iperf3 method)
if ! command -v iperf3 >/dev/null 2>&1; then
  echo "Installing iperf3..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get install -y iperf3 2>/dev/null || echo "Warning: iperf3 install failed — 'navig net speedtest --skip-iperf3' still works"
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y iperf3 2>/dev/null || true
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y iperf3 2>/dev/null || true
  else
    echo "Warning: cannot auto-install iperf3. Install manually for full speed test support."
  fi
fi

# Install autossh for persistent Bridge tunnel auto-reconnect
if ! command -v autossh >/dev/null 2>&1; then
  echo "Installing autossh (for persistent Bridge tunnels)..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get install -y autossh 2>/dev/null || echo "Warning: autossh install failed — Bridge tunnel auto-reconnect won't work"
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y autossh 2>/dev/null || true
  fi
fi

python3 -m venv "$VENV_PATH"
"$VENV_PATH/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV_PATH/bin/python" -m pip install -e "$SRC_PATH"
"$VENV_PATH/bin/python" -m pip install --quiet speedtest-cli 2>/dev/null || echo "Warning: speedtest-cli install failed — Ookla measurements unavailable"

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
build_deck
start_telegram_daemon "$TELEGRAM_TOKEN"

if [[ ":$PATH:" != *":$BIN_PATH:"* ]]; then
  echo "Add to PATH: export PATH=\"$BIN_PATH:\$PATH\""
fi

echo "NAVIG installed on Linux."
echo "Verify: navig --help"
if [[ -n "$TELEGRAM_TOKEN" ]]; then
  echo "Telegram bot auto-configured and daemon start attempted."
fi
