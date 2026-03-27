#!/usr/bin/env powershell
#Requires -RunAsAdministrator

<#
.SYNOPSIS
    Windows Network Discovery & File Sharing Troubleshooter
.DESCRIPTION
    NAVIG script to diagnose and fix Windows network sharing issues
    - Enables Network Discovery
    - Enables File and Printer Sharing
    - Configures required services
    - Sets network to Private profile
.NOTES
    Author: NAVIG AI
    Date: 2026-02-23
#>

param(
    [switch]$DiagnosticOnly,
    [switch]$AutoFix,
    [switch]$Verbose
)

$ErrorActionPreference = "Continue"

function Write-NavigLog {
    param([string]$Message, [string]$Level = "INFO")
    $color = switch ($Level) {
        "ERROR" { "Red" }
        "SUCCESS" { "Green" }
        "WARNING" { "Yellow" }
        default { "Cyan" }
    }
    Write-Host "[$Level] $Message" -ForegroundColor $color
}

function Test-IsAdmin {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-NetworkSharingStatus {
    Write-NavigLog "=== NETWORK SHARING DIAGNOSTIC ===" "INFO"

    # Network Profiles
    Write-NavigLog "`n[1] Network Profiles:" "INFO"
    $profiles = Get-NetConnectionProfile
    $profiles | Format-Table Name, InterfaceAlias, NetworkCategory, IPv4Connectivity -AutoSize

    $publicProfiles = $profiles | Where-Object { $_.NetworkCategory -eq "Public" }
    if ($publicProfiles) {
        Write-NavigLog "⚠ Found Public network profiles (should be Private for sharing)" "WARNING"
    }

    # Services
    Write-NavigLog "`n[2] Required Services:" "INFO"
    $services = Get-Service -Name FDResPub, upnphost, SSDPSRV, lmhosts
    $services | Format-Table Name, Status, StartType -AutoSize

    $stoppedServices = $services | Where-Object { $_.Status -ne "Running" }
    if ($stoppedServices) {
        Write-NavigLog "⚠ Some services are not running" "WARNING"
    }

    # Firewall Rules
    Write-NavigLog "`n[3] Firewall Rules:" "INFO"
    $ndRules = Get-NetFirewallRule -DisplayGroup "Network Discovery" | Where-Object { $_.Enabled -eq $true }
    $fsRules = Get-NetFirewallRule -DisplayGroup "File and Printer Sharing" | Where-Object { $_.Enabled -eq $true }

    Write-Host "  Network Discovery Rules Enabled: $($ndRules.Count)"
    Write-Host "  File Sharing Rules Enabled: $($fsRules.Count)"

    if ($ndRules.Count -eq 0 -or $fsRules.Count -eq 0) {
        Write-NavigLog "⚠ Firewall rules are not properly configured" "WARNING"
    }

    # SMB Configuration
    Write-NavigLog "`n[4] SMB Protocol:" "INFO"
    $smbConfig = Get-SmbServerConfiguration
    Write-Host "  SMB2/SMB3: $($smbConfig.EnableSMB2Protocol)"

    # Network Test
    Write-NavigLog "`n[5] Network Connectivity Test:" "INFO"
    try {
        $null = Get-ChildItem \\localhost\c$ -ErrorAction Stop
        Write-NavigLog "✓ Local network sharing is working" "SUCCESS"
    } catch {
        Write-NavigLog "✗ Cannot access local shares" "ERROR"
    }

    Write-NavigLog "`n=== DIAGNOSTIC COMPLETE ===" "INFO"
}

function Repair-NetworkSharing {
    Write-NavigLog "=== STARTING NETWORK SHARING REPAIR ===" "INFO"

    # 1. Set network profiles to Private
    Write-NavigLog "`n[Step 1] Setting network profiles to Private..." "INFO"
    try {
        Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq "Public" } | ForEach-Object {
            Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private
            Write-NavigLog "✓ Set '$($_.Name)' to Private" "SUCCESS"
        }
    } catch {
        Write-NavigLog "⚠ Could not change network category: $($_.Exception.Message)" "WARNING"
    }

    # 2. Enable Network Discovery
    Write-NavigLog "`n[Step 2] Enabling Network Discovery..." "INFO"
    try {
        netsh advfirewall firewall set rule group="Network Discovery" new enable=Yes | Out-Null
        Write-NavigLog "✓ Network Discovery enabled" "SUCCESS"
    } catch {
        Write-NavigLog "✗ Failed to enable Network Discovery" "ERROR"
    }

    # 3. Enable File and Printer Sharing
    Write-NavigLog "`n[Step 3] Enabling File and Printer Sharing..." "INFO"
    try {
        netsh advfirewall firewall set rule group="File and Printer Sharing" new enable=Yes | Out-Null
        Write-NavigLog "✓ File and Printer Sharing enabled" "SUCCESS"
    } catch {
        Write-NavigLog "✗ Failed to enable File Sharing" "ERROR"
    }

    # 4. Configure Services
    Write-NavigLog "`n[Step 4] Configuring network services..." "INFO"
    $requiredServices = @('FDResPub', 'upnphost', 'SSDPSRV', 'lmhosts')

    foreach ($serviceName in $requiredServices) {
        try {
            $service = Get-Service -Name $serviceName

            # Start if not running
            if ($service.Status -ne "Running") {
                Start-Service -Name $serviceName
                Write-NavigLog "✓ Started service: $serviceName" "SUCCESS"
            }

            # Set to Automatic
            if ($service.StartType -ne "Automatic") {
                Set-Service -Name $serviceName -StartupType Automatic
                Write-NavigLog "✓ Set $serviceName to Automatic startup" "SUCCESS"
            }
        } catch {
            Write-NavigLog "⚠ Issue with service $serviceName : $($_.Exception.Message)" "WARNING"
        }
    }

    # 5. Verify SMB is enabled
    Write-NavigLog "`n[Step 5] Verifying SMB protocol..." "INFO"
    $smbConfig = Get-SmbServerConfiguration
    if ($smbConfig.EnableSMB2Protocol) {
        Write-NavigLog "✓ SMB2/SMB3 is enabled" "SUCCESS"
    } else {
        Write-NavigLog "⚠ SMB2/SMB3 is disabled" "WARNING"
    }

    Write-NavigLog "`n=== REPAIR COMPLETE ===" "SUCCESS"
    Write-NavigLog "Please wait 30-60 seconds for network discovery to update..." "INFO"
}

# Main execution
if (-not (Test-IsAdmin)) {
    Write-NavigLog "This script requires Administrator privileges!" "ERROR"
    Write-NavigLog "Please run PowerShell as Administrator and try again." "ERROR"
    exit 1
}

if ($DiagnosticOnly) {
    Get-NetworkSharingStatus
} elseif ($AutoFix) {
    Repair-NetworkSharing
    Write-Host "`n"
    Get-NetworkSharingStatus
} else {
    # Interactive mode
    Get-NetworkSharingStatus
    Write-Host "`n"
    $response = Read-Host "Do you want to apply fixes? (y/n)"
    if ($response -eq 'y' -or $response -eq 'Y') {
        Repair-NetworkSharing
    }
}
