# NAVIG Cross-Platform Evolution System

## Overview

NAVIG now features a complete cross-platform automation and evolution system that works seamlessly across **Windows**, **macOS**, and **Linux**. This system allows AI-powered generation of workflows, scripts, and automation tasks that adapt to the underlying operating system.

## Key Features

### 1. Cross-Platform Automation Adapters

**Three platform-specific adapters** with unified interface:

- **Windows** (`navig.adapters.automation.ahk`)
  - Backend: AutoHotkey v2
  - Features: Advanced window management, control reading, hotkeys
  - Status: `navig ahk status`

- **Linux** (`navig.adapters.automation.linux`)
  - Backend: xdotool, wmctrl, xclip
  - Features: X11 window management, clipboard, mouse/keyboard
  - Status: `navig auto status`

- **macOS** (`navig.adapters.automation.macos`)
  - Backend: AppleScript, osascript, cliclick (optional)
  - Features: Native app control, window management, clipboard
  - Status: `navig auto status`

### 2. Unified Workflow Engine

The `WorkflowEngine` (`navig/core/automation_engine.py`) provides:

- **Automatic Platform Detection**: Selects correct adapter at runtime
- **Platform Overrides**: Workflows can specify OS-specific implementations
- **Safe Evaluation**: Secure condition evaluation with `safe_eval` module
- **Variable Capture**: Store step outputs for use in later steps
- **Conditional Execution**: `if` conditions using captured variables

### 3. Evolution System

AI-powered code generation with validation loops:

| Evolver | Purpose | Output |
|---------|---------|--------|
| `SkillEvolver` | AI agent capabilities | `skills/*/SKILL.md` |
| `WorkflowEvolver` | Automation workflows | `workflows/*.yaml` |
| `PackEvolver` | Skill collections | `packs/*/pack.yaml` |
| `ScriptEvolver` | Python scripts | `scripts/*.py` |
| `FixEvolver` | Code repairs | In-place file edits |

### 4. Cross-Platform CLI Commands

**Unified Interface** (`navig auto`):
```bash
navig auto status          # Check automation availability
navig auto click 100 200   # Click at coordinates
navig auto type "text"     # Type text
navig auto open "app"      # Open application
navig auto windows         # List all windows
navig auto snap "app" left # Snap window to screen half
navig auto clipboard       # Get/set clipboard
navig auto focus           # Show focused window
```

**Windows-Specific** (`navig ahk`):
```bash
navig ahk install          # Install AutoHotkey v2
navig ahk doctor           # Diagnose issues
navig ahk clipboard "text" # Advanced clipboardops
navig ahk screenshot       # Take screenshots
navig ahk ocr              # OCR text extraction
navig ahk listen "^!t" cmd # Register global hotkey
```

**Script Management** (`navig script`):
```bash
navig script list          # List scripts
navig script run <name>    # Execute script
navig script edit <name>   # Open in editor
navig script new <name>    # Create from template
```

## Architecture

### Adapter Interface

All adapters implement a common interface:

```python
class BaseAdapter:
    def is_available() -> bool
    def open_app(target: str) -> ExecutionResult
    def click(x, y, button) -> ExecutionResult
    def type_text(text, delay) -> ExecutionResult
    def send_keys(keys) -> ExecutionResult
    def mouse_move(x, y, speed) -> ExecutionResult
    def get_focused_window() -> WindowInfo
    def activate_window(selector) -> ExecutionResult
    def close_window(selector) -> ExecutionResult
    def move_window(selector, x, y, w, h) -> ExecutionResult
    def maximize_window(selector) -> ExecutionResult
    def minimize_window(selector) -> ExecutionResult
    def snap_window(selector, position) -> ExecutionResult
    def get_clipboard() -> str
    def set_clipboard(text) -> ExecutionResult
    def get_all_windows() -> List[WindowInfo]
```

### Workflow YAML Schema

```yaml
name: workflow_name
description: "What this workflow does"
variables:
  var_name: "default_value"
steps:
  - action: action_name
    args:
      key: "value"
      another: "{{var_name}}"  # Variable interpolation
    capture: "result_var"      # Save output
    if: "condition"            # Conditional execution
    platform:                  # OS-specific overrides
      windows:
        args:
          key: "windows_value"
      linux:
        args:
          key: "linux_value"
      darwin:
        args:
          key: "macos_value"
```

### Safe Evaluation

The `safe_eval` module (`navig/core/safe_eval.py`) provides secure expression evaluation:

- Supports: arithmetic, comparison, boolean logic, variables
- Blocks: function calls, attribute access, imports
- Used in: workflow `if` conditions

