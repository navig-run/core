#!/usr/bin/env bash
# NAVIG Installer - Linux / macOS / WSL
#
# Usage:
#   curl -fsSL https://navig.run/install.sh | bash
#   bash install.sh [OPTIONS]
#
# Options:
#   -v | --version <ver>   Pin version (e.g. 2.7.0)
#   -a | --action <mode>   install (default) | uninstall | reinstall
#   -y | --yes             Skip interactive prompts
#        --verbose         Verbose output
#        --dry-run         Preview only
#   -h | --help
#
# Environment:
#   NAVIG_VERSION    Pin version
#   NAVIG_ACTION     install | uninstall | reinstall
#   NO_COLOR         Disable color
#   NAVIG_VERBOSE    Enable verbose (set to 1)

set -euo pipefail

# ── Terminal capabilities ─────────────────────────────────────
NAV_C=0  # color capable
NAV_U=0  # unicode capable

_init_term() {
    if [ -t 1 ] && [ "${NO_COLOR:-}" = "" ] && [ "${TERM:-}" != "dumb" ]; then
        NAV_C=1
        # Check UTF-8 locale
        if echo "${LANG:-}${LC_ALL:-}" | grep -qi "utf"; then
            NAV_U=1
        fi
    fi
}

# ── Terminal capabilities persistence ────────────────────────
_write_terminal_json() {
    # Write ~/.navig/terminal.json with unicode flag.
    # nerd_font is seeded to false; terminal-setup onboarding step updates it.
    local base="$HOME/.navig"
    local ts; ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "")
    local nf="false"
    printf '{"unicode":%s,"nerd_font":%s,"checked_at":"%s"}\n' \
        "$NAV_U" "$nf" "$ts" > "$base/terminal.json" 2>/dev/null || true
}


# ── Colors ────────────────────────────────────────────────────
_RST=""  _DIM=""  _WHT=""  _CYN=""  _GRN=""  _RED=""  _YLW=""  _GRY=""
_init_colors() {
    if [ "$NAV_C" = "1" ]; then
        _RST="\033[0m"
        _DIM="\033[2m"
        _WHT="\033[97m"
        _CYN="\033[96m"
        _GRN="\033[92m"
        _RED="\033[91m"
        _YLW="\033[93m"
        _GRY="\033[90m"
    fi
}

_clr() {
    local text="$1" color="${2:-}"
    printf "%b%s%b" "$color" "$text" "$_RST"
}

# ── Symbols ───────────────────────────────────────────────────
_sym() {
    local name="$1"
    if [ "$NAV_U" = "1" ]; then
        case "$name" in
            ok)     printf "\xE2\x9C\x93" ;;   # ✓
            step)   printf "\xE2\x80\xBA" ;;   # ›
            err)    printf "\xC3\x97"     ;;   # ×
            warn)   printf "!"            ;;
            bullet) printf "\xC2\xB7"    ;;   # ·
            tl)     printf "\xE2\x95\xAD" ;;  # ╭
            tr)     printf "\xE2\x95\xAE" ;;  # ╮
            bl)     printf "\xE2\x95\xB0" ;;  # ╰
            br)     printf "\xE2\x95\xB1" ;;  # ╯
            hz)     printf "\xE2\x94\x80" ;;  # ─
            vt)     printf "\xE2\x94\x82" ;;  # │
        esac
    else
        case "$name" in
            ok)     printf "OK" ;;
            step)   printf " >" ;;
            err)    printf "!!" ;;
            warn)   printf " !" ;;
            bullet) printf "."  ;;
            tl|tr|bl|br) printf "+" ;;
            hz)     printf "-"  ;;
            vt)     printf "|"  ;;
        esac
    fi
}

# ── Layout ────────────────────────────────────────────────────
_LW=52   # box inner width
_LB=12   # label column width

_hline() {
    local w="${1:-$_LW}"
    local line="" h
    h=$(_sym hz)
    local i=0
    while [ $i -lt $w ]; do
        line="${line}${h}"
        i=$((i + 1))
    done
    printf "%s" "$line"
}

_pad() {
    # pad a string to $_LB chars
    printf "%-${_LB}s" "$1"
}

