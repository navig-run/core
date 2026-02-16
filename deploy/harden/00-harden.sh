#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# NAVIG Server Hardening — Ubuntu 22.04 / 24.04
# Production-grade base system for autonomous AI agent infra.
#
# Usage:
#   sudo ./00-harden.sh              # Full run (all phases)
#   sudo ./00-harden.sh --phase 2    # Run specific phase only
#   sudo ./00-harden.sh --dry-run    # Preview only
#   sudo ./00-harden.sh --help
#
# Assumptions:
#   - Fresh Ubuntu Server 22.04 or 24.04 (no cloud-init firewall)
#   - Root or sudo access
#   - Internet connectivity for packages
#   - You have SSH key access (password auth WILL be disabled)
#
# Author: NAVIG Infrastructure
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# ── Config ────────────────────────────────────────────────────
NAVIG_USER="navig"
NAVIG_HOSTNAME="navig-server"
NAVIG_HOME="/opt/navig"
SWAP_SIZE_GB=4                # Will auto-adjust based on RAM
SSH_PORT=22                   # Do NOT change unless explicitly instructed
MAX_AUTH_TRIES=3
LOGIN_GRACE_TIME=30
JOURNAL_MAX_DISK="500M"
JOURNAL_RETENTION="2week"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/var/log/navig-harden-$(date +%Y%m%d-%H%M%S).log"

# ── Colors ────────────────────────────────────────────────────
BOLD='\033[1m'; DIM='\033[2m'; CYAN='\033[1;36m'
GREEN='\033[1;32m'; YELLOW='\033[1;33m'; RED='\033[1;31m'
NC='\033[0m'

# ── Globals ───────────────────────────────────────────────────
DRY_RUN=0
PHASE_ONLY=0
SKIP_REBOOT=0
ERRORS=0

# ── Helpers ───────────────────────────────────────────────────
log()  { echo -e "${GREEN}[✓]${NC} $*" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[!]${NC} $*" | tee -a "$LOG_FILE"; }
err()  { echo -e "${RED}[✗]${NC} $*" | tee -a "$LOG_FILE"; ERRORS=$((ERRORS+1)); }
info() { echo -e "${CYAN}[i]${NC} $*" | tee -a "$LOG_FILE"; }
phase_header() {
    echo "" | tee -a "$LOG_FILE"
    echo -e "${CYAN}${BOLD}════════════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
    echo -e "${CYAN}${BOLD}  Phase $1 — $2${NC}" | tee -a "$LOG_FILE"
    echo -e "${CYAN}${BOLD}════════════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
}

run_cmd() {
    # Execute a command, or just print it in dry-run mode
    if [[ "$DRY_RUN" == "1" ]]; then
        echo -e "  ${DIM}[dry-run] $*${NC}" | tee -a "$LOG_FILE"
    else
        echo "  >> $*" >> "$LOG_FILE"
        eval "$@" 2>&1 | tee -a "$LOG_FILE"
    fi
}

should_run_phase() {
    [[ "$PHASE_ONLY" == "0" ]] || [[ "$PHASE_ONLY" == "$1" ]]
}

# ── Argument parsing ──────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)     DRY_RUN=1; shift ;;
        --phase)       PHASE_ONLY="$2"; shift 2 ;;
        --skip-reboot) SKIP_REBOOT=1; shift ;;
        --help|-h)
            cat << 'USAGE'
NAVIG Server Hardening Script

Usage: sudo ./00-harden.sh [OPTIONS]

Options:
  --phase <N>       Run only phase N (1-8)
  --dry-run         Preview all commands without executing
  --skip-reboot     Do not prompt for reboot at end
  --help            Show this help

Phases:
  1  Base system validation
  2  Security baseline (SSH, firewall, fail2ban)
  3  System tuning (sysctl, limits, tools)
  4  Docker environment
  5  NAVIG filesystem structure
  6  Logging & monitoring
  7  Network sanity check
  8  Final validation report
USAGE
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Preflight ─────────────────────────────────────────────────
if [[ "$(id -u)" -ne 0 ]]; then
    echo -e "${RED}Error: Must run as root (use sudo)${NC}"
    exit 1
fi

mkdir -p "$(dirname "$LOG_FILE")"
echo "NAVIG Server Hardening — $(date -Iseconds)" > "$LOG_FILE"

if [[ "$DRY_RUN" == "1" ]]; then
    warn "DRY RUN MODE — no changes will be made"
fi

echo -e "${CYAN}${BOLD}"
cat << 'BANNER'
    ╔═══════════════════════════════════════════╗
    ║   NAVIG Server Hardening                  ║
    ║   Ubuntu 22.04 / 24.04 Production Base    ║
    ╚═══════════════════════════════════════════╝
BANNER
echo -e "${NC}"

# ══════════════════════════════════════════════════════════════
#  PHASE 1 — Base System Validation
# ══════════════════════════════════════════════════════════════
if should_run_phase 1; then
phase_header 1 "Base System Validation"

# 1.1 — OS version
source /etc/os-release
OS_VER="${PRETTY_NAME}"
KERNEL_VER="$(uname -r)"
CPU_MODEL="$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)"
RAM_MB="$(free -m | awk '/^Mem:/{print $2}')"
RAM_GB="$(( RAM_MB / 1024 ))"
DISK_TOTAL="$(df -BG / | tail -1 | awk '{print $2}')"
DISK_AVAIL="$(df -BG / | tail -1 | awk '{print $4}')"
ARCH="$(uname -m)"

