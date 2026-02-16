# NAVIG Plugin Development Guide

This guide explains how to create plugins to extend NAVIG's functionality.

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Plugin Structure](#plugin-structure)
4. [Plugin API](#plugin-api)
5. [Best Practices](#best-practices)
6. [Publishing & Sharing](#publishing--sharing)
7. [Examples](#examples)

---

## Overview

NAVIG's plugin system allows you to:

- Add custom CLI commands (`navig myplugin mycommand`)
- Access NAVIG's SSH, config, and console APIs
- Store plugin-specific configuration
- Execute commands on remote hosts
- Integrate with external services

### Plugin Types

| Type | Location | Use Case |
|------|----------|----------|
| **Built-in** | `navig/plugins/` | Bundled with NAVIG |
| **User** | `~/.navig/plugins/` | Per-user extensions |
| **Project** | `.navig/plugins/` | Project-specific tools |

### Plugin Loading Order

1. Built-in plugins are loaded first
2. User plugins override built-in plugins (same name)
3. Project plugins override user plugins (same name)

---

## Quick Start

Create your first plugin in 5 minutes:

### 1. Create Plugin Directory

```bash
mkdir -p ~/.navig/plugins/my-plugin
cd ~/.navig/plugins/my-plugin
```

### 2. Create plugin.py

```python
# ~/.navig/plugins/my-plugin/plugin.py
from typing import Tuple, List
import typer

# Required: Plugin name (CLI command)
name = "my-plugin"

# Required: Typer app with commands
app = typer.Typer(help="My custom NAVIG plugin")

# Required: Dependency check function
def check_dependencies() -> Tuple[bool, List[str]]:
    return (True, [])  # No dependencies

# Commands
@app.command()
def hello():
    """Say hello from my plugin."""
    print("Hello from my-plugin!")

@app.command()
def status():
    """Show plugin status."""
    from navig.plugins.base import PluginAPI
    api = PluginAPI()
    
    host = api.get_active_host()
    print(f"Active host: {host or '(none)'}")
```

### 3. Test Your Plugin

```bash
navig my-plugin hello
# Output: Hello from my-plugin!

navig my-plugin status
# Output: Active host: production

navig plugin list
# Shows your plugin as loaded
```

---

## Plugin Structure

### Minimal Plugin

```
my-plugin/
├── plugin.py          # Required: Plugin entry point
```

### Full Plugin

```
my-plugin/
├── plugin.py          # Required: Entry point with name, app, check_dependencies
├── commands.py        # Optional: Command implementations
├── plugin.yaml        # Optional: Metadata (version, description, etc.)
├── requirements.txt   # Optional: Python dependencies
└── README.md          # Optional: Documentation
```

### plugin.py Requirements

Your `plugin.py` must export these:

| Export | Type | Description |
|--------|------|-------------|
| `name` | `str` | CLI command name (`navig <name>`) |
| `app` | `typer.Typer` | Typer app with commands |
| `check_dependencies()` | `function` | Returns `(success: bool, missing: List[str])` |

Optional exports:

| Export | Type | Description |
|--------|------|-------------|
| `description` | `str` | Plugin description for help text |
| `version` | `str` | Plugin version (e.g., "1.0.0") |
| `author` | `str` | Plugin author |

### plugin.yaml Format

```yaml
name: my-plugin
version: 1.0.0
description: My custom NAVIG plugin
author: Your Name
homepage: https://github.com/you/navig-plugin-myplugin

# Python package dependencies
dependencies:
  - requests>=2.28.0
  - python-dateutil

# Required permissions
permissions:
  - ssh           # Execute remote commands
  - config_read   # Read NAVIG config
  - config_write  # Modify NAVIG config
  - file_system   # Access local files
  - network       # Make network requests
```

---

## Plugin API

### PluginAPI Class

Access NAVIG functionality safely:

```python
from navig.plugins.base import PluginAPI

api = PluginAPI()
```

#### Get Active Context

```python
# Get active host
host = api.get_active_host()  # Returns: "production" or None

# Get active app
app = api.get_active_app()    # Returns: "myapp" or None
```

#### Execute Remote Commands

```python
# Run command on active host
success, stdout, stderr = api.run_remote("ls -la /var/www")

if success:
    print(stdout)
else:
    print(f"Error: {stderr}")

# Run on specific host
success, stdout, stderr = api.run_remote(
    "docker ps",
    host_name="staging",
    timeout=60
)
```

#### File Transfer

```python
# Upload file
success, error = api.upload_file(
    local_path="./config.yaml",
    remote_path="/etc/app/config.yaml"
)

# Download file
success, error = api.download_file(
    remote_path="/var/log/app.log",
    local_path="./app.log"
)
```

#### Console Output

```python
# Use NAVIG's console helper
api.console.success("Operation completed!")
api.console.error("Something went wrong", "Details here")
api.console.warning("Proceed with caution")
api.console.info("FYI: This is informational")
api.console.dim("Less important note")
```

### Configuration API

Store plugin-specific settings:

```python
from navig.core import Config

config = Config()

# Get plugin config
db_path = config.get_plugin_config("my-plugin", "db_path", "~/.navig/data.db")

# Set plugin config
config.set_plugin_config("my-plugin", "last_sync", "2025-12-08")
config.save()

# Get all plugin config
all_config = config.get_plugin_config("my-plugin")
# Returns: {"db_path": "...", "last_sync": "..."}
```

### Host Configuration

```python
# Get host config
host_config = api.get_host_config("production")
# Returns: {"host": "1.2.3.4", "user": "root", "port": 22, ...}

# Get active host config
active_config = api.get_host_config()  # Uses active host
```

---

## Best Practices

### 1. Dependency Checking

Always verify dependencies before using them:

```python
def check_dependencies() -> Tuple[bool, List[str]]:
    missing = []
    
    try:
        import requests
    except ImportError:
        missing.append("requests")
    
    try:
        import pandas
    except ImportError:
        missing.append("pandas")
    
    return (len(missing) == 0, missing)
```

### 2. Error Handling

Handle errors gracefully:

```python
@app.command()
def risky_command():
    """Command that might fail."""
    from navig.plugins.base import PluginAPI
    import typer
    
    api = PluginAPI()
    
    host = api.get_active_host()
    if not host:
        api.console.error("No active host", "Use 'navig host use <name>' first")
        raise typer.Exit(1)
    
    success, stdout, stderr = api.run_remote("dangerous-command")
    
    if not success:
        api.console.error("Command failed", stderr)
        raise typer.Exit(1)
    
    api.console.success("Done!")
```

### 3. User Confirmation

Ask before destructive actions:

```python
@app.command()
def delete_all(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation")
):
    """Delete everything (dangerous!)."""
    if not force:
        confirm = typer.confirm("This will delete everything. Continue?")
        if not confirm:
            raise typer.Abort()
    
    # Proceed with deletion...
```

### 4. Respect Global Flags

Check for dry-run and other flags:

```python
@app.command()
def deploy(ctx: typer.Context):
    """Deploy application."""
    # Access global flags from context
    dry_run = ctx.obj.get('dry_run', False) if ctx.obj else False
    
    if dry_run:
        print("Would deploy to production")
        return
    
    # Actually deploy...
```

### 5. Logging

Use NAVIG's console helper for consistent output:

```python
from navig import console_helper as ch

ch.success("Task completed")           # ✓ Task completed
ch.error("Failed", "Details here")     # ✗ Failed: Details here
ch.warning("Caution needed")           # ⚠ Caution needed
ch.info("Information")                 # ℹ Information
ch.dim("Less important")               # (dimmed text)
ch.step("Step 1")                      # → Step 1
```

---

## Publishing & Sharing

### Option 1: Git Repository

Share your plugin as a Git repository:

```bash
# Create repository
cd ~/.navig/plugins/my-plugin
git init
git add .
git commit -m "Initial plugin release"
git remote add origin https://github.com/you/navig-plugin-myplugin.git
git push -u origin main
```

Users can install:

```bash
# Clone to user plugins directory
git clone https://github.com/you/navig-plugin-myplugin.git ~/.navig/plugins/my-plugin
```

### Option 2: Local Installation

Share as a directory:

```bash
# Install from local path
navig plugin install /path/to/my-plugin

# Or copy manually
cp -r /path/to/my-plugin ~/.navig/plugins/
```

### Option 3: Archive

Share as a zip file:

```bash
# Create archive
cd ~/.navig/plugins
zip -r my-plugin.zip my-plugin/

# Users extract to their plugins directory
unzip my-plugin.zip -d ~/.navig/plugins/
```

---

## Examples

### Example 1: Slack Notifications

```python
# slack-notify/plugin.py
from typing import Tuple, List
import typer

name = "slack"
description = "Send Slack notifications from NAVIG"
app = typer.Typer(help=description)

def check_dependencies() -> Tuple[bool, List[str]]:
    missing = []
    try:
        import requests
    except ImportError:
        missing.append("requests")
    return (len(missing) == 0, missing)

@app.command()
def notify(
    message: str = typer.Argument(..., help="Message to send"),
    channel: str = typer.Option("#general", "--channel", "-c"),
):
    """Send notification to Slack."""
    import requests
    from navig.core import Config
    
    config = Config()
    webhook = config.get_plugin_config("slack", "webhook_url")
    
    if not webhook:
        print("Set webhook first: navig slack config --webhook URL")
        raise typer.Exit(1)
    
    requests.post(webhook, json={
        "channel": channel,
        "text": message
    })
    print(f"Sent to {channel}: {message}")

@app.command()
def config(
    webhook: str = typer.Option(None, "--webhook", help="Slack webhook URL"),
):
    """Configure Slack integration."""
    from navig.core import Config
    
    config = Config()
    
    if webhook:
        config.set_plugin_config("slack", "webhook_url", webhook)
        config.save()
        print("Webhook URL saved")
    else:
        current = config.get_plugin_config("slack", "webhook_url")
        print(f"Current webhook: {current or '(not set)'}")
```

### Example 2: S3 Backup

```python
# s3-backup/plugin.py
from typing import Tuple, List
import typer

name = "s3"
description = "Backup files to AWS S3"
app = typer.Typer(help=description)

def check_dependencies() -> Tuple[bool, List[str]]:
    missing = []
    try:
        import boto3
    except ImportError:
        missing.append("boto3")
    return (len(missing) == 0, missing)

@app.command()
def backup(
    remote_path: str = typer.Argument(..., help="Remote path to backup"),
    bucket: str = typer.Option(..., "--bucket", "-b", help="S3 bucket name"),
):
    """Backup remote files to S3."""
    import boto3
    import tempfile
    from pathlib import Path
    from navig.plugins.base import PluginAPI
    
    api = PluginAPI()
    
    # Download from remote
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / "backup.tar.gz"
        
        # Create tarball on remote
        api.run_remote(f"tar -czf /tmp/backup.tar.gz {remote_path}")
        
        # Download
        api.download_file("/tmp/backup.tar.gz", str(local_path))
        
        # Upload to S3
        s3 = boto3.client('s3')
        s3.upload_file(str(local_path), bucket, f"backups/{Path(remote_path).name}.tar.gz")
        
        api.console.success(f"Backed up to s3://{bucket}/backups/")
```

### Example 3: Custom Monitoring

```python
# health-check/plugin.py
from typing import Tuple, List
import typer

name = "health"
description = "Custom health checks for your infrastructure"
app = typer.Typer(help=description)

def check_dependencies() -> Tuple[bool, List[str]]:
    return (True, [])

@app.command()
def check(
    all_hosts: bool = typer.Option(False, "--all", "-a", help="Check all hosts"),
):
    """Run health checks."""
    from navig.plugins.base import PluginAPI
    from navig.config import get_config_manager
    
    api = PluginAPI()
    config_manager = get_config_manager()
    
    if all_hosts:
        hosts = config_manager.list_hosts()
    else:
        host = api.get_active_host()
        hosts = [host] if host else []
    
    for host_name in hosts:
        api.console.step(f"Checking {host_name}...")
        
        # Check disk
        success, stdout, _ = api.run_remote(
            "df -h / | tail -1 | awk '{print $5}'",
            host_name=host_name
        )
        if success:
            usage = stdout.strip()
            if int(usage.rstrip('%')) > 90:
                api.console.warning(f"  Disk: {usage} (HIGH)")
            else:
                api.console.dim(f"  Disk: {usage}")
        
        # Check memory
        success, stdout, _ = api.run_remote(
            "free -m | awk '/Mem:/ {printf \"%.0f%%\", $3/$2*100}'",
            host_name=host_name
        )
        if success:
            mem = stdout.strip()
            if int(mem.rstrip('%')) > 90:
                api.console.warning(f"  Memory: {mem} (HIGH)")
            else:
                api.console.dim(f"  Memory: {mem}")
        
        api.console.success(f"  {host_name} OK")
```

---

## Troubleshooting

### Plugin Not Loading

1. Check plugin status:
   ```bash
   navig plugin list --all
   ```

2. Get detailed info:
   ```bash
   navig plugin info my-plugin
   ```

3. Check for missing dependencies:
   ```bash
   pip install -r ~/.navig/plugins/my-plugin/requirements.txt
   ```

### Import Errors

Ensure your plugin.py imports are correct:

```python
# Good: Import from installed packages
import typer
from navig.plugins.base import PluginAPI

# Bad: Relative imports in plugin.py
from .commands import my_func  # This may fail
```

### Permission Errors

If your plugin needs elevated permissions, document them in plugin.yaml and handle gracefully:

```python
success, stdout, stderr = api.run_remote("sudo systemctl restart nginx")
if not success and "permission denied" in stderr.lower():
    api.console.error("Insufficient permissions", "Run with sudo-capable user")
```

---

## Plugin Command Reference

| Command | Description |
|---------|-------------|
| `navig plugin list` | List all plugins |
| `navig plugin list --all` | Include disabled plugins |
| `navig plugin info <name>` | Show plugin details |
| `navig plugin enable <name>` | Enable a disabled plugin |
| `navig plugin disable <name>` | Disable a plugin |
| `navig plugin install <path>` | Install from local path |
| `navig plugin uninstall <name>` | Remove a user plugin |

---

## Need Help?

- Check the built-in `hello` plugin for a working example
- Open an issue on GitHub
- Review the `navig/plugins/base.py` source for API details