# ── Taglines ──────────────────────────────────────────────────
_TAGLINES=(
    "Terminal-first. Chaos last."
    "Operate everything. Forget nothing."
    "Remote systems. Direct control."
    "One command closer to order."
    "Infrastructure without dashboard fatigue."
    "SSH, databases, containers. One operator surface."
    "Built for operators, not spectators."
    "Control returns to the terminal."
    "Run less guesswork."
    "From host to workflow, stay in NAVIG."
    "The operator system for real infrastructure."
    "Direct ops. No theater."
    "Your infrastructure, under command."
    "Less dashboard. More control."
    "Where remote operations become readable."
    "The terminal was never the problem."
    "No admin visible in graveyard."
    "Stay close to the metal."
)

# ── Header ────────────────────────────────────────────────────
print_header() {
    local lw=$((_LW + 2))
    local line; line=$(_hline $lw)
    local tl; tl=$(_sym tl)
    local tr; tr=$(_sym tr)
    local bl; bl=$(_sym bl)
    local br; br=$(_sym br)
    local vt; vt=$(_sym vt)
    printf "\n"
    printf "  %b%s%s%s%b\n" "$_GRY" "$tl" "$line" "$tr" "$_RST"
    printf "  %b%s%b\n"     "$_GRY" "$vt" "$_RST"
    printf "  %b%s%b   %bNAVIG%b\n" "$_GRY" "$vt" "$_RST" "$_CYN" "$_RST"
    local _tagline="${_TAGLINES[RANDOM % ${#_TAGLINES[@]}]}"
    printf "  %b%s%b   %b%s%b\n" "$_GRY" "$vt" "$_RST" "$_GRY" "$_tagline" "$_RST"
    printf "  %b%s%b\n"     "$_GRY" "$vt" "$_RST"
    printf "  %b%s%s%s%b\n" "$_GRY" "$bl" "$line" "$br" "$_RST"
    printf "\n"
}

# ── Section ───────────────────────────────────────────────────
print_section() {
    printf "\n  %b%s%b\n" "$_CYN" "$1" "$_RST"
}

# ── Rows ──────────────────────────────────────────────────────
_row() {
    local sym_char="$1" sym_color="$2" label="$3" value="${4:-}" val_color="${5:-$_WHT}"
    local lpad; lpad=$(_pad "$label")
    printf "  %b%s%b  %s" "$sym_color" "$sym_char" "$_RST" "$lpad"
    if [ -n "$value" ]; then
        printf "%b%s%b" "$val_color" "$value" "$_RST"
    fi
    printf "\n"
}

row_ok()   { _row "$(_sym ok)"   "$_GRN" "$1" "${2:-}" "$_WHT" ; }
row_step() { _row "$(_sym step)" "$_CYN" "$1" "${2:-}" "$_WHT" ; }
row_err()  { _row "$(_sym err)"  "$_RED" "$1" "${2:-}" "$_WHT" ; }
row_warn() { _row "$(_sym warn)" "$_YLW" "$1" "${2:-}" "$_WHT" ; }

log_verbose() {
    if [ "${NAVIG_VERBOSE:-0}" = "1" ] || [ "${NAV_VERBOSE:-0}" = "1" ]; then
        printf "       %b%s%b\n" "$_GRY" "$1" "$_RST"
    fi
}
log_hint() { printf "       %b%s%b\n" "$_GRY" "$1" "$_RST" ; }

# ── Done block ────────────────────────────────────────────────
print_done() {
    local version="${1:-}"
    local lw=$((_LW + 2))
    local line; line=$(_hline $lw)
    local tl; tl=$(_sym tl)
    local tr; tr=$(_sym tr)
    local bl; bl=$(_sym bl)
    local br; br=$(_sym br)
    local vt; vt=$(_sym vt)
    local label="NAVIG"
    [ -n "$version" ] && label="NAVIG $version"
    printf "\n"
    printf "  %b%s%s%s%b\n"  "$_GRN" "$tl" "$line" "$tr" "$_RST"
    printf "  %b%s%b %-$((_LW + 1))s%b%s%b\n" "$_GRN" "$vt" "$_RST" "$label" "$_GRN" "$vt" "$_RST"
    printf "  %b%s%s%s%b\n"  "$_GRN" "$bl" "$line" "$br" "$_RST"
    printf "\n"
    printf "     %bnavig --version%b   confirm install\n"  "$_YLW" "$_RST"
    printf "     %bnavig --help%b      all commands\n"     "$_YLW" "$_RST"
    printf "     %bnavig init%b        first-time setup\n" "$_YLW" "$_RST"
    printf "\n"
}