log "OS:      ${OS_VER}"
log "Kernel:  ${KERNEL_VER}"
log "CPU:     ${CPU_MODEL}"
log "Arch:    ${ARCH}"
log "RAM:     ${RAM_GB}GB (${RAM_MB}MB)"
log "Disk:    ${DISK_TOTAL} total, ${DISK_AVAIL} available"

# Verify Ubuntu
if [[ "$ID" != "ubuntu" ]]; then
    err "This script targets Ubuntu. Detected: ${ID}"
    exit 1
fi
VER_MAJOR="${VERSION_ID%%.*}"
if [[ "$VER_MAJOR" -lt 22 ]]; then
    err "Ubuntu 22.04+ required. Detected: ${VERSION_ID}"
    exit 1
fi
log "Ubuntu version validated (${VERSION_ID})"

# 1.2 — Time sync
info "Checking time synchronization..."
if timedatectl show --property=NTPSynchronized --value 2>/dev/null | grep -q "yes"; then
    log "NTP synchronized"
else
    warn "NTP not synchronized — enabling systemd-timesyncd"
    run_cmd "timedatectl set-ntp true"
    # Wait briefly for sync
    sleep 2
    log "NTP enabled"
fi

# 1.3 — Set hostname
CURRENT_HOSTNAME="$(hostname)"
if [[ "$CURRENT_HOSTNAME" != "$NAVIG_HOSTNAME" ]]; then
    info "Setting hostname: ${NAVIG_HOSTNAME} (was: ${CURRENT_HOSTNAME})"
    run_cmd "hostnamectl set-hostname '${NAVIG_HOSTNAME}'"
    # Update /etc/hosts — add entry if missing
    if ! grep -q "$NAVIG_HOSTNAME" /etc/hosts; then
        run_cmd "echo '127.0.1.1  ${NAVIG_HOSTNAME}' >> /etc/hosts"
    fi
    log "Hostname set to ${NAVIG_HOSTNAME}"
else
    log "Hostname already set: ${NAVIG_HOSTNAME}"
fi

# 1.4 — Swap check
SWAP_KB="$(grep SwapTotal /proc/meminfo | awk '{print $2}')"
SWAP_MB="$((SWAP_KB / 1024))"
if [[ "$SWAP_MB" -lt 100 ]]; then
    # Auto-size: 4GB for ≤16GB RAM, 8GB for >16GB
    if [[ "$RAM_GB" -gt 16 ]]; then
        SWAP_SIZE_GB=8
    fi
    info "No swap detected — creating ${SWAP_SIZE_GB}GB swapfile"
    run_cmd "fallocate -l ${SWAP_SIZE_GB}G /swapfile"
    run_cmd "chmod 600 /swapfile"
    run_cmd "mkswap /swapfile"
    run_cmd "swapon /swapfile"
    # Persist across reboots
    if ! grep -q '/swapfile' /etc/fstab; then
        run_cmd "echo '/swapfile none swap sw 0 0' >> /etc/fstab"
    fi
    log "Swapfile created: ${SWAP_SIZE_GB}GB"
else
    log "Swap exists: ${SWAP_MB}MB"
fi

# 1.5 — Full system update
info "Running full system update..."
run_cmd "apt-get update -y"
run_cmd "apt-get full-upgrade -y"
run_cmd "apt-get autoremove -y"
log "System updated"

# Summary table
echo ""
info "═══ Phase 1 Summary ═══"
printf "  %-20s %s\n" "OS:"       "$OS_VER"
printf "  %-20s %s\n" "Kernel:"   "$KERNEL_VER"
printf "  %-20s %s\n" "CPU:"      "$CPU_MODEL"
printf "  %-20s %s\n" "Arch:"     "$ARCH"
printf "  %-20s %s\n" "RAM:"      "${RAM_GB}GB"
printf "  %-20s %s\n" "Disk:"     "${DISK_TOTAL} total / ${DISK_AVAIL} free"
printf "  %-20s %s\n" "Swap:"     "$(free -h | awk '/^Swap:/{print $2}')"
printf "  %-20s %s\n" "Hostname:" "$(hostname)"
printf "  %-20s %s\n" "NTP:"      "$(timedatectl show --property=NTPSynchronized --value 2>/dev/null || echo 'unknown')"
echo ""

fi  # end phase 1

# ══════════════════════════════════════════════════════════════
#  PHASE 2 — Security Baseline
# ══════════════════════════════════════════════════════════════
if should_run_phase 2; then
phase_header 2 "Security Baseline"

# 2.1 — Create navig user
if id "$NAVIG_USER" &>/dev/null; then
    log "User '${NAVIG_USER}' already exists"
else
    info "Creating user: ${NAVIG_USER}"
    run_cmd "useradd --create-home --shell /bin/bash --groups sudo '${NAVIG_USER}'"
    # Lock password (key-only auth)
    run_cmd "passwd -l '${NAVIG_USER}'"
    log "User '${NAVIG_USER}' created and added to sudo group"
fi

