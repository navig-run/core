#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# NAVIG Installer - Linux / macOS / WSL
# No Admin Visible In Graveyard · Keep your servers alive. Forever.
#
# Usage:
#   curl -fsSL https://navig.run/install.sh | bash
#   curl -fsSL https://navig.run/install.sh | bash -s -- --version <release>
#   curl -fsSL https://navig.run/install.sh | bash -s -- --dev
#
# Environment variables:
#   NAVIG_VERSION          Pin version (e.g. "2.4.14")
#   NAVIG_INSTALL_METHOD   "pip" (default) or "git"
#   NAVIG_NO_CONFIRM       "1" to skip prompts
#   NAVIG_EXTRAS           Comma-separated extras (e.g. "voice,keyring")
#   NAVIG_INSTALL_PROFILE  Install profile: node, operator, architect (default: operator)
# ─────────────────────────────────────────────────────────────
set -euo pipefail

# ── Colors (suppressed when stdout is not a terminal) ───────────
if [ -t 1 ]; then
    BOLD='\033[1m'
    DIM='\033[2m'
    ACCENT='\033[1;36m'    # Cyan
    SUCCESS='\033[1;32m'   # Green
    WARN='\033[1;33m'      # Yellow
    ERROR='\033[1;31m'     # Red
    INFO='\033[0;36m'      # Light cyan
    NC='\033[0m'           # No Color
else
    BOLD='' DIM='' ACCENT='' SUCCESS='' WARN='' ERROR='' INFO='' NC=''
fi

# ── Temp cleanup ──────────────────────────────────────────────
TMPFILES=()
cleanup() { for f in "${TMPFILES[@]}"; do rm -f "$f" 2>/dev/null || true; done; }
trap cleanup EXIT
mktempfile() { local t; t="$(mktemp)"; TMPFILES+=("$t"); echo "$t"; }

# ── Globals ───────────────────────────────────────────────────
OS=""
ARCH=""
PYTHON_CMD=""
PIP_CMD=()
VERSION="${NAVIG_VERSION:-}"
INSTALL_METHOD="${NAVIG_INSTALL_METHOD:-pip}"
EXTRAS="${NAVIG_EXTRAS:-}"
INSTALL_PROFILE="${NAVIG_INSTALL_PROFILE:-operator}"
GIT_DIR="${HOME}/navig-core"
GIT_UPDATE=1
PRODUCTION=${PRODUCTION:-0}
NO_CONFIRM="${NAVIG_NO_CONFIRM:-0}"
DRY_RUN=0
VERBOSE=0
HELP=0
DEV_MODE=0
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
REPO_URL="https://github.com/navig-run/core.git"

# ── Taglines ──────────────────────────────────────────────────
TAGLINES=(
    "Your servers are in good hands now."
    "No admin visible in graveyard? Perfect."
    "SSH tunnels, remote ops - all in one CLI."
    "Because server management shouldn't feel like surgery."
    "ctrl+c to exit. But why would you?"
    "Keeping uptime personal since 2024."
    "One CLI to rule them all."
    "Servers don't sleep, and neither does NAVIG."
    "Remote ops, local comfort."
    "Born in the terminal. Lives in the cloud."
    "Your devops sidekick. No cape required."
    "Deploy, manage, survive. Repeat."
    "Less SSH, more SHH - it just works."
    "The quiet guardian of your infrastructure."
    "Admin by day, daemon by night."
)

pick_tagline() {
    echo "${TAGLINES[RANDOM % ${#TAGLINES[@]}]}"
}

# ── Banner ────────────────────────────────────────────────────
print_banner() {
    local v=""
    if [ -n "$VERSION" ]; then
        v="v$VERSION "
    fi
    echo ""
    echo -e "${ACCENT}${BOLD}  NAVIG ${v}${NC}- ${DIM}$(pick_tagline)${NC}"
    echo ""
}