# ── Failure block ─────────────────────────────────────────────
print_failure() {
    local title="${1:-Error}" problem="${2:-}" fix="${3:-}" cmd="${4:-}"
    local lw=$((_LW + 2))
    local line; line=$(_hline $lw)
    local tl; tl=$(_sym tl)
    local tr; tr=$(_sym tr)
    local bl; bl=$(_sym bl)
    local br; br=$(_sym br)
    local vt; vt=$(_sym vt)
    local err; err=$(_sym err)
    printf "\n"
    printf "  %b%s%s%s%b\n" "$_RED" "$tl" "$line" "$tr" "$_RST"
    printf "  %b%s%b %b%s  %s%b\n" "$_RED" "$vt" "$_RST" "$_RED" "$err" "$title" "$_RST"
    printf "  %b%s%s%s%b\n" "$_RED" "$bl" "$line" "$br" "$_RST"
    printf "\n"
    [ -n "$problem" ] && printf "  %bProblem%b  %s\n" "$_GRY" "$_RST" "$problem"
    [ -n "$fix" ]     && printf "  %bFix%b      %s\n" "$_GRY" "$_RST" "$fix"
    [ -n "$cmd" ]     && printf "  %bRun%b      %b%s%b\n" "$_GRY" "$_RST" "$_YLW" "$cmd" "$_RST"
    printf "\n"
}

# ── OS detection ──────────────────────────────────────────────
detect_os() {
    unset OS_TYPE OS_VERSION OS_PKG
    if [ -f /etc/os-release ]; then
        OS_TYPE=$(. /etc/os-release && echo "${ID:-}")
        OS_VERSION=$(. /etc/os-release && echo "${VERSION_ID:-}")
    fi
    if [ "$(uname)" = "Darwin" ]; then OS_TYPE="macos"; OS_VERSION=$(sw_vers -productVersion 2>/dev/null || echo ""); fi
    OS_TYPE="${OS_TYPE:-linux}"
    case "$OS_TYPE" in
        ubuntu|debian|raspbian)    OS_PKG="apt"     ;;
        fedora|centos|rhel|rocky)  OS_PKG="dnf"     ;;
        arch|manjaro)              OS_PKG="pacman"   ;;
        alpine)                    OS_PKG="apk"      ;;
        macos)                     OS_PKG="brew"     ;;
        *)                         OS_PKG="unknown"  ;;
    esac
}

# ── Isolated runtime layout ───────────────────────────────────
# NAVIG ships its own pinned CPython + venv so the user needs NOTHING
# pre-installed. Nothing here touches the system Python.
NAVIG_HOME="$HOME/.navig"
RUNTIME_DIR="$NAVIG_HOME/runtime"            # uv + python/ + venv/
RUNTIME_VENV="$RUNTIME_DIR/venv"
RUNTIME_VENV_PY="$RUNTIME_VENV/bin/python"
RUNTIME_PY_DIR="$RUNTIME_DIR/python"         # uv-managed CPython installs
RUNTIME_CACHE="$RUNTIME_DIR/cache"           # uv cache (self-contained)
UV_EXE="$RUNTIME_DIR/uv"
SHIM_DIR="$HOME/.local/bin"
SHIM_PATH="$SHIM_DIR/navig"

# Pinned Python series for the managed runtime.
PYTHON_SERIES="3.12"

# uv release pin. Leave UV_VERSION empty to track the latest GitHub release.
UV_VERSION=""                                # e.g. "0.7.13" — empty = latest

# ── Managed runtime (uv) ──────────────────────────────────────
# Everything below installs an ISOLATED Python under ~/.navig/runtime.
# No system Python is detected, required, or modified.

_uv_asset() {
    local os arch
    os=$(uname -s); arch=$(uname -m)
    case "$os" in
        Darwin)
            case "$arch" in
                arm64|aarch64) echo "uv-aarch64-apple-darwin.tar.gz" ;;
                *)             echo "uv-x86_64-apple-darwin.tar.gz" ;;
            esac ;;
        *)
            case "$arch" in
                aarch64|arm64) echo "uv-aarch64-unknown-linux-gnu.tar.gz" ;;
                *)             echo "uv-x86_64-unknown-linux-gnu.tar.gz" ;;
            esac ;;
    esac
}

_uv_url() {
    local asset; asset=$(_uv_asset)
    if [ -z "${UV_VERSION:-}" ]; then
        echo "https://github.com/astral-sh/uv/releases/latest/download/$asset"
    else
        echo "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$asset"
    fi
}

