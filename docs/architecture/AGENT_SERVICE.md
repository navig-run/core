# Agent Service Installation Guide

Install NAVIG agent as a system service for 24/7 autonomous operation.

## Overview

The service installer enables NAVIG agent to run continuously as a background service, starting automatically on system boot and restarting on failure.

**Supported Platforms:**
- Linux (systemd)
- macOS (launchd)
- Windows (Windows Service via nssm or sc.exe)

## Quick Start

### Install Service

```bash
# Install and start service
navig agent service install

# Install without starting
navig agent service install --no-start
```

### Check Status

```bash
navig agent service status
```

### Uninstall

```bash
navig agent service uninstall
```

## Platform-Specific Details

### Linux (systemd)

**User Service** (recommended for non-root users):
- Location: `~/.config/systemd/user/navig-agent.service`
- Command: `systemctl --user status navig-agent`

**System Service** (requires root):
- Location: `/etc/systemd/system/navig-agent.service`
- Command: `systemctl status navig-agent`

**Unit File Contents:**
```ini
[Unit]
Description=NAVIG Autonomous Agent
After=network.target

[Service]
Type=simple
User=<your-username>
WorkingDirectory=/home/<user>
ExecStart=/usr/bin/python3 -m navig agent start --foreground
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

**Manual Management:**
```bash
# User service
systemctl --user start navig-agent
systemctl --user stop navig-agent
systemctl --user restart navig-agent
systemctl --user enable navig-agent
systemctl --user disable navig-agent

# System service (with sudo)
sudo systemctl start navig-agent
sudo systemctl stop navig-agent
```

**View Logs:**
```bash
# User service
journalctl --user -u navig-agent -f

# System service
sudo journalctl -u navig-agent -f
```

### macOS (launchd)

**Service Location:**
- `~/Library/LaunchAgents/com.navig.agent.plist`

**Plist Contents:**
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.navig.agent</string>
    
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>-m</string>
        <string>navig</string>
        <string>agent</string>
        <string>start</string>
        <string>--foreground</string>
    </array>
    
    <key>RunAtLoad</key>
    <true/>
    
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    
    <key>StandardOutPath</key>
    <string>/Users/<user>/.navig/logs/agent.stdout.log</string>
    
    <key>StandardErrorPath</key>
    <string>/Users/<user>/.navig/logs/agent.stderr.log</string>
</dict>
</plist>
```

**Manual Management:**
```bash
# Load service
launchctl load ~/Library/LaunchAgents/com.navig.agent.plist

# Unload service
launchctl unload ~/Library/LaunchAgents/com.navig.agent.plist

# Start service
launchctl start com.navig.agent

# Stop service
launchctl stop com.navig.agent

# Check status
launchctl list | grep navig
```

**View Logs:**
```bash
tail -f ~/.navig/logs/agent.stdout.log
tail -f ~/.navig/logs/agent.stderr.log
```

### Windows (Windows Service)

**Prerequisites:**
- **Administrator privileges required**
- **nssm (recommended)**: Download from https://nssm.cc/
  - Or install via: `choco install nssm` / `scoop install nssm`

**Service Details:**
- Name: `NAVIGAgent`
- Display Name: `NAVIG Agent`
- Start Type: Automatic

**Manual Management:**

Using **nssm** (recommended):
```powershell
# Install
nssm install NAVIGAgent "C:\Python\python.exe" "-m navig agent start --foreground"

# Start
nssm start NAVIGAgent

# Stop
nssm stop NAVIGAgent

# Remove
nssm remove NAVIGAgent confirm
```

Using **sc.exe** (built-in):
```powershell
# Start
sc start NAVIGAgent

# Stop
sc stop NAVIGAgent

# Query status
sc query NAVIGAgent

# Delete
sc delete NAVIGAgent
```

Using **Services GUI**:
1. Press `Win + R`, type `services.msc`
2. Find "NAVIG Agent"
3. Right-click → Start/Stop/Restart

**View Logs:**
```powershell
# Event Viewer
eventvwr.msc

# Or check NAVIG logs
Get-Content "$env:USERPROFILE\.navig\logs\debug.log" -Tail 50 -Wait
```

## Configuration

### Agent Config

Service uses agent configuration from `~/.navig/agent/config.yaml`:

```yaml
agent:
  enabled: true
  mode: supervised  # or autonomous

  brain:
    model: openrouter:anthropic/claude-3.5-sonnet

  eyes:
    monitoring_interval: 60

  ears:
    telegram:
      enabled: false
    mcp:
      enabled: true
```

### Environment Variables

Service inherits environment variables. For secrets:

**Linux/macOS:**
```bash
# Add to ~/.bashrc or ~/.zshrc
export OPENROUTER_API_KEY="your-key"
export TELEGRAM_BOT_TOKEN="your-token"

# Then reload service
systemctl --user restart navig-agent  # Linux
launchctl restart com.navig.agent     # macOS
```

**Windows:**
```powershell
# Set system environment variable
[System.Environment]::SetEnvironmentVariable("OPENROUTER_API_KEY", "your-key", "User")

# Restart service
Restart-Service NAVIGAgent
```

## Troubleshooting

### Service Won't Start