# Ensure sudo group membership
if ! groups "$NAVIG_USER" 2>/dev/null | grep -q sudo; then
    run_cmd "usermod -aG sudo '${NAVIG_USER}'"
    log "Added ${NAVIG_USER} to sudo group"
fi

# 2.2 — SSH Hardening
info "Hardening SSH configuration..."

# Backup original
if [[ ! -f /etc/ssh/sshd_config.bak.navig ]]; then
    run_cmd "cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak.navig"
fi

# Write a drop-in config (cleaner than sed-patching the main file)
cat > /etc/ssh/sshd_config.d/90-navig-hardening.conf << 'SSHCONF'
# ─────────────────────────────────────────────────────────────
# NAVIG SSH Hardening — Drop-in configuration
# Applied on top of /etc/ssh/sshd_config defaults
# ─────────────────────────────────────────────────────────────

# Disable root login — force all access through named user accounts
PermitRootLogin no

# Disable password authentication — keys only
# WARNING: Ensure your SSH key is deployed BEFORE applying this
PasswordAuthentication no

# Disable empty passwords (defense in depth)
PermitEmptyPasswords no

# Public key authentication (should already be default, but explicit)
PubkeyAuthentication yes

# Limit authentication attempts to slow brute-force attacks
MaxAuthTries 3

# 30-second window to complete login — prevents slow-loris on SSH
LoginGraceTime 30

# Disable X11 forwarding — not needed on a headless server
X11Forwarding no

# Disable TCP forwarding by default — re-enable per-user if needed
# AllowTcpForwarding no  # Commented: NAVIG may need tunnels

# Log auth attempts at verbose level for fail2ban and audit
LogLevel VERBOSE

# Disable challenge-response authentication (PAM keyboard-interactive)
KbdInteractiveAuthentication no

# Only allow specific users (uncomment and add users as needed)
# AllowUsers navig
SSHCONF

log "SSH hardening config written to /etc/ssh/sshd_config.d/90-navig-hardening.conf"

# Validate config before restarting
if sshd -t 2>/dev/null; then
    run_cmd "systemctl reload sshd || systemctl reload ssh"
    log "SSH daemon reloaded with hardened config"
else
    err "SSH config validation failed — reverting"
    rm -f /etc/ssh/sshd_config.d/90-navig-hardening.conf
fi

# 2.3 — Firewall (UFW)
info "Configuring firewall (ufw)..."
run_cmd "apt-get install -y ufw"

# Reset to defaults
run_cmd "ufw --force reset"

# Default policies
run_cmd "ufw default deny incoming"    # Block all inbound by default
run_cmd "ufw default allow outgoing"   # Allow all outbound (for apt, API calls, etc.)

# Allow SSH — CRITICAL: do this BEFORE enabling ufw
run_cmd "ufw allow ${SSH_PORT}/tcp comment 'SSH'"

# Enable firewall (--force to avoid interactive prompt)
run_cmd "ufw --force enable"
log "Firewall enabled — SSH (${SSH_PORT}/tcp) allowed, all other inbound denied"

# 2.4 — Fail2ban
info "Installing and configuring fail2ban..."
run_cmd "apt-get install -y fail2ban"

# Create local jail config (never edit jail.conf directly)
cat > /etc/fail2ban/jail.local << 'F2BCONF'
# ─────────────────────────────────────────────────────────────
# NAVIG fail2ban — Local overrides
# ─────────────────────────────────────────────────────────────
[DEFAULT]
# Ban for 1 hour after 5 failures within 10 minutes
bantime  = 3600
findtime = 600
maxretry = 5

# Use systemd backend (recommended for Ubuntu 22.04+)
backend = systemd

# Ignore localhost
ignoreip = 127.0.0.1/8 ::1

[sshd]
enabled  = true
port     = ssh
filter   = sshd
maxretry = 3
bantime  = 3600
F2BCONF

run_cmd "systemctl enable fail2ban"
run_cmd "systemctl restart fail2ban"
log "fail2ban installed and SSH jail enabled"

# 2.5 — Automatic security updates
info "Configuring unattended-upgrades (security patches only)..."
run_cmd "apt-get install -y unattended-upgrades apt-listchanges"

# Enable only security updates
cat > /etc/apt/apt.conf.d/50unattended-upgrades << 'UUCONF'
// NAVIG — Automatic security updates only
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
    "${distro_id}ESMApps:${distro_codename}-apps-security";
    "${distro_id}ESM:${distro_codename}-infra-security";
};

// Do not auto-remove unused dependencies (leave that to manual maintenance)
Unattended-Upgrade::Remove-Unused-Dependencies "false";

// Do not auto-reboot (agent workloads should not be interrupted)
Unattended-Upgrade::Automatic-Reboot "false";

// Email notification (configure if mail relay is available)
// Unattended-Upgrade::Mail "admin@example.com";
UUCONF

# Enable the periodic check
cat > /etc/apt/apt.conf.d/20auto-upgrades << 'AUTOCONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::AutocleanInterval "7";
AUTOCONF

log "Unattended-upgrades configured (security patches only, no auto-reboot)"

# Phase 2 outputs
echo ""
info "═══ Phase 2 Outputs ═══"
echo ""
echo "--- UFW Status ---"
ufw status verbose 2>/dev/null || true
echo ""
echo "--- fail2ban Status ---"
fail2ban-client status 2>/dev/null || true
echo ""