# ── Usage ─────────────────────────────────────────────────────
print_usage() {
    echo "NAVIG Installer"
    echo ""
    echo "Usage:"
    echo "  curl -fsSL https://navig.run/install.sh | bash"
    echo "  curl -fsSL https://navig.run/install.sh | bash -s -- [ACTION] [OPTIONS]"
    echo ""
    echo "Actions:"
    echo "  install           Install NAVIG (default)"
    echo "  uninstall         Uninstall NAVIG"
    echo "  reinstall         Uninstall (preserving data) and reinstall"

    echo ""
    echo "Options:"
    echo "  --version <ver>   Install specific version (e.g. 2.4.14)"
    echo "  --method <mode>   Install via pip or git (default: pip)"
    echo "  --dev             Install from git source (dev mode)"
    echo "  --git-dir <path>  Git checkout directory (default: ~/navig-core)"
    echo "  --extras <list>   Comma-separated extras: voice,keyring,dev"
    echo "  --profile <name>  Install profile: node, operator, architect (default: operator)"
    echo "  --no-confirm      Skip interactive prompts"
    echo "  --dry-run         Preview actions without executing"
    echo "  --verbose         Show detailed output"
    echo "  --help            Show this help"
    echo ""
    echo "Environment variables:"
    echo "  NAVIG_VERSION          Pin version"
    echo "  NAVIG_INSTALL_METHOD   pip (default) or git"
    echo "  NAVIG_NO_CONFIRM       1 to skip prompts"
    echo "  NAVIG_EXTRAS           Comma-separated extras"
    echo "  NAVIG_INSTALL_PROFILE  Install profile (default: operator)"
}

ACTION="install"

# ── Argument parsing ──────────────────────────────────────────
parse_args() {
    # Check positional action
    if [[ $# -gt 0 && ! "$1" == --* ]]; then
        case "$1" in
            install|uninstall|reinstall)
                ACTION="$1"
                shift
                ;;
            *)
                echo -e "${ERROR}Unknown action: $1${NC}"
                print_usage
                exit 1
                ;;
        esac
    fi

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --uninstall)    ACTION="uninstall"; shift ;;
            --reinstall)    ACTION="reinstall"; shift ;;
            --version)      VERSION="$2"; shift 2 ;;
            --method)       INSTALL_METHOD="$2"; shift 2 ;;
            --dev)          INSTALL_METHOD="git"; DEV_MODE=1; shift ;;
            --git-dir)      GIT_DIR="$2"; shift 2 ;;
            --extras)       EXTRAS="$2"; shift 2 ;;
            --profile)      INSTALL_PROFILE="$2"; shift 2 ;;
            --production)   PRODUCTION=1; shift ;;
            --no-confirm)   NO_CONFIRM=1; shift ;;
            --dry-run)      DRY_RUN=1; shift ;;
            --verbose)      VERBOSE=1; shift ;;
            --help|-h)      HELP=1; shift ;;
            *)
                echo -e "${ERROR}Unknown option: $1${NC}"
                print_usage
                exit 1
                ;;
        esac
    done
}

configure_verbose() {
    if [[ "$VERBOSE" == "1" ]]; then
        # Note: set -x traces all commands including those that expand env vars.
        # Avoid --verbose on systems where secrets (e.g. NAVIG_TELEGRAM_BOT_TOKEN)
        # are in the environment — they will appear in output.
        set -x
    fi
}

# ── OS Detection ──────────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Darwin*)     OS="macos" ;;
        Linux*)
            if grep -qiE "microsoft|wsl" /proc/version 2>/dev/null; then
                OS="wsl"
            else
                OS="linux"
            fi
            ;;
        MINGW*|MSYS*|CYGWIN*)
            echo -e "${ERROR}Error: Windows detected. Use install.ps1 instead.${NC}"
            echo "  & ([scriptblock]::Create((irm https://navig.run/install.ps1)))"
            exit 1
            ;;
        *)
            echo -e "${ERROR}Error: Unsupported operating system: $(uname -s)${NC}"
            exit 1
            ;;
    esac
    ARCH="$(uname -m)"
    echo -e "${SUCCESS}✓${NC} OS: ${INFO}${OS}${NC} (${ARCH})"
}

# ── Privilege helpers ─────────────────────────────────────────
is_root() { [[ "$(id -u)" -eq 0 ]]; }

