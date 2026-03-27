#Requires -Version 5.1
# ─────────────────────────────────────────────────────────────
# NAVIG Installer - Windows (PowerShell 5.1+)
# No Admin Visible In Graveyard · Keep your servers alive. Forever.
#
# Usage:
#   iwr -useb https://navig.run/install.ps1 | iex
#   & ([scriptblock]::Create((irm https://navig.run/install.ps1)))
#   .\install.ps1 -Version 2.4.14
#   .\install.ps1 -Action Uninstall
#
# Environment variables:
#   NAVIG_VERSION             Pin version (e.g. "2.4.14")
#   NAVIG_INSTALL_PROFILE     Install profile: node, operator, architect (default: operator)
#   NAVIG_ACTION              install (default), uninstall, reinstall
# ─────────────────────────────────────────────────────────────

# ── Parameter Parsing (friendly for `irm | iex`) ─────────────
$Version        = $env:NAVIG_VERSION
$InstallProfile = if ([string]::IsNullOrEmpty($env:NAVIG_INSTALL_PROFILE)) { "operator" } else { $env:NAVIG_INSTALL_PROFILE }
$Action         = $env:NAVIG_ACTION
$DryRun         = $args -contains "-DryRun"    -or $args -contains "/DryRun"
$NoConfirm      = $args -contains "-NoConfirm" -or $args -contains "/NoConfirm"
$Help           = $args -contains "-Help"      -or $args -contains "/Help"
$Verbose        = $args -contains "-Verbose"   -or $args -contains "/Verbose"

for ($i = 0; $i -lt $args.Length - 1; $i++) {
    switch ($args[$i]) {
        { $_ -in "-Version","-v" }        { $Version        = $args[$i+1] }
        { $_ -in "-Action","-a" }         { $Action         = $args[$i+1] }
        { $_ -in "-InstallProfile","-p" } { $InstallProfile = $args[$i+1] }
    }
}

# ── PowerShell version guard ───────────────────────────────────────────────
if ($PSVersionTable.PSVersion.Major -lt 5 -or
    ($PSVersionTable.PSVersion.Major -eq 5 -and $PSVersionTable.PSVersion.Minor -lt 1)) {
    Write-Error "PowerShell 5.1 or higher is required. Download: https://aka.ms/PSWindows"
    exit 1
}

# ── Encoding ───────────────────────────────────────────────────────────────
$OutputEncoding           = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"

# ── Constants ──────────────────────────────────────────────────────────────
$MIN_PYTHON_MAJOR     = 3
$MIN_PYTHON_MINOR     = 10
$INSTALL_REGISTRY_KEY = "Registry::HKEY_CURRENT_USER\Software\NAVIG\Installer"
$INSTALL_MARKER_PATH  = Join-Path $env:USERPROFILE ".navig\install.marker"
$UNINSTALL_LOG        = Join-Path $env:TEMP "navig-uninstall.log"
$WINDOWS_SERVICE_NAME = "NavigDaemon"
$WINDOWS_TASK_NAME    = "NAVIG Daemon"

# ── Script-level terminal capability flags (set by Initialize-NavTerminal) ─
$script:NavColor   = $true
$script:NavUnicode = $true

# ── Terminal capability detection ──────────────────────────────────────────
function Initialize-NavTerminal {
    $noColor = ($null -ne $env:NO_COLOR) -or ($env:TERM -eq "dumb") -or (-not [Environment]::UserInteractive)
    $script:NavColor   = -not $noColor
    $script:NavUnicode = $true
    try {
        if ($PSVersionTable.PSVersion.Major -lt 6 -and
            [Console]::OutputEncoding.CodePage -notin @(65001, 1200)) {
            $script:NavUnicode = $false
        }
    } catch {}
}

function Get-NavSym {
    param([string]$Name)
    if ($script:NavUnicode) {
        switch ($Name) {
            "ok"   { return "\u2713" }  # ✓
            "step" { return "\u203a" }  # ›
            "err"  { return "\u00d7" }  # ×
            "warn" { return "!" }
        }
    } else {
        switch ($Name) {
            "ok"   { return "OK" }
            "step" { return " >" }
            "err"  { return "!!" }
            "warn" { return " !" }
        }
    }
    return "?"
}

