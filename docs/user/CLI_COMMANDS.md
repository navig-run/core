# New CLI Commands - Cross-Platform Automation

This document lists all new CLI commands added for cross-platform automation.

## `navig auto` - Cross-Platform Automation

Available on: **Windows**, **macOS**, **Linux**

### Commands

#### `navig auto status`
Check automation system availability and show installed tools.

**Example:**
```bash
navig auto status
```

**Output:**
```
✓ Automation ready on linux
  xdotool: ✓
  wmctrl: ✓
  xclip: ✓
```

---

#### `navig auto click <x> <y>`
Click at screen coordinates.

**Arguments:**
- `x` - X coordinate (required)
- `y` - Y coordinate (required)

**Options:**
- `--button, -b` - Mouse button: left, right, middle (default: left)

**Example:**
```bash
navig auto click 500 300
navig auto click 100 200 --button right
```

---

#### `navig auto type <text>`
Type text at current cursor position.

**Arguments:**
- `text` - Text to type (required)

**Options:**
- `--delay, -d` - Delay between keystrokes in ms (default: 50)

**Example:**
```bash
navig auto type "Hello World"
navig auto type "Slow typing" --delay 100
```

---

#### `navig auto open <target>`
Open application or URL.

**Arguments:**
- `target` - Application name, path, or URL (required)

**Example:**
```bash
navig auto open "Calculator"
navig auto open "https://google.com"
navig auto open "/usr/bin/code"
```

---

#### `navig auto windows`
List all visible windows with details.

**Example:**
```bash
navig auto windows
```

**Output:**
```
┌──────────┬────────────────┬───────────┬──────────┬──────────┐
│ ID       │ Title          │ Process   │ Position │ Size     │
├──────────┼────────────────┼───────────┼──────────┼──────────┤
│ 4a3b2c1  │ VS Code        │ code      │ 0,0      │ 1920x1080│
│ 5d6e7f8  │ Chrome         │ chrome    │ 100,100  │ 1024x768 │
└──────────┴────────────────┴───────────┴──────────┴──────────┘
```

---

#### `navig auto snap <selector> <position>`
Snap window to screen position.

**Arguments:**
- `selector` - Window title or app name (required)
- `position` - Position: left, right, top, bottom (required)

**Example:**
```bash
navig auto snap "VS Code" left
navig auto snap "Chrome" right
```

---

#### `navig auto clipboard [text]`
Get or set clipboard content.

**Arguments:**
- `text` - Text to copy (optional, if omitted prints current clipboard)

**Example:**
```bash
navig auto clipboard "Copy this text"
navig auto clipboard  # Print current clipboard
```

---

#### `navig auto focus`
Show currently focused window information.

**Example:**
```bash
navig auto focus
```

**Output:**
```
Focused window:
  Title: VS Code
  Process: code
  Position: (0, 0)
  Size: 1920x1080
```

---

## `navig script` - Script Management

Available on: **All platforms**

### Commands

#### `navig script list`
List all generated scripts.

**Example:**
```bash
navig script list
```

**Output:**
```
┌─────────────────────────┬──────────────────────────────┐
│ Name                    │ Path                         │
├─────────────────────────┼──────────────────────────────┤
│ backup_db               │ /home/user/.navig/scripts/..│
│ download_stocks         │ /home/user/.navig/scripts/..│
└─────────────────────────┴──────────────────────────────┘
```

---

#### `navig script run <name> [args...]`
Execute a script.

**Arguments:**
- `name` - Script name without extension (required)
- `args` - Additional arguments to pass to script (optional)

**Example:**
```bash
navig script run backup_db
navig script run process_data --input file.csv
```

---

#### `navig script edit <name>`
Open script in default editor.

**Arguments:**
- `name` - Script name (required)

**Example:**
```bash
navig script edit backup_db
```

---

#### `navig script new <name>`
Create a new script from template.

**Arguments:**
- `name` - Script name (required)

**Options:**
- `--template, -t` - Template to use: basic, automation (default: basic)

**Example:**
```bash
navig script new my_script
navig script new automation_task --template automation
```

---

## Enhanced `navig evolve` Commands

Available on: **All platforms**

### New/Updated Commands

#### `navig evolve script <goal>`
Generate a Python script using AI.

