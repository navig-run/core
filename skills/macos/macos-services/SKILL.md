---
name: macos-services
description: Manage macOS services and daemons using launchctl, plus system info and maintenance
user-invocable: true
os: [darwin]
navig-commands:
  - navig run "launchctl list"
  - navig run "launchctl kickstart system/{service}"
  - navig run "system_profiler SPSoftwareDataType"
  - navig run "top -l 1 -n 0 | head -10"
examples:
  - "What services are running on the Mac?"
  - "Check macOS system info"
  - "Restart a service on macOS"
  - "How much memory is the Mac using?"
  - "Show Mac system info"
---

# macOS Service & System Management

Manage services, check system health, and perform maintenance on macOS machines.

## System Info

### Quick System Overview

**User says:** "Show Mac system info"

```bash
sw_vers
sysctl -n hw.memsize | awk '{print $0/1073741824 " GB RAM"}'
sysctl -n hw.ncpu
uptime
```

**Response format:**
```
🍎 macOS System Info:

OS: macOS 15.2 Sequoia
Model: MacBook Pro (M3 Pro)
CPU: 12 cores
RAM: 36 GB
Uptime: 5 days, 3 hours
```

### Memory Usage

```bash
top -l 1 -n 0 | head -12
```

Or more readable:

```bash
memory_pressure
```

### Disk Space

```bash
df -h / | tail -1
```

## Service Management (launchctl)

macOS uses `launchd` instead of systemd. Services are called "launch agents" (user) and "launch daemons" (system).

### List Running Services

```bash
launchctl list | head -30
```

### Check Specific Service

```bash
launchctl list | grep {service}
```

### Start a Service

```bash
# System service
sudo launchctl kickstart system/{service}

# User service
launchctl kickstart gui/$(id -u)/{service}
```

### Stop a Service

```bash
sudo launchctl kill SIGTERM system/{service}
```

### Load/Unload a Service

```bash
# Load (enable)
sudo launchctl load /Library/LaunchDaemons/{service}.plist

# Unload (disable)
sudo launchctl unload /Library/LaunchDaemons/{service}.plist
```

## Homebrew Services (Preferred)

For services installed via Homebrew, prefer `brew services` (much simpler):

```bash
brew services list                    # List all
brew services start {service}         # Start
brew services stop {service}          # Stop
brew services restart {service}       # Restart
```

See the **homebrew** skill for details.

## Common macOS Admin Tasks

### Flush DNS Cache

```bash
sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder
```

### Check Open Ports

```bash
lsof -iTCP -sTCP:LISTEN -n -P
```

**Response format:**
```
🌐 Open Ports on Mac:

• :3000 - node (PID: 1234)
• :5432 - postgres (PID: 5678)
• :6379 - redis (PID: 9012)
• :8080 - java (PID: 3456)
```

### Kill Process by Port

```bash
lsof -ti:8080 | xargs kill -9
```

### Check Battery Health (Laptops)

```bash
system_profiler SPPowerDataType | grep -A5 "Health"
```

### Check Storage Breakdown

```bash
du -sh ~/Library/Caches ~/Library/Logs ~/Downloads 2>/dev/null
```

### Empty Trash from Terminal

```bash
rm -rf ~/.Trash/*
```

### Rebuild Spotlight Index

```bash
sudo mdutil -E /
```

## macOS vs Linux Reference

| Task | Linux | macOS |
|------|-------|-------|
| Service status | `systemctl status x` | `launchctl list \| grep x` |
| Start service | `systemctl start x` | `launchctl kickstart system/x` |
| Stop service | `systemctl stop x` | `launchctl kill SIGTERM system/x` |
| Package install | `apt install x` | `brew install x` |
| Firewall | `ufw` | `pfctl` |
| Process viewer | `htop` | `htop` or `top -o cpu` |
| Open ports | `ss -tlnp` | `lsof -iTCP -sTCP:LISTEN` |

## Safety Rules

- **Safe**: `launchctl list`, `sw_vers`, `top`, `df` (read-only)
- **Confirm**: `brew services start/stop`, `killall`, `kill`
- **Double confirm**: `launchctl unload`, `rm -rf`, `mdutil -E`

## Error Handling

- **Permission denied**: "This requires sudo. Try with admin privileges."
- **Service not found**: "Service '{name}' not found. Check with `launchctl list | grep {name}`"
- **SIP restriction**: "System Integrity Protection prevents this action. Some system services can't be modified."


