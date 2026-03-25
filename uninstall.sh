#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# NAVIG Uninstaller — Linux / WSL2 / Git Bash
# Reverses every operation performed by install.sh, in reverse order.
#
# Usage:
#   bash uninstall.sh [OPTIONS]
#   curl -fsSL https://navig.run/uninstall.sh | bash
#
# Options:
#   --dry-run       Print all actions without executing any removal
#   --silent        Skip all confirmation prompts; assume yes (CI use)
#   --self-destruct Delete uninstall.sh itself as the final step
#   --help          Print usage and exit 0
# ─────────────────────────────────────────────────────────────
set -euo pipefail

# ── Counters ──────────────────────────────────────────────────
REMOVED=0
SKIPPED=0
FAILED=0

# ── Flags ─────────────────────────────────────────────────────
DRY_RUN=false
SILENT=false
SELF_DESTRUCT=false
_ABORTED=false
_SKIP_SUMMARY=false   # set true for --help / early clean exits

# ── Color support ─────────────────────────────────────────────
_supports_color() {
    [[ -t 1 ]] && [[ "${TERM:-}" != "dumb" ]] && command -v tput &>/dev/null && tput colors &>/dev/null && [[ "$(tput colors)" -ge 8 ]]
}

if _supports_color; then
    C_GREEN='\033[1;32m'
    C_YELLOW='\033[1;33m'
    C_RED='\033[1;31m'
    C_CYAN='\033[1;36m'
    C_DIM='\033[2m'
    C_BOLD='\033[1m'
    C_NC='\033[0m'
    CHK="${C_GREEN}✔${C_NC}"
    WRN="${C_YELLOW}⚠${C_NC}"
    CRS="${C_RED}✖${C_NC}"
    SKP="${C_DIM}–${C_NC}"
else
    C_GREEN='' C_YELLOW='' C_RED='' C_CYAN='' C_DIM='' C_BOLD='' C_NC=''
    CHK='[OK]'
    WRN='[WARN]'
    CRS='[FAIL]'
    SKP='[SKIP]'
fi

# ── Traps ─────────────────────────────────────────────────────
_on_err() {
    _ABORTED=true
}

_on_exit() {
    if "$_SKIP_SUMMARY"; then return 0; fi
    if "$_ABORTED"; then
        echo ""
        echo -e "${C_RED}${C_BOLD}Uninstall aborted unexpectedly.${C_NC}"
        echo -e "  Run with ${C_CYAN}--dry-run${C_NC} to preview without changes."
        echo -e "  Run with ${C_CYAN}--silent${C_NC} to suppress all prompts (CI)."
        echo ""
    fi
    print_summary
    if "$SELF_DESTRUCT" && [[ -f "$0" ]] && ! "$DRY_RUN"; then
        echo -e "${SKP} Removing uninstall.sh..."
        rm -f "$0"
    fi
}

trap '_on_err' ERR
trap '_on_exit' EXIT

# ── Usage ─────────────────────────────────────────────────────
print_usage() {
    cat <<'USAGE'
NAVIG Uninstaller

Usage:
  bash uninstall.sh [OPTIONS]

Options:
  --dry-run       Print all actions without executing any removal
  --silent        Skip all confirmation prompts; assume yes (CI use)
  --self-destruct Delete uninstall.sh itself as the final step
  --help          Print usage and exit 0

Environment:
  NAVIG_GIT_DIR   Path to git-mode checkout (default: ~/navig-core)
USAGE
}

# ── Argument parsing ──────────────────────────────────────────
parse_args() {
    for arg in "$@"; do
        case "$arg" in
            --dry-run)       DRY_RUN=true ;;
            --silent)        SILENT=true ;;
            --self-destruct) SELF_DESTRUCT=true ;;
            --help|-h)       _SKIP_SUMMARY=true; print_usage; exit 0 ;;
            *)
                echo -e "${C_RED}Unknown flag: ${arg}${C_NC}" >&2
                echo "Run 'bash uninstall.sh --help' for usage." >&2
                exit 1
                ;;
        esac
    done
}

