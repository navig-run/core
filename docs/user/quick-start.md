# NAVIG Quick Start Guide

## What is NAVIG?

**NAVIG** (No Admin Visible In Graveyard) is a cross-platform CLI tool for managing remote servers via SSH. It provides a unified interface for:

- 🖥️ **Host Management** - Configure and manage multiple remote servers
- 📦 **App Management** - Organize applications across hosts
- 🔐 **SSH Tunnels** - Secure database and service access
- 🗄️ **Database Operations** - Query, backup, restore databases
- 📁 **File Operations** - Upload, download, manage remote files
- 🔒 **Security** - Firewall, Fail2Ban, SSH auditing
- 📊 **Monitoring** - Resource usage, health checks, reports

## Installation

```bash
# Install from PyPI
pip install navig

# Or install from source (for development)
git clone https://github.com/navig-run/core.git
cd core
pip install -e .

# Verify installation
navig --version
```

**Bootstrap Telegram during first-run setup:**
```bash
# Linux/macOS
NAVIG_TELEGRAM_BOT_TOKEN="<your-bot-token>" navig init --profile operator

# Windows PowerShell
$env:NAVIG_TELEGRAM_BOT_TOKEN="<your-bot-token>"
navig init --profile operator
```

## First Steps

### 1. Add Your First Host

```bash
# Interactive wizard
navig host add myserver

# Or create manually in ~/.navig/hosts/myserver.yaml
```

### 2. Set Active Host

```bash
navig host use myserver
```

### 3. Test Connection

```bash
navig host test
```

### 4. Run Commands

```bash
# Simple command
navig run "ls -la"

# View disk usage
navig host monitor show --disk

# List databases
navig db list
```

## Command Structure

NAVIG uses a hierarchical command structure:

```
navig <group> <command> [options]
```

### Main Command Groups

| Group | Description | Example |
|-------|-------------|---------|
| `host` | Manage remote hosts | `navig host list` |
| `app` | Manage applications | `navig app list` |
| `db` | Database operations | `navig db list` |
| `host monitor` | Server monitoring | `navig host monitor show` |
| `host security` | Security management | `navig host security show --firewall` |
| `web` | Web server control | `navig web vhosts` |
| `docker` | Docker management | `navig docker ps` |
| `tunnel` | SSH tunnel control | `navig tunnel run` |
| `backup` | Configuration backup | `navig backup export` |

### Interactive Mode

Run any group without a subcommand to enter interactive mode:

```bash
navig host      # Interactive host menu
navig db        # Interactive database menu
```

## Common Workflows

### Database Backup

```bash
# List databases
navig db list

# Dump specific database
navig db dump mydb -o backup.sql
```

### File Transfer

```bash
# Upload file
navig file add local.txt /remote/path/

# Download file
navig file get /remote/file.txt ./local/
```

### Server Health Check

```bash
# Health overview
navig host monitor show

# Detailed resource usage
navig host monitor show --resources
```

## Getting Help

```bash
# General help
navig --help

# Command group help
navig db --help

# Specific command help
navig db list --help
```

## Next Steps

- Read the [Commands Reference](commands.md) for detailed command documentation
- Check [Full Handbook](HANDBOOK.md) for comprehensive documentation
- See [Troubleshooting](troubleshooting.md) for common issues
