#Requires -Version 5.1
# NAVIG Installer - Windows (PowerShell 5.1+)
#
# Usage:
#   iwr -useb https://navig.run/install.ps1 | iex
#   .\install.ps1 [-Version <ver>] [-Action install|uninstall|reinstall] [-Verbose]
#
# Environment:
#   NAVIG_VERSION      Pin version (e.g. "2.4.14")
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
$MIN_PYTHON_MAJOR     = 3
$MIN_PYTHON_MINOR     = 10
$INSTALL_REGISTRY_KEY = "Registry::HKEY_CURRENT_USER\Software\NAVIG\Installer"
$INSTALL_MARKER_PATH  = Join-Path $env:USERPROFILE ".navig\install.marker"
$WINDOWS_SERVICE_NAME = "NavigDaemon"
$WINDOWS_TASK_NAME    = "NAVIG Daemon"

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

function hline { param([int]$w) return (sym "hz") * $w }

function Print-Header {
    $lw   = $script:LW
    $line = hline ($lw + 2)
    $tl = sym "tl"; $tr = sym "tr"; $bl = sym "bl"; $br = sym "br"; $vt = sym "vt"
    nl
    clr "  $tl$line$tr" "DarkGray"; nl
    clr "  $vt" "DarkGray"; nl
    clr "  $vt" "DarkGray"; clr "   NAVIG" "Cyan"; nl
    clr "  $vt" "DarkGray"; clr "   quiet operator tooling for real systems" "DarkGray"; nl
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
    clr "  $vt" "Green"; clr "  $($verStr.PadRight($lw))  " "White"; clr $vt "Green"; nl
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
  -Version <ver>   Install specific version (e.g. 2.4.14)
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

# ── Python detection ──────────────────────────────────────────
function Find-Python {
    $knownPaths = @(
        (Join-Path $HOME "AppData\Local\Programs\Python\Python314-32\python.exe"),
        (Join-Path $HOME "AppData\Local\Programs\Python\Python314\python.exe"),
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
                if ($maj -gt $MIN_PYTHON_MAJOR -or ($maj -eq $MIN_PYTHON_MAJOR -and $min -ge $MIN_PYTHON_MINOR)) { return $p }
            }
        } catch {}
    }
    foreach ($cmd in @("python", "python3")) {
        try {
            $out = & $cmd --version 2>&1
            if ($out -match '(\d+)\.(\d+)') {
                $maj = [int]$Matches[1]; $min = [int]$Matches[2]
                if ($maj -gt $MIN_PYTHON_MAJOR -or ($maj -eq $MIN_PYTHON_MAJOR -and $min -ge $MIN_PYTHON_MINOR)) {
                    $r = Get-Command $cmd -ErrorAction SilentlyContinue
                    return $(if ($r -and $r.Source) { $r.Source } else { $cmd })
                }
            }
        } catch {}
    }
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

