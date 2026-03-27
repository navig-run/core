#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Fix NAVIG .navig directory permissions on Windows

.DESCRIPTION
    This script diagnoses and fixes permission issues with the app-local
    .navig directory that can cause "Permission denied" errors.

.PARAMETER Diagnose
    Only diagnose the issue without making changes

.PARAMETER Fix
    Attempt to fix the permissions

.PARAMETER Delete
    Delete the .navig directory and recreate it with proper permissions

.EXAMPLE
    .\fix-navig-permissions.ps1 -Diagnose
    
.EXAMPLE
    .\fix-navig-permissions.ps1 -Fix
    
.EXAMPLE
    .\fix-navig-permissions.ps1 -Delete
#>

param(
    [switch]$Diagnose,
    [switch]$Fix,
    [switch]$Delete
)

$navigDir = Join-Path $PSScriptRoot ".." ".navig"
$navigDir = Resolve-Path $navigDir -ErrorAction SilentlyContinue

Write-Host "`n=== NAVIG Permission Diagnostic Tool ===" -ForegroundColor Cyan
Write-Host ""

# Check if .navig directory exists
if (-not (Test-Path $navigDir)) {
    Write-Host "[!] .navig directory does not exist at: $navigDir" -ForegroundColor Yellow
    Write-Host "[i] Run 'navig init' to create it." -ForegroundColor Gray
    exit 0
}

Write-Host "[+] Found .navig directory: $navigDir" -ForegroundColor Green

# Diagnose permissions
Write-Host "`n--- Current Permissions ---" -ForegroundColor Cyan
try {
    $acl = Get-Acl $navigDir
    Write-Host "Owner: $($acl.Owner)" -ForegroundColor Gray
    Write-Host "`nAccess Rules:" -ForegroundColor Gray
    foreach ($access in $acl.Access) {
        $color = if ($access.AccessControlType -eq "Allow") { "Green" } else { "Red" }
        Write-Host "  $($access.IdentityReference): $($access.FileSystemRights) ($($access.AccessControlType))" -ForegroundColor $color
    }
} catch {
    Write-Host "[!] ERROR: Cannot read permissions: $_" -ForegroundColor Red
    Write-Host "[i] You may need to run this script as Administrator." -ForegroundColor Yellow
    exit 1
}

# Test accessibility
Write-Host "`n--- Accessibility Test ---" -ForegroundColor Cyan
try {
    $null = Get-ChildItem $navigDir -ErrorAction Stop
    Write-Host "[+] Directory is accessible (can list contents)" -ForegroundColor Green
    $accessible = $true
} catch {
    Write-Host "[!] Directory is NOT accessible: $_" -ForegroundColor Red
    $accessible = $false
}

# If only diagnosing, stop here
if ($Diagnose) {
    Write-Host "`n--- Diagnosis Complete ---" -ForegroundColor Cyan
    if ($accessible) {
        Write-Host "[+] No permission issues detected." -ForegroundColor Green
    } else {
        Write-Host "[!] Permission issues detected. Run with -Fix or -Delete to resolve." -ForegroundColor Yellow
    }
    exit 0
}

# Fix permissions
if ($Fix) {
    Write-Host "`n--- Fixing Permissions ---" -ForegroundColor Cyan
    
    try {
        # Grant full control to current user
        $username = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        Write-Host "[*] Granting full control to: $username" -ForegroundColor Gray
        
        icacls $navigDir /grant "${username}:(OI)(CI)F" /T /Q
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[+] Permissions fixed successfully!" -ForegroundColor Green
            Write-Host "[i] Try running 'navig menu' again." -ForegroundColor Gray
        } else {
            Write-Host "[!] Failed to fix permissions (exit code: $LASTEXITCODE)" -ForegroundColor Red
            Write-Host "[i] Try running this script as Administrator." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "[!] ERROR: $_" -ForegroundColor Red
        Write-Host "[i] Try running this script as Administrator or use -Delete option." -ForegroundColor Yellow
    }
    
    exit 0
}

# Delete and recreate
if ($Delete) {
    Write-Host "`n--- Deleting and Recreating .navig ---" -ForegroundColor Cyan
    Write-Host "[!] WARNING: This will delete all app-local configuration!" -ForegroundColor Yellow
    Write-Host "[i] Global configuration in ~/.navig will NOT be affected." -ForegroundColor Gray
    
    $confirm = Read-Host "`nAre you sure? (yes/no)"
    if ($confirm -ne "yes") {
        Write-Host "[*] Cancelled." -ForegroundColor Gray
        exit 0
    }
    
    try {
        Write-Host "[*] Deleting $navigDir..." -ForegroundColor Gray
        Remove-Item -Path $navigDir -Recurse -Force -ErrorAction Stop
        Write-Host "[+] Deleted successfully." -ForegroundColor Green
        
        Write-Host "[*] Recreating with proper permissions..." -ForegroundColor Gray
        New-Item -Path $navigDir -ItemType Directory -Force | Out-Null
        
        # Set permissions
        $username = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        icacls $navigDir /grant "${username}:(OI)(CI)F" /T /Q | Out-Null
        
        Write-Host "[+] Recreated successfully!" -ForegroundColor Green
        Write-Host "[i] Run 'navig init' to initialize the directory." -ForegroundColor Gray
    } catch {
        Write-Host "[!] ERROR: $_" -ForegroundColor Red
        Write-Host "[i] Try running this script as Administrator." -ForegroundColor Yellow
    }
    
    exit 0
}

# No action specified
Write-Host "`n--- No Action Specified ---" -ForegroundColor Yellow
Write-Host "Usage:" -ForegroundColor Gray
Write-Host "  .\fix-navig-permissions.ps1 -Diagnose    # Check permissions" -ForegroundColor Gray
Write-Host "  .\fix-navig-permissions.ps1 -Fix         # Fix permissions" -ForegroundColor Gray
Write-Host "  .\fix-navig-permissions.ps1 -Delete      # Delete and recreate" -ForegroundColor Gray
Write-Host ""

