#Requires -Version 5.1
# ─────────────────────────────────────────────────────────────
# NAVIG Installer - Windows (PowerShell 5.1+)
# No Admin Visible In Graveyard · Keep your servers alive. Forever.
#
# Usage:
#   & ([scriptblock]::Create((irm https://navig.run/install.ps1)))
#   & ([scriptblock]::Create((irm https://navig.run/install.ps1))) -Action Uninstall
#   .\install.ps1 -Version <release>
#   .\install.ps1 -Dev
#
# Environment variables:
#   NAVIG_VERSION             Pin version (e.g. "2.4.14")
#   NAVIG_INSTALL_METHOD      "pip" (default) or "git"
#   NAVIG_EXTRAS              Comma-separated extras (e.g. "voice,keyring")
#   NAVIG_INSTALL_PROFILE     Install profile: node, operator, architect (default: operator)
#   NAVIG_ACTION              install (default), uninstall, or reinstall
# ─────────────────────────────────────────────────────────────
# ── Parameter Parsing (friendly for `irm | iex`) ─────────────
$Version = $env:NAVIG_VERSION
$InstallMethod = if ([string]::IsNullOrEmpty($env:NAVIG_INSTALL_METHOD)) { "pip" } else { $env:NAVIG_INSTALL_METHOD }
$Extras = $env:NAVIG_EXTRAS
$InstallProfile = if ([string]::IsNullOrEmpty($env:NAVIG_INSTALL_PROFILE)) { "operator" } else { $env:NAVIG_INSTALL_PROFILE }
$Action = $env:NAVIG_ACTION
$GitDir = "$HOME\navig-core"
$VersionProvided = -not [string]::IsNullOrEmpty($env:NAVIG_VERSION)
$InstallMethodProvided = -not [string]::IsNullOrEmpty($env:NAVIG_INSTALL_METHOD)
$ExtrasProvided = -not [string]::IsNullOrEmpty($env:NAVIG_EXTRAS)
$InstallProfileProvided = -not [string]::IsNullOrEmpty($env:NAVIG_INSTALL_PROFILE)
$GitDirProvided = $false
$Dev = $args -contains "-Dev" -or $args -contains "/Dev"
$Production = $args -contains "-Production" -or $args -contains "/Production"
$DryRun = $args -contains "-DryRun" -or $args -contains "/DryRun"
$NoConfirm = $args -contains "-NoConfirm" -or $args -contains "/NoConfirm"
$Help = $args -contains "-Help" -or $args -contains "/Help"

# Parse values with associated arguments (e.g. -Version 2.4.14)
for ($i = 0; $i -lt $args.Length - 1; $i++) {
    switch ($args[$i]) {
        "-Version" { $Version = $args[$i+1]; $VersionProvided = $true }
        "-InstallMethod" { $InstallMethod = $args[$i+1]; $InstallMethodProvided = $true }
        "-Extras" { $Extras = $args[$i+1]; $ExtrasProvided = $true }
        "-InstallProfile" { $InstallProfile = $args[$i+1]; $InstallProfileProvided = $true }
        "-Action" { $Action = $args[$i+1] }
        "-GitDir" { $GitDir = $args[$i+1]; $GitDirProvided = $true }
    }
}

if ($Dev) {
    $InstallMethodProvided = $true
}


if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Error "PowerShell 5.1 or higher is required. Download: https://aka.ms/PSWindows"
    exit 1
}

# ── Encoding - set before any Write-Host so Unicode renders correctly ──────
$OutputEncoding           = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"

# ── Constants ──────────────────────────────────────────────────────────────
$REPO_URL         = "https://github.com/navig-run/core.git"
$MIN_PYTHON_MAJOR = 3
$MIN_PYTHON_MINOR = 10
$INSTALL_REGISTRY_KEY = "Registry::HKEY_CURRENT_USER\Software\NAVIG\Installer"
$INSTALL_MARKER_PATH  = Join-Path $env:USERPROFILE ".navig\install.marker"
$UNINSTALL_LOG        = Join-Path $env:TEMP "navig-uninstall.log"
$WINDOWS_SERVICE_NAME = "NavigDaemon"
$WINDOWS_TASK_NAME    = "NAVIG Daemon"

# ── Output helpers ─────────────────────────────────────────────────────────
#   [->]  in-progress   [OK]  success   [!!]  failure   [i]  info
function Write-NavStep  { param([string]$msg) Write-Host "  " -NoNewline; Write-Host "[->]" -NoNewline -ForegroundColor Cyan;    Write-Host "  $msg" }
function Write-NavOk    { param([string]$msg) Write-Host "  " -NoNewline; Write-Host "[OK]" -NoNewline -ForegroundColor Green;   Write-Host "  $msg" }
function Write-NavErr   { param([string]$msg) Write-Host "  " -NoNewline; Write-Host "[!!]" -NoNewline -ForegroundColor Red;     Write-Host "  $msg" }
function Write-NavInfo  { param([string]$msg) Write-Host "  " -NoNewline; Write-Host "[i]"  -NoNewline -ForegroundColor DarkGray; Write-Host "  $msg" }
function Write-NavHint  { param([string]$msg) Write-Host "      $msg" -ForegroundColor Yellow }

function Normalize-NavigAction {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return "" }

    switch ($Value.Trim().ToLowerInvariant()) {
        "install"   { return "install" }
        "uninstall" { return "uninstall" }
        "reinstall" { return "reinstall" }
        "repair"    { return "reinstall" }
        default {
            throw "Unsupported action '$Value'. Use install, uninstall, or reinstall."
        }
    }
}

