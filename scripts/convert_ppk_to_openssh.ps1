# Convert PuTTY .ppk key to OpenSSH format for use with NAVIG

Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "CONVERT PUTTY KEY TO OPENSSH FORMAT" -ForegroundColor Yellow
Write-Host "=" * 80 -ForegroundColor Cyan

$ppkFile = "$env:USERPROFILE\.ssh\runcloud_id.ppk"
$opensshFile = "$env:USERPROFILE\.ssh\runcloud_id_openssh"

Write-Host "`nSource (PuTTY format): $ppkFile" -ForegroundColor White
Write-Host "Target (OpenSSH format): $opensshFile" -ForegroundColor White

# Check if source file exists
if (-not (Test-Path $ppkFile)) {
    Write-Host "`n[ERROR] PuTTY key file not found: $ppkFile" -ForegroundColor Red
    exit 1
}

Write-Host "`n[OK] PuTTY key file found" -ForegroundColor Green

# Check if puttygen is available
$puttygenPaths = @(
    "C:\Program Files\PuTTY\puttygen.exe",
    "C:\Program Files (x86)\PuTTY\puttygen.exe",
    "$env:LOCALAPPDATA\Programs\PuTTY\puttygen.exe",
    "puttygen.exe"  # Try PATH
)

$puttygen = $null
foreach ($path in $puttygenPaths) {
    if (Test-Path $path -ErrorAction SilentlyContinue) {
        $puttygen = $path
        break
    }
}

if (-not $puttygen) {
    # Try to find it in PATH
    try {
        $puttygen = (Get-Command puttygen -ErrorAction Stop).Source
    } catch {
        Write-Host "`n[ERROR] PuTTYgen not found!" -ForegroundColor Red
        Write-Host "`nPlease install PuTTY from: https://www.putty.org/" -ForegroundColor Yellow
        Write-Host "`nOR manually convert using PuTTYgen GUI:" -ForegroundColor Yellow
        Write-Host "  1. Open PuTTYgen" -ForegroundColor White
        Write-Host "  2. Click 'Load' and select: $ppkFile" -ForegroundColor White
        Write-Host "  3. Go to Conversions -> Export OpenSSH key" -ForegroundColor White
        Write-Host "  4. Save as: $opensshFile" -ForegroundColor White
        Write-Host "  5. Do NOT add a passphrase (leave empty)" -ForegroundColor White
        exit 1
    }
}

Write-Host "`n[OK] Found PuTTYgen: $puttygen" -ForegroundColor Green

# Convert the key
Write-Host "`n[INFO] Converting key..." -ForegroundColor Cyan

try {
    & $puttygen $ppkFile -O private-openssh -o $opensshFile
    
    if ($LASTEXITCODE -eq 0 -and (Test-Path $opensshFile)) {
        Write-Host "`n[SUCCESS] Key converted successfully!" -ForegroundColor Green
        Write-Host "`nOpenSSH private key saved to: $opensshFile" -ForegroundColor White
        
        # Update NAVIG config
        Write-Host "`n" + ("=" * 80) -ForegroundColor Cyan
        Write-Host "NEXT STEPS" -ForegroundColor Yellow
        Write-Host ("=" * 80) -ForegroundColor Cyan
        
        Write-Host "`n1. Update your NAVIG host configuration:" -ForegroundColor White
        Write-Host "   navig host edit vultr" -ForegroundColor Cyan
        Write-Host "`n   Change ssh_key to:" -ForegroundColor White
        Write-Host "   ssh_key: $opensshFile" -ForegroundColor Green
        
        Write-Host "`n2. Test the connection:" -ForegroundColor White
        Write-Host "   navig host test vultr" -ForegroundColor Cyan
        
        Write-Host "`n" + ("=" * 80) -ForegroundColor Cyan
        
    } else {
        Write-Host "`n[ERROR] Conversion failed!" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "`n[ERROR] Conversion failed: $_" -ForegroundColor Red
    exit 1
}

