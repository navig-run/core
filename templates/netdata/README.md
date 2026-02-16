# Netdata Template for NAVIG

## Overview

Netdata is a distributed, real-time performance and health monitoring tool for systems, hardware, containers, and applications. It collects thousands of metrics per second with zero configuration.

## Features

- **Real-Time Monitoring**: Per-second data collection and visualization
- **Auto-Detection**: Automatically discovers system and app metrics
- **Alerting**: Built-in health monitoring with customizable alerts
- **Low Overhead**: Minimal CPU and memory footprint
- **Streaming**: Parent-child architecture for centralized monitoring
- **Cloud Integration**: Connect to Netdata Cloud for team dashboards

## Usage

### Enable the Template

```bash
navig server-template init netdata --server <server-name>
navig server-template enable netdata --server <server-name>
```

### Common Operations

#### Service Management

```bash
# Start Netdata
navig run "systemctl start netdata"

# Check status
navig run "systemctl status netdata"

# View logs
navig run "tail -f /var/log/netdata/error.log"

# Reload configuration
navig run "killall -USR2 netdata"
```

#### API Queries

```bash
# Get system info
navig run "curl -s http://localhost:19999/api/v1/info | jq"

# List all charts
navig run "curl -s http://localhost:19999/api/v1/charts | jq '.charts | keys'"

# Get CPU data
navig run 'curl -s "http://localhost:19999/api/v1/data?chart=system.cpu&after=-60" | jq'

# Get active alarms
navig run "curl -s http://localhost:19999/api/v1/alarms | jq"
```

## Installation

### Quick Install (Official Script)

```bash
# Install with default options
navig run "wget -O /tmp/netdata-kickstart.sh https://get.netdata.cloud/kickstart.sh && sh /tmp/netdata-kickstart.sh"

# Install without Netdata Cloud
navig run "wget -O /tmp/netdata-kickstart.sh https://get.netdata.cloud/kickstart.sh && sh /tmp/netdata-kickstart.sh --dont-start-it --disable-cloud"
```

### Docker Installation

```bash
navig run "mkdir -p /opt/netdata"
```

Create `/opt/netdata/docker-compose.yml`:

```yaml
version: "3.8"

services:
  netdata:
    image: netdata/netdata:stable
    container_name: netdata
    restart: unless-stopped
    hostname: "$(hostname)"
    pid: host
    network_mode: host
    cap_add:
      - SYS_PTRACE
      - SYS_ADMIN
    security_opt:
      - apparmor:unconfined
    volumes:
      - netdataconfig:/etc/netdata
      - netdatalib:/var/lib/netdata
      - netdatacache:/var/cache/netdata
      - /etc/passwd:/host/etc/passwd:ro
      - /etc/group:/host/etc/group:ro
      - /etc/localtime:/etc/localtime:ro
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - /etc/os-release:/host/etc/os-release:ro
      - /var/log:/host/var/log:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro

volumes:
  netdataconfig:
  netdatalib:
  netdatacache:
```

Start:
```bash
navig run "cd /opt/netdata && docker compose up -d"
```

## Configuration

### Main Configuration

Edit `/etc/netdata/netdata.conf`:

```ini
[global]
    hostname = myserver
    history = 3996
    update every = 1
    memory mode = dbengine
    page cache size = 32
    dbengine multihost disk space = 256

[web]
    bind to = localhost
    default port = 19999
    allow connections from = localhost *
    allow dashboard from = localhost *

[plugins]
    proc = yes
    diskspace = yes
    cgroups = yes
    tc = yes
    enable running new plugins = yes
```

### Health/Alert Configuration

Create custom alerts in `/etc/netdata/health.d/custom.conf`:

```yaml
# High CPU usage alert
 alarm: cpu_usage_high
    on: system.cpu
lookup: average -10s unaligned of user,system,softirq,irq,guest
 units: %
 every: 10s
  warn: $this > 75
  crit: $this > 90
 delay: up 1m down 5m multiplier 1.5 max 1h
  info: CPU utilization is high

# Disk space alert
 alarm: disk_space_low
    on: disk.space
lookup: average -30s unaligned of used
 units: %
 every: 30s
  warn: $this > 80
  crit: $this > 95
  info: Disk space is running low
```

### Streaming Configuration (Parent-Child)

On **child** node (`/etc/netdata/stream.conf`):

```ini
[stream]
    enabled = yes
    destination = parent.example.com:19999
    api key = YOUR-API-KEY
```

