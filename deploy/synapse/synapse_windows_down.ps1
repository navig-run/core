$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$deployDir = Join-Path $projectRoot "deploy\synapse"
$composeFile = Join-Path $deployDir "docker-compose.yml"
$envFile = Join-Path $deployDir ".env"

if (-not (Test-Path $composeFile)) {
  throw "Missing $composeFile"
}
if (-not (Test-Path $envFile)) {
  throw "Missing $envFile."
}

docker compose --env-file $envFile -f $composeFile down
Write-Host "Synapse stopped."
