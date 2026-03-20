# NAVIG Enhanced Installers - Complete Guide

> **Status**: Production-Ready (Feb 2026)  
> **Version**: 2.0 with Remote Drive Integration

## 🎯 What's New

The enhanced installers now include **automatic remote drive setup** alongside NAVIG installation:

| Feature | Windows | Linux |
|---------|---------|-------|
| **NAVIG CLI Installation** | ✅ | ✅ |
| **Telegram Automation** | ✅ | ✅ |
| **Cloud Drive Mounting** | rclone | rclone |
| **Linux File Sharing** | SSHFS-Win | — |
| **Windows File Sharing** | — | Samba |
| **Interactive Setup** | ✅ | ✅ |
| **Silent/Fast Mode** | ✅ | ✅ |
| **Daemon Services** | ✅ | ✅ |

---

## 📥 Installation Methods

### **Option 1: Fast Setup (Recommended for New Users)**
One command to get everything running:

#### Windows:
```powershell
powershell -ExecutionPolicy Bypass -File navig_quick_setup.ps1 -Fast
```

#### Linux:
```bash
bash navig_quick_setup.sh --fast
```

**What happens:**
- ✅ Installs dependencies automatically
- ✅ Sets up NAVIG CLI
- ✅ Installs cloud/share tools
- ✅ Configures basic daemon
- ⏱️ ~5 minutes total

---

### **Option 2: Interactive Setup (Best for Learning)**

#### Windows:
```powershell
powershell -ExecutionPolicy Bypass -File install_navig_windows_enhanced.ps1
```

#### Linux:
```bash
bash install_navig_linux_enhanced.sh
```

**Features:**
- 📋 Step-by-step walkthrough
- ❓ Optional configurations
- 🎛️ Customizable choices
- 📍 Detailed progress indicators

---

### **Option 3: Advanced (Custom Configuration)**

#### Windows:
```powershell
$params = @{
    SourcePath = "C:\path\to\navig-core"
    TelegramToken = "YOUR_TOKEN"
    SkipRemote = $false
}
.\install_navig_windows_enhanced.ps1 @params
```

#### Linux:
```bash
TELEGRAM_BOT_TOKEN=xxx bash install_navig_linux_enhanced.sh --install-samba --install-rclone
```

---

## 🔄 From Existing to New Installer

### Windows
**Old way:**
```powershell
.\scripts\install_navig_windows.ps1  # Just NAVIG
# Then manually setup rclone and SSHFS
```

**New way:**
```powershell
.\scripts\install_navig_windows_enhanced.ps1  # Everything
```

### Linux
**Old way:**
```bash
bash scripts/install_navig_linux.sh  # Just NAVIG
# Then manually setup Samba and rclone
```

**New way:**
```bash
bash scripts/install_navig_linux_enhanced.sh  # Everything
```

---

## 🚀 Use Cases

### **Case 1: Home User (Windows PC + Ubuntu Server)**

1. Windows PC installation:
```powershell
powershell -ExecutionPolicy Bypass -File navig_quick_setup.ps1 -Fast
```

2. Ubuntu Server installation:
```bash
bash navig_quick_setup.sh --fast
```

3. Now you can:
   - ✅ Access Ubuntu files from Windows: `Z:\`
   - ✅ Access Windows shares from Ubuntu
   - ✅ Mount Google Drive on both machines
   - ✅ Use NAVIG CLI on both systems

---

### **Case 2: Cloud-First Setup (Windows + Google Drive + Dropbox)**

During Windows installation, choose "Cloud Storage":
```
Select option (1-4): 1
Running: rclone config
Choose: Google Drive
Authenticate via browser...
```

Then mount:
```powershell
rclone mount gdrive: G:\ --vfs-cache-mode full
```

---

### **Case 3: Team Collaboration (Multiple Machines)**

**On each machine:**
```powershell
# Windows
powershell -ExecutionPolicy Bypass -File navig_quick_setup.ps1 -Fast -TelegramToken "xxx"
```

```bash
# Linux
TELEGRAM_BOT_TOKEN=xxx bash navig_quick_setup.sh --fast
```

All machines now:
- ✅ Share files via Samba/SSHFS
- ✅ Access cloud storage uniformly
- ✅ Respond to Telegram commands
- ✅ Can be managed as a group

---

## 🔧 Post-Installation

### Verify Everything Works:

```powershell
# Windows
navig --version
navig host list
Get-PSDrive | Where-Object {$_.DisplayRoot}
```

```bash
# Linux
navig --version
navig host list
rclone listremotes
```

### Mount Cloud Drive:

```bash
# Configure first time
rclone config

# Mount on startup (add to bashrc or crontab)
rclone mount gdrive: ~/mnt/gdrive --vfs-cache-mode full &
```

### Configure Telegram:

```bash
# Set token
export TELEGRAM_BOT_TOKEN=YOUR_TOKEN