function Get-NavigShimCandidates {
    $candidates = @(
        $INSTALL_MARKER_PATH,
        (Join-Path $HOME ".local\bin\navig.cmd"),
        (Join-Path $HOME ".local\bin\navig.exe"),
        (Join-Path $env:LOCALAPPDATA "navig\navig.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python314-32\Scripts\navig.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python314\Scripts\navig.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python313-32\Scripts\navig.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python313\Scripts\navig.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python312\Scripts\navig.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python311\Scripts\navig.exe"),
        (Join-Path $HOME "AppData\Roaming\Python\Python313\Scripts\navig.exe"),
        (Join-Path $HOME "AppData\Roaming\Python\Python312\Scripts\navig.exe"),
        (Join-Path $HOME "AppData\Roaming\Python\Python311\Scripts\navig.exe"),
        (Join-Path "C:\" "Python313\Scripts\navig.exe")
    )

    return $candidates | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
}

function Get-NavigCommand {
    try {
        return Get-Command navig -ErrorAction SilentlyContinue | Select-Object -First 1
    } catch {
        return $null
    }
}

function Get-NavigInstallMetadata {
    if (-not (Test-Path $INSTALL_REGISTRY_KEY)) { return @{} }

    try {
        $props = Get-ItemProperty -Path $INSTALL_REGISTRY_KEY
        return @{
            InstallMethod    = [string]$props.InstallMethod
            GitDir           = [string]$props.GitDir
            InstallProfile   = [string]$props.InstallProfile
            MarkerPath       = [string]$props.MarkerPath
            InstalledVersion = [string]$props.InstalledVersion
            UpdatedAt        = [string]$props.UpdatedAt
        }
    } catch {
        return @{}
    }
}

function Test-NavigWindowsService {
    try {
        return $null -ne (Get-Service -Name $WINDOWS_SERVICE_NAME -ErrorAction SilentlyContinue)
    } catch {
        return $false
    }
}

function Test-NavigScheduledTask {
    try {
        schtasks /query /tn $WINDOWS_TASK_NAME 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Get-NavigInstallState {
    $metadata = Get-NavigInstallMetadata
    $command  = Get-NavigCommand

    $candidatePaths = @(Get-NavigShimCandidates)
    if ($metadata.ContainsKey("MarkerPath") -and -not [string]::IsNullOrWhiteSpace($metadata["MarkerPath"])) {
        $candidatePaths = @($metadata["MarkerPath"]) + $candidatePaths
    }

    $markerPath = $candidatePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    $registryPresent = Test-Path $INSTALL_REGISTRY_KEY
    $servicePresent  = Test-NavigWindowsService
    $taskPresent     = Test-NavigScheduledTask

    return [pscustomobject]@{
        IsInstalled    = $registryPresent -or ($null -ne $command) -or ($null -ne $markerPath) -or $servicePresent -or $taskPresent
        RegistryPresent = $registryPresent
        MarkerPath      = $markerPath
        CommandPath     = if ($command) { [string]$command.Source } else { "" }
        ServicePresent  = $servicePresent
        TaskPresent     = $taskPresent
        Metadata        = $metadata
    }
}

function Apply-NavigInstallDefaults {
    param([hashtable]$Metadata)

    if (-not $Metadata -or $Metadata.Count -eq 0) { return }

    if (
        -not $InstallMethodProvided -and
        $Metadata.ContainsKey("InstallMethod") -and
        -not [string]::IsNullOrWhiteSpace($Metadata["InstallMethod"])
    ) {
        $script:InstallMethod = $Metadata["InstallMethod"]
    }

    if (
        -not $GitDirProvided -and
        $Metadata.ContainsKey("GitDir") -and
        -not [string]::IsNullOrWhiteSpace($Metadata["GitDir"])
    ) {
        $script:GitDir = $Metadata["GitDir"]
    }

    if (
        -not $InstallProfileProvided -and
        $Metadata.ContainsKey("InstallProfile") -and
        -not [string]::IsNullOrWhiteSpace($Metadata["InstallProfile"])
    ) {
        $script:InstallProfile = $Metadata["InstallProfile"]
    }
}

function Write-NavigInstallState {
    param([string]$InstalledVersion)

    $markerDir = Split-Path -Path $INSTALL_MARKER_PATH -Parent
    if (-not (Test-Path $markerDir)) {
        New-Item -ItemType Directory -Path $markerDir -Force | Out-Null
    }

    $markerPayload = @{
        version    = $InstalledVersion
        method     = $InstallMethod
        git_dir    = $GitDir
        profile    = $InstallProfile
        updated_at = (Get-Date).ToString("o")
    } | ConvertTo-Json -Compress

    Set-Content -Path $INSTALL_MARKER_PATH -Value $markerPayload -Encoding UTF8

    if (-not (Test-Path $INSTALL_REGISTRY_KEY)) {
        New-Item -Path $INSTALL_REGISTRY_KEY -Force | Out-Null
    }

    foreach ($entry in @{
        Installed        = "1"
        InstallMethod    = $InstallMethod
        GitDir           = $GitDir
        InstallProfile   = $InstallProfile
        MarkerPath       = $INSTALL_MARKER_PATH
        InstalledVersion = $InstalledVersion
        UpdatedAt        = (Get-Date).ToString("o")
    }.GetEnumerator()) {
        New-ItemProperty -Path $INSTALL_REGISTRY_KEY -Name $entry.Key -Value $entry.Value -PropertyType String -Force | Out-Null
    }
}

function Remove-NavigInstallState {
    $metadata = Get-NavigInstallMetadata
    $markerPaths = @($INSTALL_MARKER_PATH)
    if ($metadata.ContainsKey("MarkerPath") -and -not [string]::IsNullOrWhiteSpace($metadata["MarkerPath"])) {
        $markerPaths += $metadata["MarkerPath"]
    }

    foreach ($path in ($markerPaths | Select-Object -Unique)) {
        if (Test-Path $path) {
            Remove-Item -Path $path -Force -ErrorAction Stop
        }
    }

    if (Test-Path $INSTALL_REGISTRY_KEY) {
        Remove-Item -Path $INSTALL_REGISTRY_KEY -Recurse -Force -ErrorAction Stop
    }
}

function Test-InteractiveSession {
    if (-not [Environment]::UserInteractive) { return $false }

    try {
        if ([Console]::IsInputRedirected) { return $false }
    } catch {}

    return $true
}

function Read-KeyWithTimeout {
    param([int]$TimeoutSeconds = 30)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            if ([Console]::KeyAvailable) {
                return [Console]::ReadKey($true)
            }
        } catch {
            return $null
        }

        Start-Sleep -Milliseconds 100
    }

    return $null
}

function Show-InstalledActionMenu {
    $deadline = (Get-Date).AddSeconds(30)
    $invalidSelections = 0

    Write-Host ""
    Write-Host "  Navig is already installed."
    Write-Host ""
    Write-Host "  [1] Uninstall"
    Write-Host "  [2] Reinstall / Repair"
    Write-Host "  [3] Cancel"
    Write-Host ""

    while ($true) {
        $secondsRemaining = [int][Math]::Ceiling(($deadline - (Get-Date)).TotalSeconds)
        if ($secondsRemaining -le 0) {
            Write-Host ""
            Write-NavInfo "No selection received within 30 seconds - canceling"
            return "cancel"
        }

        Write-Host "  Select an option: " -NoNewline
        $keyInfo = Read-KeyWithTimeout -TimeoutSeconds $secondsRemaining
        if ($null -eq $keyInfo) {
            Write-Host ""
            Write-NavInfo "No selection received within 30 seconds - canceling"
            return "cancel"
        }

        $selection = if ($keyInfo.KeyChar) { [string]$keyInfo.KeyChar } else { "" }
        if ([string]::IsNullOrWhiteSpace($selection)) { Write-Host "" } else { Write-Host $selection }

        switch ($selection) {
            "1" { return "uninstall" }
            "2" { return "reinstall" }
            "3" { return "cancel" }
            default {
                $invalidSelections++
                if ($invalidSelections -gt 1) {
                    Write-NavInfo "Invalid input entered twice - canceling"
                    return "cancel"
                }
                Write-NavErr "Invalid selection. Enter 1, 2, or 3."
            }
        }
    }
}

function Resolve-NavigExecutionAction {
    param(
        [bool]$IsInstalled,
        [string]$RequestedAction
    )

    if (-not [string]::IsNullOrWhiteSpace($RequestedAction)) {
        return $RequestedAction
    }

    if ($IsInstalled -and -not $NoConfirm -and (Test-InteractiveSession)) {
        return Show-InstalledActionMenu
    }

    return "install"
}

function Split-PathEntries {
    param([string]$RawPath)

    if ([string]::IsNullOrWhiteSpace($RawPath)) { return @() }
    return $RawPath -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
}

function Get-PipCommandSpec {
    $candidates = @(
        @{ Exe = "py";      Args = @("-3", "-m", "pip") },
        @{ Exe = "python";  Args = @("-m", "pip") },
        @{ Exe = "python3"; Args = @("-m", "pip") },
        @{ Exe = "pip";     Args = @() },
        @{ Exe = "pip3";    Args = @() }
    )

    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }

        try {
            & $candidate.Exe @($candidate.Args + @("--version")) 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return [pscustomobject]@{
                    Exe  = $candidate.Exe
                    Args = $candidate.Args
                }
            }
        } catch {}
    }

    return $null
}

