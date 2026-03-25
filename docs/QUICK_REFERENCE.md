# NAVIG Remote Setup - Quick Reference Card

**Print this or pin it!** ⭐

---

## 📋 Installation (One Command)

### Windows
```powershell
powershell -ExecutionPolicy Bypass -File navig_quick_setup.ps1 -Fast
```

### Linux
```bash
bash navig_quick_setup.sh --fast
```

---

## ⏱️ Timeline

| Step | Time | Action |
|------|------|--------|
| 1 | 1 min | Check prerequisites |
| 2 | 1 min | Install NAVIG |
| 3 | 1 min | Install tools (rclone/Samba) |
| 4 | 1 min | Configure automaiton (optional) |
| 5 | 1 min | Verify & test |
| | **~5 min** | **✅ Done!** |

---

## 📁 File Locations After Setup

### Windows
```
C:\Users\<user>\.navig\
├── venv\              (Python environment)
├── config.yaml        (Configuration)
├── .env              (Secrets - chmod 600)
└── logs\             (Operation logs)

C:\Users\<user>\.local\bin\
└── navig.cmd         (Command wrapper)
```

### Linux
```
~/.navig/
├── venv/             (Python environment)
├── config.yaml       (Configuration)
├── .env             (Secrets)
└── logs\            (Operation logs)

~/.local/bin/
└── navig            (Command wrapper)

/etc/samba/smb.conf  (Samba share config)
~/.config/rclone/    (rclone configuration)
```

---

## 🔌 Mount Commands

### Cloud Drive (Both Platforms)
```bash
# First time - configure
rclone config

# Then mount with cache
rclone mount gdrive: ~/mnt/gdrive --vfs-cache-mode full

# Windows equivalent
rclone mount gdrive: G:\ --vfs-cache-mode full
```

### Linux → Windows (SSHFS)
```powershell
# Windows only
net use Z: \\sshfs\username@linuxhost/home/username
```

### Linux → Windows (rclone SFTP)
```bash
# Configure SFTP remote
rclone config         # Choose 'sftp'

# Mount
rclone mount sftp-ubuntu: ~/mnt/linux --vfs-cache-mode full
```

### Windows → Linux (Samba)
```bash
# Linux - automatically configured
# From Windows, access:
\\linuxhostname\navig_share
```

---

## ✅ Verification

### NAVIG Working
```bash
navig --version
navig host list
navig --help
```

### Cloud Storage Ready
```bash
rclone listremotes
rclone ls gdrive:
```

### Network Shares Ready
```bash
# Windows
Get-PSDrive | Where-Object {$_.DisplayRoot}

# Linux
mount | grep sshfs
```

### Telegram Bot (if configured)
```bash
navig service status
```

---

## 🔧 Troubleshooting

| Issue | Solution |
|-------|----------|
| NAVIG not found | Open new terminal or add `~/.local/bin` to PATH |
| rclone not found | Run installer again: `choco install rclone` (Windows) or `sudo apt install rclone` (Linux) |
| Can't see Linux share | Check Windows firewall, Samba status: `sudo systemctl status smbd` |
| Mount permission denied | Ensure username/password correct, check user in Samba config |
| Telegram not responding | Check: `navig config show`, verify token, restart: `navig service restart` |
| Disk space issues | Check with: `df -h` (Linux) or `Get-PSDrive` (Windows) |

---

## 📊 Common Tools

| Tool | Purpose | Windows | Linux |
|------|---------|---------|-------|
| **rclone** | Cloud mounting | ✅ | ✅ |
| **SSHFS-Win** | Linux SFTP access | ✅ | — |
| **Samba** | Windows file sharing | — | ✅ |
| **OpenSSH** | SSH access | ✅ (Win10+) | ✅ |

---

## 🚀 Quick Start Cheat Sheet

```bash
# 1. Install NAVIG (run once)
powershell -ExecutionPolicy Bypass -File navig_quick_setup.ps1 -Fast  # Windows
bash navig_quick_setup.sh --fast                                      # Linux

# 2. Configure cloud storage
rclone config

# 3. Mount cloud drive
rclone mount gdrive: ~/mnt/gdrive --vfs-cache-mode full

# 4. Check status
navig host list
rclone listremotes

# 5. Optional: Set Telegram
export TELEGRAM_BOT_TOKEN=xxx
navig service restart
```

---

## 📞 Get Help

```bash
# Built-in help
navig help
navig help host
navig help config

# Check configuration
navig config validate
navig config show

# Verify installation
navig --version
navig host test

# View logs
cat ~/.navig/logs/navig.log      # Linux
Get-Content $env:USERPROFILE\.navig\logs\navig.log  # Windows
```

---

## 💾 Backup Important Files

### Before major changes, backup:
```bash
# Linux
tar czf navig-backup.tar.gz ~/.navig

# Windows
Compress-Archive -Path $env:USERPROFILE\.navig -DestinationPath navig-backup.zip
```

---

## 🔐 Security Reminders

- ✅ Keep `~/.navig/.env` private (chmod 600)
- ✅ Don't share Telegram tokens
- ✅ Use strong Samba passwords
- ✅ Limit Samba shares to needed directories only
- ✅ Update packages regularly: `sudo apt update && apt upgrade`
- ✅ Monitor access: `sudo smbstatus` (Samba), `navig logs` (NAVIG)

---

## 🎯 Next Steps

After installation:

1. **Day 1:** Verify everything works with verification commands above
2. **Day 2:** Configure cloud storage providers
3. **Day 3:** Set up Telegram automation (optional)
4. **Week 1:** Test all mount points, adjust configurations
5. **Ongoing:** Monitor logs, keep tools updated

---

## 📖 Full Documentation

- **Complete Guide:** `ENHANCED_INSTALLERS_GUIDE.md`
- **Remote Drives:** `REMOTE_DRIVES_GUIDE.md`
- **Implementation:** `IMPLEMENTATION_SUMMARY.md`

---

## ℹ️ Pro Tips

1. **For cloud:** Use `--vfs-cache-mode full` for offline access
2. **For Linux:** Samba auto-configures; Samba passwords ≠ Linux passwords
3. **For Windows:** SSHFS-Win needs SSH key authentication setup
4. **For Teams:** Configure NAVIG with Telegram for remote commands
5. **For Speed:** Mount remotes on application startup (cron/Task Scheduler)

---

**Bookmark this card!** 🤖  
Print or save for quick reference.