# Restart daemon
navig service restart
```

---

## ⚙️ Configuration Files

### Windows
- **NAVIG config:** `C:\Users\<user>\.navig\config.yaml`
- **Environment:** `C:\Users\<user>\.navig\.env`
- **Telegram token:** Set in `.env` and config.yaml

### Linux
- **NAVIG config:** `~/.navig/config.yaml`
- **Environment:** `~/.navig/.env`
- **Samba config:** `/etc/samba/smb.conf`
- **rclone config:** `~/.config/rclone/rclone.conf`

---

## 🐛 Troubleshooting

### "NAVIG command not found"
```powershell
# Windows: Add to PATH
$env:Path += ";$env:USERPROFILE\.local\bin"

# Or open a new PowerShell window
```

### "rclone not found"
```bash
# Linux
sudo apt install rclone

# macOS
brew install rclone

# Windows (via installer)
choco install rclone -y
```

### "Samba not accessible from Windows"
```bash
# Check Samba status
sudo systemctl status smbd

# Restart Samba
sudo systemctl restart smbd nmbd

# Test from Windows
net view <hostname>
```

### "SSHFS mount failing"
```powershell
# Ensure SSHFS-Win is installed
Test-Path "C:\Program Files\SSHFS-Win"

# Try mounting manually
net use Z: \\sshfs\user@hostname/home/user
```

---

## 📊 Installation Comparison

| Aspect | Old | New |
|--------|-----|-----|
| **Setup Time** | 10-15 min | 5 min (fast) / interactive |
| **Includes Remote Tools** | ❌ | ✅ |
| **Cloud Drive Ready** | ❌ | ✅ auto-suggests |
| **Network Sharing** | ❌ | ✅ auto-setup |
| **Telegram Token** | ⚠️ Manual | ✅ Auto-configured |
| **Verification** | ❌ | ✅ Built-in |
| **Documentation** | 📄 Separate | 📖 Integrated |

---

## 🎓 Learning Resources

**Start here:**
1. Run the fast setup: `navig_quick_setup.ps1` / `.sh`
2. Follow the interactive prompts
3. Read the completion guide

**Advanced usage:**
- `navig help` - all built-in commands
- `navig host show` - see your configured servers
- `navig app list` - manage applications
- `rclone config` - advanced cloud storage

---

## 🔐 Security Notes

### Environment Variables
Sensitive tokens are stored in `~/.navig/.env` with restricted permissions:
```bash
# Linux
chmod 600 ~/.navig/.env

# Windows - automatic via PowerShell Set-Content
```

### Telegram Bot
- Token stored securely in `.env`
- Only saved to disk, not logged
- Daemon runs with user privileges (not root/admin)

### Samba Shares
- Share limited to your user account
- Password required for authentication
- Can be further restricted in `smb.conf`

### rclone
- Credentials stored in `~/.config/rclone/rclone.conf`
- OAuth tokens encrypted
- 600 permissions recommended

---

## 📞 Support

**Need help?**
1. Check the troubleshooting section above
2. Run `navig config validate` for config issues
3. Check logs: `~/.navig/` directory
4. GitHub Issues: github.com/navig-run/core

---

## 🎯 Next Steps After Installation

### Immediate (Day 1)
```bash
# Verify installation
navig --version

# List configured hosts
navig host list

# Test connectivity
navig host test
```

### Setup (Day 1-2)
```bash
# Configure cloud storage
rclone config

# Mount cloud drive
rclone mount <remote>: ~/mnt/<name> --vfs-cache-mode full

# Add more hosts (if needed)
navig host add
```

### Automation (Day 3+)
```bash
# Configure Telegram bot
export TELEGRAM_BOT_TOKEN=xxx
navig service restart

# Setup cron jobs for regular tasks
navig flow list
navig flow run <name>
```

---

## 📈 Performance Benchmarks

### Installation Times

| Task | Time |
|------|------|
| Fast setup (complete) | ~5 min |
| NAVIG installation | ~1 min |
| rclone setup | ~30 sec |
| Samba setup | ~20 sec |
| Telegram daemon start | ~10 sec |

### First-Time Operations

| Operation | Time |
|-----------|------|
| Mount SSHFS share | 2-3 sec |
| Mount rclone (cold) | 15-30 sec |
| Mount rclone (cached) | <1 sec |
| NAVIG command | 300-500ms |

---

## 🔄 Migration from Old Installers

If you installed with the old script, the new one recognizes your existing setup:

```powershell
# Windows: Just run the enhanced one
.\install_navig_windows_enhanced.ps1

# It will:
# ✅ Detect existing NAVIG
# ✅ Ask to upgrade dependencies
# ✅ Add remote tools
# ✅ Preserve configuration
```

```bash
# Linux: Same process
bash install_navig_linux_enhanced.sh

# It will:
# ✅ Detect existing NAVIG
# ✅ Ask to add Samba
# ✅ Install rclone
# ✅ Preserve configuration
```

---

## 📋 Checklist After Installation

- [ ] NAVIG CLI working: `navig --version`
- [ ] Can list hosts: `navig host list`
- [ ] Cloud storage configured: `rclone listremotes`
- [ ] File sharing ready (Samba or SSHFS)
- [ ] Telegram bot running (if configured)
- [ ] Can mount at least one remote
- [ ] Documentation saved: `REMOTE_DRIVES_GUIDE.md`

---

**Installed successfully? Great!** 🎉  
You now have a unified system for managing **NAVIG + remote access + cloud storage** across Windows and Linux.

Need help? See troubleshooting above or open an issue on GitHub.
