# NAVIG Installation Guide

## 📦 Installing NAVIG in Editable Mode

This guide explains how to install NAVIG as a Python package in editable mode, which allows you to:
- ✅ Use the `navig` command globally from any directory
- ✅ Have code changes reflected immediately without reinstallation
- ✅ Maintain proper Python package structure for development and distribution

---

## Prerequisites

- **Python 3.8 or higher** (check with `python --version` or `python3 --version`)
- **pip** (Python package installer)
- **Git** (for cloning the repository)

---

## Installation Steps

### 1. Navigate to the App Directory

```powershell
cd remote-manager
```

### 2. (Optional) Uninstall Existing Installation

If you previously installed NAVIG, uninstall it first:

```powershell
pip uninstall navig -y
```

### 3. Install in Editable Mode

Install NAVIG with all dependencies in editable mode:

```powershell
pip install -e .
```

**What this does**:
- Installs all required dependencies from `pyapp.toml`
- Creates a `navig` command that's available globally
- Links the installation to your source code (changes are reflected immediately)
- Creates/updates the `navig.egg-info` directory with package metadata

### 4. Verify Installation

Check that the installation was successful:

```powershell
# Check version
navig --version

# Show help
navig --help

# Test from a different directory
cd ~
navig --version
```

**Expected output**:
```
NAVIG v1.0.0
The Schema's encrypted operations tool
```

### Cross-platform one-shot installers

From `navig-core/`:

```bash
# Linux
bash scripts/install_navig_linux.sh

# macOS
bash scripts/install_navig_macos.sh
```

```powershell
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File .\scripts\install_navig_windows.ps1
```

### Automatic Telegram setup during install

If you provide a token, installers now auto-configure Telegram and attempt to start the NAVIG daemon with bot + gateway.

```bash
# Linux / macOS
export NAVIG_TELEGRAM_BOT_TOKEN="<your-bot-token>"
bash scripts/install_navig_linux.sh
# or
bash scripts/install_navig_macos.sh
```

```powershell
# Windows
$env:NAVIG_TELEGRAM_BOT_TOKEN="<your-bot-token>"
powershell -ExecutionPolicy Bypass -File .\scripts\install_navig_windows.ps1
```

Main installers support the same variable:

```bash
NAVIG_TELEGRAM_BOT_TOKEN="<your-bot-token>" bash install.sh
```

```powershell
$env:NAVIG_TELEGRAM_BOT_TOKEN="<your-bot-token>"
.\install.ps1
```

---

## What Changed in pyapp.toml

### ✅ Fixed Issues

1. **Added missing `pyperclip` dependency**:
   - Was in `requirements.txt` but missing from `pyapp.toml`
   - Now included: `"pyperclip>=1.8.2"`

2. **Removed `questionary` dependency**:
   - Commented out in `requirements.txt` due to Windows compatibility issues
   - Removed from `pyapp.toml` to match

3. **Added `navig.modules` to package list**:
   - Was missing from the packages list
   - Now includes: `packages = ["navig", "navig.commands", "navig.modules"]`

### 📋 Current Configuration

**Entry Point**:
```toml
[app.scripts]
navig = "navig.cli:app"
```

This creates a `navig` command that calls the Typer `app` object in `navig/cli.py`.

**Dependencies**:
- `typer[all]>=0.9.0` - CLI framework
- `rich>=13.0.0` - Terminal UI and formatting
- `pyyaml>=6.0` - Configuration file handling
- `requests>=2.31.0` - HTTP requests (OpenRouter API)
- `paramiko>=3.0.0` - SSH operations
- `colorama>=0.4.6` - Cross-platform ANSI colors
- `psutil>=5.9.0` - Process management
- `pyperclip>=1.8.2` - Clipboard operations

**Packages**:
- `navig` - Main package
- `navig.commands` - Command modules
- `navig.modules` - Utility modules

---

## Development Workflow

### Making Code Changes

1. Edit any Python file in the `navig/` directory
2. Changes are **immediately available** - no reinstallation needed
3. Just run `navig` to test your changes

### Adding New Dependencies

If you add a new dependency:

1. Add it to `pyapp.toml` under `[app] dependencies`
2. Run `pip install -e .` again to install the new dependency

### Running Tests

```powershell
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=navig
```

---

## Troubleshooting

### Issue: `navig: command not found`

**Solution**: Make sure pip's scripts directory is in your PATH:

```powershell
# Windows PowerShell
$env:PATH += ";$env:APPDATA\Python\Python312\Scripts"

# Or add permanently via System Environment Variables
```

### Issue: Changes not reflected

**Solution**: 
- Editable mode should reflect changes immediately
- If not, try reinstalling: `pip install -e . --force-reinstall --no-deps`

### Issue: Import errors

**Solution**:
- Make sure all packages are listed in `[tool.setuptools] packages`
- Verify `__init__.py` files exist in all package directories

---

## Uninstalling

To completely remove NAVIG:

```powershell
pip uninstall navig
```

This removes the package but keeps your source code intact.

---

## Next Steps

After installation, you can:

1. **Run the interactive menu**: `navig`
2. **Add a host**: `navig host add <name>`
3. **View help**: `navig --help`
4. **Check specific command help**: `navig host --help`

For more information, see:
- `README.md` - App overview
- `USAGE_GUIDE.md` - Detailed usage instructions
- `CHANGELOG.md` - Recent changes and improvements

---

## Ubuntu Server Installer (Operational Factory MVP)

Use this path when you want NAVIG deployed as a normal server installer with systemd persistence.

### What it installs
- Docker Engine + Compose plugin (if missing)
- NAVIG Operational Factory stack in `/opt/navig/factory`
- systemd service: `navig-factory.service`
- Local-only dashboard at `127.0.0.1:8088`

### Run installer

```bash
cd navig-core
sudo bash scripts/install_navig_factory_server.sh
```

### Service lifecycle

```bash
sudo systemctl status navig-factory
sudo systemctl restart navig-factory
sudo systemctl stop navig-factory
sudo systemctl start navig-factory
```

### Stack lifecycle (direct compose)

```bash
cd /opt/navig/factory
./scripts.sh start
./scripts.sh stop
./scripts.sh status
./scripts.sh logs
./scripts.sh migrate
```

### Required env file

`/opt/navig/factory/.env` is generated from `.env.example` on first install.
Update secrets and email settings before production use.

### Security defaults
- Restricted actions are approval-gated (send email, merge PR, deploy, payment, delete, mass message)
- All tool calls are audited with sanitized inputs/outputs
- No credentials are embedded in prompts



