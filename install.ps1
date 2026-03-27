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

# ── Output helpers ─────────────────────────────────────────────────────────
#   [->] in-progress   [OK] success   [!!] failure   [i] info
function Write-NavStep { param([string]$msg) Write-Host "  " -NoNewline; Write-Host "[->]" -NoNewline -ForegroundColor Cyan;     Write-Host "  $msg" }
function Write-NavOk   { param([string]$msg) Write-Host "  " -NoNewline; Write-Host "[OK]" -NoNewline -ForegroundColor Green;    Write-Host "  $msg" }
function Write-NavErr  { param([string]$msg) Write-Host "  " -NoNewline; Write-Host "[!!]" -NoNewline -ForegroundColor Red;      Write-Host "  $msg" }
function Write-NavInfo { param([string]$msg) Write-Host "  " -NoNewline; Write-Host "[i]"  -NoNewline -ForegroundColor DarkGray; Write-Host "  $msg" }
function Write-NavHint { param([string]$msg) Write-Host "      $msg" -ForegroundColor Yellow }

# ── Banner ─────────────────────────────────────────────────────────────────
function Show-Banner {
    $taglines = @(
        "Your servers are in good hands now.",
        "No admin visible in graveyard? Perfect.",
        "SSH tunnels, remote ops - all in one CLI.",
        "Because server management shouldn't feel like surgery.",
        "ctrl+c to exit. But why would you?",
        "Keeping uptime personal since 2024.",
        "One CLI to rule them all.",
        "Servers don't sleep, and neither does NAVIG.",
        "Remote ops, local comfort.",
        "Born in the terminal. Lives in the cloud.",
        "Your devops sidekick. No cape required.",
        "Deploy, manage, survive. Repeat.",
        "Less SSH, more SHH - it just works.",
        "The quiet guardian of your infrastructure.",
        "Admin by day, daemon by night."
    )
    $tagline = $taglines[(Get-Random -Maximum $taglines.Length)]
    Write-Host ""
    $vStr = if ($Version) { "v$Version " } else { "" }
    Write-Host "  NAVIG $vStr" -NoNewline -ForegroundColor Cyan
    Write-Host "- $tagline" -ForegroundColor DarkGray
    Write-Host ""
}

# ── Success banner ─────────────────────────────────────────────────────────
function Show-SuccessBanner {
    param([string]$InstalledVersion)
    $verLabel = if ($InstalledVersion) { "  NAVIG v$InstalledVersion installed  " } else { "  NAVIG installed  " }
    $inner    = 44
    Write-Host ""
    Write-Host "  +$("=" * $inner)+" -ForegroundColor Magenta
    Write-Host "  |" -NoNewline -ForegroundColor Magenta; Write-Host $verLabel.PadRight($inner) -NoNewline -ForegroundColor White; Write-Host "|" -ForegroundColor Magenta
    Write-Host "  |" -NoNewline -ForegroundColor Magenta; Write-Host "  Welcome aboard. Keep those servers alive.".PadRight($inner) -NoNewline -ForegroundColor White; Write-Host "|" -ForegroundColor Magenta
    Write-Host "  +$("=" * $inner)+" -ForegroundColor Magenta
    Write-Host ""
}

