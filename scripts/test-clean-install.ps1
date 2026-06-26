<#
.SYNOPSIS
  Validate the NAVIG one-line installer on a CLEAN, Python-free Windows VM.

.DESCRIPTION
  Copy the `navig-core` folder (or at least install.ps1 + this script) to a fresh
  Windows VM with NO Python installed, then run:

      pwsh navig-core\scripts\test-clean-install.ps1

  It runs the LOCAL install.ps1 and asserts the isolated-runtime contract:
    * the uv-managed venv was built under ~/.navig/runtime,
    * `navig` resolves via the ~/.local/bin shim,
    * NO system Python was added to PATH (runtime python/venv stay off PATH),
    * the daemon auto-start task was registered,
    * `-Action uninstall` removes runtime, shim, PATH entry, and the task.

  Prints PASS/FAIL per check and exits non-zero if any check fails. This installs
  NAVIG on the machine it runs on — use a disposable VM/sandbox.

  NOTE: the live https://navig.run/install.ps1 is synced from navig-core main and
  may lag local changes; this script deliberately tests the LOCAL copy.

.PARAMETER InstallScript
  Path to install.ps1 (default: the sibling ..\install.ps1).

.PARAMETER KeepInstalled
  Skip the uninstall phase so you can inspect the install manually.
#>
param(
    [string] $InstallScript = (Join-Path (Split-Path -Parent $PSScriptRoot) "install.ps1"),
    [switch] $KeepInstalled
)

$ErrorActionPreference = "Stop"

$script:Pass = 0
$script:Fail = 0
function Check {
    param([string]$Name, [scriptblock]$Test, [string]$Detail = "")
    $ok = $false
    try { $ok = [bool](& $Test) } catch { $ok = $false; $Detail = "$Detail $($_.Exception.Message)".Trim() }
    if ($ok) { $script:Pass++; Write-Host ("  [PASS] {0}" -f $Name) -ForegroundColor Green }
    else     { $script:Fail++; Write-Host ("  [FAIL] {0}{1}" -f $Name, $(if ($Detail) { " — $Detail" } else { "" })) -ForegroundColor Red }
}
function Section { param([string]$t) Write-Host "`n== $t ==" -ForegroundColor Cyan }

# ── Paths under test ──────────────────────────────────────────
$RuntimeDir = Join-Path $env:USERPROFILE ".navig\runtime"
$VenvNavig  = Join-Path $RuntimeDir "venv\Scripts\navig.exe"
$Shim       = Join-Path $env:USERPROFILE ".local\bin\navig.cmd"
$TaskName   = "NAVIG Daemon"

function Get-UserPath { [Environment]::GetEnvironmentVariable("PATH", "User") }

Section "Preconditions"
Check "install.ps1 exists" { Test-Path $InstallScript } $InstallScript
$sysPyBefore = (Get-Command python -ErrorAction SilentlyContinue)
Write-Host ("  (system python before: {0})" -f $(if ($sysPyBefore) { $sysPyBefore.Source } else { "none" }))

# ── Install ───────────────────────────────────────────────────
Section "Install (running local install.ps1 -NoConfirm)"
& pwsh -NoProfile -ExecutionPolicy Bypass -File $InstallScript -NoConfirm
$installExit = $LASTEXITCODE
Check "installer exited 0" { $installExit -eq 0 } "exit=$installExit"

# ── Runtime assertions ────────────────────────────────────────
Section "Isolated runtime"
Check "runtime venv navig.exe exists" { Test-Path $VenvNavig } $VenvNavig
Check "launcher shim exists"          { Test-Path $Shim } $Shim
Check "navig --version via venv" {
    $v = & $VenvNavig --version 2>&1 | Select-Object -First 1
    Write-Host "         version: $v"
    $LASTEXITCODE -eq 0 -and "$v" -match '\d'
}
Check "shim dir on user PATH" { (Get-UserPath) -match [regex]::Escape(".local\bin") }

Section "System Python untouched"
Check "runtime python NOT on user PATH" { (Get-UserPath) -notmatch 'runtime\\python' }
Check "runtime venv NOT on user PATH"   { (Get-UserPath) -notmatch 'runtime\\venv' }
Check "no system python introduced" {
    $after = Get-Command python -ErrorAction SilentlyContinue
    # Pass if still none, or unchanged from before, and not pointing inside our runtime.
    (-not $after) -or (($after.Source -eq $sysPyBefore.Source) -and ($after.Source -notmatch '\.navig'))
}

Section "Daemon supervision"
Check "scheduled task '$TaskName' registered" {
    schtasks /query /tn $TaskName 2>$null | Out-Null
    $LASTEXITCODE -eq 0
} "set NAVIG_NO_DAEMON to intentionally skip"

# ── Uninstall ─────────────────────────────────────────────────
if (-not $KeepInstalled) {
    Section "Uninstall (-Action uninstall -NoConfirm)"
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $InstallScript -Action uninstall -NoConfirm
    $uninstExit = $LASTEXITCODE
    Check "uninstaller exited 0"      { $uninstExit -eq 0 } "exit=$uninstExit"
    Check "runtime dir removed"       { -not (Test-Path $RuntimeDir) }
    Check "shim removed"              { -not (Test-Path $Shim) }
    Check "shim dir off user PATH"    { (Get-UserPath) -notmatch [regex]::Escape(".local\bin") }
    Check "scheduled task removed" {
        schtasks /query /tn $TaskName 2>$null | Out-Null
        $LASTEXITCODE -ne 0
    }
} else {
    Write-Host "`n  (KeepInstalled set — skipping uninstall)"
}

# ── Summary ───────────────────────────────────────────────────
Section "Summary"
Write-Host ("  PASS: {0}   FAIL: {1}" -f $script:Pass, $script:Fail) -ForegroundColor $(if ($script:Fail -eq 0) { "Green" } else { "Red" })
exit $(if ($script:Fail -eq 0) { 0 } else { 1 })
