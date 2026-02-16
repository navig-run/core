# Agent Learning System

NAVIG's autonomous agent learns from its operational history by analyzing logs to detect error patterns, predict issues, and provide actionable recommendations.

## Overview

The learning system analyzes agent logs to:

- **Detect recurring error patterns**
- **Identify failure trends**
- **Suggest preventive actions**
- **Export insights for further analysis**

## How It Works

### Log Analysis

The learning system scans:

1. **Debug Log** (`~/.navig/logs/debug.log`)
   - Component errors
   - Operation failures
   - System warnings

2. **Remediation Log** (`~/.navig/logs/remediation.log`)
   - Automatic recovery attempts
   - Remediation failures
   - Component restart patterns

### Pattern Detection

Built-in patterns recognized:

| Pattern | Detects | Threshold |
|---------|---------|-----------|
| `connection_failed` | Network/SSH connection issues | > 10 occurrences = warning |
| `permission_denied` | File/resource access problems | > 5 occurrences = warning |
| `config_error` | Configuration parsing failures | > 3 occurrences = warning |
| `component_error` | Component lifecycle failures | > 5 occurrences = warning |
| `resource_exhausted` | Memory/disk/quota issues | > 0 occurrences = critical |

## CLI Usage

### Basic Analysis

```bash
navig agent learn
```

Analyzes last 7 days of logs and displays:
- Error count by pattern
- Example log entries
- Actionable recommendations

### Custom Time Range

```bash
navig agent learn --days 30
```

Analyze last 30 days instead of default 7.

### Export Patterns

```bash
navig agent learn --export
```

Exports findings to `~/.navig/workspace/error-patterns.json`:

```json
{
  "analyzed_date": "2026-02-06T14:30:00",
  "days_analyzed": 7,
  "patterns": {
    "connection_failed": {
      "count": 15,
      "examples": [
        "[2026-02-01 10:23:45] Connection to host 10.0.0.10 failed: timeout",
        "[2026-02-02 08:15:30] SSH connection refused on port 22",
        "[2026-02-03 14:20:10] Connection timeout after 30 seconds"
      ]
    },
    "config_error": {
      "count": 3,
      "examples": [
        "[2026-02-04 09:00:00] Config parse error: invalid YAML syntax"
      ]
    }
  }
}
```

## Example Output

```
$ navig agent learn --days 7

ℹ Analyzing logs from last 7 days...

⚠️ Found 23 errors across 3 patterns

  ● Connection Failed: 15 occurrences
    Examples:
      [2026-02-01 10:23:45] Connection to host 10.0.0.10 failed: timeout
      [2026-02-02 08:15:30] SSH connection refused on port 22

  ● Permission Denied: 6 occurrences
    Examples:
      [2026-02-03 11:45:20] Permission denied: ~/.navig/workspace/config.yaml
      [2026-02-04 15:30:10] Access denied to /etc/systemd/system/

  ● Config Error: 2 occurrences
    Examples:
      [2026-02-05 09:00:00] Config parse error: invalid YAML syntax

ℹ Recommendations:
  • Review network connectivity and firewall rules
  • Check file permissions and user access rights
```

## Recommendations Engine

Based on detected patterns, the system provides targeted advice:

### Connection Issues (> 10 occurrences)
```
• Review network connectivity and firewall rules
• Verify SSH keys and authentication
• Check if remote hosts are accessible
```

### Permission Problems (> 5 occurrences)
```
• Check file permissions and user access rights
• Verify NAVIG runs with appropriate user
• Review sudo/admin requirements
```

### Config Errors (> 3 occurrences)
```
• Validate configuration files for syntax errors
• Check YAML indentation and structure
• Review recent config changes
```

### Component Failures (> 5 occurrences)
```
• Components may need restarting or reconfiguration
• Check component-specific logs for details
• Verify dependencies are installed
```

### Resource Exhaustion (any occurrences)
```
• [CRITICAL] Check system resources (memory, disk)
• Review resource-intensive operations
• Consider scaling or optimization
```

## Integration with Self-Healing

Learning complements the self-healing system:

1. **Self-Healing** - Immediate automatic recovery
2. **Learning** - Long-term pattern analysis

Use both together:

```bash
# Check what failures occurred
navig agent learn --days 7

# See how self-healing responded
navig agent remediation list
```