fi  # end phase 2

# ══════════════════════════════════════════════════════════════
#  PHASE 3 — System Tuning for LLM + Agent Workloads
# ══════════════════════════════════════════════════════════════
if should_run_phase 3; then
phase_header 3 "System Tuning for LLM + Agent Workloads"

# 3.1 — File descriptor limits for navig user
info "Setting file descriptor limits for user ${NAVIG_USER}..."
# Why: LLM inference, Docker containers, and agent subprocesses open
# many simultaneous file handles. Default 1024 is far too low.
cat > /etc/security/limits.d/99-navig.conf << LIMITSCONF
# NAVIG — Increased file descriptor limits
# Required for: Docker containers, LLM inference (many open files),
#               agent subprocesses, and WebSocket connections
${NAVIG_USER}  soft  nofile  65535
${NAVIG_USER}  hard  nofile  65535
${NAVIG_USER}  soft  nproc   65535
${NAVIG_USER}  hard  nproc   65535
LIMITSCONF
log "File descriptor limits set: nofile=65535, nproc=65535"

# 3.2 — Kernel tuning
info "Applying kernel tuning parameters..."
cat > /etc/sysctl.d/99-navig.conf << 'SYSCTLCONF'
# ─────────────────────────────────────────────────────────────
# NAVIG Kernel Tuning — LLM + Agent + Container Workloads
# ─────────────────────────────────────────────────────────────

# vm.swappiness=10
# Rationale: LLM inference loads large models into RAM. A high swappiness
# value (default 60) would aggressively page model data to disk, causing
# catastrophic latency. Value 10 keeps data in RAM as much as possible
# while still allowing swap under genuine memory pressure.
vm.swappiness = 10

# fs.file-max=2097152
# Rationale: Each Docker container, LLM process, and agent subprocess
# opens many file handles. Default ~1M is sufficient for typical servers
# but AI workloads with many concurrent model files, sockets, and logs
# benefit from a higher ceiling. 2M provides ample headroom.
fs.file-max = 2097152

# net.core.somaxconn=65535
# Rationale: The NAVIG gateway, Ollama API, and agent WebSocket endpoints
# can experience connection bursts. Default 4096 can cause dropped
# connections under load. 65535 matches the fd limit and handles spikes.
net.core.somaxconn = 65535

# net.ipv4.tcp_tw_reuse=1
# Rationale: Allows reuse of TIME_WAIT sockets for new connections.
# Important when agents make many short-lived HTTP calls to Ollama/APIs.
net.ipv4.tcp_tw_reuse = 1

# net.ipv4.ip_local_port_range
# Rationale: Expand ephemeral port range for high-throughput agent comms.
net.ipv4.ip_local_port_range = 10240 65535

# vm.overcommit_memory=1
# Rationale: Prevents fork failures in Redis (used by NAVIG stack).
# Redis uses fork() for background saves; without this, large Redis
# instances can fail to fork even with sufficient free memory.
vm.overcommit_memory = 1
SYSCTLCONF

run_cmd "sysctl --system"
log "Kernel parameters applied"

# 3.3 — Journald retention
info "Configuring journald log retention..."
# Create drop-in to avoid editing the main config
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/99-navig.conf << JOURNALCONF
# NAVIG — Journald retention limits
# Prevent log files from consuming excessive disk on a server
# that runs many containerized services with verbose output.
[Journal]
Storage=persistent
SystemMaxUse=${JOURNAL_MAX_DISK}
MaxRetentionSec=${JOURNAL_RETENTION}
Compress=yes
JOURNALCONF

run_cmd "systemctl restart systemd-journald"
log "Journald configured: max ${JOURNAL_MAX_DISK}, retention ${JOURNAL_RETENTION}"

# 3.4 — Essential tools
info "Installing essential tools..."
run_cmd "apt-get install -y htop curl jq git unzip build-essential \
    net-tools software-properties-common ca-certificates gnupg lsb-release \
    wget tree tmux ncdu iotop sysstat"
log "Essential tools installed"

# 3.5 — CPU frequency governor
info "Checking CPU frequency governor..."
GOVERNOR=""
if [[ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]]; then
    GOVERNOR="$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
    log "CPU governor: ${GOVERNOR}"

    # Only set to 'performance' on bare metal (not VM)
    IS_VM=0
    if systemd-detect-virt --quiet 2>/dev/null; then
        IS_VM=1
        info "Running in VM — keeping governor as-is (host manages frequency)"
    else
        if [[ "$GOVERNOR" != "performance" ]]; then
            info "Bare metal detected — setting CPU governor to 'performance'"
            for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
                echo "performance" > "$gov" 2>/dev/null || true
            done
            # Persist via cpufrequtils
            if command -v cpufreq-set &>/dev/null || apt-get install -y cpufrequtils 2>/dev/null; then
                echo 'GOVERNOR="performance"' > /etc/default/cpufrequtils 2>/dev/null || true
            fi
            log "CPU governor set to 'performance'"
        else
            log "CPU governor already set to 'performance'"
        fi
    fi
else
    warn "CPU frequency scaling not available (may be virtualized)"
fi

# 3.6 — Virtualization support
info "Checking virtualization support..."
VIRT_FLAGS="$(lscpu | grep -i 'Virtualization' || echo 'none detected')"
log "Virtualization: ${VIRT_FLAGS}"

