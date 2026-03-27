#!/usr/bin/env bash
# NAVIG Installer - Linux / macOS / WSL
#
# Usage:
#   curl -fsSL https://navig.run/install.sh | bash
#   bash install.sh [OPTIONS]
#
# Options:
#   -v | --version <ver>   Pin version (e.g. 2.4.14)
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
    printf "  %b%s%b   %bquiet operator tooling for real systems%b\n" "$_GRY" "$vt" "$_RST" "$_GRY" "$_RST"
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
    printf "  %b%s%b  %-${_LW}s  %b%s%b\n" "$_GRN" "$vt" "$_RST" "$label" "$_GRN" "$vt" "$_RST"
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

# ── Python detection ──────────────────────────────────────────
MIN_PY_MAJOR=3
MIN_PY_MINOR=10

_py_meets_min() {
    local exe="$1"
    local out; out=$("$exe" --version 2>&1) || return 1
    local maj min
    maj=$(echo "$out" | grep -oE '[0-9]+\.[0-9]+' | head -1 | cut -d. -f1)
    min=$(echo "$out" | grep -oE '[0-9]+\.[0-9]+' | head -1 | cut -d. -f2)
    [ -z "$maj" ] && return 1
    { [ "$maj" -gt "$MIN_PY_MAJOR" ] || { [ "$maj" -eq "$MIN_PY_MAJOR" ] && [ "$min" -ge "$MIN_PY_MINOR" ]; }; }
}

detect_python() {
    unset PYTHON_EXE PYTHON_VERSION
    for cand in python3 python python3.14 python3.13 python3.12 python3.11 python3.10; do
        if command -v "$cand" > /dev/null 2>&1 && _py_meets_min "$cand"; then
            PYTHON_EXE=$(command -v "$cand")
            PYTHON_VERSION=$("$PYTHON_EXE" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
            return 0
        fi
    done
    return 1
}

# ── pip detection ─────────────────────────────────────────────
detect_pip() {
    unset PIP_EXE
    if [ -n "${PYTHON_EXE:-}" ] && "$PYTHON_EXE" -m pip --version > /dev/null 2>&1; then
        PIP_EXE="$PYTHON_EXE -m pip"
        return 0
    fi
    for cand in pip3 pip; do
        if command -v "$cand" > /dev/null 2>&1; then PIP_EXE="$cand"; return 0; fi
    done
    return 1
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

# ── Python install ────────────────────────────────────────────
install_python() {
    row_step "Python" "not found — attempting system install..."
    case "${OS_PKG:-}" in
        apt)
            if command -v sudo > /dev/null 2>&1; then
                sudo apt-get update -qq && sudo apt-get install -y python3 python3-pip python3-venv
            else
                apt-get update -qq && apt-get install -y python3 python3-pip python3-venv
            fi
            ;;
        dnf)
            if command -v sudo > /dev/null 2>&1; then sudo dnf install -y python3 python3-pip
            else dnf install -y python3 python3-pip; fi
            ;;
        pacman)
            if command -v sudo > /dev/null 2>&1; then sudo pacman -Sy --noconfirm python python-pip
            else pacman -Sy --noconfirm python python-pip; fi
            ;;
        apk)
            if command -v sudo > /dev/null 2>&1; then sudo apk add --no-cache python3 py3-pip
            else apk add --no-cache python3 py3-pip; fi
            ;;
        brew)
            if ! command -v brew > /dev/null 2>&1; then install_homebrew || return 1; fi
            brew install python
            ;;
        *)
            print_failure \
                "Cannot install Python automatically" \
                "Unsupported package manager on ${OS_TYPE:-unknown}." \
                "Install Python $MIN_PY_MAJOR.$MIN_PY_MINOR+ manually." \
                "https://www.python.org/downloads"
            return 1
            ;;
    esac
    detect_python && return 0 || return 1
}

# ── PATH management ───────────────────────────────────────────
fix_path() {
    local bin_dir="${1:-}"
    [ -z "$bin_dir" ] && return
    # Session
    case ":$PATH:" in
        *":$bin_dir:"*) ;;
        *) export PATH="$bin_dir:$PATH" ;;
    esac
    # Persist
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        [ -f "$rc" ] || continue
        if ! grep -qF "$bin_dir" "$rc" 2>/dev/null; then
            printf '\nexport PATH="%s:$PATH"\n' "$bin_dir" >> "$rc"
            log_verbose "Appended to $rc"
        fi
    done
}

# ── pip install ───────────────────────────────────────────────
install_navig_pip() {
    local spec="${1:-navig}"
    local tmp; tmp=$(mktemp)
    # shellcheck disable=SC2086
    if $PIP_EXE install --quiet --upgrade --disable-pip-version-check "$spec" 2>"$tmp"; then
        rm -f "$tmp"; return 0
    fi
    tail -8 "$tmp" | while IFS= read -r l; do log_hint "$l"; done
    rm -f "$tmp"; return 1
}

