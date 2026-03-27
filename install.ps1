#Requires -Version 5.1
# ─────────────────────────────────────────────────────────────
# NAVIG Installer — Windows (PowerShell 5.1+)
# No Admin Visible In Graveyard · Keep your servers alive. Forever.
#
# Usage:
#   & ([scriptblock]::Create((irm https://navig.run/install.ps1)))
#   .\install.ps1 -Version 2.3.0
#   .\install.ps1 -Dev
#
# Environment variables:
#   NAVIG_VERSION             Pin version (e.g. "2.3.0")
#   NAVIG_INSTALL_METHOD      "pip" (default) or "git"
#   NAVIG_EXTRAS              Comma-separated extras (e.g. "voice,keyring")
#   NAVIG_TELEGRAM_BOT_TOKEN  Telegram bot token for automatic bot setup
# ─────────────────────────────────────────────────────────────
[CmdletBinding()]
param(
    [string]$Version        = $env:NAVIG_VERSION,
    [string]$InstallMethod  = $(if ($env:NAVIG_INSTALL_METHOD) { $env:NAVIG_INSTALL_METHOD } else { "pip" }),
    [string]$Extras         = $env:NAVIG_EXTRAS,
    [string]$TelegramToken  = $(if ($env:NAVIG_TELEGRAM_BOT_TOKEN) { $env:NAVIG_TELEGRAM_BOT_TOKEN } else { $env:TELEGRAM_BOT_TOKEN }),
    [string]$GitDir         = "$HOME\navig-core",
    [switch]$Dev,
    [switch]$Production,
    [switch]$DryRun,
    [switch]$NoConfirm,
    [switch]$Help
)

if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Error "PowerShell 5.1 or higher is required. Download: https://aka.ms/PSWindows"
    exit 1
}

# ── Encoding — set before any Write-Host so Unicode renders correctly ──────
$OutputEncoding           = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"

# ── Constants ──────────────────────────────────────────────────────────────
$REPO_URL         = "https://github.com/navig-run/core.git"
$MIN_PYTHON_MAJOR = 3
$MIN_PYTHON_MINOR = 10

# ── Output helpers ─────────────────────────────────────────────────────────
#   [->]  in-progress   [OK]  success   [!!]  failure   [i]  info
function Write-NavStep  { param([string]$msg) Write-Host "  " -NoNewline; Write-Host "[->]" -NoNewline -ForegroundColor Cyan;    Write-Host "  $msg" }
function Write-NavOk    { param([string]$msg) Write-Host "  " -NoNewline; Write-Host "[OK]" -NoNewline -ForegroundColor Green;   Write-Host "  $msg" }
function Write-NavErr   { param([string]$msg) Write-Host "  " -NoNewline; Write-Host "[!!]" -NoNewline -ForegroundColor Red;     Write-Host "  $msg" }
function Write-NavInfo  { param([string]$msg) Write-Host "  " -NoNewline; Write-Host "[i]"  -NoNewline -ForegroundColor DarkGray; Write-Host "  $msg" }
function Write-NavHint  { param([string]$msg) Write-Host "      $msg" -ForegroundColor Yellow }

# ── Live spinner ───────────────────────────────────────────────────────────
# Starts $Exe $ArgList as a child process, animates a spinner on the current
# line, then overwrites with a success/failure indicator. Returns exit code.
function Invoke-WithSpinner {
    param(
        [string]   $Label,
        [string]   $Exe,
        [string[]] $ArgList
    )

    $frames = @('|','/','-','\')   # ASCII spinner — works everywhere
    $pad    = "                                        "
    $tmpOut = [System.IO.Path]::GetTempFileName()
    $tmpErr = [System.IO.Path]::GetTempFileName()

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
        $code = $proc.ExitCode
    } catch {
        $code = 1
    }

    if ($code -eq 0) {
        Write-Host "`r  " -NoNewline
        Write-Host "[OK]" -NoNewline -ForegroundColor Green
        Write-Host "  $Label$pad"
    } else {
        Write-Host "`r  " -NoNewline
        Write-Host "[!!]" -NoNewline -ForegroundColor Red
        Write-Host "  $Label$pad"
        if ($VerbosePreference -ne 'SilentlyContinue') {
            Get-Content $tmpErr -ErrorAction SilentlyContinue |
                ForEach-Object { Write-Host "       $_" -ForegroundColor DarkGray }
        }
    }

    Remove-Item $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
    return $code
}

