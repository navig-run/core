```skill
---
name: defender-exclusion-manage
description: Add, remove, and list Windows Defender exclusions (paths and processes) via PowerShell
user-invocable: true
navig-commands:
  - navig sys defender exclude path --path {path}
  - navig sys defender exclude process --name {process.exe}
  - navig sys defender exclude remove --path {path}
  - navig sys defender exclude list
requires:
  - Windows Defender (built-in)
  - Admin rights for add/remove
os: [windows]
examples:
  - "Exclude C:\\USB from Defender scanning"
  - "Stop Defender from scanning my dev folder"
  - "Add an exclusion for python.exe"
  - "List all Defender exclusions"
  - "Remove the USB exclusion"
---

# Windows Defender Exclusion Manager

Manage Windows Defender real-time protection exclusions to prevent false positives, unlock files held by MsMpEng, and reduce scan overhead on known-safe paths.

## Prerequisites

- **Admin required** for `path`, `process`, and `remove` — read-only `list` runs without admin
- Windows only — uses PowerShell `*-MpPreference` cmdlets

## Common Tasks

### Exclude the USB drive (fixes MsMpEng file locks)

**User says:** "Defender keeps locking files on my USB"

```bash
navig sys defender exclude path --path "C:\USB"
```

This releases file locks held by `MsMpEng.exe` and prevents future scanning of USB tools.

### Exclude a development folder

```bash
navig sys defender exclude path --path "C:\dev"
```

### Exclude a process by name

```bash
navig sys defender exclude process --name "python.exe"
```

### List all current exclusions

```bash
navig sys defender exclude list
```

Returns separate arrays for `ExclusionPath`, `ExclusionProcess`, `ExclusionExtension`.

### Remove an exclusion

```bash
navig sys defender exclude remove --path "C:\USB"
```

### Dry-run preview

```bash
navig sys defender exclude path --path "C:\USB" --dry-run
```

## Safety Notes

- Never exclude system folders (C:\Windows, C:\System32)
- Don't exclude user profile root (C:\Users\{name}) — too broad
- Exclusions persist until explicitly removed — use `list` to audit periodically
- `--dry-run` shows the PowerShell command without executing it
```