maybe_sudo() {
    if is_root; then
        [[ "${1:-}" == "-E" ]] && shift
        "$@"
    else
        sudo "$@"
    fi
}

require_sudo() {
    [[ "$OS" == "macos" ]] && return 0
    is_root && return 0
    if command -v sudo &>/dev/null; then
        return 0
    fi
    echo -e "${ERROR}Error: sudo is required for system installs on Linux${NC}"
    echo "Install sudo or re-run as root."
    exit 1
}

# ── Homebrew (macOS) ──────────────────────────────────────────
install_homebrew() {
    [[ "$OS" != "macos" ]] && return 0
    if command -v brew &>/dev/null; then
        echo -e "${SUCCESS}✓${NC} Homebrew already installed"
        return 0
    fi
    echo -e "${WARN}→${NC} Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [[ -f "/opt/homebrew/bin/brew" ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -f "/usr/local/bin/brew" ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    echo -e "${SUCCESS}✓${NC} Homebrew installed"
}

# ── Python Detection & Installation ──────────────────────────
detect_python() {
    local candidates=("python3" "python" "python3.13" "python3.12" "python3.11" "python3.10")
    for cmd in "${candidates[@]}"; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            # grep -oE is portable to both GNU grep and macOS/BSD grep
            # grep -oP (Perl regex) is NOT available on macOS stock grep
            ver="$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)"
            local major minor
            major="$(echo "$ver" | cut -d. -f1)"
            minor="$(echo "$ver" | cut -d. -f2)"
            if [[ "$major" -ge "$MIN_PYTHON_MAJOR" && "$minor" -ge "$MIN_PYTHON_MINOR" ]]; then
                PYTHON_CMD="$cmd"
                echo -e "${SUCCESS}✓${NC} Python ${INFO}$("$cmd" --version 2>&1)${NC} found"
                return 0
            fi
        fi
    done
    return 1
}

detect_pip() {
    # Store PIP_CMD as an array to prevent word-splitting on "python3 -m pip"
    if "$PYTHON_CMD" -m pip --version &>/dev/null; then
        PIP_CMD=("$PYTHON_CMD" -m pip)
        return 0
    fi
    # Try standalone pip3/pip
    for cmd in pip3 pip; do
        if command -v "$cmd" &>/dev/null; then
            PIP_CMD=("$cmd")
            return 0
        fi
    done
    return 1
}

# ── Dedup apt-get update ─────────────────────────────────────
_APT_UPDATED=0
_apt_update() {
    if [[ "$_APT_UPDATED" == "0" ]]; then
        maybe_sudo apt-get update -y
        _APT_UPDATED=1
    fi
}

install_python() {
    echo -e "${WARN}→${NC} Installing Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+..."
    if [[ "$OS" == "macos" ]]; then
        brew install python@3.12
        brew link python@3.12 --overwrite --force 2>/dev/null || true
    elif [[ "$OS" == "linux" || "$OS" == "wsl" ]]; then
        require_sudo
        if command -v apt-get &>/dev/null; then
            _apt_update
            maybe_sudo apt-get install -y python3 python3-pip python3-venv
        elif command -v dnf &>/dev/null; then
            maybe_sudo dnf install -y python3 python3-pip
        elif command -v yum &>/dev/null; then
            maybe_sudo yum install -y python3 python3-pip
        elif command -v pacman &>/dev/null; then
            maybe_sudo pacman -S --noconfirm python python-pip
        elif command -v apk &>/dev/null; then
            maybe_sudo apk add python3 py3-pip
        else
            echo -e "${ERROR}Error: Could not detect package manager${NC}"
            echo "Please install Python 3.8+ manually: https://python.org"
            exit 1
        fi
    fi
    echo -e "${SUCCESS}✓${NC} Python installed"
}

# ── Git ───────────────────────────────────────────────────────
check_git() {
    if command -v git &>/dev/null; then
        echo -e "${SUCCESS}✓${NC} Git already installed"
        return 0
    fi
    return 1
}

