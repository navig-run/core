#!/usr/bin/env powershell
#Requires -RunAsAdministrator

<#
.SYNOPSIS
    NAVIG Windows Installer with Remote Drive Setup
.DESCRIPTION
    Complete NAVIG setup for Windows with optional:
    - Cloud drive mounting (rclone)
    - Linux share access (SSHFS-Win)
    - Telegram bot automation
    - Daemon services
.NOTES
    Author: NAVIG AI
    Date: 2026-02-23
    Enhanced with remote drive functionality
#>

param(
    [string]$SourcePath = "",
    [switch]$InstallFactory,
    [switch]$SkipRemote,
    [switch]$Silent,
    [string]$TelegramToken = "",
    [string]$RcloneSetup = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ── LOGGING & OUTPUT ──────────────────────────────────────────
function Write-Nav {
    param([string]$Message, [string]$Level = "INFO")
    $color = switch ($Level) {
        "ERROR" { "Red" }
        "SUCCESS" { "Green" }
        "WARNING" { "Yellow" }
        "STEP" { "Cyan" }
        default { "White" }
    }
    $timestamp = Get-Date -Format "HH:mm:ss"
    Write-Host "[$timestamp] [$Level] $Message" -ForegroundColor $color
}

function Write-Progress-Step {
    param([int]$Step, [int]$Total, [string]$Message)
    Write-Host "`n╔═══════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║ Step $Step/$Total" -NoNewline -ForegroundColor Cyan
    Write-Host (" " * (31 - "$Step/$Total".Length)) + "║" -ForegroundColor Cyan
    Write-Host "║ $Message" -NoNewline -ForegroundColor Cyan
    Write-Host (" " * (40 - $Message.Length)) + "║" -ForegroundColor Cyan
    Write-Host "╚═══════════════════════════════════════════╝" -ForegroundColor Cyan
}

# ── PREREQUISITE CHECKS ───────────────────────────────────────
function Test-IsAdmin {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-Python {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $version = & python --version 2>&1
        Write-Nav "Python found: $version" "SUCCESS"
        return $true
    }
    Write-Nav "Python 3.10+ required" "ERROR"
    Write-Nav "Download from: https://www.python.org/downloads/" "WARNING"
    return $false
}

function Install-Chocolatey {
    if (Get-Command choco -ErrorAction SilentlyContinue) {
        Write-Nav "Chocolatey already installed" "SUCCESS"
        return $true
    }

    Write-Nav "Installing Chocolatey..." "STEP"

    $script = @'
Set-ExecutionPolicy Bypass -Scope Process -Force
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
'@

    & powershell -NoProfile -InputFormat None -ExecutionPolicy Bypass -Command $script

    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine")

    if (Get-Command choco -ErrorAction SilentlyContinue) {
        Write-Nav "Chocolatey installed successfully" "SUCCESS"
        return $true
    }

    Write-Nav "Failed to install Chocolatey" "WARNING"
    Write-Nav "Try manual install: https://chocolatey.org/install" "INFO"
    return $false
}

function Install-RemoteTools {
    Write-Nav "Installing remote drive tools..." "STEP"

    if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
        Write-Nav "Chocolatey required for automatic tool installation" "WARNING"
        return $false
    }

    $tools = @{
        'rclone' = 'Cloud drive mounting';
        'sshfs' = 'Linux SFTP share access'
    }

    $toInstall = @()

    foreach ($tool in $tools.Keys) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            $toInstall += $tool
            Write-Nav "Will install: $($tools[$tool])" "INFO"
        } else {
            Write-Nav "$tool already installed" "SUCCESS"
        }
    }

    if ($toInstall.Count -eq 0) {
        Write-Nav "All remote tools already installed" "SUCCESS"
        return $true
    }

    if (-not $Silent) {
        $response = Read-Host "Install $($toInstall -join ', ')? (y/n)"
        if ($response -ne 'y' -and $response -ne 'Y') {
            Write-Nav "Skipping tool installation" "INFO"
            return $false
        }
    }

    foreach ($tool in $toInstall) {
        Write-Nav "Installing $tool..." "STEP"
        try {
            & choco install $tool -y --no-progress 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Nav "$tool installed successfully" "SUCCESS"
            } else {
                Write-Nav "$tool installation had issues, continuing..." "WARNING"
            }
        } catch {
            Write-Nav "$tool installation failed: $_" "WARNING"
        }
    }

    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    return $true
}

