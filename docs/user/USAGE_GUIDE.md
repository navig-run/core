# NAVIG - Usage Guide

**No Admin Visible In Graveyard**
Keep your servers alive. Forever.

## Table of Contents

1. [Quick Start](#quick-start)
2. [Multi-Server Management](#multi-server-management)
3. [Global Flags](#global-flags)
4. [File Operations](#file-operations)
5. [Database Management](#database-management)
6. [HestiaCP Integration](#hestiacp-integration)
7. [Backup System](#backup-system)
8. [Resource Monitoring](#resource-monitoring)
9. [Security Management](#security-management)
10. [System Maintenance](#system-maintenance)
11. [Web Server Management](#web-server-management)
12. [Troubleshooting](#troubleshooting)

---

## Quick Start

```bash
# Install NAVIG
pip install -r requirements.txt

# Configure your servers
python navig.py server add production    # Production server
python navig.py server add staging       # Staging/testing server
python navig.py server add dev           # Development server
python navig.py server add local         # Local development (localhost)

# Set active server
python navig.py server use production

# Test connection
python navig.py sql "SELECT 1"

# View server health
python navig.py health

# Switch between servers
python navig.py server use dev
python navig.py health
```

### Multi-Server Configuration Examples

**Production Server:**
```bash
python navig.py server add production
# Host: 136.244.93.52
# Port: 22
# User: root
# SSH Key: ~/.ssh/id_rsa_production
# Database: MySQL on port 3306
```

**Staging Server:**
```bash
python navig.py server add staging
# Host: staging.example.com
# Port: 22
# User: deploy
# SSH Key: ~/.ssh/id_rsa_staging
# Database: MySQL on port 3306
```

**Development Server:**
```bash
python navig.py server add dev
# Host: dev.example.com
# Port: 2222
# User: developer
# SSH Key: ~/.ssh/id_rsa_dev
# Database: MySQL on port 3306
```

**Local Server:**
```bash
python navig.py server add local
# Host: localhost (127.0.0.1)
# Port: 22
# User: your-username
# SSH Key: ~/.ssh/id_rsa
# Database: MySQL on port 3306
```

---

## Multi-Server Management

NAVIG makes it easy to manage multiple servers (local dev, staging, production) from a single tool.

### Adding Servers

```bash
# Add production server
python navig.py server add production

# Add staging server
python navig.py server add staging

# Add development server
python navig.py server add dev

# Add local server
python navig.py server add local
```

### Listing Servers

```bash
# List all configured servers
python navig.py server list

# Output:
# Available servers:
#   * production (active) - 136.244.93.52
#     staging             - staging.example.com
#     dev                 - dev.example.com
#     local               - localhost
```

### Switching Between Servers

```bash
# Switch to production
python navig.py server use production

# Switch to staging
python navig.py server use staging

# Check current server
python navig.py server current
# Output: Current server: staging
```

### Using Servers Without Switching

```bash
# Override with --app flag
python navig.py --app production sql "SELECT COUNT(*) FROM users"
python navig.py --app staging health
python navig.py --app dev db-list

# Or use short flag -p
python navig.py -p local monitor-resources
```

### Real-World Multi-Server Workflow

```bash
# Test on local first
python navig.py --app local sql "SELECT 1"

# Deploy to dev server
python navig.py --app dev upload ./dist /var/www/html

# Test on dev
python navig.py --app dev health
python navig.py --app dev webserver-reload --server nginx

# If successful, deploy to staging
python navig.py --app staging upload ./dist /var/www/html
python navig.py --app staging webserver-reload --server nginx

# Run tests on staging
python navig.py --app staging sql "SELECT COUNT(*) FROM users"

# Finally, deploy to production
python navig.py --app production backup-all
python navig.py --app production upload ./dist /var/www/html
python navig.py --app production webserver-reload --server nginx
```

### Per-App Server Configuration

Create `.navig` file in each app directory:

```bash
# E-commerce app (uses production)
cd ~/apps/ecommerce
echo "production" > .navig

# Blog app (uses staging)
cd ~/apps/blog
echo "staging" > .navig

# New feature development (uses dev)
cd ~/apps/new-feature
echo "dev" > .navig

# Local testing (uses local)
cd ~/apps/local-test
echo "local" > .navig

# Now commands auto-use the right server
cd ~/apps/ecommerce
python navig.py health                    # Uses production

cd ~/apps/blog
python navig.py health                    # Uses staging

cd ~/apps/new-feature
python navig.py health                    # Uses dev
```

### Setting Default Server

```bash
# Set default server (used when no app marker or cache)
python navig.py server default production

# Now all commands default to production unless overridden
python navig.py health                    # Uses production
python navig.py --app dev health      # Uses dev
```

### Server Configuration Examples

**Production Server (Remote VPS):**
```yaml
# ~/.navig/apps/production.yaml
name: production
host: 136.244.93.52
port: 22
user: root
ssh_key: ~/.ssh/id_rsa_production
database:
  type: mysql
  remote_port: 3306
  local_tunnel_port: 3307
  name: app_production
  user: app_user
  password: <secure-password>
paths:
  web_root: /var/www/html
  logs: /var/log/nginx
```

**Staging Server (Testing Environment):**
```yaml
# ~/.navig/apps/staging.yaml
name: staging
host: staging.example.com
port: 22
user: deploy
ssh_key: ~/.ssh/id_rsa_staging
database:
  type: mysql
  remote_port: 3306
  local_tunnel_port: 3308
  name: app_staging
  user: app_user
  password: <secure-password>
paths:
  web_root: /var/www/staging
  logs: /var/log/nginx
```

**Development Server (Shared Dev Box):**
```yaml
# ~/.navig/apps/dev.yaml
name: dev
host: dev.example.com
port: 2222
user: developer
ssh_key: ~/.ssh/id_rsa_dev
database:
  type: mysql
  remote_port: 3306
  local_tunnel_port: 3309
  name: app_dev
  user: dev_user
  password: <dev-password>
paths:
  web_root: /home/developer/public_html
  logs: /home/developer/logs
```

**Local Server (Your Machine):**
```yaml
# ~/.navig/apps/local.yaml
name: local
host: localhost
port: 22
user: your-username
ssh_key: ~/.ssh/id_rsa
database:
  type: mysql
  remote_port: 3306
  local_tunnel_port: 3310
  name: app_local
  user: root
  password: <local-password>
paths:
  web_root: /var/www/html
  logs: /var/log/nginx
```

### Managing Server Credentials

```bash
# View server details (credentials hidden)
python navig.py server current

# Remove a server
python navig.py server remove old-dev

# Update server configuration (re-add with same name)
python navig.py server add production
# Will prompt to overwrite existing configuration
```

---

## Global Flags

All NAVIG commands support these global flags:

| Flag | Short | Description | Example |
|------|-------|-------------|---------|
| `--app` | `-p` | Specify server to use | `navig --app staging sql "SELECT 1"` |
| `--dry-run` | - | Preview actions without executing | `navig --dry-run delete /var/log/old.log` |
| `--json` | - | Output results as JSON | `navig --json db-list` |
| `--verbose` | `-v` | Detailed logging | `navig --verbose backup-create` |
| `--quiet` | `-q` | Minimal output (errors only) | `navig --quiet system-update` |

### App Selection Priority

1. **Command flag** (highest): `--app production`
2. **App marker**: `.navig` file in current directory
3. **Cached server**: Last used server
4. **Default server**: From config

### Creating App Markers

```bash
# In your app directory
echo "production" > .navig

# All commands now auto-use production server
navig health
navig sql "SELECT COUNT(*) FROM users"
```

---

## File Operations

### Delete Files/Directories

```bash
# Delete single file
navig delete /tmp/oldfile.txt

# Delete directory (requires confirmation)
navig delete /var/tmp/cache --recursive

# Force delete without confirmation
navig delete /var/tmp/cache --recursive --force

# Preview deletion
navig --dry-run delete /var/log/*.log
```

### Create Directories

```bash
# Create single directory
navig mkdir /var/www/newsite

# Create with custom permissions
navig mkdir /var/www/newsite --mode 755

# Create parent directories automatically
navig mkdir /var/www/sites/example.com/public --parents
```

### Change Permissions

```bash
# Set file permissions
navig chmod /var/www/index.php 644

# Set directory permissions recursively
navig chmod /var/www/html 755 --recursive

# Preview permission changes
navig --dry-run chmod /var/www 755 --recursive
```

### Change Ownership

```bash
# Change owner
navig chown /var/www/html www-data

# Change owner and group
navig chown /var/www/html www-data:www-data

# Change recursively
navig chown /var/www/html www-data:www-data --recursive
```

---

## Database Management

### List Databases

```bash
# List all databases with sizes
navig db-list

# JSON output for scripting
navig db-list --json | jq '.databases[] | select(.size_mb > 100)'
```

### List Tables

```bash
# List tables in database
navig db-tables myapp_production

# See table sizes and row counts
navig db-tables wordpress --json
```

### Optimize Tables

```bash
# Optimize single table (reclaim space)
navig db-optimize wp_posts

# Preview optimization
navig --dry-run db-optimize wp_posts

# Optimize during maintenance window (locks table)
navig db-optimize large_table
```

### Repair Corrupted Tables

```bash
# Repair table
navig db-repair wp_options

# Check results
navig --verbose db-repair wp_postmeta
```

### List Database Users

```bash
# List all MySQL users
navig db-users

# JSON output
navig db-users --json
```

---

## HestiaCP Integration

### User Management

```bash
# List all users
navig hestia users

# List domains
navig hestia domains

# List domains for specific user
navig hestia domains --user john

# Add new user
navig hestia add-user john SecurePass123! john@example.com

# Delete user (with confirmation)
navig hestia delete-user john

# Force delete without confirmation
navig hestia delete-user john --force
```

### Domain Management

```bash
# Add domain to user
navig hestia add-domain john example.com

# Delete domain
navig hestia delete-domain john example.com

# Renew SSL certificate
navig hestia renew-ssl john example.com

# Rebuild web configuration
navig hestia rebuild-web john
```

### Backup

```bash
# Backup user account
navig hestia backup-user john

# JSON output
navig hestia backup-user john --json
```

---

## Backup System

### Create Backups

```bash
# Backup system configuration files
navig backup-system-config

# Backup all databases
navig backup-all-databases

# Compress backup with gzip
navig backup-all-databases --compress gzip

# Backup HestiaCP data
navig backup-hestia

# Backup web server configs
navig backup-web-config

# Comprehensive backup (everything)
navig backup-all
```

### Restore Backups

```bash
# Restore from backup
navig restore-backup backup-2025-11-21.tar.gz

# Restore specific component
navig restore-backup backup-2025-11-21.tar.gz --component database

# Preview restore
navig --dry-run restore-backup backup-2025-11-21.tar.gz
```

### List Backups

```bash
# List all backups
navig list-backups

# JSON output
navig list-backups --json
```

---

## Resource Monitoring

### Monitor Resources

```bash
# Real-time resource monitoring (CPU, RAM, disk, network)
navig monitor-resources

# JSON output for dashboards
navig monitor-resources --json

# Continuous monitoring (refresh every 5s)
navig monitor-resources --continuous
```

### Monitor Disk Usage

```bash
# Monitor disk with 80% threshold alert
navig monitor-disk --threshold 80

# JSON output
navig monitor-disk --threshold 90 --json
```

### Monitor Services

```bash
# Check status of all configured services
navig monitor-services

# JSON output
navig monitor-services --json
```

### Network Monitoring

```bash
# Monitor network connections and bandwidth
navig monitor-network

# JSON output
navig monitor-network --json
```

### Health Check

```bash
# Comprehensive health check
navig health

# JSON output
navig health --json
```

### Generate Reports

```bash
# Generate detailed system report
navig generate-report

# JSON output
navig generate-report --json
```

---

## Security Management

### Firewall (UFW)

```bash
# Check firewall status
navig firewall-status

# Allow SSH
navig firewall-allow 22 --tcp

# Allow HTTP/HTTPS
navig firewall-allow 80 --tcp
navig firewall-allow 443 --tcp

# Allow from specific IP
navig firewall-allow 3306 --tcp --from 10.0.0.10

# Delete firewall rule
navig firewall-delete 3306 --tcp

# Reset firewall (warning: removes all rules)
navig firewall-reset
```

### Port Scanning

```bash
# Scan open ports
navig scan-ports

# JSON output
navig scan-ports --json
```

### Fail2Ban

```bash
# Check Fail2Ban status
navig check-fail2ban

# JSON output
navig check-fail2ban --json
```

### Permission Auditing

```bash
# Audit file/directory permissions
navig audit-permissions /var/www

# Recursive audit
navig audit-permissions /etc --recursive

# JSON output
navig audit-permissions /var/www --json
```

### SSL Certificate Check

```bash
# Check SSL certificate expiry
navig check-ssl example.com

# JSON output
navig check-ssl example.com --json
```

### Security Audit

```bash
# Comprehensive security scan
navig security-audit

# Save results as JSON
navig security-audit --json > security-report.json
```

---

## System Maintenance

### System Updates

```bash
# Update package lists
navig system-update

# Update and upgrade packages
navig system-update --upgrade

# Dry-run (preview updates)
navig --dry-run system-update --upgrade
```

### Log Cleanup

```bash
# Clean old logs (30+ days)
navig cleanup-logs

# Custom retention (60 days)
navig cleanup-logs --days 60

# Preview cleanup
navig --dry-run cleanup-logs
```

### Package Cleanup

```bash
# Remove unused packages
navig cleanup-packages

# Preview cleanup
navig --dry-run cleanup-packages
```

### System Optimization

```bash
# Optimize system (clear caches, temp files)
navig optimize-system

# JSON output
navig optimize-system --json
```

### Check Updates

```bash
# Check for available updates
navig check-updates

# JSON output
navig check-updates --json
```

### Schedule Maintenance

```bash
# Schedule maintenance window
navig schedule-maintenance --time "03:00" --duration 60

# JSON output
navig schedule-maintenance --time "03:00" --duration 60 --json
```

---

## Web Server Management

### List Virtual Hosts

```bash
# List Nginx virtual hosts
navig webserver-list-vhosts --server nginx

# List Apache virtual hosts
navig webserver-list-vhosts --server apache

# JSON output
navig webserver-list-vhosts --server nginx --json
```

### Test Configuration

```bash
# Test Nginx configuration before reload
navig webserver-test-config --server nginx

# Test Apache configuration
navig webserver-test-config --server apache

# JSON output
navig webserver-test-config --server nginx --json
```

### Enable/Disable Sites

```bash
# Enable Nginx site
navig webserver-enable-site example.com --server nginx

# Disable Nginx site
navig webserver-disable-site example.com --server nginx

# Enable Apache site
navig webserver-enable-site example.com --server apache

# Preview changes
navig --dry-run webserver-enable-site example.com --server nginx
```

### Enable/Disable Modules (Apache)

```bash
# Enable Apache module
navig webserver-enable-module rewrite --server apache
navig webserver-enable-module ssl --server apache

# Disable Apache module
navig webserver-disable-module status --server apache

# Preview changes
navig --dry-run webserver-enable-module headers --server apache
```

### Safe Reload

```bash
# Safely reload Nginx (tests config first)
navig webserver-reload --server nginx

# Safely reload Apache
navig webserver-reload --server apache

# JSON output
navig webserver-reload --server nginx --json
```

### Performance Recommendations

```bash
# Get Nginx optimization tips
navig webserver-recommendations --server nginx

# Get Apache optimization tips
navig webserver-recommendations --server apache

# JSON output
navig webserver-recommendations --server nginx --json
```

---

## Troubleshooting

### Connection Issues

```bash
# Test SSH connection
navig run "echo test"

# Check server configuration
navig server current

# Verbose logging
navig --verbose sql "SELECT 1"
```

### Dry-Run Mode

Always test destructive operations first:

```bash
# Preview deletion
navig --dry-run delete /var/log/old.log --recursive

# Preview firewall changes
navig --dry-run firewall-allow 3306

# Preview system updates
navig --dry-run system-update --upgrade
```

### JSON Output for Automation

```bash
# Check disk usage and alert if > 90%
navig monitor-disk --threshold 90 --json | jq '.alert'

# List large databases
navig db-list --json | jq '.databases[] | select(.size_mb > 1000)'

# Export security audit to file
navig security-audit --json > security-$(date +%Y%m%d).json
```

### Common Errors

**"No active server"**
```bash
# Set active server
navig server use production

# Or create app marker
echo "production" > .navig
```

**"Permission denied"**
```bash
# Check SSH key
navig run "whoami"

# Check sudo access
navig run "sudo -l"
```

**"Tunnel connection failed"**
```bash
# Check tunnel status
navig tunnel status

# Restart tunnel
navig tunnel restart
```

---

## Best Practices

### 1. Always Use Dry-Run First

```bash
# Bad - immediate execution
navig delete /var/log --recursive

# Good - preview first
navig --dry-run delete /var/log --recursive
# Review output, then execute if safe
navig delete /var/log --recursive
```

### 2. Use App Markers

```bash
# Create .navig file in each app
cd ~/apps/ecommerce
echo "ecommerce-prod" > .navig

cd ~/apps/blog
echo "blog-staging" > .navig

# Commands now auto-use correct server
cd ~/apps/ecommerce
navig health  # Uses ecommerce-prod

cd ~/apps/blog
navig health  # Uses blog-staging
```

### 3. Automate with JSON Output

```bash
# Create monitoring script
#!/bin/bash
DISK=$(navig monitor-disk --threshold 90 --json | jq -r '.alert')
if [ "$DISK" = "true" ]; then
    echo "WARNING: Disk usage > 90%" | mail -s "Server Alert" admin@example.com
fi
```

### 4. Test Configuration Before Reload

```bash
# Always test web server config before reload
navig webserver-test-config --server nginx
# If successful, then reload
navig webserver-reload --server nginx
```

### 5. Regular Backups

```bash
# Create weekly backup script
#!/bin/bash
navig backup-all --compress gzip
navig cleanup-logs --days 30
navig optimize-system
```

---

## Getting Help

```bash
# General help
python navig.py --help

# Command-specific help
python navig.py server --help
python navig.py sql --help
python navig.py firewall-allow --help

# View all available commands
python navig.py --help | grep "navig "
```

---

**NAVIG - No Admin Visible In Graveyard**
Keep your servers alive. Forever.