install_git() {
    echo -e "${WARN}→${NC} Installing Git..."
    if [[ "$OS" == "macos" ]]; then
        brew install git
    elif [[ "$OS" == "linux" || "$OS" == "wsl" ]]; then
        require_sudo
        if command -v apt-get &>/dev/null; then
            _apt_update
            maybe_sudo apt-get install -y git
        elif command -v dnf &>/dev/null; then
            maybe_sudo dnf install -y git
        elif command -v yum &>/dev/null; then
            maybe_sudo yum install -y git
        elif command -v pacman &>/dev/null; then
            maybe_sudo pacman -S --noconfirm git
        elif command -v apk &>/dev/null; then
            maybe_sudo apk add git
        fi
    fi
    echo -e "${SUCCESS}✓${NC} Git installed"
}

# ── SSH client check ──────────────────────────────────────────
check_ssh() {
    if command -v ssh &>/dev/null; then
        echo -e "${SUCCESS}✓${NC} SSH client available"
        return 0
    fi
    echo -e "${WARN}→${NC} Installing OpenSSH client..."
    if [[ "$OS" == "macos" ]]; then
        echo -e "${SUCCESS}✓${NC} SSH should be built-in on macOS"
        return 0
    elif [[ "$OS" == "linux" || "$OS" == "wsl" ]]; then
        require_sudo
        if command -v apt-get &>/dev/null; then
            maybe_sudo apt-get install -y openssh-client
        elif command -v dnf &>/dev/null; then
            maybe_sudo dnf install -y openssh-clients
        elif command -v yum &>/dev/null; then
            maybe_sudo yum install -y openssh-clients
        fi
    fi
    echo -e "${SUCCESS}✓${NC} SSH client installed"
}

# ── autossh (persistent SSH tunnels for Bridge) ────────────────
check_autossh() {
    if command -v autossh &>/dev/null; then
        echo -e "${SUCCESS}✓${NC} autossh available"
        return 0
    fi
    echo -e "${WARN}→${NC} Installing autossh (required for persistent Bridge tunnels)..."
    if [[ "$OS" == "macos" ]]; then
        if command -v brew &>/dev/null; then
            brew install autossh
        else
            echo -e "${WARN}!${NC} Install autossh manually: brew install autossh"
            return 0
        fi
    elif [[ "$OS" == "linux" || "$OS" == "wsl" ]]; then
        require_sudo
        if command -v apt-get &>/dev/null; then
            maybe_sudo apt-get install -y autossh
        elif command -v dnf &>/dev/null; then
            maybe_sudo dnf install -y autossh
        elif command -v yum &>/dev/null; then
            maybe_sudo yum install -y autossh
        elif command -v pacman &>/dev/null; then
            maybe_sudo pacman -S --noconfirm autossh
        elif command -v apk &>/dev/null; then
            maybe_sudo apk add autossh
        fi
    fi
    if command -v autossh &>/dev/null; then
        echo -e "${SUCCESS}✓${NC} autossh installed"
    else
        echo -e "${WARN}!${NC} autossh install failed - Bridge tunnel auto-reconnect won't work"
    fi
}

# ── pip install helpers ───────────────────────────────────────
ensure_pip_user_bin_on_path() {
    local user_base
    user_base="$("$PYTHON_CMD" -m site --user-base 2>/dev/null || echo "$HOME/.local")"
    local bin_dir="${user_base}/bin"
    mkdir -p "$bin_dir"
    export PATH="$bin_dir:$PATH"

    # shellcheck disable=SC2016
    local path_line="export PATH=\"${bin_dir}:\$PATH\""
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        if [[ -f "$rc" ]] && ! grep -q "${bin_dir}" "$rc"; then
            echo "" >> "$rc"
            echo "# NAVIG CLI" >> "$rc"
            echo "$path_line" >> "$rc"
        fi
    done
}