**Arguments:**
- `goal` - Description of what the script should do (required)

**Options:**
- `--retries, -n` - Max evolution attempts (default: 3)

**Example:**
```bash
navig evolve script "Backup MySQL database to S3"
navig evolve script "Download stock prices from Yahoo Finance" --retries 5
```

---

#### `navig evolve fix <file> <instruction>`
Fix or improve existing code file.

**Arguments:**
- `file` - Path to file to fix (required)
- `instruction` - Description of bug or improvement (required)

**Options:**
- `--check, -c` - Command to run for validation, use {file} as placeholder (optional)

**Example:**
```bash
navig evolve fix app.py "Fix SQL injection vulnerability"
navig evolve fix --check "pytest tests/" server.py "Add input validation"
navig evolve fix --check "python -m py_compile {file}" script.py "Fix syntax errors"
```

---

## Enhanced Windows-Only Commands

Available on: **Windows only**

### New `navig ahk` Commands

#### `navig ahk clipboard [text]`
Get or set clipboard content (Windows-optimized).

**Arguments:**
- `text` - Text to copy (optional)

**Example:**
```bash
navig ahk clipboard "Windows clipboard"
navig ahk clipboard
```

---

#### `navig ahk screenshot`
Take a screenshot.

**Options:**
- `--output, -o` - Output file path (optional)
- `--region` - Region as x,y,w,h (optional)

**Example:**
```bash
navig ahk screenshot --output screen.png
navig ahk screenshot --region "100,100,800,600"
```

---

#### `navig ahk ocr`
Extract text from screen or image using OCR.

**Options:**
- `--image` - Image file path (default: screenshot)
- `--region` - Region as x,y,w,h (optional)

**Requirements:** pytesseract, Pillow

**Example:**
```bash
navig ahk ocr --image document.png
navig ahk ocr --region "0,0,500,500"
```

---

#### `navig ahk listen <hotkey> <command>`
Register global hotkey to run a command.

**Arguments:**
- `hotkey` - AHK hotkey syntax (e.g., ^!t for Ctrl+Alt+T) (required)
- `command` - Command to run when hotkey pressed (required)

**Options:**
- `--start, -s` - Start listener immediately (optional)

**Example:**
```bash
navig ahk listen "^!n" "notepad.exe"
navig ahk listen "^!t" "wt.exe" --start
```

---

#### `navig ahk listener-start`
Start or restart the persistent hotkey listener.

**Example:**
```bash
navig ahk listener-start
```

---

#### `navig ahk listener-edit`
Open listener script in default editor.

**Example:**
```bash
navig ahk listener-edit
```

---

## Command Comparison Matrix

| Feature | `navig auto` | `navig ahk` | Notes |
|---------|--------------|-------------|-------|
| Cross-platform | ✓ | ✗ | ahk is Windows-only |
| Window management | ✓ | ✓ | Both support |
| Clipboard | ✓ | ✓ | ahk has extra features |
| Screenshots | ✗ | ✓ | Windows-only |
| OCR | ✗ | ✓ | Requires pytesseract |
| Hotkeys | ✗ | ✓ | Windows-only |
| Mouse/keyboard | ✓ | ✓ | Both support |

## Platform-Specific Features

### All Platforms (navig auto)
- Window management
- Mouse/keyboard automation
- Clipboard operations
- Application launching

### Windows Only (navig ahk)
- Screenshots
- OCR text extraction
- Global hotkeys
- Control text reading
- Advanced window manipulation

### Linux Specific
- X11 window IDs
- xdotool for precise control
- wmctrl for window management

### macOS Specific
- AppleScript integration
- Native app control
- Optional cliclick for mouse

## Usage Tips

1. **Use `navig auto` for cross-platform workflows**
2. **Use `navig ahk` for Windows-specific advanced features**
3. **Generate automation with AI**: `navig evolve workflow "your task"`
4. **Test before deploying**: Run workflows in test environment first
5. **Capture variables**: Chain actions together with `capture`

## Getting Help

```bash
navig auto --help
navig ahk --help
navig script --help
navig evolve --help
```

## Documentation References

- Full automation guide: `docs/automation.md`
- Quick reference: `docs/QUICK_REFERENCE.md`
- Architecture: `docs/ARCHITECTURE.md`
- Evolution system: `docs/evolution.md`