# ── Helpers ───────────────────────────────────────────────────
log_ok()   { echo -e "  ${CHK} ${1}"; (( REMOVED++ )) || true; }
log_skip() { echo -e "  ${SKP} ${C_DIM}[SKIP]${C_NC} Already absent: ${1}"; (( SKIPPED++ )) || true; }
log_warn() { echo -e "  ${WRN} ${C_YELLOW}[WARN]${C_NC} ${1}"; (( FAILED++ )) || true; }
log_dry()  { echo -e "  ${C_CYAN}[DRY-RUN]${C_NC} Would remove: ${1}"; }
log_info() { echo -e "  ${SKP} ${1}"; }

confirm() {
    # $1 = prompt text, $2 = default label shown (e.g. "y/N")
    local prompt="${1}" default="${2:-N}"
    if "$SILENT"; then
        return 0
    fi
    local reply
    read -r -p "$(echo -e "  ${C_YELLOW}?${C_NC} ${prompt} [${default}] ")" reply
    reply="${reply:-N}"
    case "$reply" in
        y|Y) return 0 ;;
        *)   return 1 ;;
    esac
}

safe_remove_file() {
    local path="$1" label="${2:-$1}"
    if [[ ! -e "$path" && ! -L "$path" ]]; then
        log_skip "$label"
        return 0
    fi
    if "$DRY_RUN"; then log_dry "$label"; return 0; fi
    if rm -f "$path" 2>/dev/null; then
        if [[ ! -e "$path" ]]; then
            log_ok "Removed file: $label"
        else
            log_warn "Removal reported success but '$path' still exists"
        fi
    else
        log_warn "Failed to remove file: $label"
    fi
}

safe_remove_dir() {
    local path="$1" label="${2:-$1}"
    if [[ ! -d "$path" ]]; then
        log_skip "$label"
        return 0
    fi
    if "$DRY_RUN"; then log_dry "$label (directory)"; return 0; fi
    if rm -rf "$path" 2>/dev/null; then
        if [[ ! -d "$path" ]]; then
            log_ok "Removed directory: $label"
        else
            log_warn "Removal reported success but '$path' still exists"
        fi
    else
        log_warn "Failed to remove directory: $label"
    fi
}

# ── OS Detection ──────────────────────────────────────────────
detect_os() {
    local os_type="${OSTYPE:-}"
    case "$os_type" in
        linux-gnu*|linux-musl*)
            # Standard Linux or WSL
            if grep -qi microsoft /proc/version 2>/dev/null; then
                log_info "Detected: WSL2 (Windows Subsystem for Linux)"
            else
                log_info "Detected: Linux"
            fi
            ;;
        msys*|cygwin*|mingw*)
            echo -e "${C_YELLOW}${C_BOLD}Warning:${C_NC} install.sh does not run on native Windows (Git Bash / MSYS)."
            echo -e "  This uninstaller targets ${C_CYAN}WSL2${C_NC} or a ${C_CYAN}Linux${C_NC} environment."
            echo -e "  For native Windows use ${C_CYAN}uninstall.ps1${C_NC} instead."
            echo ""
            if ! "$SILENT"; then
                if ! confirm "Continue anyway in this shell environment? (not recommended)" "y/N"; then
                    echo -e "  Aborted. Run ${C_CYAN}uninstall.ps1${C_NC} on Windows."
                    _ABORTED=false   # clean exit, not error
                    exit 0
                fi
            fi
            ;;
        darwin*)
            log_info "Detected: macOS (install.sh supports macOS; proceeding)"
            ;;
        *)
            echo -e "${C_YELLOW}Warning:${C_NC} Unrecognised OS type '${os_type}'. Proceeding cautiously."
            ;;
    esac
}

# ── Detect install prefix ─────────────────────────────────────
PYTHON_CMD=""
USER_BASE=""
BIN_DIR=""
GIT_DIR="${NAVIG_GIT_DIR:-${HOME}/navig-core}"
GIT_MODE=false

