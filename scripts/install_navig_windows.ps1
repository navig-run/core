param(
  [string]$SourcePath = "",
  [switch]$InstallFactory,
  [string]$TelegramToken = ""
)

$ErrorActionPreference = "Stop"

if (-not $SourcePath) {
  $SourcePath = (Resolve-Path "$PSScriptRoot\..").Path
}

if (-not $TelegramToken) {
  $TelegramToken = if ($env:NAVIG_TELEGRAM_BOT_TOKEN) { $env:NAVIG_TELEGRAM_BOT_TOKEN } else { $env:TELEGRAM_BOT_TOKEN }
}

$VenvPath = Join-Path $env:USERPROFILE ".navig\venv"
$BinPath = Join-Path $env:USERPROFILE ".local\bin"
$CmdShim = Join-Path $BinPath "navig.cmd"

Write-Host "[1/5] Ensuring Python..."
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw "Python is required. Install Python 3.10+ first." }

Write-Host "[2/5] Creating virtualenv at $VenvPath"
python -m venv $VenvPath

Write-Host "[3/5] Installing NAVIG package"
& "$VenvPath\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
& "$VenvPath\Scripts\python.exe" -m pip install -e "$SourcePath"

Write-Host "[3b/5] Installing speedtest-cli (for navig net speedtest)"
try {
  & "$VenvPath\Scripts\python.exe" -m pip install --quiet speedtest-cli | Out-Null
  Write-Host "  speedtest-cli installed."
} catch {
  Write-Warning "speedtest-cli install failed — Ookla measurements unavailable. Run: pip install speedtest-cli"
}

Write-Host "[3c/5] Checking for iperf3 (for navig net speedtest)"
if (-not (Get-Command iperf3 -ErrorAction SilentlyContinue)) {
  Write-Warning "iperf3 not found in PATH. Download from https://iperf.fr/iperf-download.php and add to PATH for iperf3 measurements."
}

Write-Host "[4/5] Creating command shim"
New-Item -ItemType Directory -Force -Path $BinPath | Out-Null
@"
@echo off
"$VenvPath\Scripts\python.exe" -m navig.main %*
"@ | Set-Content -Encoding ASCII $CmdShim

Write-Host "[5/5] Updating user PATH"
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*$BinPath*") {
  [Environment]::SetEnvironmentVariable("Path", "$currentPath;$BinPath", "User")
  Write-Host "Added $BinPath to User PATH (open a new terminal)."
}

if ($TelegramToken) {
  $navigHome = Join-Path $env:USERPROFILE ".navig"
  New-Item -ItemType Directory -Force -Path $navigHome | Out-Null

  $envFile = Join-Path $navigHome ".env"
  "TELEGRAM_BOT_TOKEN=$TelegramToken" | Set-Content -Encoding UTF8 $envFile

  [Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", $TelegramToken, "User")
  $env:TELEGRAM_BOT_TOKEN = $TelegramToken

  $configFile = Join-Path $navigHome "config.yaml"
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

  & $CmdShim service install --bot --gateway --scheduler --no-start | Out-Null
  & $CmdShim service start | Out-Null
}

Write-Host "NAVIG installed on Windows."
Write-Host "Verify: navig --help"
if ($TelegramToken) {
  Write-Host "Telegram bot auto-configured and daemon start attempted."
}

if ($InstallFactory) {
  Write-Warning "Operational Factory installer is Linux/server-focused. Use WSL or remote Ubuntu host deployment."
}