# ── Get-started block ──────────────────────────────────────────────────────
function Show-GetStarted {
    param([string]$PythonCmd)
    $configPath = Join-Path $env:USERPROFILE ".navig\"
    Write-Host "  Get started:" -ForegroundColor White
    Write-Host "    " -NoNewline; Write-Host "navig"         -NoNewline -ForegroundColor Yellow; Write-Host "                  Open interactive menu"
    Write-Host "    " -NoNewline; Write-Host "navig host add" -NoNewline -ForegroundColor Yellow; Write-Host "         Add your first server"
    Write-Host "    " -NoNewline; Write-Host "navig help"    -NoNewline -ForegroundColor Yellow; Write-Host "             Show all commands"
    Write-Host ""
    Write-Host "  " -NoNewline; Write-Host "Update: " -NoNewline -ForegroundColor White
    if ($PythonCmd) {
        Write-Host "$PythonCmd -m pip install --upgrade navig" -ForegroundColor Yellow
    } else {
        Write-Host "python.exe -m pip install --upgrade navig" -ForegroundColor Yellow
    }
    Write-Host "  " -NoNewline; Write-Host "Config: " -NoNewline -ForegroundColor White; Write-Host $configPath -ForegroundColor Yellow
    Write-Host "  " -NoNewline; Write-Host "Docs:   " -NoNewline -ForegroundColor White; Write-Host "https://github.com/navig-run/core" -ForegroundColor Yellow
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
        Write-NavInfo "Could not write to User PATH registry: $($_.Exception.Message)"
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
            Write-NavOk "navig $($verOut.ToString().Trim()) is ready"
            return $true
        } catch {
            Write-NavErr "navig found but failed to execute: $($_.Exception.Message)"
        }
    }

    Write-NavErr "navig is not callable after installation"
    if ($ScriptsDir) {
        Write-NavHint "Scripts directory: $ScriptsDir"
        Write-NavHint "Run in a new terminal:  navig --version"
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
    Write-NavOk "Config directory ready at $configDir\"
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
    Write-NavInfo "Warning in '$Step': $Message"
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
        try { & $pip uninstall navig -y 2>&1 | Out-Null; Write-NavOk "Removed pip package: navig"; break } catch {}
    }
    # .navig home dir
    $navigHome = Join-Path $env:USERPROFILE ".navig"
    if ($PreserveUserData) {
        Write-NavInfo "Preserving user data at $navigHome"
    } elseif (Test-Path $navigHome) {
        try { Remove-Item -Path $navigHome -Recurse -Force -ErrorAction Stop; Write-NavOk "Removed: $navigHome" }
        catch { Add-UninstallFailure -Step "Remove $navigHome" -Message $_.Exception.Message }
    }
}

function Remove-NavigRegistryArtifacts {
    if (-not (Test-Path $INSTALL_REGISTRY_KEY) -and -not (Test-Path $INSTALL_MARKER_PATH)) {
        Write-NavInfo "No installer registry state present"
        return
    }
    try { Remove-NavigInstallState; Write-NavOk "Removed installer registry state" }
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
        try { & sc.exe delete $WINDOWS_SERVICE_NAME 2>$null | Out-Null; Write-NavOk "Removed service: $WINDOWS_SERVICE_NAME" }
        catch { Add-UninstallFailure -Step "Remove service" -Message $_.Exception.Message }
    } else { Write-NavInfo "Service not present: $WINDOWS_SERVICE_NAME" }
    if (Test-NavigScheduledTask) {
        try { schtasks /delete /tn $WINDOWS_TASK_NAME /f 2>$null | Out-Null; Write-NavOk "Removed task: $WINDOWS_TASK_NAME" }
        catch { Add-UninstallFailure -Step "Remove scheduled task" -Message $_.Exception.Message }
    } else { Write-NavInfo "Scheduled task not present: $WINDOWS_TASK_NAME" }
}

function Remove-NavigPathArtifacts {
    try {
        $rawPath = [Environment]::GetEnvironmentVariable("PATH", "User")
        $entries = @(Split-PathEntries $rawPath)
        $kept    = $entries | Where-Object { $_ -notmatch '(?i)navig' -and $_ -notmatch '(?i)\.local\\bin' }
        if ($kept.Count -lt $entries.Count) {
            [Environment]::SetEnvironmentVariable("PATH", ($kept -join ';'), "User")
            Write-NavOk "Removed NAVIG PATH entries from User PATH"
        } else { Write-NavInfo "No NAVIG-specific PATH entries found" }
    } catch { Add-UninstallFailure -Step "Remove PATH entries" -Message $_.Exception.Message }
}

function Invoke-NavigUninstall {
    param([switch]$PreserveUserData, [switch]$ForReinstall)
    Reset-NavigUninstallState
    Write-Host ""
    Write-NavInfo (if ($ForReinstall) { "Preparing reinstall cleanup..." } else { "Starting NAVIG uninstall..." })
    Write-NavStep "Stopping NAVIG processes...";    Stop-NavigBackgroundArtifacts
    Write-NavStep "Removing NAVIG files...";        Remove-NavigFiles -PreserveUserData:$PreserveUserData
    Write-NavStep "Removing registry entries...";   Remove-NavigRegistryArtifacts
    Write-NavStep "Removing services and tasks..."; Remove-NavigServiceArtifacts
    Write-NavStep "Removing PATH entries...";       Remove-NavigPathArtifacts
    $ok = $script:UninstallFailures.Count -eq 0
    if (-not $ForReinstall) {
        if ($ok) { Write-NavOk "NAVIG uninstalled successfully" }
        else { Write-NavErr "Uninstall completed with $($script:UninstallFailures.Count) warning(s)" }
    }
    return @{ Success = $ok; Failures = $script:UninstallFailures }
}

