param(
  [string]$ServerName = "navig.local",
  [int]$Port = 8008,
  [ValidateSet("yes","no")][string]$ReportStats = "no",
  [switch]$CreateAdmin,
  [string]$AdminUser = "navigadmin",
  [string]$AdminPassword = "",
  [switch]$ForceRegenerateConfig
)

$ErrorActionPreference = "Stop"

function Ensure-Docker {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not installed. Install Docker Desktop first."
  }

  docker version | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is installed but not running. Start Docker Desktop and retry."
  }
}

function New-RandomSecret([int]$Length = 48) {
  $chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  $bytes = New-Object byte[] $Length
  $rng.GetBytes($bytes)
  $sb = New-Object System.Text.StringBuilder
  foreach ($b in $bytes) {
    [void]$sb.Append($chars[$b % $chars.Length])
  }
  $sb.ToString()
}

function Set-OrAppendYamlKey {
  param(
    [string]$Path,
    [string]$Key,
    [string]$Value
  )

  $content = Get-Content -Raw $Path
  $pattern = "(?m)^\s*" + [regex]::Escape($Key) + "\s*:.*$"
  if ($content -match $pattern) {
    $content = [regex]::Replace($content, $pattern, "${Key}: $Value")
  } else {
    if (-not $content.EndsWith("`n")) {
      $content += "`r`n"
    }
    $content += "${Key}: $Value`r`n"
  }
  Set-Content -Path $Path -Value $content -Encoding UTF8
}

Ensure-Docker

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$deployDir = Join-Path $projectRoot "deploy\synapse"
$dataDir = Join-Path $deployDir "data"
$envExample = Join-Path $deployDir ".env.example"
$envFile = Join-Path $deployDir ".env"
$composeFile = Join-Path $deployDir "docker-compose.yml"
$homeserver = Join-Path $dataDir "homeserver.yaml"

if (-not (Test-Path $deployDir)) { New-Item -ItemType Directory -Force -Path $deployDir | Out-Null }
if (-not (Test-Path $dataDir)) { New-Item -ItemType Directory -Force -Path $dataDir | Out-Null }
if (-not (Test-Path $composeFile)) { throw "Missing $composeFile" }
if (-not (Test-Path $envExample)) { throw "Missing $envExample" }

if (-not (Test-Path $envFile)) {
  Copy-Item $envExample $envFile
}

$envMap = @{}
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
  $parts = $_ -split '=', 2
  $envMap[$parts[0].Trim()] = $parts[1].Trim()
}

$envMap["SYNAPSE_SERVER_NAME"] = $ServerName
$envMap["SYNAPSE_PORT"] = "$Port"
$envMap["SYNAPSE_REPORT_STATS"] = $ReportStats
if (-not $envMap.ContainsKey("SYNAPSE_REGISTRATION_SHARED_SECRET") -or [string]::IsNullOrWhiteSpace($envMap["SYNAPSE_REGISTRATION_SHARED_SECRET"]) -or $envMap["SYNAPSE_REGISTRATION_SHARED_SECRET"] -eq "change-me") {
  $envMap["SYNAPSE_REGISTRATION_SHARED_SECRET"] = New-RandomSecret
}

$orderedKeys = @(
  "SYNAPSE_SERVER_NAME",
  "SYNAPSE_PORT",
  "SYNAPSE_REPORT_STATS",
  "SYNAPSE_REGISTRATION_SHARED_SECRET"
)
$envLines = foreach ($k in $orderedKeys) { "$k=$($envMap[$k])" }
Set-Content -Path $envFile -Value ($envLines -join "`r`n") -Encoding UTF8

if ($ForceRegenerateConfig -and (Test-Path $homeserver)) {
  Remove-Item $homeserver -Force
}

if (-not (Test-Path $homeserver)) {
  Write-Host "Generating Synapse config..."
  docker run --rm `
    -e "SYNAPSE_SERVER_NAME=$ServerName" `
    -e "SYNAPSE_REPORT_STATS=$ReportStats" `
    -v "${dataDir}:/data" `
    matrixdotorg/synapse:latest generate | Out-Host
}

if (-not (Test-Path $homeserver)) {
  throw "homeserver.yaml was not generated at $homeserver"
}

Set-OrAppendYamlKey -Path $homeserver -Key "enable_registration" -Value "false"
Set-OrAppendYamlKey -Path $homeserver -Key "registration_shared_secret" -Value "\"$($envMap["SYNAPSE_REGISTRATION_SHARED_SECRET"])\""

Write-Host "Starting Synapse via docker compose..."
docker compose --env-file $envFile -f $composeFile up -d | Out-Host

$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 2
  try {
    $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/_matrix/client/versions" -TimeoutSec 2
    if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) {
      $healthy = $true
      break
    }
  } catch {}
}

if (-not $healthy) {
  Write-Warning "Synapse did not report healthy within timeout. Check logs with: docker compose --env-file $envFile -f $composeFile logs -f"
} else {
  Write-Host "Synapse is up at http://127.0.0.1:$Port"
}

if ($CreateAdmin) {
  if ([string]::IsNullOrWhiteSpace($AdminPassword)) {
    throw "-CreateAdmin requires -AdminPassword"
  }
  Write-Host "Creating admin user '$AdminUser'..."
  $cmd = "register_new_matrix_user -u `"$AdminUser`" -p `"$AdminPassword`" -a -c /data/homeserver.yaml http://localhost:8008"
  docker compose --env-file $envFile -f $composeFile exec -T synapse sh -lc $cmd | Out-Host
}

Write-Host ""
Write-Host "Windows Synapse install complete."
Write-Host "Useful commands:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\synapse_windows_up.ps1"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\synapse_windows_down.ps1"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\create_synapse_admin_windows.ps1 -Username navigbot -Password 'ChangeMe'"
Write-Host ""
Write-Host "NAVIG matrix config baseline:"
Write-Host "  navig config set comms.matrix.enabled true"
Write-Host "  navig config set comms.matrix.homeserver_url http://127.0.0.1:$Port"
Write-Host "  navig config set comms.matrix.user_id @<user>:$ServerName"
Write-Host "  navig config set comms.matrix.default_room_id !<roomId>:$ServerName"