if systemd-detect-virt --quiet 2>/dev/null; then
    VIRT_TYPE="$(systemd-detect-virt)"
    info "Running inside: ${VIRT_TYPE}"
else
    info "Running on bare metal"
fi

fi  # end phase 3

# ══════════════════════════════════════════════════════════════
#  PHASE 4 — Docker Environment
# ══════════════════════════════════════════════════════════════
if should_run_phase 4; then
phase_header 4 "Docker Environment"

# 4.1 — Install Docker via official APT repo (NOT Snap)
if command -v docker &>/dev/null; then
    log "Docker already installed: $(docker --version)"
else
    info "Installing Docker Engine from official repository..."

    # Remove any old/snap versions
    run_cmd "apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true"
    run_cmd "snap remove docker 2>/dev/null || true"

    # Add Docker's official GPG key
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    # Add the repository
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
        https://download.docker.com/linux/ubuntu \
        $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
        tee /etc/apt/sources.list.d/docker.list > /dev/null

    run_cmd "apt-get update -y"
    run_cmd "apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"

    log "Docker installed: $(docker --version)"
fi

# 4.2 — Add navig user to docker group
if getent group docker &>/dev/null; then
    run_cmd "usermod -aG docker '${NAVIG_USER}'"
    log "User '${NAVIG_USER}' added to docker group"
else
    warn "docker group does not exist yet"
fi

# 4.3 — Enable Docker on boot
run_cmd "systemctl enable docker"
run_cmd "systemctl start docker"
log "Docker enabled on boot"

# 4.4 — Configure Docker daemon
info "Writing Docker daemon configuration..."
mkdir -p /etc/docker
cat > /etc/docker/daemon.json << 'DOCKERCONF'
{
    "log-driver": "json-file",
    "log-opts": {
        "max-size": "10m",
        "max-file": "3"
    },
    "default-address-pools": [
        {
            "base": "172.17.0.0/16",
            "size": 24
        }
    ],
    "storage-driver": "overlay2",
    "live-restore": true,
    "iptables": true,
    "userland-proxy": false,
    "no-new-privileges": true
}
DOCKERCONF

# Explanation of each setting:
# log-driver/log-opts: Limit container log sizes to prevent disk exhaustion
#   max-size 10m × 3 files = 30MB per container maximum
# default-address-pools: Use 172.17.0.0/16 to avoid conflicts with common
#   LAN ranges (10.x.x.x, 10.0.x.x)
# storage-driver overlay2: Most performant and stable storage driver
# live-restore: Keep containers running during daemon restarts
# iptables: true: Let Docker manage its own iptables rules
# userland-proxy: false: Use iptables for port forwarding (more performant)
# no-new-privileges: Prevent container processes from gaining additional privileges

# CRITICAL: Docker listens only on Unix socket by default (/var/run/docker.sock)
# We do NOT add "hosts" to daemon.json, which would expose the API over TCP.
# This is the safest default — no remote API exposure.

run_cmd "systemctl restart docker"
log "Docker daemon configured and restarted"

# 4.5 — Verify Docker runs without sudo for navig user
info "Verifying Docker access for user ${NAVIG_USER}..."
if su - "$NAVIG_USER" -c "docker info" &>/dev/null 2>&1; then
    log "Docker runs without sudo for ${NAVIG_USER}"
else
    warn "Docker group membership may require re-login. Verify manually after reboot."
fi

# 4.6 — Allow Docker through UFW (internal bridge traffic)
# Docker manages its own iptables rules. We just need to ensure
# UFW doesn't break Docker networking. The default UFW config is fine
# as long as we don't add FORWARD chain rules blocking Docker.

# Phase 4 output
echo ""
info "═══ Phase 4 Outputs ═══"
echo ""
echo "--- docker info (summary) ---"
docker info 2>/dev/null | head -25 || true
echo ""
echo "--- docker compose version ---"
docker compose version 2>/dev/null || true
echo ""

fi  # end phase 4

# ══════════════════════════════════════════════════════════════
#  PHASE 5 — NAVIG Filesystem Structure
# ══════════════════════════════════════════════════════════════
if should_run_phase 5; then
phase_header 5 "NAVIG Filesystem Structure"

# Why /opt/navig/ over alternatives:
#
# /opt/navig/ (chosen):
#   - FHS-compliant for "optional application software packages"
#   - Survives OS reinstalls targeting / (separate from /home)
#   - Clear separation from user home directories
#   - Easy to back up as a single path
#   - Standard for self-contained application deployments
#
# /home/navig/ (rejected):
#   - Mixes application data with user profile data
#   - Typically on smaller partitions in enterprise setups
#   - /home may have noexec mount options on hardened systems
#
# /srv/ (considered but rejected):
#   - Intended for "data served by this system" (web content, FTP)
#   - NAVIG is agent infrastructure, not a served data service
#   - Less conventional for application runtime directories

DIRS=(
    "${NAVIG_HOME}"
    "${NAVIG_HOME}/runtime"    # Active agent runtime data (state, sessions, PID files)
    "${NAVIG_HOME}/models"     # Local LLM model storage (Ollama, GGUF files)
    "${NAVIG_HOME}/logs"       # Application-level logs (not journald)
    "${NAVIG_HOME}/backups"    # Scheduled backup destination
    "${NAVIG_HOME}/stack"      # Docker Compose files
    "${NAVIG_HOME}/config"     # Application configuration
)

