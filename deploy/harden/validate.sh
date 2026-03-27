#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# NAVIG Server Validation — Post-Hardening Check
# Standalone script that verifies hardening was applied correctly.
#
# Usage:
#   sudo ./validate.sh            # Full check with colors
#   sudo ./validate.sh --json     # Machine-readable output
#   sudo ./validate.sh --quiet    # Exit code only (0=pass, 1=fail)
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

# ── Options ───────────────────────────────────────────────────
OUTPUT_MODE="human"  # human | json | quiet
while [[ $# -gt 0 ]]; do
    case "$1" in
        --json)  OUTPUT_MODE="json"; shift ;;
        --quiet) OUTPUT_MODE="quiet"; shift ;;
        --help|-h) echo "Usage: sudo ./validate.sh [--json|--quiet]"; exit 0 ;;
        *) shift ;;
    esac
done

# ── Colors ────────────────────────────────────────────────────
if [[ "$OUTPUT_MODE" == "human" ]]; then
    PASS='\033[1;32m✓\033[0m'; FAIL='\033[1;31m✗\033[0m'
    WARN='\033[1;33m!\033[0m'; BOLD='\033[1m'; NC='\033[0m'
else
    PASS='PASS'; FAIL='FAIL'; WARN='WARN'; BOLD=''; NC=''
fi

CHECKS=0
PASSED=0
FAILED=0
WARNINGS=0
JSON_RESULTS="["

check() {
    local name="$1" result="$2" detail="${3:-}"
    CHECKS=$((CHECKS + 1))

    if [[ "$result" == "pass" ]]; then
        PASSED=$((PASSED + 1))
        [[ "$OUTPUT_MODE" == "human" ]] && printf "  ${PASS} %-45s %s\n" "$name" "$detail"
    elif [[ "$result" == "warn" ]]; then
        WARNINGS=$((WARNINGS + 1))
        [[ "$OUTPUT_MODE" == "human" ]] && printf "  ${WARN} %-45s %s\n" "$name" "$detail"
    else
        FAILED=$((FAILED + 1))
        [[ "$OUTPUT_MODE" == "human" ]] && printf "  ${FAIL} %-45s %s\n" "$name" "$detail"
    fi

    if [[ "$OUTPUT_MODE" == "json" ]]; then
        [[ "$CHECKS" -gt 1 ]] && JSON_RESULTS+=","
        JSON_RESULTS+="{\"name\":\"${name}\",\"result\":\"${result}\",\"detail\":\"${detail}\"}"
    fi
}

# ── Root check ────────────────────────────────────────────────
if [[ "$(id -u)" -ne 0 ]]; then
    echo "Warning: Some checks require root. Run with sudo for full results."
fi

[[ "$OUTPUT_MODE" == "human" ]] && echo -e "\n${BOLD}NAVIG Server Validation Report${NC}\n"

# ══════════════════════════════════════════════════════════════
#  System Basics
# ══════════════════════════════════════════════════════════════
[[ "$OUTPUT_MODE" == "human" ]] && echo -e "${BOLD}System:${NC}"

# OS version
source /etc/os-release 2>/dev/null || true
VER_MAJOR="${VERSION_ID%%.*}"
if [[ "$ID" == "ubuntu" && "$VER_MAJOR" -ge 22 ]]; then
    check "Ubuntu 22.04+" "pass" "${PRETTY_NAME}"
else
    check "Ubuntu 22.04+" "fail" "${PRETTY_NAME:-unknown}"
fi

# Hostname
HOSTNAME="$(hostname)"
if [[ "$HOSTNAME" == "navig-server" ]]; then
    check "Hostname set" "pass" "$HOSTNAME"
else
    check "Hostname set" "warn" "$HOSTNAME (expected: navig-server)"
fi

# Swap
SWAP_MB="$(free -m | awk '/^Swap:/{print $2}')"
if [[ "$SWAP_MB" -ge 1000 ]]; then
    check "Swap configured" "pass" "${SWAP_MB}MB"
elif [[ "$SWAP_MB" -gt 0 ]]; then
    check "Swap configured" "warn" "${SWAP_MB}MB (recommend ≥4GB)"
else
    check "Swap configured" "fail" "no swap"
fi

# NTP
NTP_SYNC="$(timedatectl show --property=NTPSynchronized --value 2>/dev/null || echo 'no')"
if [[ "$NTP_SYNC" == "yes" ]]; then
    check "NTP synchronized" "pass" ""
