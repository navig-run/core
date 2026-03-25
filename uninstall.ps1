<#
.SYNOPSIS
    NAVIG Windows Uninstaller — mirrors install.ps1 in reverse.
.DESCRIPTION
    Removes all NAVIG artifacts: standalone binary, pip package,
    config/vault (~\.navig\), Telegram .env file, and User PATH entries.
    Idempotent — safe to run multiple times.
.PARAMETER EnvFile
    Path to the Telegram .env file to remove. Default: .\.env
.PARAMETER NoConfirm
    Skip all prompts including the vault deletion gate.
.EXAMPLE
    .\uninstall.ps1
    .\uninstall.ps1 -NoConfirm
    .\uninstall.ps1 -EnvFile "C:\projects\myapp\.env"
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$EnvFile   = ".\.env",
    [switch]$NoConfirm
)

Set-StrictMode -Version Latest

# ── Counters ──────────────────────────────────────────────────────────────────
$script:ok   = 0
$script:skip = 0
$script:fail = 0

# ── Log helper — prints colored status and increments the right counter ───────
function Log {
    param([string]$Tag, [string]$Msg)
    $color = switch ($Tag) {
        'OK'    { 'Green'    }
        'SKIP'  { 'DarkGray' }
        'FAIL'  { 'Red'      }
        'ABORT' { 'Red'      }
        default { 'White'    }
    }
    Write-Host "[$Tag] $Msg" -ForegroundColor $color
    switch ($Tag) {
        'OK'    { $script:ok++   }
        'SKIP'  { $script:skip++ }
        'FAIL'  { $script:fail++ }
        'ABORT' { exit 1         }
    }
}

Write-Host "`nNAVIG Uninstaller" -ForegroundColor Cyan
Write-Host "─────────────────────────────────────────" -ForegroundColor DarkGray

# Directories that install.ps1 / install_navig_windows.ps1 may add to PATH
$BinDirs = @(
    "$env:LOCALAPPDATA\navig",      # standard pip install places navig.exe here
    "$env:USERPROFILE\.local\bin"   # dev shim installer places navig.cmd here
)

