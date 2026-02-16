# 🚀 NAVIG AutoHotkey Integration - Quick Start Guide

## Installation Verification

```bash
# 1. Check if AutoHotkey v2 is detected
navig ahk status

# Expected output:
# ✓ Available
# Version: 2.0.19
# Executable: C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe
```

## 5-Minute Tutorial

### 1. Basic Clipboard Operations ✅

```bash
# Copy text to clipboard
navig ahk clipboard "Hello from NAVIG!"

# Read clipboard
navig ahk clipboard
# Output: Hello from NAVIG!
```

### 2. Window Management ✅

```bash
# Snap your browser to the left half of screen
navig ahk snap "Chrome" left

# Pin a window to stay always on top
navig ahk pin "Notepad"

# Open live window dashboard
navig ahk dashboard
# Press Ctrl+C to exit
```

### 3. Save & Restore Layouts ✅

```bash
# Arrange your windows however you like, then:
navig ahk layout save my_workspace

# Later, restore it:
navig ahk layout restore my_workspace

# List all saved layouts
navig ahk layout list
```

### 4. Screenshot & OCR ✅

```bash
# Take a screenshot
navig ahk screenshot --output test.png

# Capture specific region
navig ahk screenshot --region 0,0,800,600

# Extract text from screen (requires pytesseract)
navig ahk ocr --region 0,0,500,500
```

### 5. Global Hotkeys ✅

```bash
# Register Ctrl+Alt+T to show AHK status
navig ahk listen "^!t" "navig ahk status" --start

# The listener is now running in background
# Press Ctrl+Alt+T to trigger it

# Manage listener
navig ahk listener-start  # Start/restart
navig ahk listener-edit   # Edit hotkeys manually
```

### 6. AI-Powered Automation 🤖 (Requires API Key)

First, configure your AI provider:

```bash
# Edit config
notepad ~/.navig/config.yaml

# Add:
openrouter:
  api_key: "sk-or-v1-..."
```

Then:

```bash
# Generate a script (single attempt)
navig ahk generate "open notepad and type hello world" --dry-run

# Evolve a script (auto-refines if it fails)
navig ahk evolve "minimize all windows except Chrome"

# Save successful scripts to library
navig ahk generate "take screenshot" --save

# Next time, the script runs instantly from cache!
```

## Agent Integration Examples

### Python API

```python
from navig.agent.hands import Hands
from navig.agent.config import HandsConfig

# Create hands component
hands = Hands(HandsConfig())

# Use in async context
async def automate_workspace():
    # AI generates and runs automation
    result = await hands.evolve_ahk("arrange VSCode and Chrome side-by-side")
    
    if result.success:
        # Save the layout for future use
        await hands.save_window_layout("dev_mode")
    
    # Register a hotkey
    await hands.register_global_hotkey(
        hotkey="^!r",  # Ctrl+Alt+R
        command="navig remote exec prod restart_service"
    )
```

### Brain Prompt Integration

The NAVIG agent now understands Windows automation:

```
User: "Arrange my windows for coding"

Agent Brain:
- Recognizes Windows automation request
- Calls hands.evolve_ahk("arrange windows for coding")
- AI generates AHK script
- Script snaps VSCode right, Chrome left
- Success! Saves to library for next time
```

## Troubleshooting

### Common Issues

**1. Clipboard command fails**
```bash
# Solution: Already fixed in latest version
# Update: git pull
```

**2. AHK not detected**
```bash
navig ahk install
# Follow installation instructions
```

**3. AI commands fail**
```bash
# Set API key in ~/.navig/config.yaml
openrouter:
  api_key: "your-key"
```

**4. OCR not working**
```bash
# Install Tesseract
pip install pytesseract
# Download Tesseract binary from:
# https://github.com/UB-Mannheim/tesseract/wiki
```

## Advanced Usage

### Combine with Remote Operations

```bash
# Deploy to server, then update local UI
navig remote exec prod "deploy" && \
navig ahk evolve "show notification 'Deploy complete'"
```

### Workflow Integration

```yaml
# workflow.yaml
name: Development Setup
steps:
  - action: ahk
    command: restore_layout
    args: ["dev_mode"]
  
  - action: ahk
    command: snap
    args: ["VSCode", "right"]
  
  - action: run_command
    cmd: "code ~/projects/navig"
```

Run:
```bash
navig workflow run development_setup
```

### Custom Hotkey Setup

Edit `~/.navig/scripts/listener.ahk`:

```autohotkey
#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

; NAVIG AutoHotkey Listener

^!t::Run "navig ahk status"
^!s::Run "navig ahk screenshot"
^!d::Run "navig ahk dashboard"
^!1::Run "navig ahk layout restore coding"
^!2::Run "navig ahk layout restore meeting"
```

Start:
```bash
navig ahk listener-start
```

## Performance Tips

1. **Use the script library**: AI-generated scripts are cached automatically
2. **Dry-run first**: Test with `--dry-run` to verify scripts
3. **Save layouts**: Much faster than repositioning windows each time
4. **Background listener**: Register frequently-used hotkeys once

## Security Best Practices

1. **Review AI scripts**: Use `--dry-run` first
2. **Safe mode**: Enabled by default (30s timeout)
3. **Hotkey safety**: Test hotkeys before registering globally
4. **Limit scope**: Use specific window selectors, not wildcards

## What's Next?

Explore the full documentation:
- `docs/AHK_IMPLEMENTATION.md` - Complete technical reference
- `docs/AHK_COMPLETE_SUMMARY.md` - High-level overview

Try these advanced features:
```bash
navig ahk evolve "screenshot each window and save"
navig ahk evolve "organize windows by application type"
navig ahk generate "backup clipboard history to file"
```

---

**You're ready to revolutionize your Windows workflow!** 🎉

The NAVIG agent can now control your desktop, learn from your patterns, and automate repetitive tasks intelligently.

*Welcome to autonomous Windows automation.*