# ── Output helpers ─────────────────────────────────────────────────────────
function Write-NavPhase {
    param([string]$msg)
    Write-Host ""
    if ($script:NavColor) { Write-Host "  $msg" -ForegroundColor Cyan } else { Write-Host "  $msg" }
}

function Write-NavOk {
    param([string]$msg)
    $sym = Get-NavSym "ok"
    if ($script:NavColor) { Write-Host "  " -NoNewline; Write-Host $sym -NoNewline -ForegroundColor Green;  Write-Host "  $msg" }
    else                  { Write-Host "  $sym  $msg" }
}

function Write-NavStep {
    param([string]$msg)
    $sym = Get-NavSym "step"
    if ($script:NavColor) { Write-Host "  " -NoNewline; Write-Host $sym -NoNewline -ForegroundColor Cyan;   Write-Host "  $msg" }
    else                  { Write-Host "  $sym  $msg" }
}

function Write-NavErr {
    param([string]$msg)
    $sym = Get-NavSym "err"
    if ($script:NavColor) { Write-Host "  " -NoNewline; Write-Host $sym -NoNewline -ForegroundColor Red;    Write-Host "  $msg" }
    else                  { Write-Host "  $sym  $msg" }
}

function Write-NavWarn {
    param([string]$msg)
    $sym = Get-NavSym "warn"
    if ($script:NavColor) { Write-Host "  " -NoNewline; Write-Host $sym -NoNewline -ForegroundColor Yellow; Write-Host "  $msg" }
    else                  { Write-Host "  $sym  $msg" }
}

function Write-NavHint {
    param([string]$msg)
    Write-Host "      $msg"
}

function Write-NavVerbose {
    param([string]$msg)
    if ($Verbose) {
        if ($script:NavColor) { Write-Host "      $msg" -ForegroundColor DarkGray }
        else                  { Write-Host "      $msg" }
    }
}

# ── Banner ─────────────────────────────────────────────────────────────────
function Show-Banner {
    Write-Host ""
    if ($script:NavColor) {
        Write-Host "  NAVIG" -NoNewline -ForegroundColor Cyan
        Write-Host " — install"
        Write-Host "  quiet operator tooling for real systems" -ForegroundColor DarkGray
    } else {
        Write-Host "  NAVIG — install"
        Write-Host "  quiet operator tooling for real systems"
    }
    Write-Host ""
}

# ── Success screen ─────────────────────────────────────────────────────────
function Show-Success {
    param([string]$InstalledVersion)
    $verLabel = if ($InstalledVersion) { "NAVIG $InstalledVersion" } else { "NAVIG" }
    Write-Host ""
    if ($script:NavColor) { Write-Host "  $verLabel" -ForegroundColor Cyan } else { Write-Host "  $verLabel" }
    Write-Host ""
    Write-Host "  Ready."
    Write-Host ""
    Write-Host "  Run " -NoNewline
    if ($script:NavColor) { Write-Host "navig init" -NoNewline -ForegroundColor Yellow } else { Write-Host "navig init" -NoNewline }
    Write-Host " to complete first-time setup."
    Write-Host ""
    Write-Host "  Common commands:"
    $pad = "    "
    if ($script:NavColor) {
        Write-Host "${pad}navig            " -NoNewline; Write-Host "Interactive menu"   -ForegroundColor DarkGray
        Write-Host "${pad}navig host add   " -NoNewline; Write-Host "Add your first server" -ForegroundColor DarkGray
        Write-Host "${pad}navig help       " -NoNewline; Write-Host "All commands"        -ForegroundColor DarkGray
    } else {
        Write-Host "${pad}navig              Interactive menu"
        Write-Host "${pad}navig host add     Add your first server"
        Write-Host "${pad}navig help         All commands"
    }
    Write-Host ""
}