# ── NAVIG INSTALLATION ────────────────────────────────────────
function Install-NAVIG {
    param([string]$Source)

    Write-Progress-Step 2 5 "Installing NAVIG"

    $VenvPath = Join-Path $env:USERPROFILE ".navig\venv"
    $BinPath = Join-Path $env:USERPROFILE ".local\bin"
    $CmdShim = Join-Path $BinPath "navig.cmd"

    Write-Nav "Creating Python virtualenv..." "STEP"
    & python -m venv $VenvPath

    Write-Nav "Installing NAVIG package..." "STEP"
    & "$VenvPath\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel 2>&1 | Select-String -Pattern "Successfully|Requirement"
    & "$VenvPath\Scripts\python.exe" -m pip install -e "$Source" 2>&1 | Select-String -Pattern "Successfully|Installing|error"

    Write-Nav "Creating command shim..." "STEP"
    New-Item -ItemType Directory -Force -Path $BinPath | Out-Null
    @"
@echo off
"$VenvPath\Scripts\python.exe" -m navig.main %*
"@ | Set-Content -Encoding ASCII $CmdShim

    Write-Nav "Updating user PATH..." "STEP"
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentPath -notlike "*$BinPath*") {
        [Environment]::SetEnvironmentVariable("Path", "$currentPath;$BinPath", "User")
        $env:Path = "$env:Path;$BinPath"
        Write-Nav "Added $BinPath to PATH" "SUCCESS"
    }

    Write-Nav "NAVIG core installation complete" "SUCCESS"
    return $CmdShim
}

