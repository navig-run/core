"""
Cross-Platform Automation Workflow Documentation
"""

# Cross-Platform Automation

NAVIG now supports cross-platform desktop automation across Windows, macOS, and Linux.

## Platforms

### Windows
- **Backend**: AutoHotkey v2
- **Installation**: `navig ahk install`
- **CLI**: `navig ahk <command>`

### Linux
- **Backend**: xdotool, wmctrl, xclip
- **Installation**: `sudo apt install xdotool wmctrl xclip`
- **CLI**: `navig auto <command>`

### macOS
- **Backend**: AppleScript, osascript
- **Optional**: `brew install cliclick` (for advanced mouse control)
- **CLI**: `navig auto <command>`

## Common Commands

```bash
# Check automation status
navig auto status

# Click at coordinates
navig auto click 100 200

# Type text
navig auto type "Hello World"

# Open application
navig auto open "Calculator"          # macOS/Windows
navig auto open "gnome-calculator"    # Linux

# List windows
navig auto windows

# Snap window to screen half
navig auto snap "Calculator" left

# Clipboard operations
navig auto clipboard "Copy this"
navig auto clipboard  # Print current clipboard

# Get focused window
navig auto focus
```

## Workflow Engine

The WorkflowEngine automatically selects the correct adapter for your platform:

```yaml
name: cross_platform_demo
description: Works on Windows, macOS, and Linux
steps:
  - action: open_app
    args:
      target: "{{app_name}}"

  - action: wait
    args:
      seconds: 1

  - action: type
    args:
      text: "Hello from NAVIG!"

  - action: snap_window
    args:
      selector: "{{app_name}}"
      position: "right"
```

## Platform-Specific Overrides

Use the `platform` key to provide platform-specific implementations:

```yaml
name: platform_specific
steps:
  - action: open_app
    args:
      target: "default_app"
    platform:
      windows:
        args:
          target: "notepad.exe"
      linux:
        args:
          target: "gedit"
      darwin:
        args:
          target: "TextEdit"
```

## Available Actions

All actions work across platforms (with adapter-specific implementations):

- `open_app` - Open application or URL
- `click` - Click at coordinates
- `type` - Type text
- `send` - Send key sequence
- `mouse_move` - Move mouse
- `activate_window` - Focus window by selector
- `close_window` - Close window
- `move_window` - Move/resize window
- `maximize_window` - Maximize window
- `minimize_window` - Minimize window
- `snap_window` - Snap to screen position
- `get_focused_window` - Get active window info
- `get_clipboard` - Get clipboard content
- `set_clipboard` - Set clipboard content
- `wait` - Sleep for duration
- `wait_for` - Wait for condition
- `run_command` - Execute shell command

## Conditional Execution

Use the `if` field with safe evaluation:

```yaml
steps:
  - action: get_focused_window
    capture: window_info

  - action: type
    args:
      text: "Window is focused!"
    if: window_info != None
```

## Variables

Capture step outputs and use them in later steps:

```yaml
steps:
  - action: get_clipboard
    capture: clipboard_content

  - action: type
    args:
      text: "You copied: {{clipboard_content}}"
```

## Creating Workflows

Use AI to generate workflows:

```bash
navig evolve workflow "Open calculator and type 1+1"
```

The WorkflowEvolver understands cross-platform constraints and will generate portable workflows.

## Running Workflows

```bash
# List available workflows
navig workflow list

# Run workflow
navig workflow run <name>

# Run with variables
navig workflow run <name> --var app_name=Calculator
```

## Agent Integration

The NAVIG agent can execute workflows directly:

```python
from navig.agent.hands import Hands

hands = Hands()
result = await hands.run_workflow("my_workflow", {"var1": "value1"})
```

## Tool Installation

### Linux
```bash
# Debian/Ubuntu
sudo apt install xdotool wmctrl xclip

# Arch
sudo pacman -S xdotool wmctrl xclip

# Fedora
sudo dnf install xdotool wmctrl xclip
```

### macOS
```bash
# Core functionality works out of the box

# Optional: Enhanced mouse control
brew install cliclick
```

### Windows
```bash
# Install AutoHotkey v2
navig ahk install
```

## Platform Differences

Some features are platform-specific:

- **read_text**: Windows only (AHK control text reading)
- **cliclick**: macOS optional (for precise mouse control)
- **window IDs**: Linux provides real IDs, macOS uses opaque references

The WorkflowEngine gracefully handles missing features by skipping or warning.