# ── Banner ─────────────────────────────────────────────────────────────────
function Show-Banner {
    $art = @(
        " _   _    _   __   ___ ____  "
        "| \ | |  / \ |\ \ / /|_ _/ ___|"
        "|  \| | / _ \| \ V /  | || |  _"
        "| |\  |/ ___ \  | |   | || |_| |"
        "|_| \_/_/   \_\ |_|  |___\____|"
    )
    $tagline = "Born in the terminal. Lives in the cloud."
    $inner   = 74
    $border  = "-" * $inner
    $blank   = " " * $inner

    Write-Host ""
    Write-Host "  +-$border-+" -ForegroundColor Cyan
    Write-Host "  | $blank |" -ForegroundColor Cyan

    foreach ($line in $art) {
        $padded = ("    " + $line).PadRight($inner)
        Write-Host "  |" -NoNewline -ForegroundColor Cyan
        Write-Host $padded -NoNewline -ForegroundColor White
        Write-Host "|" -ForegroundColor Cyan
    }

    Write-Host "  | $blank |" -ForegroundColor Cyan

    $tlen  = $tagline.Length
    $lpad  = [int](($inner - $tlen) / 2)
    $rpad  = $inner - $tlen - $lpad
    $tline = (" " * $lpad) + $tagline + (" " * $rpad)
    Write-Host "  |" -NoNewline -ForegroundColor Cyan
    Write-Host $tline -NoNewline -ForegroundColor DarkGray
    Write-Host "|" -ForegroundColor Cyan

    Write-Host "  | $blank |" -ForegroundColor Cyan
    Write-Host "  +-$border-+" -ForegroundColor Cyan
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
    .\install.ps1 [OPTIONS]

Options:
  -Version <ver>    Install specific version (e.g. 2.3.0)
  -Dev              Install from git source (dev mode)
  -GitDir <path>    Git checkout directory (default: $HOME\navig-core)
  -Extras <list>    Comma-separated extras: voice,keyring,dev
  -TelegramToken    Telegram bot token for auto-configuration
  -NoConfirm        Skip interactive prompts
  -DryRun           Preview actions without executing
  -Verbose          Show detailed output
  -Help             Show this help

Environment variables:
  NAVIG_VERSION             Pin version
  NAVIG_INSTALL_METHOD      pip (default) or git
  NAVIG_EXTRAS              Comma-separated extras
  NAVIG_TELEGRAM_BOT_TOKEN  Telegram bot token for auto setup
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
    Write-NavStep "Python not found — installing via winget..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
            $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
            Write-NavOk "Python installed via winget"
            return $true
        } catch {
            Write-NavStep "winget failed — falling back to direct download..."
        }
    }
    Write-NavStep "Downloading Python 3.12 installer..."
    $installerUrl  = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
    $installerPath = Join-Path $env:TEMP "python-installer.exe"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
        Write-NavStep "Running Python installer (silent)..."
        Start-Process -FilePath $installerPath -ArgumentList "/quiet","InstallAllUsers=0","PrependPath=1","Include_test=0" -Wait
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
        Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
        Write-NavOk "Python installed"
        return $true
    } catch {
        Write-NavErr "Failed to install Python automatically"
        Write-NavHint "Install manually from https://python.org then re-run this script"
        return $false
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
    Write-NavInfo "SSH client not found — enable OpenSSH in Settings > Optional Features"
    return $true   # non-blocking: navig uses paramiko fallback
}

# ── Install via pip (with live spinner) ───────────────────────────────────
function Install-NavigPip {
    param([string]$PipCmd)

    $installSpec = "navig"
    if ($Version) { $installSpec = "navig==$Version" }
    if ($Extras)  { $installSpec = "navig[$Extras]"; if ($Version) { $installSpec = "navig[$Extras]==$Version" } }

    $pipParts = $PipCmd -split ' '
    $exe      = $pipParts[0]
    $baseArgs = if ($pipParts.Length -gt 1) { $pipParts[1..($pipParts.Length-1)] } else { @() }
    $fullArgs = $baseArgs + @("install","--upgrade",$installSpec)

    $code = Invoke-WithSpinner -Label "Installing NAVIG  ($installSpec)" -Exe $exe -ArgList $fullArgs
    if ($code -ne 0) {
        Write-NavHint "Manual fallback:  pip install $installSpec"
        exit 1
    }
}

