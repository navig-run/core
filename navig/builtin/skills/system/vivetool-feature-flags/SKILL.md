```skill
---
name: vivetool-feature-flags
description: Enable, disable, or query Windows 11 feature experiments using ViVeTool
user-invocable: true
navig-commands:
  - navig sys vivetool enable --ids {id1,id2}
  - navig sys vivetool disable --ids {id1,id2}
  - navig sys vivetool query --id {id}
requires:
  - ViVeTool.exe (from C:\USB\system\vivetool\ViVeTool.exe)
  - Admin rights
os: [windows]
examples:
  - "Enable Windows feature 40729001"
  - "Disable the new file explorer flag"
  - "Check if feature 35057793 is enabled"
  - "Turn on all the new Explorer features"
---

# ViVeTool Windows Feature Flags

Manage Windows 11 A/B feature experiments using ViVeTool.

## Prerequisites

- **Admin required**
- ViVeTool binary from USB at `C:\USB\system\vivetool\ViVeTool.exe`
- Windows 11 only (safe to run on Windows 10 but most IDs are W11-specific)

## Common Tasks

### Enable a feature

**User says:** "Enable the new Windows 11 taskbar"

```bash
navig sys vivetool enable --ids 35057793
```

Multiple IDs (comma-separated):

```bash
navig sys vivetool enable --ids 35057793,40729001,44774629
```

### Disable a feature

```bash
navig sys vivetool disable --ids 40729001
```

### Query feature state

```bash
navig sys vivetool query --id 35057793
```

Returns enabled/disabled status and current payload value.

## Safety Notes

- `--dry-run` shows the ViVeTool command without executing
- Changes take effect after a **reboot** or Explorer restart
- Always note which feature IDs you change so you can roll back
- Feature IDs vary by Windows build — verify IDs from community wikis
```
