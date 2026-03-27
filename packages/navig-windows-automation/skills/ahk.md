---
name: "windows-automation"
description: "Control Windows UI: Click, Type, Open Apps, Manage Windows."
user-invocable: true
version: "1.0.0"
category: "automation"
risk-level: "moderate"
os: ["windows"]
tags: ["gui", "desktop", "ahk", "windows"]

navig-commands:
  - name: "click"
    syntax: "navig windows click <x> <y> [button] [clicks]"
    description: "Click mouse at coordinates."
    parameters:
      x: { type: "integer", description: "X coordinate", required: true }
      y: { type: "integer", description: "Y coordinate", required: true }
      button: { type: "string", description: "Mouse button", default: "left" }
      clicks: { type: "integer", description: "Click count", default: 1 }

  - name: "type"
    syntax: "navig windows type <text>"
    description: "Type text simulation."
    parameters:
        text: { type: "string", description: "Text to type", required: true }

  - name: "open-app"
    syntax: "navig windows open <target>"
    description: "Open an application or file."
    parameters:
        target: { type: "string", required: true, description: "Path or app name (e.g. notepad)" }

  - name: "window-list"
    syntax: "navig windows list"
    description: "List all visible windows."

  - name: "window-close"
    syntax: "navig windows close <title>"
    description: "Close a window by title."
    risk: "moderate"

examples:
  - user: "Open notepad"
    thought: "User wants to launch an app."
    command: "navig windows open notepad"

  - user: "Click close button at 1900, 10"
    thought: "User specified coordinates."
    command: "navig windows click 1900 10"

  - user: "Type 'Hello World'"
    thought: "User wants to send keystrokes."
    command: "navig windows type 'Hello World'"
---

# Windows Automation (AHK)

Powered by AutoHotkey v2. Allows low-level UI interaction.

## Prerequisites
- Windows OS
- AutoHotkey v2 installed (Plugin will attempt detection)
