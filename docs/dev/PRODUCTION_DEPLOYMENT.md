# NAVIG Agent Production Deployment Guide

Complete checklist and procedures for deploying NAVIG agent to production.

## Overview

This guide covers:
1. Pre-deployment verification
2. Installation procedures
3. Post-deployment validation
4. Operational monitoring
5. Maintenance procedures
6. Incident response

## Prerequisites

### System Requirements

**Minimum:**
- Python 3.10+
- 2GB RAM
- 10GB disk space
- Network connectivity

**Recommended:**
- Python 3.11+
- 4GB RAM
- 20GB disk space
- Static IP or domain name
- Monitoring system

### Required Credentials

- OpenRouter API key (for Brain AI)
- Telegram Bot Token (if using Telegram integration)
- SSH keys for remote host access
- Database credentials

### Configuration Files

Ensure these are prepared:
- `~/.navig/config.yaml` - Main configuration
- `~/.navig/hosts/*.yaml` - Host definitions
- `~/.navig/apps/*.yaml` - Application definitions

## Pre-Deployment Checklist

### 1. Test Locally

```bash
# Test all components
navig agent status
navig agent test-brain
navig agent test-remediation
navig agent test-learning

# Test remote connections
navig host list
navig host test <host-name>

# Test database operations
navig db list
navig db test-connection
```

### 2. Verify Configuration

```bash
# Check config validity
navig config validate

# Check API keys
navig config show | grep -E '(openrouter|telegram)'

# Test Brain connection
navig agent test-brain
```

### 3. Backup Current State

```bash
# Create backup
mkdir -p ~/.navig-backup-$(date +%Y%m%d)
cp -r ~/.navig/* ~/.navig-backup-$(date +%Y%m%d)/

# On Linux/macOS
tar -czf navig-backup-$(date +%Y%m%d).tar.gz ~/.navig/

# On Windows (PowerShell)
Compress-Archive -Path $env:USERPROFILE\.navig -DestinationPath navig-backup-$(Get-Date -Format yyyyMMdd).zip
```

### 4. Review Resource Requirements

```bash
# Check disk space
df -h ~/.navig  # Linux/macOS
Get-PSDrive C | Select-Object Used,Free  # Windows

# Check memory
free -h  # Linux
vm_stat  # macOS
Get-CimInstance Win32_OperatingSystem | Select-Object FreePhysicalMemory  # Windows

# Check network
ping -c 3 api.openrouter.ai
```

### 5. Security Review

```bash
# Check file permissions
ls -la ~/.navig  # Linux/macOS
icacls $env:USERPROFILE\.navig  # Windows

# Secure sensitive files
chmod 600 ~/.navig/config.yaml  # Linux/macOS
chmod 700 ~/.navig/workspace  # Linux/macOS

# Review SSH keys
ls -la ~/.ssh
chmod 600 ~/.ssh/id_* ~/.ssh/authorized_keys
```

## Installation

### Option 1: Systemd (Linux - Recommended)

```bash
# Install as user service (non-root)
navig agent service install --user

# Verify installation
systemctl --user status navig-agent

# Enable auto-start
systemctl --user enable navig-agent

# Start service
systemctl --user start navig-agent

# Check logs
journalctl --user -u navig-agent -f
```

### Option 2: Launchd (macOS)

```bash
# Install as user agent
navig agent service install

# Verify installation
launchctl list | grep navig

# Start service
launchctl load ~/Library/LaunchAgents/com.navig.agent.plist

# Check logs
tail -f ~/.navig/agent.log
```

### Option 3: Windows Service

```bash
# Install (requires admin PowerShell)
navig agent service install

# Verify installation
Get-Service navig-agent

# Start service
Start-Service navig-agent

# Check status
Get-Service navig-agent | Format-List *

# Check logs
Get-EventLog -LogName Application -Source "navig-agent" -Newest 20
```

### Option 4: Docker Container

```bash
# Build image
docker build -t navig-agent:latest .

# Run container
docker run -d \
  --name navig-agent \
  --restart unless-stopped \
  -v ~/.navig:/root/.navig:ro \
  -v ~/.navig/workspace:/root/.navig/workspace:rw \
  -e OPENROUTER_API_KEY=$OPENROUTER_API_KEY \
  -e TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN \
  navig-agent:latest

# Check logs
docker logs -f navig-agent

# Check status
docker exec navig-agent navig agent status
```

