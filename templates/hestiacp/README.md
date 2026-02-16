# HestiaCP Template for NAVIG

## Overview

This template provides integration with HestiaCP, a free and open-source web hosting control panel. It includes default paths, service definitions, and common administrative commands.

## Features

- **Pre-configured Paths**: Standard HestiaCP directory structure
- **Service Definitions**: All HestiaCP services (web, mail, DNS, FTP)
- **Common Commands**: Quick access to v-list-*, v-backup-*, v-restart-* commands
- **API Integration**: Endpoints and authentication method references

## Usage

### Enable the Template

```bash
navig template enable hestiacp
```

### Server Configuration

When enabled, this template automatically adds HestiaCP-specific paths and services to your server configuration. No manual configuration needed for standard HestiaCP installations.

### Common Commands

The template provides these frequently-used HestiaCP commands:

- **List Users**: `v-list-users`
- **List Domains**: `v-list-web-domains USER`
- **List Databases**: `v-list-databases USER`
- **Backup User**: `v-backup-user USER`
- **Restart Services**: `v-restart-web && v-restart-proxy`

Execute them via NAVIG:

```bash
navig run "v-list-users"
navig run "v-list-web-domains admin"
```

## Paths Provided

| Path | Location | Description |
|------|----------|-------------|
| `hestia_root` | `/usr/local/hestia` | HestiaCP installation root |
| `hestia_bin` | `/usr/local/hestia/bin` | HestiaCP binaries and scripts |
| `hestia_conf` | `/usr/local/hestia/conf` | Configuration files |
| `hestia_data` | `/usr/local/hestia/data` | User and system data |
| `web_root` | `/home/admin/web` | Web domains root |
| `backup_dir` | `/backup` | Backup storage |
| `log_dir` | `/var/log/hestia` | Log files |

## Services

- `control_panel`: hestia
- `web`: nginx
- `php`: php-fpm
- `database`: mysql
- `mail`: exim4
- `dns`: bind9
- `ftp`: vsftpd

## Requirements

- HestiaCP installed on target server
- Root or admin SSH access
- HestiaCP version 1.6.0 or higher recommended

## API Integration

HestiaCP provides a REST API for programmatic access:

- **Endpoint**: `https://server-hostname:8083/api/`
- **Authentication**: API key
- **Documentation**: https://hestiacp.com/docs/server-administration/rest-api.html

## Troubleshooting

### Command Not Found

If HestiaCP commands (v-*) are not found, ensure:
1. HestiaCP is properly installed
2. You're running commands as root or admin user
3. PATH includes `/usr/local/hestia/bin`

### Permission Denied

HestiaCP commands require elevated privileges. Ensure your SSH user has sudo or root access.

## Learn More

- Official Site: https://hestiacp.com
- Documentation: https://hestiacp.com/docs/
- GitHub: https://github.com/hestiacp/hestiacp


