```skill
---
name: futurerestore-ios
description: Restore an iOS device to a specific firmware using a saved SHSH2 blob via futurerestore
user-invocable: true
navig-commands:
  - navig ios futurerestore restore --blob {shsh2} --ipsw {firmware.ipsw}
  - navig ios futurerestore restore --blob {shsh2} --ipsw {firmware.ipsw} --latest-sep --no-baseband --dry-run
requires:
  - futurerestore.exe (from C:\USB\ios\futurerestore\futurerestore.exe)
  - Valid .shsh2 blob for target iOS version and device
  - Target .ipsw firmware file
  - iTunes or Apple Mobile Device Support installed
os: [windows]
examples:
  - "Restore my iPhone to iOS 16.5 with my saved blob"
  - "Downgrade using shsh2 blob"
  - "Preview the restore command without running it"
---

# futurerestore iOS Restore

Restore an iOS device to any firmware version using a saved SHSH2 blob. Useful for downgrading or restoring to unsigned firmware.

## Prerequisites

- **SHSH2 blob** saved for the target iOS version + your specific device's ECID
- **IPSW firmware file** for the target iOS version
- iTunes or Apple Mobile Device Support installed on Windows
- Device must be in DFU or Recovery mode before running

## ⚠️ High Risk — Read Before Using

- This operation is **irreversible** — wrong blob or IPSW will brick the device
- Always use `--dry-run` first to verify the command
- For cellular iPhones, use `--latest-sep` (required) + appropriate baseband flag

## Common Tasks

### Restore with blob (WiFi-only device or no-baseband)

```bash
navig ios futurerestore restore --blob C:\blobs\iPhone_16.5.shsh2 --ipsw C:\firmware\iOS16.5.ipsw --no-baseband
```

### Restore cellular device (use latest SEP + baseband)

```bash
navig ios futurerestore restore --blob C:\blobs\iPhone_16.5.shsh2 --ipsw C:\firmware\iOS16.5.ipsw --latest-sep --latest-baseband
```

### Dry-run (preview command only)

```bash
navig ios futurerestore restore --blob C:\blobs\iPhone_16.5.shsh2 --ipsw C:\firmware\iOS16.5.ipsw --dry-run
```

## Timeouts

The operation is allowed up to 600 seconds (10 minutes). Large IPSW restores are slow — don't interrupt.

## Safety Notes

- `--dry-run` validates that blob and IPSW files exist, then prints the command without executing
- Exits with code `2` on any futurerestore error
- Do not unplug the device during restore
```
