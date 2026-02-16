# n8n Template for NAVIG

## Overview

This template provides integration with n8n, a powerful workflow automation platform. It includes service management commands, webhook configurations, and common administrative tasks.

## Features

- **Service Management**: Start, stop, restart, and monitor n8n service
- **Workflow Operations**: Export and import workflows
- **Webhook Configuration**: Pre-configured webhook URL patterns
- **Log Access**: Quick access to n8n service logs
- **Environment Variables**: Standard n8n configuration

## Usage

### Enable the Template

```bash
navig template enable n8n
```

### Common Operations

#### Service Management

```bash
# Start n8n
navig run "systemctl start n8n"

# Check status
navig run "systemctl status n8n"

# View logs
navig run "journalctl -u n8n -f"
```

#### Workflow Management

```bash
# Export all workflows
navig run "n8n export:workflow --all --output=/tmp/n8n-workflows.json"

# Download exported workflows
navig download /tmp/n8n-workflows.json ./n8n-backup.json

# Upload and import workflows
navig upload ./n8n-workflows.json /tmp/n8n-workflows.json
navig run "n8n import:workflow --input=/tmp/n8n-workflows.json"
```

## Paths Provided

| Path | Location | Description |
|------|----------|-------------|
| `n8n_home` | `/root/.n8n` | n8n home directory |
| `n8n_data` | `/root/.n8n` | Data storage |
| `workflows_dir` | `/root/.n8n/workflows` | Workflow definitions |
| `credentials_dir` | `/root/.n8n/credentials` | Stored credentials |
| `log_dir` | `/var/log/n8n` | Log files |

## Services

- `automation`: n8n
- `n8n_service`: n8n.service (systemd)

## Environment Variables

The template provides these standard n8n environment variables:

```bash
N8N_HOST=0.0.0.0
N8N_PORT=5678
N8N_PROTOCOL=https
WEBHOOK_URL=https://your-domain.com
N8N_ENCRYPTION_KEY=change-this-encryption-key
```

**⚠️ Important**: Update `WEBHOOK_URL` and `N8N_ENCRYPTION_KEY` in your server configuration after enabling this template.

## API Access

n8n provides a REST API for programmatic access:

- **Endpoint**: `http://localhost:5678/api/v1/`
- **Webhook URL**: `https://your-domain.com/webhook/`
- **Authentication**: API key
- **Documentation**: https://docs.n8n.io/api/

## Installation

If n8n is not installed on your server, use these commands:

```bash
# Install n8n globally
navig run "npm install -g n8n"

# Create systemd service
navig run "cat > /etc/systemd/system/n8n.service << 'EOF'
[Unit]
Description=n8n - Workflow Automation
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/n8n start
Restart=always
Environment=N8N_HOST=0.0.0.0
Environment=N8N_PORT=5678
Environment=N8N_PROTOCOL=https
Environment=WEBHOOK_URL=https://your-domain.com

[Install]
WantedBy=multi-user.target
EOF"

# Enable and start service
navig run "systemctl daemon-reload"
navig run "systemctl enable n8n"
navig run "systemctl start n8n"
```

## Webhook Configuration

n8n webhooks are accessible at:

```
https://your-domain.com/webhook/<webhook-path>
https://your-domain.com/webhook-test/<webhook-path>
```

Ensure your reverse proxy (Nginx/Apache) is configured to forward requests to n8n:

```nginx
location /webhook {
    proxy_pass http://localhost:5678;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection 'upgrade';
    proxy_set_header Host $host;
    proxy_cache_bypass $http_upgrade;
}
```

## Backup and Restore

### Backup Workflows

```bash
# Export workflows
navig run "n8n export:workflow --all --output=/tmp/n8n-backup.json"

# Download backup
navig download /tmp/n8n-backup.json ./backups/n8n-$(date +%Y%m%d).json
```

### Restore Workflows

```bash
# Upload backup
navig upload ./backups/n8n-20250120.json /tmp/n8n-restore.json

# Import workflows
navig run "n8n import:workflow --input=/tmp/n8n-restore.json"
```

## Troubleshooting

### Service Won't Start

Check logs for errors:

```bash
navig run "journalctl -u n8n -n 50 --no-pager"
```

Common issues:
- Port 5678 already in use
- Missing Node.js installation
- Incorrect file permissions on `/root/.n8n`

### Webhooks Not Working

1. Check n8n is running: `navig run "systemctl status n8n"`
2. Verify WEBHOOK_URL environment variable is set correctly
3. Ensure reverse proxy is configured
4. Check firewall allows traffic to port 5678

## Learn More

- Official Site: https://n8n.io
- Documentation: https://docs.n8n.io
- Community: https://community.n8n.io
- GitHub: https://github.com/n8n-io/n8n