install_navig_uv() {
    # Ensure $UV_EXE exists (self-contained under the runtime dir).
    [ -x "$UV_EXE" ] && { log_verbose "uv present: $UV_EXE"; return 0; }
    mkdir -p "$RUNTIME_DIR"
    local url tmp tmpd
    url=$(_uv_url)
    tmp=$(mktemp); tmpd=$(mktemp -d)
    log_verbose "Downloading uv: $url"
    if ! curl -fsSL "$url" -o "$tmp"; then
        log_hint "uv download failed: $url"; rm -rf "$tmp" "$tmpd"; return 1
    fi
    tar -xzf "$tmp" -C "$tmpd" 2>/dev/null || { log_hint "uv archive extract failed"; rm -rf "$tmp" "$tmpd"; return 1; }
    local found; found=$(find "$tmpd" -type f -name uv | head -1)
    if [ -z "$found" ]; then log_hint "uv binary not found in archive"; rm -rf "$tmp" "$tmpd"; return 1; fi
    cp "$found" "$UV_EXE" && chmod +x "$UV_EXE"
    rm -rf "$tmp" "$tmpd"
    [ -x "$UV_EXE" ]
}

run_uv() {
    # Run uv with a self-contained environment (managed python + cache under
    # the runtime dir).
    UV_PYTHON_INSTALL_DIR="$RUNTIME_PY_DIR" UV_CACHE_DIR="$RUNTIME_CACHE" "$UV_EXE" "$@"
}

install_navig_runtime() {
    # Build (or rebuild) the isolated runtime: uv -> pinned CPython -> venv.
    install_navig_uv || return 1
    log_verbose "uv python install $PYTHON_SERIES"
    run_uv python install "$PYTHON_SERIES" > /dev/null 2>&1 || { log_hint "uv python install failed"; return 1; }
    if [ ! -x "$RUNTIME_VENV_PY" ]; then
        log_verbose "uv venv $RUNTIME_VENV"
        run_uv venv "$RUNTIME_VENV" --python "$PYTHON_SERIES" > /dev/null 2>&1 || { log_hint "uv venv failed"; return 1; }
    fi
    return 0
}

# ── Homebrew ──────────────────────────────────────────────────
install_homebrew() {
    if command -v brew > /dev/null 2>&1; then return 0; fi
    row_step "Homebrew" "installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if command -v brew > /dev/null 2>&1; then
        eval "$(brew shellenv 2>/dev/null)" || true
        return 0
    fi
    return 1
}

# NOTE: system-Python install paths were removed — NAVIG now ships an isolated
# uv-managed CPython (see install_navig_runtime). No system Python is touched.

# ── PATH management ───────────────────────────────────────────
fix_path() {
    local bin_dir="${1:-}"
    [ -z "$bin_dir" ] && return
    # Session
    case ":$PATH:" in
        *":$bin_dir:"*) ;;
        *) export PATH="$bin_dir:$PATH" ;;
    esac
    # Persist — tag lines with '# navig' so uninstall can remove them precisely
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        [ -f "$rc" ] || continue
        if ! grep -qF "$bin_dir" "$rc" 2>/dev/null; then
            printf '\nexport PATH="%s:$PATH" # navig\n' "$bin_dir" >> "$rc"
            log_verbose "Appended to $rc"
        fi
    done
}

# ── Install navig into the managed runtime ────────────────────
install_navig_uv_pip() {
    local spec="${1:-navig}"
    local tmp; tmp=$(mktemp)
    if run_uv pip install --python "$RUNTIME_VENV_PY" --upgrade "$spec" 2>"$tmp"; then
        rm -f "$tmp"; return 0
    fi
    tail -8 "$tmp" | while IFS= read -r l; do log_hint "$l"; done
    rm -f "$tmp"; return 1
}

# ── Launcher shim ─────────────────────────────────────────────
make_shim() {
    # ~/.local/bin/navig -> the venv's navig. A single stable PATH entry that
    # survives runtime rebuilds, so updates never churn PATH.
    mkdir -p "$SHIM_DIR"
    ln -sf "$RUNTIME_VENV/bin/navig" "$SHIM_PATH"
    [ -e "$SHIM_PATH" ]
}

