# Mattermost Addon for NAVIG

## Overview
Mattermost is an open-source, self-hosted Slack alternative for team messaging and collaboration. It offers secure messaging, file sharing, integrations, and enterprise features for DevOps and development teams.

## Features
- Real-time messaging with channels and direct messages
- File sharing and search
- Integrations with GitHub, GitLab, Jira, and more
- Playbooks for incident response and workflows
- Mobile and desktop apps
- Self-hosted with full data control
- Enterprise features (compliance, SSO, clustering)

## Usage

### Enable the Addon
```bash
navig server-template init mattermost --server <server-name>
navig server-template enable mattermost --server <server-name>
```

### Common Operations
```bash
# Check service status
navig run "systemctl status mattermost" --server <server-name>

# View logs
navig run "journalctl -u mattermost -f" --server <server-name>

# Check version
navig run "/opt/mattermost/bin/mattermost version" --server <server-name>

# Reset user password
navig run "/opt/mattermost/bin/mattermost user password username newpassword" --server <server-name>

# Restart service
navig run "systemctl restart mattermost" --server <server-name>
```

## Configuration

### Key Settings in `/opt/mattermost/config/config.json`:
- `ServiceSettings.SiteURL` - Your Mattermost URL
- `SqlSettings.DriverName` - Database driver (postgres/mysql)
- `SqlSettings.DataSource` - Database connection string
- `EmailSettings.*` - SMTP configuration
- `FileSettings.Directory` - File storage path

### Environment Variables:
All config.json settings can be overridden with `MM_` prefixed environment variables.

## Default Paths
| Path | Description |
|------|-------------|
| `/opt/mattermost` | Installation directory |
| `/opt/mattermost/config` | Configuration directory |
| `/opt/mattermost/data` | User files and attachments |
| `/opt/mattermost/logs` | Log files |
| `/opt/mattermost/plugins` | Server plugins |

## Default Port
- **8065** - HTTP/WebSocket

Use a reverse proxy (nginx/Traefik) to expose on 443 with HTTPS.

## References
- Official Website: https://mattermost.com/
- Documentation: https://docs.mattermost.com/
- API Reference: https://api.mattermost.com/
- GitHub: https://github.com/mattermost/mattermost-server