On **parent** node (`/etc/netdata/stream.conf`):

```ini
[YOUR-API-KEY]
    enabled = yes
    allow from = *
```

### Claim to Netdata Cloud

```bash
navig run "netdata-claim.sh -token=YOUR_CLAIM_TOKEN -rooms=YOUR_ROOM_ID -url=https://app.netdata.cloud"
```

## Default Paths

| Path | Location | Description |
|------|----------|-------------|
| config_dir | `/etc/netdata` | Configuration files |
| data_dir | `/var/cache/netdata` | Database and cache |
| log_dir | `/var/log/netdata` | Log files |
| plugins_dir | `/usr/libexec/netdata/plugins.d` | Collector plugins |

## Default Port

- **Web UI/API**: 19999

## Key Metrics

### System Metrics

| Chart | Description |
|-------|-------------|
| system.cpu | CPU utilization |
| system.load | System load average |
| system.ram | RAM usage |
| system.swap | Swap usage |
| system.io | Disk I/O |
| system.net | Network traffic |
| system.processes | Process statistics |

### API Examples

```bash
# CPU data for last hour
navig run 'curl -s "http://localhost:19999/api/v1/data?chart=system.cpu&after=-3600" | jq'

# Memory data
navig run 'curl -s "http://localhost:19999/api/v1/data?chart=system.ram" | jq'

# Disk I/O
navig run 'curl -s "http://localhost:19999/api/v1/data?chart=system.io" | jq'

# Network traffic
navig run 'curl -s "http://localhost:19999/api/v1/data?chart=system.net" | jq'

# Get specific chart info
navig run 'curl -s "http://localhost:19999/api/v1/chart?chart=system.cpu" | jq'
```

## Backup & Restore

### Backup Configuration

```bash
# Backup config files
navig run "tar -czvf /backup/netdata-config-$(date +%Y%m%d).tar.gz /etc/netdata"

# Download
navig download /backup/netdata-config-*.tar.gz ./backups/
```

### Restore Configuration

```bash
# Upload backup
navig upload ./backups/netdata-config-backup.tar.gz /backup/

# Restore
navig run "tar -xzvf /backup/netdata-config-backup.tar.gz -C /"

# Restart
navig run "systemctl restart netdata"
```

## Troubleshooting

### Netdata Not Starting

```bash
# Check status
navig run "systemctl status netdata"

# Check logs
navig run "cat /var/log/netdata/error.log | tail -50"

# Debug mode
navig run "netdata -D"
```

### Missing Metrics

```bash
# Check which collectors are running
navig run "curl -s http://localhost:19999/api/v1/info | jq '.collectors'"

# Check plugin status
navig run "ls -la /usr/libexec/netdata/plugins.d/"

# Check specific plugin logs
navig run "grep -i plugin /var/log/netdata/error.log"
```

### High CPU Usage

```bash
# Check which plugins are using CPU
navig run "curl -s http://localhost:19999/api/v1/data?chart=netdata.plugin_proc_cpu | jq"

# Disable unused plugins in netdata.conf
# [plugins]
#     python.d = no
```

### Web Interface Not Accessible

```bash
# Check if listening
navig run "ss -tlnp | grep 19999"

# Check bind configuration
navig run "grep -A5 '\[web\]' /etc/netdata/netdata.conf"

# Check firewall
navig run "ufw status | grep 19999"
```

## Security Best Practices

1. **Bind to localhost**: Only expose through reverse proxy
2. **Authentication**: Use reverse proxy with authentication
3. **TLS**: Enable HTTPS via reverse proxy
4. **Streaming**: Use API keys for parent-child streaming
5. **Firewall**: Block port 19999 from external access
6. **Updates**: Keep Netdata updated

### Nginx Reverse Proxy

```nginx
server {
    listen 443 ssl http2;
    server_name netdata.example.com;

    ssl_certificate /etc/letsencrypt/live/netdata.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/netdata.example.com/privkey.pem;

    auth_basic "Netdata";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:19999;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location ~ ^/api/ {
        proxy_pass http://127.0.0.1:19999;
        proxy_set_header Host $host;
    }
}
```

## References

- Official Website: https://netdata.cloud
- Documentation: https://learn.netdata.cloud
- GitHub: https://github.com/netdata/netdata
- API Reference: https://learn.netdata.cloud/docs/agent/web/api
- Chart Reference: https://learn.netdata.cloud/docs/dashboard/charts-tab