else
    check "NTP synchronized" "warn" "not synced"
fi

echo ""

# ══════════════════════════════════════════════════════════════
#  Security
# ══════════════════════════════════════════════════════════════
[[ "$OUTPUT_MODE" == "human" ]] && echo -e "${BOLD}Security:${NC}"

# navig user exists
if id navig &>/dev/null; then
    check "User 'navig' exists" "pass" "$(id navig)"
else
    check "User 'navig' exists" "fail" "not found"
fi

# SSH: PermitRootLogin
if [[ -f /etc/ssh/sshd_config.d/90-navig-hardening.conf ]]; then
    if grep -q "PermitRootLogin no" /etc/ssh/sshd_config.d/90-navig-hardening.conf; then
        check "SSH root login disabled" "pass" ""
    else
        check "SSH root login disabled" "fail" ""
    fi
else
    # Check main config
    ROOT_LOGIN="$(sshd -T 2>/dev/null | grep -i 'permitrootlogin' | awk '{print $2}')"
    if [[ "$ROOT_LOGIN" == "no" ]]; then
        check "SSH root login disabled" "pass" "(main config)"
    else
        check "SSH root login disabled" "fail" "PermitRootLogin=$ROOT_LOGIN"
    fi
fi

# SSH: Password auth
PASS_AUTH="$(sshd -T 2>/dev/null | grep -i 'passwordauthentication' | awk '{print $2}')"
if [[ "$PASS_AUTH" == "no" ]]; then
    check "SSH password auth disabled" "pass" ""
else
    check "SSH password auth disabled" "fail" "PasswordAuthentication=$PASS_AUTH"
fi

# UFW active
UFW_STATUS="$(ufw status 2>/dev/null | head -1 || echo 'not installed')"
if echo "$UFW_STATUS" | grep -qi "active"; then
    check "UFW firewall active" "pass" ""
else
    check "UFW firewall active" "fail" "$UFW_STATUS"
fi

# fail2ban running
if systemctl is-active fail2ban &>/dev/null; then
    JAILS="$(fail2ban-client status 2>/dev/null | grep 'Jail list' | cut -d: -f2 | xargs)"
    check "fail2ban running" "pass" "jails: ${JAILS}"
else
    check "fail2ban running" "fail" "not active"
fi

# Unattended-upgrades
if dpkg -l unattended-upgrades 2>/dev/null | grep -q '^ii'; then
    check "Unattended-upgrades installed" "pass" ""
else
    check "Unattended-upgrades installed" "fail" ""
fi

echo ""

# ══════════════════════════════════════════════════════════════
#  Kernel Tuning
# ══════════════════════════════════════════════════════════════
[[ "$OUTPUT_MODE" == "human" ]] && echo -e "${BOLD}Kernel Tuning:${NC}"

# sysctl values
declare -A SYSCTL_CHECKS=(
    ["vm.swappiness"]="10"
    ["fs.file-max"]="2097152"
    ["net.core.somaxconn"]="65535"
    ["vm.overcommit_memory"]="1"
)

for key in "${!SYSCTL_CHECKS[@]}"; do
    expected="${SYSCTL_CHECKS[$key]}"
    actual="$(sysctl -n "$key" 2>/dev/null || echo 'unset')"
    if [[ "$actual" == "$expected" ]]; then
        check "sysctl ${key}" "pass" "${actual}"
    else
        check "sysctl ${key}" "warn" "got ${actual}, expected ${expected}"
    fi
done

# File limits
if [[ -f /etc/security/limits.d/99-navig.conf ]]; then
    check "File limits configured" "pass" "/etc/security/limits.d/99-navig.conf"
else
    check "File limits configured" "warn" "config file not found"
fi

echo ""

# ══════════════════════════════════════════════════════════════
#  Docker
# ══════════════════════════════════════════════════════════════
[[ "$OUTPUT_MODE" == "human" ]] && echo -e "${BOLD}Docker:${NC}"

# Docker installed
if command -v docker &>/dev/null; then
    check "Docker installed" "pass" "$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')"
else
    check "Docker installed" "fail" "not found"
fi

# Docker Compose
if docker compose version &>/dev/null; then
    check "Docker Compose plugin" "pass" "$(docker compose version --short 2>/dev/null)"