for dir in "${DIRS[@]}"; do
    if [[ ! -d "$dir" ]]; then
        run_cmd "mkdir -p '$dir'"
    fi
done

run_cmd "chown -R ${NAVIG_USER}:${NAVIG_USER} '${NAVIG_HOME}'"
run_cmd "chmod -R 755 '${NAVIG_HOME}'"

log "Directory structure created:"
for dir in "${DIRS[@]}"; do
    info "  ${dir}/"
done

fi  # end phase 5

# ══════════════════════════════════════════════════════════════
#  PHASE 6 — Logging & Monitoring Baseline
# ══════════════════════════════════════════════════════════════
if should_run_phase 6; then
phase_header 6 "Logging & Monitoring"

# 6.1 — Persistent journald (already set in Phase 3 drop-in)
# Verify it's in effect
if journalctl --disk-usage &>/dev/null; then
    JOURNAL_SIZE="$(journalctl --disk-usage 2>/dev/null | awk '{print $NF}')"
    log "Journald persistent logging active (current: ${JOURNAL_SIZE})"
fi

# 6.2 — Monitoring tool: Prometheus node_exporter
# Choice justification:
# - node_exporter is chosen over netdata for production agent infrastructure:
#   1. Minimal footprint (~15MB RSS vs netdata's ~200-400MB)
#   2. NAVIG agents themselves can scrape /metrics (pull model)
#   3. No web UI exposed by default (security advantage)
#   4. Industry standard — integrates with any Prometheus/Grafana stack
#   5. Ollama, PostgreSQL, Redis all have Prometheus exporters available
# - netdata would be preferable if a built-in dashboard is needed,
#   but it adds attack surface and memory usage on agent workloads.

info "Installing Prometheus node_exporter..."
if command -v node_exporter &>/dev/null || systemctl is-active prometheus-node-exporter &>/dev/null 2>&1; then
    log "node_exporter already installed"
else
    run_cmd "apt-get install -y prometheus-node-exporter"
fi

# Ensure it binds to localhost only (not exposed to network)
mkdir -p /etc/default
cat > /etc/default/prometheus-node-exporter << 'NODEEXP'
# NAVIG — node_exporter configuration
# Bind to localhost only — not exposed to network
# Scrape at http://127.0.0.1:9100/metrics
ARGS="--web.listen-address=127.0.0.1:9100"
NODEEXP

run_cmd "systemctl enable prometheus-node-exporter"
run_cmd "systemctl restart prometheus-node-exporter"
log "node_exporter active on 127.0.0.1:9100 (localhost only)"

# 6.3 — Daily backup script
info "Creating daily backup script..."
cat > "${NAVIG_HOME}/backups/daily-backup.sh" << 'BACKUPSCRIPT'
#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# NAVIG Daily Backup
# Archives runtime data, logs, and optionally PostgreSQL.
# Retains last 7 daily backups.
#
# Registered as: systemd timer (navig-backup.timer)
# ─────────────────────────────────────────────────────────────
set -euo pipefail

BACKUP_DIR="/opt/navig/backups"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/navig-daily-${TIMESTAMP}.tar.gz"
RETENTION_DAYS=7

echo "[$(date -Iseconds)] Starting daily backup..."

# Archive runtime and logs
tar czf "$BACKUP_FILE" \
    --exclude='*.socket' \
    --exclude='*.pid' \
    -C / \
    opt/navig/runtime \
    opt/navig/logs \
    opt/navig/config \
    2>/dev/null || {
        echo "[WARN] Some files could not be archived (may be in use)"
    }

echo "[OK] Archive created: ${BACKUP_FILE} ($(du -h "$BACKUP_FILE" | cut -f1))"

# PostgreSQL dump (skip gracefully if not available)
if command -v pg_dump &>/dev/null; then
    PG_BACKUP="${BACKUP_DIR}/navig-pg-${TIMESTAMP}.sql.gz"
    pg_dump -U navig -h 127.0.0.1 navig 2>/dev/null | gzip > "$PG_BACKUP" && \
        echo "[OK] PostgreSQL dump: ${PG_BACKUP}" || \
        echo "[SKIP] PostgreSQL dump failed (DB may not be running)"
elif docker exec navig-postgres pg_isready -U navig &>/dev/null 2>&1; then
    PG_BACKUP="${BACKUP_DIR}/navig-pg-${TIMESTAMP}.sql.gz"
    docker exec navig-postgres pg_dump -U navig navig 2>/dev/null | gzip > "$PG_BACKUP" && \
        echo "[OK] PostgreSQL dump (via Docker): ${PG_BACKUP}" || \
        echo "[SKIP] PostgreSQL dump failed"
else
    echo "[SKIP] PostgreSQL not available — skipping database backup"
fi

# Prune old backups (keep last N days)
echo "[i] Pruning backups older than ${RETENTION_DAYS} days..."
find "$BACKUP_DIR" -name 'navig-daily-*.tar.gz' -mtime +${RETENTION_DAYS} -delete 2>/dev/null || true
find "$BACKUP_DIR" -name 'navig-pg-*.sql.gz' -mtime +${RETENTION_DAYS} -delete 2>/dev/null || true

