# ─────────────────────────────────────────────────────────────
# NAVIG Installer — Windows (PowerShell 5.1+)
# No Admin Visible In Graveyard · Keep your servers alive. Forever.
#
# Usage:
#   irm https://navig.run/install.ps1 | iex
#   .\install.ps1 -Version 2.3.0
#   .\install.ps1 -Dev
#
# Environment variables:
#   NAVIG_VERSION          Pin version (e.g. "2.3.0")
#   NAVIG_INSTALL_METHOD   "pip" (default) or "git"
#   NAVIG_EXTRAS           Comma-separated extras (e.g. "voice,keyring")
#   NAVIG_TELEGRAM_BOT_TOKEN  Telegram bot token for automatic bot setup
# ─────────────────────────────────────────────────────────────
[CmdletBinding()]
param(
    [string]$Version = $env:NAVIG_VERSION,
    [string]$InstallMethod = $(if ($env:NAVIG_INSTALL_METHOD) { $env:NAVIG_INSTALL_METHOD } else { "pip" }),
    [string]$Extras = $env:NAVIG_EXTRAS,
    [string]$TelegramToken = $(if ($env:NAVIG_TELEGRAM_BOT_TOKEN) { $env:NAVIG_TELEGRAM_BOT_TOKEN } else { $env:TELEGRAM_BOT_TOKEN }),
    [string]$GitDir = "$HOME\navig-core",
    [switch]$Dev,
    [switch]$Production,
    [switch]$DryRun,
    [switch]$NoConfirm,
    [switch]$Verbose,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

# ── Constants ─────────────────────────────────────────────────
$REPO_URL = "https://github.com/navig-run/core.git"
$MIN_PYTHON_MAJOR = 3
$MIN_PYTHON_MINOR = 10

# ── Taglines ──────────────────────────────────────────────────
$TAGLINES = @(
    "Your servers are in good hands now."
    "No admin visible in graveyard? Perfect."
    "SSH tunnels, remote ops - all in one CLI."
    "Because server management shouldn't feel like surgery."
    "Keeping uptime personal since 2024."
    "One CLI to rule them all."
    "Remote ops, local comfort."
    "Born in the terminal. Lives in the cloud."
    "Your devops sidekick. No cape required."
    "Deploy, manage, survive. Repeat."
    "Less SSH, more SHH - it just works."
    "The quiet guardian of your infrastructure."
    "Admin by day, daemon by night."
)

function Get-Tagline {
    $TAGLINES | Get-Random
}

# ── Output helpers ────────────────────────────────────────────
function Write-Step  { param([string]$msg) Write-Host "  -> " -NoNewline -ForegroundColor Yellow; Write-Host $msg }
function Write-Ok    { param([string]$msg) Write-Host "  OK " -NoNewline -ForegroundColor Green; Write-Host $msg }
function Write-Err   { param([string]$msg) Write-Host "  ERR " -NoNewline -ForegroundColor Red; Write-Host $msg }
function Write-Info  { param([string]$msg) Write-Host "  i  " -NoNewline -ForegroundColor Cyan; Write-Host $msg }

# ── Banner ────────────────────────────────────────────────────
function Show-Banner {
    Write-Host ""
    Write-Host @"
    ╔╗╔┌─┐┬  ┬┬┌─┐
    ║║║├─┤└┐┌┘││ ┬
    ╝╚╝┴ ┴ └┘ ┴└─┘
"@ -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  $(Get-Tagline)" -ForegroundColor DarkGray
    Write-Host ""
}

# ── Usage ─────────────────────────────────────────────────────
function Show-Usage {
    Write-Host @"
NAVIG Installer for Windows

Usage:
  irm https://navig.run/install.ps1 | iex
  .\install.ps1 [OPTIONS]

Options:
  -Version <ver>    Install specific version (e.g. 2.3.0)
  -Dev              Install from git source (dev mode)
  -GitDir <path>    Git checkout directory (default: ~/navig-core)
  -Extras <list>    Comma-separated extras: voice,keyring,dev
    -TelegramToken    Telegram bot token for auto-configuration
  -NoConfirm        Skip interactive prompts
  -DryRun           Preview actions without executing
  -Verbose          Show detailed output
  -Help             Show this help

Environment variables:
  NAVIG_VERSION          Pin version
  NAVIG_INSTALL_METHOD   pip (default) or git
  NAVIG_EXTRAS           Comma-separated extras
    NAVIG_TELEGRAM_BOT_TOKEN  Telegram bot token for auto setup
"@
}

# ── Python Detection ──────────────────────────────────────────
function Find-Python {
    # Prefer known-good user-installed Python paths over system/server paths
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
                        Write-Ok "Python $verOutput found at $p"
                        return $p
                    }
                }
            } catch {}
        }
    }
    $candidates = @("python", "python3", "py -3")

    foreach ($cmd in $candidates) {
        try {
            $parts = $cmd -split ' '
            $exe = $parts[0]
            $args = if ($parts.Length -gt 1) { $parts[1..($parts.Length-1)] } else { @() }

            $verOutput = if ($args.Length -gt 0) {
                & $exe @args --version 2>&1
            } else {
                & $exe --version 2>&1
            }

            if ($verOutput -match '(\d+)\.(\d+)\.(\d+)') {
                $major = [int]$Matches[1]
                $minor = [int]$Matches[2]
                if ($major -ge $MIN_PYTHON_MAJOR -and $minor -ge $MIN_PYTHON_MINOR) {
                    Write-Ok "Python $verOutput found"
                    return $cmd
                }
            }
        } catch {
            # Command not found, try next
        }
    }
    return $null
}

