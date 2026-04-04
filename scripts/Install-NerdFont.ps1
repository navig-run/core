#Requires -Version 7.2
<#
.SYNOPSIS
    Idempotent Nerd Font installer for NAVIG CLI.

.DESCRIPTION
    Downloads and installs the specified Nerd Font on Windows, macOS, or Linux,
    then patches VS Code settings.json (all platforms) and Windows Terminal
    settings.json (Windows only).

.PARAMETER FontName
    Base name of the Nerd Font release archive (default: JetBrainsMono).

.PARAMETER NerdRelease
    Nerd Fonts release tag to download (default: "latest").
    Pin to a specific version like "v3.2.1" for reproducible installs.

.PARAMETER LocalZip
    Optional path to a pre-downloaded .zip archive.
    Overrides the GitHub download step entirely.
    Can also be set via $env:NAVIG_FONT_ZIP.

.EXAMPLE
    pwsh scripts/Install-NerdFont.ps1
    pwsh scripts/Install-NerdFont.ps1 -NerdRelease v3.2.1
    pwsh scripts/Install-NerdFont.ps1 -LocalZip C:\Downloads\JetBrainsMono.zip
#>
[CmdletBinding()]
param(
    [string]$FontName    = "JetBrainsMono",
    [string]$NerdRelease = "latest",
    [string]$LocalZip    = $env:NAVIG_FONT_ZIP
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Logging ───────────────────────────────────────────────────────────────────
$LogDir  = Join-Path $PSScriptRoot ".." "logs"
$LogFile = Join-Path $LogDir "font-install.log"

function Write-Log {
    param(
        [ValidateSet('INFO', 'WARN', 'ERROR')][string]$Level,
        [string]$Message
    )
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [$Level] $Message"
    $null = New-Item -ItemType Directory -Force -Path $LogDir
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    switch ($Level) {
        'INFO'  { Write-Host $line }
        'WARN'  { Write-Host $line -ForegroundColor Yellow }
        'ERROR' { Write-Host $line -ForegroundColor Red }
    }
}

function Invoke-Step {
    param([string]$Name, [scriptblock]$Action)
    try {
        Write-Log INFO "START: $Name"
        & $Action
        Write-Log INFO "OK:    $Name"
    } catch {
        Write-Log ERROR "FAIL:  $Name -- $_"
        Write-Log ERROR "See $LogFile for details."
        throw
    }
}

# ── Platform detection ────────────────────────────────────────────────────────
$platform = if ($IsWindows) { 'windows' } elseif ($IsMacOS) { 'macos' } else { 'linux' }
Write-Log INFO "Platform: $platform"

# ── Font presence check ───────────────────────────────────────────────────────
function Test-FontInstalled {
    switch ($platform) {
        'windows' {
            $key = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts'
            try {
                $found = (Get-ItemProperty $key).PSObject.Properties.Name |
                    Where-Object { $_ -like "*JetBrainsMono*Nerd*" } |
                    Select-Object -First 1
                return [bool]$found
            } catch {
                return $false
            }
        }
        default {
            $list = & fc-list 2>$null
            return ($list -match 'JetBrainsMono')
        }
    }
}

if (Test-FontInstalled) {
    Write-Log INFO "Font '$FontName Nerd Font' already installed -- skipping download and install."
} else {
    # -- Step 1: Acquire archive -----------------------------------------------
    Invoke-Step "Acquire font archive" {
        if ($LocalZip -and (Test-Path $LocalZip)) {
            $script:ZipPath = $LocalZip
            Write-Log INFO "Using local archive: $($script:ZipPath)"
        } else {
            if ($NerdRelease -eq 'latest') {
                Write-Log INFO "Querying latest Nerd Fonts release from GitHub..."
                $release = Invoke-RestMethod `
                    -Uri "https://api.github.com/repos/ryanoasis/nerd-fonts/releases/latest" `
                    -Headers @{ 'User-Agent' = 'NAVIG-CLI' }
                $tag = $release.tag_name
            } else {
                $tag = $NerdRelease
            }
            Write-Log INFO "Nerd Fonts release: $tag"
            $url = "https://github.com/ryanoasis/nerd-fonts/releases/download/$tag/$FontName.zip"
            $script:ZipPath = Join-Path ([System.IO.Path]::GetTempPath()) "$FontName-NF.zip"
            Write-Log INFO "Downloading: $url"
            try {
                Invoke-WebRequest -Uri $url -OutFile $script:ZipPath -UseBasicParsing
            } catch {
                Write-Log ERROR "Download failed. Check network connectivity or set `$env:NAVIG_FONT_ZIP` to a local archive path."
                throw
            }
            Write-Log INFO "Downloaded to: $($script:ZipPath)"
        }
    }

    # -- Step 2: Extract and install -------------------------------------------
    Invoke-Step "Extract and install fonts" {
        $tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) "$FontName-NF-extracted"
        if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
        Expand-Archive -Path $script:ZipPath -DestinationPath $tmpDir -Force

        $ttfFiles = Get-ChildItem $tmpDir -Filter "*.ttf" -Recurse
        if (-not $ttfFiles) {
            throw "No .ttf files found in archive. Archive may be corrupt or unsupported."
        }
        Write-Log INFO "Found $($ttfFiles.Count) .ttf file(s) to install."

        switch ($platform) {
            'windows' {
                $fontDir = "$env:SystemRoot\Fonts"
                $regKey  = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts'
                foreach ($f in $ttfFiles) {
                    $dest = Join-Path $fontDir $f.Name
                    Copy-Item $f.FullName -Destination $dest -Force
                    $regName = "$($f.BaseName) (TrueType)"
                    Set-ItemProperty -Path $regKey -Name $regName -Value $f.Name
                    Write-Log INFO "Installed: $($f.Name)"
                }
            }
            'macos' {
                $fontDir = Join-Path $HOME "Library/Fonts"
                $null = New-Item -ItemType Directory -Force -Path $fontDir
                foreach ($f in $ttfFiles) {
                    Copy-Item $f.FullName -Destination $fontDir -Force
                    Write-Log INFO "Installed: $($f.Name)"
                }
            }
            'linux' {
                $fontDir = Join-Path $HOME ".local/share/fonts"
                $null = New-Item -ItemType Directory -Force -Path $fontDir
                foreach ($f in $ttfFiles) {
                    Copy-Item $f.FullName -Destination $fontDir -Force
                    Write-Log INFO "Installed: $($f.Name)"
                }
                Write-Log INFO "Rebuilding font cache (fc-cache -fv)..."
                & fc-cache -fv 2>&1 | Out-Null
            }
        }
    }
}

