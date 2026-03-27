```skill
---
name: screenshot-capture
description: Capture screenshots of the full screen, a specific monitor, or a region
user-invocable: true
navig-commands:
  - navig sys screenshot take
  - navig sys screenshot take --output {path.png}
  - navig sys screenshot take --monitor {index}
  - navig sys screenshot take --region {x,y,w,h}
  - navig sys screenshot monitors
requires:
  - Windows: Pillow or mss (pip install mss pillow)
  - macOS: built-in screencapture
  - Linux: scrot, gnome-screenshot, or imagemagick import
os: [windows, linux, mac]
examples:
  - "Take a screenshot"
  - "Capture my second monitor"
  - "Screenshot the top-left 800x600 region"
  - "What monitors do I have?"
  - "Save a screenshot to C:\\temp\\snap.png"
---

# Screenshot Capture

Take screenshots of the full desktop, individual monitors, or specific regions. Works across Windows, macOS, and Linux.

## Prerequisites

**Windows:** `pip install mss pillow` (auto-selected in order: mss → PIL → fallback)
**macOS:** Uses system `screencapture -x` — no extra deps
**Linux:** Uses first available: `scrot`, `gnome-screenshot`, `import` (ImageMagick)

## Common Tasks

### Full-screen capture

**User says:** "Take a screenshot"

```bash
navig sys screenshot take
```

Saves to a timestamped file in the current directory.

### Save to specific path

```bash
navig sys screenshot take --output C:\temp\snap.png
```

### Capture second monitor only

```bash
navig sys screenshot take --monitor 2
```

Use `monitors` to list available monitor indices first.

### List monitors

```bash
navig sys screenshot monitors
```

Returns array of `{index, width, height, x, y, is_primary}`.

### Capture a region

```bash
navig sys screenshot take --region 0,0,800,600
```

Format: `x,y,width,height` in screen pixels.

## Output Format

```json
{
  "ok": true,
  "data": {
    "path": "C:\\temp\\snap.png",
    "width": 1920,
    "height": 1080,
    "size_bytes": 245000
  }
}
```

## Safety Notes

- `--dry-run` returns what would be captured (monitor/region info) without saving
- Output directory must be writable — tool checks before capture
```
