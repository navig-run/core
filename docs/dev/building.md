# Building NAVIG Distributions

This guide explains how to create optimized single-binary distributions of NAVIG for different platforms.

## Prerequisites

### Required
- Python 3.8 or higher
- All NAVIG dependencies installed: `pip install -r requirements.txt`

### For PyInstaller builds
```bash
pip install pyinstaller
```

### For Nuitka builds (produces smaller/faster binaries)
```bash
pip install nuitka
# On Windows, you may also need:
# - Visual Studio Build Tools (or MinGW)
# - OrderedSet: pip install ordered-set
```

## Quick Start

### Build with PyInstaller (recommended for quick builds)
```bash
python scripts/build.py --tool pyinstaller
```

### Build with Nuitka (recommended for production)
```bash
python scripts/build.py --tool nuitka
```

### Compare both tools
```bash
python scripts/build.py --compare
```

## Build Commands

| Command | Description |
|---------|-------------|
| `--tool pyinstaller` | Build with PyInstaller (faster build, larger binary) |
| `--tool nuitka` | Build with Nuitka (slower build, smaller/faster binary) |
| `--compare` | Build with both and show comparison |
| `--compile-bytecode` | Pre-compile all .py files to .pyc |
| `--measure-startup` | Analyze startup performance |
| `--onedir` | Create directory distribution instead of single file |

## Output

Binaries are placed in the `dist/` directory with versioned names:
- Windows: `navig-X.Y.Z-windows-x64.exe`
- Linux: `navig-X.Y.Z-linux-x64`
- macOS: `navig-X.Y.Z-darwin-arm64`

## Performance Targets

| Metric | Target | Notes |
|--------|--------|-------|
| Startup time (`--help`) | <100ms | Binary should start faster than Python |
| Binary size (PyInstaller) | <50MB | Single file, all dependencies bundled |
| Binary size (Nuitka) | <30MB | Optimized, often 30-50% smaller |

## Optimizing Startup Time

NAVIG uses lazy loading to defer heavy imports. To analyze import performance:

```bash
# Measure current startup time
python scripts/build.py --measure-startup

# Detailed import timing
python -X importtime -c "import navig.cli" 2>&1 | head -50
```

### Key lazy-loaded modules
- `paramiko` - SSH library, loaded on tunnel/remote commands
- `rich.*` - Terminal UI, loaded via `console_helper`
- `requests` - HTTP client, loaded for AI/API calls
- `navig.ai` - AI assistant, loaded on `navig ai` commands

## Pre-compiling Bytecode

For faster imports during development:

```bash
# Standard compilation
python scripts/build.py --compile-bytecode

# Or manually:
python -m compileall navig/
python -O -m compileall navig/   # Remove asserts
python -OO -m compileall navig/  # Remove docstrings too
```

## Troubleshooting

### PyInstaller: Missing modules
If commands fail with import errors, add hidden imports:

1. Edit `scripts/build.py`
2. Add the missing module to `get_hidden_imports()`
3. Rebuild

### Nuitka: Build failures on Windows
Ensure Visual Studio Build Tools are installed:
```powershell
winget install Microsoft.VisualStudio.2022.BuildTools
# Or download from: https://visualstudio.microsoft.com/downloads/
```

### Binary size too large
Try Nuitka with additional optimization:
```bash
python -m nuitka --onefile --lto=yes --enable-plugin=anti-bloat navig/main.py
```

### Startup still slow
Check which modules are loaded at import time:
```bash
python -X importtime -c "import navig.cli" 2>&1 | sort -t'|' -k2 -rn | head -20
```

## Cross-Platform Builds

### Building for Linux on Windows (WSL)
```bash
wsl python scripts/build.py --tool pyinstaller
```

### GitHub Actions Matrix Build
See `.github/workflows/build.yml` for automated multi-platform builds.

## Verifying the Build

After building, test the binary:

```bash
# Basic functionality
./dist/navig-* --help
./dist/navig-* --version

# Test lazy-loaded modules
./dist/navig-* host list
./dist/navig-* tunnel show

# Test SSH operations (requires configured host)
./dist/navig-* run "echo test"
```

## Size Comparison (Typical)

| Tool | Windows | Linux | macOS |
|------|---------|-------|-------|
| PyInstaller | ~45MB | ~40MB | ~42MB |
| Nuitka | ~25MB | ~22MB | ~24MB |

Nuitka typically produces 30-50% smaller binaries with faster startup.