# ── Step 1: Remove NAVIG entries from User PATH ───────────────────────────────
Write-Host "`n[1/5] Cleaning User PATH..." -ForegroundColor DarkCyan
try {
    $rawPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts   = $rawPath -split ';' | Where-Object { $_ -ne '' }
    $removed = @()
    $kept    = @()
    foreach ($p in $parts) {
        # Case-insensitive comparison, ignoring trailing backslashes
        if ($BinDirs | Where-Object { $_.TrimEnd('\') -ieq $p.TrimEnd('\') }) {
            $removed += $p
        } else {
            $kept += $p
        }
    }
    if ($removed.Count -gt 0) {
        [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')
        foreach ($r in $removed) { Log 'OK' "Removed PATH entry: $r" }
    } else {
        Log 'SKIP' 'No NAVIG PATH entries found'
    }
} catch {
    Log 'FAIL' "PATH cleanup — $($_.Exception.Message)"
}

# ── Step 2: Remove binary files ───────────────────────────────────────────────
Write-Host "`n[2/5] Removing binaries..." -ForegroundColor DarkCyan
$Binaries = @(
    "$env:LOCALAPPDATA\navig\navig.exe",    # pip-installed binary
    "$env:USERPROFILE\.local\bin\navig.cmd" # dev installer .cmd shim
)
foreach ($bin in $Binaries) {
    try {
        if (Test-Path $bin) { Remove-Item -Path $bin -Force; Log 'OK'   "Removed: $bin" }
        else                {                                Log 'SKIP' "Not found: $bin" }
    } catch {
        Log 'FAIL' "Remove $bin — $($_.Exception.Message)"
    }
}
# Remove the install directory itself if it is now empty
$installDir = "$env:LOCALAPPDATA\navig"
try {
    if ((Test-Path $installDir) -and (@(Get-ChildItem $installDir -Force).Count -eq 0)) {
        Remove-Item -Path $installDir -Force
        Log 'OK' "Removed empty directory: $installDir"
    }
} catch {
    Log 'FAIL' "Remove install dir — $($_.Exception.Message)"
}

# ── Step 3: pip package removal ───────────────────────────────────────────────
Write-Host "`n[3/5] Uninstalling pip package..." -ForegroundColor DarkCyan
$pipCmd = $null
foreach ($c in @('pip', 'pip3')) {
    if (Get-Command $c -ErrorAction SilentlyContinue) { $pipCmd = $c; break }
}
if (-not $pipCmd) {
    Log 'SKIP' 'pip not found in PATH — package may still be installed'
} else {
    try {
        $null = & $pipCmd show navig 2>&1
        if ($LASTEXITCODE -ne 0) {
            Log 'SKIP' 'navig pip package not installed'
        } else {
            & $pipCmd uninstall navig -y 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { Log 'OK'   'pip package navig removed'         }
            else                     { Log 'FAIL' "pip uninstall exited $LASTEXITCODE" }
        }
    } catch {
        Log 'FAIL' "pip uninstall — $($_.Exception.Message)"
    }
}

# ── Step 4: Remove config / vault / cache (~\.navig\) — GATED ────────────────
Write-Host "`n[4/5] Removing config, vault, and cache..." -ForegroundColor DarkCyan
$navigDir = "$env:USERPROFILE\.navig"
if (-not (Test-Path $navigDir)) {
    Log 'SKIP' "Directory not found: $navigDir"
} else {
    $proceed = [bool]$NoConfirm
    if (-not $proceed) {
        Write-Host "`n  WARNING: This will permanently delete ALL NAVIG data:" -ForegroundColor Yellow
        Write-Host "           $navigDir" -ForegroundColor Yellow
        Write-Host "           (config, vault, SSH keys, logs, cache, credentials)`n" -ForegroundColor Yellow
        $ans = Read-Host "  Delete ALL NAVIG data? (y/N)"
        $proceed = ($ans.Trim() -ceq 'y')
    }
    if ($proceed) {
        try {
            Remove-Item -Path $navigDir -Recurse -Force
            Log 'OK' "Deleted: $navigDir"
        } catch {
            Log 'FAIL' "Delete $navigDir — $($_.Exception.Message)"
        }
    } else {
        Log 'SKIP' 'Vault deletion declined by user'
    }
}

# ── Step 5: Remove .env file ──────────────────────────────────────────────────
Write-Host "`n[5/5] Removing .env file..." -ForegroundColor DarkCyan
try {
    $envPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($EnvFile)
    if (Test-Path $envPath) { Remove-Item -Path $envPath -Force; Log 'OK'   "Removed: $envPath" }
    else                    {                                     Log 'SKIP' "Not found: $envPath" }
} catch {
    Log 'FAIL' ".env removal — $($_.Exception.Message)"
}

# ── Developer sync (set $env:NAVIG_DEV_SYNC=1 to activate) ───────────────
# Copies uninstall scripts to the sibling navig-www project.
# NOT triggered when piped from curl/irm.
if ($env:NAVIG_DEV_SYNC -eq "1") {
    $wwwDir = Join-Path $PSScriptRoot "..\navig-www"
    if (-not (Test-Path $wwwDir)) {
        Log 'FAIL' "Developer sync — navig-www not found at: $wwwDir"
    } else {
        foreach ($f in @("uninstall.ps1", "uninstall.sh")) {
            $src = Join-Path $PSScriptRoot $f
            $dst = Join-Path $wwwDir $f
            try {
                if (Test-Path $src) {
                    Copy-Item -Path $src -Destination $dst -Force
                    Log 'OK' "Synced: $f -> $dst"
                } else {
                    Log 'SKIP' "Not found: $src"
                }
            } catch {
                Log 'FAIL' "Developer sync for $f — $($_.Exception.Message)"
            }
        }
    }
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host "`n─────────────────────────────────────────" -ForegroundColor DarkGray
$exitColor = if ($script:fail -gt 0) { 'Yellow' } else { 'Green' }
$exitMsg   = if ($script:fail -gt 0) { 'Completed with errors' } else { 'Completed successfully' }
Write-Host "$exitMsg — OK: $($script:ok)  SKIP: $($script:skip)  FAIL: $($script:fail)`n" -ForegroundColor $exitColor

exit $(if ($script:fail -gt 0) { 1 } else { 0 })