function Find-Pip {
    param([string]$PythonCmd)

    # Try python -m pip first
    try {
        $parts = $PythonCmd -split ' '
        $exe = $parts[0]
        $args = if ($parts.Length -gt 1) { $parts[1..($parts.Length-1)] + @("-m", "pip", "--version") } else { @("-m", "pip", "--version") }
        $out = & $exe @args 2>&1
        if ($LASTEXITCODE -eq 0) {
            return "$PythonCmd -m pip"
        }
    } catch {}

    # Try standalone pip
    foreach ($cmd in @("pip3", "pip")) {
        try {
            & $cmd --version 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return $cmd
            }
        } catch {}
    }
    return $null
}

# ── Python Installation ──────────────────────────────────────
function Install-PythonWindows {
    Write-Step "Python not found. Installing via winget..."

    # Try winget first
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
            # Refresh PATH
            $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
            Write-Ok "Python installed via winget"
            return $true
        } catch {
            Write-Step "winget install failed, trying direct download..."
        }
    }

    # Direct download fallback
    Write-Step "Downloading Python installer..."
    $installerUrl = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
    $installerPath = Join-Path $env:TEMP "python-installer.exe"

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing

        Write-Step "Running Python installer (silent)..."
        Start-Process -FilePath $installerPath -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0" -Wait

        # Refresh PATH
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")

        Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
        Write-Ok "Python installed"
        return $true
    } catch {
        Write-Err "Failed to install Python. Please install manually from https://python.org"
        return $false
    }
}

# ── Git Detection & Installation ─────────────────────────────
function Find-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Ok "Git already installed"
        return $true
    }
    return $false
}

function Install-GitWindows {
    Write-Step "Installing Git..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
            $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
            Write-Ok "Git installed via winget"
            return $true
        } catch {
            Write-Err "Git installation failed. Install manually from https://git-scm.com"
            return $false
        }
    }
    Write-Err "winget not available. Install Git manually from https://git-scm.com"
    return $false
}

# ── SSH Check ─────────────────────────────────────────────────
function Test-SSH {
    if (Get-Command ssh -ErrorAction SilentlyContinue) {
        Write-Ok "SSH client available"
        return $true
    }
    Write-Info "SSH client not found. Windows 10+ includes OpenSSH - enable it in Settings > Optional Features"
    return $true  # Non-blocking; navig can use paramiko fallback
}

# ── Install via pip ───────────────────────────────────────────
function Install-NavigPip {
    param([string]$PipCmd)

    $installSpec = "navig"
    if ($Version) {
        $installSpec = "navig==$Version"
    }
    if ($Extras) {
        $installSpec = "navig[$Extras]"
        if ($Version) { $installSpec = "navig[$Extras]==$Version" }
    }

    Write-Step "Installing NAVIG via pip: $installSpec"

    $pipParts = $PipCmd -split ' '
    $exe = $pipParts[0]
    $pipArgs = @()
    if ($pipParts.Length -gt 1) {
        $pipArgs += $pipParts[1..($pipParts.Length-1)]
    }
    $pipArgs += @("install", "--upgrade", $installSpec)

    & $exe @pipArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Err "pip install failed"
        Write-Host "  Try manually: pip install $installSpec" -ForegroundColor Cyan
        exit 1
    }

    Write-Ok "NAVIG installed via pip"
}