# ── Install via git ───────────────────────────────────────────────────────
function Install-NavigGit {
    param([string]$PipCmd)

    $repoDir = $GitDir

    if (Test-Path "$repoDir\.git") {
        Write-NavStep "Updating existing checkout: $repoDir"
    } else {
        Write-NavStep "Cloning NAVIG from: $REPO_URL"
    }

    if (-not (Find-Git)) {
        if (-not (Install-GitWindows)) { exit 1 }
    }

    if (-not (Test-Path $repoDir)) {
        git clone $REPO_URL $repoDir
    } else {
        $dirty = git -C $repoDir status --porcelain 2>$null
        if (-not $dirty) { git -C $repoDir pull --rebase 2>$null }
        else             { Write-NavInfo "Repo has local changes — skipping git pull" }
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
    $configDir = "$env:USERPROFILE\.navig"
    foreach ($sub in @("","workspace","logs","cache")) {
        $path = if ($sub) { Join-Path $configDir $sub } else { $configDir }
        if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
    }
    Write-NavOk "Config directory ready at $configDir\"
}

function Configure-Telegram {
    if (-not $TelegramToken) { return }
    $configDir = "$env:USERPROFILE\.navig"
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null

    $envFile = Join-Path $configDir ".env"
    "TELEGRAM_BOT_TOKEN=$TelegramToken" | Set-Content -Encoding UTF8 $envFile

    [Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", $TelegramToken, "User")
    $env:TELEGRAM_BOT_TOKEN = $TelegramToken

    $configFile = Join-Path $configDir "config.yaml"
    if (-not (Test-Path $configFile)) {
@"
telegram:
  bot_token: "$TelegramToken"
  allowed_users: []
  allowed_groups: []
  session_isolation: true
  group_activation_mode: "mention"
"@ | Set-Content -Encoding UTF8 $configFile
    }
    Write-NavOk "Telegram token configured"
}

function Start-TelegramDaemon {
    if (-not $TelegramToken) { return }
    try { navig service install --bot --gateway --scheduler --no-start | Out-Null } catch {}
    try { navig service start | Out-Null } catch {}
    Write-NavOk "Telegram daemon start attempted"
}

# ── Existing installation check ───────────────────────────────────────────
function Test-ExistingNavig {
    if (Get-Command navig -ErrorAction SilentlyContinue) {
        try {
            $ver = navig --version 2>&1 | Select-Object -First 1
            Write-NavInfo "Existing NAVIG detected: $ver — upgrading"
        } catch {
            Write-NavInfo "Existing NAVIG detected — upgrading"
        }
        return $true
    }
    return $false
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

    Write-NavInfo "navig installed — restart your terminal to pick up PATH changes"
    return $false
}

# ── Resolve installed version ─────────────────────────────────────────────
function Get-NavigVersion {
    try {
        $line = (pip show navig 2>$null) | Select-String 'Version:'
        if ($line) { return ($line -replace 'Version:\s*','').Trim() }
    } catch {}
    try {
        $ver = navig --version 2>&1 | Select-Object -First 1
        return ($ver -replace '[^\d\.]','') -replace '^\.+',''
    } catch {}
    return ""
}

# ── Main ──────────────────────────────────────────────────────────────────
function Main {
    if ($Help) { Show-Usage; return }
    if ($Dev)  { $InstallMethod = "git" }

    Show-Banner

    if ($DryRun) {
        Write-NavInfo "Dry run — no changes will be made"
        Write-Host ""
        Write-Host "  OS:              Windows $([System.Environment]::OSVersion.Version)" -ForegroundColor DarkGray
        Write-Host "  Install method:  $InstallMethod"                                     -ForegroundColor DarkGray
        Write-Host "  Version:         $(if ($Version) { $Version } else { 'latest' })"   -ForegroundColor DarkGray
        Write-Host "  Extras:          $(if ($Extras)  { $Extras  } else { 'none'   })"   -ForegroundColor DarkGray
        Write-Host "  Telegram:        $(if ($TelegramToken) { 'enabled' } else { 'disabled' })" -ForegroundColor DarkGray
        Write-Host "  Git dir:         $GitDir"                                             -ForegroundColor DarkGray
        Write-Host "  Config dir:      $env:USERPROFILE\.navig\"                           -ForegroundColor DarkGray
        Write-Host ""
        Write-NavInfo "Dry run complete — nothing was changed"
        return
    }

    # Step 1: OS
    $osVer = [System.Environment]::OSVersion.Version
    Write-NavOk "Windows $osVer ($env:PROCESSOR_ARCHITECTURE)"

    # Step 2: Existing install?
    $isUpgrade = Test-ExistingNavig

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
        Write-NavStep "pip not found — bootstrapping via ensurepip..."
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

    # Step 8: Optional Telegram
    Configure-Telegram
    Start-TelegramDaemon

    # Step 9: Verify PATH
    Test-NavigInstall | Out-Null

    # Step 10: Success
    $installedVer = Get-NavigVersion
    Show-SuccessBanner -InstalledVersion $installedVer

    $gitSource = if ($InstallMethod -eq "git") { $GitDir } else { $null }
    Show-GetStarted -PipCmd $pipCmd -GitSource $gitSource
}

# ── Entry point ───────────────────────────────────────────────────────────
Main

# ── Developer sync (set $env:NAVIG_DEV_SYNC=1 to activate) ───────────────
if ($env:NAVIG_DEV_SYNC -eq "1") {
    $wwwDir = Join-Path $PSScriptRoot "..\navig-www\public"
    if (-not (Test-Path $wwwDir)) {
        Write-NavErr "navig-www/public directory not found at: $(Resolve-Path $wwwDir -ErrorAction SilentlyContinue)"
        Write-NavHint "Copy manually:  Copy-Item install.ps1 ..\navig-www\public\install.ps1"
        exit 1
    }
    foreach ($f in @("install.ps1", "install.sh", "uninstall.ps1", "uninstall.sh")) {
        $src = Join-Path $PSScriptRoot $f
        $dst = Join-Path $wwwDir $f
        if (Test-Path $src) {
            Copy-Item -Path $src -Destination $dst -Force
            Write-NavOk "Synced $f -> navig-www"
        }
    }
}
