# scripts/navig_windows_remote_deploy.ps1
#
# Phase 5 & 6: Remote Navig deployment from Windows via SSH.
#
# Covers:
#   5a  Detect Ubuntu server on local network
#   5b  Set up SSH key auth (no-password)
#   5c  Trigger remote navig install/init/start
#   6a  Enable OpenSSH Server on a Windows laptop
#   6b  Deploy to the Windows laptop
#
# Usage:
#   # Deploy to Ubuntu server:
#   .\navig_windows_remote_deploy.ps1 -Target ubuntu -UbuntuIp 192.168.1.50
#
#   # Deploy to Windows laptop (also enables OpenSSH Server on it first):
#   .\navig_windows_remote_deploy.ps1 -Target windows -LaptopIp 192.168.1.80 -LaptopUser alice
#
#   # Full scan + Ubuntu deploy (auto-detect IP):
#   .\navig_windows_remote_deploy.ps1 -Target ubuntu -Scan -Subnet 192.168.1.0/24
#
# Requirements: OpenSSH client (built into Windows 10+), nmap (optional, for -Scan)
# ─────────────────────────────────────────────────────────────────────────────
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('ubuntu', 'windows')]
    [string] $Target,

    [string] $UbuntuIp   = '',
    [string] $UbuntuUser = 'navig',
    [string] $LaptopIp   = '',
    [string] $LaptopUser = $env:USERNAME,
    [string] $Subnet     = '192.168.1.0/24',
    [string] $NaviVersion = '',        # Pin version e.g. "2.4.14"; empty = latest
    [switch] $Scan,                    # Auto-detect host via nmap
    [switch] $SkipKeySetup,            # Skip SSH key generation/copy
    [switch] $DryRun                   # Show commands without executing
)

$ErrorActionPreference = 'Stop'
$LogFile = "$env:TEMP\navig_deploy_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

function Write-Step($msg) { Write-Host "`n── $msg ──" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  ⚠  $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "  ❌ $msg" -ForegroundColor Red; throw $msg }
function Invoke-Safe($cmd) {
    Add-Content $LogFile "CMD: $cmd"
    if ($DryRun) { Write-Host "  [DRY-RUN] $cmd" -ForegroundColor DarkGray; return }
    Invoke-Expression $cmd
}

Write-Host "════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  NAVIG — Windows Remote Deploy"          -ForegroundColor Magenta
Write-Host "  Target: $Target  |  Log: $LogFile"      -ForegroundColor Magenta
Write-Host "════════════════════════════════════════" -ForegroundColor Magenta

# ── 5a: Network scan ─────────────────────────────────────────────────────────
if ($Scan -and $Target -eq 'ubuntu') {
    Write-Step "Scanning network for hosts ($Subnet)"
    if (Get-Command nmap -ErrorAction SilentlyContinue) {
        $nmapOut = nmap -sn $Subnet --open 2>&1
        Write-Host $nmapOut
        Add-Content $LogFile $nmapOut
        Write-Warn "Review the scan output above and set -UbuntuIp to the correct host before proceeding."
    } else {
        Write-Warn "nmap not found — skipping scan. Install via: winget install nmap"
        Write-Warn "Performing a quick ping sweep instead (may be slow)..."
        $base = ($Subnet -replace '/\d+$', '') -replace '\.\d+$', '.'
        1..254 | ForEach-Object {
            $ip = "$base$_"
            if (Test-Connection -ComputerName $ip -Count 1 -Quiet -TimeoutSeconds 1) {
                Write-OK "Responsive: $ip"
            }
        }
    }
    if (-not $UbuntuIp) {
        $UbuntuIp = Read-Host "Enter the Ubuntu server IP"
    }
}

# ── Confirm connectivity ──────────────────────────────────────────────────────
function Test-Host($ip, $label) {
    Write-Step "Testing connectivity to $label ($ip)"
    if (Test-Connection -ComputerName $ip -Count 2 -Quiet) {
        Write-OK "$ip is reachable"
    } else {
        Write-Fail "$ip is not reachable. Check network/firewall."
    }
}

# ── 5b: SSH key setup ────────────────────────────────────────────────────────
function Setup-SshKey($remoteUser, $remoteIp) {
    Write-Step "Setting up SSH key auth → $remoteUser@$remoteIp"
    $keyPath = "$env:USERPROFILE\.ssh\id_ed25519"
    if (-not (Test-Path $keyPath)) {
        Write-Host "  Generating new ED25519 key..."
        Invoke-Safe "ssh-keygen -t ed25519 -C 'navig-deploy' -f '$keyPath' -N ''"
    } else {
        Write-OK "SSH key already exists: $keyPath"
    }

    $pubKey = Get-Content "$keyPath.pub" -Raw
    $fingerprint = (ssh-keygen -lf "$keyPath.pub" 2>&1)
    Write-OK "Public key fingerprint: $fingerprint"

    # Copy public key to remote host
    Write-Host "  Copying public key to $remoteUser@$remoteIp..."
    $copyCmd = "ssh -o StrictHostKeyChecking=accept-new $remoteUser@$remoteIp " +
               """mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '$pubKey' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"""
    Invoke-Safe $copyCmd

    # Verify passwordless login
    Write-Host "  Verifying passwordless SSH..."
    $testCmd = "ssh -o BatchMode=yes -o ConnectTimeout=5 $remoteUser@$remoteIp 'echo navig-ssh-ok'"
    $result = if ($DryRun) { "navig-ssh-ok" } else { Invoke-Expression $testCmd 2>&1 }
    if ($result -match 'navig-ssh-ok') {
        Write-OK "Passwordless SSH confirmed"
    } else {
        Write-Fail "SSH key auth not working. Output: $result"
    }

    return $fingerprint
}

