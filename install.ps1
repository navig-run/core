# NAVIG Installer - Windows (PowerShell 5.1+)
#
# Usage:
#   iwr -useb https://navig.run/install.ps1 | iex
#   .\install.ps1 [-Version <ver>] [-Action install|uninstall|reinstall] [-Verbose]
#

if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Error "NAVIG requires PowerShell 5.1 or newer. You are running version $($PSVersionTable.PSVersion)."
    exit 1
}
#
# Environment:
#   NAVIG_VERSION      Pin version (e.g. "2.7.0")
#   NAVIG_ACTION       install (default) | uninstall | reinstall
#   NO_COLOR           Disable color output

# ── Arg parsing (compatible with `irm | iex` pipe) ───────────
$Version   = $env:NAVIG_VERSION
$Action    = $env:NAVIG_ACTION
$DryRun    = $args -contains "-DryRun"    -or $args -contains "/DryRun"
$NoConfirm = $args -contains "-NoConfirm" -or $args -contains "/NoConfirm"
$Help      = $args -contains "-Help"      -or $args -contains "/Help"
$Verbose   = $args -contains "-Verbose"   -or $args -contains "/Verbose"

for ($i = 0; $i -lt $args.Length - 1; $i++) {
    switch ($args[$i]) {
        { $_ -in "-Version", "-v" } { $Version = $args[$i + 1] }
        { $_ -in "-Action",  "-a" } { $Action  = $args[$i + 1] }
    }
}

# ── Encoding (best-effort; some hosts restrict Console properties) ───────────
try { $OutputEncoding           = [System.Text.Encoding]::UTF8 } catch {}
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { [Console]::InputEncoding  = [System.Text.Encoding]::UTF8 } catch {}

# ── Constants ─────────────────────────────────────────────────
$INSTALL_REGISTRY_KEY = "Registry::HKEY_CURRENT_USER\Software\NAVIG\Installer"
$INSTALL_MARKER_PATH  = Join-Path $env:USERPROFILE ".navig\install.marker"
$WINDOWS_SERVICE_NAME = "NavigDaemon"
$WINDOWS_TASK_NAME    = "NAVIG Daemon"

# ── Isolated runtime layout ───────────────────────────────────
# NAVIG ships its own pinned CPython + venv so the user needs NOTHING
# pre-installed. Nothing here touches the system Python.
$NAVIG_HOME       = Join-Path $env:USERPROFILE ".navig"
$RUNTIME_DIR      = Join-Path $NAVIG_HOME "runtime"        # uv.exe + python/ + venv/
$RUNTIME_VENV     = Join-Path $RUNTIME_DIR "venv"
$RUNTIME_VENV_PY  = Join-Path $RUNTIME_VENV "Scripts\python.exe"
$RUNTIME_PY_DIR   = Join-Path $RUNTIME_DIR "python"        # uv-managed CPython installs
$RUNTIME_CACHE    = Join-Path $RUNTIME_DIR "cache"         # uv cache (self-contained)
$UV_EXE           = Join-Path $RUNTIME_DIR "uv.exe"
$SHIM_DIR         = Join-Path $env:USERPROFILE ".local\bin"
$SHIM_PATH        = Join-Path $SHIM_DIR "navig.cmd"

# Pinned Python series for the managed runtime.
$PYTHON_SERIES    = "3.12"

# uv release pin. Leave $UV_VERSION empty to track the latest GitHub release.
# When pinning a version, also set $UV_SHA256 to that asset's SHA-256 to enable
# checksum verification (download is TLS-authenticated regardless).
$UV_VERSION       = ""          # e.g. "0.7.13" — empty = latest
$UV_SHA256        = ""          # SHA-256 of the windows asset for $UV_VERSION

# ── Terminal capabilities ─────────────────────────────────────
$script:NavColor   = $true
$script:NavUnicode = $true

