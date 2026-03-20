```skill
---
name: procmon-capture
description: Capture Windows process activity (file, registry, network events) to a PML file using Sysinternals Process Monitor
user-invocable: true
navig-commands:
  - navig sys procmon capture --output {file.pml} --duration {seconds}
  - navig sys procmon stop
requires:
  - Procmon64.exe (from C:\USB\system\procmon\Procmon64.exe)
  - Admin rights recommended for full capture
os: [windows]
examples:
  - "Capture process activity for 30 seconds"
  - "Record what files my app touches"
  - "Log registry changes during installer"
  - "Stop any running Procmon"
---

# Process Monitor Capture

Silently capture file, registry, process, and network events with Sysinternals Process Monitor.

## Prerequisites

- Procmon64.exe from USB at `C:\USB\system\procmon\Procmon64.exe`
- Running as admin gives full system-wide visibility
- PML output files can be opened in Process Monitor GUI for analysis

## Common Tasks

### Capture 30 seconds of activity

**User says:** "Record what happens when I run the installer"

```bash
navig sys procmon capture --output C:\temp\install_trace.pml --duration 30
```

### Capture with filter config

```bash
navig sys procmon capture --output C:\temp\trace.pml --filter C:\USB\system\procmon\myfilter.pmc --duration 60
```

### Stop any running Procmon

```bash
navig sys procmon stop
```

### Dry-run (preview command)

```bash
navig sys procmon capture --output trace.pml --duration 10 --dry-run
```

## Output Format

```json
{
  "ok": true,
  "data": {
    "output_path": "C:\\temp\\install_trace.pml",
    "duration_sec": 30,
    "size_bytes": 2048000
  }
}
```

## Safety Notes

- Capture automatically terminates after `--duration` seconds (default: 15)
- PML files can be large for long captures — set duration conservatively
- Use `--filter` with a PMC file to reduce noise and file size
```