## Post-Deployment Validation

### 1. Service Running

```bash
# Check service status
navig agent service status

# Expected output: "Service is running"
```

### 2. Component Health

```bash
# Check all components
navig agent status

# Expected output:
# ✓ Heart: running
# ✓ Brain: connected
# ✓ Hands: ready
# ✓ Remediation: active
# ✓ Learning: active
```

### 3. Auto-Restart Behavior

```bash
# Test auto-restart
navig agent service stop

# Wait 30 seconds
sleep 30

# Verify restarted
navig agent service status
```

### 4. Brain Connectivity

```bash
# Test AI connection
navig agent test-brain

# Expected: Response from OpenRouter API
```

### 5. Remediation Engine

```bash
# Verify remediation is active
navig agent remediation list

# Should show learned patterns from debug.log
```

### 6. Learning System

```bash
# Check learning is working
navig agent learn

# Should detect patterns from logs
```

### 7. Log Files

```bash
# Check logs are being written
tail -n 50 ~/.navig/debug.log

# Verify no critical errors
grep -i error ~/.navig/debug.log | tail -n 20
```

## Monitoring Setup

### 1. Health Checks

```bash
# Set up periodic health check (cron on Linux/macOS)
crontab -e

# Add this line:
*/5 * * * * navig agent status --plain > /tmp/navig-health.txt 2>&1

# On Windows (Task Scheduler)
schtasks /create /tn "NAVIG Health Check" /tr "navig agent status --plain" /sc minute /mo 5
```

### 2. Log Rotation

**Linux/macOS:**
```bash
# Create logrotate config
sudo tee /etc/logrotate.d/navig <<EOF
$HOME/.navig/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    create 644 $USER $USER
}
EOF
```

**Windows (PowerShell):**
```powershell
# Create log rotation script
$scriptPath = "$env:USERPROFILE\.navig\scripts\rotate-logs.ps1"
@"
Get-ChildItem "$env:USERPROFILE\.navig\*.log" |
Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
Remove-Item
"@ | Out-File -FilePath $scriptPath

# Schedule daily
schtasks /create /tn "NAVIG Log Rotation" /tr "powershell.exe -File $scriptPath" /sc daily /st 00:00
```

### 3. Alerts

Set up Telegram alerts (if enabled):

```bash
# Configure alert thresholds
navig config --set brain.alert_threshold error
navig config --set brain.alert_interval 300

# Test alerts
navig agent send-test-alert
```

### 4. Prometheus Metrics (Advanced)

```bash
# Enable metrics endpoint
navig config --set agent.metrics_enabled true
navig config --set agent.metrics_port 9090

# Restart agent
navig agent service restart

# Test metrics endpoint
curl http://localhost:9090/metrics
```

## Operational Procedures

### Daily Checks

```bash
# 1. Service status
navig agent service status

# 2. Component health
navig agent status

# 3. Error count
grep -c ERROR ~/.navig/debug.log

# 4. Remediation stats
navig agent remediation stats
```

### Weekly Maintenance

```bash
# 1. Review learned patterns
navig agent learn --analyze

# 2. Check disk space
df -h ~/.navig

# 3. Review logs for anomalies
grep -i "unusual\|unexpected\|anomaly" ~/.navig/debug.log

# 4. Update dependencies
pip install --upgrade -r requirements.txt

# 5. Restart service
navig agent service restart
```

### Monthly Tasks

```bash
# 1. Full backup
tar -czf navig-backup-$(date +%Y%m).tar.gz ~/.navig/

# 2. Analyze learning trends
navig agent learn --report

# 3. Review goal completion rate
navig agent goal list --completed --since "1 month ago"

# 4. Security audit
navig audit --security

# 5. Performance review
navig agent stats --period month
```

### Quarterly Updates

