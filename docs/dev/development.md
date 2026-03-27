# NAVIG Development Guide

This guide covers setting up your development environment, running tests, and contributing to NAVIG.

## Table of Contents
- [Quick Setup](#quick-setup)
- [Modern Package Managers](#modern-package-managers)
- [Development Installation](#development-installation)
- [Running Tests](#running-tests)
- [Performance Optimization](#performance-optimization)
- [Code Style](#code-style)

## Quick Setup

### Traditional (pip)
```bash
git clone https://github.com/navig-run/core.git
cd core
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/macOS
pip install -e ".[dev]"
```

### Fast Setup with uv (Recommended)
```bash
pip install uv
uv pip install -e ".[dev]"
```

## Modern Package Managers

NAVIG supports modern Python tooling for faster development workflows.

`requirements.lock` is the single committed lockfile for Python dependency sync.
Use `uv` as the installer, but do not add a separate `uv.lock` for this repo.

### Option 1: uv (Fastest pip replacement)

[uv](https://github.com/astral-sh/uv) is a Rust-based package installer that's 10-100x faster than pip.

#### Installation
```bash
# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Or via pip
pip install uv
```

#### Usage
```bash
# Create virtual environment
uv venv

# Install dependencies (10-100x faster than pip)
uv pip install -r requirements.txt

# Install dev dependencies
uv pip install -e ".[dev]"

# Add a new dependency
uv pip install some-package

# Sync with lock file
uv pip sync requirements.lock
```

#### Benchmark (typical)
| Command | pip | uv |
|---------|-----|-----|
| Fresh install | 45s | 2s |
| Cached install | 8s | 0.3s |

### Option 2: rye (Full Project Manager)

[rye](https://rye-up.com/) is an all-in-one Python project manager that handles Python versions, virtual environments, dependencies, and builds.

#### Installation
```bash
# Windows (PowerShell)
irm https://rye-up.com/get | iex

# Linux/macOS
curl -sSf https://rye-up.com/get | bash
```

#### Usage
```bash
# Initialize (first time only)
rye init

# Sync dependencies
rye sync

# Add a dependency
rye add requests

# Add a dev dependency
rye add --dev pytest

# Run commands in project environment
rye run navig --help
rye run pytest

# Build distribution
rye build
```

#### rye.toml Configuration
If using rye, it reads from `pyproject.toml`. No additional config needed.

### Option 3: Traditional pip

For compatibility with existing workflows:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install -e ".[dev]"
```

## Development Installation

### Editable Install (Development Mode)
```bash
# With uv (fast)
uv pip install -e ".[dev]"

# With pip (traditional)
pip install -e ".[dev]"
```

This installs NAVIG in development mode, so changes to source files take effect immediately without reinstalling.

### Verify Installation
```bash
navig --version
navig --help
```

## Running Tests

### Run All Tests
```bash
pytest
```

### Run Specific Test File
```bash
pytest tests/test_config.py
```

### Run with Coverage
```bash
pytest --cov=navig --cov-report=html
# Open htmlcov/index.html in browser
```

### Run with Verbose Output
```bash
pytest -v --tb=long
```

## Performance Optimization

### Measuring Startup Time
```bash
# Quick measurement
python scripts/build.py --measure-startup

# Detailed import timing
python -X importtime -c "import navig.cli" 2>&1 | head -30
```

### Lazy Loading Guidelines

Heavy dependencies should be lazy-loaded to keep `navig --help` fast (<100ms).

**Pattern 1: Module-level lazy import**
```python
from navig.lazy_loader import lazy_import

# Don't import directly - use lazy_import
ch = lazy_import("navig.console_helper")

def my_command():
    ch.success("This loads console_helper only when called")
```

**Pattern 2: Singleton getter functions**
```python
_config_manager = None

def _get_config_manager():
    global _config_manager
    if _config_manager is None:
        from navig.config import get_config_manager
        _config_manager = get_config_manager()
    return _config_manager
```

**Pattern 3: Lazy class loading**
```python
from navig.lazy_loader import lazy_class

# Class not imported until instantiated
TunnelManager = lazy_class('navig.tunnel', 'TunnelManager')

def start_tunnel():
    manager = TunnelManager()  # Now navig.tunnel is imported
```

### Bytecode Compilation

For faster imports during development:
```bash
# Pre-compile all modules
python scripts/build.py --compile-bytecode

# Or manually
python -m compileall navig/
```

## Code Style

### Formatting (Black)
```bash
black navig/ tests/
```

### Linting (Flake8)
```bash
flake8 navig/ tests/
```

### Type Checking (MyPy)
```bash
mypy navig/
```

### Pre-commit Checks
```bash
# Run all checks
black navig/ tests/ && flake8 navig/ && pytest
```

## Project Structure

```
navig/
├── cli.py              # Main CLI commands (Typer)
├── main.py             # Entry point with fast-path handling
├── config.py           # Configuration management
├── lazy_loader.py      # Lazy import utilities
├── console_helper.py   # Rich terminal formatting
├── commands/           # Command implementations
│   ├── host.py
│   ├── app.py
│   ├── database.py
│   └── ...
├── plugins/            # Plugin system
│   ├── base.py
│   └── hello/
└── modules/            # Supporting modules
```

## CI/CD Integration

### Using uv in GitHub Actions
```yaml
- name: Install uv
  run: pip install uv

- name: Install dependencies
  run: uv pip install -r requirements.txt

- name: Run tests
  run: pytest
```

### Using rye in GitHub Actions
```yaml
- name: Install rye
  run: curl -sSf https://rye-up.com/get | bash

- name: Sync dependencies
  run: rye sync

- name: Run tests
  run: rye run pytest
```

## Troubleshooting

### Import Errors After Changes
```bash
# Clear bytecode cache
find . -type d -name __pycache__ -exec rm -rf {} +

# Reinstall in dev mode
pip install -e .
```

### Slow Startup
Check for accidental direct imports of heavy modules:
```bash
python -X importtime -c "import navig.cli" 2>&1 | grep -E "(paramiko|rich|requests)"
```

### Windows-Specific Issues
- Use PowerShell or Windows Terminal (not cmd.exe)
- Some Unicode symbols may not render in legacy consoles
- Use `python -m navig` if `navig` command isn't found