# ── Daemon supervision ────────────────────────────────────────
register_navig_daemon() {
    # Register the NAVIG daemon for auto-start via the runtime's own
    # `navig service install` (systemd on Linux, launchd on macOS). The service
    # manager launches the venv's own python, so it inherits the isolated
    # runtime. Best-effort; set NAVIG_NO_DAEMON to skip (e.g. CI / headless).
    [ -n "${NAVIG_NO_DAEMON:-}" ] && return 1
    [ -x "$RUNTIME_VENV/bin/navig" ] || return 1
    "$RUNTIME_VENV/bin/navig" service install > /dev/null 2>&1
}

# ── Verify ────────────────────────────────────────────────────
verify_navig() {
    # Prefer the venv exe directly (deterministic), fall back to PATH.
    local venv_navig="$RUNTIME_VENV/bin/navig"
    case ":$PATH:" in
        *":$SHIM_DIR:"*) ;;
        *) export PATH="$SHIM_DIR:$PATH" ;;
    esac
    if [ -x "$venv_navig" ]; then
        local v; v=$("$venv_navig" --version 2>&1 | head -1) || true
        printf "%s" "$v"; return 0
    fi
    if command -v navig > /dev/null 2>&1; then
        local v; v=$(navig --version 2>&1 | head -1) || true
        printf "%s" "$v"; return 0
    fi
    return 1
}

# ── Setup config dirs ─────────────────────────────────────────
setup_config() {
    local base="$HOME/.navig"
    for sub in "" workspace logs cache; do
        local d
        if [ -z "$sub" ]; then d="$base"; else d="$base/$sub"; fi
        [ -d "$d" ] || mkdir -p "$d"
    done
    log_verbose "Config: $base/"
}

# ── Stop background processes / services ─────────────────────
_stop_navig_background() {
    pkill -f 'navig' 2>/dev/null || true
    if command -v systemctl > /dev/null 2>&1; then
        for unit in navig-daemon navig-tunnel navig; do
            systemctl stop    "${unit}.service" 2>/dev/null || true
            systemctl disable "${unit}.service" 2>/dev/null || true
        done
    fi
}

# ── Uninstall ─────────────────────────────────────────────────
uninstall_navig() {
    local preserve_data="${1:-0}"
    local version="${2:-}"
    local _nav_str="NAVIG"
    [ -n "$version" ] && _nav_str="NAVIG $version"

    # Confirmation prompt (skip when -y/--yes or called for reinstall)
    if [ "${_YES:-0}" != "1" ] && [ "${preserve_data}" != "1" ]; then
        printf "\n  %b%s%b  Really uninstall %s? [y/N] " "$_YLW" "$(_sym warn)" "$_RST" "$_nav_str"
        read -r _uninst_reply < /dev/tty || _uninst_reply="n"
        case "$_uninst_reply" in
            [Yy]*) ;;
            *) printf "  Cancelled.\n"; return 0 ;;
        esac
    fi

    row_step "Removing" "$_nav_str"
    _uninstall_ok=1
    _removed_items=""

    # 1. Stop background processes and services
    _stop_navig_background
    log_verbose "Stopped background processes / services"

    # 2. Remove managed runtime + launcher shim (always, so reinstall is clean)
    rm -f "$SHIM_PATH" 2>/dev/null || true
    if [ -d "$RUNTIME_DIR" ]; then
        if rm -rf "$RUNTIME_DIR" 2>/dev/null; then
            row_ok  "runtime" "removed: $RUNTIME_DIR"
        else
            row_warn "runtime" "could not remove $RUNTIME_DIR"
            _uninstall_ok=0
        fi
    else
        row_warn "runtime" "not found — skipping"
    fi

    # 3. Remove config / data directory
    if [ "$preserve_data" != "1" ]; then
        if [ -d "$HOME/.navig" ]; then
            if rm -rf "$HOME/.navig" 2>/dev/null; then
                row_ok "Config dir" "removed: $HOME/.navig"
            else
                row_warn "Config dir" "could not remove $HOME/.navig"
                _uninstall_ok=0
            fi
        else
            row_warn "Config dir" "not found — skipping"
        fi
    else
        row_warn "Config dir" "preserved (reinstall mode)"
    fi

    # 4. Remove install marker
    rm -f "$HOME/.navig/install.marker" 2>/dev/null || true

    # 5. Clean PATH entries from shell profiles (only lines tagged '# navig')
    local _rc_cleaned=0
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        [ -f "$rc" ] || continue
        if grep -q '# navig' "$rc" 2>/dev/null; then
            sed -i.bak '/# navig$/d' "$rc" 2>/dev/null || true
            _rc_cleaned=1
            log_verbose "Cleaned PATH entries from $rc"
        fi
    done
    if [ "$_rc_cleaned" = "1" ]; then
        row_ok  "Shell profiles" "PATH entries removed"
        row_warn "Note"          "open a new terminal to apply PATH changes"
    else
        row_warn "Shell profiles" "no tagged entries found — skipping"
    fi

    # 6. Done
    if [ "${_uninstall_ok}" = "1" ]; then
        row_ok "Done" "$_nav_str fully uninstalled."
    else
        row_warn "Done" "$_nav_str removed with warnings — check output above."
    fi
    return 0
}

