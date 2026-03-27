# NAVIG Installation Guide

## 📦 Installing NAVIG in Editable Mode

This guide explains how to install NAVIG as a Python package in editable mode, which allows you to:
- ✅ Use the `navig` command globally from any directory
- ✅ Have code changes reflected immediately without reinstallation
- ✅ Maintain proper Python package structure for development and distribution

---

## Prerequisites

- **Python 3.10 or higher** (check with `python --version` or `python3 --version`)
- **pip** (Python package installer)
- **Git** (for cloning the repository)

---

## Installation Steps

### 1. Navigate to the App Directory

```powershell
cd navig-core
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
- Installs all required dependencies from `pyproject.toml`
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

**Expected output**: NAVIG version string — run `navig --version` to verify
you see the current version printed (e.g. `NAVIG 2.4.14`).

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

### Windows Matrix Synapse (recommended: Docker Desktop)

If you want Matrix support on Windows, run Synapse in Docker Desktop (Linux containers).
This is more reliable and easier to maintain than a native WSL-only install.

From `navig-core/`:

```powershell
# 1) Bootstrap Synapse files, generate config, start container
powershell -ExecutionPolicy Bypass -File .\scripts\install_matrix_synapse_windows.ps1 -ServerName navig.local -Port 8008

# 2) Create an admin user
powershell -ExecutionPolicy Bypass -File .\scripts\create_synapse_admin_windows.ps1 -Username navigadmin -Password "ChangeMe!123" -Admin

# 3) Configure NAVIG to use the local homeserver
navig config set comms.matrix.enabled true
navig config set comms.matrix.homeserver_url http://127.0.0.1:8008
navig config set comms.matrix.user_id @navigadmin:navig.local
```

Operational helpers:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\synapse_windows_up.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\synapse_windows_down.ps1
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

## pyproject.toml — Current Configuration

**Entry Point**:
```toml
[project.scripts]
navig = "navig.main:main"
```

The `navig` command runs `main()` in `navig/main.py`.

**Core Dependencies** (see `pyproject.toml` for the full authoritative list):
- `typer[all]>=0.9.0` — CLI framework
- `rich>=13.7.0` — Terminal UI and formatting
- `pyyaml>=6.0` — Configuration files
- `requests>=2.31.0` + `httpx>=0.27.0` + `aiohttp>=3.9.0` — HTTP
- `paramiko>=3.4.0` — SSH operations
- `loguru>=0.7.0` — Logging
- `pydantic>=2.0.0` — Data validation
- `Jinja2>=3.1.0` — Templates
- `cryptography>=42.0.0` — Vault encryption
- `platformdirs>=4.0.0` — OS-standard paths
- `psutil>=5.9.0`, `colorama>=0.4.6`, `pyperclip>=1.8.2` — System utilities

**Packages**: auto-discovered from `navig/` via `[tool.setuptools.packages.find]`

---

## Development Workflow

### Making Code Changes

1. Edit any Python file in the `navig/` directory
2. Changes are **immediately available** - no reinstallation needed
3. Just run `navig` to test your changes

### Adding New Dependencies

If you add a new dependency:

1. Add it to `pyproject.toml` under `[project] dependencies`
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

## Configuration File Locations

NAVIG stores configuration and runtime data in OS-standard directories. No
files are written to the install directory or system paths.

### Windows

| Data type | Path | Notes |
|-----------|------|-------|
| **Config** (roaming) | `%APPDATA%\NAVIG\config.yaml` | Synced across machines if roaming profiles are enabled |
| **Hosts / apps** | `%APPDATA%\NAVIG\hosts\` | Host YAML files live here |
| **Vault** (encrypted) | `%LOCALAPPDATA%\NAVIG\vault\` | Machine-local, never synced |
| **Logs** | `%LOCALAPPDATA%\NAVIG\logs\` | Machine-local |
| **Cache / state** | `%LOCALAPPDATA%\NAVIG\` | Active host, tunnels, staging |
| **Project overrides** | `.navig\` in repo root | Per-project host/app configs |

Locate your config file from PowerShell:
```powershell
notepad "$env:APPDATA\NAVIG\config.yaml"
```

### Linux / macOS

| Data type | Path |
|-----------|------|
| **Config** | `~/.navig/config.yaml` |
| **Hosts / apps** | `~/.navig/hosts/` |
| **Vault** | `~/.navig/vault/` |
| **Logs** | `~/.local/share/navig/logs/` or `~/.navig/logs/` |
| **Project overrides** | `.navig/` in repo root |

Locate your config file:
```bash
cat ~/.navig/config.yaml
```

> ⚠️ On Linux/macOS `~/.navig` may be a symlink to `~/.local/share/navig` depending
> on your install method. `navig config show` always prints the resolved path.

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