# ── Usage ──────────────────────────────────────────────────────────────────
function Show-Usage {
    Write-Host @"
NAVIG Installer for Windows

Usage:
    iwr -useb https://navig.run/install.ps1 | iex
    .\install.ps1 [OPTIONS]

Options:
  -Version <ver>        Install specific version (e.g. 2.4.14)
  -Action <mode>        install (default), uninstall, reinstall
  -InstallProfile <p>   node, operator, architect (default: operator)
  -NoConfirm            Skip interactive prompts
  -DryRun               Preview actions without executing
  -Help                 Show this help

Environment variables:
  NAVIG_VERSION             Pin version
  NAVIG_INSTALL_PROFILE     Install profile (default: operator)
  NAVIG_ACTION              install (default), uninstall, reinstall
"@
}

# ── Normalize action string ────────────────────────────────────────────────
function Normalize-NavigAction {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
    switch ($Value.Trim().ToLowerInvariant()) {
        "install"   { return "install" }
        "uninstall" { return "uninstall" }
        "reinstall" { return "reinstall" }
        "repair"    { return "reinstall" }
        default     { throw "Unsupported action '$Value'. Use: install, uninstall, reinstall." }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# ── Python detection ───────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
function Find-Python {
    # Check known install locations first (most specific/reliable on Windows).
    $knownPaths = @(
        (Join-Path $HOME "AppData\Local\Programs\Python\Python314-32\python.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python314\python.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python313-32\python.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python313\python.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python312\python.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python311\python.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python310\python.exe")
    )
    foreach ($p in $knownPaths) {
        if (-not (Test-Path $p)) { continue }
        try {
            $out = & $p --version 2>&1
            if ($out -match '(\d+)\.(\d+)') {
                $maj = [int]$Matches[1]; $min = [int]$Matches[2]
                if ($maj -gt $MIN_PYTHON_MAJOR -or ($maj -eq $MIN_PYTHON_MAJOR -and $min -ge $MIN_PYTHON_MINOR)) {
                    return $p
                }
            }
        } catch {}
    }

    # Fall back to PATH candidates.
    foreach ($cmd in @("python", "python3")) {
        try {
            $out = & $cmd --version 2>&1
            if ($out -match '(\d+)\.(\d+)') {
                $maj = [int]$Matches[1]; $min = [int]$Matches[2]
                if ($maj -gt $MIN_PYTHON_MAJOR -or ($maj -eq $MIN_PYTHON_MAJOR -and $min -ge $MIN_PYTHON_MINOR)) {
                    $resolved = (Get-Command $cmd -ErrorAction SilentlyContinue)
                    if ($resolved -and $resolved.Source) { return $resolved.Source }
                    return $cmd
                }
            }
        } catch {}
    }

    # py launcher (Windows Python Launcher).
    try {
        $out = & py -3 --version 2>&1
        if ($out -match '(\d+)\.(\d+)') {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if ($maj -gt $MIN_PYTHON_MAJOR -or ($maj -eq $MIN_PYTHON_MAJOR -and $min -ge $MIN_PYTHON_MINOR)) {
                return (& py -3 -c "import sys; print(sys.executable)" 2>&1).Trim()
            }
        }
    } catch {}

    return $null
}

# ─────────────────────────────────────────────────────────────────────────────
# ── Resolve the Scripts\ directory for a given python.exe ─────────────────
# ─────────────────────────────────────────────────────────────────────────────
function Get-PythonScriptsDir {
    param([string]$PythonExe)
    try {
        $dir = (& $PythonExe -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>&1).Trim()
        if ($dir -and (Test-Path $dir)) { return $dir }
    } catch {}
    # Fallback: Scripts\ lives next to python.exe
    $candidate = Join-Path (Split-Path $PythonExe -Parent) "Scripts"
    if (Test-Path $candidate) { return $candidate }
    return $null
}

# ─────────────────────────────────────────────────────────────────────────────
# ── PATH helper: add a dir to current session and User registry ────────────
# ─────────────────────────────────────────────────────────────────────────────
function Add-NavigBinToPath {
    param([string]$BinDir)
    if ([string]::IsNullOrWhiteSpace($BinDir) -or -not (Test-Path $BinDir)) { return }
    # Current session
    if ($env:PATH -notlike "*$BinDir*") {
        $env:PATH = "$BinDir;$env:PATH"
    }
    # Permanent User PATH
    try {
        $userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
        if ($userPath -notlike "*$BinDir*") {
            [System.Environment]::SetEnvironmentVariable("PATH", "$BinDir;$userPath", "User")
        }
    } catch {
        Write-NavVerbose "Could not write to User PATH registry: $($_.Exception.Message)"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# ── Install NAVIG via pip (only) ───────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
function Install-Navig {
    param(
        [string]$PythonExe,
        [string]$PinVersion
    )
    $installSpec = "navig"
    if ($PinVersion) { $installSpec = "navig==$PinVersion" }

    $pipArgs = @("-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                 "--upgrade", $installSpec)

    Write-NavStep "Installing $installSpec via pip..."

    $tmpErr = [System.IO.Path]::GetTempFileName()
    try {
        $proc = Start-Process -FilePath $PythonExe -ArgumentList $pipArgs `
                              -NoNewWindow -Wait -PassThru `
                              -RedirectStandardError $tmpErr
        $code = $proc.ExitCode
        if ($code -ne 0) {
            $errors = Get-Content $tmpErr -ErrorAction SilentlyContinue
            Write-NavErr "pip install failed (exit $code)"
            $errors | Where-Object { $_ -match '\S' } | Select-Object -Last 20 |
                ForEach-Object { Write-Host "       $_" -ForegroundColor DarkGray }
            Write-NavHint "Try manually:  $PythonExe -m pip install navig"
            Write-NavHint "Docs:          https://github.com/navig-run/core"
            exit 1
        }
    } finally {
        Remove-Item $tmpErr -Force -ErrorAction SilentlyContinue
    }

    Write-NavOk "$installSpec installed via pip"
}

# ─────────────────────────────────────────────────────────────────────────────
# ── Verify navig is callable after PATH update ─────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
function Test-NavigCommand {
    param([string]$ScriptsDir)

    # Reload PATH from registry so the current session sees the update we just made.
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")    + ";" + $env:PATH

    $navigGcm = Get-Command navig -ErrorAction SilentlyContinue
    if (-not $navigGcm -and $ScriptsDir) {
        $explicit = Join-Path $ScriptsDir "navig.exe"
        if (Test-Path $explicit) { $navigGcm = $explicit }
    }

    if ($navigGcm) {
        try {
            $verOut = & navig --version 2>&1 | Select-Object -First 1
            Write-NavOk "navig $($verOut.ToString().Trim())"
            return $true
        } catch {
            Write-NavErr "navig found but failed to execute: $($_.Exception.Message)"
        }
    }

    Write-NavErr "navig is not callable after installation"
    if ($ScriptsDir) {
        Write-NavVerbose "Scripts directory: $ScriptsDir"
        Write-NavHint "Open a new terminal and run:  navig --version"
        Write-NavHint "Or add manually:  `$env:PATH = '$ScriptsDir;' + `$env:PATH"
    }
    return $false
}

# ─────────────────────────────────────────────────────────────────────────────
# ── Config dir, version detection, install state ──────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
function Initialize-NavigConfig {
    $configDir = Join-Path $env:USERPROFILE ".navig"
    foreach ($sub in @("", "workspace", "logs", "cache")) {
        $path = if ($sub) { Join-Path $configDir $sub } else { $configDir }
        if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
    }
    Write-NavVerbose "Config directory: $configDir\"
}

function Get-NavigVersion {
    try {
        $ver = (navig --version 2>&1 | Select-Object -First 1).ToString()
        if ($ver -match '(\d+\.\d+\.\d+)') { return $Matches[1] }
    } catch {}
    foreach ($pip in @("pip3", "pip")) {
        if (-not (Get-Command $pip -ErrorAction SilentlyContinue)) { continue }
        try {
            $line = (& $pip show navig 2>$null) | Select-String 'Version:'
            if ($line) { return ($line -replace 'Version:\s*', '').Trim() }
        } catch {}
    }
    return ""
}

function Write-NavigInstallState {
    param([string]$InstalledVersion)
    try {
        if (-not (Test-Path $INSTALL_REGISTRY_KEY)) {
            New-Item -Path $INSTALL_REGISTRY_KEY -Force | Out-Null
        }
        Set-ItemProperty -Path $INSTALL_REGISTRY_KEY -Name "Version"     -Value $InstalledVersion -ErrorAction Stop
        Set-ItemProperty -Path $INSTALL_REGISTRY_KEY -Name "InstallDate" -Value (Get-Date -Format "yyyy-MM-dd") -ErrorAction Stop
        Set-ItemProperty -Path $INSTALL_REGISTRY_KEY -Name "Method"      -Value "pip" -ErrorAction Stop
        $markerDir = Split-Path $INSTALL_MARKER_PATH -Parent
        if (-not (Test-Path $markerDir)) { New-Item -ItemType Directory -Path $markerDir -Force | Out-Null }
        Set-Content -Path $INSTALL_MARKER_PATH -Value $InstalledVersion -Encoding UTF8
    } catch {}
}

function Get-NavigInstallState {
    $isInstalled = (Test-Path $INSTALL_MARKER_PATH) -or (Test-Path $INSTALL_REGISTRY_KEY)
    $meta = @{}
    if (Test-Path $INSTALL_REGISTRY_KEY) {
        try {
            $reg = Get-ItemProperty -Path $INSTALL_REGISTRY_KEY -ErrorAction SilentlyContinue
            if ($reg) { $meta = @{ Version = $reg.Version; Method = $reg.Method } }
        } catch {}
    }
    return @{ IsInstalled = $isInstalled; Metadata = $meta }
}

function Remove-NavigInstallState {
    if (Test-Path $INSTALL_REGISTRY_KEY) {
        Remove-Item -Path $INSTALL_REGISTRY_KEY -Recurse -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -Path $INSTALL_MARKER_PATH -Force -ErrorAction SilentlyContinue
}

# ─────────────────────────────────────────────────────────────────────────────
# ── Uninstall support ─────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
$script:UninstallFailures = @()

function Reset-NavigUninstallState { $script:UninstallFailures = @() }

function Add-UninstallFailure {
    param([string]$Step, [string]$Message)
    $script:UninstallFailures += @{ Step = $Step; Message = $Message }
    Write-NavVerbose "Warning in '$Step': $Message"
}

function Split-PathEntries {
    param([string]$PathStr)
    if ([string]::IsNullOrWhiteSpace($PathStr)) { return @() }
    return ($PathStr -split ';') | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
}

function Stop-NavigBackgroundArtifacts {
    try { Get-Process navig -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
    $service = Get-Service -Name $WINDOWS_SERVICE_NAME -ErrorAction SilentlyContinue
    if ($service -and $service.Status -eq 'Running') {
        try { Stop-Service -Name $WINDOWS_SERVICE_NAME -Force -ErrorAction SilentlyContinue } catch {}
    }
}

function Remove-NavigFiles {
    param([switch]$PreserveUserData)
    # pip uninstall
    foreach ($pip in @("pip3", "pip")) {
        if (-not (Get-Command $pip -ErrorAction SilentlyContinue)) { continue }
        try { & $pip uninstall navig -y 2>&1 | Out-Null; Write-NavVerbose "Removed pip package: navig"; break } catch {}
    }
    # .navig home dir
    $navigHome = Join-Path $env:USERPROFILE ".navig"
    if ($PreserveUserData) {
        Write-NavVerbose "Preserving user data at $navigHome"
    } elseif (Test-Path $navigHome) {
        try { Remove-Item -Path $navigHome -Recurse -Force -ErrorAction Stop; Write-NavVerbose "Removed: $navigHome" }
        catch { Add-UninstallFailure -Step "Remove $navigHome" -Message $_.Exception.Message }
    }
}

function Remove-NavigRegistryArtifacts {
    if (-not (Test-Path $INSTALL_REGISTRY_KEY) -and -not (Test-Path $INSTALL_MARKER_PATH)) {
        Write-NavVerbose "No installer registry state present"
        return
    }
    try { Remove-NavigInstallState; Write-NavVerbose "Removed installer registry state" }
    catch { Add-UninstallFailure -Step "Remove registry state" -Message $_.Exception.Message }
}

function Test-NavigScheduledTask {
    try { return ($null -ne (Get-ScheduledTask -TaskName $WINDOWS_TASK_NAME -ErrorAction SilentlyContinue)) }
    catch { return $false }
}

function Remove-NavigServiceArtifacts {
    $service = Get-Service -Name $WINDOWS_SERVICE_NAME -ErrorAction SilentlyContinue
    if ($service) {
        try { & sc.exe stop $WINDOWS_SERVICE_NAME 2>$null | Out-Null } catch {}
        try { & sc.exe delete $WINDOWS_SERVICE_NAME 2>$null | Out-Null; Write-NavVerbose "Removed service: $WINDOWS_SERVICE_NAME" }
        catch { Add-UninstallFailure -Step "Remove service" -Message $_.Exception.Message }
    } else { Write-NavVerbose "Service not present: $WINDOWS_SERVICE_NAME" }
    if (Test-NavigScheduledTask) {
        try { schtasks /delete /tn $WINDOWS_TASK_NAME /f 2>$null | Out-Null; Write-NavVerbose "Removed task: $WINDOWS_TASK_NAME" }
        catch { Add-UninstallFailure -Step "Remove scheduled task" -Message $_.Exception.Message }
    } else { Write-NavVerbose "Scheduled task not present: $WINDOWS_TASK_NAME" }
}

function Remove-NavigPathArtifacts {
    try {
        $rawPath = [Environment]::GetEnvironmentVariable("PATH", "User")
        $entries = @(Split-PathEntries $rawPath)
        $kept    = $entries | Where-Object { $_ -notmatch '(?i)navig' -and $_ -notmatch '(?i)\.local\\bin' }
        if ($kept.Count -lt $entries.Count) {
            [Environment]::SetEnvironmentVariable("PATH", ($kept -join ';'), "User")
            Write-NavVerbose "Removed NAVIG PATH entries from User PATH"
        } else { Write-NavVerbose "No NAVIG-specific PATH entries found" }
    } catch { Add-UninstallFailure -Step "Remove PATH entries" -Message $_.Exception.Message }
}

function Invoke-NavigUninstall {
    param([switch]$PreserveUserData, [switch]$ForReinstall)
    Reset-NavigUninstallState
    if (-not $ForReinstall) {
        Write-Host ""
        Write-NavStep "Uninstalling NAVIG"
    }
    Write-NavVerbose "Stopping background processes"
    Stop-NavigBackgroundArtifacts
    Write-NavStep "Removing files..."
    Remove-NavigFiles -PreserveUserData:$PreserveUserData
    Write-NavVerbose "Removing registry state"
    Remove-NavigRegistryArtifacts
    Write-NavVerbose "Removing services and tasks"
    Remove-NavigServiceArtifacts
    Write-NavStep "Cleaning PATH..."
    Remove-NavigPathArtifacts
    $ok = $script:UninstallFailures.Count -eq 0
    if (-not $ForReinstall) {
        if ($ok) { Write-NavOk "Done." }
        else { Write-NavWarn "Completed with $($script:UninstallFailures.Count) warning(s)" }
    }
    return @{ Success = $ok; Failures = $script:UninstallFailures }
}

# ─────────────────────────────────────────────────────────────────────────────
# ── Main ──────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
function Main {
    if ($Help) { Show-Usage; return }

    Initialize-NavTerminal
    Show-Banner

    # Normalise action
    $normalizedAction = ""
    try { $normalizedAction = Normalize-NavigAction $Action }
    catch { Write-NavErr $_.Exception.Message; exit 1 }

    $installState = Get-NavigInstallState

    # ── Explicit uninstall
    if ($normalizedAction -eq "uninstall") {
        Write-NavVerbose "NAVIG $(if (-not $installState.IsInstalled) { "not installed — " })removing artifacts"
        $result = Invoke-NavigUninstall
        exit $(if ($result.Success) { 0 } else { 1 })
    }

    # ── Already-installed interactive menu
    if ($installState.IsInstalled -and $normalizedAction -eq "") {
        $existingVer = $installState.Metadata.Version
        $verStr = if ($existingVer) { " $existingVer" } else { "" }
        Write-Host ""
        if ($script:NavColor) { Write-Host "  NAVIG$verStr is installed." -ForegroundColor Cyan }
        else                  { Write-Host "  NAVIG$verStr is installed." }
        Write-Host ""
        Write-Host "    1  Repair / Reinstall"
        Write-Host "    2  Uninstall"
        Write-Host "    3  Cancel"
        Write-Host ""
        $opt = Read-Host "  Select [1-3]"
        switch ($opt.Trim()) {
            "1" { $normalizedAction = "reinstall" }
            "2" { $normalizedAction = "uninstall" }
            default { Write-Host "  Cancelled."; return }
        }
    }

    # ── Post-menu uninstall
    if ($normalizedAction -eq "uninstall") {
        $result = Invoke-NavigUninstall
        exit $(if ($result.Success) { 0 } else { 1 })
    }

    # ── Reinstall: clean first
    if ($normalizedAction -eq "reinstall" -and $installState.IsInstalled) {
        Write-NavVerbose "Reinstall: cleaning existing installation"
        $cleanup = Invoke-NavigUninstall -PreserveUserData -ForReinstall
        if (-not $cleanup.Success) { Write-NavVerbose "Pre-reinstall cleanup had warnings (continuing)" }
        $installState = Get-NavigInstallState
    }

    # ── Environment
    Write-NavPhase "Environment"
    $osVer   = [System.Environment]::OSVersion.Version
    $archStr = if ([System.Environment]::Is64BitOperatingSystem) { "AMD64" } else { "x86" }
    Write-NavOk "Windows $($osVer.Major).$($osVer.Minor) · $archStr"

    # ── Requirements
    Write-NavPhase "Requirements"
    Write-NavStep "Checking Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+..."
    $pythonExe = Find-Python
    if (-not $pythonExe) {
        Write-NavErr "Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR or higher is required"
        Write-NavHint "Download:  https://www.python.org/downloads"
        Write-NavHint "Enable 'Add Python to PATH' in the installer."
        exit 1
    }
    $pyVer = (& $pythonExe --version 2>&1).ToString().Trim()
    Write-NavOk $pyVer

    # ── Install
    Write-NavPhase "Install"
    Install-Navig -PythonExe $pythonExe -PinVersion $Version
    $scriptsDir = Get-PythonScriptsDir -PythonExe $pythonExe
    if ($scriptsDir) {
        Add-NavigBinToPath -BinDir $scriptsDir
        Write-NavVerbose "Scripts directory: $scriptsDir"
    }
    Initialize-NavigConfig

    # ── Verify
    Write-NavPhase "Verify"
    $verified = Test-NavigCommand -ScriptsDir $scriptsDir
    if (-not $verified) {
        Write-NavErr "navig is not callable in this terminal"
        Write-NavHint "Open a new terminal and run:  navig --version"
        exit 1
    }

    # ── Done
    $installedVer = Get-NavigVersion
    try { Write-NavigInstallState -InstalledVersion $installedVer } catch {}
    Show-Success -InstalledVersion $installedVer
}

# ── Entry point ───────────────────────────────────────────────────────────
if ($env:NAVIG_INSTALL_PS1_NO_RUN -ne "1") {
    Main
}

# ── Developer sync (set $env:NAVIG_DEV_SYNC=1 to activate) ───────────────
if ($env:NAVIG_DEV_SYNC -eq "1") {
    $devRoot = if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) { $PWD.Path } else { $PSScriptRoot }
    $wwwDir = [System.IO.Path]::GetFullPath((Join-Path $devRoot "..\navig-www\public"))
    if (-not (Test-Path $wwwDir)) {
        Write-NavInfo "navig-www/public not found at: $wwwDir — skipping dev sync"
        Write-NavHint "Copy manually:  Copy-Item install.ps1 ..\navig-www\public\install.ps1"
    } else {
        foreach ($f in @("install.ps1", "install.sh")) {
            $src = Join-Path $devRoot $f
            $dst = Join-Path $wwwDir $f
            if (Test-Path $src) { Copy-Item -Path $src -Destination $dst -Force; Write-NavOk "Synced $f -> navig-www" }
        }
    }
}
