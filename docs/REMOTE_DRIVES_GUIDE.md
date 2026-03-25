# Remote Drive Mounting Solutions for Windows

## 🎯 Quick Start Guide

### Solution 1: Mount NAVIG Ubuntu via SSHFS (Recommended for Linux)

**Prerequisites:**
```powershell
# Install via Chocolatey
choco install sshfs -y
# OR download manually:
# WinFsp: https://winfsp.dev/
# SSHFS-Win: https://github.com/winfsp/sshfs-win/releases
```

**Mount Command:**
```powershell
# Check your NAVIG host details first
navig host show

# Mount using net use (replace with your details)
net use Z: \\sshfs\username@hostname/home/username

# Example:
net use Z: \\sshfs\developer@ubuntu-server/home/developer
```

**Unmount:**
```powershell
net use Z: /delete
```

---

### Solution 2: Mount Cloud Drives via rclone (Best for Cloud Storage)

**Prerequisites:**
```powershell
# Install rclone
choco install rclone -y
# OR download from: https://rclone.org/downloads/
```

**Setup:**
```powershell
# 1. Configure your cloud provider
rclone config

# Follow the wizard:
# - Choose provider (Google Drive, Dropbox, OneDrive, etc.)
# - Authenticate via browser
# - Name it (e.g., "gdrive", "dropbox", "onedrive")

# 2. Mount to drive letter
rclone mount gdrive: Y:\ --vfs-cache-mode full

# Or run in background
Start-Process rclone -ArgumentList "mount gdrive: Y:\ --vfs-cache-mode full" -WindowStyle Hidden
```

**Supported Cloud Providers:**
- ✓ Google Drive
- ✓ Microsoft OneDrive
- ✓ Dropbox
- ✓ Amazon S3
- ✓ Box
- ✓ pCloud
- ✓ 40+ more providers

**Unmount:**
```powershell
# Stop rclone process
Get-Process rclone | Stop-Process
```

---

### Solution 3: SMB/CIFS Share from Linux (If Samba installed)

**On Linux server (NAVIG Ubuntu):**
```bash
# Install Samba
sudo apt update
sudo apt install samba -y

# Create a share
sudo nano /etc/samba/smb.conf

# Add this at the end:
[share]
    path = /home/username
    browseable = yes
    read only = no
    valid users = username

# Set Samba password
sudo smbpasswd -a username

# Restart Samba
sudo systemctl restart smbd
```

**On Windows:**
```powershell
# Mount the share
net use Z: \\ubuntu-server\share /user:username

# Or via File Explorer:
# \\ubuntu-server\share
```

---

## 🚀 Using the NAVIG Scripts

### Run Network Diagnostic & Fix:
```powershell
# As Administrator
cd K:\_PROJECTS\navig\navig-core\scripts

# Diagnostic only
.\fix_windows_network_sharing.ps1 -DiagnosticOnly

# Auto-fix all issues
.\fix_windows_network_sharing.ps1 -AutoFix

# Interactive mode
.\fix_windows_network_sharing.ps1
```

### Mount Remote Drives:
```powershell
# Interactive wizard
.\mount_remote_drives.ps1

# Mount Linux via SSHFS
.\mount_remote_drives.ps1 -MountLinux -Host "ubuntu-server" -RemotePath "/home/user" -DriveLetter "Z:" -Username "user"

# Mount cloud drive (after rclone config)
.\mount_remote_drives.ps1 -MountCloud -CloudName "gdrive" -DriveLetter "Y:"

# List mounted drives
.\mount_remote_drives.ps1 -ListMounts

# Unmount all
.\mount_remote_drives.ps1 -UnmountAll
```

---

## 🔧 Integration with NAVIG CLI

You can use NAVIG to help automate this:

```powershell
# Get host details from NAVIG
$host_info = navig host show --json | ConvertFrom-Json

# Use in mount command
net use Z: "\\sshfs\$($host_info.username)@$($host_info.hostname)/home/$($host_info.username)"
```

---

## 📌 Recommended Setup

**For your use case:**

1. **Local WiFi sharing (laptop ↔ PC):** ✅ Already working!

2. **NAVIG Ubuntu remote server:**
   - **Option A:** SSHFS-Win (native file access, slower)
   - **Option B:** rclone SFTP (faster, cached)

3. **Cloud drives:**
   - Use `rclone` for all cloud providers
   - Mount as Windows drive letters
   - Works offline with cache

---

## ⚡ Quick Commands

```powershell
# 1. Install tools (run once)
choco install sshfs rclone -y

# 2. Configure rclone for cloud
rclone config

# 3. Mount Google Drive
rclone mount gdrive: G:\ --vfs-cache-mode full

# 4. Mount NAVIG Ubuntu
net use Z: \\sshfs\user@hostname/home/user

# 5. Check what's mounted
Get-PSDrive | Where-Object {$_.DisplayRoot}
```

---

## 🎯 Next Steps

1. **Install SSHFS-Win** for Linux mounts
2. **Install rclone** for cloud drives
3. **Configure your cloud provider** with `rclone config`
4. **Run the mount script** or use commands directly
5. **Add to startup** (optional) to auto-mount on boot

Enjoy seamless access to all your drives! 🚀