# ── Argument defaults ─────────────────────────────────────────
_VERSION="${NAVIG_VERSION:-}"
_ACTION="${NAVIG_ACTION:-install}"
_YES=0
_DRY_RUN=0
_HELP=0
[ "${NAVIG_VERBOSE:-0}" = "1" ] && NAV_VERBOSE=1 || NAV_VERBOSE=0

# ── Argument parsing ──────────────────────────────────────────
_parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            # Bare positional subcommand: install | uninstall | reinstall | repair
            install|uninstall|reinstall|repair) _ACTION="$1" ;;
            -v|--version)    shift; _VERSION="${1:-}"  ;;
            -a|--action)     shift; _ACTION="${1:-}"   ;;
            -y|--yes)        _YES=1                     ;;
            --dry-run)       _DRY_RUN=1                 ;;
            --verbose|-V)    NAV_VERBOSE=1              ;;
            -h|--help)       _HELP=1                    ;;
        esac
        shift
    done
}

show_usage() {
    printf "NAVIG Installer\n\n"
    printf "Usage:\n"
    printf "  curl -fsSL https://navig.run/install.sh | bash\n"
    printf "  bash install.sh [OPTIONS]\n\n"
    printf "Options:\n"
    printf "  -v <ver>    Pin version\n"
    printf "  -a <mode>   install | uninstall | reinstall\n"
    printf "  -y          Skip prompts\n"
    printf "  --verbose   Verbose output\n"
    printf "  --dry-run   Preview only\n"
    printf "  -h          Show this help\n"
}