# ── Live spinner ───────────────────────────────────────────────────────────
# Starts $Exe $ArgList as a child process, animates a spinner on the current
# line, then overwrites with a success/failure indicator. Returns exit code.
function Invoke-WithSpinner {
    param(
        [string]   $Label,
        [string]   $Exe,
        [string[]] $ArgList
    )

    $frames = @('|','/','-','\')   # ASCII spinner - works everywhere
    $pad    = "                                        "
    $tmpOut = [System.IO.Path]::GetTempFileName()
    $tmpErr = [System.IO.Path]::GetTempFileName()

    $errorLines = @()
    try {
        $proc = Start-Process -FilePath $Exe -ArgumentList $ArgList `
            -RedirectStandardOutput $tmpOut `
            -RedirectStandardError  $tmpErr `
            -NoNewWindow -PassThru

        $i = 0
        while (-not $proc.HasExited) {
            $f = $frames[$i % $frames.Length]
            Write-Host "`r  [ $f ]  $Label$pad" -NoNewline -ForegroundColor Cyan
            Start-Sleep -Milliseconds 100
            $i++
        }
        # WaitForExit() ensures the OS has fully flushed the exit code into the
        # Process object. On Windows, HasExited can flip true before ExitCode is
        # reliable — this call is a no-op if the process already exited.
        $proc.WaitForExit()
        $code = $proc.ExitCode
        # Capture lines for diagnostics. Prefer stderr; fall back to stdout
        # when stderr is empty (e.g. pipx writes its main output to stdout).
        if ($code -ne 0) {
            $errorLines = @(Get-Content $tmpErr -ErrorAction SilentlyContinue)
            if (-not $errorLines -or $errorLines.Count -eq 0) {
                $errorLines = @(Get-Content $tmpOut -ErrorAction SilentlyContinue)
            }
        }
    } catch {
        $code = 1
    } finally {
        # Always clean up temp files - even on CTRL+C or unhandled exception
        Remove-Item $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
    }

    if ($code -eq 0) {
        Write-Host "`r  " -NoNewline
        Write-Host "[OK]" -NoNewline -ForegroundColor Green
        Write-Host "  $Label$pad"
    } else {
        Write-Host "`r  " -NoNewline
        Write-Host "[!!]" -NoNewline -ForegroundColor Red
        Write-Host "  $Label$pad"
        if ($errorLines -and $errorLines.Count -gt 0) {
            $showLines = if ($VerbosePreference -ne 'SilentlyContinue') {
                $errorLines
            } else {
                $errorLines | Select-Object -Last 12
            }
            $showLines | Where-Object { $_ -match '\S' } | ForEach-Object {
                Write-Host "       $_" -ForegroundColor DarkGray
            }
        }
    }

    return $code
}

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
    if ($Version) { $vStr = "v$Version " } else { $vStr = "" }
    Write-Host "  NAVIG $vStr" -NoNewline -ForegroundColor Cyan
    Write-Host "- $tagline" -ForegroundColor DarkGray
    Write-Host ""
}

# ── Success banner ─────────────────────────────────────────────────────────
function Show-SuccessBanner {
    param([string]$InstalledVersion)

    $verLabel = if ($InstalledVersion) { "  NAVIG v$InstalledVersion installed" } else { "  NAVIG installed" }
    $inner    = 44

    Write-Host ""
    Write-Host "  +$("=" * $inner)+" -ForegroundColor Magenta
    Write-Host "  |" -NoNewline -ForegroundColor Magenta
    Write-Host $verLabel.PadRight($inner) -NoNewline -ForegroundColor White
    Write-Host "|" -ForegroundColor Magenta
    Write-Host "  |" -NoNewline -ForegroundColor Magenta
    Write-Host "  Welcome aboard. Keep those servers".PadRight($inner) -NoNewline -ForegroundColor White
    Write-Host "|" -ForegroundColor Magenta
    Write-Host "  |" -NoNewline -ForegroundColor Magenta
    Write-Host "  alive.".PadRight($inner) -NoNewline -ForegroundColor White
    Write-Host "|" -ForegroundColor Magenta
    Write-Host "  +$("=" * $inner)+" -ForegroundColor Magenta
    Write-Host ""
}

# ── Get-started block ──────────────────────────────────────────────────────
function Show-GetStarted {
    param([string]$PipCmd, [string]$GitSource)

    $configPath = "$env:USERPROFILE\.navig\"

    Write-Host "  Get started:" -ForegroundColor White
    Write-Host "    " -NoNewline; Write-Host "navig" -NoNewline -ForegroundColor Yellow; Write-Host "                    Open interactive menu"
    Write-Host "    " -NoNewline; Write-Host "navig host add" -NoNewline -ForegroundColor Yellow; Write-Host "           Add your first server"
    Write-Host "    " -NoNewline; Write-Host "navig help" -NoNewline -ForegroundColor Yellow; Write-Host "               Show all commands"
    Write-Host ""

    if ($GitSource) {
        Write-Host "  " -NoNewline; Write-Host "Update: " -NoNewline -ForegroundColor White
        Write-Host "cd $GitSource && git pull && pip install -e ." -ForegroundColor Yellow
        Write-Host "  " -NoNewline; Write-Host "Source: " -NoNewline -ForegroundColor White
        Write-Host $GitSource -ForegroundColor Yellow
    } else {
        Write-Host "  " -NoNewline; Write-Host "Update: " -NoNewline -ForegroundColor White
        Write-Host "python.exe -m pip install --upgrade navig" -ForegroundColor Yellow
    }
    Write-Host "  " -NoNewline; Write-Host "Config: " -NoNewline -ForegroundColor White
    Write-Host $configPath -ForegroundColor Yellow
    Write-Host "  " -NoNewline; Write-Host "Docs:   " -NoNewline -ForegroundColor White
    Write-Host "https://github.com/navig-run/core" -ForegroundColor Yellow
    Write-Host ""
}

