# scripts/_lib/installer_common.ps1
# Shared functions for all NAVIG PowerShell installers.
# Dot-source this file in installer scripts:
#   . "$PSScriptRoot\_lib\installer_common.ps1"

<#
.SYNOPSIS
  Shared PowerShell helpers for NAVIG installers.
#>

# ── Telegram configuration ────────────────────────────────────────────────────

function Invoke-NavigTelegramSetup {
    <#
    .SYNOPSIS
      Write Telegram bot token to ~/.navig/.env and bootstrap config.yaml.
    .PARAMETER Token
      Telegram bot token string. Function is a no-op when empty/null.
    .PARAMETER CmdShim
      Full path to the navig.cmd shim (used to start the daemon).
    #>
    param(
        [string]$Token = "",
        [string]$CmdShim = ""
    )

    if ([string]::IsNullOrWhiteSpace($Token)) { return $false }

    $navigHome = Join-Path $env:USERPROFILE ".navig"
    New-Item -ItemType Directory -Force -Path $navigHome | Out-Null

    # Write .env
    $envFile = Join-Path $navigHome ".env"
    "TELEGRAM_BOT_TOKEN=$Token" | Set-Content -Encoding UTF8 $envFile
    [Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", $Token, "User")
    $env:TELEGRAM_BOT_TOKEN = $Token

    # Write config.yaml if token block is missing
    $configFile = Join-Path $navigHome "config.yaml"
    if (-not (Test-Path $configFile) -or -not (Select-String -Path $configFile -Pattern "bot_token:" -Quiet)) {
        @"
telegram:
  bot_token: "$Token"
  allowed_users: []
  allowed_groups: []
  session_isolation: true
  group_activation_mode: "mention"
"@ | Set-Content -Encoding UTF8 $configFile
    }

    return $true
}

function Start-NavigDaemon {
    <#
    .SYNOPSIS
      Install and start NAVIG daemon services (bot, gateway, scheduler).
    .PARAMETER CmdShim
      Full path to the navig.cmd shim.
    #>
    param([string]$CmdShim)

    if (-not $CmdShim -or -not (Test-Path $CmdShim)) { return }

    try {
        & $CmdShim service install --bot --gateway --scheduler --no-start 2>&1 | Out-Null
        & $CmdShim service start 2>&1 | Out-Null
    } catch {
        Write-Warning "Daemon setup had issues (non-critical): $_"
    }
}

# ── Venv / shim helpers ───────────────────────────────────────────────────────

function New-NavigVenv {
    <#
    .SYNOPSIS
      Create a Python venv, upgrade pip, and install an editable package.
    .PARAMETER VenvPath   Path where the venv is created.
    .PARAMETER SourcePath Path to the navig-core source tree.
    #>
    param(
        [string]$VenvPath,
        [string]$SourcePath
    )

    & python -m venv $VenvPath
    & "$VenvPath\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel 2>&1 |
        Select-String -Pattern "Successfully|Requirement" | Write-Host
    & "$VenvPath\Scripts\python.exe" -m pip install -e "$SourcePath" 2>&1 |
        Select-String -Pattern "Successfully|Installing|error" | Write-Host
}

function New-NavigShim {
    <#
    .SYNOPSIS
      Write navig.cmd launcher shim to BinPath and add BinPath to user PATH.
    #>
    param(
        [string]$VenvPath,
        [string]$BinPath
    )

    New-Item -ItemType Directory -Force -Path $BinPath | Out-Null
    $cmdShim = Join-Path $BinPath "navig.cmd"

    @"
@echo off
"$VenvPath\Scripts\python.exe" -m navig.main %*
"@ | Set-Content -Encoding ASCII $cmdShim

    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentPath -notlike "*$BinPath*") {
        [Environment]::SetEnvironmentVariable("Path", "$currentPath;$BinPath", "User")
        $env:Path = "$env:Path;$BinPath"
        Write-Host "Added $BinPath to user PATH (open a new terminal to use 'navig')."
    }

    return $cmdShim
}
