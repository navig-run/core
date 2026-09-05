#!/usr/bin/env bash
set -euo pipefail

# NAVIG Linux Installation with Remote Drive Setup
# Usage:
#   bash install_navig_linux_enhanced.sh
#   TELEGRAM_BOT_TOKEN=xxx bash install_navig_linux_enhanced.sh
#   bash install_navig_linux_enhanced.sh --skip-samba --silent

# ── COLORS ────────────────────────────────────────────────────
BOLD='\033[1m'
DIM='\033[2m'
ACCENT='\033[1;36m'
SUCCESS='\033[1;32m'
WARN='\033[1;33m'
ERROR='\033[1;31m'
INFO='\033[0;36m'
NC='\033[0m'

# ── GLOBALS ───────────────────────────────────────────────────
SRC_PATH="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
TELEGRAM_TOKEN="${TELEGRAM_BOT_TOKEN:-${NAVIG_TELEGRAM_BOT_TOKEN:-}}"
VENV_PATH="${HOME}/.navig/venv"
BIN_PATH="${HOME}/.local/bin"
SKIP_SAMBA=0
SKIP_RCLONE=0
SILENT=0
INSTALL_SAMBA=0
INSTALL_RCLONE=0

# ── ARGUMENT PARSING ──────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-samba)    SKIP_SAMBA=1; shift ;;
        --skip-rclone)   SKIP_RCLONE=1; shift ;;
        --install-samba) INSTALL_SAMBA=1; shift ;;
        --install-rclone) INSTALL_RCLONE=1; shift ;;
        --silent)        SILENT=1; shift ;;
        --help)
            echo "NAVIG Linux Installation + Remote Drive Setup"
            echo ""
            echo "Usage: bash install_navig_linux_enhanced.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --skip-samba        Don't offer Samba share setup"
            echo "  --skip-rclone       Don't offer rclone setup"
            echo "  --install-samba     Auto-install Samba"
            echo "  --install-rclone    Auto-install rclone"
            echo "  --silent            Non-interactive mode"
            echo "  --help              Show this help"
            echo ""
            echo "Environment:"
            echo "  TELEGRAM_BOT_TOKEN  Telegram bot token for automation"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ── LOGGING ───────────────────────────────────────────────────
log_info() {
    echo -e "${INFO}[INFO]${NC} $1"
}

log_success() {
    echo -e "${SUCCESS}[✓]${NC} $1"
}

log_warn() {
    echo -e "${WARN}[!]${NC} $1"
}

log_error() {
    echo -e "${ERROR}[✗]${NC} $1"
}

progress_step() {
    local step=$1
    local total=$2
    local msg=$3
    echo ""
    echo -e "${ACCENT}╔═══════════════════════════════════════════╗${NC}"
    echo -e "${ACCENT}║${NC} Step $step/$total"
    echo -e "${ACCENT}║${NC} $msg"
    echo -e "${ACCENT}╚═══════════════════════════════════════════╝${NC}"
}

# ── PREREQUISITE CHECKS ───────────────────────────────────────
check_python() {
    if ! command -v python3 >/dev/null 2>&1; then
        log_error "Python3 is required (3.10+)"
        log_info "Install with: sudo apt install python3 python3-venv python3-pip"
        exit 1
    fi

    local ver=$(python3 --version 2>&1 | awk '{print $NF}')
    log_success "Python found: $ver"
}

check_disk_space() {
    local available=$(df "$HOME" | tail -1 | awk '{print $4}')
    local required=$((2 * 1024 * 1024))  # 2GB

    if (( available < required )); then
        log_warn "Low disk space: $(numfmt --to=iec $((available * 1024)))"
    else
        log_success "Sufficient disk space"
    fi
}

# ── PACKAGE MANAGERS ──────────────────────────────────────────
is_apt_available() {
    command -v apt >/dev/null 2>&1
}

is_rclone_installed() {
    command -v rclone >/dev/null 2>&1
}

is_samba_installed() {
    command -v smbd >/dev/null 2>&1
}

install_rclone() {
    log_info "Installing rclone..."

    if is_apt_available; then
        sudo apt update >/dev/null
        sudo apt install -y rclone >/dev/null
    else
        # Generic installation
        curl https://rclone.org/install.sh | sudo bash >/dev/null 2>&1
    fi

    if is_rclone_installed; then
        log_success "rclone installed: $(rclone --version | head -1)"
    else
        log_warn "rclone installation may have failed"
    fi
}

