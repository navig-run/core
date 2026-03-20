#!/usr/bin/env bash
# NAVIG Fast Setup for Linux + Remote Windows/Cloud
# Usage:
#   bash navig_quick_setup.sh              # Interactive
#   bash navig_quick_setup.sh --fast       # Automated (5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAST_MODE=0
TELEGRAM_TOKEN="${TELEGRAM_BOT_TOKEN:-}"

[[ "$1" = "--fast" ]] && FAST_MODE=1

# Colors
declare -A C
C[accent]=$'\e[1;35m'
C[success]=$'\e[1;32m'
C[error]=$'\e[1;31m'
C[warn]=$'\e[1;33m'
C[info]=$'\e[0;36m'
C[reset]=$'\e[0m'

banner() {
    clear
    cat <<'EOF'

╔════════════════════════════════════════════════════════════╗
║                                                            ║
║  ⚡ NAVIG FAST SETUP                                      ║
║     Linux + Remote Windows/Cloud                          ║
║                                                            ║
║  • Install NAVIG (2 min)                                  ║
║  • Mount cloud drives (Google Drive, Dropbox, etc)        ║
║  • Share via Samba (access from Windows)                  ║
║  • Connect to remote servers                              ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝

EOF
}

step() {
    echo ""
    echo -e "${C[accent]}├─ STEP $1${C[reset]} : ${C[info]}$2${C[reset]}"
    echo "${C[accent]}│${C[reset]}"
}

success() {
    echo -e "${C[success]}✓${C[reset]} $1"
}

info() {
    echo -e "${C[info]}ℹ${C[reset]} $1"
}

warn() {
    echo -e "${C[warn]}!${C[reset]} $1"
}

error() {
    echo -e "${C[error]}✗${C[reset]} $1"
}

find_installer() {
    local paths=(
        "install_navig_linux_enhanced.sh"
        "./install_navig_linux_enhanced.sh"
        "../scripts/install_navig_linux_enhanced.sh"
        "$SCRIPT_DIR/install_navig_linux_enhanced.sh"
    )
    
    for p in "${paths[@]}"; do
        [[ -f "$p" ]] && echo "$p" && return 0
    done
    
    return 1
}

main() {
    banner
    
    if [[ $FAST_MODE -eq 1 ]]; then
        echo -e "${C[accent]}⏱️  FAST MODE: Automated setup${C[reset]}"
        echo "   • Pre-installs Samba & rclone"
        echo "   • Skips optional prompts"
        echo "   • ~5 minute setup"
        echo ""
        
        installer=$(find_installer || true)
        if [[ -z "$installer" ]]; then
            error "Enhanced installer not found!"
            exit 1
        fi
        
        info "Running installer: $installer"
        bash "$installer" --install-samba --install-rclone --silent
        
    else
        # Interactive mode
        echo "NAVIG Fast Setup"
        echo "1. Automated setup (5 min) - ${C[success]}recommended${C[reset]}"
        echo "2. Interactive setup"
        echo "0. Exit"
        echo ""
        read -p "Choose: " choice
        
        case $choice in
            1)
                FAST_MODE=1
                main  # Re-run in fast mode
                ;;
            2)
                installer=$(find_installer || true)
                if [[ -z "$installer" ]]; then
                    error "Enhanced installer not found!"
                    exit 1
                fi
                bash "$installer"
                ;;
            *)
                exit 0
                ;;
        esac
    fi
    
    # Show completion summary
    cat <<EOF

${C[accent]}╔════════════════════════════════════════════════════════════╗${C[reset]}
${C[accent]}║  ✅ SETUP COMPLETE!${C[reset]}
${C[accent]}╚════════════════════════════════════════════════════════════╝${C[reset]}

QUICK COMMANDS:
───────────────────────────────────────────────────────────
${C[accent]}# Check NAVIG${C[reset]}
navig --help
navig host list

${C[accent]}# Mount cloud drive${C[reset]}
rclone config            # First time only
rclone mount gdrive: ~/mnt/gdrive --vfs-cache-mode full

${C[accent]}# Share to Windows${C[reset]}
# Access from Windows: \\hostname\navig_share

WHAT'S NEXT:
───────────────────────────────────────────────────────────
1. Linux → Cloud: Configure rclone (Google Drive, Dropbox, etc)
2. Linux → Windows: Access from Windows via Samba
3. Windows → Linux: Use SSHFS or rclone SFTP

EOF
}

main "$@"
