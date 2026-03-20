# bootstrap_windows.ps1 — Bootstrap Navig Mesh on Windows
# Usage: pwsh -File bootstrap_windows.ps1 [-MeshSecret <secret>] [-Formation <name>]
param(
    [string]$MeshSecret = "",
    [string]$Formation  = "default"
)
$ErrorActionPreference = "Stop"
$NavigHome = if ($env:NAVIG_HOME) { $env:NAVIG_HOME } else { "$env:USERPROFILE\.navig" }

Write-Host "=== Navig Mesh bootstrap (Windows) ===" -ForegroundColor Cyan

# 1. Install navig-core
Write-Host "[1/5] Checking navig-core..."
try {
    $null = Get-Command navig -ErrorAction Stop
    Write-Host "  navig already installed — skipping"
} catch {
    Write-Host "  Installing navig-core via pip..."
    py -3 -m pip install --user --quiet navig-core
}

# 2. Create ~/.navig structure
Write-Host "[2/5] Creating config directories..."
@("vault","workspace","daemon","wiki") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path "$NavigHome\$_" | Out-Null
}

# 3. Write mesh config
Write-Host "[3/5] Writing mesh config..."
$cfgPath = "$NavigHome\config.yaml"
if (-not (Test-Path $cfgPath)) {
    @"
mesh:
  enabled: true
  formation: "$Formation"
  multicast_group: "224.0.0.251"
  multicast_port: 5354
  heartbeat_interval_s: 5
  election_ttl_s: 15
  sync_interval_s: 10
  collective_enabled: false
"@ | Set-Content -Path $cfgPath -Encoding UTF8
    Write-Host "  Written: $cfgPath"
} else {
    Write-Host "  Config exists — skipping (edit $cfgPath to change mesh settings)"
}

# 4. Write mesh secret to vault
if ($MeshSecret -ne "") {
    Write-Host "[4/5] Writing mesh secret to vault..."
    $secretPath = "$NavigHome\vault\mesh_secret"
    [System.IO.File]::WriteAllText($secretPath, $MeshSecret, [System.Text.Encoding]::UTF8)
    Write-Host "  Vault: $secretPath"
} else {
    Write-Host "[4/5] No -MeshSecret provided — auto-generated key will be used"
}

# 5. Announce on LAN
Write-Host "[5/5] Announcing presence on LAN..."
$env:PYTHONIOENCODING = "utf-8"
py -3 -c @"
import sys
try:
    from navig.mesh.discovery import announce_once
    announce_once()
    print('  UDP HELLO sent on 224.0.0.251:5354')
except Exception as e:
    print(f'  Note: {e} (safe to ignore if daemon not running yet)')
"@

Write-Host ""
Write-Host "=== Bootstrap complete ===" -ForegroundColor Green
Write-Host "Start: navig service start"
Write-Host "Peers: navig mesh status"