detect_python_and_prefix() {
    for cmd in python3 python python3.12 python3.11 python3.10; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver="$("$cmd" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || echo "(0, 0)")"
            if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
                PYTHON_CMD="$cmd"
                break
            fi
        fi
    done

    if [[ -n "$PYTHON_CMD" ]]; then
        USER_BASE="$("$PYTHON_CMD" -m site --user-base 2>/dev/null || echo "${HOME}/.local")"
    else
        USER_BASE="${HOME}/.local"
    fi
    BIN_DIR="${USER_BASE}/bin"
}

detect_install_mode() {
    if [[ -d "$GIT_DIR" ]]; then
        # Heuristic: if pip reports navig as editable from git dir it is dev mode
        if command -v pip3 &>/dev/null && pip3 show navig 2>/dev/null | grep -qiE "Location.*navig.core|Editable.*True"; then
            GIT_MODE=true
        elif command -v pip &>/dev/null && pip show navig 2>/dev/null | grep -qiE "Location.*navig.core|Editable.*True"; then
            GIT_MODE=true
        elif [[ -f "${GIT_DIR}/pyproject.toml" ]] && grep -q 'name = "navig"' "${GIT_DIR}/pyproject.toml" 2>/dev/null; then
            GIT_MODE=true
        fi
    fi
}

# ── Preflight: is NAVIG installed? ────────────────────────────
check_installed() {
    local found=false
    [[ -d "${HOME}/.navig" ]]             && found=true
    [[ -f "${BIN_DIR}/navig" ]]           && found=true
    command -v navig &>/dev/null          && found=true
    [[ -d "${GIT_DIR}" ]] && "$GIT_MODE"  && found=true

    if ! "$found"; then
        echo ""
        echo -e "${C_DIM}NAVIG is not installed. Nothing to do.${C_NC}"
        echo ""
        _ABORTED=false
        _SKIP_SUMMARY=true
        exit 0
    fi
}