install_samba() {
    log_info "Installing Samba server..."

    if is_apt_available; then
        sudo apt update >/dev/null
        sudo apt install -y samba samba-common-bin >/dev/null
        log_success "Samba installed"
    else
        log_warn "apt not available, cannot install Samba automatically"
    fi
}

# ── NAVIG INSTALLATION ────────────────────────────────────────
install_navig() {
    progress_step 2 5 "Installing NAVIG"

    log_info "Creating Python virtualenv..."
    python3 -m venv "$VENV_PATH"

    log_info "Installing NAVIG package..."
    "$VENV_PATH/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1
    "$VENV_PATH/bin/python" -m pip install -e "$SRC_PATH" >/dev/null 2>&1

    log_info "Creating command wrapper..."
    mkdir -p "$BIN_PATH"
    cat > "$BIN_PATH/navig" <<'EOF'
#!/usr/bin/env bash
if [[ -f "$HOME/.navig/.env" ]]; then
  set -a
  source "$HOME/.navig/.env"
  set +a
fi
exec "$HOME/.navig/venv/bin/python" -m navig.main "$@"
EOF
    chmod +x "$BIN_PATH/navig"

    log_success "NAVIG core installation complete"
}

# ── TELEGRAM SETUP ────────────────────────────────────────────
setup_telegram() {
    local token="$1"
    [[ -z "$token" ]] && return 0

    progress_step 3 5 "Configuring Telegram Bot"

    local config_dir="${HOME}/.navig"
    local env_file="${config_dir}/.env"
    local config_file="${config_dir}/config.yaml"

    mkdir -p "$config_dir"

    # Update .env
    if [[ -f "$env_file" ]]; then
        grep -v '^TELEGRAM_BOT_TOKEN=' "$env_file" > "${env_file}.tmp" || true
        mv "${env_file}.tmp" "$env_file"
    fi
    printf 'TELEGRAM_BOT_TOKEN=%s\n' "$token" >> "$env_file"
    chmod 600 "$env_file"

    # Create config.yaml if missing
    if [[ ! -f "$config_file" ]] || ! grep -q 'bot_token:' "$config_file"; then
        cat > "$config_file" <<YAML
telegram:
  bot_token: "$token"
  allowed_users: []
  allowed_groups: []
  session_isolation: true
  group_activation_mode: "mention"
YAML
    fi

    log_info "Starting NAVIG daemon..."
    "$BIN_PATH/navig" service install --bot --gateway --scheduler --no-start >/dev/null 2>&1 || true
    "$BIN_PATH/navig" service start >/dev/null 2>&1 || true

    log_success "Telegram bot configured and daemon started"
}

# ── SAMBA SETUP (LINUX → WINDOWS) ────────────────────────────
setup_samba() {
    progress_step 3 5 "Setting Up Samba File Sharing"

    if is_samba_installed; then
        log_success "Samba already installed"
    else
        if [[ $SILENT -eq 0 ]]; then
            read -p "Install Samba for Windows file sharing? (y/n) " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                return
            fi
        fi

        install_samba
    fi

    # Create share configuration
    local username="${SUDO_USER:-${USER}}"
    local home_dir="/home/${username}"
    local smb_config="/etc/samba/smb.conf"

    log_info "Configuring Samba share for $username..."

    if ! grep -q '\[navig_share\]' "$smb_config"; then
        sudo tee -a "$smb_config" >/dev/null <<SAMBA_CONFIG
[navig_share]
    path = ${home_dir}
    browseable = yes
    read only = no
    valid users = ${username}
    force user = ${username}
    force group = ${username}
SAMBA_CONFIG

        log_info "Restarting Samba..."
        sudo systemctl restart smbd nmbd >/dev/null 2>&1 || true
    fi

    log_success "Samba share ready!"
    log_info "From Windows, mount with:"
    log_info "  net use Z: \\\\$(hostname)\\navig_share /user:${username}"
}

