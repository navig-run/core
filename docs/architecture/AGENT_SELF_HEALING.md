# Agent Self-Healing

NAVIG's autonomous agent includes automatic self-healing capabilities that detect and recover from component failures without manual intervention.

## Overview

The self-healing system consists of:

1. **Remediation Engine** - Coordinates automatic recovery actions
2. **Heart Integration** - Detects component failures through health checks
3. **Eyes Monitoring** - Triggers remediation on threshold violations
4. **Exponential Backoff** - Prevents rapid failure loops

## How It Works

### Automatic Component Restart

When a component enters ERROR state:

1. Heart detects the failure during health check loop
2. Schedules restart action through remediation engine
3. Remediation engine executes restart with backoff:
   - Attempt 1: 1 second delay
   - Attempt 2: 2 seconds delay
   - Attempt 3: 4 seconds delay
   - Attempt 4: 8 seconds delay
   - Attempt 5: 16 seconds delay
   - Subsequent: 60 seconds delay
4. Maximum 5 attempts before giving up
5. All actions logged to `~/.navig/logs/remediation.log`

### Connection Retry

When connection failures are detected:

1. Eyes monitoring system detects connection issue
2. Schedules connection retry action
3. Remediation engine retries with same backoff pattern
4. Service-specific metadata tracked for debugging

### Configuration Rollback

When configuration changes cause failures:

1. Component fails to start with new config
2. Remediation engine triggered
3. Automatically restores last known good config from backup
4. Failed config saved to `config-failed-<timestamp>.yaml`
5. Component restarted with restored config

## Configuration

### Backup System

- Location: `~/.navig/workspace/config-backup/`
- Format: `<component>-config-<timestamp>.yaml`
- Automatic backup before config changes
- Failed configs preserved for troubleshooting

### Logs

- Remediation Log: `~/.navig/logs/remediation.log`
- Format: `[timestamp] [LEVEL] message`
- Includes: action scheduling, execution, success/failure

## CLI Commands

### View Remediation Actions

```bash
navig agent remediation list
```

Shows all current and recent remediation actions with:
- Status (PENDING, IN_PROGRESS, SUCCESS, FAILED, SKIPPED)
- Component name
- Reason for remediation
- Attempt count
- Error messages (if any)

### Check Specific Action

```bash
navig agent remediation status --id <action_id>
```

Detailed view of a specific remediation action including:
- Full metadata
- Timestamp
- Execution history

### Clear Completed Actions

```bash
navig agent remediation clear
```

Removes completed/failed actions older than 1 hour (automatic cleanup).

## Remediation Types

### COMPONENT_RESTART

- **Trigger**: Component enters ERROR state
- **Action**: Stop and restart component
- **Backoff**: Exponential (1s to 60s)
- **Max Attempts**: 5

### CONNECTION_RETRY

- **Trigger**: Network connection failure
- **Action**: Retry connection to service
- **Backoff**: Exponential (1s to 60s)
- **Max Attempts**: 5

### CONFIG_ROLLBACK

- **Trigger**: Component failure after config change
- **Action**: Restore previous configuration
- **Backup**: Automatic before changes
- **Max Attempts**: 1 (single rollback)

### PERMISSION_FIX

- **Trigger**: Permission denied errors
- **Action**: Attempt to fix file permissions
- **Max Attempts**: 2

### SERVICE_RESTART

- **Trigger**: System service failure
- **Action**: Restart system service (systemd/launchd)
- **Max Attempts**: 3

## Best Practices

### 1. Monitor Remediation Logs

```bash
tail -f ~/.navig/logs/remediation.log
```

Watch real-time remediation activity to understand failure patterns.

### 2. Review Failed Actions

If remediation keeps failing:

```bash
navig agent remediation list
```

Look for patterns in failed actions - they indicate underlying issues that need manual attention.

### 3. Preserve Config Backups

The backup directory contains your safety net:

```bash
ls ~/.navig/workspace/config-backup/
```

These are automatically created and managed, but you can manually restore if needed.

### 4. Combine with Learning

Use the learning system to analyze patterns:

```bash
navig agent learn --days 7
```

This reveals why remediation is being triggered frequently.

## Integration

### Heart Orchestrator

The Heart component manages component lifecycle and triggers remediation:

```python
# In health check loop
if component.state == ComponentState.ERROR:
    await remediation.schedule_restart(
        component=name,
        reason=health_status.message,
        metadata={'health': health_status.to_dict()}
    )
```

### Eyes Monitoring

Eyes detects threshold violations and triggers remediation:

```python
# When disk usage exceeds threshold
if disk_percent > 90:
    await remediation.schedule_connection_retry(
        component='storage',
        service='disk',
        reason=f'Disk usage critical: {disk_percent}%'
    )
```

## Troubleshooting

### Remediation Not Working

1. **Check agent status**:
   ```bash
   navig agent status
   ```

2. **Verify remediation engine is running**:
   ```bash
   tail -f ~/.navig/logs/remediation.log
   ```
   Should show "Remediation engine started" message

3. **Check for errors in debug log**:
   ```bash
   tail -f ~/.navig/logs/debug.log | grep -i remediation
   ```

### Component Keeps Failing

If remediation repeatedly fails (5+ attempts):

1. Check component-specific logs
2. Verify configuration is valid
3. Check system resources (memory, disk, CPU)
4. Review error patterns with `navig agent learn`
5. Manually investigate root cause

### Config Rollback Failed

If config rollback doesn't work:

1. **Check backup directory exists**:
   ```bash
   ls ~/.navig/workspace/config-backup/
   ```

2. **Manually restore config**:
   ```bash
   cp ~/.navig/workspace/config-backup/<component>-config-<timestamp>.yaml \
      ~/.navig/workspace/config.yaml
   ```

3. **Restart agent**:
   ```bash
   navig agent restart
   ```

## Limitations

- **Max 5 attempts per action** - Prevents infinite loops
- **1 hour action retention** - Old actions cleaned automatically
- **No cross-component dependencies** - Each component heals independently
- **Manual intervention required** for persistent failures

## See Also

- [Agent Learning](AGENT_LEARNING.md) - Error pattern detection
- [Agent Mode](AGENT_MODE.md) - Autonomous agent overview
- [Troubleshooting](troubleshooting.md) - General troubleshooting guide