# ─────────────────────────────────────────────────────────────────────────────
# ── Main ──────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
function Main {
    if ($Help) { Show-Usage; return }

    $osVer = [System.Environment]::OSVersion.Version
    Show-Banner
    Write-NavOk "Windows $($osVer.Major).$($osVer.Minor) detected"

    # Normalise action
    $normalizedAction = ""
    try { $normalizedAction = Normalize-NavigAction $Action }
    catch { Write-NavErr $_.Exception.Message; exit 1 }

    $installState = Get-NavigInstallState

    # ── Uninstall
    if ($normalizedAction -eq "uninstall") {
        if (-not $installState.IsInstalled) {
            Write-NavInfo "NAVIG is not installed — removing any leftover artifacts"
        }
        $result = Invoke-NavigUninstall
        exit $(if ($result.Success) { 0 } else { 1 })
    }

    # ── Reinstall: clean first
    if ($normalizedAction -eq "reinstall" -and $installState.IsInstalled) {
        Write-NavInfo "Existing NAVIG detected — performing reinstall"
        $cleanup = Invoke-NavigUninstall -PreserveUserData -ForReinstall
        if (-not $cleanup.Success) { Write-NavInfo "Pre-reinstall cleanup had warnings (continuing)" }
        $installState = Get-NavigInstallState
    }

    if ($installState.IsInstalled) {
        $existingVer = $installState.Metadata.Version
        Write-NavInfo "Existing NAVIG detected$(if ($existingVer) { ": v$existingVer" }) — upgrading"
    }

    # ── Step 1: Find Python 3.10+
    Write-NavStep "Checking Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+..."
    $pythonExe = Find-Python
    if (-not $pythonExe) {
        Write-NavErr "Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR or higher is required"
        Write-NavHint "Download:        https://www.python.org/downloads"
        Write-NavHint "Enable 'Add Python to PATH' in the installer."
        exit 1
    }
    $pyVer = (& $pythonExe --version 2>&1).ToString().Trim()
    Write-NavOk "$pyVer at $pythonExe"

    # ── Step 2: pip install navig
    Install-Navig -PythonExe $pythonExe -PinVersion $Version

    # ── Step 3: Resolve Scripts\ and fix PATH
    Write-NavStep "Updating PATH..."
    $scriptsDir = Get-PythonScriptsDir -PythonExe $pythonExe
    if ($scriptsDir) {
        Add-NavigBinToPath -BinDir $scriptsDir
        Write-NavOk "Scripts directory on PATH: $scriptsDir"
    } else {
        Write-NavInfo "Could not resolve Scripts directory automatically"
    }

    # ── Step 4: Config directory
    Initialize-NavigConfig

    # ── Step 5: Verify navig is callable
    Write-NavStep "Verifying navig command..."
    $verified = Test-NavigCommand -ScriptsDir $scriptsDir
    if (-not $verified) {
        Write-NavErr "Installation completed but navig is not callable in this terminal"
        Write-NavHint "Open a new terminal and run:  navig --version"
        exit 1
    }

    # ── Step 6: Persist install state + success output
    $installedVer = Get-NavigVersion
    try { Write-NavigInstallState -InstalledVersion $installedVer } catch {}

    Show-SuccessBanner -InstalledVersion $installedVer
    Show-GetStarted    -PythonCmd $pythonExe

    Write-Host "  Run " -NoNewline -ForegroundColor White
    Write-Host "navig init" -NoNewline -ForegroundColor Yellow
    Write-Host " to complete first-time setup.  Profile: " -NoNewline -ForegroundColor White
    Write-Host $InstallProfile -ForegroundColor Cyan
    Write-Host ""
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
