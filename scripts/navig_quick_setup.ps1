#!/usr/bin/env powershell

<#
.SYNOPSIS
    NAVIG Fast Setup for Windows + Remote Linux/Cloud
.DESCRIPTION
    One-command setup:
    - Installs NAVIG Windows
    - Connects to remote Linux/Cloud
    - Configures sharing automatically
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File navig_quick_setup.ps1
    powershell -ExecutionPolicy Bypass -File navig_quick_setup.ps1 -Fast
#>

param(
    [switch]$Fast
)

$ErrorActionPreference = "Stop"

# Colors
$Colors = @{
    Success = "Green"
    Error = "Red"
    Warning = "Yellow"
    Info = "Cyan"
    Accent = "Magenta"
}

# Fast setup defaults
$FastDefaults = @{
    InstallRclone = $true
    InstallSSHFS = $true
    SkipTelegram = $true
    AutoConfigure = $true
}

function Write-Banner {
    Clear-Host
    Write-Host @"

╔════════════════════════════════════════════════════════════╗
║                                                            ║
║  ⚡ NAVIG FAST SETUP                                      ║
║     Windows + Remote Linux/Cloud                          ║
║                                                            ║
║  • Install NAVIG (2 min)                                  ║
║  • Mount cloud drives (Google Drive, Dropbox, etc)        ║
║  • Connect to Ubuntu/Linux servers                        ║
║  • Share files bidirectionally                            ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝

"@ -ForegroundColor $Colors.Accent
}

function Write-Step {
    param([int]$Number, [string]$Title)
    Write-Host "`n├─ STEP $Number" -ForegroundColor $Colors.Accent -NoNewline
    Write-Host " : " -NoNewline
    Write-Host $Title -ForegroundColor $Colors.Info
    Write-Host "│"
}

function Test-Admin {
    $isAdmin = ([Security.Principal.WindowsIdentity]::GetCurrent().Groups -match "S-1-5-32-544") -ne $null
    return $isAdmin
}

function Get-NAVIG-Installer {
    # Find the enhanced installer in the workspace
    $possiblePaths = @(
        ".\navig-core\scripts\install_navig_windows_enhanced.ps1",
        "..\navig-core\scripts\install_navig_windows_enhanced.ps1",
        "$PSScriptRoot\navig-core\scripts\install_navig_windows_enhanced.ps1",
        "$PSScriptRoot\..\navig-core\scripts\install_navig_windows_enhanced.ps1"
    )

    foreach ($path in $possiblePaths) {
        if (Test-Path $path) {
            return (Resolve-Path $path).Path
        }
    }

    return $null
}

function Quick-Install {
    if (-not (Test-Admin)) {
        Write-Host "ERROR: Administrator privileges required!" -ForegroundColor Red
        Write-Host "Please run PowerShell as Administrator" -ForegroundColor Red
        exit 1
    }

    Write-Banner

    Write-Host "⏱️  FAST MODE: Automated setup with sensible defaults" -ForegroundColor $Colors.Accent
    Write-Host "   • Pre-installs tools (rclone, SSHFS)"
    Write-Host "   • Skips optional prompts"
    Write-Host "   • ~5 minute setup"
    Write-Host ""

    # Find installer
    $installer = Get-NAVIG-Installer
    if (-not $installer) {
        Write-Host "ERROR: Enhanced installer not found!" -ForegroundColor Red
        Write-Host "Expected: install_navig_windows_enhanced.ps1" -ForegroundColor Red
        exit 1
    }

    Write-Host "Running: $installer`n" -ForegroundColor $Colors.Info

    # Run with fast mode flags
    & $installer -SkipRemote:$false -Silent:$Fast
}

function Interactive-Setup {
    Write-Banner

    Write-Host "Press any key to start interactive setup..." -ForegroundColor $Colors.Warning
    Read-Host

    $installer = Get-NAVIG-Installer
    if (-not $installer) {
        Write-Host "ERROR: Enhanced installer not found!" -ForegroundColor Red
        exit 1
    }

    & $installer
}

# Main decision
if ($Fast) {
    Quick-Install
} else {
    Write-Host "NAVIG Fast Setup" -ForegroundColor $Colors.Accent
    Write-Host "1. Automated setup (5 min)" -NoNewline -ForegroundColor $Colors.Info
    Write-Host " - recommended" -ForegroundColor Green
    Write-Host "2. Interactive setup" -ForegroundColor $Colors.Info
    Write-Host "0. Exit"

    $choice = Read-Host "`nChoose"

    switch ($choice) {
        "1" { Quick-Install }
        "2" { Interactive-Setup }
        default { exit 0 }
    }
}