# ── Usage ──────────────────────────────────────────────────────────────────
function Show-Usage {
    Write-Host @"
NAVIG Installer for Windows

Usage:
    & ([scriptblock]::Create((irm https://navig.run/install.ps1)))
    & ([scriptblock]::Create((irm https://navig.run/install.ps1))) -Action uninstall
    .\install.ps1 [OPTIONS]

Options:
  -Version <ver>    Install specific version (e.g. 2.4.14)
  -Action <mode>    install, uninstall, or reinstall
  -Dev              Install from git source (dev mode)
  -GitDir <path>    Git checkout directory (default: $HOME\navig-core)
  -Extras <list>    Comma-separated extras: voice,keyring,dev
  -InstallProfile   Install profile: node, operator, architect (default: operator)
  -NoConfirm        Skip interactive prompts
  -DryRun           Preview actions without executing
  -Verbose          Show detailed output
  -Help             Show this help

Environment variables:
  NAVIG_VERSION             Pin version
  NAVIG_INSTALL_METHOD      pip (default) or git
  NAVIG_EXTRAS              Comma-separated extras
  NAVIG_INSTALL_PROFILE     Install profile (default: operator)
  NAVIG_ACTION              install (default), uninstall, reinstall
"@
}

# ── Python detection ──────────────────────────────────────────────────────
function Find-Python {
    $preferredPaths = @(
        (Join-Path $HOME "AppData\Local\Programs\Python\Python314-32\python.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python314\python.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python313-32\python.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python313\python.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python312\python.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python311\python.exe")
    )
    foreach ($p in $preferredPaths) {
        if (Test-Path $p) {
            try {
                $verOutput = & $p --version 2>&1
                if ($verOutput -match '(\d+)\.(\d+)\.(\d+)') {
                    $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                    if ($major -ge $MIN_PYTHON_MAJOR -and $minor -ge $MIN_PYTHON_MINOR) {
                        Write-NavOk "Python $verOutput found at $p"
                        return $p
                    }
                }
            } catch {}
        }
    }

    foreach ($cmd in @("python", "python3", "py -3")) {
        try {
            $parts     = $cmd -split ' '
            $exe       = $parts[0]
            $cargs     = if ($parts.Length -gt 1) { $parts[1..($parts.Length-1)] } else { @() }
            $verOutput = if ($cargs.Length -gt 0) { & $exe @cargs --version 2>&1 } else { & $exe --version 2>&1 }
            if ($verOutput -match '(\d+)\.(\d+)\.(\d+)') {
                $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                if ($major -ge $MIN_PYTHON_MAJOR -and $minor -ge $MIN_PYTHON_MINOR) {
                    Write-NavOk "Python $verOutput found"
                    return $cmd
                }
            }
        } catch {}
    }
    return $null
}

function Find-Pip {
    param([string]$PythonCmd)
    if ([string]::IsNullOrWhiteSpace($PythonCmd)) { return $null }
    try {
        $parts = $PythonCmd -split ' '
        $exe   = $parts[0]
        $cargs = if ($parts.Length -gt 1) { $parts[1..($parts.Length-1)] + @("-m","pip","--version") } else { @("-m","pip","--version") }
        $out   = & $exe @cargs 2>&1
        if ($LASTEXITCODE -eq 0) { return "$PythonCmd -m pip" }
    } catch {}
    foreach ($cmd in @("pip3","pip")) {
        try {
            & $cmd --version 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { return $cmd }
        } catch {}
    }
    return $null
}

# ── Python installation ────────────────────────────────────────────────────
function Install-PythonWindows {
    Write-NavStep "Python not found - installing via winget..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
            if ($LASTEXITCODE -eq 0) {
                $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
                # Re-verify Python is actually on PATH now (winget can succeed but
                # not update the current session PATH on first run).
                if (-not (Find-Python)) {
                    Write-NavStep "winget reported success but Python not yet on PATH — falling back to direct download..."
                } else {
                    Write-NavOk "Python installed via winget"
                    return $true
                }
            } else {
                Write-NavStep "winget exited $LASTEXITCODE - falling back to direct download..."
            }
        } catch {
            Write-NavStep "winget failed ($($_.Exception.Message)) - falling back to direct download..."
        }
    }
    Write-NavStep "Downloading Python 3.12 installer..."
    $installerUrl  = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
    $installerPath = Join-Path $env:TEMP "navig-python-installer-$([System.IO.Path]::GetRandomFileName()).exe"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
        Write-NavStep "Running Python installer (silent)..."
        $proc = Start-Process -FilePath $installerPath -ArgumentList "/quiet","InstallAllUsers=0","PrependPath=1","Include_test=0" -Wait -PassThru
        if ($proc.ExitCode -ne 0) {
            Write-NavErr "Python installer exited with code $($proc.ExitCode)"
            Write-NavHint "Install manually from https://python.org then re-run this script"
            return $false
        }
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
        Write-NavOk "Python installed"
        return $true
    } catch {
        Write-NavErr "Failed to install Python automatically: $($_.Exception.Message)"
        Write-NavHint "Install manually from https://python.org then re-run this script"
        return $false
    } finally {
        Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
    }
}

# ── Git detection & installation ──────────────────────────────────────────
function Find-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-NavOk "Git available"
        return $true
    }
    return $false
}

function Install-GitWindows {
    Write-NavStep "Installing Git..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
            $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
            Write-NavOk "Git installed via winget"
            return $true
        } catch {
            Write-NavErr "Git installation failed"
            Write-NavHint "Install manually from https://git-scm.com"
            return $false
        }
    }
    Write-NavErr "winget not available"
    Write-NavHint "Install Git manually from https://git-scm.com"
    return $false
}

# ── SSH check ──────────────────────────────────────────────────────────────
function Test-SSH {
    if (Get-Command ssh -ErrorAction SilentlyContinue) {
        Write-NavOk "SSH client available"
        return $true
    }
    Write-NavInfo "SSH client not found - enable OpenSSH in Settings > Optional Features"
    return $true   # non-blocking: navig uses paramiko fallback
}

# ── Install via pip (with live spinner) ───────────────────────────────────
function Find-Pipx {
    param([string]$PipCmd)
    if (Get-Command pipx -ErrorAction SilentlyContinue) { return "pipx" }

    $pipParts = $PipCmd -split ' '
    $exe = $pipParts[0]
    try {
        & $exe -m pipx --version 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { return "$exe -m pipx" }
    } catch {}
    return $null
}