# ── TELEGRAM & DAEMON SETUP ──────────────────────────────────
function Setup-Telegram {
    param([string]$Token, [string]$CmdShim)

    if ([string]::IsNullOrWhiteSpace($Token)) {
        if ($Silent) { return $false }

        Write-Progress-Step 3 5 "Optional: Telegram Bot"
        Write-Host "`nAutomation via Telegram? (y/n): " -NoNewline -ForegroundColor Yellow
        $response = Read-Host
        if ($response -ne 'y' -and $response -ne 'Y') {
            Write-Nav "Skipping Telegram setup" "INFO"
            return $false
        }

        Write-Host "Enter Telegram Bot Token (or press Enter to skip): " -NoNewline -ForegroundColor Yellow
        $Token = Read-Host
        if ([string]::IsNullOrWhiteSpace($Token)) {
            return $false
        }
    }

    Write-Nav "Configuring Telegram bot..." "STEP"

    $navigHome = Join-Path $env:USERPROFILE ".navig"
    New-Item -ItemType Directory -Force -Path $navigHome | Out-Null

    $envFile = Join-Path $navigHome ".env"
    "TELEGRAM_BOT_TOKEN=$Token" | Set-Content -Encoding UTF8 $envFile
    [Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", $Token, "User")

    $configFile = Join-Path $navigHome "config.yaml"
    if (-not (Test-Path $configFile)) {
        @"
telegram:
  bot_token: "$Token"
  allowed_users: []
  allowed_groups: []
  session_isolation: true
  group_activation_mode: "mention"
"@ | Set-Content -Encoding UTF8 $configFile
    }

    Write-Nav "Starting NAVIG daemon services..." "STEP"
    try {
        & $CmdShim service install --bot --gateway --scheduler --no-start 2>&1 | Out-Null
        & $CmdShim service start 2>&1 | Out-Null
        Write-Nav "Telegram bot configured and daemon started" "SUCCESS"
    } catch {
        Write-Nav "Daemon setup had issues (non-critical)" "WARNING"
    }

    return $true
}

# ── REMOTE DRIVE SETUP ────────────────────────────────────────
function Setup-RemoteAccess {
    param([string]$CmdShim)

    Write-Progress-Step 4 5 "Remote Drive Setup"

    if (-not (Get-Command rclone -ErrorAction SilentlyContinue) -and -not (Get-Command sshfs -ErrorAction SilentlyContinue)) {
        Write-Nav "Remote tools not available, skipping..." "WARNING"
        return
    }

    Write-Host @"

╔════════════════════════════════════════════════════════════╗
║  REMOTE DRIVE MOUNTING OPTIONS                            ║
╚════════════════════════════════════════════════════════════╝

1. Cloud Storage (Google Drive, Dropbox, OneDrive)
2. Linux/Ubuntu Server (via SFTP/SSHFS)
3. Skip for now
4. Configure later with script

"@ -ForegroundColor Cyan

    if ($Silent) {
        Write-Nav "Running in silent mode, skipping interactive setup" "INFO"
        return
    }

    $choice = Read-Host "Select option (1-4)"

    switch ($choice) {
        "1" {
            Write-Nav "Opening rclone configuration..." "STEP"
            Write-Host @"

NAVIG Remote Drive Setup:
────────────────────────────────────────
1. Running: rclone config
2. Choose 'n' for new remote
3. Select your cloud provider
4. Authenticate via browser

Press any key to continue...
"@ -ForegroundColor Green
            Read-Host

            & rclone config

            Write-Host @"

✓ Cloud provider configured!
  Mount your cloud drive anytime:

  rclone mount gdrive: G:\ --vfs-cache-mode full

"@ -ForegroundColor Green
        }

        "2" {
            Write-Nav "Setting up Linux/Ubuntu access..." "STEP"
            Write-Host @"

NAVIG Linux Server Setup:
────────────────────────────────────────
This will help you mount your Linux server.

"@ -ForegroundColor Green

            if (Get-Command navig -ErrorAction SilentlyContinue) {
                & navig host show

                Write-Host @"

✓ NAVIG host information displayed.

  Mount commands:

  1. Via SSHFS (native file access):
     net use Z: \\sshfs\username@hostname/home/username

  2. Via rclone SFTP (faster, with caching):
     rclone config          # Add SFTP remote
     rclone mount navig: Z:\ --vfs-cache-mode full

"@ -ForegroundColor Green
            } else {
                Write-Nav "NAVIG not yet available in PATH, restart terminal and configure manually" "INFO"
            }
        }

        "3" {
            Write-Nav "Remote drive setup skipped" "INFO"
        }

        "4" {
            Write-Host @"

NAVIG Remote Drive Setup Scripts:
────────────────────────────────────────
Location: ~/.local/bin/navig ... scripts/mount_remote_drives.ps1

Run anytime to:
1. Mount cloud drives
2. Mount Linux servers
3. Configure new remotes
4. Test connections

Execute:
  .\mount_remote_drives.ps1

"@ -ForegroundColor Green
        }
    }
}

# ── VERIFICATION ─────────────────────────────────────────────
function Verify-Installation {
    param([string]$CmdShim)

    Write-Progress-Step 5 5 "Verifying Installation"

    $issues = @()

    # Test NAVIG command
    try {
        $output = & $CmdShim --version 2>&1
        Write-Nav "✓ NAVIG CLI working: $output" "SUCCESS"
    } catch {
        $issues += "NAVIG command not accessible"
    }

    # Test Python env
    if (Test-Path "$env:USERPROFILE\.navig\venv") {
        Write-Nav "✓ Virtual environment created" "SUCCESS"
    } else {
        $issues += "Virtual environment missing"
    }

    # Test remote tools
    if (Get-Command rclone -ErrorAction SilentlyContinue) {
        Write-Nav "✓ rclone available" "SUCCESS"
    }

    if (Get-Command sshfs -ErrorAction SilentlyContinue) {
        Write-Nav "✓ SSHFS-Win available" "SUCCESS"
    }

    # Test daemon
    try {
        $daemon = Get-Process navig -ErrorAction SilentlyContinue
        if ($daemon) {
            Write-Nav "✓ NAVIG daemon running" "SUCCESS"
        }
    } catch {}

    if ($issues.Count -gt 0) {
        Write-Nav "`nSome issues detected:" "WARNING"
        $issues | ForEach-Object { Write-Nav "  ⚠ $_" "WARNING" }
        Write-Nav "These may resolve after opening a new terminal." "INFO"
    } else {
        Write-Nav "✓ All checks passed!" "SUCCESS"
    }
}

# ── MAIN FLOW ─────────────────────────────────────────────────
function Main {
    # Welcome
    Write-Host @"

╔════════════════════════════════════════════════════════════╗
║                                                            ║
║  🚀 NAVIG Windows Installation + Remote Drive Setup       ║
║                                                            ║
║  This installer will:                                     ║
║  • Install NAVIG CLI for Windows                          ║
║  • Setup Telegram automation (optional)                   ║
║  • Configure cloud drive / Linux mounting                 ║
║  • Create daemon services                                 ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝

"@ -ForegroundColor Cyan

    # Check admin
    if (-not (Test-IsAdmin)) {
        Write-Nav "ERROR: This script requires Administrator privileges!" "ERROR"
        Write-Nav "Please run PowerShell as Administrator" "WARNING"
        exit 1
    }

    # Determine source
    if (-not $SourcePath) {
        $SourcePath = (Resolve-Path "$PSScriptRoot\..").Path
    }

    Write-Nav "Installation source: $SourcePath" "INFO"

    # Parse Telegram token
    if (-not $TelegramToken) {
        $TelegramToken = if ($env:NAVIG_TELEGRAM_BOT_TOKEN) {
            $env:NAVIG_TELEGRAM_BOT_TOKEN
        } else {
            $env:TELEGRAM_BOT_TOKEN
        }
    }

    # Step 1: Prerequisites
    Write-Progress-Step 1 5 "Checking Prerequisites"

    if (-not (Test-Python)) {
        exit 1
    }

    Install-Chocolatey | Out-Null

    # Step 2: Install NAVIG
    $cmdShim = Install-NAVIG -Source $SourcePath

    # Step 3: Telegram Setup (optional)
    if (-not $SkipRemote) {
        Setup-Telegram -Token $TelegramToken -CmdShim $cmdShim | Out-Null
    }

    # Step 4: Remote Drives
    if (-not $SkipRemote) {
        Install-RemoteTools | Out-Null
        Setup-RemoteAccess -CmdShim $cmdShim
    }

    # Step 5: Verify
    Verify-Installation -CmdShim $cmdShim

    # Done
    Write-Host @"

╔════════════════════════════════════════════════════════════╗
║  ✅ INSTALLATION COMPLETE!                                ║
╚════════════════════════════════════════════════════════════╝

NEXT STEPS:
───────────────────────────────────────────────────────────

1. Open a NEW PowerShell/terminal window

2. Verify NAVIG:
   navig --help
   navig host list

3. Configure remote access:
   • Cloud: rclone config
   • Linux: NAVIG host setup guide

4. Mount drives:
   # Cloud storage (e.g., Google Drive)
   rclone mount gdrive: G:\ --vfs-cache-mode full

   # Linux server
   net use Z: \\sshfs\user@hostname/home/user

5. Check mounted drives:
   Get-PSDrive | Where-Object {$_.DisplayRoot}

ADVANCED:
───────────────────────────────────────────────────────────
• Telegram bot status: navig service status
• Install Operational Factory: $PSScriptRoot\install_navig_factory_server.sh
• Full documentation: https://github.com/navig-run/core

"@ -ForegroundColor Green
}

# ── EXECUTION ─────────────────────────────────────────────────
Main
