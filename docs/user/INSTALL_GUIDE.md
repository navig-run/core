# NAVIG Installation Guide

This guide covers every supported way to install, remove, and reinstall NAVIG across Ubuntu Server and Windows. Follow the numbered steps top-to-bottom. Each section ends with a verification command — confirm it passes before moving on.

---

## Table of Contents

1. [Before You Start](#before-you-start)
2. [Ubuntu — Backup existing installation](#ubuntu--backup-existing-installation)
3. [Ubuntu — Full removal](#ubuntu--full-removal)
4. [Ubuntu — Fresh install from PyPI](#ubuntu--fresh-install-from-pypi)
5. [Remote install from Windows via SSH](#remote-install-from-windows-via-ssh)
6. [Windows laptop install](#windows-laptop-install)
7. [Build and publish to PyPI (maintainers only)](#build-and-publish-to-pypi-maintainers-only)
8. [Final summary table](#final-summary-table)

---

## Before You Start

| Requirement | Minimum version |
|---|---|
| Python | 3.10 |
| pip | 22+ |
| Ubuntu | 20.04 LTS or later |
| Windows | 10 (build 1809) or later |
| OpenSSH client | included with Windows 10+ |

Check your Python version:

```bash
python3 --version   # Ubuntu
py -3 --version     # Windows
```

If Python is missing on Ubuntu: `sudo apt install python3 python3-pip`

---

## Ubuntu — Backup existing installation

Run this **before** any removal. The script saves all Navig config, data, systemd unit files, and crontab entries to a single timestamped directory.

```bash
# On the Ubuntu server, as the navig user (with sudo)
bash scripts/navig_ubuntu_backup_uninstall.sh
```

The script will:
1. Create `~/navig_backup_<timestamp>/`
2. Copy `~/.navig/`, `/etc/navig/`, `/var/lib/navig/`, `/var/log/navig/`, `/opt/navig/`
3. Copy systemd unit files
4. Dump current crontab
5. **Abort automatically if the backup directory is empty**

**Verification:**
```bash
find ~/navig_backup_* -type f | wc -l
# Must return a number greater than 0
```

---

## Ubuntu — Full removal

The same script continues after the backup and performs a full removal:

```bash
# Continues automatically from the backup script above
# Or run standalone after a backup already exists:
bash scripts/navig_ubuntu_backup_uninstall.sh
```

Manual steps (if you prefer to run them yourself):

```bash
# 1. Stop and disable services
sudo systemctl stop navig navig-stack 2>/dev/null || true
sudo systemctl disable navig navig-stack 2>/dev/null || true

# 2. Remove the pip package
pip uninstall navig -y

# 3. Remove residual files
sudo rm -rf /etc/navig /var/lib/navig /var/log/navig /opt/navig ~/.navig
sudo rm -f /etc/systemd/system/navig.service /etc/systemd/system/navig-stack.service
sudo systemctl daemon-reload
```

**Verification — all three must return empty or "not found":**
```bash
which navig
pip show navig
systemctl status navig
```

---

## Ubuntu — Fresh install from PyPI

```bash
# Install (as the navig user, or any user with sudo)
pip install navig

# Initialize Navig (run once after first install)
navig init

# Start the daemon
navig start
```

If `navig` is not found after install, add pip's script directory to your PATH:
```bash
export PATH="$HOME/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

**Install and register as a systemd service (recommended for servers):**
```bash
# Copy the service file from the repo
sudo cp deploy/navig.service /etc/systemd/system/navig.service
sudo systemctl daemon-reload
sudo systemctl enable navig
sudo systemctl start navig
```

**Verification:**
```bash
navig --version          # Must print: navig 2.4.14 (or later)
navig status             # Must show: daemon running
systemctl is-active navig   # Must print: active
```

---

## Remote install from Windows via SSH

### Step 1 — Find the Ubuntu server on your network

Open PowerShell:
```powershell
# If nmap is installed:
nmap -sn 192.168.1.0/24    # adjust subnet to match your network

# Without nmap — quick ping sweep:
1..254 | ForEach-Object {
    $ip = "192.168.1.$_"
    if (Test-Connection -ComputerName $ip -Count 1 -Quiet -TimeoutSeconds 1) {
        Write-Host "Alive: $ip"
    }
}
```

### Step 2 — Set up SSH key auth (no password needed)

```powershell
# Generate a key (skip if you already have one)
ssh-keygen -t ed25519 -C "navig-deploy"

# Copy public key to the Ubuntu server
$pubKey = Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub" -Raw
ssh navig@<ubuntu_ip> "mkdir -p ~/.ssh; echo '$pubKey' >> ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys"

# Verify passwordless access
ssh navig@<ubuntu_ip>
# You should land in a shell without being asked for a password
```

### Step 3 — Trigger remote installation

```powershell
# Install, initialize, and start — all in one command
ssh navig@<ubuntu_ip> "pip install navig && navig init && navig start" 2>&1 | Tee-Object -FilePath navig_deploy.log

# Verify
ssh navig@<ubuntu_ip> "navig --version && navig status"
```

### Using the automation script

```powershell
# Auto-detect IP + install:
.\scripts\navig_windows_remote_deploy.ps1 -Target ubuntu -Scan -Subnet 192.168.1.0/24

# Known IP:
.\scripts\navig_windows_remote_deploy.ps1 -Target ubuntu -UbuntuIp 192.168.1.50

# Dry run (preview commands without executing):
.\scripts\navig_windows_remote_deploy.ps1 -Target ubuntu -UbuntuIp 192.168.1.50 -DryRun
```

**Verification:**
```powershell
ssh navig@<ubuntu_ip> "navig status"
# Must show: daemon running
```

---

## Windows laptop install

### Option A — Automated (recommended)

1. On the Windows laptop, open PowerShell **as Administrator** and enable the SSH server:
   ```powershell
   Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
   Start-Service sshd
   Set-Service -Name sshd -StartupType Automatic
   New-NetFirewallRule -Name sshd -DisplayName "OpenSSH Server" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
   ```

2. From your deployment machine, trigger the install:
   ```powershell
   .\scripts\navig_windows_remote_deploy.ps1 -Target windows -LaptopIp <laptop_ip> -LaptopUser <username>
   ```

### Option B — Direct install on the laptop

Open a regular PowerShell window on the laptop:

```powershell
pip install navig
navig init
navig --version   # Must print navig version
```

If Python is not installed, download it from [python.org](https://python.org) (check "Add to PATH" during setup) and then run the commands above.

**Verification:**
```powershell
navig --version    # Must print correct version
navig status       # Daemon status
```

---

## Build and publish to PyPI (maintainers only)

These steps produce distributable packages and upload them to PyPI.

### Prerequisites

```bash
pip install build twine
```

Ensure these are set before uploading:
- `TWINE_USERNAME=__token__`
- `TWINE_PASSWORD=<your-pypi-api-token>`

Or configure `~/.pypirc`:
```ini
[pypi]
username = __token__
password = pypi-...
```

### Build

```bash
# From the repo root
python -m build

# Verify artifacts (must print PASSED for both)
python -m twine check dist/*
```

Expected output:
```
Checking dist/navig-2.4.14-py3-none-any.whl: PASSED
Checking dist/navig-2.4.14.tar.gz: PASSED
```

### Test on TestPyPI first

```bash
twine upload --repository testpypi dist/*

# Smoke test on a clean virtualenv
python -m venv /tmp/navig-test-env
/tmp/navig-test-env/bin/pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ navig==2.4.14
/tmp/navig-test-env/bin/navig --version   # Must print 2.4.14
```

### Publish to production PyPI (automated)

Primary path: publish a GitHub Release (non-draft, non-prerelease) from a `v*` tag.
This automatically runs `.github/workflows/publish.yml`, which:
- validates release tag version against `pyproject.toml`
- builds sdist/wheel artifacts
- runs `twine check`
- publishes to PyPI via trusted publishing (OIDC)

Manual fallback (emergency only):

```bash
twine upload dist/*
```

Verify on a clean machine:
```bash
pip install navig==2.4.14
navig --version
```

### Automated release (recommended)

The `release.sh` script handles git tagging and pushing in one step:

```bash
bash scripts/release.sh 2.4.14
```

The script will:
1. Verify you are on `main` with a clean working tree
2. Create and push the annotated git tag
3. Sync release manifests
4. Prompt next steps to publish a GitHub Release
5. Trigger PyPI publishing via GitHub Actions when the release is published

---

## Final summary table

| Item | Value |
|---|---|
| PyPI package name | `navig` |
| PyPI URL | `https://pypi.org/project/navig/` |
| Current version | `2.4.14` |
| Ubuntu service user | `navig` |
| Ubuntu systemd unit | `/etc/systemd/system/navig.service` |
| Ubuntu data directory | `/opt/navig` / `~/.navig/` |
| Backup script | `scripts/navig_ubuntu_backup_uninstall.sh` |
| Reinstall script | `scripts/navig_ubuntu_reinstall.sh` |
| Windows remote deploy | `scripts/navig_windows_remote_deploy.ps1` |
| Release + publish script | `scripts/release.sh <version>` |

---

## Troubleshooting

**`navig` command not found after pip install**
```bash
export PATH="$HOME/.local/bin:$PATH"
```

**Permission errors on Ubuntu**
```bash
sudo chown -R navig:navig ~/.navig /opt/navig
```

**SSH: "Permission denied (publickey)"**
- Confirm `~/.ssh/authorized_keys` on the server contains your public key
- Confirm permissions: `chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys`
- Test verbose: `ssh -v navig@<server_ip>`

**Daemon fails to start**
```bash
journalctl -u navig -n 50
navig daemon start --foreground   # run in foreground to see errors
```

**PyPI upload: "403 Forbidden"**
- Verify your API token is correct and scoped to the `navig` project
- Ensure `TWINE_USERNAME=__token__` (literally the string `__token__`, not your username)