# ── pipx detection & install ──────────────────────────────────
check_install_pipx() {
    if command -v pipx &>/dev/null; then
        echo -e "${SUCCESS}✓${NC} pipx available"
        return 0
    fi
    echo -e "${WARN}→${NC} Installing pipx (isolated installs)..."
    if ! "${PIP_CMD[@]}" install --user pipx --quiet 2>/dev/null; then
        echo -e "${WARN}!${NC} pipx install failed - using pip --user fallback"
        return 0
    fi
    # Expose the freshly installed pipx in the current session
    local user_base
    user_base="$("$PYTHON_CMD" -m site --user-base 2>/dev/null || echo "$HOME/.local")"
    export PATH="${user_base}/bin:$PATH"
    hash -r 2>/dev/null || true
    if command -v pipx &>/dev/null; then
        pipx ensurepath --quiet 2>/dev/null || true
        echo -e "${SUCCESS}✓${NC} pipx installed and on PATH"
    else
        echo -e "${WARN}!${NC} pipx installed but not on PATH yet - restart shell or source ~/.bashrc"
    fi
}

# ── Install via pip ───────────────────────────────────────────
install_navig_pip() {
    local install_spec="navig"
    if [[ -n "$VERSION" ]]; then
        install_spec="navig==${VERSION}"
    fi

    # Add extras if specified
    if [[ -n "$EXTRAS" ]]; then
        install_spec="navig[${EXTRAS}]"
        [[ -n "$VERSION" ]] && install_spec="navig[${EXTRAS}]==${VERSION}"
    fi

    echo -e "${WARN}→${NC} Installing NAVIG via pip: ${INFO}${install_spec}${NC}"

    local pip_args=("install" "--upgrade")

    # Use --user if not root and not in a venv
    if ! is_root && [[ -z "${VIRTUAL_ENV:-}" ]]; then
        pip_args+=("--user")
    fi

    pip_args+=("$install_spec")

    if ! "${PIP_CMD[@]}" "${pip_args[@]}"; then
        echo -e "${ERROR}Error: pip install failed${NC}"
        echo -e "Try manually: ${INFO}pip install ${install_spec}${NC}"
        exit 1
    fi

    echo -e "${SUCCESS}✓${NC} NAVIG installed via pip"
}

# ── Install via git ───────────────────────────────────────────
install_navig_git() {
    local repo_dir="$GIT_DIR"

    if [[ -d "$repo_dir/.git" ]]; then
        echo -e "${WARN}→${NC} Updating existing NAVIG checkout: ${INFO}${repo_dir}${NC}"
    else
        echo -e "${WARN}→${NC} Installing NAVIG from source: ${INFO}${REPO_URL}${NC}"
    fi

    if ! check_git; then
        install_git
    fi

    if [[ ! -d "$repo_dir" ]]; then
        git clone "$REPO_URL" "$repo_dir"
    elif [[ "$GIT_UPDATE" == "1" ]]; then
        if [[ -z "$(git -C "$repo_dir" status --porcelain 2>/dev/null || true)" ]]; then
            git -C "$repo_dir" pull --rebase || true
        else
            echo -e "${WARN}→${NC} Repo is dirty; skipping git pull"
        fi
    fi

    if [[ "$PRODUCTION" == "1" ]]; then
        echo -e "${WARN}→${NC} Installing NAVIG from source (production - no editable install)..."
        # Non-editable: no __editable__ finder overhead (~20ms startup savings)
        local pip_args=("install")
        if [[ -n "$EXTRAS" ]]; then
            pip_args+=("${repo_dir}[${EXTRAS}]")
        else
            pip_args+=("$repo_dir")
        fi
    else
        echo -e "${WARN}→${NC} Installing NAVIG in editable mode..."
        local pip_args=("install" "-e")
        if [[ -n "$EXTRAS" ]]; then
            pip_args+=("${repo_dir}[${EXTRAS}]")
        else
            pip_args+=("$repo_dir")
        fi
    fi

    "${PIP_CMD[@]}" "${pip_args[@]}"

    echo -e "${SUCCESS}✓${NC} NAVIG installed from source"
    echo -e "${INFO}i${NC} Source directory: ${INFO}${repo_dir}${NC}"
    echo -e "${INFO}i${NC} To update: ${INFO}cd ${repo_dir} && git pull && pip install -e .${NC}"
}

