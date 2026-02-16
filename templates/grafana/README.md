# Grafana Template for NAVIG

## Overview

Grafana is a multi-platform open-source analytics and interactive visualization web application. It provides charts, graphs, and alerts for the web when connected to supported data sources.

## Features

- **Data Visualization**: Rich set of visualization options
- **Data Sources**: Connect to Prometheus, InfluxDB, MySQL, PostgreSQL, and more
- **Alerting**: Configure alert rules and notifications
- **Dashboards**: Create, share, and manage dashboards
- **Plugins**: Extend functionality with community plugins
- **API**: Full HTTP API for automation

## Usage

### Enable the Template

```bash
navig server-template init grafana --server <server-name>
navig server-template enable grafana --server <server-name>
```

### Common Operations

#### Service Management

```bash
# Start Grafana
navig run "systemctl start grafana-server"

# Check status
navig run "systemctl status grafana-server"

# View logs
navig run "tail -f /var/log/grafana/grafana.log"
```

#### Plugin Management

```bash
# List installed plugins
navig run "grafana-cli plugins ls"

# Install a plugin
navig run "grafana-cli plugins install grafana-piechart-panel"

# Update all plugins
navig run "grafana-cli plugins update-all"

# Remove a plugin
navig run "grafana-cli plugins remove plugin-name"

# Restart after plugin changes
navig run "systemctl restart grafana-server"
```

#### API Operations

```bash
# Get all dashboards
navig run "curl -s -H 'Authorization: Bearer YOUR_API_KEY' http://localhost:3000/api/search | jq"

# Get datasources
navig run "curl -s -H 'Authorization: Bearer YOUR_API_KEY' http://localhost:3000/api/datasources | jq"

# Health check
navig run "curl -s http://localhost:3000/api/health | jq"
```

## Configuration

### Main Configuration

Edit `/etc/grafana/grafana.ini`:

```ini
[server]
http_port = 3000
domain = grafana.example.com
root_url = https://grafana.example.com

[database]
type = sqlite3
path = /var/lib/grafana/grafana.db

[security]
admin_user = admin
admin_password = securepassword
secret_key = your-secret-key

[users]
allow_sign_up = false
allow_org_create = false

[auth.anonymous]
enabled = false

[smtp]
enabled = true
host = smtp.example.com:587
user = alerts@example.com
password = smtp_password
from_address = alerts@example.com
```

### Data Source Provisioning

Create `/etc/grafana/provisioning/datasources/prometheus.yml`:

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://localhost:9090
    isDefault: true
    editable: false
```

### Dashboard Provisioning

Create `/etc/grafana/provisioning/dashboards/default.yml`:

```yaml
apiVersion: 1

providers:
  - name: 'Default'
    folder: ''
    type: file
    disableDeletion: false
    editable: true
    options:
      path: /var/lib/grafana/dashboards
```

## Default Paths

| Path | Location | Description |
|------|----------|-------------|
| config_file | `/etc/grafana/grafana.ini` | Main configuration |
| data_dir | `/var/lib/grafana` | Data and SQLite database |
| log_dir | `/var/log/grafana` | Log files |
| plugins_dir | `/var/lib/grafana/plugins` | Installed plugins |
| provisioning_dir | `/etc/grafana/provisioning` | Auto-provisioned configs |

## Default Port

- **Grafana Web UI**: 3000

## Backup & Restore

### Backup Grafana

```bash
# Backup database
navig run "cp /var/lib/grafana/grafana.db /backup/grafana-$(date +%Y%m%d).db"

# Backup configuration
navig run "tar -czvf /backup/grafana-config-$(date +%Y%m%d).tar.gz /etc/grafana"

# Backup dashboards
navig run "tar -czvf /backup/grafana-dashboards-$(date +%Y%m%d).tar.gz /var/lib/grafana/dashboards"

# Download backups
navig download /backup/grafana-*.* ./backups/
```

### Export Dashboards via API

```bash
# Export all dashboards
navig run 'for uid in $(curl -s -H "Authorization: Bearer $API_KEY" http://localhost:3000/api/search | jq -r ".[].uid"); do
  curl -s -H "Authorization: Bearer $API_KEY" "http://localhost:3000/api/dashboards/uid/$uid" > "/backup/dashboard-$uid.json"
done'
```

### Restore Grafana

```bash
# Restore database
navig upload ./backups/grafana-backup.db /var/lib/grafana/grafana.db
navig run "chown grafana:grafana /var/lib/grafana/grafana.db"
navig run "systemctl restart grafana-server"
```

## Troubleshooting

### Grafana Won't Start

```bash
# Check logs
navig run "journalctl -u grafana-server -n 50"

# Verify configuration
navig run "grafana-server -config /etc/grafana/grafana.ini -homepath /usr/share/grafana cfg:default.log.mode=console"

# Check permissions
navig run "ls -la /var/lib/grafana"
```

### Database Issues

```bash
# Check database integrity
navig run "sqlite3 /var/lib/grafana/grafana.db 'PRAGMA integrity_check;'"

# Backup and recreate
navig run "mv /var/lib/grafana/grafana.db /var/lib/grafana/grafana.db.bak"
navig run "systemctl restart grafana-server"
```

### Password Reset

```bash
# Reset admin password
navig run "grafana-cli admin reset-admin-password newpassword"
```

### Data Source Connection

```bash
# Test Prometheus connection
navig run "curl -s http://localhost:9090/api/v1/status/config | jq"

# Test MySQL connection
navig run "mysql -h localhost -u grafana -p -e 'SELECT 1'"
```

## Security Best Practices

1. **Change Default Password**: Reset admin password immediately after install
2. **HTTPS Only**: Use reverse proxy (Nginx/Traefik) with TLS
3. **Disable Sign-up**: Set `allow_sign_up = false`
4. **API Keys**: Use API keys with minimal permissions
5. **OAuth/LDAP**: Configure enterprise authentication
6. **Network**: Bind to localhost and use reverse proxy

### Nginx Reverse Proxy Example

```nginx
server {
    listen 443 ssl http2;
    server_name grafana.example.com;

    ssl_certificate /etc/letsencrypt/live/grafana.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/grafana.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket support for live features
    location /api/live/ {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## References

- Official Website: https://grafana.com
- Documentation: https://grafana.com/docs/grafana/latest/
- Plugin Library: https://grafana.com/grafana/plugins/
- Dashboard Library: https://grafana.com/grafana/dashboards/
- GitHub: https://github.com/grafana/grafana


