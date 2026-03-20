# suspend_node.ps1 — Gracefully yield leadership and stop mesh heartbeating.
param(
    [string]$GatewayUrl = ($env:NAVIG_GATEWAY_URL ?? "http://127.0.0.1:8090")
)
$ErrorActionPreference = "SilentlyContinue"

Write-Host "[mesh] Requesting graceful yield + suspend..."
try {
    $resp = Invoke-RestMethod -Uri "$GatewayUrl/mesh/suspend" `
        -Method POST `
        -ContentType "application/json" `
        -Body '{}' `
        -TimeoutSec 5
    Write-Host "[mesh] Node suspended — $($resp.status)"
} catch {
    Write-Host "[mesh] Suspend skipped: $($_.Exception.Message)"
}
