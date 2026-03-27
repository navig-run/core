#!/usr/bin/env powershell

<#
.SYNOPSIS
    Mount Remote Cloud Drives and Linux Shares on Windows
.DESCRIPTION
    NAVIG script to mount:
    - Cloud storage (via rclone)
    - Linux SSH/SFTP shares (via SSHFS-Win)
    - SMB/CIFS shares from Linux servers
.NOTES
    Author: NAVIG AI
    Date: 2026-02-23
.EXAMPLE
    .\mount_remote_drives.ps1 -MountLinux -Host "ubuntu-server" -RemotePath "/home/user" -DriveLetter "Z:"
    .\mount_remote_drives.ps1 -MountCloud -CloudName "gdrive" -DriveLetter "Y:"
#>

param(
    [switch]$MountLinux,
    [switch]$MountCloud,
    [switch]$ListMounts,
    [switch]$UnmountAll,
    [string]$Host,
    [string]$RemotePath,
    [string]$CloudName,
    [string]$DriveLetter,
    [string]$Username
)

$ErrorActionPreference = "Stop"

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

function Test-RcloneInstalled {
    try {
        $rclone = Get-Command rclone -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Test-SSHFSInstalled {
    # Check for SSHFS-Win (WinFsp + SSHFS-Win)
    $winfsp = Test-Path "C:\Program Files (x86)\WinFsp"
    $sshfs = Test-Path "C:\Program Files\SSHFS-Win"
    return ($winfsp -and $sshfs)
}

function Install-Prerequisites {
    Write-NavigLog "=== CHECKING PREREQUISITES ===" "INFO"
    
    # Check for Chocolatey
    $chocoInstalled = $null -ne (Get-Command choco -ErrorAction SilentlyContinue)
    
    if (-not $chocoInstalled) {
        Write-NavigLog "Chocolatey not found. Install from: https://chocolatey.org/" "WARNING"
    }
    
    # Check rclone
    if (-not (Test-RcloneInstalled)) {
        Write-NavigLog "rclone not installed" "WARNING"
        if ($chocoInstalled) {
            Write-Host "Install with: choco install rclone -y"
        } else {
            Write-Host "Download from: https://rclone.org/downloads/"
        }
    } else {
        Write-NavigLog "✓ rclone installed" "SUCCESS"
    }
    
    # Check SSHFS-Win
    if (-not (Test-SSHFSInstalled)) {
        Write-NavigLog "SSHFS-Win not installed" "WARNING"
        if ($chocoInstalled) {
            Write-Host "Install with: choco install sshfs -y"
        } else {
            Write-Host "Download WinFsp from: https://winfsp.dev/"
            Write-Host "Download SSHFS-Win from: https://github.com/billziss-gh/sshfs-win"
        }
    } else {
        Write-NavigLog "✓ SSHFS-Win installed" "SUCCESS"
    }
}

function Mount-LinuxShare {
    param(
        [string]$HostName,
        [string]$Path,
        [string]$Drive,
        [string]$User
    )
    
    if (-not (Test-SSHFSInstalled)) {
        Write-NavigLog "SSHFS-Win is not installed!" "ERROR"
        Write-NavigLog "Install with: choco install sshfs -y" "INFO"
        return
    }
    
    Write-NavigLog "Mounting Linux share from ${User}@${HostName}:${Path} to ${Drive}" "INFO"
    
    # Using SSHFS-Win via net use
    $remotePath = "\\sshfs\${User}@${HostName}${Path}"
    
    try {
        net use $Drive $remotePath
        Write-NavigLog "✓ Successfully mounted ${Drive}" "SUCCESS"
        Write-NavigLog "Access your files at: ${Drive}" "INFO"
    } catch {
        Write-NavigLog "Failed to mount: $($_.Exception.Message)" "ERROR"
        Write-NavigLog "Alternative: Use rclone with SFTP backend" "INFO"
    }
}

function Mount-CloudDrive {
    param(
        [string]$Remote,
        [string]$Drive
    )
    
    if (-not (Test-RcloneInstalled)) {
        Write-NavigLog "rclone is not installed!" "ERROR"
        Write-NavigLog "Install with: choco install rclone -y" "INFO"
        return
    }
    
    Write-NavigLog "Mounting cloud drive '$Remote' to ${Drive}" "INFO"
    
    # List available remotes
    $remotes = rclone listremotes
    if ($remotes -notcontains "${Remote}:") {
        Write-NavigLog "Remote '$Remote' not found. Available remotes:" "ERROR"
        rclone listremotes
        Write-NavigLog "Configure with: rclone config" "INFO"
        return
    }
    
    # Mount using rclone
    $mountPoint = "${Drive}\"
    
    Write-NavigLog "Starting rclone mount (this runs in background)..." "INFO"
    Start-Process -FilePath "rclone" -ArgumentList "mount ${Remote}: ${mountPoint} --vfs-cache-mode full" -WindowStyle Hidden
    
    Start-Sleep -Seconds 3
    
    if (Test-Path $mountPoint) {
        Write-NavigLog "✓ Successfully mounted ${Drive}" "SUCCESS"
        Write-NavigLog "Access your cloud files at: ${Drive}" "INFO"
    } else {
        Write-NavigLog "Mount may still be initializing. Check with: Get-Process rclone" "WARNING"
    }
}

function Mount-NavigHost {
    Write-NavigLog "=== MOUNT NAVIG HOST VIA SMB/SFTP ===" "INFO"
    
    # Check if NAVIG CLI is available
    $navigInstalled = $null -ne (Get-Command navig -ErrorAction SilentlyContinue)
    
    if (-not $navigInstalled) {
        Write-NavigLog "NAVIG CLI not found in PATH" "WARNING"
        Write-NavigLog "Make sure NAVIG is installed and in your PATH" "INFO"
        return
    }
    
    # Get active host
    try {
        $activeHost = navig host show --json 2>$null | ConvertFrom-Json
        Write-NavigLog "Active NAVIG host: $($activeHost.hostname)" "INFO"
        
        # Option 1: SSH Tunnel + SSHFS
        Write-Host "`nOption 1: Mount via SSHFS"
        Write-Host "Command: net use Z: \\sshfs\$($activeHost.username)@$($activeHost.hostname)/home/$($activeHost.username)"
        
        # Option 2: rclone SFTP
        Write-Host "`nOption 2: Configure rclone SFTP remote"
        Write-Host "1. Run: rclone config"
        Write-Host "2. Choose 'n' for new remote"
        Write-Host "3. Name it: navig-ubuntu"
        Write-Host "4. Choose: sftp"
        Write-Host "5. Host: $($activeHost.hostname)"
        Write-Host "6. User: $($activeHost.username)"
        Write-Host "7. Use SSH key from: $($activeHost.ssh_key_path)"
        Write-Host "Then mount: rclone mount navig-ubuntu: Z:\ --vfs-cache-mode full"
        
        # Option 3: If Samba is installed on Linux
        Write-Host "`nOption 3: SMB Share (if Samba is installed on Linux)"
        Write-Host "Command: net use Z: \\$($activeHost.hostname)\share /user:$($activeHost.username)"
        
    } catch {
        Write-NavigLog "Could not get NAVIG host info" "ERROR"
    }
}

function Get-MountedDrives {
    Write-NavigLog "=== MOUNTED NETWORK DRIVES ===" "INFO"
    
    Get-PSDrive -PSProvider FileSystem | Where-Object { $_.DisplayRoot -ne $null } | Format-Table Name, Root, DisplayRoot -AutoSize
    
    # Check for rclone mounts
    $rcloneProcesses = Get-Process -Name rclone -ErrorAction SilentlyContinue
    if ($rcloneProcesses) {
        Write-NavigLog "`nActive rclone mounts:" "INFO"
        $rcloneProcesses | Format-Table Id, ProcessName, StartTime -AutoSize
    }
}

function Dismount-AllDrives {
    Write-NavigLog "=== UNMOUNTING ALL NETWORK DRIVES ===" "WARNING"
    
    # Unmount network drives
    Get-PSDrive -PSProvider FileSystem | Where-Object { $_.DisplayRoot -ne $null } | ForEach-Object {
        Write-Host "Unmounting: $($_.Name)"
        net use "$($_.Name):" /delete /y
    }
    
    # Stop rclone processes
    $rcloneProcesses = Get-Process -Name rclone -ErrorAction SilentlyContinue
    if ($rcloneProcesses) {
        Write-Host "Stopping rclone processes..."
        $rcloneProcesses | Stop-Process -Force
    }
    
    Write-NavigLog "✓ All drives unmounted" "SUCCESS"
}

# Main execution
if ($ListMounts) {
    Get-MountedDrives
    exit 0
}

if ($UnmountAll) {
    Dismount-AllDrives
    exit 0
}

if ($MountLinux) {
    if (-not $Host -or -not $RemotePath -or -not $DriveLetter) {
        Write-NavigLog "Missing parameters! Required: -Host, -RemotePath, -DriveLetter" "ERROR"
        Write-Host "Example: .\mount_remote_drives.ps1 -MountLinux -Host 'ubuntu-server' -RemotePath '/home/user' -DriveLetter 'Z:' -Username 'user'"
        exit 1
    }
    
    if (-not $Username) {
        $Username = Read-Host "Enter SSH username"
    }
    
    Mount-LinuxShare -HostName $Host -Path $RemotePath -Drive $DriveLetter -User $Username
    exit 0
}

if ($MountCloud) {
    if (-not $CloudName -or -not $DriveLetter) {
        Write-NavigLog "Missing parameters! Required: -CloudName, -DriveLetter" "ERROR"
        Write-Host "Example: .\mount_remote_drives.ps1 -MountCloud -CloudName 'gdrive' -DriveLetter 'Y:'"
        exit 1
    }
    
    Mount-CloudDrive -Remote $CloudName -Drive $DriveLetter
    exit 0
}

# Interactive mode
Write-Host @"
╔════════════════════════════════════════════════════════════╗
║         NAVIG Remote Drive Mount Helper                    ║
╚════════════════════════════════════════════════════════════╝
"@

Install-Prerequisites

Write-Host "`nWhat would you like to mount?`n"
Write-Host "1. NAVIG Ubuntu/Linux host (via SSHFS or rclone)"
Write-Host "2. Cloud storage (Google Drive, Dropbox, etc.)"
Write-Host "3. Custom Linux server (SSH/SFTP)"
Write-Host "4. List currently mounted drives"
Write-Host "5. Unmount all network drives"
Write-Host "0. Exit"

$choice = Read-Host "`nEnter choice"

switch ($choice) {
    "1" { Mount-NavigHost }
    "2" {
        Write-Host "`nFirst, configure rclone with: rclone config"
        Write-Host "Then run this script again with: -MountCloud -CloudName 'name' -DriveLetter 'Z:'"
    }
    "3" {
        $h = Read-Host "Enter hostname/IP"
        $u = Read-Host "Enter username"
        $p = Read-Host "Enter remote path (e.g., /home/user)"
        $d = Read-Host "Enter drive letter (e.g., Z:)"
        Mount-LinuxShare -HostName $h -Path $p -Drive $d -User $u
    }
    "4" { Get-MountedDrives }
    "5" { Dismount-AllDrives }
    "0" { Write-Host "Goodbye!" }
    default { Write-Host "Invalid choice" }
}