Example:
```yaml
steps:
  - action: get_focused_window
    capture: window
  
  - action: type
    args:
      text: "Calculator is active!"
    if: window != None and 'Calculator' in window.get('title', '')
```

## Usage Examples

### Generate Cross-Platform Workflow

```bash
# AI creates workflow that works on all platforms
navig evolve workflow "Open calculator, type 2+2, snap to right half"

# Execute it
navig workflow run calculator_demo
```

### Generate Python Script

```bash
navig evolve script "Backup postgres database to S3 with compression"
navig script run backup_postgres_to_s3
```

### Fix Existing Code

```bash
navig evolve fix app.py "Fix the SQL injection vulnerability in login"
navig evolve fix --check "pytest tests/" server.py "Add input validation"
```

### Agent Workflow Execution

The NAVIG agent can execute workflows programmatically:

```python
from navig.agent.hands import Hands

hands = Hands()
result = await hands.run_workflow("setup_dev_environment", {
    "project_name": "my-app",
    "language": "python"
})

# Returns final variable state
print(result.stdout)  # JSON-serialized variables
```

## Platform-Specific Features

### Windows Only
- `read_text`: Extract text from window controls
- `ahk listen`: Global hotkey registration
- `ahk dashboard`: Real-time window monitoring

### Linux Specific
- Requires: `xdotool`, `wmctrl`, `xclip`
- Window IDs are actual X11 window identifiers
- Native multi-monitor support

### macOS Specific
- AppleScript for app control
- Optional `cliclick` for advanced mouse control
- Accessibility permissions required

## Installation

### Windows
```bash
# Install AutoHotkey v2
navig ahk install

# Or manually download from autohotkey.com
```

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
# Core automation works out of the box

# Optional: Enhanced mouse control
brew install cliclick
```

## File Structure

```
navig/
├── adapters/
│   └── automation/
│       ├── ahk.py      # Windows adapter (AutoHotkey v2)
│       ├── linux.py    # Linux adapter (xdotool/wmctrl)
│       └── macos.py    # macOS adapter (AppleScript)
├── commands/
│   ├── ahk.py          # Windows automation CLI
│   ├── auto.py         # Cross-platform automation CLI
│   ├── evolution.py    # Evolution commands
│   └── script.py       # Script management CLI
├── core/
│   ├── automation_engine.py   # Workflow execution engine
│   ├── safe_eval.py           # Secure expression evaluator
│   └── evolution/
│       ├── base.py       # Base evolver class
│       ├── workflow.py   # Workflow generator
│       ├── script.py     # Script generator
│       ├── fix.py        # Code repair
│       ├── skill.py      # Skill generator
│       └── pack.py       # Pack generator
├── scripts/              # Generated scripts
├── workflows/            # Generated workflows
└── docs/
    ├── automation.md     # Automation guide
    └── evolution.md      # Evolution system docs
```

## Security

- **Safe Evaluation**: No `eval()` or `exec()` in workflows
- **Sandboxed Expressions**: Only allow safe operations
- **No Function Calls**: Prevent arbitrary code execution
- **No Imports**: Block malicious module loading
- **Subprocess Validation**: Check commands before execution (FixEvolver)

## Future Enhancements

1. **Action Recorder**: Record user actions and generate workflows
2. **Visual Workflow Editor**: Web-based workflow designer
3. **Workflow Marketplace**: Share/download community workflows
4. **Advanced Conditions**: Complex logic with AND/OR/NOT
5. **Error Handling**: Try/catch blocks in workflows
6. **Loop Support**: Iterate over collections in workflows
7. **Remote Execution**: Run workflows on remote hosts via SSH
8. **Testing Framework**: Unit tests for workflows
9. **Performance Metrics**: Workflow execution timing and stats
10. **Multi-Platform Testing**: Validate workflows across OSes

## Contributing

To add a new platform adapter:

1. Create `navig/adapters/automation/<platform>.py`
2. Implement the adapter interface
3. Add platform detection in `WorkflowEngine.adapter` property
4. Update CLI commands to support new platform
5. Add documentation and examples

## Summary

NAVIG now provides a **complete cross-platform automation ecosystem**:

✅ Works on Windows, macOS, and Linux  
✅ AI-powered workflow generation  
✅ Safe condition evaluation  
✅ Variable capture and reuse  
✅ Platform-specific overrides  
✅ Unified CLI interface  
✅ Agent integration  
✅ Evolution system for self-improvement  

This enables developers to create once, run anywhere automation workflows with AI assistance.