function Install-Pipx {
    param([string]$PipCmd)
    $pipParts = $PipCmd -split ' '
    $exe      = $pipParts[0]
    $baseArgs = if ($pipParts.Length -gt 1) { $pipParts[1..($pipParts.Length-1)] } else { @() }
    $fullArgs = $baseArgs + @("install", "--user", "pipx")

    $code = Invoke-WithSpinner -Label "Installing pipx" -Exe $exe -ArgList $fullArgs
    if ($code -ne 0) {
        Write-NavInfo "pipx unavailable - will use pip install --user instead"
        return $null
    }

    try { & pipx ensurepath 2>$null } catch {}

    return "$exe -m pipx"
}

function Install-NavigPip {
    param([string]$PipCmd)

    $installSpec = "navig"
    if ($Version) { $installSpec = "navig==$Version" }
    if ($Extras)  { $installSpec = "navig[$Extras]"; if ($Version) { $installSpec = "navig[$Extras]==$Version" } }

    # ── Try pipx first (isolated venv, cleanest install) ──────────────────
    $usePipx = Find-Pipx -PipCmd $PipCmd
    if (-not $usePipx) {
        $usePipx = Install-Pipx -PipCmd $PipCmd
    }

    if ($usePipx) {
        $pipxParts = $usePipx -split ' '
        $exe      = $pipxParts[0]
        $baseArgs = if ($pipxParts.Length -gt 1) { $pipxParts[1..($pipxParts.Length-1)] } else { @() }
        $fullArgs = $baseArgs + @("install", $installSpec, "--force")
        $code = Invoke-WithSpinner -Label "Installing NAVIG via pipx" -Exe $exe -ArgList $fullArgs
        # On Windows, pipx's .cmd shim can report a non-zero exit code via
        # Start-Process even when the install succeeded (PATH env variance,
        # shim-launch overhead). Do a secondary shim-existence check.
        if ($code -ne 0) {
            $pipxHome = if ($env:PIPX_HOME) { $env:PIPX_HOME } else { Join-Path $HOME ".local\pipx" }
            $shims = @(
                (Join-Path $pipxHome "venvs\navig\Scripts\navig.exe"),
                (Join-Path $HOME ".local\bin\navig.exe"),
                (Join-Path $HOME ".local\bin\navig.cmd"),
                (Join-Path $HOME "AppData\Local\Programs\pipx\venvs\navig\Scripts\navig.exe")
            )
            if ($shims | Where-Object { Test-Path $_ }) {
                Write-NavInfo "pipx reported non-zero but navig shim found — treating as success"
                $code = 0
            }
        }
        if ($code -eq 0) {
            try { & pipx ensurepath 2>$null } catch {}
            return
        }
        Write-NavInfo "pipx install failed - retrying with pip install --user"
    }

    # ── pip --user (direct or fallback from pipx failure) ─────────────────
    $pipParts = $PipCmd -split ' '
    $exe      = $pipParts[0]
    $baseArgs = if ($pipParts.Length -gt 1) { $pipParts[1..($pipParts.Length-1)] } else { @() }
    $fullArgs = $baseArgs + @("install", "--user", "--upgrade", $installSpec)

    $code = Invoke-WithSpinner -Label "Installing NAVIG via pip" -Exe $exe -ArgList $fullArgs
    if ($code -ne 0) {
        # Auto-retry once with --no-cache-dir (clears any corrupted cache entries)
        Write-NavInfo "pip install failed - retrying with --no-cache-dir"
        $retryArgs = $baseArgs + @("install", "--user", "--upgrade", "--no-cache-dir", $installSpec)
        $code = Invoke-WithSpinner -Label "Installing NAVIG via pip (no-cache)" -Exe $exe -ArgList $retryArgs
    }
    if ($code -ne 0) {
        Write-NavErr "Installation failed"
        Write-NavHint "Try manually:"
        Write-NavHint "  pip install --user $installSpec"
        Write-NavHint "  pip install --user --no-cache-dir $installSpec"
        Write-NavHint "Docs: https://github.com/navig-run/core"
        exit 1
    }
    # Refresh PATH for the current session so navig is immediately usable
    # without requiring a terminal restart.
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH","User")    + ";" + $env:PATH
}

# ── Install via git ───────────────────────────────────────────────────────
function Install-NavigGit {
    param([string]$PipCmd)

    $repoDir = $GitDir

    if (Test-Path "$repoDir\.git") {
        Write-NavStep "Updating existing checkout: $repoDir"
    }

    if (-not (Find-Git)) {
        if (-not (Install-GitWindows)) { exit 1 }
    }

    if (-not (Test-Path $repoDir)) {
        try {
            Write-NavStep "Cloning NAVIG from: $REPO_URL"
            & git clone "$REPO_URL" "$repoDir" 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-NavErr "git clone failed (exit $LASTEXITCODE). Check network and credentials."
                exit 1
            }
        } catch {
            Write-NavErr "git clone threw an exception: $($_.Exception.Message)"
            exit 1
        }
    } else {
        try {
            $dirty = git -C $repoDir status --porcelain 2>&1
            if ($LASTEXITCODE -ne 0) { Write-NavInfo "git status failed - skipping pull"; $dirty = "dirty" }
            if (-not $dirty) {
                & git -C $repoDir pull --rebase 2>&1
                if ($LASTEXITCODE -ne 0) { Write-NavInfo "git pull --rebase failed (exit $LASTEXITCODE) - continuing with existing checkout" }
            } else {
                Write-NavInfo "Repo has local changes - skipping git pull"
            }
        } catch {
            Write-NavInfo "git pull threw an exception ($($_.Exception.Message)) - continuing with existing checkout"
        }
    }

    $pipParts = $PipCmd -split ' '
    $exe      = $pipParts[0]
    $baseArgs = if ($pipParts.Length -gt 1) { $pipParts[1..($pipParts.Length-1)] } else { @() }

    if ($Production) {
        $spec     = if ($Extras) { "${repoDir}[$Extras]" } else { $repoDir }
        $fullArgs = $baseArgs + @("install",$spec)
    } else {
        $spec     = if ($Extras) { "${repoDir}[$Extras]" } else { $repoDir }
        $fullArgs = $baseArgs + @("install","-e",$spec)
    }

    $code = Invoke-WithSpinner -Label "Installing NAVIG from source" -Exe $exe -ArgList $fullArgs
    if ($code -ne 0) {
        Write-NavErr "Editable install failed"
        exit 1
    }
    Write-NavInfo "Source: $repoDir"
}

# ── Config directory setup ────────────────────────────────────────────────
function Initialize-NavigConfig {
    $configDir = Join-Path $env:USERPROFILE ".navig"
    foreach ($sub in @("","workspace","logs","cache")) {
        $path = if ($sub) { Join-Path $configDir $sub } else { $configDir }
        if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
    }
    Write-NavOk "Config directory ready at $configDir\"
}