# ── VS Code settings.json patch ───────────────────────────────────────────────
Invoke-Step "Patch VS Code settings.json" {
    $vsCodeSettings = switch ($platform) {
        'windows' { "$env:APPDATA\Code\User\settings.json" }
        'macos'   { "$HOME/Library/Application Support/Code/User/settings.json" }
        'linux'   { "$HOME/.config/Code/User/settings.json" }
    }

    if (Test-Path $vsCodeSettings) {
        $raw = Get-Content $vsCodeSettings -Raw -Encoding UTF8
        # Strip JSON comments (single-line // ...) for compatibility
        $stripped = $raw -replace '(?m)^\s*//.*$', ''
        $cfg = $stripped | ConvertFrom-Json -AsHashtable
        $cfg['terminal.integrated.fontFamily'] = 'JetBrainsMono Nerd Font Mono'
        $json = $cfg | ConvertTo-Json -Depth 20
        [System.IO.File]::WriteAllText($vsCodeSettings, $json, [System.Text.Encoding]::UTF8)
        Write-Log INFO "VS Code settings.json patched: $vsCodeSettings"
    } else {
        Write-Log WARN "VS Code settings.json not found at $vsCodeSettings -- skipping."
        Write-Log WARN "To configure manually: set `"terminal.integrated.fontFamily`": `"JetBrainsMono Nerd Font Mono`""
    }
}

# ── Windows Terminal patch ────────────────────────────────────────────────────
if ($platform -eq 'windows') {
    Invoke-Step "Patch Windows Terminal settings.json" {
        $wtPaths = @(
            # Stable release (Store)
            "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json",
            # Preview release (Store)
            "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json",
            # Unpackaged / sideloaded
            "$env:LOCALAPPDATA\Microsoft\Windows Terminal\settings.json"
        )
        $wtSettings = $wtPaths | Where-Object { Test-Path $_ } | Select-Object -First 1

        if ($wtSettings) {
            $raw = Get-Content $wtSettings -Raw -Encoding UTF8
            $cfg = $raw | ConvertFrom-Json -AsHashtable
            if ($cfg.ContainsKey('profiles') -and $cfg.profiles -is [hashtable]) {
                if (-not $cfg.profiles.ContainsKey('defaults')) {
                    $cfg.profiles['defaults'] = @{}
                }
                $cfg.profiles.defaults['fontFace'] = 'JetBrainsMono Nerd Font Mono'
                $json = $cfg | ConvertTo-Json -Depth 20
                [System.IO.File]::WriteAllText($wtSettings, $json, [System.Text.Encoding]::UTF8)
                Write-Log INFO "Windows Terminal patched: $wtSettings"
            } else {
                Write-Log WARN "Windows Terminal settings structure unexpected -- skipping font patch."
            }
        } else {
            Write-Log WARN "Windows Terminal settings.json not found -- skipping."
        }
    }
}

# ── macOS/Linux manual reminder ───────────────────────────────────────────────
if ($platform -ne 'windows') {
    Write-Log INFO ""
    Write-Log INFO "ACTION REQUIRED: Set your terminal emulator font manually:"
    Write-Log INFO "  iTerm2       : Preferences -> Profiles -> Text -> Font -> JetBrainsMono Nerd Font Mono"
    Write-Log INFO "  Kitty        : font_family JetBrainsMono Nerd Font Mono  (in ~/.config/kitty/kitty.conf)"
    Write-Log INFO "  GNOME Terminal: Edit -> Preferences -> Profile -> Text -> Custom font"
    Write-Log INFO "  Alacritty    : font.normal.family = 'JetBrainsMono Nerd Font Mono'  (in alacritty.toml)"
    Write-Log INFO ""
}

# ── Final verification ────────────────────────────────────────────────────────
Invoke-Step "Verify installation" {
    if (-not (Test-FontInstalled)) {
        Write-Log ERROR "Font not detected after install. This can happen if:"
        Write-Log ERROR "  Windows: the registry write requires administrator privileges."
        Write-Log ERROR "  Linux:   fc-cache did not run or fonts dir is non-standard."
        Write-Log ERROR "  macOS:   Fonts directory permissions issue."
        throw "Post-install font verification failed. Check $LogFile for details."
    }
    Write-Log INFO "SUCCESS: JetBrainsMono Nerd Font confirmed installed on $platform."
    Write-Log INFO "Restart your terminal or VS Code to apply the new font."
}
