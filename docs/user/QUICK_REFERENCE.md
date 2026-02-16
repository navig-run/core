# NAVIG Cross-Platform Quick Reference

## Installation

### Windows
```bash
navig ahk install
```

### Linux
```bash
sudo apt install xdotool wmctrl xclip
```

### macOS
```bash
brew install cliclick  # Optional
```

## Check Status

```bash
navig auto status
```

## Common Commands

### Automation

```bash
# Click at coordinates
navig auto click 100 200

# Type text
navig auto type "Hello World"

# Open app
navig auto open "Calculator"

# List windows
navig auto windows

# Snap window
navig auto snap "VS Code" left

# Clipboard
navig auto clipboard "Copy this"
navig auto clipboard  # Print

# Get focused window
navig auto focus
```

### AI Generation

```bash
# Generate workflow
navig evolve workflow "Open notepad and type hello"

# Generate script
navig evolve script "Backup database to S3"

# Fix code
navig evolve fix app.py "Fix bug in login function"
navig evolve fix --check "pytest tests/" server.py "Add validation"

# Generate skill
navig evolve skill "Monitor CPU usage and alert if high"

# Generate pack
navig evolve pack "DevOps tools bundle"
```

### Workflows

```bash
# List workflows
navig workflow list

# Run workflow
navig workflow run <name>

# Run with variables
navig workflow run <name> --var key=value
```

### Scripts

```bash
# List scripts
navig script list

# Run script
navig script run <name>

# Edit script
navig script edit <name>

# Create new script
navig script new <name> --template automation
```

## Workflow YAML Structure

```yaml
name: my_workflow
description: "What it does"
variables:
  var1: "default_value"
steps:
  - action: open_app
    args:
      target: "{{var1}}"
    capture: result
    if: "condition"
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

| Action | Description | Args |
|--------|-------------|------|
| `open_app` | Open application | `target` |
| `click` | Click coordinates | `x`, `y`, `button` |
| `type` | Type text | `text`, `delay` |
| `send` | Send keys | `keys` |
| `mouse_move` | Move mouse | `x`, `y`, `speed` |
| `activate_window` | Focus window | `selector` |
| `close_window` | Close window | `selector` |
| `move_window` | Move/resize | `selector`, `x`, `y`, `width`, `height` |
| `maximize_window` | Maximize | `selector` |
| `minimize_window` | Minimize | `selector` |
| `snap_window` | Snap to position | `selector`, `position` |
| `get_focused_window` | Get active window | - |
| `get_clipboard` | Get clipboard | - |
| `set_clipboard` | Set clipboard | `text` |
| `wait` | Sleep | `seconds` |
| `wait_for` | Wait for condition | `type`, `target`, `timeout` |
| `run_command` | Execute shell | `command` |

## Condition Syntax

Variables are accessed directly (no `{{}}`):

```yaml
if: "status == 'ready'"
if: "count > 5"
if: "window != None"
if: "text in 'Calculator'"
if: "count > 10 and status == 'ready'"
```

## Variable Interpolation

In args, use `{{varname}}`:

```yaml
args:
  text: "Hello {{username}}"
  target: "{{app_name}}"
```

## Platform Detection

The engine auto-detects:
- `windows` → AHKAdapter
- `linux` → LinuxAdapter
- `darwin` → MacOSAdapter

## Security

✅ Safe evaluation (no eval/exec)  
✅ Whitelisted operations  
✅ No function calls  
✅ No imports  
✅ Sandboxed scope  

## Examples

### Example 1: Screenshot & OCR (Windows)
```bash
navig ahk screenshot --output screenshot.png
navig ahk ocr --image screenshot.png
```

### Example 2: Window Management
```bash
# Snap all Chrome windows to left
navig auto snap "Chrome" left
```

### Example 3: Clipboard Automation
```yaml
name: clipboard_demo
steps:
  - action: get_clipboard
    capture: original
  
  - action: set_clipboard
    args:
      text: "Processed: {{original}}"
```

### Example 4: Conditional Execution
```yaml
name: conditional_demo
steps:
  - action: get_focused_window
    capture: window
  
  - action: type
    args:
      text: "VS Code is active!"
    if: "'Code' in window.get('title', '')"
```

### Example 5: Cross-Platform App Launch
```yaml
name: open_editor
variables:
  editor: "code"
steps:
  - action: open_app
    args:
      target: "{{editor}}"
    platform:
      windows:
        args:
          target: "code.exe"
      linux:
        args:
          target: "/usr/bin/code"
      darwin:
        args:
          target: "Visual Studio Code"
```

## Debugging

### Check adapter status
```bash
navig auto status
```

### Test workflow
```bash
navig workflow run cross_platform_test
```

### View workflow output
Captured variables are returned as JSON after execution.

## Help

```bash
navig --help
navig auto --help
navig evolve --help
navig workflow --help
navig script --help
```

## Documentation

- `docs/automation.md` - Full automation guide
- `docs/evolution.md` - Evolution system
- `docs/ARCHITECTURE.md` - System architecture
- `docs/CROSS_PLATFORM_EVOLUTION.md` - Complete overview

## Common Issues

### Linux: "Automation not available"
```bash
sudo apt install xdotool wmctrl xclip
```

### macOS: Mouse click not working
```bash
brew install cliclick
```

### Windows: AHK not found
```bash
navig ahk install
```

### Workflow syntax error
Check YAML syntax with:
```bash
navig workflow test <name>
```

## Tips

1. **Test on your platform first** with `navig auto status`
2. **Use platform overrides** for OS-specific cases
3. **Capture variables** to chain actions
4. **Use conditions** for flexible workflows
5. **Generate with AI** using `navig evolve workflow`
6. **Start simple** and iterate

## Next Steps

1. Generate your first workflow:
   ```bash
   navig evolve workflow "Your automation idea"
   ```

2. Test it:
   ```bash
   navig workflow run <generated_name>
   ```

3. Edit if needed:
   ```bash
   # Workflows are saved in workflows/
   code workflows/<name>.yaml
   ```

4. Share it with your team! 🚀


