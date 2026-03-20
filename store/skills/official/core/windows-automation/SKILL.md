---
name: windows-automation
description: Best-in-class Windows automation using Python (UI Automation) and AutoHotkey.
metadata:
  navig:
    emoji: 🪟
    requires:
      bins: [python, ahk, powershell]
      libs: [pyautogui, pillow]
---

# Windows Automation Skill

The ultimate toolkit for controlling Windows. Combines robust UI inspection (reading text/buttons directly) with global hotkeys (AHK) and vision (screenshots).

## ⚡ Quick Reference

| Task | Command | Precision |
| :--- | :--- | :--- |
| **Click Button** | `python scripts/click_text.py "Save"` | High (No coords needed) |
| **Read Text** | `python scripts/read_window.py "Chrome"` | High (Direct API) |
| **Global Media** | `navig ahk send "{Media_Play_Pause}"` | 100% |
| **Type Text** | `python scripts/type_text.py "Hello"` | High |
| **Screenshot** | `python scripts/screenshot.py` | Visual only |

## 1. UI Automation (Python)
*Uses Windows Accessibility API - Fast & Accurate*

### Windows & Dialogs
```bash
# List all windows
python scripts/list_windows.py

# Handle a popup dialog
python scripts/handle_dialog.py dismiss

# Read all UI elements (buttons, links) in a window
python scripts/read_ui_elements.py "Chrome" --json
```

### Interaction
```bash
# Click by text name (Best method!)
python scripts/click_text.py "Submit"

# Focus specific window
python scripts/focus_window.py "Code"
```

## 2. Global Control (AutoHotkey)
*System-wide overrides via NAVIG's AHK integration*

```bash
# Multimedia Keys
navig ahk send "{Volume_Mute}"
navig ahk send "{Media_Next}"

# Window Management
navig ahk activate "Visual Studio Code"
navig ahk maximize "A"  # Active window
```

## 3. Legacy / Visual (PyAutoGUI)
*Fallback methods using coordinates*

```bash
# Click at specific pixels
python scripts/click.py 500 300

# Screenshot to stdout (Base64)
python scripts/screenshot.py > capture.b64
```

## 📂 Scripts Reference
All scripts are located in `scripts/`.

- `click_text.py`: Finds and clicks text elements.
- `read_window.py`: dumps window text content.
- `handle_dialog.py`: Interacts with standard Windows dialogs.
- `type_text.py`: Simulates keyboard input.

## Best Practices
1. **Try `click_text` first**: It works even if the window is moved or resized.
2. **Use `read_window`**: It sees text that screenshots might miss (or requires OCR).
3. **AHK for Global**: Use AHK commands for things that don't depend on specific UI elements (volume, media, window states).