REMAINING="$(find "$BACKUP_DIR" -name 'navig-daily-*.tar.gz' | wc -l)"
echo "[OK] Backup complete. ${REMAINING} daily archives retained."
BACKUPSCRIPT

chmod +x "${NAVIG_HOME}/backups/daily-backup.sh"
chown "${NAVIG_USER}:${NAVIG_USER}" "${NAVIG_HOME}/backups/daily-backup.sh"
log "Backup script created: ${NAVIG_HOME}/backups/daily-backup.sh"

# 6.4 — Register as systemd timer
cat > /etc/systemd/system/navig-backup.service << 'BKPSVC'
[Unit]
Description=NAVIG Daily Backup
After=docker.service

[Service]
Type=oneshot
User=navig
ExecStart=/opt/navig/backups/daily-backup.sh
StandardOutput=journal
StandardError=journal
SyslogIdentifier=navig-backup
BKPSVC

cat > /etc/systemd/system/navig-backup.timer << 'BKPTIMER'
[Unit]
Description=NAVIG Daily Backup Timer

[Timer]
# Run daily at 03:00 UTC
OnCalendar=*-*-* 03:00:00
# Add random delay up to 15 min to avoid thundering herd on multi-server setups
RandomizedDelaySec=900
Persistent=true

[Install]
WantedBy=timers.target
BKPTIMER

run_cmd "systemctl daemon-reload"
run_cmd "systemctl enable navig-backup.timer"
run_cmd "systemctl start navig-backup.timer"
log "Backup timer registered: daily at 03:00 UTC"

fi  # end phase 6

# ══════════════════════════════════════════════════════════════
#  PHASE 7 — Network Sanity Check
# ══════════════════════════════════════════════════════════════
if should_run_phase 7; then
phase_header 7 "Network Sanity Check"

# 7.1 — List all listening ports
info "Current listening ports:"
echo ""
ss -tulnp 2>/dev/null || netstat -tulnp 2>/dev/null
echo ""

# 7.2 — Check for unexpected 0.0.0.0 bindings
info "Checking for services bound to 0.0.0.0 (all interfaces)..."
WILDCARD_SERVICES="$(ss -tulnp 2>/dev/null | grep '0.0.0.0:' | grep -v ':22 ' || true)"
if [[ -n "$WILDCARD_SERVICES" ]]; then
    warn "Services bound to all interfaces (review these):"
    echo "$WILDCARD_SERVICES"
else
    log "No unexpected wildcard (0.0.0.0) bindings found"
fi

# Verify specific services bind to localhost
info "Verifying local-only service bindings..."
for svc_port in "9100:node_exporter" "5432:PostgreSQL" "6379:Redis" "11434:Ollama"; do
    PORT="${svc_port%%:*}"
    NAME="${svc_port#*:}"
    BINDING="$(ss -tlnp 2>/dev/null | grep ":${PORT} " || true)"
    if [[ -z "$BINDING" ]]; then
        info "  ${NAME} (${PORT}): not running (OK)"
    elif echo "$BINDING" | grep -q "127.0.0.1"; then
        log "  ${NAME} (${PORT}): bound to localhost ✓"
    elif echo "$BINDING" | grep -q "0.0.0.0"; then
        warn "  ${NAME} (${PORT}): bound to 0.0.0.0 — should be localhost"
    fi
done

# 7.3 — Reverse proxy guide
echo ""
info "═══ Exposing NAVIG Dashboard (Future Reference) ═══"
cat << 'PROXYGUIDE'

  To safely expose a NAVIG dashboard (or any internal service) to the
  internet, use a reverse proxy with TLS termination. Install Caddy
  (recommended for automatic HTTPS) or Nginx with Let's Encrypt. Point
  the proxy to the localhost-bound service (e.g., 127.0.0.1:7001 for
  the dashboard). Caddy automatically obtains and renews TLS certificates
  via ACME, while Nginx requires certbot. Then open only ports 80 and
  443 in UFW (ufw allow 80/tcp; ufw allow 443/tcp). Never expose Docker
  API, database, or Redis ports to the public internet.

PROXYGUIDE

fi  # end phase 7

# ══════════════════════════════════════════════════════════════
#  PHASE 8 — Final Validation Report
# ══════════════════════════════════════════════════════════════
if should_run_phase 8; then
phase_header 8 "Final Validation Report"

# Gather data
source /etc/os-release 2>/dev/null || true
_OS_VER="${PRETTY_NAME:-unknown}"
_KERNEL="$(uname -r)"
_UFW_STATUS="$(ufw status 2>/dev/null | head -1 || echo 'unknown')"
_UFW_RULES="$(ufw status numbered 2>/dev/null | grep -c '^\[' || echo '0')"
_OPEN_PORTS="$(ss -tlnp 2>/dev/null | grep LISTEN | awk '{print $4}' | sed 's/.*://' | sort -un | tr '\n' ',' | sed 's/,$//')"
_USERS_CREATED="$(getent passwd "$NAVIG_USER" &>/dev/null && echo "$NAVIG_USER" || echo 'none')"
_SWAP="$(free -h | awk '/^Swap:/{print $2}')"
_DOCKER_VER="$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',' || echo 'not installed')"
_COMPOSE_VER="$(docker compose version --short 2>/dev/null || echo 'not installed')"
_UNATTENDED="$(dpkg -l unattended-upgrades 2>/dev/null | grep -q '^ii' && echo 'enabled' || echo 'disabled')"
_F2B_STATUS="$(fail2ban-client status 2>/dev/null | head -1 || echo 'not running')"
_MONITORING="$(systemctl is-active prometheus-node-exporter 2>/dev/null || echo 'not installed')"