# ── Display uninstall plan ────────────────────────────────────
print_plan() {
    echo ""
    echo -e "${C_BOLD}${C_CYAN}Uninstall plan:${C_NC}"
    echo ""

    local n=0

    (( ++n ))
    if command -v navig &>/dev/null; then
        echo -e "  [${n}] navig daemon service           →  ${C_YELLOW}will be stopped and uninstalled${C_NC}"
    else
        echo -e "  [${n}] navig daemon service           →  ${C_DIM}navig binary not found — will skip${C_NC}"
    fi

    (( ++n ))
    local pkg_found=false
    { command -v pip3 &>/dev/null && pip3 show navig &>/dev/null && pkg_found=true; } || \
    { command -v pip  &>/dev/null && pip  show navig &>/dev/null && pkg_found=true; } || true
    if "$pkg_found"; then
        echo -e "  [${n}] pip package 'navig'             →  ${C_YELLOW}will be uninstalled${C_NC}"
    else
        echo -e "  [${n}] pip package 'navig'             →  ${C_DIM}not found in pip — will skip${C_NC}"
    fi

    (( ++n ))
    if [[ -f "${BIN_DIR}/navig" || -L "${BIN_DIR}/navig" ]]; then
        echo -e "  [${n}] ${BIN_DIR}/navig               →  ${C_YELLOW}will be deleted${C_NC}"
    else
        echo -e "  [${n}] ${BIN_DIR}/navig               →  ${C_DIM}already absent${C_NC}"
    fi

    (( ++n ))
    if "$GIT_MODE" && [[ -d "$GIT_DIR" ]]; then
        echo -e "  [${n}] ${GIT_DIR}/                    →  ${C_YELLOW}will be deleted${C_NC} ${C_DIM}(git/dev mode)${C_NC}"
    else
        echo -e "  [${n}] ${GIT_DIR}/                    →  ${C_DIM}not present / not dev mode — will skip${C_NC}"
    fi

    (( ++n ))
    local profiles_with_injection=()
    for rc in "${HOME}/.bashrc" "${HOME}/.zshrc" "${HOME}/.profile"; do
        [[ -f "$rc" ]] && grep -q "# NAVIG CLI" "$rc" 2>/dev/null && profiles_with_injection+=("$rc")
    done
    if [[ ${#profiles_with_injection[@]} -gt 0 ]]; then
        echo -e "  [${n}] Shell profile injections        →  ${C_YELLOW}will remove NAVIG lines from:${C_NC}"
        for p in "${profiles_with_injection[@]}"; do
            echo -e "       ${C_DIM}${p}${C_NC}"
        done
    else
        echo -e "  [${n}] Shell profile injections        →  ${C_DIM}no NAVIG lines found${C_NC}"
    fi

    (( ++n ))
    if [[ -d "${HOME}/.navig" ]]; then
        local has_vault=false
        [[ -d "${HOME}/.navig/vault" ]] && has_vault=true
        if "$has_vault"; then
            echo -e "  [${n}] ~/.navig/                      →  ${C_RED}will be deleted${C_NC} ${C_YELLOW}(contains vault/ — keys/tokens will be lost)${C_NC}"
        else
            echo -e "  [${n}] ~/.navig/                      →  ${C_YELLOW}will be deleted${C_NC}"
        fi
    else
        echo -e "  [${n}] ~/.navig/                      →  ${C_DIM}already absent${C_NC}"
    fi

    (( ++n ))
    echo -e "  [${n}] Windows Registry entries        →  ${C_DIM}N/A — install.sh does not write registry entries${C_NC}"

    (( ++n ))
    local cron_lines=""
    cron_lines="$(crontab -l 2>/dev/null | grep -i navig || true)"
    if [[ -n "$cron_lines" ]]; then
        echo -e "  [${n}] cron jobs                      →  ${C_YELLOW}will remove NAVIG cron entries${C_NC}"
    else
        echo -e "  [${n}] cron jobs                      →  ${C_DIM}no NAVIG cron entries found${C_NC}"
    fi

    if "$SELF_DESTRUCT"; then
        (( ++n ))
        echo -e "  [${n}] uninstall.sh                   →  ${C_YELLOW}will be deleted${C_NC} ${C_DIM}(--self-destruct)${C_NC}"
    fi

    if "$DRY_RUN"; then
        echo ""
        echo -e "  ${C_CYAN}--dry-run active: no changes will be made.${C_NC}"
    fi
    echo ""
}

# ── Step A: Stop and uninstall the NAVIG daemon ───────────────
stop_daemon() {
    echo -e "${C_BOLD}Step 1/8 — Stop NAVIG daemon service${C_NC}"

    if ! command -v navig &>/dev/null; then
        log_skip "navig binary (service cannot be stopped without it)"
        return 0
    fi

    if "$DRY_RUN"; then
        log_dry "navig service stop"
        log_dry "navig service uninstall"
        return 0
    fi

    # Detect whether a service is actually registered
    local svc_active=false
    if navig service status &>/dev/null 2>&1; then
        svc_active=true
    fi

    if "$svc_active"; then
        if navig service stop 2>/dev/null; then
            log_ok "navig service stopped"
        else
            log_warn "navig service stop failed (may already be stopped)"
        fi
        if navig service uninstall 2>/dev/null; then
            log_ok "navig service uninstalled"
        else
            log_warn "navig service uninstall failed (manual cleanup may be needed)"
        fi
    else
        log_skip "navig daemon service (not registered or already stopped)"
    fi
}

# ── Step B: pip uninstall ─────────────────────────────────────
uninstall_pip_package() {
    echo -e "${C_BOLD}Step 2/8 — Remove pip package${C_NC}"

    local pip_cmd=""
    command -v pip3 &>/dev/null && pip3 show navig &>/dev/null 2>&1 && pip_cmd="pip3"
    command -v pip  &>/dev/null && pip  show navig &>/dev/null 2>&1 && pip_cmd="${pip_cmd:-pip}"

    if [[ -z "$pip_cmd" ]]; then
        log_skip "pip package 'navig' (not installed in any pip)"
        return 0
    fi

    if "$DRY_RUN"; then
        log_dry "$pip_cmd uninstall navig -y"
        return 0
    fi

    if "$pip_cmd" uninstall navig -y 2>/dev/null; then
        # Verify removal
        if ! "$pip_cmd" show navig &>/dev/null 2>&1; then
            log_ok "pip package 'navig' uninstalled"
        else
            log_warn "pip uninstall ran but 'navig' still shown by pip (may need manual cleanup)"
        fi
    else
        log_warn "pip uninstall failed — run '$pip_cmd uninstall navig -y' manually"
    fi
}

# ── Step C: Remove binary / symlink from BIN_DIR ─────────────
remove_symlinks() {
    echo -e "${C_BOLD}Step 3/8 — Remove binary from PATH directory${C_NC}"

    local navig_bin="${BIN_DIR}/navig"

    if [[ ! -f "$navig_bin" && ! -L "$navig_bin" ]]; then
        # Also check system PATH in case pip installed elsewhere
        local other
        other="$(command -v navig 2>/dev/null || true)"
        if [[ -n "$other" && "$other" != "$navig_bin" ]]; then
            echo -e "  ${C_DIM}Note: 'navig' also found at ${other} — not removed (may be system/brew install)${C_NC}"
        fi
        log_skip "${navig_bin}"
        return 0
    fi

    safe_remove_file "$navig_bin" "${navig_bin}"
}

# ── Step D: Remove git clone (dev mode only) ──────────────────
remove_git_clone() {
    echo -e "${C_BOLD}Step 4/8 — Remove git checkout${C_NC}"

    if ! "$GIT_MODE" || [[ ! -d "$GIT_DIR" ]]; then
        log_skip "${GIT_DIR} (not present or not dev mode)"
        return 0
    fi

    if "$DRY_RUN"; then
        log_dry "${GIT_DIR}/ (git clone)"
        return 0
    fi

    # Warn if the worktree has uncommitted changes
    local dirty=false
    if command -v git &>/dev/null && [[ -d "${GIT_DIR}/.git" ]]; then
        local status_out
        status_out="$(git -C "$GIT_DIR" status --porcelain 2>/dev/null || true)"
        [[ -n "$status_out" ]] && dirty=true
    fi

    if "$dirty"; then
        echo -e "  ${C_YELLOW}${C_BOLD}Warning:${C_NC} ${GIT_DIR} has uncommitted changes."
        if ! "$SILENT"; then
            if ! confirm "${GIT_DIR} has uncommitted changes. Delete anyway?" "y/N"; then
                log_warn "Skipped deletion of ${GIT_DIR} (user chose to keep dirty worktree)"
                return 0
            fi
        fi
    fi

    safe_remove_dir "$GIT_DIR" "${GIT_DIR}/"
}

# ── Step E: Clean shell profile injections ────────────────────
clean_shell_profiles() {
    echo -e "${C_BOLD}Step 5/8 — Remove shell profile injections${C_NC}"

    local profiles=("${HOME}/.bashrc" "${HOME}/.zshrc" "${HOME}/.profile")
    local any_found=false

    for rc in "${profiles[@]}"; do
        if [[ ! -f "$rc" ]]; then
            continue
        fi

        # Check if our marker is present
        if ! grep -q "# NAVIG CLI" "$rc" 2>/dev/null; then
            log_skip "No NAVIG lines in ${rc}"
            continue
        fi

        any_found=true

        if "$DRY_RUN"; then
            log_dry "Remove '# NAVIG CLI' block from ${rc}"
            continue
        fi

        # Create a backup before editing
        local bak="${rc}.navig_uninstall.bak"
        cp -p "$rc" "$bak"

        # Remove the two-line block: a blank line (optional), '# NAVIG CLI', and the
        # immediately following 'export PATH=".../bin:$PATH"' line.
        # Strategy: delete '# NAVIG CLI' and any line directly below it that looks
        # like our injected export, plus a preceding blank line if the result would
        # leave a double-blank.
        local original_size
        original_size="$(wc -c < "$rc")"

        # sed: find the comment line; consume it and the next line (N) which is the
        # export; delete the pair.  A second pass removes any blank line that now
        # directly precedes an EOF or another blank line due to the deletion.
        sed -i '/^# NAVIG CLI$/{
N
/\nexport PATH=".*\/bin:\$PATH"/d
}' "$rc"

        # Guard: if file is now empty and was not empty before, restore
        local new_size
        new_size="$(wc -c < "$rc")"
        if [[ "$new_size" -eq 0 && "$original_size" -gt 0 ]]; then
            mv "$bak" "$rc"
            log_warn "sed produced an empty ${rc} — restored from backup (manual cleanup needed)"
            continue
        fi

        # Clean up backup
        rm -f "$bak"

        # Verify the marker is gone
        if grep -q "# NAVIG CLI" "$rc" 2>/dev/null; then
            log_warn "NAVIG marker still present in ${rc} after edit — manual cleanup needed"
        else
            log_ok "Removed NAVIG injection from ${rc}"
        fi
    done

    if ! "$any_found" && ! "$DRY_RUN"; then
        log_skip "shell profiles (no NAVIG injections found in any profile)"
    fi
}

# ── Step F: Remove ~/.navig config directory ──────────────────
remove_config_dir() {
    echo -e "${C_BOLD}Step 6/8 — Remove ~/.navig config directory${C_NC}"

    local navig_dir="${HOME}/.navig"

    if [[ ! -d "$navig_dir" ]]; then
        log_skip "${navig_dir}"
        return 0
    fi

    if "$DRY_RUN"; then
        log_dry "${navig_dir}/ (config, logs, cache, vault)"
        return 0
    fi

    # Extra confirmation when vault is present (contains SSH keys / tokens)
    if [[ -d "${navig_dir}/vault" ]] && ! "$SILENT"; then
        echo ""
        echo -e "  ${C_RED}${C_BOLD}Warning:${C_NC} ${navig_dir}/vault/ contains private keys and tokens."
        echo -e "  Deleting it is ${C_BOLD}irreversible${C_NC}."
        if ! confirm "Delete ~/.navig/ including vault (keys/tokens will be permanently lost)?" "y/N"; then
            log_warn "Skipped deletion of ${navig_dir} (user chose to keep vault)"
            return 0
        fi
    fi

    safe_remove_dir "$navig_dir" "${navig_dir}/"
}

# ── Step G: Registry entries (N/A for install.sh) ────────────
remove_registry_entries() {
    echo -e "${C_BOLD}Step 7/8 — Windows Registry entries${C_NC}"
    # install.sh exits immediately on native Windows; it does not write
    # any registry entries. This step is intentionally a no-op.
    echo -e "  ${SKP} ${C_DIM}[SKIP]${C_NC} Registry entries: not applicable (install.sh does not write registry entries)"
    (( SKIPPED++ )) || true
}

# ── Step H: Remove cron jobs ──────────────────────────────────
remove_cron_jobs() {
    echo -e "${C_BOLD}Step 8/8 — Remove cron jobs${C_NC}"

    local existing_cron=""
    existing_cron="$(crontab -l 2>/dev/null || true)"

    if [[ -z "$existing_cron" ]]; then
        log_skip "cron jobs (no crontab entries)"
        return 0
    fi

    local navig_lines
    navig_lines="$(echo "$existing_cron" | grep -i navig || true)"

    if [[ -z "$navig_lines" ]]; then
        log_skip "cron jobs (no NAVIG entries in crontab)"
        return 0
    fi

    if "$DRY_RUN"; then
        log_dry "Remove NAVIG cron entries:"
        echo "$navig_lines" | while IFS= read -r line; do
            echo -e "       ${C_DIM}${line}${C_NC}"
        done
        return 0
    fi

    local new_cron
    new_cron="$(echo "$existing_cron" | grep -iv navig || true)"

    if echo "$new_cron" | crontab - 2>/dev/null; then
        log_ok "Removed NAVIG cron entries"
    else
        log_warn "Failed to update crontab — manual cleanup needed"
    fi
}

# ── Developer sync (opt-in) ─────────────────────────────────
run_developer_sync() {
    if [[ "${NAVIG_DEV_SYNC:-0}" != "1" ]]; then
        return 0
    fi

    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local www_dir
    www_dir="$(cd "${script_dir}/../navig-www" 2>/dev/null && pwd || true)"
    if [[ -z "$www_dir" || ! -d "$www_dir" ]]; then
        log_warn "Developer sync skipped — navig-www directory not found at ../navig-www"
        return 0
    fi

    local src_ps1="${script_dir}/uninstall.ps1"
    local src_sh="${script_dir}/uninstall.sh"

    if [[ -f "$src_sh" ]]; then
        if cp -f "$src_sh" "$www_dir/uninstall.sh" 2>/dev/null; then
            log_ok "Synced: uninstall.sh -> $www_dir/uninstall.sh"
        else
            log_warn "Developer sync failed for uninstall.sh"
        fi
    else
        log_skip "$src_sh"
    fi

    if [[ -f "$src_ps1" ]]; then
        if cp -f "$src_ps1" "$www_dir/uninstall.ps1" 2>/dev/null; then
            log_ok "Synced: uninstall.ps1 -> $www_dir/uninstall.ps1"
        else
            log_warn "Developer sync failed for uninstall.ps1"
        fi
    else
        log_skip "$src_ps1"
    fi
}

# ── Exit summary ──────────────────────────────────────────────
print_summary() {
    local sep="═══════════════════════════════════════"
    echo ""
    echo -e "${C_BOLD}${sep}${C_NC}"
    echo -e "${C_BOLD} NAVIG Uninstall Summary${C_NC}"
    echo -e "${C_BOLD}${sep}${C_NC}"
    echo -e " ${CHK} Removed   : ${C_GREEN}${REMOVED}${C_NC} artifacts"
    echo -e " ${WRN} Skipped   : ${C_YELLOW}${SKIPPED}${C_NC} already absent"
    echo -e " ${CRS} Failed    : ${C_RED}${FAILED}${C_NC}$([ "$FAILED" -gt 0 ] && echo " (see warnings above)" || true)"
    echo -e "${C_BOLD}${sep}${C_NC}"

    if "$DRY_RUN"; then
        echo -e " ${C_CYAN}Dry run — no changes were made.${C_NC}"
        echo -e "${C_BOLD}${sep}${C_NC}"
    elif [[ "$FAILED" -eq 0 && "$REMOVED" -gt 0 ]]; then
        echo -e " ${C_DIM}NAVIG has been removed. Restart your shell to clear PATH.${C_NC}"
        echo -e "${C_BOLD}${sep}${C_NC}"
    fi
    echo ""
}

# ── Main ──────────────────────────────────────────────────────
main() {
    parse_args "$@"

    echo ""
    echo -e "${C_BOLD}${C_CYAN}NAVIG Uninstaller${C_NC}"
    echo -e "${C_DIM}Reversing install.sh — Linux / WSL2${C_NC}"
    echo ""

    if "$DRY_RUN"; then
        echo -e "${C_CYAN}--dry-run active: no filesystem changes will be made.${C_NC}"
        echo ""
    fi

    # ── Preflight ─────────────────────────────────────────────
    detect_os
    detect_python_and_prefix
    detect_install_mode

    check_installed   # exits 0 with "Nothing to do" if not installed

    # ── Display plan ──────────────────────────────────────────
    print_plan

    # ── Soft confirmation gate ────────────────────────────────
    if ! "$DRY_RUN" && ! "$SILENT"; then
        if ! confirm "Proceed with uninstall?" "y/N"; then
            echo ""
            echo -e "  Aborted. Nothing was changed."
            echo ""
            _ABORTED=false        # clean, intentional abort — not an error
            _SKIP_SUMMARY=true    # nothing happened; no summary needed
            exit 0
        fi
        echo ""
    fi

    # ── Execute steps (reverse of install.sh order) ───────────
    echo -e "${C_BOLD}Running uninstall steps...${C_NC}"
    echo ""

    stop_daemon
    echo ""
    uninstall_pip_package
    echo ""
    remove_symlinks
    echo ""
    remove_git_clone
    echo ""
    clean_shell_profiles
    echo ""
    remove_config_dir
    echo ""
    remove_registry_entries
    echo ""
    remove_cron_jobs
    echo ""
    run_developer_sync
    echo ""

    # Disable the ERR trap — from here on a non-zero exit is expected
    # (e.g. exit 1 from print_summary when FAILED > 0), and we don't
    # want the trap to flip _ABORTED.
    trap - ERR
    _ABORTED=false

    # EXIT trap will call print_summary and optionally self-destruct.
    if [[ "$FAILED" -gt 0 ]]; then
        exit 1
    fi
    exit 0
}

main "$@"
