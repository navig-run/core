"""
win_perf_tuner worker — Windows performance tuning: power plan, NTFS cache,
kernel paging, GPU HAGS scheduling, and Visual FX.

CLI paths:
    navig sys perf scan
    navig sys perf apply  [--target all|power|kernel|gpu|fx] [--dry-run]
    navig sys perf revert [--target all|power|kernel|gpu|fx] [--dry-run]
    navig sys perf status
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import Timer, current_os, emit, err, is_admin, ok, run  # noqa: E402

TOOL = "win_perf_tuner"
PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]

# ── Power plan GUIDs ──────────────────────────────────────────────────────────
GUID_ULTIMATE = "635f4cac-d42a-47ac-81da-263977fd1f30"
GUID_HIGH = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
GUID_BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
PLAN_NAMES = {
    GUID_ULTIMATE: "Ultimate Performance",
    GUID_HIGH: "High Performance",
    GUID_BALANCED: "Balanced",
}

# ── Registry paths ────────────────────────────────────────────────────────────
REG_MEM_MGMT = (
    r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
)
REG_GPU_SCHED = r"HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers"
REG_VFX = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects"
REG_DESKTOP = r"HKCU\Control Panel\Desktop"
REG_PERSONALIZE = r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
REG_EXPLORER = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _check_os() -> str | None:
    if current_os() != "windows":
        return "win_perf_tuner is Windows-only."
    return None


def _ps(cmd: str, dry: bool = False) -> tuple[int, str, str]:
    return run([*PS, cmd], timeout=15, dry_run=dry)


def _reg_query_dword(key: str, value: str) -> int | None:
    """Return integer value of a REG_DWORD, or None on miss."""
    try:
        rc, out, _ = run(["reg", "query", key, "/v", value], timeout=8)
        if rc != 0:
            return None
        for line in out.splitlines():
            line = line.strip()
            if value.lower() in line.lower() and "REG_DWORD" in line:
                return int(line.split()[-1], 16)
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
    return None


def _reg_set_dword(
    key: str, value: str, data: int, dry: bool = False
) -> tuple[int, str]:
    cmd = ["reg", "add", key, "/v", value, "/t", "REG_DWORD", "/d", str(data), "/f"]
    if dry:
        return 0, f"[dry-run] reg add {key} /v {value} /d {data}"
    rc, out, er = run(cmd, timeout=10)
    return rc, er or out


def _active_power_plan() -> tuple[str, str]:
    """Return (guid, name) of active power plan."""
    try:
        rc, out, _ = run(["powercfg", "/getactivescheme"], timeout=8)
        if rc == 0:
            parts = out.strip().split()
            for i, p in enumerate(parts):
                if len(p) == 36 and p.count("-") == 4:
                    guid = p.lower()
                    name = PLAN_NAMES.get(guid, " ".join(parts[i + 1 :]).strip("()"))
                    return guid, name
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
    return "unknown", "Unknown"


def _restart_explorer(dry: bool = False) -> None:
    if dry:
        return
    try:
        subprocess.Popen(
            [
                *PS,
                "Stop-Process -Name explorer -Force; Start-Sleep 1; Start-Process explorer",
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical


# ─────────────────────────────────────────────────────────────────────────────
# Target: POWER
# ─────────────────────────────────────────────────────────────────────────────


def _scan_power() -> dict:
    guid, name = _active_power_plan()
    optimal = guid == GUID_ULTIMATE
    return {
        "target": "power",
        "current_plan": name,
        "current_guid": guid,
        "optimal": optimal,
        "recommendation": (
            None if optimal else f"Switch to Ultimate Performance ({GUID_ULTIMATE})"
        ),
    }


def _apply_power(dry: bool = False) -> dict:
    t = Timer()
    if dry:
        return ok(
            TOOL,
            "apply.power",
            {"dry_run": True, "action": f"powercfg /s {GUID_ULTIMATE}"},
            ms=t.ms(),
        )
    rc, out, er = run(["powercfg", "/s", GUID_ULTIMATE], timeout=8)
    if rc != 0:
        return err(TOOL, "apply.power", [er or out], ms=t.ms())
    _, name = _active_power_plan()
    return ok(TOOL, "apply.power", {"plan": name, "guid": GUID_ULTIMATE}, ms=t.ms())


def _revert_power(dry: bool = False) -> dict:
    t = Timer()
    if dry:
        return ok(
            TOOL,
            "revert.power",
            {"dry_run": True, "action": f"powercfg /s {GUID_HIGH}"},
            ms=t.ms(),
        )
    rc, out, er = run(["powercfg", "/s", GUID_HIGH], timeout=8)
    if rc != 0:
        # Fall back to Balanced if High Performance is missing
        run(["powercfg", "/s", GUID_BALANCED], timeout=8)
    _, name = _active_power_plan()
    return ok(TOOL, "revert.power", {"plan": name}, ms=t.ms())


# ─────────────────────────────────────────────────────────────────────────────
# Target: KERNEL (NTFS metadata cache + paging executive)
# ─────────────────────────────────────────────────────────────────────────────


def _get_ntfs_cache() -> int | None:
    try:
        rc, out, _ = run(["fsutil", "behavior", "query", "memoryusage"], timeout=8)
        if rc == 0:
            for part in out.split():
                if part.isdigit():
                    return int(part)
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
    return None


def _scan_kernel() -> dict:
    ntfs = _get_ntfs_cache()
    paging = _reg_query_dword(REG_MEM_MGMT, "DisablePagingExecutive")
    return {
        "target": "kernel",
        "ntfs_metadata_cache": {
            "current": ntfs,
            "optimal": 2,
            "ok": ntfs == 2,
            "description": "0=small default, 2=max (uses free RAM for NTFS metadata cache)",
        },
        "paging_executive": {
            "current": paging,
            "optimal": 1,
            "ok": paging == 1,
            "description": "0=kernel may page to disk, 1=kernel pinned in RAM (requires reboot)",
        },
        "optimal": ntfs == 2 and paging == 1,
        "reboot_required_for": [] if paging == 1 else ["DisablePagingExecutive"],
    }


def _apply_kernel(dry: bool = False) -> dict:
    t = Timer()
    if not dry and not is_admin():
        return err(
            TOOL,
            "apply.kernel",
            ["Admin required for NTFS and registry changes."],
            ms=t.ms(),
        )

    results: dict = {}

    # NTFS metadata cache
    if dry:
        results["ntfs_cache"] = {
            "dry_run": True,
            "action": "fsutil behavior set memoryusage 2",
        }
    else:
        rc, out, er = run(["fsutil", "behavior", "set", "memoryusage", "2"], timeout=10)
        results["ntfs_cache"] = {"ok": rc == 0, "output": er or out}

    # Paging executive
    rc2, msg2 = _reg_set_dword(REG_MEM_MGMT, "DisablePagingExecutive", 1, dry)
    results["paging_executive"] = {
        "dry_run": dry,
        "ok": dry or rc2 == 0,
        "note": "Takes effect after reboot.",
    }

    had_error = not dry and (
        not results["ntfs_cache"].get("ok", True)
        or not results["paging_executive"].get("ok", True)
    )
    if had_error:
        return err(
            TOOL,
            "apply.kernel",
            ["One or more kernel settings failed — see data."],
            ms=t.ms(),
            data=results,
        )
    return ok(TOOL, "apply.kernel", results, ms=t.ms())


def _revert_kernel(dry: bool = False) -> dict:
    t = Timer()
    if not dry and not is_admin():
        return err(TOOL, "revert.kernel", ["Admin required."], ms=t.ms())
    results = {}
    if dry:
        results["ntfs_cache"] = {
            "dry_run": True,
            "action": "fsutil behavior set memoryusage 0",
        }
        results["paging_executive"] = {
            "dry_run": True,
            "action": "reg DisablePagingExecutive=0",
        }
    else:
        rc, out, er = run(["fsutil", "behavior", "set", "memoryusage", "0"], timeout=10)
        results["ntfs_cache"] = {"ok": rc == 0}
        rc2, _ = _reg_set_dword(REG_MEM_MGMT, "DisablePagingExecutive", 0)
        results["paging_executive"] = {
            "ok": rc2 == 0,
            "note": "Takes effect after reboot.",
        }
    return ok(TOOL, "revert.kernel", results, ms=t.ms())


# ─────────────────────────────────────────────────────────────────────────────
# Target: GPU (Hardware Accelerated GPU Scheduling)
# ─────────────────────────────────────────────────────────────────────────────


def _scan_gpu() -> dict:
    hags = _reg_query_dword(REG_GPU_SCHED, "HwSchMode")
    return {
        "target": "gpu",
        "hags": {
            "current": hags,
            "optimal": 2,
            "ok": hags == 2,
            "description": "1=disabled, 2=enabled. Reduces GPU-CPU scheduling latency.",
        },
        "optimal": hags == 2,
        "reboot_required_for": [] if hags == 2 else ["HwSchMode (HAGS)"],
        "manual_step": (
            "NVIDIA Control Panel → Manage 3D Settings → "
            "Power management mode = 'Prefer maximum performance' + "
            "Shader Cache Size = 'Unlimited'"
        ),
    }


def _apply_gpu(dry: bool = False) -> dict:
    t = Timer()
    if not dry and not is_admin():
        return err(
            TOOL, "apply.gpu", ["Admin required for GPU registry changes."], ms=t.ms()
        )
    rc, msg = _reg_set_dword(REG_GPU_SCHED, "HwSchMode", 2, dry)
    if not dry and rc != 0:
        return err(TOOL, "apply.gpu", [msg], ms=t.ms())
    return ok(
        TOOL,
        "apply.gpu",
        {
            "hags": {"set": 2, "dry_run": dry, "note": "Reboot required."},
            "manual": (
                "Still needed: NVIDIA Control Panel → Manage 3D Settings → "
                "Power management mode → Prefer maximum performance"
            ),
        },
        ms=t.ms(),
    )


def _revert_gpu(dry: bool = False) -> dict:
    t = Timer()
    if not dry and not is_admin():
        return err(TOOL, "revert.gpu", ["Admin required."], ms=t.ms())
    rc, _ = _reg_set_dword(REG_GPU_SCHED, "HwSchMode", 1, dry)
    return ok(
        TOOL,
        "revert.gpu",
        {"hags": {"set": 1, "dry_run": dry, "note": "Reboot required."}},
        ms=t.ms(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Target: FX (Visual Effects — custom: keep Aero, kill invisible drags)
# ─────────────────────────────────────────────────────────────────────────────


def _get_vfx_state() -> dict:
    """Read current Visual FX registry state."""
    vfx_setting = _reg_query_dword(REG_VFX.replace("\\", "\\\\"), "VisualFXSetting")
    # Use reg query for MinAnimate since it's a string not dword
    try:
        rc, out, _ = run(["reg", "query", REG_DESKTOP, "/v", "MinAnimate"], timeout=8)
        min_animate = "0" if "0x0" in out or '"0"' in out else "1"
    except Exception:
        min_animate = None
    transparency = _reg_query_dword(REG_PERSONALIZE, "EnableTransparency")
    return {
        "visual_fx_setting": vfx_setting,  # 0=auto,1=best-look,2=best-perf,3=custom
        "min_animate": min_animate,  # 0=no anim, 1=animate
        "transparency": transparency,  # 1=on
    }


def _scan_fx() -> dict:
    state = _get_vfx_state()
    optimal = (
        state.get("visual_fx_setting") == 3  # custom
        and state.get("min_animate") == "0"  # no minimize animation
        and state.get("transparency") == 1  # Aero ON
    )
    return {
        "target": "fx",
        "current": state,
        "optimal": optimal,
        "recommendation": (
            None
            if optimal
            else "Set VisualFXSetting=3 (custom), MinAnimate=0, transparency=1. "
            "Removes invisible effect overhead, keeps full Aero look."
        ),
    }


def _apply_fx(dry: bool = False) -> dict:
    t = Timer()
    actions = []

    def _regset(key, name, val, reg_type="REG_DWORD"):
        if dry:
            actions.append(f"[dry] reg add {key} /v {name} /d {val}")
            return
        run(
            ["reg", "add", key, "/v", name, "/t", reg_type, "/d", str(val), "/f"],
            timeout=8,
        )
        actions.append(f"set {name}={val}")

    # Visual FX = custom (3)
    _regset(REG_VFX, "VisualFXSetting", 3)
    # MinAnimate = 0 (no minimize/maximize animation)
    _regset(REG_DESKTOP, "MinAnimate", 0, "REG_SZ")
    # Keep Aero transparency ON
    _regset(REG_PERSONALIZE, "EnableTransparency", 1)
    # Keep taskbar animations ON
    _regset(REG_EXPLORER, "TaskbarAnimations", 1)

    # Mask: clear MENUANIMATION(0x04), COMBOBOXANIMATION(0x08), LISTBOXSMOOTHSCROLLING(0x10)
    # Keep everything else (tooltips, shadows, fonts, thumbnails, etc.)
    if not dry:
        try:
            rc, out, _ = run(
                [
                    *PS,
                    "(Get-ItemProperty 'HKCU:\\Control Panel\\Desktop').UserPreferencesMask",
                ],
                timeout=8,
            )
            # The mask comes back as a byte array representation; safer to read via reg binary
            mask_script = (
                "$m=[byte[]](Get-ItemProperty 'HKCU:\\Control Panel\\Desktop').UserPreferencesMask;"
                "$m[0]=$m[0]-band(-bnot 0x1C);"
                "Set-ItemProperty 'HKCU:\\Control Panel\\Desktop' UserPreferencesMask $m"
            )
            run([*PS, mask_script], timeout=10)
            actions.append(
                "UserPreferencesMask: cleared MENUANIMATION|COMBOBOXANIMATION|LISTBOXSMOOTH"
            )
        except Exception as e:
            actions.append(f"mask update skipped: {e}")

    # Restart Explorer to apply without reboot
    _restart_explorer(dry)
    if not dry:
        actions.append("explorer restarted")

    return ok(
        TOOL,
        "apply.fx",
        {
            "actions": actions,
            "dry_run": dry,
            "kept_on": [
                "transparency",
                "Aero blur",
                "font smoothing",
                "taskbar animations",
                "window shadows",
                "thumbnails",
            ],
        },
        ms=t.ms(),
    )


def _revert_fx(dry: bool = False) -> dict:
    t = Timer()
    actions = []

    def _regset(key, name, val, reg_type="REG_DWORD"):
        if dry:
            actions.append(f"[dry] reg add {key} /v {name} /d {val}")
            return
        run(
            ["reg", "add", key, "/v", name, "/t", reg_type, "/d", str(val), "/f"],
            timeout=8,
        )

    # VisualFXSetting=0 → let Windows choose
    _regset(REG_VFX, "VisualFXSetting", 0)
    # MinAnimate back to 1
    _regset(REG_DESKTOP, "MinAnimate", 1, "REG_SZ")

    _restart_explorer(dry)
    if not dry:
        actions.append("explorer restarted")

    return ok(TOOL, "revert.fx", {"actions": actions, "dry_run": dry}, ms=t.ms())


# ─────────────────────────────────────────────────────────────────────────────
# Public command handlers
# ─────────────────────────────────────────────────────────────────────────────

TARGETS = ("power", "kernel", "gpu", "fx")


def cmd_scan(args: dict) -> dict:
    t = Timer()
    if e := _check_os():
        return err(TOOL, "scan", [e], ms=t.ms())
    report = {
        "power": _scan_power(),
        "kernel": _scan_kernel(),
        "gpu": _scan_gpu(),
        "fx": _scan_fx(),
    }
    all_optimal = all(v.get("optimal", False) for v in report.values())
    report["summary"] = {
        "all_optimal": all_optimal,
        "needs_attention": [
            k
            for k, v in report.items()
            if k != "summary" and not v.get("optimal", False)
        ],
    }
    result = ok(TOOL, "scan", report, ms=t.ms())
    save = args.get("save")
    if save:
        try:
            Path(save).write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["data"]["saved_to"] = save
        except Exception as e:
            result.setdefault("warnings", []).append(f"Could not save: {e}")
    return result


def cmd_status(args: dict) -> dict:
    t = Timer()
    if e := _check_os():
        return err(TOOL, "status", [e], ms=t.ms())
    power = _scan_power()
    kernel = _scan_kernel()
    gpu = _scan_gpu()
    fx = _scan_fx()
    lines = [
        f"power  : {'✓ Ultimate Performance' if power['optimal'] else '✗ ' + power['current_plan']}",
        f"kernel : ntfs={'2(max)' if kernel['ntfs_metadata_cache']['ok'] else '0(default)'}"
        f" | paging_exec={'pinned' if kernel['paging_executive']['ok'] else 'pageable'}",
        f"gpu    : hags={'ON' if gpu['hags']['ok'] else 'OFF'}",
        f"fx     : {'✓ custom (Aero kept)' if fx['optimal'] else '✗ default'}",
    ]
    return ok(
        TOOL,
        "status",
        {
            "lines": lines,
            "all_optimal": power["optimal"]
            and kernel["optimal"]
            and gpu["optimal"]
            and fx["optimal"],
        },
        ms=t.ms(),
    )


def cmd_apply(args: dict) -> dict:
    t = Timer()
    if e := _check_os():
        return err(TOOL, "apply", [e], ms=t.ms())
    target = (args.get("target") or "all").lower()
    dry = args.get("dry_run", False)
    results = {}

    targets_to_run = TARGETS if target == "all" else (target,)
    for bad in targets_to_run:
        if bad not in TARGETS:
            return err(
                TOOL,
                "apply",
                [f"Unknown target '{bad}'. Valid: {', '.join(TARGETS)}, all"],
            )

    admin_targets = {"kernel", "gpu"}
    needs_admin = any(t2 in admin_targets for t2 in targets_to_run)
    if needs_admin and not dry and not is_admin():
        return err(
            TOOL,
            "apply",
            [
                "Admin rights required for 'kernel' and 'gpu' targets. "
                "Re-run from an elevated terminal or use --dry-run."
            ],
            ms=t.ms(),
        )

    for tgt in targets_to_run:
        if tgt == "power":
            results["power"] = _apply_power(dry)
        elif tgt == "kernel":
            results["kernel"] = _apply_kernel(dry)
        elif tgt == "gpu":
            results["gpu"] = _apply_gpu(dry)
        elif tgt == "fx":
            results["fx"] = _apply_fx(dry)

    any_err = any(not v.get("ok") for v in results.values())
    notes = []
    if not dry:
        reboot_needed = []
        if "kernel" in results and results["kernel"].get("ok"):
            reboot_needed.append("DisablePagingExecutive")
        if "gpu" in results and results["gpu"].get("ok"):
            reboot_needed.append("HAGS (HwSchMode)")
        if reboot_needed:
            notes.append(f"Reboot required for: {', '.join(reboot_needed)}")
        if "gpu" in results:
            notes.append(
                "Manual step: NVIDIA Control Panel → Manage 3D Settings → "
                "Power management mode = Prefer maximum performance | "
                "Shader Cache Size = Unlimited"
            )

    envelope = err if any_err else ok
    return envelope(
        TOOL, "apply", {"results": results, "notes": notes, "dry_run": dry}, ms=t.ms()
    )


def cmd_revert(args: dict) -> dict:
    t = Timer()
    if e := _check_os():
        return err(TOOL, "revert", [e], ms=t.ms())
    target = (args.get("target") or "all").lower()
    dry = args.get("dry_run", False)
    results = {}
    targets_to_run = TARGETS if target == "all" else (target,)

    admin_targets = {"kernel", "gpu"}
    needs_admin = any(t2 in admin_targets for t2 in targets_to_run)
    if needs_admin and not dry and not is_admin():
        return err(
            TOOL, "revert", ["Admin required for kernel/gpu targets."], ms=t.ms()
        )

    for tgt in targets_to_run:
        if tgt == "power":
            results["power"] = _revert_power(dry)
        elif tgt == "kernel":
            results["kernel"] = _revert_kernel(dry)
        elif tgt == "gpu":
            results["gpu"] = _revert_gpu(dry)
        elif tgt == "fx":
            results["fx"] = _revert_fx(dry)

    any_err = any(not v.get("ok") for v in results.values())
    envelope = err if any_err else ok
    return envelope(TOOL, "revert", {"results": results, "dry_run": dry}, ms=t.ms())


# ─────────────────────────────────────────────────────────────────────────────
# Worker main (stdin/stdout JSON protocol)
# ─────────────────────────────────────────────────────────────────────────────

HANDLERS = {
    "scan": cmd_scan,
    "apply": cmd_apply,
    "revert": cmd_revert,
    "status": cmd_status,
}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            handler = HANDLERS.get(req.get("command", ""))
            emit(
                handler(req.get("args", {}))
                if handler
                else err(TOOL, req.get("command", "?"), ["Unknown command"])
            )
        except Exception as exc:
            emit(err(TOOL, "?", [str(exc)]))


if __name__ == "__main__":
    main()
