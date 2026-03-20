---
type: workflow
id: win-perf-apply-all
version: "0.1.0"
os: windows
---

# Workflow: win-perf-apply-all

**ID:** `win-perf-apply-all`  
**Version:** `0.1.0`  
**OS:** Windows only  
**Tool:** `win_perf_tuner`  
**Skill:** `win-perf-tuner`  
**Admin required:** Yes (kernel + gpu targets)

---

## Purpose

Applies all five Windows performance tunings in the correct order, validates each
step, and emits a final consolidated JSON envelope. Designed for a fresh machine
setup or post-reinstall hardening pass on NEURON-class workstations.

Tunings applied (in order):

| # | Target | What changes | Reversible |
|---|--------|--------------|------------|
| 1 | `power` | Activates Ultimate Performance power plan | ✅ |
| 2 | `kernel` | DisablePagingExecutive=1, NTFS memoryusage=2 | ✅ |
| 3 | `gpu` | HAGS enabled (HwSchMode=2) | ✅ |
| 4 | `fx` | MinAnimate=0, mask 0x82, transparency ON, Explorer restart | ✅ |

---

## Preconditions

- Windows 10 22H2 or Windows 11
- Elevated (admin) session — required for targets `kernel` and `gpu`
- No critical applications open (Explorer will restart during `fx` step)
- `win_perf_tuner` tool registered in NAVIG (`scripts/win_perf_tuner/manifest.json`)

---

## Steps (DAG / ordered)

```
[pre-scan] ──► [apply: power] ──► [apply: kernel] ──► [apply: gpu] ──► [apply: fx] ──► [verify-scan]
     │                                   │                  │
     │ abort if already_optimal=all      │ abort if !admin  │ warn: reboot needed
     ▼                                   ▼                  ▼
  [skip + report already_optimal]   [exit code 3]     [set pending_reboot=true]
```

### Step 1 — Pre-scan

```bash
navig sys perf scan
```

- Captures current state for each target.
- If **all four** targets report `optimal: true` → skip steps 2–5, jump to report.
- Stores scan result as `pre_scan`.

### Step 2 — Apply power plan

```bash
navig sys perf apply --target power
```

- No admin required.
- Sets Ultimate Performance power plan active.
- On failure: log warning, continue (non-critical).

### Step 3 — Apply kernel tweaks

```bash
navig sys perf apply --target kernel --yes
```

- **Requires admin.** If not elevated, abort with exit code `3`.
- Sets `DisablePagingExecutive=1` and `fsutil memoryusage=2`.
- Sets `pending_reboot=true` (DisablePagingExecutive takes effect after reboot).

### Step 4 — Apply GPU (HAGS)

```bash
navig sys perf apply --target gpu --yes
```

- **Requires admin.**
- Sets `HwSchMode=2` in registry.
- Sets `pending_reboot=true`.
- On failure: log warning, continue.

### Step 5 — Apply visual effects

```bash
navig sys perf apply --target fx --yes
```

- No admin required.
- Sets custom FX mask, disables minimize animation, restarts Explorer.
- Explorer restart is immediate — brief desktop flicker is expected.

### Step 6 — Verify scan

```bash
navig sys perf scan
```

- Captures post-apply state as `post_scan`.
- Compares `pre_scan` vs `post_scan` for each target.
- Any target still `optimal: false` after apply → emit as `warnings`.

---

## Rollback / Abort Behavior

| Condition | Action |
|-----------|--------|
| Pre-scan shows all optimal | Skip all apply steps, emit `already_optimal: true` |
| Admin check fails (step 3 or 4) | Abort with exit code `3`; steps 1–2 remain applied |
| Individual target apply fails | Log to `errors[]`, continue remaining steps |
| Verify scan shows regression | Emit in `warnings[]`, do NOT auto-revert |

Manual rollback:

```bash
navig sys perf revert              # revert all targets to Windows defaults
navig sys perf revert --target fx  # revert only one target
```

---

## Final JSON Envelope

```json
{
  "ok": true,
  "tool": "win_perf_tuner",
  "workflow": "win-perf-apply-all",
  "ts": "<ISO8601>",
  "data": {
    "pre_scan":  { "power": {}, "kernel": {}, "gpu": {}, "fx": {} },
    "post_scan": { "power": {}, "kernel": {}, "gpu": {}, "fx": {} },
    "already_optimal": false,
    "pending_reboot": true,
    "steps": [
      { "step": "apply:power",  "ok": true,  "ms": 120 },
      { "step": "apply:kernel", "ok": true,  "ms": 340 },
      { "step": "apply:gpu",    "ok": true,  "ms": 210 },
      { "step": "apply:fx",     "ok": true,  "ms": 890 }
    ]
  },
  "warnings": [],
  "errors": [],
  "metrics": { "ms": 1560, "backend": "worker" }
}
```

**Exit codes:**

| Code | Meaning |
|------|---------|
| `0` | All steps succeeded |
| `3` | Admin required — not elevated |
| `4` | Partial success — some steps failed, see `errors[]` |

---

## Post-Apply Checklist (manual)

These cannot be automated from OS level — must be done manually:

1. **NVIDIA Control Panel** → Manage 3D Settings → Power management mode → `Prefer maximum performance`
2. **NVIDIA Control Panel** → Shader Cache Size → `Unlimited`
3. **BIOS** → Enable XMP / DOCP → DDR4-3200 (biggest single remaining gain, +15–25% RAM bandwidth)
4. **BIOS** → Move 980 PRO to M.2_2 slot → restores PCIe ×16 to GPU
5. **BIOS** → Enable Resizable BAR
6. **Reboot** → required for `DisablePagingExecutive` + `HAGS` (HwSchMode) to take effect

---

## CLI Invocation

```bash
# Full apply (requires admin shell)
navig sys perf apply --yes

# Dry-run preview (no changes made)
navig sys perf apply --dry-run

# Apply only safe (non-admin) targets
navig sys perf apply --target power
navig sys perf apply --target fx

# Check current state
navig sys perf scan
navig sys perf status
```

---

## Skills Used

- [`win-perf-tuner`](../skills/system/win-perf-tuner/skill.md) — all commands

---

## Heartbeat / Scheduling

Not a recurring heartbeat workflow. Run on:
- Fresh Windows install / post-reinstall
- After major Windows updates (may reset power plan or FX settings)
- Manually on demand: `navig sys perf scan` to audit drift

See `docs/heartbeat.md` for system health thresholds.
