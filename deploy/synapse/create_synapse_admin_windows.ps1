param(
  [string]$Username = "navigadmin",
  [string]$Password = "",
  [switch]$Admin
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Password)) {
  throw "Provide -Password for the Matrix user."
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$deployDir = Join-Path $projectRoot "deploy\synapse"
$composeFile = Join-Path $deployDir "docker-compose.yml"
$envFile = Join-Path $deployDir ".env"

if (-not (Test-Path $composeFile)) { throw "Missing $composeFile" }
if (-not (Test-Path $envFile)) { throw "Missing $envFile. Run install_matrix_synapse_windows.ps1 first." }

$adminFlag = if ($Admin) { "-a" } else { "" }

$server = (Get-Content $envFile | Where-Object { $_ -match '^SYNAPSE_SERVER_NAME=' } | Select-Object -First 1)
$serverName = if ($server) { ($server -split '=',2)[1].Trim() } else { "navig.local" }

$cmd = "register_new_matrix_user -u `"$Username`" -p `"$Password`" $adminFlag -c /data/homeserver.yaml http://localhost:8008"

docker compose --env-file $envFile -f $composeFile exec -T synapse sh -lc $cmd

Write-Host "Created Matrix user: @${Username}:$serverName"
Write-Host "Now authenticate NAVIG with: navig matrix auth login --homeserver http://127.0.0.1:8008 --user-id @${Username}:$serverName"