function Get-PythonScriptsDir { param([string]$PythonExe)
    try {
        $d = (& $PythonExe -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>&1).Trim()
        if ($d -and (Test-Path $d)) { return $d }
    } catch {}
    $c = Join-Path (Split-Path $PythonExe -Parent) "Scripts"
    if (Test-Path $c) { return $c }
    return $null
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

# ── pip install ───────────────────────────────────────────────
function Install-Navig { param([string]$PythonExe, [string]$PinVersion)
    $spec    = if ($PinVersion) { "navig==$PinVersion" } else { "navig" }
    $pipArgs = @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--upgrade", $spec)
    $tmp     = [IO.Path]::GetTempFileName()
    try {
        $proc = Start-Process -FilePath $PythonExe -ArgumentList $pipArgs `
                              -NoNewWindow -Wait -PassThru -RedirectStandardError $tmp
        if ($proc.ExitCode -ne 0) {
            $errs = Get-Content $tmp -ErrorAction SilentlyContinue |
                    Where-Object { $_ -match '\S' } | Select-Object -Last 8
            $errs | ForEach-Object { Write-NavHint $_ }
            return $false
        }
        return $true
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

# ── Verify ────────────────────────────────────────────────────
function Test-NavigCommand { param([string]$ScriptsDir)
    $env:PATH = [Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("PATH", "User")    + ";" + $env:PATH
    $gcm = Get-Command navig -ErrorAction SilentlyContinue
    if (-not $gcm -and $ScriptsDir) {
        $e = Join-Path $ScriptsDir "navig.exe"
        if (Test-Path $e) { $gcm = $e }
    }
    if ($gcm) {
        try {
            $v = (& navig --version 2>&1 | Select-Object -First 1).ToString().Trim()
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
        Set-ItemProperty -Path $INSTALL_REGISTRY_KEY -Name "Method"      -Value "pip"
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
    foreach ($pip in @("pip3", "pip")) {
        if (-not (Get-Command $pip -ErrorAction SilentlyContinue)) { continue }
        try { & $pip uninstall navig -y 2>&1 | Out-Null; Write-NavVerbose "Removed pip package: navig"; break } catch {}
    }
    $home_ = Join-Path $env:USERPROFILE ".navig"
    if ($PreserveUserData) { Write-NavVerbose "Preserving $home_" }
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

function Invoke-NavigUninstall { param([switch]$PreserveUserData, [switch]$ForReinstall)
    Reset-NavigUninstallState
    if (-not $ForReinstall) { Write-Step "Uninstalling" "NAVIG" }
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

    # ── Uninstall ─────────────────────────────────────────────
    if ($normalizedAction -eq "uninstall") {
        $result = Invoke-NavigUninstall
        exit $(if ($result.Success) { 0 } else { 1 })
    }

    # ── Already-installed menu ────────────────────────────────
    if ($installState.IsInstalled -and $normalizedAction -eq "" -and -not $NoConfirm) {
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
        $result = Invoke-NavigUninstall
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

    # ── Requirements ──────────────────────────────────────────
    Print-Section "Requirements"
    Write-Step "Python" "detecting..."
    $pythonExe = Find-Python
    if (-not $pythonExe) {
        Print-Failure `
            "Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ not found" `
            "No Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR or higher was detected on this system." `
            "Download from python.org and enable 'Add Python to PATH' during setup." `
            "Start-Process 'https://www.python.org/downloads'"
        exit 1
    }
    $pyVerStr = (& $pythonExe --version 2>&1).ToString().Trim() -replace '^Python\s*', ''
    Write-Ok "Python" $pyVerStr
    Write-NavVerbose $pythonExe

    # ── Install ───────────────────────────────────────────────
    Print-Section "Install"
    $spec = if ($Version) { "navig==$Version" } else { "navig" }
    Write-Step "navig" "pip install $spec ..."
    $ok = Install-Navig -PythonExe $pythonExe -PinVersion $Version
    if (-not $ok) {
        Print-Failure `
            "pip install failed" `
            "pip exited with a non-zero code while installing navig." `
            "Run the command below manually to see the full error output." `
            "$pythonExe -m pip install --upgrade navig"
        exit 1
    }
    Write-Ok "navig" "installed"

    $scriptsDir = Get-PythonScriptsDir -PythonExe $pythonExe
    if ($scriptsDir) {
        Add-NavigBinToPath -BinDir $scriptsDir
        Write-Ok "PATH" "$(sym 'bullet') $scriptsDir"
    }

    Initialize-NavigConfig

    # ── Verify ────────────────────────────────────────────────
    Print-Section "Verify"
    $navVer = Test-NavigCommand -ScriptsDir $scriptsDir
    if (-not $navVer) {
        Print-Failure `
            "navig not callable" `
            "The navig executable was installed but is not on PATH in this session." `
            "Add the Scripts directory to your PATH, then open a new terminal." `
            "`$env:PATH = '$scriptsDir;' + `$env:PATH"
        exit 1
    }
    Write-Ok "navig" $navVer

    $cleanVer = if ($navVer -match '(\d+\.\d+[\.\d]*)') { $Matches[1] } else { $navVer }
    try { Write-NavigInstallState -InstalledVersion $cleanVer } catch {}

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