**Check Python path:**
```bash
which python3  # Linux/macOS
where python   # Windows
```

Update service file with correct Python path.

**Check permissions:**
```bash
# Linux - ensure user has read/write to ~/.navig
ls -la ~/.navig

# macOS - same as above

# Windows - run PowerShell as Administrator
```

**Check logs:**
```bash
# Linux
journalctl --user -u navig-agent -n 50

# macOS
cat ~/.navig/logs/agent.stderr.log

# Windows
Get-Content "$env:USERPROFILE\.navig\logs\debug.log" -Tail 50
```

### Service Keeps Restarting

Check agent logs for errors:
```bash
navig agent logs --level error
```

Common issues:
- Missing API keys (OpenRouter, Telegram)
- Invalid configuration in `~/.navig/agent/config.yaml`
- Port conflicts (MCP server default 8765)

### Can't Stop Service

**Linux:**
```bash
# Force stop
systemctl --user kill navig-agent

# Or find and kill process
ps aux | grep navig
kill -9 <pid>
```

**macOS:**
```bash
launchctl remove com.navig.agent
pkill -9 -f "navig agent"
```

**Windows:**
```powershell
# Force stop
Stop-Service NAVIGAgent -Force

# Or Task Manager → Services → NAVIG Agent → Stop
```

### Service Not Auto-Starting on Boot

**Linux:**
```bash
# Enable service
systemctl --user enable navig-agent

# Check if enabled
systemctl --user is-enabled navig-agent
```

**macOS:**
```bash
# Check RunAtLoad is true in plist
plutil -p ~/Library/LaunchAgents/com.navig.agent.plist | grep RunAtLoad
```

**Windows:**
```powershell
# Check startup type
sc qc NAVIGAgent

# Set to automatic
sc config NAVIGAgent start= auto
```

## Security Considerations

### File Permissions

Ensure config files are protected:

```bash
# Linux/macOS
chmod 700 ~/.navig
chmod 600 ~/.navig/agent/config.yaml

# Windows (PowerShell)
icacls "$env:USERPROFILE\.navig" /inheritance:r /grant:r "$env:USERNAME:(OI)(CI)F"
```

### Running as Non-Root

**Linux - User Service (Recommended):**
```bash
# Install as user service (no sudo)
navig agent service install
```

Benefits:
- No root access required
- Isolated to user account
- Easier debugging

**Linux - System Service (Not Recommended):**
Only use if agent needs system-wide access. Create dedicated user:

```bash
sudo useradd -r -s /bin/false navig
sudo -u navig navig agent service install
```

### Network Access

Service may need firewall rules for:
- MCP server (default port 8765)
- Telegram bot API (outbound HTTPS)
- SSH connections to managed hosts

## Best Practices

### 1. Test Before Service Installation

```bash
# Test agent runs correctly
navig agent start --foreground

# Verify all components work
navig agent status

# Check for errors
navig agent logs
```

### 2. Monitor Service Health

```bash
# Check service status regularly
navig agent service status

# View recent logs
navig agent logs --level warning

# Monitor remediation activity
navig agent remediation list
```

### 3. Update Strategy

When updating NAVIG:

```bash
# Stop service
navig agent service uninstall

# Update NAVIG
pip install --upgrade navig

# Reinstall service
navig agent service install
```

### 4. Backup Configuration

```bash
# Backup before changes
cp -r ~/.navig ~/.navig.backup

# After successful changes, create timestamped backup
tar -czf ~/.navig/backups/config-$(date +%Y%m%d).tar.gz ~/.navig/agent/config.yaml
```

## Advanced Configuration

### Custom Service Name (Linux)

Edit service file to use custom name:
```bash
cp ~/.config/systemd/user/navig-agent.service ~/.config/systemd/user/navig-prod.service
systemctl --user enable navig-prod
systemctl --user start navig-prod
```

### Multiple Instances

Run multiple agents (different configs):

```bash
# Instance 1 (default)
navig agent service install

# Instance 2 (custom config)
# Manually create service with different:
# - Service name
# - Config directory
# - MCP port
```

### Service Dependencies (Linux)

Add dependencies to service file:

```ini
[Unit]
After=network-online.target postgresql.service
Wants=network-online.target
```

## Performance Tuning

### Resource Limits (Linux)

Add to service file:

```ini
[Service]
MemoryMax=512M
CPUQuota=50%
TasksMax=100
```

### Log Rotation

**Linux (journald):**
```bash
# Limit journal size
sudo journalctl --vacuum-size=100M
```

**macOS/Windows:**
```bash
# Use logrotate or similar tool
# Or manually clean old logs
find ~/.navig/logs -name "*.log" -mtime +30 -delete
```

## Uninstallation

### Complete Removal

```bash
# Stop and remove service
navig agent service uninstall

# Remove configuration (optional)
rm -rf ~/.navig/agent

# Remove logs (optional)
rm -rf ~/.navig/logs
```

## See Also

- [Agent Mode Overview](AGENT_MODE.md)
- [Agent Self-Healing](AGENT_SELF_HEALING.md)
- [Agent Learning](AGENT_LEARNING.md)
- [Troubleshooting Guide](troubleshooting.md)


