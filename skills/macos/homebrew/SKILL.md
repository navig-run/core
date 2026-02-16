---
name: homebrew
description: Manage Homebrew packages on macOS - install, update, search, cleanup
user-invocable: true
os: [darwin]
navig-commands:
  - navig run "brew list"
  - navig run "brew install {package}"
  - navig run "brew update && brew upgrade"
  - navig run "brew search {query}"
  - navig run "brew info {package}"
examples:
  - "Install htop with brew"
  - "Update all brew packages"
  - "What brew packages are installed?"
  - "Search for a package"
  - "Clean up old brew versions"
---

# Homebrew Package Management (macOS)

Manage Homebrew packages on macOS — local machine or remote Mac servers.

## How to Use

- **Local Mac**: Run `brew` commands directly
- **Remote Mac server**: Use `navig run "brew ..."`

## Common Tasks

### Update Homebrew & Upgrade All

**User says:** "Update my brew packages"

```bash
brew update && brew upgrade
```

**Response format:**
```
🍺 Homebrew Update:

Updated Homebrew core tap
Upgraded 5 packages:
• node 20.11 → 21.5
• python 3.11.7 → 3.12.1
• git 2.43 → 2.44
• wget 1.21 → 1.24
• openssl 3.2.0 → 3.2.1

✅ Everything up to date!
```

### Install a Package

```bash
brew install {package}
```

**Response:**
```
🍺 Installed {package} ✅

Version: x.y.z
Binaries: {package} → /opt/homebrew/bin/{package}
```

### Install a GUI App (Cask)

```bash
brew install --cask {app}
```

Common casks: `visual-studio-code`, `docker`, `iterm2`, `firefox`, `discord`

### Search for Packages

```bash
brew search {query}
```

### Show Package Info

```bash
brew info {package}
```

### List Installed Packages

```bash
brew list --formula    # CLI tools
brew list --cask       # GUI apps
```

**Response format:**
```
🍺 Installed Packages:

CLI Tools (32):
  git, node, python, wget, curl, htop, tmux, jq, gh, ...

GUI Apps (8):
  Docker, iTerm2, Visual Studio Code, Firefox, ...
```

### Remove a Package

```bash
brew uninstall {package}
```

### Clean Up Old Versions

```bash
brew cleanup --prune=7
```

**Response:**
```
🧹 Cleanup:

Removed 12 old versions
Freed: 1.2GB disk space

✅ Homebrew is clean!
```

### Check for Issues

```bash
brew doctor
```

## Homebrew Services

Manage background services (databases, servers) via Homebrew:

### List Services

```bash
brew services list
```

**Response format:**
```
🍺 Homebrew Services:

✅ postgresql@16 - running (pid: 1234)
✅ redis - running (pid: 5678)
⏹️ mysql - stopped
⏹️ nginx - stopped
```

### Start/Stop/Restart Service

```bash
brew services start {service}
brew services stop {service}
brew services restart {service}
```

## Common Packages for Dev

| Package | Description | Install |
|---------|-------------|---------|
| `git` | Version control | `brew install git` |
| `node` | Node.js runtime | `brew install node` |
| `python` | Python 3 | `brew install python` |
| `gh` | GitHub CLI | `brew install gh` |
| `jq` | JSON processor | `brew install jq` |
| `htop` | Process viewer | `brew install htop` |
| `tmux` | Terminal multiplexer | `brew install tmux` |
| `wget` | File downloader | `brew install wget` |
| `postgresql@16` | PostgreSQL database | `brew install postgresql@16` |
| `redis` | Redis cache | `brew install redis` |

## Proactive Suggestions

- **Outdated packages**: "📦 8 packages can be upgraded. Run `brew upgrade`?"
- **Large cleanup**: "🧹 2.5GB of old versions can be cleaned. Run `brew cleanup`?"
- **Doctor warnings**: "⚠️ `brew doctor` found issues. Want me to show them?"

## Error Handling

- **Brew not installed**: "Install Homebrew: `/bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"`"
- **Package not found**: "Package '{name}' not found. Try `brew search {name}`"
- **Permission issues**: "Permission denied. Don't use sudo with brew. Fix ownership: `sudo chown -R $(whoami) /opt/homebrew`"


