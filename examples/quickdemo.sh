#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# NAVIG Quick Demo — 60-second first-run walkthrough
#
# Requires: pip install navig
# Runs entirely on localhost — no remote server required.
#
# Usage:
#   bash examples/quickdemo.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SEP="───────────────────────────────────────────────────────────────"

step() {
    echo ""
    echo "  $SEP"
    printf "  %-60s\n" "▶  $1"
    echo "  $SEP"
    echo ""
}

banner() {
    echo ""
    echo "  ╔══════════════════════════════════════════════════════════╗"
    echo "  ║          NAVIG — 60-second demo walkthrough             ║"
    echo "  ║     One CLI for all your infrastructure operations      ║"
    echo "  ╚══════════════════════════════════════════════════════════╝"
    echo ""
}

# ── Preflight ────────────────────────────────────────────────────────────────
if ! command -v navig &>/dev/null; then
    echo "  [!!] navig not found. Install with: pip install navig"
    exit 1
fi

banner

# ── Step 1: Version ──────────────────────────────────────────────────────────
step "1/6  Version check"
navig --version

# ── Step 2: Discover local host ──────────────────────────────────────────────
step "2/6  Discover local machine as a host"
navig host discover-local 2>/dev/null || echo "  (local host already registered)"

# ── Step 3: Run a remote command (locally) ───────────────────────────────────
step "3/6  Run your first command via NAVIG"
navig run "echo '  Hello from NAVIG!'; uname -a; uptime"

# ── Step 4: File operations ───────────────────────────────────────────────────
step "4/6  List files on the host"
navig file list /tmp --tree --depth 2 2>/dev/null || navig file list /tmp --all

# ── Step 5: Show active host context ─────────────────────────────────────────
step "5/6  Show active host context"
navig host show 2>/dev/null || navig status

# ── Step 6: Help overview ─────────────────────────────────────────────────────
step "6/6  Core command reference"
echo "  Most useful commands to know:"
echo ""
echo "    navig host add              Add a remote SSH host"
echo "    navig host use <name>       Switch to a host context"
echo "    navig run \"<cmd>\"           Run any command on the active host"
echo "    navig db query \"SELECT 1\"   Query a remote database"
echo "    navig file show <path>      Read a remote file"
echo "    navig docker ps             List containers on the host"
echo "    navig vault set KEY=value   Store an encrypted secret"
echo "    navig ai ask \"...\"          Ask the AI operator anything"
echo "    navig help                  Browse all commands"
echo ""

echo ""
echo "  $SEP"
echo "  ✅  Demo complete. Run 'navig help' to explore all commands."
echo "  $SEP"
echo ""