function Initialize-Terminal {
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

# ── Color / print helpers ─────────────────────────────────────
function clr { param([string]$t, [string]$fg)
    if ($script:NavColor) { Write-Host $t -NoNewline -ForegroundColor $fg }
    else                  { Write-Host $t -NoNewline }
}
function nl { Write-Host "" }

# ── Symbols ───────────────────────────────────────────────────
# Always return [string] — PS 5.1 does not support [char] * [int] (used in hline)
function sym { param([string]$n)
    if ($script:NavUnicode) {
        switch ($n) {
            "ok"     { return [string][char]0x2713 }  # ✓
            "step"   { return [string][char]0x203A }  # ›
            "err"    { return [string][char]0x00D7 }  # ×
            "warn"   { return "!" }
            "bullet" { return [string][char]0x00B7 }  # ·
            "tl"     { return [string][char]0x256D }  # ╭
            "tr"     { return [string][char]0x256E }  # ╮
            "bl"     { return [string][char]0x2570 }  # ╰
            "br"     { return [string][char]0x256F }  # ╯
            "hz"     { return [string][char]0x2500 }  # ─
            "vt"     { return [string][char]0x2502 }  # │
        }
    } else {
        switch ($n) {
            "ok"     { return "OK" }
            "step"   { return " >" }
            "err"    { return "!!" }
            "warn"   { return " !" }
            "bullet" { return "." }
            "tl"     { return "+" }
            "tr"     { return "+" }
            "bl"     { return "+" }
            "br"     { return "+" }
            "hz"     { return "-" }
            "vt"     { return "|" }
        }
    }
    return "?"
}

# ── Layout ────────────────────────────────────────────────────
$script:LW = 52
$script:LB = 12

$script:Taglines = @(
    "Terminal-first. Chaos last.",
    "Operate everything. Forget nothing.",
    "Remote systems. Direct control.",
    "One command closer to order.",
    "Infrastructure without dashboard fatigue.",
    "SSH, databases, containers. One operator surface.",
    "Built for operators, not spectators.",
    "Control returns to the terminal.",
    "Run less guesswork.",
    "From host to workflow, stay in NAVIG.",
    "The operator system for real infrastructure.",
    "Direct ops. No theater.",
    "Your infrastructure, under command.",
    "Less dashboard. More control.",
    "Where remote operations become readable.",
    "The terminal was never the problem.",
    "No admin visible in graveyard.",
    "Stay close to the metal."
)

function hline { param([int]$w) return (sym "hz") * $w }

function Print-Header {
    $lw   = $script:LW
    $line = hline ($lw + 2)
    $tl = sym "tl"; $tr = sym "tr"; $bl = sym "bl"; $br = sym "br"; $vt = sym "vt"
    nl
    clr "  $tl$line$tr" "DarkGray"; nl
    clr "  $vt" "DarkGray"; nl
    clr "  $vt" "DarkGray"; clr "   NAVIG" "Cyan"; nl
    $tagline = $script:Taglines | Get-Random
    clr "  $vt" "DarkGray"; clr "   $tagline" "DarkGray"; nl
    clr "  $vt" "DarkGray"; nl
    clr "  $bl$line$br" "DarkGray"; nl
    nl
}

function Print-Section { param([string]$title)
    nl; clr "  $title" "Cyan"; nl
}

function Write-Row {
    param([string]$Symbol, [string]$Color, [string]$Label, [string]$Value = "", [string]$ValueColor = "White")
    $labelPad = $Label.PadRight($script:LB)
    clr "  $Symbol  " $Color
    clr $labelPad "White"
    if ($Value) { clr $Value $ValueColor }
    nl
}

function Write-Ok    { param([string]$lbl, [string]$val = "") Write-Row (sym "ok")     "Green"  $lbl $val }
function Write-Step  { param([string]$lbl, [string]$val = "") Write-Row (sym "step")   "Cyan"   $lbl $val }
function Write-Err   { param([string]$lbl, [string]$val = "") Write-Row (sym "err")    "Red"    $lbl $val }
function Write-Warn  { param([string]$lbl, [string]$val = "") Write-Row (sym "warn")   "Yellow" $lbl $val }

function Write-NavVerbose { param([string]$msg)
    if ($Verbose) { clr "       $msg" "DarkGray"; nl }
}
function Write-NavHint { param([string]$msg) clr "       $msg" "DarkGray"; nl }

# ── Done block ────────────────────────────────────────────────
function Print-Done { param([string]$Version)
    $lw   = $script:LW
    $line = hline ($lw + 2)
    $tl = sym "tl"; $tr = sym "tr"; $bl = sym "bl"; $br = sym "br"; $vt = sym "vt"
    $verStr = if ($Version) { "NAVIG $Version" } else { "NAVIG" }
    nl
    clr "  $tl$line$tr" "Green"; nl
    $inner = ("  " + $verStr).PadRight($lw + 2)
    clr "  $vt" "Green"; clr $inner "White"; clr $vt "Green"; nl
    clr "  $bl$line$br" "Green"; nl
    nl
    clr "     " "DarkGray"; clr "navig --version" "Yellow"; clr "   confirm install"   "DarkGray"; nl
    clr "     " "DarkGray"; clr "navig --help   " "Yellow"; clr "   all commands"      "DarkGray"; nl
    clr "     " "DarkGray"; clr "navig init     " "Yellow"; clr "   first-time setup"  "DarkGray"; nl
    nl
}

# ── Failure block ─────────────────────────────────────────────
function Print-Failure {
    param([string]$Title, [string]$Problem = "", [string]$Fix = "", [string]$Command = "")
    $lw   = $script:LW
    $line = hline ($lw + 2)
    $tl = sym "tl"; $tr = sym "tr"; $bl = sym "bl"; $br = sym "br"; $vt = sym "vt"
    nl
    clr "  $tl$line$tr" "Red"; nl
    clr "  $vt " "Red"; clr " $(sym 'err')  $Title" "Red"; nl
    clr "  $bl$line$br" "Red"; nl
    nl
    if ($Problem) { clr "  Problem  " "DarkGray"; clr $Problem "White"; nl }
    if ($Fix)     { clr "  Fix      " "DarkGray"; clr $Fix     "White"; nl }
    if ($Command) { clr "  Run      " "DarkGray"; clr $Command "Yellow"; nl }
    nl
}

# ── Usage ─────────────────────────────────────────────────────
function Show-Usage {
    Write-Host @"
NAVIG Installer for Windows

Usage:
    iwr -useb https://navig.run/install.ps1 | iex
    .\install.ps1 [OPTIONS]

Options:
  -Version <ver>   Install specific version (e.g. 2.7.0)
  -Action <mode>   install (default) | uninstall | reinstall
  -NoConfirm       Skip interactive prompts
  -DryRun          Preview actions without executing
  -Verbose         Show detailed output
  -Help            Show this help

Environment:
  NAVIG_VERSION    Pin version
  NAVIG_ACTION     install | uninstall | reinstall
  NO_COLOR         Disable color output
"@
}

# ── Normalize action ──────────────────────────────────────────
function Normalize-NavigAction { param([string]$v)
    if ([string]::IsNullOrWhiteSpace($v)) { return "" }
    switch ($v.Trim().ToLowerInvariant()) {
        "install"   { return "install" }
        "uninstall" { return "uninstall" }
        "reinstall" { return "reinstall" }
        "repair"    { return "reinstall" }
        default     { throw "Unknown action '$v'. Use: install, uninstall, reinstall." }
    }
}

# ── Managed runtime (uv) ──────────────────────────────────────
# Everything below installs an ISOLATED Python under ~/.navig/runtime.
# No system Python is detected, required, or modified.

function Get-NavigUvAsset {
    # Returns the uv release asset name for this machine's architecture.
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($arch -eq "ARM64") { return "uv-aarch64-pc-windows-msvc.zip" }
    return "uv-x86_64-pc-windows-msvc.zip"
}

function Get-NavigUvUrl {
    $asset = Get-NavigUvAsset
    if ([string]::IsNullOrWhiteSpace($UV_VERSION)) {
        return "https://github.com/astral-sh/uv/releases/latest/download/$asset"
    }
    return "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$asset"
}

function Install-NavigUv {
    # Ensures $UV_EXE exists (pinned, self-contained under the runtime dir).
    if (Test-Path $UV_EXE) { Write-NavVerbose "uv present: $UV_EXE"; return $true }

    if (-not (Test-Path $RUNTIME_DIR)) { New-Item -ItemType Directory -Path $RUNTIME_DIR -Force | Out-Null }
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

    $url    = Get-NavigUvUrl
    $tmpZip = Join-Path ([IO.Path]::GetTempPath()) ("navig-uv-" + [Guid]::NewGuid().ToString("N") + ".zip")
    $tmpDir = Join-Path ([IO.Path]::GetTempPath()) ("navig-uv-" + [Guid]::NewGuid().ToString("N"))
    try {
        Write-NavVerbose "Downloading uv: $url"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmpZip -ErrorAction Stop

        if (-not [string]::IsNullOrWhiteSpace($UV_SHA256)) {
            $actual = (Get-FileHash -Path $tmpZip -Algorithm SHA256).Hash
            if ($actual -ne $UV_SHA256.ToUpperInvariant()) {
                Write-NavHint "uv checksum mismatch (expected $UV_SHA256, got $actual)"
                return $false
            }
            Write-NavVerbose "uv checksum verified"
        } else {
            Write-NavVerbose "uv checksum skipped (no pin set; download was TLS-authenticated)"
        }

        Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force
        $found = Get-ChildItem -Path $tmpDir -Filter "uv.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $found) { Write-NavHint "uv.exe not found in downloaded archive"; return $false }
        Copy-Item -Path $found.FullName -Destination $UV_EXE -Force
        return (Test-Path $UV_EXE)
    } catch {
        Write-NavHint "uv download failed: $($_.Exception.Message)"
        return $false
    } finally {
        Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
        Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-NavigUv {
    # Run uv with a self-contained environment (managed python + cache under
    # the runtime dir). Returns @{ Ok = <bool>; Tail = <last error lines> }.
    param([string[]]$UvArgs)
    $env:UV_PYTHON_INSTALL_DIR = $RUNTIME_PY_DIR
    $env:UV_CACHE_DIR          = $RUNTIME_CACHE
    $outFile = [IO.Path]::GetTempFileName()
    $errFile = [IO.Path]::GetTempFileName()
    try {
        $proc = Start-Process -FilePath $UV_EXE -ArgumentList $UvArgs `
                              -NoNewWindow -Wait -PassThru `
                              -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        if ($proc.ExitCode -ne 0) {
            $tail = @()
            foreach ($f in @($errFile, $outFile)) {
                $tail += Get-Content $f -ErrorAction SilentlyContinue |
                         Where-Object { $_ -match '\S' } | Select-Object -Last 6
            }
            return @{ Ok = $false; Tail = ($tail | Select-Object -Last 8) }
        }
        return @{ Ok = $true; Tail = @() }
    } catch {
        return @{ Ok = $false; Tail = @($_.Exception.Message) }
    } finally {
        Remove-Item $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
}

function Install-NavigRuntime {
    # Build (or rebuild) the isolated runtime: uv -> pinned CPython -> venv.
    # Returns $true on success. The actual `navig` install is done separately.
    if (-not (Install-NavigUv)) { return $false }

    Write-NavVerbose "uv python install $PYTHON_SERIES"
    $r = Invoke-NavigUv -UvArgs @("python", "install", $PYTHON_SERIES)
    if (-not $r.Ok) { $r.Tail | ForEach-Object { Write-NavHint $_ }; return $false }

    if (-not (Test-Path $RUNTIME_VENV_PY)) {
        Write-NavVerbose "uv venv $RUNTIME_VENV"
        $r = Invoke-NavigUv -UvArgs @("venv", $RUNTIME_VENV, "--python", $PYTHON_SERIES)
        if (-not $r.Ok) { $r.Tail | ForEach-Object { Write-NavHint $_ }; return $false }
    }
    return $true
}

# ── PATH management ───────────────────────────────────────────
function Add-NavigBinToPath { param([string]$BinDir)
    if ([string]::IsNullOrWhiteSpace($BinDir) -or -not (Test-Path $BinDir)) { return }
    if ($env:PATH -notlike "*$BinDir*") {
        $env:PATH = "$BinDir;$env:PATH"
        Write-NavVerbose "Session PATH updated: $BinDir"
    }
    try {
        $u = [Environment]::GetEnvironmentVariable("PATH", "User")
        if ($u -notlike "*$BinDir*") {
            [Environment]::SetEnvironmentVariable("PATH", "$BinDir;$u", "User")
            Write-NavVerbose "User PATH persisted: $BinDir"
        }
    } catch {
        Write-Warn "PATH" "Could not persist to user registry: $($_.Exception.Message)"
    }
}

# ── Install navig into the managed runtime ────────────────────
function Install-Navig { param([string]$PinVersion)
    $spec = if ($PinVersion) { "navig[interactive]==$PinVersion" } else { "navig[interactive]" }
    $r = Invoke-NavigUv -UvArgs @("pip", "install", "--python", $RUNTIME_VENV_PY, "--upgrade", $spec)
    if (-not $r.Ok) { $r.Tail | ForEach-Object { Write-NavHint $_ }; return $false }
    return $true
}

# ── Launcher shim ─────────────────────────────────────────────
function New-NavigShim {
    # ~/.local/bin/navig.cmd -> the venv's navig.exe. A single stable PATH
    # entry that survives runtime rebuilds (the venv path is fixed), so updates
    # never churn PATH.
    if (-not (Test-Path $SHIM_DIR)) { New-Item -ItemType Directory -Path $SHIM_DIR -Force | Out-Null }
    $shim = @(
        '@echo off',
        'rem NAVIG launcher shim - generated by install.ps1',
        '"%USERPROFILE%\.navig\runtime\venv\Scripts\navig.exe" %*'
    ) -join "`r`n"
    try { Set-Content -Path $SHIM_PATH -Value $shim -Encoding ASCII; return (Test-Path $SHIM_PATH) }
    catch { return $false }
}

# ── Daemon supervision ────────────────────────────────────────
function Register-NavigDaemon {
    # Register the NAVIG daemon for auto-start (logon trigger + restart-on-failure)
    # via the runtime's own `navig service install --method task`. The service
    # manager launches the venv's own python, so it inherits the isolated runtime.
    # Best-effort: a failure here never fails the install. Set NAVIG_NO_DAEMON
    # to skip (e.g. CI / headless).
    if ($env:NAVIG_NO_DAEMON) { Write-NavVerbose "Skipping daemon registration (NAVIG_NO_DAEMON)"; return $false }
    $venvNavig = Join-Path $RUNTIME_VENV "Scripts\navig.exe"
    if (-not (Test-Path $venvNavig)) { return $false }
    $tmp = [IO.Path]::GetTempFileName()
    try {
        $proc = Start-Process -FilePath $venvNavig `
                              -ArgumentList @("service", "install", "--method", "task") `
                              -NoNewWindow -Wait -PassThru -RedirectStandardError $tmp
        return ($proc.ExitCode -eq 0)
    } catch {
        Write-NavVerbose "Daemon registration failed: $($_.Exception.Message)"
        return $false
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

# ── Verify ────────────────────────────────────────────────────
function Test-NavigCommand {
    $env:PATH = [Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("PATH", "User")    + ";" + $env:PATH
    # Prefer the venv exe directly (deterministic), fall back to PATH resolution.
    $venvNavig = Join-Path $RUNTIME_VENV "Scripts\navig.exe"
    $exe = if (Test-Path $venvNavig) { $venvNavig }
           else { $g = Get-Command navig -ErrorAction SilentlyContinue; if ($g) { $g.Source } else { $null } }
    if ($exe) {
        try {
            $v = (& $exe --version 2>&1 | Select-Object -First 1).ToString().Trim()
            return $v
        } catch {}
    }
    return $null
}

# ── Install state ─────────────────────────────────────────────
function Get-NavigInstallState {
    $installed = (Test-Path $INSTALL_MARKER_PATH) -or (Test-Path $INSTALL_REGISTRY_KEY)
    $meta = @{}
    if (Test-Path $INSTALL_REGISTRY_KEY) {
        try {
            $r = Get-ItemProperty -Path $INSTALL_REGISTRY_KEY -ErrorAction SilentlyContinue
            if ($r) { $meta = @{ Version = $r.Version; Method = $r.Method } }
        } catch {}
    }
    return @{ IsInstalled = $installed; Metadata = $meta }
}

function Write-NavigInstallState { param([string]$InstalledVersion)
    try {
        if (-not (Test-Path $INSTALL_REGISTRY_KEY)) { New-Item -Path $INSTALL_REGISTRY_KEY -Force | Out-Null }
        Set-ItemProperty -Path $INSTALL_REGISTRY_KEY -Name "Version"     -Value $InstalledVersion
        Set-ItemProperty -Path $INSTALL_REGISTRY_KEY -Name "InstallDate" -Value (Get-Date -Format "yyyy-MM-dd")
        Set-ItemProperty -Path $INSTALL_REGISTRY_KEY -Name "Method"      -Value "uv"
        $md = Split-Path $INSTALL_MARKER_PATH -Parent
        if (-not (Test-Path $md)) { New-Item -ItemType Directory -Path $md -Force | Out-Null }
        Set-Content -Path $INSTALL_MARKER_PATH -Value $InstalledVersion -Encoding UTF8
    } catch {}
}

function Remove-NavigInstallState {
    Remove-Item -Path $INSTALL_REGISTRY_KEY -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $INSTALL_MARKER_PATH  -Force         -ErrorAction SilentlyContinue
}

# ── Config dir ────────────────────────────────────────────────
function Initialize-NavigConfig {
    $base = Join-Path $env:USERPROFILE ".navig"
    foreach ($sub in @("", "workspace", "logs", "cache")) {
        $p = if ($sub) { Join-Path $base $sub } else { $base }
        if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
    }
    Write-NavVerbose "Config: $base\"
}

function Write-TerminalCapabilities {
    # Write ~\.navig\terminal.json with unicode/nerd_font capability flags.
    # nerd_font is set to false here; the terminal-setup onboarding step updates it.
    $base = Join-Path $env:USERPROFILE ".navig"
    $data = [ordered]@{
        unicode    = [bool]$script:NavUnicode
        nerd_font  = $false
        checked_at = (Get-Date -Format "o")
    }
    $json = $data | ConvertTo-Json -Compress
    try {
        [System.IO.File]::WriteAllText(
            (Join-Path $base "terminal.json"),
            $json,
            [System.Text.Encoding]::UTF8
        )
        Write-NavVerbose "Wrote terminal.json"
    } catch {
        Write-NavVerbose "Could not write terminal.json: $_"
    }
}


# ── Uninstall ─────────────────────────────────────────────────
$script:UninstallFailures = @()

function Reset-NavigUninstallState { $script:UninstallFailures = @() }

function Add-UninstallFailure { param([string]$Step, [string]$Message)
    $script:UninstallFailures += @{ Step = $Step; Message = $Message }
    Write-NavVerbose "Warning in '$Step': $Message"
}

function Split-PathEntries { param([string]$PathStr)
    if ([string]::IsNullOrWhiteSpace($PathStr)) { return @() }
    return ($PathStr -split ';') | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
}

function Stop-NavigBackgroundArtifacts {
    try { Get-Process navig -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
    $svc = Get-Service -Name $WINDOWS_SERVICE_NAME -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") {
        try { Stop-Service -Name $WINDOWS_SERVICE_NAME -Force -ErrorAction SilentlyContinue } catch {}
    }
}

function Remove-NavigFiles { param([switch]$PreserveUserData)
    # Always tear down the managed runtime + shim so reinstall is clean.
    foreach ($p in @($RUNTIME_DIR, $SHIM_PATH)) {
        if (Test-Path $p) {
            try { Remove-Item -Path $p -Recurse -Force -ErrorAction Stop; Write-NavVerbose "Removed: $p" }
            catch { Add-UninstallFailure "Remove $p" $_.Exception.Message }
        }
    }
    # User data (config, vault, data, logs) only on a full uninstall.
    $home_ = $NAVIG_HOME
    if ($PreserveUserData) { Write-NavVerbose "Preserving user data in $home_" }
    elseif (Test-Path $home_) {
        try { Remove-Item -Path $home_ -Recurse -Force -ErrorAction Stop; Write-NavVerbose "Removed: $home_" }
        catch { Add-UninstallFailure "Remove $home_" $_.Exception.Message }
    }
}

function Remove-NavigRegistryArtifacts {
    if (-not (Test-Path $INSTALL_REGISTRY_KEY) -and -not (Test-Path $INSTALL_MARKER_PATH)) {
        Write-NavVerbose "No registry state present"; return
    }
    try { Remove-NavigInstallState; Write-NavVerbose "Removed registry state" }
    catch { Add-UninstallFailure "Remove registry state" $_.Exception.Message }
}

function Test-NavigScheduledTask {
    try { return ($null -ne (Get-ScheduledTask -TaskName $WINDOWS_TASK_NAME -ErrorAction SilentlyContinue)) }
    catch { return $false }
}

function Remove-NavigServiceArtifacts {
    $svc = Get-Service -Name $WINDOWS_SERVICE_NAME -ErrorAction SilentlyContinue
    if ($svc) {
        try { & sc.exe stop   $WINDOWS_SERVICE_NAME 2>$null | Out-Null } catch {}
        try { & sc.exe delete $WINDOWS_SERVICE_NAME 2>$null | Out-Null; Write-NavVerbose "Removed service" }
        catch { Add-UninstallFailure "Remove service" $_.Exception.Message }
    }
    if (Test-NavigScheduledTask) {
        try { schtasks /delete /tn $WINDOWS_TASK_NAME /f 2>$null | Out-Null; Write-NavVerbose "Removed task" }
        catch { Add-UninstallFailure "Remove scheduled task" $_.Exception.Message }
    }
}

function Remove-NavigPathArtifacts {
    try {
        $raw  = [Environment]::GetEnvironmentVariable("PATH", "User")
        $kept = @(Split-PathEntries $raw) | Where-Object { $_ -notmatch '(?i)navig' -and $_ -notmatch '(?i)\.local\\bin' }
        if ($kept.Count -lt @(Split-PathEntries $raw).Count) {
            [Environment]::SetEnvironmentVariable("PATH", ($kept -join ';'), "User")
            Write-NavVerbose "Removed NAVIG PATH entries"
        }
    } catch { Add-UninstallFailure "Remove PATH entries" $_.Exception.Message }
}

function Invoke-NavigUninstall { param([switch]$PreserveUserData, [switch]$ForReinstall, [string]$Version = "")
    Reset-NavigUninstallState
    if (-not $ForReinstall) {
        $navStr = if ($Version) { "NAVIG $Version" } else { "NAVIG" }
        Write-Step "Removing" $navStr
    }
    Stop-NavigBackgroundArtifacts
    Write-Step "Removing files..."
    Remove-NavigFiles -PreserveUserData:$PreserveUserData
    Remove-NavigRegistryArtifacts
    Remove-NavigServiceArtifacts
    Write-Step "Cleaning PATH..."
    Remove-NavigPathArtifacts
    $ok = $script:UninstallFailures.Count -eq 0
    if (-not $ForReinstall) {
        if ($ok) { Write-Ok "Done" "NAVIG removed." }
        else     { Write-Warn "Done" "$($script:UninstallFailures.Count) warning(s)" }
    }
    return @{ Success = $ok; Failures = $script:UninstallFailures }
}

# ── Get installed version ──────────────────────────────────────
function Get-NavigVersion {
    try {
        $v = (navig --version 2>&1 | Select-Object -First 1).ToString()
        if ($v -match '(\d+\.\d+[\.\d]*)') { return $Matches[1] }
    } catch {}
    return ""
}

# ─────────────────────────────────────────────────────────────
# ── Main ─────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
function Main {
    $ErrorActionPreference = "Stop"
    if ($Help) { Show-Usage; return }

    Initialize-Terminal
    Print-Header

    $normalizedAction = ""
    try { $normalizedAction = Normalize-NavigAction $Action }
    catch {
        Print-Failure "Invalid argument" $_.Exception.Message "Use: install, uninstall, reinstall" ".\install.ps1 -Action install"
        exit 1
    }

    $installState = Get-NavigInstallState

    # ── Dry-run mode ─────────────────────────────────────────
    if ($DryRun) {
        Write-Host "  Dry run mode: no changes will be made."
        nl
        $verTarget = if ($Version) { $Version } elseif ($env:NAVIG_VERSION) { $env:NAVIG_VERSION } else { "latest" }
        Write-Host "  Would install: NAVIG $verTarget"
        Write-Host "  Would configure: PATH, shell completions"
        Write-Host ""
        Write-Host "  Dry run complete."
        return
    }

    # ── Uninstall ─────────────────────────────────────────────
    if ($normalizedAction -eq "uninstall") {
        $ver = $installState.Metadata.Version
        $result = Invoke-NavigUninstall -Version $ver
        exit $(if ($result.Success) { 0 } else { 1 })
    }

    # ── Already-installed menu ────────────────────────────────
    if ($installState.IsInstalled -and $normalizedAction -eq "" -and -not $NoConfirm -and -not $DryRun) {
        $ver  = $installState.Metadata.Version
        $vStr = if ($ver) { " $ver" } else { "" }
        clr "  NAVIG$vStr is already installed." "Cyan"; nl
        nl
        clr "    1" "Yellow"; clr "  Repair / Reinstall" "White"; nl
        clr "    2" "Yellow"; clr "  Uninstall"          "White"; nl
        clr "    3" "Yellow"; clr "  Cancel"              "White"; nl
        nl
        $choice = Read-Host "  Select [1-3]"
        switch ($choice.Trim()) {
            "1" { $normalizedAction = "reinstall" }
            "2" { $normalizedAction = "uninstall" }
            default { Write-Host "  Cancelled."; return }
        }
    }

    if ($normalizedAction -eq "uninstall") {
        $result = Invoke-NavigUninstall -Version $ver
        exit $(if ($result.Success) { 0 } else { 1 })
    }

    if ($normalizedAction -eq "reinstall" -and $installState.IsInstalled) {
        Write-NavVerbose "Reinstall: cleaning existing installation"
        Invoke-NavigUninstall -PreserveUserData -ForReinstall | Out-Null
        $installState = Get-NavigInstallState
    }

    # ── Environment ───────────────────────────────────────────
    Print-Section "Environment"
    $osv  = [Environment]::OSVersion.Version
    $arch = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
    Write-Ok "OS"    "Windows $($osv.Major).$($osv.Minor) $(sym 'bullet') $arch"
    $psv = $PSVersionTable.PSVersion
    Write-Ok "Shell" "PowerShell $($psv.Major).$($psv.Minor)"

    # ── Runtime ───────────────────────────────────────────────
    # NAVIG bundles its own pinned Python. Nothing is required up front and
    # the system Python is never detected, used, or modified.
    Print-Section "Runtime"
    Write-Step "Python" "preparing isolated runtime..."
    if (-not (Install-NavigRuntime)) {
        Print-Failure `
            "Could not prepare the NAVIG runtime" `
            "Failed to fetch uv or build the isolated Python $PYTHON_SERIES environment." `
            "Check your network / proxy and re-run. Your system Python is never touched." `
            "iwr -useb https://navig.run/install.ps1 | iex"
        exit 1
    }
    Write-Ok "Python" "$PYTHON_SERIES $(sym 'bullet') isolated (~/.navig/runtime)"

    # ── Install ───────────────────────────────────────────────
    Print-Section "Install"
    $spec = if ($Version) { "navig[interactive]==$Version" } else { "navig[interactive]" }
    Write-Step "navig" "installing $spec ..."
    $ok = Install-Navig -PinVersion $Version
    if (-not $ok) {
        Print-Failure `
            "navig install failed" `
            "uv exited with a non-zero code while installing navig into the runtime." `
            "Run the command below manually to see the full error output." `
            "$UV_EXE pip install --python `"$RUNTIME_VENV_PY`" --upgrade navig[interactive]"
        exit 1
    }
    Write-Ok "navig" "installed"

    if (New-NavigShim) {
        Add-NavigBinToPath -BinDir $SHIM_DIR
        Write-Ok "PATH" "$(sym 'bullet') $SHIM_DIR"
    } else {
        Write-Warn "PATH" "could not create launcher shim at $SHIM_PATH"
    }

    Initialize-NavigConfig
    Write-TerminalCapabilities

    # ── Optional: fzf (best picker UI) ───────────────────────
    if (-not (Get-Command fzf -ErrorAction SilentlyContinue)) {
        $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
        if ($hasWinget) {
            Write-Step "fzf" "installing via winget..."
            try {
                $r = Start-Process winget -ArgumentList @(
                    'install', '--id', 'junegunn.fzf',
                    '--silent', '--accept-source-agreements', '--accept-package-agreements'
                ) -NoNewWindow -Wait -PassThru -ErrorAction SilentlyContinue
                if ($r -and $r.ExitCode -eq 0) {
                    Write-Ok "fzf" "installed (best picker UI)"
                } else {
                    Write-NavHint "fzf optional — install manually: winget install junegunn.fzf"
                }
            } catch {
                Write-NavHint "fzf optional — install manually: winget install junegunn.fzf"
            }
        } else {
            Write-NavHint "fzf optional — install for the best picker UI: winget install junegunn.fzf"
        }
    } else {
        Write-Ok "fzf" "already installed"
    }

    # ── Verify ────────────────────────────────────────────────
    Print-Section "Verify"
    $navVer = Test-NavigCommand
    if (-not $navVer) {
        Print-Failure `
            "navig not callable" `
            "navig installed into the runtime but is not resolving on PATH in this session." `
            "Open a new terminal (PATH was just updated) and run 'navig --version'." `
            "& `"$RUNTIME_VENV\Scripts\navig.exe`" --version"
        exit 1
    }
    Write-Ok "navig" $navVer

    $cleanVer = if ($navVer -match '(\d+\.\d+[\.\d]*)') { $Matches[1] } else { $navVer }
    try { Write-NavigInstallState -InstalledVersion $cleanVer } catch {}

    # ── Daemon ────────────────────────────────────────────────
    Print-Section "Daemon"
    Write-Step "service" "registering auto-start..."
    if (Register-NavigDaemon) {
        Write-Ok "service" "auto-start enabled (Task Scheduler)"
    } else {
        Write-Warn "service" "skipped $(sym 'bullet') run 'navig service install --method task' later"
    }

    # ── NAVIG Anchor offer ────────────────────────────────────────────────────
    # Disabled until Anchor v1.0 ships publicly.
    # Future URL: https://github.com/navig-run/anchor/releases/latest
    # Re-enable: $env:NAVIG_OFFER_ANCHOR = "1"

    # ── Done ──────────────────────────────────────────────────
    Print-Done -Version $cleanVer
}

# ── Entry point ───────────────────────────────────────────────
if ($env:NAVIG_INSTALL_PS1_NO_RUN -ne "1") { Main }

# ── Dev sync (set NAVIG_DEV_SYNC=1) ──────────────────────────
if ($env:NAVIG_DEV_SYNC -eq "1") {
    $root   = if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) { $PWD.Path } else { $PSScriptRoot }
    $wwwDir = [IO.Path]::GetFullPath((Join-Path $root "..\navig-www\public"))
    if (Test-Path $wwwDir) {
        foreach ($f in @("install.ps1", "install.sh")) {
            $src = Join-Path $root $f
            $dst = Join-Path $wwwDir $f
            if (Test-Path $src) { Copy-Item $src $dst -Force; Write-Host "  Synced $f" }
        }
    }
}