# ── Install via git ───────────────────────────────────────────
function Install-NavigGit {
    param([string]$PipCmd)

    $repoDir = $GitDir

    if (Test-Path "$repoDir\.git") {
        Write-Step "Updating existing checkout: $repoDir"
    } else {
        Write-Step "Cloning NAVIG from: $REPO_URL"
    }

    if (-not (Find-Git)) {
        if (-not (Install-GitWindows)) { exit 1 }
    }

    if (-not (Test-Path $repoDir)) {
        git clone $REPO_URL $repoDir
    } else {
        $dirty = git -C $repoDir status --porcelain 2>$null
        if (-not $dirty) {
            git -C $repoDir pull --rebase 2>$null
        } else {
            Write-Step "Repo is dirty; skipping git pull"
        }
    }

if ($Production) {
        Write-Step "Installing NAVIG from source (production mode — no editable install)..."
    } else {
        Write-Step "Installing NAVIG in editable mode..."
    }
    $pipParts = $PipCmd -split ' '
    $exe = $pipParts[0]
    $pipArgs = @()
    if ($pipParts.Length -gt 1) { $pipArgs += $pipParts[1..($pipParts.Length-1)] }

    if ($Production) {
        # Non-editable: no __editable__ finder overhead (~20ms startup savings)
        if ($Extras) {
            $pipArgs += @("install", "${repoDir}[$Extras]")
        } else {
            $pipArgs += @("install", $repoDir)
        }
    } elseif ($Extras) {
        $pipArgs += @("install", "-e", "${repoDir}[$Extras]")
    } else {
        $pipArgs += @("install", "-e", $repoDir)
    }

    & $exe @pipArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Editable install failed"
        exit 1
    }

    Write-Ok "NAVIG installed from source"
    Write-Info "Source: $repoDir"
}

# ── Config directory setup ────────────────────────────────────
function Initialize-NavigConfig {
    $configDir = Join-Path $HOME ".navig"
    $dirs = @("workspace", "logs", "cache")

    if (-not (Test-Path $configDir)) {
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    }
    foreach ($d in $dirs) {
        $sub = Join-Path $configDir $d
        if (-not (Test-Path $sub)) {
            New-Item -ItemType Directory -Path $sub -Force | Out-Null
        }
    }
    Write-Ok "Config directory: $configDir"
}

function Configure-Telegram {
    if (-not $TelegramToken) { return }

    $configDir = Join-Path $HOME ".navig"
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

    Write-Ok "Telegram token configured"
}

function Start-TelegramDaemon {
    if (-not $TelegramToken) { return }

    try { navig service install --bot --gateway --scheduler --no-start | Out-Null } catch {}
    try { navig service start | Out-Null } catch {}
    Write-Ok "Telegram daemon start attempted"
}

# ── Check existing installation ───────────────────────────────
function Test-ExistingNavig {
    if (Get-Command navig -ErrorAction SilentlyContinue) {
        try {
            $ver = navig --version 2>&1 | Select-Object -First 1
            Write-Step "Existing NAVIG detected: $ver"
        } catch {
            Write-Step "Existing NAVIG detected (version unknown)"
        }
        return $true
    }
    return $false
}