```bash
# 1. Check for NAVIG updates
pip install --upgrade navig

# 2. Review configuration
navig config validate
navig config audit

# 3. Test disaster recovery
# - Restore from backup
# - Verify all components work
# - Document any issues

# 4. Capacity planning
# - Review disk usage trends
# - Check memory usage patterns
# - Plan for scaling if needed
```

## Incident Response

### Service Not Running

```bash
# 1. Check service status
navig agent service status

# 2. Check logs
tail -n 100 ~/.navig/debug.log

# 3. Try manual start
navig agent service start

# 4. If fails, check config
navig config validate

# 5. Check API keys
navig agent test-brain
```

### High Error Rate

```bash
# 1. Identify error types
grep ERROR ~/.navig/debug.log | cut -d' ' -f5- | sort | uniq -c | sort -rn

# 2. Check remediation attempts
navig agent remediation list

# 3. Force learn new patterns
navig agent learn --force

# 4. Check external dependencies
ping api.openrouter.ai
navig host test-all
```

### Memory/CPU High

```bash
# 1. Check resource usage
top -p $(pgrep -f "navig agent")  # Linux
ps aux | grep "navig agent"  # macOS
Get-Process | Where-Object {$_.ProcessName -like "*navig*"}  # Windows

# 2. Check for stuck goals
navig agent goal list --state blocked

# 3. Restart service
navig agent service restart

# 4. Review configuration
navig config show | grep -E '(interval|timeout|batch)'
```

### Data Corruption

```bash
# 1. Stop service
navig agent service stop

# 2. Backup current state
cp -r ~/.navig ~/.navig-corrupt-$(date +%Y%m%d)

# 3. Restore from last good backup
tar -xzf navig-backup-YYYYMMDD.tar.gz -C ~

# 4. Verify configuration
navig config validate

# 5. Start service
navig agent service start
```

## Rollback Procedure

If deployment fails or causes issues:

```bash
# 1. Stop service
navig agent service stop

# 2. Uninstall service
navig agent service uninstall

# 3. Restore backup
rm -rf ~/.navig
cp -r ~/.navig-backup-YYYYMMDD ~/.navig

# 4. Verify old version works
navig agent status

# 5. Reinstall service if needed
navig agent service install
```

## Scaling Considerations

### Single Instance Limits

- Max ~1000 hosts
- Max ~500 simultaneous connections
- Max ~100 goals in progress

### Multi-Instance Setup

For larger deployments:

```bash
# Instance 1: Monitoring & remediation
navig agent service install --name navig-monitor --config ~/.navig/config-monitor.yaml

# Instance 2: Goal execution
navig agent service install --name navig-executor --config ~/.navig/config-executor.yaml

# Instance 3: Learning & analytics
navig agent service install --name navig-analytics --config ~/.navig/config-analytics.yaml
```

## Security Hardening

### 1. Principle of Least Privilege

```bash
# Run as non-root user
useradd -r -m -d /opt/navig -s /bin/bash navig
sudo -u navig navig agent service install --user

# Restrict file permissions
chmod 700 /opt/navig/.navig
chmod 600 /opt/navig/.navig/config.yaml
```

### 2. Network Security

```bash
# Firewall rules (example)
sudo ufw allow from 10.0.0.0/24 to any port 9090  # Metrics
sudo ufw deny 9090  # Block external access

# Or restrict to localhost
navig config --set agent.metrics_bind 127.0.0.1
```

### 3. API Key Rotation

```bash
# Rotate OpenRouter API key
navig config --set brain.openrouter_api_key "new-key-here"
navig agent service restart

# Verify new key works
navig agent test-brain
```

### 4. Audit Logging

```bash
# Enable audit log
navig config --set security.audit_enabled true
navig config --set security.audit_log ~/.navig/audit.log

# Review audit log
tail -f ~/.navig/audit.log
```

## Performance Tuning

### Optimize Heartbeat Interval

```bash
# Default: 60 seconds
# Reduce for faster response (more CPU)
navig config --set agent.heartbeat_interval 30

# Increase to save resources (slower response)
navig config --set agent.heartbeat_interval 120
```

### Adjust Learning Frequency

```bash
# Default: Continuous
# Periodic learning (less CPU)
navig config --set agent.learning_mode periodic
navig config --set agent.learning_interval 3600  # Every hour
```

