$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$deployDir = Join-Path $projectRoot "deploy\synapse"
$composeFile = Join-Path $deployDir "docker-compose.yml"
$envFile = Join-Path $deployDir ".env"

if (-not (Test-Path $composeFile)) {
  throw "Missing $composeFile"
}
if (-not (Test-Path $envFile)) {
  throw "Missing $envFile. Run scripts/install_matrix_synapse_windows.ps1 first."
}

docker compose --env-file $envFile -f $composeFile up -d
Write-Host "Synapse started."
Write-Host "Check: http://127.0.0.1:8008/_matrix/client/versions"