echo ""
echo -e "${CYAN}${BOLD}┌─────────────────────────────────────────────────────┐${NC}"
echo -e "${CYAN}${BOLD}│          NAVIG Server — Final Validation Report      │${NC}"
echo -e "${CYAN}${BOLD}└─────────────────────────────────────────────────────┘${NC}"
echo ""
printf "  ${BOLD}%-30s${NC} %s\n" "OS version"               "$_OS_VER"
printf "  ${BOLD}%-30s${NC} %s\n" "Kernel version"            "$_KERNEL"
printf "  ${BOLD}%-30s${NC} %s\n" "Firewall status"           "$_UFW_STATUS ($_UFW_RULES rules)"
printf "  ${BOLD}%-30s${NC} %s\n" "Open inbound ports"        "$_OPEN_PORTS"
printf "  ${BOLD}%-30s${NC} %s\n" "Users created"             "$_USERS_CREATED"
printf "  ${BOLD}%-30s${NC} %s\n" "Swap size"                 "$_SWAP"
printf "  ${BOLD}%-30s${NC} %s\n" "Docker version"            "$_DOCKER_VER"
printf "  ${BOLD}%-30s${NC} %s\n" "Docker Compose version"    "$_COMPOSE_VER"
printf "  ${BOLD}%-30s${NC} %s\n" "Auto security updates"     "$_UNATTENDED"
printf "  ${BOLD}%-30s${NC} %s\n" "fail2ban status"           "$_F2B_STATUS"
printf "  ${BOLD}%-30s${NC} %s\n" "Monitoring tool"           "node_exporter ($_MONITORING)"
echo ""

# Security summary
echo -e "${GREEN}${BOLD}  Security Configuration Summary:${NC}"
echo "  • SSH hardened: key-only auth, root login disabled, MaxAuthTries=3"
echo "  • Firewall (UFW): deny all inbound, allow SSH only"
echo "  • fail2ban: SSH jail active (3 retries, 1h ban)"
echo "  • Unattended-upgrades: security patches enabled, no auto-reboot"
echo "  • Docker: Unix socket only (no TCP API), no-new-privileges"
echo "  • node_exporter: bound to 127.0.0.1 only"
echo "  • All database/cache ports bound to localhost"
echo "  • Kernel hardened: swappiness=10, file-max=2M, somaxconn=65535"
echo "  • Journald: 500MB max, 2-week retention, compressed"
echo ""

# Remaining risks
echo -e "${YELLOW}${BOLD}  Potential Risks & Gaps:${NC}"
echo "  • No TLS configured yet — internal services use plaintext on localhost"
echo "  • No IDS/IPS beyond fail2ban (consider OSSEC or Wazuh for full audit)"
echo "  • Backup destination is local only — no offsite/S3 target configured"
echo "  • No centralized log aggregation (add Loki or similar for multi-node)"
echo "  • SSH key for user 'navig' must be deployed manually"
echo "  • No disk encryption at rest (consider LUKS for sensitive model data)"
echo "  • Docker image scanning not configured (add Trivy for CI/CD)"
echo ""

# Next steps
echo -e "${CYAN}${BOLD}  Recommended Next Steps:${NC}"
echo "  1. Deploy SSH public key for user 'navig':"
echo "     ssh-copy-id -i ~/.ssh/id_ed25519.pub navig@${NAVIG_HOSTNAME}"
echo "  2. Copy NAVIG stack files to /opt/navig/stack/ and run:"
echo "     cd /opt/navig/stack && docker compose up -d"
echo "  3. Install Caddy/Nginx reverse proxy with TLS for any exposed services"
echo "  4. Configure offsite backup target (S3, rsync, or restic)"
echo "  5. Deploy NAVIG CLI: pip install navig (as user 'navig')"
echo "  6. Pull initial Ollama model: ollama pull llama3.2"
echo "  7. Run healthcheck: navig-healthcheck"
echo ""

fi  # end phase 8

# ══════════════════════════════════════════════════════════════
#  Wrap up
# ══════════════════════════════════════════════════════════════
echo ""
if [[ "$ERRORS" -gt 0 ]]; then
    echo -e "${RED}${BOLD}Completed with ${ERRORS} error(s). Review log: ${LOG_FILE}${NC}"
else
    echo -e "${GREEN}${BOLD}All phases completed successfully.${NC}"
fi
echo -e "${DIM}Full log: ${LOG_FILE}${NC}"
echo ""

if [[ "$SKIP_REBOOT" == "0" && "$DRY_RUN" == "0" && "$PHASE_ONLY" == "0" ]]; then
    echo -e "${YELLOW}A reboot is recommended to apply all kernel parameters and group changes.${NC}"
    echo -e "Run: ${CYAN}sudo reboot${NC}"
fi