### Connection Pool Tuning

```bash
# Increase for more concurrent operations
navig config --set connection_pool.max_size 20
navig config --set connection_pool.max_overflow 10

# Decrease for fewer resources
navig config --set connection_pool.max_size 5
```

## Disaster Recovery

### Backup Strategy

**What to Backup:**
- `~/.navig/config.yaml` - Configuration
- `~/.navig/workspace/` - Goals, cache, state
- `~/.navig/hosts/` - Host definitions
- `~/.navig/apps/` - Application definitions

**Backup Schedule:**
- Daily: Configuration files
- Weekly: Full workspace
- Monthly: Complete ~/.navig directory

**Backup Script (Linux/macOS):**
```bash
#!/bin/bash
BACKUP_DIR="/backup/navig"
DATE=$(date +%Y%m%d)

# Daily backup
tar -czf "$BACKUP_DIR/daily/config-$DATE.tar.gz" ~/.navig/config.yaml ~/.navig/hosts ~/.navig/apps

# Weekly backup (Sundays)
if [ $(date +%u) -eq 7 ]; then
    tar -czf "$BACKUP_DIR/weekly/workspace-$DATE.tar.gz" ~/.navig/workspace
fi

# Monthly backup (1st of month)
if [ $(date +%d) -eq 01 ]; then
    tar -czf "$BACKUP_DIR/monthly/full-$DATE.tar.gz" ~/.navig
fi

# Cleanup old backups
find "$BACKUP_DIR/daily" -mtime +7 -delete
find "$BACKUP_DIR/weekly" -mtime +30 -delete
find "$BACKUP_DIR/monthly" -mtime +365 -delete
```

### Recovery Procedure

```bash
# 1. Stop service
navig agent service stop

# 2. Restore from backup
tar -xzf /backup/navig/full-YYYYMMDD.tar.gz -C ~

# 3. Verify configuration
navig config validate

# 4. Test locally
navig agent status
navig agent test-brain

# 5. Restart service
navig agent service start

# 6. Verify all components
navig agent status
```

## Troubleshooting

### Service Won't Start

See [AGENT_SERVICE.md - Troubleshooting](AGENT_SERVICE.md#troubleshooting)

### Goals Not Executing

See [AGENT_GOALS.md - Troubleshooting](AGENT_GOALS.md#troubleshooting)

### High Memory Usage

```bash
# Check for memory leaks
navig agent stats --memory

# Restart service
navig agent service restart

# If persists, reduce cache
navig config --set cache.max_size 100
```

### Remediation Not Working

```bash
# Check remediation status
navig agent remediation stats

# Force re-learning
navig agent learn --force

# Verify patterns
navig agent remediation list
```

## Success Criteria

✅ **Deployment Complete When:**

1. Service running and auto-starts
2. All components healthy (Heart, Brain, Hands)
3. Remediation engine active with learned patterns
4. Learning system detecting and categorizing errors
5. Goals can be created and tracked
6. Logs being written and rotated
7. Monitoring alerts configured
8. Backups scheduled and tested
9. Documentation accessible to team
10. Incident response procedures documented

## Next Steps

After successful deployment:

1. **Monitor for 1 Week**
   - Check health daily
   - Review logs for anomalies
   - Verify auto-restart works

2. **Train Team**
   - Share documentation
   - Demo key features
   - Document custom workflows

3. **Optimize Configuration**
   - Adjust based on observed usage
   - Fine-tune intervals and thresholds
   - Add custom remediation patterns

4. **Plan Scaling**
   - Monitor resource usage trends
   - Plan for growth
   - Consider multi-instance setup

## Support

For issues or questions:

1. Check [Troubleshooting Guide](troubleshooting.md)
2. Review [Agent Documentation](AGENT_MODE.md)
3. Check debug logs: `~/.navig/debug.log`
4. File issues on GitHub

## See Also

- [Agent Mode Overview](AGENT_MODE.md)
- [Service Installation](AGENT_SERVICE.md)
- [Goal Planning](AGENT_GOALS.md)
- [Self-Healing](AGENT_SELF_HEALING.md)
- [Troubleshooting](troubleshooting.md)