# ── Existing installation check ───────────────────────────────────────────
function Write-ExistingNavigMessage {
    $ver = Get-NavigVersion
    if ($ver) {
        Write-NavInfo "Existing NAVIG detected: $ver - upgrading"
    } else {
        Write-NavInfo "Existing NAVIG detected - upgrading"
    }
}

# ── Verify installation ───────────────────────────────────────────────────
function Test-NavigInstall {
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH","User")   + ";" + $env:PATH

    if (Get-Command navig -ErrorAction SilentlyContinue) {
        Write-NavOk "navig command verified in PATH"
        return $true
    }

    $pipPaths = @(
        (Join-Path $HOME "AppData\Local\Programs\Python\Python314-32\Scripts"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python314\Scripts"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python313-32\Scripts"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python313\Scripts"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python312\Scripts"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python311\Scripts"),
        (Join-Path $HOME "AppData\Roaming\Python\Python313\Scripts"),
        (Join-Path $HOME "AppData\Roaming\Python\Python312\Scripts"),
        (Join-Path $HOME "AppData\Roaming\Python\Python311\Scripts"),
        "C:\Python313\Scripts"
    )

    foreach ($p in $pipPaths) {
        if (Test-Path (Join-Path $p "navig.exe")) {
            $env:PATH = "$p;$env:PATH"
            Write-NavOk "navig found at $p"
            Write-NavInfo "Add to PATH permanently:"
            Write-NavHint "[Environment]::SetEnvironmentVariable('PATH', `"$p;`" + [Environment]::GetEnvironmentVariable('PATH','User'), 'User')"
            return $true
        }
    }

    Write-NavInfo "navig installed - restart your terminal to pick up PATH changes"
    return $false
}

# ── Resolve installed version ─────────────────────────────────────────────
function Get-NavigVersion {
    # Try navig itself first (most reliable)
    try {
        $ver = navig --version 2>&1 | Select-Object -First 1
        if ($ver -match '(\d+\.\d+\.\d+)') { return $Matches[1] }
    } catch {}
    # Fall back to pip show - avoid bare 'pip' which may throw under ErrorActionPreference=Stop
    foreach ($pipCandidate in @("pip3","pip")) {
        if (-not (Get-Command $pipCandidate -ErrorAction SilentlyContinue)) { continue }
        try {
            $line = (& $pipCandidate show navig 2>$null) | Select-String 'Version:'
            if ($line) { return ($line -replace 'Version:\s*','').Trim() }
        } catch {}
    }
    return ""
}

$script:UninstallFailures = @()

function Reset-NavigUninstallState {
    $script:UninstallFailures = @()
    Remove-Item -Path $UNINSTALL_LOG -Force -ErrorAction SilentlyContinue
}

function Add-UninstallFailure {
    param(
        [string]$Step,
        [string]$Message
    )

    $entry = "$Step - $Message"
    $script:UninstallFailures += $entry
    Add-Content -Path $UNINSTALL_LOG -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $entry)
    Write-NavErr $entry
}

function Stop-NavigBackgroundArtifacts {
    try {
        $service = Get-Service -Name $WINDOWS_SERVICE_NAME -ErrorAction SilentlyContinue
        if ($service) {
            if ($service.Status -ne "Stopped") {
                Stop-Service -Name $WINDOWS_SERVICE_NAME -Force -ErrorAction Stop
                Write-NavOk "Stopped service: $WINDOWS_SERVICE_NAME"
            } else {
                Write-NavInfo "Service already stopped: $WINDOWS_SERVICE_NAME"
            }
        } else {
            Write-NavInfo "Service not present: $WINDOWS_SERVICE_NAME"
        }
    } catch {
        Add-UninstallFailure -Step "Stop service $WINDOWS_SERVICE_NAME" -Message $_.Exception.Message
    }

    try {
        if (Test-NavigScheduledTask) {
            schtasks /end /tn $WINDOWS_TASK_NAME 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-NavOk "Stopped scheduled task: $WINDOWS_TASK_NAME"
            } else {
                Write-NavInfo "Scheduled task was not running: $WINDOWS_TASK_NAME"
            }
        } else {
            Write-NavInfo "Scheduled task not present: $WINDOWS_TASK_NAME"
        }
    } catch {
        Add-UninstallFailure -Step "Stop scheduled task $WINDOWS_TASK_NAME" -Message $_.Exception.Message
    }

    try {
        $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.Name -match '^(navig(\.exe)?|python(w)?\.exe)$' -and (
                $_.ExecutablePath -match '(?i)\\navig(\.exe)?$' -or
                $_.CommandLine -match '(?i)\bnavig(\.daemon\.entry|\.main)?\b'
            )
        }

        if ($processes) {
            foreach ($proc in ($processes | Sort-Object ProcessId -Unique)) {
                try {
                    Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
                    Write-NavOk "Stopped process: $($proc.Name) (PID $($proc.ProcessId))"
                } catch {
                    Add-UninstallFailure -Step "Stop process $($proc.ProcessId)" -Message $_.Exception.Message
                }
            }
        } else {
            Write-NavInfo "No running NAVIG processes found"
        }
    } catch {
        Add-UninstallFailure -Step "Stop NAVIG processes" -Message $_.Exception.Message
    }
}

function Remove-NavigFiles {
    param([switch]$PreserveUserData)

    $installState = Get-NavigInstallState
    $metadata = $installState.Metadata
    $pipSpec = Get-PipCommandSpec

    if ($pipSpec) {
        try {
            & $pipSpec.Exe @($pipSpec.Args + @("uninstall", "-y", "navig")) 2>&1 | Out-Null
            Write-NavOk "Removed pip package: navig (if present)"
        } catch {}
    } else {
        Write-NavInfo "pip not available - skipping pip uninstall"
    }

    try {
        if (Get-Command pipx -ErrorAction SilentlyContinue) {
            & pipx uninstall navig 2>&1 | Out-Null
            Write-NavOk "Removed pipx package: navig (if present)"
        }
    } catch {}

    $fileTargets = @(
        $INSTALL_MARKER_PATH,
        (Join-Path $HOME ".local\bin\navig.cmd"),
        (Join-Path $HOME ".local\bin\navig.exe"),
        (Join-Path $HOME ".local\bin\navig-script.py"),
        (Join-Path $HOME ".local\bin\navig.exe.manifest"),
        (Join-Path $env:LOCALAPPDATA "navig\navig.exe")
    )

    if (-not [string]::IsNullOrWhiteSpace($installState.CommandPath)) {
        $fileTargets += $installState.CommandPath
        $commandDir = Split-Path -Path $installState.CommandPath -Parent
        if (-not [string]::IsNullOrWhiteSpace($commandDir)) {
            $fileTargets += @(
                (Join-Path $commandDir "navig.exe"),
                (Join-Path $commandDir "navig-script.py"),
                (Join-Path $commandDir "navig.exe.manifest")
            )
        }
    }

    foreach ($path in ($fileTargets | Where-Object { $_ } | Select-Object -Unique)) {
        if (-not (Test-Path $path)) { continue }

        try {
            Remove-Item -Path $path -Force -ErrorAction Stop
            Write-NavOk "Removed file: $path"
        } catch {
            Add-UninstallFailure -Step "Remove file $path" -Message $_.Exception.Message
        }
    }

    $dirTargets = @(
        (Join-Path $env:LOCALAPPDATA "navig"),
        (Join-Path $env:USERPROFILE ".navig\venv"),
        (Join-Path $env:USERPROFILE ".navig\daemon")
    )

    if (
        $metadata.ContainsKey("InstallMethod") -and
        $metadata["InstallMethod"] -eq "git" -and
        $metadata.ContainsKey("GitDir") -and
        -not [string]::IsNullOrWhiteSpace($metadata["GitDir"])
    ) {
        $dirTargets += $metadata["GitDir"]
    }

    foreach ($path in ($dirTargets | Where-Object { $_ } | Select-Object -Unique)) {
        if (-not (Test-Path $path)) { continue }

        try {
            Remove-Item -Path $path -Recurse -Force -ErrorAction Stop
            Write-NavOk "Removed directory: $path"
        } catch {
            Add-UninstallFailure -Step "Remove directory $path" -Message $_.Exception.Message
        }
    }

    $navigHome = Join-Path $env:USERPROFILE ".navig"
    if ($PreserveUserData) {
        Write-NavInfo "Preserving user data at $navigHome"
    } elseif (Test-Path $navigHome) {
        try {
            Remove-Item -Path $navigHome -Recurse -Force -ErrorAction Stop
            Write-NavOk "Removed directory: $navigHome"
        } catch {
            Add-UninstallFailure -Step "Remove directory $navigHome" -Message $_.Exception.Message
        }
    }

    $binDir = Join-Path $HOME ".local\bin"
    if (Test-Path $binDir) {
        try {
            if (@(Get-ChildItem -Path $binDir -Force -ErrorAction SilentlyContinue).Count -eq 0) {
                Remove-Item -Path $binDir -Force -ErrorAction Stop
                Write-NavOk "Removed empty directory: $binDir"
            }
        } catch {
            Add-UninstallFailure -Step "Remove directory $binDir" -Message $_.Exception.Message
        }
    }
}

function Remove-NavigRegistryArtifacts {
    if (-not (Test-Path $INSTALL_REGISTRY_KEY) -and -not (Test-Path $INSTALL_MARKER_PATH)) {
        Write-NavInfo "Installer registry state not present"
        return
    }

    try {
        Remove-NavigInstallState
        Write-NavOk "Removed installer registry state"
    } catch {
        Add-UninstallFailure -Step "Remove installer registry state" -Message $_.Exception.Message
    }
}

function Remove-NavigServiceArtifacts {
    $service = $null
    try {
        $service = Get-Service -Name $WINDOWS_SERVICE_NAME -ErrorAction SilentlyContinue
    } catch {}

    if ($service) {
        $removed = $false

        if (Get-Command nssm -ErrorAction SilentlyContinue) {
            try {
                & nssm stop $WINDOWS_SERVICE_NAME 2>$null | Out-Null
                & nssm remove $WINDOWS_SERVICE_NAME confirm 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-NavOk "Removed service: $WINDOWS_SERVICE_NAME"
                    $removed = $true
                }
            } catch {}
        }

        if (-not $removed) {
            try {
                & sc.exe stop $WINDOWS_SERVICE_NAME 2>$null | Out-Null
                & sc.exe delete $WINDOWS_SERVICE_NAME 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-NavOk "Removed service: $WINDOWS_SERVICE_NAME"
                } else {
                    Add-UninstallFailure -Step "Remove service $WINDOWS_SERVICE_NAME" -Message "sc.exe exited with code $LASTEXITCODE"
                }
            } catch {
                Add-UninstallFailure -Step "Remove service $WINDOWS_SERVICE_NAME" -Message $_.Exception.Message
            }
        }
    } else {
        Write-NavInfo "Service not present: $WINDOWS_SERVICE_NAME"
    }

    try {
        if (Test-NavigScheduledTask) {
            schtasks /delete /tn $WINDOWS_TASK_NAME /f 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-NavOk "Removed scheduled task: $WINDOWS_TASK_NAME"
            } else {
                Add-UninstallFailure -Step "Remove scheduled task $WINDOWS_TASK_NAME" -Message "schtasks exited with code $LASTEXITCODE"
            }
        } else {
            Write-NavInfo "Scheduled task not present: $WINDOWS_TASK_NAME"
        }
    } catch {
        Add-UninstallFailure -Step "Remove scheduled task $WINDOWS_TASK_NAME" -Message $_.Exception.Message
    }
}

function Remove-NavigPathArtifacts {
    try {
        $targets = @(
            (Join-Path $env:USERPROFILE ".local\bin"),
            (Join-Path $env:LOCALAPPDATA "navig")
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

        $rawPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $parts = @(Split-PathEntries $rawPath)
        $removed = @()
        $kept = @()

        foreach ($part in $parts) {
            $normalizedPart = $part.Trim().TrimEnd('\')
            $isNavigPath = $false

            foreach ($target in $targets) {
                if ($normalizedPart -ieq $target.Trim().TrimEnd('\')) {
                    $isNavigPath = $true
                    break
                }
            }

            if ($isNavigPath) {
                $removed += $part
            } else {
                $kept += $part
            }
        }

        if ($removed.Count -gt 0) {
            [Environment]::SetEnvironmentVariable("Path", ($kept -join ';'), "User")
            $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" +
                        [System.Environment]::GetEnvironmentVariable("PATH","User")
            foreach ($entry in $removed) {
                Write-NavOk "Removed PATH entry: $entry"
            }
        } else {
            Write-NavInfo "No NAVIG PATH entries found"
        }
    } catch {
        Add-UninstallFailure -Step "Remove NAVIG PATH entries" -Message $_.Exception.Message
    }
}

function Invoke-NavigUninstall {
    param(
        [switch]$PreserveUserData,
        [switch]$ForReinstall
    )

    Reset-NavigUninstallState

    Write-Host ""
    if ($ForReinstall) {
        Write-NavInfo "Preparing reinstall / repair cleanup..."
    } else {
        Write-NavInfo "Starting NAVIG uninstall..."
    }

    Write-NavStep "Stopping NAVIG processes..."
    Stop-NavigBackgroundArtifacts

    Write-NavStep "Removing NAVIG files..."
    Remove-NavigFiles -PreserveUserData:$PreserveUserData

    Write-NavStep "Removing NAVIG registry entries..."
    Remove-NavigRegistryArtifacts

    Write-NavStep "Removing NAVIG services and tasks..."
    Remove-NavigServiceArtifacts

    Write-NavStep "Removing NAVIG PATH entries..."
    Remove-NavigPathArtifacts

    Write-Host ""
    if ($script:UninstallFailures.Count -gt 0) {
        $summaryMessage = if ($ForReinstall) {
            "Cleanup completed with warnings. Continuing reinstall / repair."
        } else {
            "Navig uninstall completed with warnings."
        }
        Write-NavErr $summaryMessage
        Write-NavHint "Failure log: $UNINSTALL_LOG"
        foreach ($failure in $script:UninstallFailures) {
            Write-NavHint $failure
        }
    } elseif ($ForReinstall) {
        Write-NavOk "Cleanup completed. Continuing reinstall / repair."
    } else {
        Write-NavOk "Navig has been successfully uninstalled."
    }

    return [pscustomobject]@{
        Success  = ($script:UninstallFailures.Count -eq 0)
        Failures = @($script:UninstallFailures)
    }
}

# ── Main ──────────────────────────────────────────────────────────────────
function Main {
    if ($Help) { Show-Usage; return }
    if ($Dev)  { $InstallMethod = "git" }

    try {
        $requestedAction = Normalize-NavigAction $Action
    } catch {
        Write-NavErr $_.Exception.Message
        exit 1
    }

    Show-Banner

    $installState = Get-NavigInstallState
    Apply-NavigInstallDefaults -Metadata $installState.Metadata
    $plannedAction = if (-not [string]::IsNullOrWhiteSpace($requestedAction)) {
        $requestedAction
    } elseif ($installState.IsInstalled -and -not $NoConfirm -and (Test-InteractiveSession)) {
        "prompt"
    } else {
        "install"
    }

    if ($DryRun) {
        Write-NavInfo "Dry run - no changes will be made"
        Write-Host ""
        Write-Host "  OS:              Windows $([System.Environment]::OSVersion.Version)" -ForegroundColor DarkGray
        Write-Host "  Installed:       $(if ($installState.IsInstalled) { 'yes' } else { 'no' })" -ForegroundColor DarkGray
        Write-Host "  Planned action:  $plannedAction" -ForegroundColor DarkGray
        Write-Host "  Install method:  $InstallMethod"                                     -ForegroundColor DarkGray
        Write-Host "  Version:         $(if ($Version) { $Version } else { 'latest' })"   -ForegroundColor DarkGray
        Write-Host "  Extras:          $(if ($Extras)  { $Extras  } else { 'none'   })"   -ForegroundColor DarkGray
        Write-Host "  Profile:         $InstallProfile"                                            -ForegroundColor DarkGray
        Write-Host "  Git dir:         $GitDir"                                             -ForegroundColor DarkGray
        Write-Host "  Config dir:      $env:USERPROFILE\.navig\"                           -ForegroundColor DarkGray
        Write-Host ""
        Write-NavInfo "Dry run complete - nothing was changed"
        return
    }

    # Step 1: OS
    $osVer = [System.Environment]::OSVersion.Version
    Write-NavOk "Windows $osVer ($env:PROCESSOR_ARCHITECTURE)"

    # Step 2: Resolve install/uninstall action
    $resolvedAction = Resolve-NavigExecutionAction -IsInstalled $installState.IsInstalled -RequestedAction $requestedAction
    switch ($resolvedAction) {
        "cancel" {
            Write-NavInfo "Canceled - no changes were made"
            return
        }
        "uninstall" {
            if (-not $installState.IsInstalled) {
                Write-NavInfo "NAVIG is not currently installed - removing leftover artifacts if any"
            }
            $uninstallResult = Invoke-NavigUninstall
            if (-not $uninstallResult.Success) { exit 1 }
            return
        }
        "reinstall" {
            if ($installState.IsInstalled) {
                Write-NavInfo "Existing NAVIG detected - performing reinstall / repair"
                $reinstallClean = Invoke-NavigUninstall -PreserveUserData -ForReinstall
                if (-not $reinstallClean.Success) {
                    Write-NavInfo "Pre-reinstall cleanup reported failures (see above) — continuing with install"
                }
                $installState = Get-NavigInstallState
            } else {
                Write-NavInfo "No existing NAVIG installation found - continuing with install"
            }
        }
    }

    $isUpgrade = $installState.IsInstalled
    if ($isUpgrade) {
        Write-ExistingNavigMessage
    }

    # Step 3: Python
    Write-NavStep "Checking Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+..."
    $pythonCmd = Find-Python
    if (-not $pythonCmd) {
        if (-not (Install-PythonWindows)) {
            Write-NavErr "Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ is required"
            Write-NavHint "Install from https://python.org or run:  winget install Python.Python.3.12"
            exit 1
        }
        $pythonCmd = Find-Python
        if (-not $pythonCmd) {
            Write-NavErr "Python still not found after install"
            Write-NavHint "Restart this terminal and run the installer again"
            exit 1
        }
    }

    # Step 4: pip
    Write-NavStep "Checking pip..."
    $pipCmd = Find-Pip -PythonCmd $pythonCmd
    if (-not $pipCmd) {
        Write-NavStep "pip not found - bootstrapping via ensurepip..."
        $parts = $pythonCmd -split ' '
        & $parts[0] -m ensurepip --upgrade 2>$null
        $pipCmd = Find-Pip -PythonCmd $pythonCmd
        if (-not $pipCmd) {
            Write-NavErr "pip is required but could not be installed"
            Write-NavHint "Try running:  python -m ensurepip --upgrade"
            exit 1
        }
    }
    Write-NavOk "pip available"

    # Step 5: SSH
    Test-SSH | Out-Null

    # Step 6: Install NAVIG
    if ($InstallMethod -eq "git") {
        Install-NavigGit -PipCmd $pipCmd
    } else {
        Install-NavigPip -PipCmd $pipCmd
    }

    # Step 7: Config directory
    Initialize-NavigConfig

    # Step 8: Verify PATH
    Test-NavigInstall | Out-Null

    # Step 9: Success
    $installedVer = Get-NavigVersion
    try {
        Write-NavigInstallState -InstalledVersion $installedVer
    } catch {
        Write-NavErr "NAVIG installed, but failed to persist install state: $($_.Exception.Message)"
        Write-NavHint "Install / uninstall auto-detection may be incomplete until this is fixed"
    }
    Show-SuccessBanner -InstalledVersion $installedVer

    $gitSource = if ($InstallMethod -eq "git") { $GitDir } else { $null }
    Show-GetStarted -PipCmd $pipCmd -GitSource $gitSource

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
            if (Test-Path $src) {
                Copy-Item -Path $src -Destination $dst -Force
                Write-NavOk "Synced $f -> navig-www"
            }
        }
    }
}
