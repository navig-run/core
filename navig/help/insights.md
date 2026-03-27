# Operations Insights & Analytics

The insights system provides analytics and intelligence on your command patterns,
helping you understand usage, detect anomalies, and optimize operations.

## Quick Start

```bash
# View insights summary (default view)
navig insights

# Show host health scores
navig insights hosts

# See your most-used commands
navig insights commands

# Detect potential issues
navig insights anomalies

# Get personalized recommendations
navig insights recommend

# Generate full report
navig insights report
```

## Commands

### `navig insights [show]`
Display a high-level summary including total operations, success rate,
active hosts, most-used commands, and recent anomalies.

### `navig insights hosts`
Calculate health scores (0-100) for each host based on:
- **Success rate** (60% weight) - How often commands succeed
- **Latency** (40% weight) - Average command execution time

Shows trend indicators (↑ improving, ↓ declining, → stable).

### `navig insights commands`
List the most frequently used commands with:
- Execution count
- Success rate
- Average duration
- Last used time

Options:
- `--limit, -n`: Number of commands (default: 10)

### `navig insights time`
Analyze time-based usage patterns:
- Activity by hour of day
- Success rates per time period
- Peak usage times

### `navig insights anomalies`
Detect unusual patterns and potential issues:
- **Error spikes**: Sudden increase in failure rates
- **Inactive hosts**: Hosts not used recently
- **Slow commands**: Commands taking longer than usual
- **Unusual patterns**: Deviations from normal behavior

Severity levels: critical, warning, info

### `navig insights recommend`
Get personalized recommendations based on your usage:
- Commands to alias for efficiency
- Hosts that need attention
- Automation opportunities
- Best practices suggestions

### `navig insights report`
Generate a comprehensive analytics report containing:
- Overall statistics
- Host health scores
- Top commands
- Time patterns
- Detected anomalies
- Personalized recommendations

Options:
- `--output, -o`: Save report to file
- `--json`: Output as JSON for further processing

## Time Ranges

All commands support a time range filter:

```bash
navig insights hosts --range today   # Last 24 hours
navig insights hosts --range week    # Last 7 days (default)
navig insights hosts --range month   # Last 30 days
navig insights hosts --range all     # All history
```

## Output Formats

```bash
# Rich terminal output (default)
navig insights hosts

# Plain text (for scripting)
navig insights hosts --plain

# JSON output (for automation)
navig insights hosts --json
```

## Health Score Calculation

Host health scores are computed as:

```
score = (success_rate * 0.6) + (latency_score * 0.4)
```

Where latency_score is based on:
- < 1 second: 100 points
- 1-5 seconds: 70-100 points
- 5-30 seconds: 40-70 points
- > 30 seconds: 0-40 points

Trends compare current period vs. previous period of same length.

## Anomaly Detection

The system uses statistical analysis to detect:

1. **Error Rate Spikes**
   - Compares current error rate to baseline
   - Triggers if current rate > baseline + 2σ

2. **Inactive Hosts**
   - Identifies hosts with no recent operations
   - Configurable threshold (default: 7 days)

3. **Slow Commands**
   - Tracks command execution times
   - Alerts when latency increases significantly

4. **Unusual Activity**
   - Monitors for command count deviations
   - Detects off-hours activity (if patterns established)

## Data Source

Insights are derived from the operations history:
- Location: `~/.navig/history/operations.jsonl`
- Populated automatically by all NAVIG operations
- Use `navig history` to view raw history data

## Example Workflows

### Daily Operations Check
```bash
# Quick overview
navig insights

# Check for problems
navig insights anomalies

# Review recommendations
navig insights recommend
```

### Weekly Review
```bash
# Generate full report
navig insights report --range week --output weekly-report.json

# Review host health trends
navig insights hosts --range week
```

### Troubleshooting
```bash
# Check specific time range for issues
navig insights anomalies --range today

# See command success rates
navig insights commands --range today

# Check if specific host is healthy
navig insights hosts | grep "hostname"
```

## Integration with Other Features

Insights works with:
- **History System**: Source data for all analytics
- **Triggers**: Create triggers based on insight thresholds
- **Quick Actions**: Recommendations can suggest new quick actions
- **Dashboard**: Key metrics appear on dashboard

```bash
# Example: Create trigger when error rate exceeds threshold
navig trigger add "High Error Rate" \
  --type threshold \
  --action "navig notify 'Error rate spike detected'" \
  --source "insights.error_rate" \
  --threshold 0.2
```

## Tips

1. **Build history first**: Insights improve with more data. Use NAVIG regularly
   for a few days before expecting accurate analytics.

2. **Check anomalies regularly**: Set up a quick action or trigger to review
   anomalies daily.

3. **Export reports**: Use `--json` output for integration with external tools
   like dashboards or alerting systems.

4. **Follow recommendations**: The system learns from your patterns and provides
   increasingly relevant suggestions over time.
