# Skill: win-perf-tuner

**id**: `win-perf-tuner`
**version**: `1.0.0`
**os**: `windows`
**tool_id**: `win_perf_tuner`
**cli**: `navig sys perf`

---

## Purpose

Audit and apply a curated set of Windows performance tweaks that reduce UI latency, build/dev tool slowness, and kernel overhead — without changing the visual appearance of the desktop (Aero, transparency, and animations are preserved).

## Scope

| Target | What it changes | Admin? | Reboot? |
|--------|-----------------|--------|---------|
| `power` | Switches to **Ultimate Performance** power plan (no CPU C-state dips) | No | No |
| `kernel` | NTFS metadata cache → max (`memoryusage=2`) + kernel pinned in RAM (`DisablePagingExecutive=1`) | Yes | Paging exec only |
| `gpu` | Hardware Accelerated GPU Scheduling enabled (`HwSchMode=2`) | Yes | Yes |
| `fx` | Visual FX custom: removes 3 invisible effect drags, keeps all Aero glass | No | No |

## Permissions

- `power`, `fx` — user-level, no UAC
- `kernel`, `gpu` — require **Administrator**
- All targets support `--dry-run` with no system changes

## Commands

### Audit
```
navig sys perf scan
navig sys perf status
```

`scan` returns per-target `optimal: true/false` with recommendations.
`status` returns a single summary block, machine-readable.

### Apply
```
navig sys perf apply                       # all 4 targets
navig sys perf apply --target power        # just power plan
navig sys perf apply --target kernel       # just NTFS + paging exec
navig sys perf apply --target gpu          # just HAGS
navig sys perf apply --target fx           # just visual effects
navig sys perf apply --dry-run             # preview all without changes
```

### Revert
```
navig sys perf revert                      # restore all to Windows defaults
navig sys perf revert --target fx          # restore only visual effects
```

## Expected Output (apply --all)

```json
{
  "ok": true,
  "tool": "win_perf_tuner",
  "command": "apply",
  "data": {
    "results": {
      "power":  { "ok": true, "plan": "Ultimate Performance" },
      "kernel": { "ok": true, "ntfs_cache": { "ok": true }, "paging_executive": { "ok": true } },
      "gpu":    { "ok": true, "hags": { "set": 2 } },
      "fx":     { "ok": true, "actions": ["..."] }
    },
    "notes": [
      "Reboot required for: DisablePagingExecutive, HAGS (HwSchMode)",
      "Manual step: NVIDIA Control Panel → Power management mode = Prefer maximum performance"
    ]
  }
}
```

## Failure Modes

| Error | Cause | Resolution |
|-------|-------|------------|
| `Admin required` | `kernel` or `gpu` target from non-elevated shell | Re-run from elevated PowerShell |
| `fsutil: access denied` | Not admin | Elevate |
| `Unknown target` | Typo in `--target` | Use: `all`, `power`, `kernel`, `gpu`, `fx` |
| `powercfg not found` | Non-standard Windows | Ensure `powercfg.exe` is in PATH |

## Safety Rules

- `--dry-run` always safe — no registry or filesystem changes
- `revert` restores: power → High Performance, kernel → defaults, gpu → HAGS off, fx → Windows auto
- Explorer is restarted after `fx` apply/revert (your taskbar will flash for ~1 second)
- Paging executive and HAGS changes only fully activate after a reboot; interim is safe

## Manual Step (not automatable)

NVIDIA Control Panel settings are not writable via registry without driver restart risk:
> **NVIDIA Control Panel → Manage 3D Settings → Global Settings**:
> - Power management mode → **Prefer maximum performance**
> - Shader Cache Size → **Unlimited**
