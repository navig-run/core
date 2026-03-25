#Requires -Version 5
<#
.SYNOPSIS
    Install NAVIG Tray for Windows.

.DESCRIPTION
    Sets up the NAVIG system tray launcher:
    - Creates a desktop shortcut
    - Optionally adds to Windows startup
    - Configures settings

.PARAMETER AutoStart
    Enable auto-start with Windows.

.PARAMETER Python
    Path to python.exe (auto-detected if not specified).

.EXAMPLE
    .\install-tray.ps1
    .\install-tray.ps1 -AutoStart
    .\install-tray.ps1 -Python "C:\Python312\python.exe"
#>

param(
    [switch]$AutoStart,
    [string]$Python
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  NAVIG Tray Installer" -ForegroundColor Cyan
Write-Host ""

# Find project root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

# Find Python
if (-not $Python) {
    # Try common locations
    $candidates = @(
        "C:\Server\bin\python\python-3.12\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        (Get-Command python -ErrorAction SilentlyContinue).Source,
        (Get-Command python3 -ErrorAction SilentlyContinue).Source
    ) | Where-Object { $_ -and (Test-Path $_) }
    
    if ($candidates.Count -gt 0) {
        $Python = $candidates[0]
    } else {
        Write-Host "[!] Python not found. Specify with -Python parameter." -ForegroundColor Red
        exit 1
    }
}

$PythonW = Join-Path (Split-Path $Python) "pythonw.exe"
if (-not (Test-Path $PythonW)) {
    $PythonW = $Python
}

Write-Host "[OK] Python: $Python" -ForegroundColor Green

# Check dependencies
Write-Host "[*] Checking dependencies..." -ForegroundColor Yellow
& $Python -c "import pystray; from PIL import Image; print('OK')" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[*] Installing pystray and Pillow..." -ForegroundColor Yellow
    & $Python -m pip install pystray Pillow --quiet
}
Write-Host "[OK] Dependencies ready" -ForegroundColor Green

# Create settings
$NavigDir = Join-Path $env:USERPROFILE ".navig"
$SettingsFile = Join-Path $NavigDir "tray_settings.json"

if (-not (Test-Path $NavigDir)) {
    New-Item -ItemType Directory -Path $NavigDir -Force | Out-Null
}

$settings = @{
    auto_start = $AutoStart.IsPresent
    start_gateway_on_launch = $false
    start_agent_on_launch = $false
    python_exe = $Python
    gateway_port = 8765
}

$settings | ConvertTo-Json | Set-Content $SettingsFile -Encoding UTF8
Write-Host "[OK] Settings saved: $SettingsFile" -ForegroundColor Green

# Create desktop shortcut
$PywScript = Join-Path $ProjectRoot "scripts\navig_tray.pyw"
$ShortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "NAVIG Tray.lnk"
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PythonW
$Shortcut.Arguments = "`"$PywScript`""
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.Description = "NAVIG System Tray"

# Use NAVIG icon if available
$IconPath = Join-Path $ProjectRoot "packages\navig-cloud\public\favicon.ico"
if (Test-Path $IconPath) {
    $Shortcut.IconLocation = $IconPath
}

$Shortcut.Save()
Write-Host "[OK] Desktop shortcut created" -ForegroundColor Green

# Auto-start
if ($AutoStart) {
    $RegPath = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    $RegValue = "`"$PythonW`" `"$PywScript`""
    Set-ItemProperty -Path $RegPath -Name "NavigTray" -Value $RegValue
    Write-Host "[OK] Auto-start enabled (launches at Windows login)" -ForegroundColor Green
}

Write-Host ""
Write-Host "  Installation complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Launch NAVIG Tray:" -ForegroundColor Cyan
Write-Host "    - Double-click 'NAVIG Tray' on desktop"
Write-Host "    - Or run: navig tray start"
Write-Host "    - Or run: $Python scripts\navig_tray.py"
Write-Host ""