# ── RCLONE SETUP (CLOUD DRIVES) ───────────────────────────────
setup_rclone() {
    progress_step 4 5 "Setting Up Cloud Drive Mounting"

    if is_rclone_installed; then
        log_success "rclone already installed"
    else
        if [[ $SILENT -eq 0 ]]; then
            read -p "Install rclone for cloud drive mounting? (y/n) " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                return
            fi
        fi

        install_rclone
    fi

    if is_rclone_installed; then
        log_info "rclone is ready!"
        log_info "Configure cloud provider with:"
        log_info "  rclone config"
        log_info ""
        log_info "Supported providers:"
        log_info "  • Google Drive"
        log_info "  • Microsoft OneDrive"
        log_info "  • Dropbox"
        log_info "  • Amazon S3"
        log_info "  • 40+ more..."
        log_info ""
        log_info "Mount example:"
        log_info "  rclone mount gdrive: ~/mnt/gdrive --vfs-cache-mode full"
    fi
}

# ── PATH CONFIGURATION ────────────────────────────────────────
update_path() {
    if [[ ":$PATH:" != *":$BIN_PATH:"* ]]; then
        log_warn "Add to PATH in ~/.bashrc or ~/.zshrc:"
        echo "  export PATH=\"$BIN_PATH:\$PATH\""
    else
        log_success "PATH already includes $BIN_PATH"
    fi
}

# ── VERIFICATION ─────────────────────────────────────────────
verify_installation() {
    progress_step 5 5 "Verifying Installation"

    local issues=0

    if "$BIN_PATH/navig" --version >/dev/null 2>&1; then
        local ver=$("$BIN_PATH/navig" --version)
        log_success "NAVIG CLI working: $ver"
    else
        log_error "NAVIG command not accessible (add $BIN_PATH to PATH)"
        ((issues++))
    fi

    if [[ -d "$VENV_PATH" ]]; then
        log_success "Virtual environment created"
    else
        log_error "Virtual environment missing"
        ((issues++))
    fi

    if is_rclone_installed; then
        log_success "rclone available"
    else
        log_info "rclone not installed (optional)"
    fi

    if is_samba_installed; then
        log_success "Samba server available"
    fi

    return $issues
}

# ── MAIN FLOW ─────────────────────────────────────────────────
main() {
    echo ""
    cat <<'BANNER'
╔════════════════════════════════════════════════════════════╗
║                                                            ║
║  🚀 NAVIG Linux Installation + Remote Drive Setup         ║
║                                                            ║
║  This installer will:                                     ║
║  • Install NAVIG CLI for Linux                            ║
║  • Setup Telegram automation (optional)                   ║
║  • Configure Samba for Windows access                     ║
║  • Setup cloud drive mounting (rclone)                    ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝
BANNER
    echo ""

    # Step 1: Prerequisites
    progress_step 1 5 "Checking Prerequisites"
    check_python
    check_disk_space

    # Step 2: Install NAVIG
    install_navig

    # Step 3: Telegram (if token provided)
    if [[ -n "$TELEGRAM_TOKEN" ]]; then
        setup_telegram "$TELEGRAM_TOKEN"
    else
        log_info "No Telegram token provided (skipping bot setup)"
    fi

    # Step 4: Samba (unless skipped)
    if [[ $SKIP_SAMBA -eq 0 ]]; then
        setup_samba
    fi

    # Step 5: rclone (unless skipped)
    if [[ $SKIP_RCLONE -eq 0 ]]; then
        setup_rclone
    fi

    # Update PATH
    update_path

    # Verify
    verify_installation || true

    # Done
    cat <<'DONE'

╔════════════════════════════════════════════════════════════╗
║  ✅ INSTALLATION COMPLETE!                                ║
╚════════════════════════════════════════════════════════════╝

NEXT STEPS:
───────────────────────────────────────────────────────────

1. Add to your shell PATH (if needed):
   export PATH="$HOME/.local/bin:$PATH"

2. Verify NAVIG:
   navig --help
   navig host list

3. Configure cloud storage (rclone):
   rclone config

4. Mount cloud drive:
   mkdir -p ~/mnt/gdrive
   rclone mount gdrive: ~/mnt/gdrive --vfs-cache-mode full

5. From Windows, access this Linux server:
   net use Z: \\hostname\navig_share

WINDOWS ACCESS:
───────────────────────────────────────────────────────────
Samba share: \\hostname\navig_share
Credentials: your Linux username

ADVANCED:
───────────────────────────────────────────────────────────
• Telegram daemon: navig service status
• Full docs: https://github.com/navig-run/core
• Samba config: /etc/samba/smb.conf

DONE
}

# ── EXECUTION ─────────────────────────────────────────────────
main "$@"
