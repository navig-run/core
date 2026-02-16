# Vaultwarden Addon for NAVIG

## Overview
Vaultwarden is a lightweight, self-hosted password manager server compatible with Bitwarden clients. It's written in Rust and provides a complete Bitwarden API implementation with minimal resource usage.

## Features
- Full Bitwarden API compatibility
- Supports official Bitwarden clients (web, mobile, desktop, browser extensions)
- Organizations and collections support
- File attachments and Send feature
- Two-factor authentication (TOTP, FIDO2, Duo, email)
- Admin panel for server management
- SQLite, PostgreSQL, or MySQL database support

## Usage

### Enable the Addon
```bash
navig server-template init vaultwarden --server <server-name>
navig server-template enable vaultwarden --server <server-name>
```

### Common Operations
```bash
# Check service status
navig run "systemctl status vaultwarden" --server <server-name>

# View logs
navig run "journalctl -u vaultwarden -f" --server <server-name>

# Backup database
navig run "cp /var/lib/vaultwarden/db.sqlite3 /var/backups/vaultwarden/db-$(date +%Y%m%d).sqlite3" --server <server-name>

# Restart service
navig run "systemctl restart vaultwarden" --server <server-name>
```

## Configuration

### Key Settings in `/etc/vaultwarden.env`:
- `DOMAIN` - Your Vaultwarden URL (required for proper client sync)
- `SIGNUPS_ALLOWED` - Set to `false` after creating accounts
- `ADMIN_TOKEN` - Secure token for admin panel access
- `SMTP_*` - Email configuration for notifications

### Security Recommendations:
1. Always use HTTPS with a reverse proxy
2. Set `SIGNUPS_ALLOWED=false` after initial setup
3. Generate a strong `ADMIN_TOKEN`
4. Enable 2FA for all accounts
5. Regular backups of the database

## Default Paths
| Path | Description |
|------|-------------|
| `/opt/vaultwarden` | Installation directory |
| `/var/lib/vaultwarden` | Data directory (database, attachments) |
| `/etc/vaultwarden.env` | Configuration file |
| `/var/log/vaultwarden` | Log directory |

## Default Port
- **8080** - Web interface and API
- **3012** - WebSocket notifications (optional)

Use a reverse proxy (nginx/Traefik) to expose on 443 with HTTPS.

## References
- Official Repository: https://github.com/dani-garcia/vaultwarden
- Wiki/Documentation: https://github.com/dani-garcia/vaultwarden/wiki
- Bitwarden Clients: https://bitwarden.com/download/