## Advanced Usage

### Scheduled Learning

Add to cron for periodic analysis:

```bash
# Analyze logs daily and export patterns
0 2 * * * navig agent learn --export
```

### Custom Analysis

The exported JSON can be processed with custom scripts:

```python
import json
from pathlib import Path

patterns = json.load(open(Path.home() / '.navig/workspace/error-patterns.json'))

# Find most common error
top_error = max(patterns['patterns'].items(), key=lambda x: x[1]['count'])
print(f"Most common: {top_error[0]} ({top_error[1]['count']} times)")

# Check for critical patterns
critical = [p for p, data in patterns['patterns'].items() 
            if data['count'] > 20]
if critical:
    print(f"Critical patterns detected: {critical}")
```

### AI-Powered Root Cause Analysis

Combine with NAVIG AI for deeper insights:

```bash
# Export patterns
navig agent learn --export

# Analyze with AI
navig ai prompt "Analyze the error patterns in ~/.navig/workspace/error-patterns.json and suggest root causes and fixes"
```

## Log Format Requirements

For accurate detection, logs should follow this format:

```
[YYYY-MM-DD HH:MM:SS] [LEVEL] message with keywords
```

Example:
```
[2026-02-06 10:30:00] [ERROR] Connection failed: timeout
[2026-02-06 10:30:15] [WARNING] Permission denied: /etc/hosts
[2026-02-06 10:30:30] [ERROR] Component eyes failed to start
```

## Pattern Customization

To add custom patterns, extend the detection regex:

```python
# In navig/commands/agent.py, agent_learn() function
patterns = {
    'connection_failed': r'connection.*(failed|refused|timeout)',
    'permission_denied': r'permission denied|access denied',
    'config_error': r'config.*error|invalid.*config',
    'component_error': r'component.*error|failed to start',
    'resource_exhausted': r'out of memory|disk full|quota exceeded',
    
    # Add your custom patterns:
    'database_error': r'database.*error|sql.*failed',
    'api_timeout': r'api.*timeout|request.*timeout',
}
```

## Troubleshooting

### No Patterns Detected

If learning finds no errors:

1. **Check logs exist**:
   ```bash
   ls -lh ~/.navig/logs/
   ```

2. **Verify agent has been running**:
   ```bash
   navig agent status
   ```

3. **Check log contents**:
   ```bash
   tail -50 ~/.navig/logs/debug.log
   ```

### Analysis Too Slow

For large log files (>100MB):

1. **Reduce time range**:
   ```bash
   navig agent learn --days 1
   ```

2. **Rotate logs**:
   ```bash
   mv ~/.navig/logs/debug.log ~/.navig/logs/debug.log.old
   ```

3. **Use log rotation** (automatic with agent mode)

### False Positives

If learning detects too many non-issues:

1. **Adjust detection thresholds** in recommendations logic
2. **Filter specific patterns** in custom analysis script
3. **Review log verbosity settings**

## Best Practices

### 1. Regular Analysis

Run weekly to catch trends early:

```bash
# Every Monday at 9 AM
0 9 * * 1 navig agent learn --export
```

### 2. Correlate with Metrics

Compare error patterns with system metrics:

```bash
navig agent learn --days 7
navig monitoring metrics --days 7
```

### 3. Track Improvements

After fixing issues, verify they're resolved:

```bash
# Before fixes
navig agent learn --export > before.json

# [Apply fixes]

# After fixes
navig agent learn --export > after.json
diff before.json after.json
```

### 4. Share Insights

Export and share patterns with your team:

```bash
navig agent learn --export
# Email ~/.navig/workspace/error-patterns.json to team
```

## Future Enhancements

Planned improvements:

- **ML-based anomaly detection** - Detect unusual patterns automatically
- **Trend prediction** - Predict failures before they occur
- **Smart recommendations** - AI-powered root cause analysis
- **Dashboard visualization** - Web UI for pattern analysis
- **Alert integration** - Notify on critical patterns

## See Also

- [Self-Healing](AGENT_SELF_HEALING.md) - Automatic recovery system
- [Agent Mode](AGENT_MODE.md) - Autonomous agent overview
- [Monitoring](../commands/monitoring.md) - System metrics and alerts
- [Troubleshooting](troubleshooting.md) - General troubleshooting