# ── Remote navig install ──────────────────────────────────────────────────────
function Install-NaviRemote($remoteUser, $remoteIp, $os) {
    Write-Step "Installing Navig on $os host ($remoteUser@$remoteIp)"

    $pipFlag = if ($NaviVersion) { "navig==$NaviVersion" } else { "navig" }

    if ($os -eq 'ubuntu') {
        $installCmd = "pip install --quiet $pipFlag 2>&1 || pip3 install --quiet $pipFlag 2>&1"
        $initCmd    = "navig init --yes 2>/dev/null || navig init; navig start"
        $verifyCmd  = "navig --version"
        $fullCmd    = "$installCmd && $initCmd"
    } else {
        # Windows remote (PowerShell over SSH)
        $installCmd = "pip install $pipFlag"
        $initCmd    = "navig init"
        $fullCmd    = "$installCmd; $initCmd"
        $verifyCmd  = "navig --version"
    }

    Write-Host "  Running: $fullCmd"
    $deployLog = "$env:TEMP\navig_deploy_remote_$remoteIp.log"
    $sshCmd = "ssh -o BatchMode=yes $remoteUser@$remoteIp '$fullCmd' 2>&1 | Tee-Object -FilePath '$deployLog'"
    Invoke-Safe $sshCmd

    Write-Host "  Verifying on remote..."
    $verOut = if ($DryRun) { "navig v2.4.14" } else {
        ssh -o BatchMode=yes "$remoteUser@$remoteIp" $verifyCmd 2>&1
    }
    Write-OK "Remote navig version: $verOut"
    Write-OK "Deploy log: $deployLog"
}

# ── 6a: Enable OpenSSH Server on a Windows laptop ────────────────────────────
function Enable-SshdOnLaptop($laptopUser, $laptopIp) {
    Write-Step "Enabling OpenSSH Server on Windows laptop ($laptopIp)"
    Write-Warn "This requires admin access on the laptop. You will be prompted for credentials."
    $cmds = @(
        "Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0",
        "Start-Service sshd",
        "Set-Service -Name sshd -StartupType Automatic",
        "New-NetFirewallRule -Name 'sshd' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -ErrorAction SilentlyContinue"
    )
    foreach ($cmd in $cmds) {
        $sshCmd = "ssh -t $laptopUser@$laptopIp 'powershell -Command `"$cmd`"'"
        Invoke-Safe $sshCmd
    }
    Start-Sleep -Seconds 3
    Write-OK "sshd should now be running on $laptopIp"
}

# ══ Main flow ══════════════════════════════════════════════════════════════════

$sshFingerprint = ''

if ($Target -eq 'ubuntu') {
    if (-not $UbuntuIp) { $UbuntuIp = Read-Host "Enter Ubuntu server IP" }
    Test-Host $UbuntuIp 'Ubuntu server'

    if (-not $SkipKeySetup) {
        $sshFingerprint = Setup-SshKey $UbuntuUser $UbuntuIp
    }
    Install-NaviRemote $UbuntuUser $UbuntuIp 'ubuntu'

    # Verify service running
    Write-Step "Checking navig status on Ubuntu"
    $statusOut = if ($DryRun) { "daemon: running" } else {
        ssh -o BatchMode=yes "$UbuntuUser@$UbuntuIp" "navig status 2>/dev/null || systemctl status navig --no-pager -l 2>&1 | head -20" 2>&1
    }
    Write-Host $statusOut

} elseif ($Target -eq 'windows') {
    if (-not $LaptopIp)   { $LaptopIp   = Read-Host "Enter Windows laptop IP" }
    if (-not $LaptopUser) { $LaptopUser = Read-Host "Enter Windows laptop username" }

    Test-Host $LaptopIp 'Windows laptop'

    # Try connecting first; if it fails, enable sshd remotely (requires existing conn or admin share)
    Write-Step "Testing existing SSH access to laptop"
    $testOut = try {
        if ($DryRun) { "navig-ssh-ok" }
        else { ssh -o BatchMode=yes -o ConnectTimeout=5 "$LaptopUser@$LaptopIp" "echo navig-ssh-ok" 2>&1 }
    } catch { "" }

    if ($testOut -notmatch 'navig-ssh-ok') {
        Write-Warn "SSH not yet available on laptop. Attempting to enable OpenSSH Server..."
        Write-Warn "Manual fallback: On the laptop, run as admin:"
        Write-Host  "  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0" -ForegroundColor Yellow
        Write-Host  "  Start-Service sshd" -ForegroundColor Yellow
        Read-Host   "  Press Enter after sshd is running on the laptop"
    }

    if (-not $SkipKeySetup) {
        $sshFingerprint = Setup-SshKey $LaptopUser $LaptopIp
    }
    Install-NaviRemote $LaptopUser $LaptopIp 'windows'
}

# ── Final summary ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  Deployment Summary"                     -ForegroundColor Magenta
Write-Host "════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  Target type      : $Target"
if ($Target -eq 'ubuntu') {
    Write-Host "  Ubuntu user       : $UbuntuUser"
    Write-Host "  Ubuntu IP         : $UbuntuIp"
}
if ($Target -eq 'windows') {
    Write-Host "  Laptop user       : $LaptopUser"
    Write-Host "  Laptop IP         : $LaptopIp"
}
if ($sshFingerprint) {
    Write-Host "  SSH key fp        : $sshFingerprint"
}
if ($NaviVersion)    { Write-Host "  Navig version     : $NaviVersion" }
else                 { Write-Host "  Navig version     : latest" }
Write-Host "  Full log          : $LogFile"
