# suspend_node.ps1 — Gracefully yield leadership and stop mesh heartbeating.
param(
    # `??` is PowerShell 7+ only; on Windows PowerShell 5.1 it is a parse error, which took
    # the whole param block with it. The if/else is also more correct here: `??` falls back
    # only on $null, so an env var set to the EMPTY string produced an empty gateway URL.
    [string]$GatewayUrl = $(if ($env:NAVIG_GATEWAY_URL) { $env:NAVIG_GATEWAY_URL } else { "http://127.0.0.1:8090" })
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