else
    check "Docker Compose plugin" "fail" "not found"
fi

# Docker daemon.json exists
if [[ -f /etc/docker/daemon.json ]]; then
    # Check for no-new-privileges
    if grep -q '"no-new-privileges"' /etc/docker/daemon.json; then
        check "Docker no-new-privileges" "pass" ""
    else
        check "Docker no-new-privileges" "warn" "not in daemon.json"
    fi
    # Check log limits
    if grep -q '"max-size"' /etc/docker/daemon.json; then
        check "Docker log limits" "pass" ""
    else
        check "Docker log limits" "warn" "no log size limits"
    fi
else
    check "Docker daemon.json" "warn" "not found"
fi

# navig in docker group
if id navig &>/dev/null && groups navig 2>/dev/null | grep -q docker; then
    check "navig in docker group" "pass" ""
else
    check "navig in docker group" "warn" "not in docker group"
fi

echo ""

# ══════════════════════════════════════════════════════════════
#  Filesystem & Services
# ══════════════════════════════════════════════════════════════
[[ "$OUTPUT_MODE" == "human" ]] && echo -e "${BOLD}Filesystem & Services:${NC}"

# /opt/navig structure
for dir in /opt/navig /opt/navig/runtime /opt/navig/models /opt/navig/logs /opt/navig/backups; do
    if [[ -d "$dir" ]]; then
        OWNER="$(stat -c '%U:%G' "$dir" 2>/dev/null || echo 'unknown')"
        check "Directory ${dir}" "pass" "owner: ${OWNER}"
    else
        check "Directory ${dir}" "fail" "missing"
    fi
done

# Backup timer
if systemctl is-enabled navig-backup.timer &>/dev/null; then
    NEXT="$(systemctl show navig-backup.timer --property=NextElapseUSecRealtime --value 2>/dev/null || echo 'unknown')"
    check "Backup timer enabled" "pass" "next: ${NEXT}"
else
    check "Backup timer enabled" "warn" "not enabled"
fi

# Monitoring
if systemctl is-active prometheus-node-exporter &>/dev/null; then
    # Check if bound to localhost
    NE_BIND="$(ss -tlnp 2>/dev/null | grep ':9100' | head -1 || true)"
    if echo "$NE_BIND" | grep -q '127.0.0.1'; then
        check "node_exporter (localhost)" "pass" "127.0.0.1:9100"
    elif [[ -n "$NE_BIND" ]]; then
        check "node_exporter (localhost)" "warn" "may be on 0.0.0.0"
    fi
else
    check "node_exporter" "warn" "not running"
fi

echo ""

# ══════════════════════════════════════════════════════════════
#  Network
# ══════════════════════════════════════════════════════════════
[[ "$OUTPUT_MODE" == "human" ]] && echo -e "${BOLD}Network:${NC}"

# Check for wildcard bindings (excluding SSH)
WILDCARD_COUNT="$(ss -tlnp 2>/dev/null | grep '0.0.0.0:' | grep -v ':22 ' | wc -l)"
if [[ "$WILDCARD_COUNT" -eq 0 ]]; then
    check "No unexpected 0.0.0.0 bindings" "pass" ""
else
    check "No unexpected 0.0.0.0 bindings" "warn" "${WILDCARD_COUNT} services on 0.0.0.0"
fi

echo ""

# ══════════════════════════════════════════════════════════════
#  Summary
# ══════════════════════════════════════════════════════════════
if [[ "$OUTPUT_MODE" == "json" ]]; then
    JSON_RESULTS+="]"
    echo "{\"checks\":${CHECKS},\"passed\":${PASSED},\"failed\":${FAILED},\"warnings\":${WARNINGS},\"results\":${JSON_RESULTS}}"
elif [[ "$OUTPUT_MODE" == "human" ]]; then
    echo -e "${BOLD}Summary:${NC} ${CHECKS} checks — ${PASSED} passed, ${FAILED} failed, ${WARNINGS} warnings"
    if [[ "$FAILED" -eq 0 ]]; then
        echo -e "\033[1;32mServer hardening validated successfully.\033[0m"
    else
        echo -e "\033[1;31m${FAILED} check(s) failed. Review and re-run hardening.\033[0m"
    fi
    echo ""
fi

# Exit code: 0 if no failures
[[ "$FAILED" -eq 0 ]]