# ── Verify ────────────────────────────────────────────────────
verify_navig() {
    # Reload PATH with common user-bin dirs
    local user_bin="$HOME/.local/bin"
    case ":$PATH:" in
        *":$user_bin:"*) ;;
        *) export PATH="$user_bin:$PATH" ;;
    esac
    if command -v navig > /dev/null 2>&1; then
        local v; v=$(navig --version 2>&1 | head -1) || true
        printf "%s" "$v"
        return 0
    fi
    # Last-ditch: check user bin directly
    if [ -x "$user_bin/navig" ]; then
        local v; v=$("$user_bin/navig" --version 2>&1 | head -1) || true
        printf "%s" "$v"
        return 0
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

# ── Uninstall ─────────────────────────────────────────────────
uninstall_navig() {
    local preserve_data="${1:-0}"
    row_step "Uninstalling" "NAVIG..."
    for pip_cmd in pip3 pip; do
        if command -v "$pip_cmd" > /dev/null 2>&1; then
            $pip_cmd uninstall navig -y > /dev/null 2>&1 || true
            break
        fi
    done
    if [ "$preserve_data" != "1" ] && [ -d "$HOME/.navig" ]; then
        rm -rf "$HOME/.navig" && log_verbose "Removed $HOME/.navig"
    fi
    # Remove PATH lines from rc files
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        [ -f "$rc" ] || continue
        if grep -q "navig\|\.local/bin" "$rc" 2>/dev/null; then
            sed -i.bak '/navig\|\.local\/bin/d' "$rc" 2>/dev/null || true
            log_verbose "Cleaned $rc"
        fi
    done
    row_ok "Done" "NAVIG removed."
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

    # Parse args if called as script (not piped)
    if [ -n "${BASH_SOURCE[0]:-}" ]; then
        _parse_args "$@"
    fi

    if [ "$_HELP" = "1" ]; then show_usage; return; fi
    if [ "$_DRY_RUN" = "1" ]; then
        printf "DRY RUN - no changes will be made\n"
    fi
    export NAVIG_VERBOSE="$NAV_VERBOSE"

    _init_term
    _init_colors
    print_header

    # ── Uninstall path ────────────────────────────────────────
    if [ "$_ACTION" = "uninstall" ]; then
        uninstall_navig
        return
    fi

    # ── Environment ───────────────────────────────────────────
    print_section "Environment"
    detect_os
    local arch; arch=$(uname -m)
    row_ok "OS"    "${OS_TYPE:-$(uname)} $(_sym bullet) $arch"
    row_ok "Shell" "${SHELL:-sh}"

    # ── Requirements ──────────────────────────────────────────
    print_section "Requirements"
    row_step "Python" "detecting..."
    if ! detect_python; then
        install_python || {
            print_failure \
                "Python $MIN_PY_MAJOR.$MIN_PY_MINOR+ required" \
                "No compatible Python found and automatic install failed." \
                "Install Python $MIN_PY_MAJOR.$MIN_PY_MINOR+ for your platform." \
                "https://www.python.org/downloads"
            exit 1
        }
    fi
    row_ok "Python" "${PYTHON_VERSION:-detected}"
    log_verbose "$PYTHON_EXE"

    if ! detect_pip; then
        print_failure \
            "pip not available" \
            "Python found but pip is not available." \
            "Ensure pip is installed: python3 -m ensurepip" \
            "$PYTHON_EXE -m ensurepip --upgrade"
        exit 1
    fi

    # ── Install ───────────────────────────────────────────────
    print_section "Install"
    if [ "$_ACTION" = "reinstall" ]; then
        row_step "navig" "removing old version..."
        $PIP_EXE uninstall navig -y > /dev/null 2>&1 || true
    fi
    local spec="navig"
    [ -n "$_VERSION" ] && spec="navig==$_VERSION"
    row_step "navig" "pip install $spec ..."
    if [ "$_DRY_RUN" != "1" ]; then
        install_navig_pip "$spec" || {
            print_failure \
                "pip install failed" \
                "pip exited with a non-zero code while installing navig." \
                "Run manually to see the full output." \
                "$PYTHON_EXE -m pip install --upgrade navig"
            exit 1
        }
    fi
    row_ok "navig" "installed"

    # ── PATH ──────────────────────────────────────────────────
    local user_bin="$HOME/.local/bin"
    fix_path "$user_bin"
    row_ok "PATH" "$(_sym bullet) $user_bin"
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

        # ── Done ──────────────────────────────────────────────
        print_done "$clean_ver"
    fi
}

main "$@"
