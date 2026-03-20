---
name: package-manager
description: Install, update, and manage packages on Linux using apt, yum, or dnf
user-invocable: true
os: [linux]
navig-commands:
  - navig run "apt list --installed"
  - navig run "apt update && apt upgrade -y"
  - navig run "apt install -y {package}"
  - navig run "apt remove {package}"
  - navig run "apt search {query}"
examples:
  - "Update packages on the server"
  - "Install htop"
  - "What packages are installed?"
  - "Is nginx installed?"
  - "Remove old packages"
  - "Check for security updates"
---

# Linux Package Management

Install, update, and manage system packages on remote Linux servers.

## Auto-Detect Package Manager

```bash
# Detect which package manager is available
navig run "which apt && echo 'DEBIAN' || (which dnf && echo 'FEDORA' || (which yum && echo 'CENTOS'))"
```

## Debian/Ubuntu (apt)

### Update Package Lists

```bash
navig run "apt update"
```

### Upgrade All Packages

⚠️ Confirm: "This will upgrade all packages. Continue?"

```bash
navig run "apt update && apt upgrade -y"
```

**Response format:**
```
📦 Package Update on {host}:

Updated: 12 packages
- nginx 1.24.0 → 1.25.3
- openssl 3.0.11 → 3.0.13
- libssl3 3.0.11 → 3.0.13
- ...8 more

✅ System is up to date!

💡 Reboot needed: No
```

### Install a Package

```bash
navig run "apt install -y {package}"
```

### Remove a Package

⚠️ Confirm first!

```bash
navig run "apt remove {package}"
```

### Search for Packages

```bash
navig run "apt search {query} 2>/dev/null | head -20"
```

### Check If Package Is Installed

```bash
navig run "dpkg -l | grep {package}"
```

### List Installed Packages

```bash
navig run "dpkg --get-selections | grep -v deinstall | wc -l"
```

### Check for Security Updates

```bash
navig run "apt list --upgradable 2>/dev/null"
```

**Response format:**
```
🔒 Security Updates Available on {host}:

🔴 Critical:
• openssl 3.0.11 → 3.0.13 (CVE-2024-xxxx)
• linux-image 5.15.0-91 → 5.15.0-94

🟡 Recommended:
• nginx 1.24.0 → 1.25.3
• curl 7.81.0 → 7.88.1

💡 Run updates: "update my server packages"
```

### Clean Up Old Packages

```bash
navig run "apt autoremove -y && apt autoclean"
```

## RHEL/CentOS/Fedora (dnf/yum)

### Update All

```bash
navig run "dnf update -y"
```

### Install

```bash
navig run "dnf install -y {package}"
```

### Remove

```bash
navig run "dnf remove {package}"
```

### Search

```bash
navig run "dnf search {query}"
```

### Check for Updates

```bash
navig run "dnf check-update"
```

### List Installed

```bash
navig run "rpm -qa | wc -l"
```

## Common Packages to Install

| Package | Description | Command |
|---------|-------------|---------|
| `htop` | Interactive process viewer | `apt install -y htop` |
| `tmux` | Terminal multiplexer | `apt install -y tmux` |
| `curl` | HTTP client | `apt install -y curl` |
| `wget` | File downloader | `apt install -y wget` |
| `git` | Version control | `apt install -y git` |
| `unzip` | Archive extraction | `apt install -y unzip` |
| `ncdu` | Disk usage analyzer | `apt install -y ncdu` |
| `net-tools` | Network utilities | `apt install -y net-tools` |
| `fail2ban` | Intrusion prevention | `apt install -y fail2ban` |
| `ufw` | Firewall | `apt install -y ufw` |

## Safety Rules

- **Safe**: `apt list`, `dpkg -l`, `apt search`, `apt policy` (read-only)
- **Confirm**: `apt install`, `apt update` (modifies system)
- **Double confirm**: `apt remove`, `apt purge`, `apt dist-upgrade` (destructive)

## Proactive Suggestions

- **Outdated system**: "⚠️ System hasn't been updated in 30+ days. Run updates?"
- **Reboot required**: "🔄 Kernel update installed. Reboot needed to apply."
- **Many upgradable**: "📦 42 packages can be upgraded. Want to update?"
- **Orphaned packages**: "🧹 15 packages are no longer needed. Clean up with autoremove?"

## Error Handling

- **Package not found**: "Package '{name}' not found. Try: `apt search {name}`"
- **Broken dependencies**: "Dependency issues detected. Try: `apt --fix-broken install`"
- **Locked**: "Package manager is locked (another process is running). Wait or check `ps aux | grep apt`"
- **No space**: "Not enough disk space to install. Free some space first."


