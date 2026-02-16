# NAVIG App Configuration Examples

This directory contains example app configuration files for NAVIG.

---

## Overview

Starting with NAVIG v2.1+, apps are stored in **individual files** (recommended format) rather than embedded in host YAML files. Each file represents one application instance and contains all app-specific configuration.

---

## File Structure

Each app configuration file follows this structure:

```yaml
# App identification (REQUIRED)
name: string                    # App name (must match filename)
host: string                    # Host reference (must exist in hosts/ directory)

# App metadata (OPTIONAL)
metadata:
  description: string
  type: string                  # laravel, nodejs, react, wordpress, etc.
  environment: string           # production, staging, development
  version: string
  repository: string
  domain: string

# App paths (OPTIONAL)
paths:
  web_root: string
  logs: string
  nginx_config: string          # For nginx apps
  php_config: string            # For PHP apps

# Database configuration (OPTIONAL)
database:
  type: string                  # mysql, postgresql, etc.
  host: string
  port: integer
  name: string
  user: string
  password: string
  remote_port: integer
  local_tunnel_port: integer

# Services (OPTIONAL)
services:
  web: string                   # nginx, apache2
  php: string                   # php8.2-fpm, php8.1-fpm
  database: string              # mysql, postgresql
  cache: string                 # redis-server, memcached
  queue: string                 # supervisor, systemd

# Webserver configuration (REQUIRED)
webserver:
  type: string                  # nginx or apache2 (REQUIRED)
  service_name: string
  config_test_command: string
  reload_command: string
  restart_command: string
```

---

## Available Examples

### Production Apps (myhost Host)

- **myapp.yaml** - Laravel production app with Apache2
- **myapp-staging.yaml** - Laravel staging environment
- **myapp-dev.yaml** - Laravel development environment
- **ai.yaml** - Node.js AI platform with Nginx and PostgreSQL

### Production Apps (Hetzner Host)

- **portfolio.yaml** - React portfolio website with Nginx
- **blog.yaml** - WordPress blog with Nginx and MySQL

### Local Development Apps

- **myapp-local.yaml** - Laravel local development
- **portfolio-local.yaml** - React local development

---

## Usage

### Copy to NAVIG Configuration Directory

```bash
# Create apps directory if it doesn't exist
mkdir -p ~/.navig/apps

# Copy example configuration
cp examples/apps/myapp.yaml ~/.navig/apps/myapp.yaml

# Edit with your actual values
nano ~/.navig/apps/myapp.yaml
```

### Customize for Your Environment

1. **Update app identification**:
   - `name`: Your app name (must match filename)
   - `host`: Reference to your host in `~/.navig/hosts/`

2. **Update app paths**:
   - `web_root`: Your web server document root
   - `logs`: Your log files directory
   - `storage`: Your storage/uploads directory

3. **Update database credentials** (if applicable):
   - `name`: Your database name
   - `user`: Your database username
   - `password`: Your database password

4. **Set webserver type** (REQUIRED):
   - `webserver.type`: Must be `nginx` or `apache2`

---

## Environment Naming Convention

Different environments are handled as **separate apps with naming suffixes**:

```yaml
# Production (no suffix or -prod)
name: myapp
host: production-server

# Staging
name: myapp-staging
host: production-server

# Development
name: myapp-dev
host: production-server

# Local development
name: myapp-local
host: local
```

---

## Migration from Legacy Format

If you have apps embedded in host YAML files, you can migrate them:

```bash
# Preview migration
navig app migrate --host <hostname> --dry-run

# Perform migration
navig app migrate --host <hostname>
```

This will extract apps from the host YAML and create individual files.

---

## Validation

After creating your app configuration, validate it:

```bash
# Show app configuration
navig app show <hostname>:<appname>

# List all apps
navig app list

# Validate configuration
navig config validate
```

---

## Complete Schema Reference

For complete field documentation, see `docs/CONFIG_SCHEMA.md`.

---

## Support

For questions or issues:
- See `docs/CONFIG_SCHEMA.md` for complete schema reference
- See `docs/MIGRATION_GUIDE.md` for migration instructions
- See `examples/hosts/README.md` for host configuration examples



