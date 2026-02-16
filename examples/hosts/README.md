# NAVIG Host Configuration Examples

This directory contains example host configuration files for NAVIG.

---

## Overview

Host configuration files contain **infrastructure and connection details** for remote servers. Starting with NAVIG v2.1+, app-specific configurations are stored separately in `examples/apps/` directory.

---

## Files

### `local.yaml`
**Description**: Local development environment example

**Use Case**: Developer's local machine running apps for development

**Apps** (see `examples/apps/`):
- `myapp-local` - Laravel application (nginx)
- `portfolio-local` - React application (nginx)

**Key Features**:
- Uses `localhost` as host
- Development-friendly configuration
- References local app configurations

---

### `myhost.yaml`
**Description**: Production VPS with multiple apps and environments

**Use Case**: Production server hosting multiple apps with staging/dev variants

**Apps** (see `examples/apps/`):
- `myapp` - Production environment (apache2)
- `myapp-staging` - Staging environment (apache2)
- `myapp-dev` - Development environment (apache2)
- `ai` - AI platform production (nginx)

**Key Features**:
- Demonstrates environment naming convention (separate apps with suffixes)
- Multiple webserver types (apache2 for Laravel, nginx for Node.js)
- Complete SSL configuration
- Production-grade infrastructure

---

### `example-vps.yaml`
**Description**: Alternative VPS hosting different apps

**Use Case**: Secondary server for portfolio and blog

**Apps** (see `examples/apps/`):
- `portfolio` - React application (nginx)
- `blog` - WordPress blog (nginx)

**Key Features**:
- Different hosting provider
- WordPress-specific configuration
- Nginx for all apps

---

## Usage

### Copy to NAVIG Configuration Directory

```bash
# Create hosts directory if it doesn't exist
mkdir -p ~/.navig/hosts

# Copy example configuration
cp examples/hosts/local.yaml ~/.navig/hosts/local.yaml

# Edit with your actual values
nano ~/.navig/hosts/local.yaml
```

### Customize for Your Environment

1. **Update host connection details**:
   - `host`: Your server's hostname or IP
   - `user`: Your SSH username
   - `ssh_key`: Path to your SSH private key

2. **Configure database root credentials** (optional):
   - `database.root_user`: Database root username (for host-level DB management)
   - `database.root_password`: Database root password

3. **Configure default app** (optional):
   - `default_app`: Default app to use when not specified

4. **Update metadata** (optional):
   - `description`: Server description
   - `location`: Physical location
   - `provider`: Hosting provider
   - `os`: Operating system

---

## App Configuration

**Important**: App-specific configurations are now stored in separate files in `examples/apps/` directory.

See `examples/apps/README.md` for:
- App configuration structure
- Available app examples
- How to create and customize app configurations

---

## Environment Naming Convention

Different environments are handled as **separate apps with naming suffixes**:

- `myapp` - Production (default, no suffix)
- `myapp-staging` - Staging environment
- `myapp-dev` - Development environment
- `myapp-local` - Local development

**Key Points**:
- Each environment is a **separate, independent app file**
- No config merging or inheritance
- Use suffixes: `-staging`, `-dev`, `-local`, `-prod` (optional)

**Usage**:
```bash
# Production
navig --host myhost --app myapp webserver-reload

# Staging
navig --host myhost --app myapp-staging webserver-reload

# Development
navig --host myhost --app myapp-dev webserver-reload
```

---

## Validation

After creating your configuration, validate it:

```bash
# Show host configuration
navig config show myhost

# Show app configuration
navig config show myhost:myapp

# Validate configuration
navig config validate
```

---

## Migration from Legacy Format

If you have apps embedded in host YAML files (legacy format), migrate them to individual files:

```bash
# Preview migration
navig app migrate --host <hostname> --dry-run

# Perform migration
navig app migrate --host <hostname>
```

This will extract apps from the host YAML and create individual files in `~/.navig/apps/`.

See `docs/MIGRATION_GUIDE.md` for detailed migration instructions.

---

## Complete Schema Reference

For complete field documentation, see `docs/CONFIG_SCHEMA.md`.

---

## Security Notes

1. **SSH Keys**: Use SSH keys instead of passwords for better security
2. **Database Passwords**: Consider encrypting sensitive values
3. **File Permissions**: Ensure configuration files have appropriate permissions:
   ```bash
   chmod 600 ~/.navig/hosts/*.yaml
   ```

---

## Support

For questions or issues:
- See `docs/CONFIG_SCHEMA.md` for complete schema reference
- See `docs/MIGRATION_GUIDE.md` for migration instructions
- See `examples/apps/README.md` for app configuration examples


