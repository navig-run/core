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
# Clone the repository
git clone https://github.com/your-org/navig.git
cd navig

# Install dependencies
pip install -r requirements.txt

# Verify installation
python navig.py --version
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
navig monitor disk

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
| `monitor` | Server monitoring | `navig monitor health` |
| `security` | Security management | `navig security firewall` |
| `web` | Web server control | `navig web vhosts` |
| `docker` | Docker management | `navig docker ps` |
| `tunnel` | SSH tunnel control | `navig tunnel start` |
| `backup` | Configuration backup | `navig backup export` |

### Interactive Mode

Run any group without a subcommand to enter interactive mode:

```bash
navig host      # Interactive host menu
navig db        # Interactive database menu
navig menu      # Full interactive interface
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
navig upload local.txt /remote/path/

# Download file
navig download /remote/file.txt ./local/
```

### Server Health Check

```bash
# Quick health check
navig monitor health

# Detailed resource usage
navig monitor resources
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


