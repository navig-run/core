# navig dashboard

Real-time TUI dashboard for infrastructure monitoring and operations overview.

## Features

- **Host Health Panel**: Live SSH connectivity status with latency
- **Docker Panel**: Container status for active host
- **History Panel**: Recent operations from command history
- **Resources Panel**: CPU, memory, disk overview

## Usage

```bash
# Full live dashboard (auto-refresh)
navig dashboard

# Single snapshot (no live updates)
navig dashboard --no-live

# Custom refresh interval (default: 5 seconds)
navig dashboard --refresh 10
navig dashboard -r 3
```

## Keyboard Controls

| Key | Action |
|-----|--------|
| `Q` | Quit dashboard |
| `R` | Force refresh |
| `Ctrl+C` | Exit |

## Panels

### Host Health
Shows all configured hosts with:
- Connectivity status (green/red indicator)
- IP address
- Response latency
- Active host highlighted

### Docker Containers
For the active host, shows:
- Running containers
- Container status
- Port mappings
- Image names

### Recent Operations
From the history system:
- Last 8 operations
- Timestamp
- Command (truncated)
- Target host
- Success/failure status

### System Resources
For the active host:
- CPU usage
- Memory usage
- Disk usage
- System load

## Requirements

- Interactive terminal (not piped output)
- Rich library (included with NAVIG)
- Host connectivity for live updates

## Tips

- Use `--no-live` if your terminal doesn't support full-screen mode
- Increase `--refresh` interval on slow connections
- Use `navig status` for quick non-interactive status

## See Also

- `navig status` — Quick status summary
- `navig history` — Full operation history
- `navig host list` — List all hosts


