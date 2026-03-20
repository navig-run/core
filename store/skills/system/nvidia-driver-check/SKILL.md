```skill
---
name: nvidia-driver-check
description: Check if a newer NVIDIA driver is available using TinyNvidiaUpdateChecker
user-invocable: true
navig-commands:
  - navig sys nvidia check
requires:
  - TinyNvidiaUpdateChecker.exe (from C:\USB\system\nvidia-updater\TinyNvidiaUpdateChecker.exe)
  - Windows + NVIDIA GPU
os: [windows]
examples:
  - "Is there a new NVIDIA driver?"
  - "Check my GPU driver version"
  - "Should I update my drivers?"
---

# NVIDIA Driver Update Check

Silently check whether a newer NVIDIA driver is available without opening GeForce Experience.

## Prerequisites

- TinyNvidiaUpdateChecker from USB at `C:\USB\system\nvidia-updater\TinyNvidiaUpdateChecker.exe`
- NVIDIA GPU must be installed
- Internet access required (checks NVIDIA's API)

## Common Tasks

### Check for driver updates

**User says:** "Do I need to update my NVIDIA drivers?"

```bash
navig sys nvidia check
```

**Response (up to date):**
```json
{
  "ok": true,
  "data": {
    "current_version": "556.12",
    "latest_version": "556.12",
    "update_available": false
  }
}
```

**Response (update available):**
```json
{
  "ok": true,
  "data": {
    "current_version": "551.23",
    "latest_version": "556.12",
    "update_available": true,
    "download_url": "https://..."
  }
}
```

## Notes

- Read-only operation — never downloads or installs automatically
- Lightweight alternative to GeForce Experience for driver version checks
```
