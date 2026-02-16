#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# NAVIG Stack Healthcheck
# Validates all NAVIG infrastructure services are operational.
#
# Usage:
#   navig-healthcheck           Full check with output
#   navig-healthcheck --quiet   Exit code only (for cron/monitoring)
#   navig-healthcheck --json    JSON output (for APIs/automation)
# ─────────────────────────────────────────────────────────────
set -euo pipefail

QUIET=0
JSON=0
ERRORS=0
WARNINGS=0
RESULTS=()

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --quiet|-q) QUIET=1; shift ;;
        --json|-j)  JSON=1; shift ;;
        --help|-h)
            echo "navig-healthcheck [--quiet|--json|--help]"
            exit 0
            ;;
        *) shift ;;
    esac
done

# ── Check function ────────────────────────────────────────────
check() {
    local name="$1"
    local cmd="$2"
    local status="ok"
    local detail=""

    if eval "$cmd" &>/dev/null; then
        status="ok"
    else
        status="fail"
        ERRORS=$((ERRORS + 1))
    fi

    RESULTS+=("{\"name\":\"${name}\",\"status\":\"${status}\"}")

    if [[ "$QUIET" == "0" && "$JSON" == "0" ]]; then
        if [[ "$status" == "ok" ]]; then
            printf "  ✓  %-20s %s\n" "$name" "OK"
        else
            printf "  ✗  %-20s %s\n" "$name" "FAIL"
        fi
    fi
}

warn_check() {
    local name="$1"
    local cmd="$2"

    if ! eval "$cmd" &>/dev/null; then
        WARNINGS=$((WARNINGS + 1))
        RESULTS+=("{\"name\":\"${name}\",\"status\":\"warn\"}")
        if [[ "$QUIET" == "0" && "$JSON" == "0" ]]; then
            printf "  !  %-20s %s\n" "$name" "WARN"
        fi
    else
        RESULTS+=("{\"name\":\"${name}\",\"status\":\"ok\"}")
        if [[ "$QUIET" == "0" && "$JSON" == "0" ]]; then
            printf "  ✓  %-20s %s\n" "$name" "OK"
        fi
    fi
}

# ── Run checks ────────────────────────────────────────────────
if [[ "$QUIET" == "0" && "$JSON" == "0" ]]; then
    echo ""
    echo "  NAVIG Stack Health Check"
    echo "  ────────────────────────"
fi

# Core services
check "Docker"        "docker info"
check "PostgreSQL"    "docker exec navig-postgres pg_isready -U navig"
check "Redis"         "docker exec navig-redis redis-cli ping"
check "Ollama"        "curl -sf http://127.0.0.1:11434/api/tags"

# System resources
check "Disk (>5GB)"   "test \$(df -BG / | tail -1 | awk '{print \$4}' | tr -d G) -ge 5"
check "Memory (>512M)" "test \$(free -m | awk '/^Mem:/{print \$7}') -ge 512"

# Optional services
if command -v navig &>/dev/null; then
    check "NAVIG CLI" "navig --version"
fi

if command -v ollama &>/dev/null; then
    warn_check "Ollama Native" "ollama list"
fi

# systemd services
if systemctl is-active navig-stack &>/dev/null; then
    check "navig-stack.service" "systemctl is-active navig-stack"
fi

# ── Output ────────────────────────────────────────────────────
if [[ "$JSON" == "1" ]]; then
    local_results=$(printf '%s,' "${RESULTS[@]}")
    local_results="[${local_results%,}]"
    echo "{\"checks\":${local_results},\"errors\":${ERRORS},\"warnings\":${WARNINGS},\"timestamp\":\"$(date -Iseconds)\"}"
elif [[ "$QUIET" == "0" ]]; then
    echo ""
    if [[ "$ERRORS" -eq 0 && "$WARNINGS" -eq 0 ]]; then
        echo "  All checks passed ✓"
    elif [[ "$ERRORS" -eq 0 ]]; then
        echo "  Passed with ${WARNINGS} warning(s)"
    else
        echo "  ${ERRORS} check(s) FAILED, ${WARNINGS} warning(s)"
    fi
    echo ""
fi

exit "$ERRORS"