# ── Main ──────────────────────────────────────────────────────
main() {
    [ "${NAVIG_INSTALL_SH_NO_RUN:-0}" = "1" ] && return

    # Always parse args — works both as a script and when piped via curl | bash -s -- uninstall
    _parse_args "$@"

    if [ "$_HELP" = "1" ]; then show_usage; return; fi
    case "$_ACTION" in
        install|uninstall|reinstall|repair) ;;
        *)
            print_failure \
                "invalid action" \
                "Unsupported action: $_ACTION" \
                "Use one of: install, uninstall, reinstall, repair" \
                "bash install.sh --action install"
            return 1
            ;;
    esac

    if [ "$_DRY_RUN" = "1" ]; then
        printf "Dry run mode - no changes will be made\n"
    fi
    export NAVIG_VERBOSE="$NAV_VERBOSE"

    _init_term
    _init_colors
    print_header

    # ── Dry-run preview path (never mutates host) ────────────
    if [ "$_DRY_RUN" = "1" ]; then
        print_section "Dry run"
        row_ok "Action" "$_ACTION"
        row_ok "Status" "Dry run complete"
        return
    fi

    # ── Uninstall path ────────────────────────────────────────
    if [ "$_ACTION" = "uninstall" ]; then
        _inst_ver=$(navig --version 2>&1 | grep -oE '[0-9]+[.][0-9.]+' | head -1 || true)
        uninstall_navig 0 "${_inst_ver:-}"
        return
    fi

    # ── Environment ───────────────────────────────────────────
    print_section "Environment"
    detect_os
    local arch; arch=$(uname -m)
    row_ok "OS"    "${OS_TYPE:-$(uname)} $(_sym bullet) $arch"
    row_ok "Shell" "${SHELL:-sh}"

    # ── Runtime ───────────────────────────────────────────────
    # NAVIG bundles its own pinned Python. Nothing is required up front and
    # the system Python is never detected, used, or modified.
    print_section "Runtime"
    row_step "Python" "preparing isolated runtime..."
    if [ "$_DRY_RUN" != "1" ]; then
        install_navig_runtime || {
            print_failure \
                "Could not prepare the NAVIG runtime" \
                "Failed to fetch uv or build the isolated Python $PYTHON_SERIES environment." \
                "Check your network / proxy and re-run. Your system Python is never touched." \
                "curl -fsSL https://navig.run/install.sh | sh"
            exit 1
        }
    fi
    row_ok "Python" "$PYTHON_SERIES $(_sym bullet) isolated (~/.navig/runtime)"

    # ── Install ───────────────────────────────────────────────
    print_section "Install"
    local spec="navig[interactive]"
    [ -n "$_VERSION" ] && spec="navig[interactive]==$_VERSION"
    row_step "navig" "installing $spec ..."
    if [ "$_DRY_RUN" != "1" ]; then
        install_navig_uv_pip "$spec" || {
            print_failure \
                "navig install failed" \
                "uv exited with a non-zero code while installing navig into the runtime." \
                "Run manually to see the full output." \
                "$UV_EXE pip install --python \"$RUNTIME_VENV_PY\" --upgrade navig[interactive]"
            exit 1
        }
    fi
    row_ok "navig" "installed"

    # ── PATH ──────────────────────────────────────────────────
    if [ "$_DRY_RUN" != "1" ]; then make_shim || row_warn "shim" "could not create $SHIM_PATH"; fi
    fix_path "$SHIM_DIR"
    row_ok "PATH" "$(_sym bullet) $SHIM_DIR"
    setup_config

    # ── Verify ────────────────────────────────────────────────
    print_section "Verify"
    if [ "$_DRY_RUN" = "1" ]; then
        row_ok "navig" "DRY RUN — skipped"
    else
        local nav_ver
        if ! nav_ver=$(verify_navig); then
            print_failure \
                "navig not callable" \
                "navig was installed but is not found on PATH in this session." \
                "Source your shell profile and retry." \
                "source ~/.bashrc && navig --version"
            exit 1
        fi
        local clean_ver; clean_ver=$(echo "$nav_ver" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "$nav_ver")
        row_ok "navig" "$clean_ver"

        # Write install marker so reinstall detection works
        mkdir -p "$HOME/.navig"
        printf "%s\n" "${clean_ver}" > "$HOME/.navig/install.marker"
        log_verbose "Wrote install marker: $HOME/.navig/install.marker"
        _write_terminal_json

        # ── Daemon auto-start ────────────────────────────────────
        if register_navig_daemon; then
            row_ok   "service" "auto-start enabled"
        else
            row_warn "service" "skipped — run 'navig service install' later"
        fi
        # ── Optional: fzf (best picker UI) ───────────────────────
        if ! command -v fzf > /dev/null 2>&1; then
            _fzf_installed=0
            case "${OS_TYPE:-}" in
                macos)
                    if command -v brew > /dev/null 2>&1; then
                        row_step "fzf" "installing via brew..."
                        brew install fzf > /dev/null 2>&1 && _fzf_installed=1 || true
                    fi ;;
                linux)
                    if command -v apt-get > /dev/null 2>&1; then
                        row_step "fzf" "installing via apt..."
                        sudo apt-get install -y -qq fzf > /dev/null 2>&1 && _fzf_installed=1 || true
                    elif command -v pacman > /dev/null 2>&1; then
                        row_step "fzf" "installing via pacman..."
                        sudo pacman -S --noconfirm --quiet fzf > /dev/null 2>&1 && _fzf_installed=1 || true
                    elif command -v dnf > /dev/null 2>&1; then
                        row_step "fzf" "installing via dnf..."
                        sudo dnf install -y -q fzf > /dev/null 2>&1 && _fzf_installed=1 || true
                    fi ;;
            esac
            if [ "$_fzf_installed" = "1" ]; then
                row_ok "fzf" "installed (best picker UI)"
            else
                log_hint "fzf optional — install for the best picker UI:"
                case "${OS_TYPE:-}" in
                    macos)  log_hint "  brew install fzf" ;;
                    linux)  log_hint "  sudo apt install fzf  (or pacman -S fzf / dnf install fzf)" ;;
                    *)      log_hint "  https://github.com/junegunn/fzf#installation" ;;
                esac
            fi
        else
            row_ok "fzf" "already installed"
        fi
        # ── Done ──────────────────────────────────────────────
        print_done "$clean_ver"
    fi
}

main "$@"