# ── Verify installation ──────────────────────────────────────
function Test-NavigInstall {
    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" + $env:PATH

    if (Get-Command navig -ErrorAction SilentlyContinue) {
        Write-Ok "navig command available"
        return $true
    }

    # Check common pip install locations
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
        "C:\Python313\Scripts",
    )

    foreach ($p in $pipPaths) {
        if (Test-Path (Join-Path $p "navig.exe")) {
            $env:PATH = "$p;$env:PATH"
            Write-Ok "navig found at: $p"
            Write-Info "Add to PATH permanently: [Environment]::SetEnvironmentVariable('PATH', `"$p;`" + [Environment]::GetEnvironmentVariable('PATH', 'User'), 'User')"
            return $true
        }
    }

    Write-Step "navig installed but not found on PATH"
    Write-Info "You may need to restart your terminal"
    return $false
}

# ── Get installed version ─────────────────────────────────────
function Get-NavigVersion {
    try {
        $ver = navig --version 2>&1 | Select-Object -First 1
        return $ver -replace '[^\d\.]', '' -replace '^\.'
    } catch {
        return ""
    }
}

# ── Main ──────────────────────────────────────────────────────
function Main {
    if ($Help) {
        Show-Usage
        return
    }

    if ($Dev) { $InstallMethod = "git" }

    Show-Banner

    if ($DryRun) {
        Write-Info "Dry run mode - no changes will be made"
        Write-Host "  OS:              Windows $([System.Environment]::OSVersion.Version)" -ForegroundColor DarkGray
        Write-Host "  Install method:  $InstallMethod" -ForegroundColor DarkGray
        Write-Host "  Version:         $(if ($Version) { $Version } else { 'latest' })" -ForegroundColor DarkGray
        Write-Host "  Extras:          $(if ($Extras) { $Extras } else { 'none' })" -ForegroundColor DarkGray
        Write-Host "  Telegram:        $(if ($TelegramToken) { 'enabled' } else { 'disabled' })" -ForegroundColor DarkGray
        Write-Host "  Git dir:         $GitDir" -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "  Dry run complete." -ForegroundColor DarkGray
        return
    }

    # Step 0: OS info
    Write-Ok "Windows $([System.Environment]::OSVersion.Version) ($env:PROCESSOR_ARCHITECTURE)"

    # Step 1: Check existing
    $isUpgrade = Test-ExistingNavig

    # Step 2: Python
    $pythonCmd = Find-Python
    if (-not $pythonCmd) {
        if (-not (Install-PythonWindows)) {
            Write-Err "Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ is required"
            exit 1
        }
        $pythonCmd = Find-Python
        if (-not $pythonCmd) {
            Write-Err "Python still not found after install. Restart terminal and try again."
            exit 1
        }
    }

    # Step 3: pip
    $pipCmd = Find-Pip -PythonCmd $pythonCmd
    if (-not $pipCmd) {
        Write-Step "Installing pip..."
        $parts = $pythonCmd -split ' '
        & $parts[0] -m ensurepip --upgrade 2>$null
        $pipCmd = Find-Pip -PythonCmd $pythonCmd
        if (-not $pipCmd) {
            Write-Err "pip is required but could not be installed"
            exit 1
        }
    }
    Write-Ok "pip available"

    # Step 4: SSH
    Test-SSH | Out-Null

    # Step 5: Install NAVIG
    if ($InstallMethod -eq "git") {
        Install-NavigGit -PipCmd $pipCmd
    } else {
        Install-NavigPip -PipCmd $pipCmd
    }

    # Step 6: Config
    Initialize-NavigConfig

    # Step 6.5: Optional Telegram setup
    Configure-Telegram

    # Step 6.6: Optional daemon start
    Start-TelegramDaemon

    # Step 7: Verify
    Test-NavigInstall | Out-Null
    $installedVer = Get-NavigVersion

    # ── Success ───────────────────────────────────────────────
    Write-Host ""
    if ($installedVer) {
        Write-Host "  NAVIG installed successfully (v$installedVer)!" -ForegroundColor Green
    } else {
        Write-Host "  NAVIG installed successfully!" -ForegroundColor Green
    }

    if ($isUpgrade) {
        $msgs = @(
            "Upgraded and operational. Your servers barely noticed."
            "New version, same mission. Keeping things alive."
            "Patched and ready. Your infrastructure thanks you."
        )
        Write-Host "  $($msgs | Get-Random)" -ForegroundColor DarkGray
    } else {
        $msgs = @(
            "Welcome aboard. Let's keep those servers alive."
            "Ready to go. Run 'navig' to get started."
            "Your devops workflow just leveled up."
        )
        Write-Host "  $($msgs | Get-Random)" -ForegroundColor DarkGray
    }

    Write-Host ""
    Write-Host "  Get started:" -ForegroundColor White
    Write-Host "    navig                    Open interactive menu" -ForegroundColor Cyan
    Write-Host "    navig host add           Add your first server" -ForegroundColor Cyan
    Write-Host "    navig help               Show available commands" -ForegroundColor Cyan
    Write-Host ""

    if ($InstallMethod -eq "git") {
        Write-Host "  Source: $GitDir" -ForegroundColor Cyan
        Write-Host "  Update: cd $GitDir; git pull; pip install -e ." -ForegroundColor Cyan
    } else {
        Write-Host "  Update: pip install --upgrade navig" -ForegroundColor Cyan
    }

    Write-Host "  Config: ~/.navig/" -ForegroundColor Cyan
    Write-Host "  Docs:   https://github.com/navig-run/core" -ForegroundColor Cyan
    Write-Host ""
}

# ── Entry point ───────────────────────────────────────────────
Main