# ── Setup NAVIG config directory ──────────────────────────────
setup_navig_config() {
    local config_dir="$HOME/.navig"
    mkdir -p "$config_dir"
    mkdir -p "$config_dir/workspace"
    mkdir -p "$config_dir/logs"
    mkdir -p "$config_dir/cache"

    echo -e "${SUCCESS}✓${NC} Config directory: ${INFO}${config_dir}${NC}"
}

# ── Check existing installation ───────────────────────────────
check_existing_navig() {
    if command -v navig &>/dev/null; then
        local current_ver
        current_ver="$(navig --version 2>/dev/null | head -1 || echo "unknown")"
        echo -e "${WARN}→${NC} Existing NAVIG installation detected: ${INFO}${current_ver}${NC}"
        return 0
    fi
    return 1
}

# ── Resolve installed version ─────────────────────────────────
resolve_navig_version() {
    if command -v navig &>/dev/null; then
        navig --version 2>/dev/null | head -1 || echo ""
    elif [[ ${#PIP_CMD[@]} -gt 0 ]]; then
        "${PIP_CMD[@]}" show navig 2>/dev/null | grep -i "^version:" | awk '{print $2}' || echo ""
    else
        echo ""
    fi
}

# ── Post-install verification ─────────────────────────────────
verify_install() {
    hash -r 2>/dev/null || true

    if command -v navig &>/dev/null; then
        echo -e "${SUCCESS}✓${NC} navig command available"
        return 0
    fi

    # Check pip user bin
    ensure_pip_user_bin_on_path
    hash -r 2>/dev/null || true

    if command -v navig &>/dev/null; then
        echo -e "${SUCCESS}✓${NC} navig command available (in user bin)"
        return 0
    fi

    echo -e "${WARN}→${NC} navig installed but not on PATH"
    local user_base
    user_base="$("$PYTHON_CMD" -m site --user-base 2>/dev/null || echo "$HOME/.local")"
    echo -e "  Add to your shell profile:"
    echo -e "  ${INFO}export PATH=\"${user_base}/bin:\$PATH\"${NC}"
    echo -e "  Then restart your terminal or run: ${INFO}source ~/.bashrc${NC}"
    return 1
}


# ── Uninstall Logic ───────────────────────────────────────────
uninstall_navig() {
    local preserve_data="${1:-0}"
    echo -e "${WARN}→${NC} Uninstalling NAVIG..."

    # Non-blocking failure log for uninstall
    set +e
    local log_file="${HOME}/.navig/logs/uninstall-fail.log"
    mkdir -p "${HOME}/.navig/logs" 2>/dev/null || true

    _try() {
        if ! "$@" >> "$log_file" 2>&1; then
            echo -e "  ${WARN}!${NC} Failed: $* (see $log_file)"
        fi
    }

    # Step A: Stop daemon
    if command -v navig &>/dev/null; then
        _try navig service stop
        _try navig service uninstall
    fi

    # Step B: pip uninstall
    local pip_cmd=""
    command -v pip3 &>/dev/null && pip3 show navig &>/dev/null && pip_cmd="pip3"
    command -v pip &>/dev/null && pip show navig &>/dev/null && pip_cmd="${pip_cmd:-pip}"
    if [[ -n "$pip_cmd" ]]; then
        _try "$pip_cmd" uninstall navig -y
    fi

    # Step C: Remove binary
    local user_base="$("$PYTHON_CMD" -m site --user-base 2>/dev/null || echo "$HOME/.local")"
    local bin_dir="${user_base}/bin"
    if [[ -f "${bin_dir}/navig" || -L "${bin_dir}/navig" ]]; then
        _try rm -f "${bin_dir}/navig"
    fi

    # Step D: Remove git clone
    if [[ "$INSTALL_METHOD" == "git" && -d "$GIT_DIR" ]]; then
        _try rm -rf "$GIT_DIR"
    fi

    # Step E: Clean shell profiles (atomic: write to tmp first, then mv)
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        if [[ -f "$rc" ]] && grep -q "# NAVIG CLI" "$rc" 2>/dev/null; then
            local rctmp
            rctmp="$(mktemp)"
            if grep -v "# NAVIG CLI" "$rc" > "$rctmp" 2>/dev/null; then
                _try mv "$rctmp" "$rc"
            else
                rm -f "$rctmp"
            fi
        fi
    done

    # Step F: Remove ~/.navig config dir
    local navig_dir="${HOME}/.navig"
    if [[ -d "$navig_dir" ]]; then
        if [[ "$preserve_data" == "1" ]]; then
            echo -e "  ${INFO}✓${NC} Preserving user data in $navig_dir"
            _try find "$navig_dir" -mindepth 1 -maxdepth 1 ! -name 'vault' ! -name 'logs' -exec rm -rf {} +
        else
            _try rm -rf "$navig_dir"
        fi
    fi

    # Step G: Remove cron jobs
    local existing_cron="$(crontab -l 2>/dev/null || true)"
    if [[ -n "$existing_cron" ]] && echo "$existing_cron" | grep -qi navig; then
        echo "$existing_cron" | grep -iv navig | crontab - 2>/dev/null || true
    fi

    set -e
    echo -e "${SUCCESS}✓${NC} Uninstall complete."
}

# ── Main ──────────────────────────────────────────────────────
main() {
    if [[ "$HELP" == "1" ]]; then
        print_usage
        return 0
    fi

    # Root guard - running as root is risky; require explicit opt-in
    if [[ "$(id -u)" -eq 0 ]] && [[ "${NAVIG_ALLOW_ROOT:-0}" != "1" ]]; then
        echo -e "${ERROR}Error: do not run the installer as root.${NC}"
        echo -e "  NAVIG installs per-user, not system-wide."
        echo -e "  To override: ${INFO}NAVIG_ALLOW_ROOT=1 bash install.sh${NC}"
        exit 1
    fi

    print_banner

    if [[ "$DRY_RUN" == "1" ]]; then
        echo -e "${INFO}Dry run mode - no changes will be made${NC}"
        echo -e "  OS detection:     $(uname -s) / $(uname -m)"
        echo -e "  Install method:   ${INSTALL_METHOD}"
        echo -e "  Version:          ${VERSION:-latest}"
        echo -e "  Extras:           ${EXTRAS:-none}"
        echo -e "  Profile:          ${INSTALL_PROFILE}"
        echo -e "  Git dir:          ${GIT_DIR}"
        echo -e "${DIM}Dry run complete.${NC}"
        return 0
    fi

    # Step 0: Detect OS
    detect_os

    # Handle state marker and prompt
    local marker="${HOME}/.navig/.install_state"
    if [[ -f "$marker" && "$ACTION" == "install" && "$NO_CONFIRM" == "0" ]]; then
        echo -e "${INFO}NAVIG is already installed.${NC}"
        echo "1) Repair / Reinstall (preserve data)"
        echo "2) Uninstall"
        echo "3) Cancel"
        # Only prompt when stdin is an interactive terminal
        if [[ -t 0 ]]; then
            read -rp "Select an option [1-3]: " opt
        else
            echo -e "  ${INFO}Non-interactive session - skipping prompt, continuing install.${NC}"
            opt=""
        fi
        case "$opt" in
            1) ACTION="reinstall" ;;
            2) ACTION="uninstall" ;;
            *) echo "Cancelled."; exit 0 ;;
        esac
    fi

    if [[ "$ACTION" == "uninstall" ]]; then
        detect_python
        uninstall_navig 0
        if [[ -f "$marker" ]]; then rm -f "$marker"; fi
        return 0
    elif [[ "$ACTION" == "reinstall" ]]; then
        detect_python
        uninstall_navig 1
    fi

    # Step 1: Check existing installation
    local is_upgrade=false
    if check_existing_navig; then
        is_upgrade=true
    fi

    # Step 2: Homebrew (macOS only)
    install_homebrew

    # Step 3: Python
    if ! detect_python; then
        install_python
        if ! detect_python; then
            echo -e "${ERROR}Error: Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required but could not be found after install${NC}"
            exit 1
        fi
    fi

    # Step 4: pip
    if ! detect_pip; then
        echo -e "${WARN}→${NC} Installing pip..."
        "$PYTHON_CMD" -m ensurepip --upgrade 2>/dev/null || {
            curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$PYTHON_CMD"
        }
        if ! detect_pip; then
            echo -e "${ERROR}Error: pip is required but could not be installed${NC}"
            exit 1
        fi
    fi
    echo -e "${SUCCESS}✓${NC} pip available"

    # Step 5: SSH client
    check_ssh

    # Step 5.5: autossh (for persistent Bridge tunnels)
    check_autossh

    # Step 6: Install NAVIG
    if [[ "$INSTALL_METHOD" == "git" ]]; then
        if ! check_git; then
            install_git
        fi
        install_navig_git
    else
        # Ensure pip user bin is on PATH for --user installs
        ensure_pip_user_bin_on_path
        # Step 5.6: Offer pipx for isolated installs (non-root, non-venv)
        if ! is_root && [[ -z "${VIRTUAL_ENV:-}" ]]; then
            check_install_pipx
        fi
        install_navig_pip
    fi

    # Step 7: Setup config directory
    setup_navig_config

    # Step 8: Verify installation
    local installed_version
    verify_install || true
    installed_version="$(resolve_navig_version)"


    # Write install state marker
    echo "installed" > "${HOME}/.navig/.install_state"

    # ── Success ───────────────────────────────────────────────
    echo ""
    if [[ -n "$installed_version" ]]; then
        echo -e "${SUCCESS}${BOLD}⚡ NAVIG installed successfully (v${installed_version})!${NC}"
    else
        echo -e "${SUCCESS}${BOLD}⚡ NAVIG installed successfully!${NC}"
    fi

    if [[ "$is_upgrade" == "true" ]]; then
        local upgrade_msgs=(
            "Upgraded and operational. Your servers barely noticed."
            "New version, same mission. Keeping things alive."
            "Back online with fresh powers."
            "Updated. The uptime counter continues."
            "Patched and ready. Your infrastructure thanks you."
        )
        echo -e "${DIM}${upgrade_msgs[RANDOM % ${#upgrade_msgs[@]}]}${NC}"
    else
        local fresh_msgs=(
            "Welcome aboard. Let's keep those servers alive."
            "Installed. Time to manage some infrastructure."
            "Ready to go. Run 'navig' to get started."
            "The terminal just got more powerful."
            "Your devops workflow just leveled up."
        )
        echo -e "${DIM}${fresh_msgs[RANDOM % ${#fresh_msgs[@]}]}${NC}"
    fi

    echo ""
    echo -e "Get started:"
    echo -e "  ${INFO}navig${NC}                           Open interactive menu"
    echo -e "  ${INFO}navig host add${NC}                  Add your first server"
    echo -e "  ${INFO}navig help${NC}                      Show available commands"
    echo ""
    echo -e "Run ${INFO}navig init${NC} to complete first-time setup and configuration."
    echo -e "  Run with a profile: ${INFO}navig init --profile ${INSTALL_PROFILE}${NC}"
    echo ""

    if [[ "$INSTALL_METHOD" == "git" ]]; then
        echo -e "Source: ${INFO}${GIT_DIR}${NC}"
        echo -e "Update: ${INFO}cd ${GIT_DIR} && git pull && pip install -e .${NC}"
    else
        echo -e "Update: ${INFO}pip install --upgrade navig${NC}"
    fi

    echo -e "Config: ${INFO}~/.navig/${NC}"
    echo -e "Docs:   ${INFO}https://github.com/navig-run/core${NC}"
    echo ""
}

# ── Entry point ───────────────────────────────────────────────
if [[ "${NAVIG_INSTALL_SH_NO_RUN:-0}" != "1" ]]; then
    parse_args "$@"
    configure_verbose
    # Tee all output to ~/.navig/logs/install.log.
    # Process substitution ( >(tee ...) ) requires bash 4+.
    # macOS ships with bash 3.2 — guard to avoid a syntax/runtime error.
    mkdir -p "${HOME}/.navig/logs" 2>/dev/null || true
    if (( BASH_VERSINFO[0] >= 4 )); then
        exec > >(tee -a "${HOME}/.navig/logs/install.log") 2>&1
    fi
    main
fi
