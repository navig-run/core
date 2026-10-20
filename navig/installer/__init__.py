"""
navig.installer — idempotent profile-driven installer for NAVIG.

Public API
----------
run_install(profile, dry_run, quiet)
    Orchestrate a full install run: plan → apply → persist → report.

Profiles
--------
Legacy (modules only):
  node            minimal: config dirs + CLI verify + legacy migration
  operator        node + shell integration + vault bootstrap  ← default
  architect       operator + MCP pack
  system_standard operator + service daemon
  system_deep     system_standard + tray + persona assets

Public installation profiles (a manifest: modules + extras + daemon flags +
a one-time mode suggestion — see navig.installer.profiles):
  cli       config dirs + CLI verify only, never a service
  minimal   cli + shell PATH integration, never a service
  server    headless config stack + MCP, daemon with gateway + scheduler
  full      everything the default install runs today

Applying any profile writes ``install.profile`` into the global config; the
manifest layers above are applied once at that moment (never re-imposed).

Examples
--------
CLI (added to `navig init`)::

    navig init --profile operator
    navig init --profile server
    navig init --profile cli --dry-run
    navig init --profile system_standard --quiet

Python::

    from navig.installer import run_install
    run_install(profile="operator", dry_run=True)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from navig.installer import state as _state
from navig.installer.contracts import InstallerContext, ModuleState, Result
from navig.installer.planner import plan
from navig.installer.profiles import (
    DEFAULT_PROFILE,
    PROFILE_CONFIG_KEY,
    VALID_PROFILES,
    daemon_cli_args,
    missing_profile_extras,
    profile_extras_spec,
    validate_install_profile,
)
from navig.installer.runner import apply
from navig.platform.paths import config_dir as _default_config_dir


def run_install(
    profile: str = DEFAULT_PROFILE,
    dry_run: bool = False,
    quiet: bool = False,
    config_dir: Path | None = None,
    extra: dict | None = None,
) -> list[Result]:
    """Run the installer for *profile* and return Results.

    Parameters
    ----------
    profile:
        One of VALID_PROFILES.
    dry_run:
        Preview actions without making any changes.
    quiet:
        Suppress console output (useful in CI / script installs).
    config_dir:
        Override the default ~/.navig base directory.
    extra:
        Arbitrary extra data forwarded to every module.

    Returns
    -------
    List[Result]
        One entry per planned action (plus one per profile-setting step when
        a profile was applied).

    Raises
    ------
    ValueError
        If *profile* is unknown, or its manifest fails validation (the check
        runs BEFORE any module mutates anything).
    SystemExit
        If a critical module fails and quiet=False.
    """
    ctx = InstallerContext(
        profile=profile,
        dry_run=dry_run,
        quiet=quiet,
        config_dir=config_dir or _default_config_dir(),
        extra=extra or {},
    )

    actions = plan(ctx)
    # Manifest defects (unknown extra/module/daemon flag) abort here — before apply.
    manifest = validate_install_profile(profile)

    if not quiet:
        _print_plan(actions, profile, manifest, dry_run)

    results = apply(actions, ctx)

    # Persist only when something was applied (not pure dry-run)
    applied = [r for r in results if r.state == ModuleState.APPLIED]
    if applied and not dry_run:
        try:
            manifest_saved = _state.save(actions, results, ctx)
            if not quiet:
                from navig import console_helper as ch

                ch.dim(f"  manifest: {manifest_saved}")
        except Exception:  # noqa: BLE001
            pass  # non-fatal

    # Profile settings (install.profile / mode / daemon) — after the installer
    # manifest is saved so they never pollute the rollback history.
    if not dry_run:
        results.extend(_apply_profile_settings(profile, manifest, ctx))

    if not quiet:
        _print_results(results, dry_run)

    return results


# ─────────────── profile settings (config key · mode · daemon) ───────────────


def _apply_profile_settings(
    profile: str,
    manifest: dict | None,
    ctx: InstallerContext,
) -> list[Result]:
    """Apply the non-module half of a profile: config key, mode, service flags.

    Called exactly once per apply (never by boot, doctor or the gateway), which
    is what makes the mode suggestion one-time rather than re-imposed.

    Every step reports: an applied step is an APPLIED result, a deliberately
    skipped one is SKIPPED, and a failure is FAILED — never silence.
    """
    results: list[Result] = []

    # 1. install.profile — records what was applied, wherever the caller pointed.
    try:
        from navig.config import get_config_manager

        get_config_manager(config_dir=ctx.config_dir).set(PROFILE_CONFIG_KEY, profile)
        results.append(
            Result(
                action_id="profile.config",
                state=ModuleState.APPLIED,
                message=f"{PROFILE_CONFIG_KEY} = {profile}",
            )
        )
    except Exception as exc:  # noqa: BLE001 — a config write must not take the install down
        results.append(
            Result(
                action_id="profile.config",
                state=ModuleState.FAILED,
                message=f"could not write {PROFILE_CONFIG_KEY} = {profile}",
                error=str(exc),
            )
        )

    if manifest is None:
        return results  # legacy profile — modules only, exactly as before

    # 1b. One-time config defaults — written only where the key is unset, so a
    # value the operator chose (e.g. cloud.enabled: true on a server that SHOULD
    # be reachable) is never overridden by re-applying the profile.
    for key, value in (manifest.get("config") or {}).items():
        results.append(_apply_config_default(ctx.config_dir, key, value))

    # 2 & 3 act on the AMBIENT install: the modes manager resolves
    # paths.config_dir() itself, and `navig service install` registers a
    # platform-global service. Applying either while targeting a DIFFERENT
    # config dir than the one those resolve would write the operator's real
    # ~/.navig / register a real service from a staging run. The config key
    # above needs no guard — it writes ctx.config_dir explicitly.
    from navig.platform.paths import config_dir as _ambient_config_dir

    if Path(ctx.config_dir) != _ambient_config_dir():
        results.append(
            Result(
                action_id="profile.mode_daemon",
                state=ModuleState.SKIPPED,
                message="mode/daemon not applied (config_dir override, not this install)",
            )
        )
        return results

    # 2. One-time mode suggestion: written only when NO mode is set yet, so a
    # mode the operator already chose (navig profile set — PIN-gated for
    # privileged ones) is never silently overridden by applying a profile.
    mode = manifest.get("mode")
    if mode:
        results.append(_apply_mode_suggestion(str(mode)))
    else:
        results.append(
            Result(
                action_id="profile.mode",
                state=ModuleState.SKIPPED,
                message="no mode suggestion in this profile — active_mode untouched",
            )
        )

    # 3. Daemon flags → `navig service install`. NAVIG_NO_DAEMON is honoured
    # here the same way install.sh's register_navig_daemon honours it.
    daemon = manifest.get("daemon", None)
    if daemon is None:
        results.append(
            Result(
                action_id="profile.daemon",
                state=ModuleState.SKIPPED,
                message="no service registration (this profile registers no daemon)",
            )
        )
    elif _no_daemon_env():
        results.append(
            Result(
                action_id="profile.daemon",
                state=ModuleState.SKIPPED,
                message="service registration skipped (NAVIG_NO_DAEMON)",
            )
        )
    else:
        results.append(_register_daemon(profile, daemon))

    return results


def _apply_config_default(config_dir: Path, key: str, value: object) -> Result:
    action_id = f"profile.config.{key}"
    try:
        from navig.config import get_config_manager

        cm = get_config_manager(config_dir=config_dir)
        current = cm.get(key)
        if current is not None:
            return Result(
                action_id=action_id,
                state=ModuleState.SKIPPED,
                message=f"{key} already {current!r} — default {value!r} not imposed",
            )
        cm.set(key, value)
        return Result(action_id=action_id, state=ModuleState.APPLIED, message=f"{key} = {value!r}")
    except Exception as exc:  # noqa: BLE001 — reported, never silent
        return Result(
            action_id=action_id,
            state=ModuleState.FAILED,
            message=f"could not write {key} = {value!r}",
            error=str(exc),
        )


def extras_install_command(spec: str) -> str:
    """The command that installs *spec* into THIS navig's environment.

    install.sh builds ~/.navig/runtime/venv with uv and no pip, and Ubuntu 24.04's system
    pip refuses (PEP 668) — so a bare "pip install" hint could not work for exactly the
    users the installer creates. Inside that runtime, point at its own uv.
    """
    prefix = Path(sys.prefix)
    uv = prefix.parent / ("uv.exe" if os.name == "nt" else "uv")
    if prefix.name == "venv" and prefix.parent.name == "runtime" and uv.is_file():
        return f'{uv} pip install --python {sys.executable} "{spec}"'
    return f'{Path(sys.executable).name} -m pip install "{spec}"'


def _no_daemon_env() -> bool:
    """True when NAVIG_NO_DAEMON is set — the same gate install.sh applies."""
    return bool(os.environ.get("NAVIG_NO_DAEMON", "").strip())


def _apply_mode_suggestion(mode: str) -> Result:
    from navig.core.yaml_io import safe_load_yaml
    from navig.modes.manager import set_active_mode
    from navig.platform.paths import config_dir

    cfg_path = Path(config_dir()) / "config.yaml"
    # safe_load_yaml returns None for missing AND unreadable; the write below
    # goes through the mode manager's read-modify-write guard, which refuses an
    # unreadable-but-populated config rather than wiping it.
    data = safe_load_yaml(cfg_path) or {}
    if isinstance(data, dict) and data.get("active_mode"):
        return Result(
            action_id="profile.mode",
            state=ModuleState.SKIPPED,
            message=f"active_mode already {data['active_mode']!r} — suggestion {mode!r} not imposed",
        )
    set_active_mode(mode)
    return Result(
        action_id="profile.mode",
        state=ModuleState.APPLIED,
        message=f"active_mode = {mode} (one-time suggestion)",
    )


def _register_daemon(profile: str, daemon: dict) -> Result:
    from navig.commands.service import service_install

    flags = daemon_cli_args(daemon)
    detail = ", ".join(
        f"{key}={'yes' if daemon.get(key) is not None else 'default'}"
        for key in ("bot", "gateway", "scheduler")
    )
    try:
        # Every parameter passed explicitly: calling a typer command function
        # directly leaves an OptionInfo instance as the default otherwise.
        service_install(
            method=None,
            no_start=False,
            bot=daemon.get("bot"),
            gateway=daemon.get("gateway"),
            scheduler=daemon.get("scheduler"),
            health_port=None,
        )
    except (SystemExit, Exception) as exc:  # noqa: BLE001 — typer.Exit is a RuntimeError
        # Visible, not swallowed: `service install` already printed its own
        # error; record the failure in the results the caller sees.
        return Result(
            action_id="profile.daemon",
            state=ModuleState.FAILED,
            message=f"service registration failed for profile {profile!r} ({detail})",
            error=str(exc) or type(exc).__name__,
        )
    return Result(
        action_id="profile.daemon",
        state=ModuleState.APPLIED,
        message=f"service registered ({detail}"
        + (f" via {' '.join(flags)}" if flags else " via service-install defaults")
        + ")",
    )


# ─────────────────────── private console helpers ──────────────────────────────


def _print_plan(actions, profile: str, manifest: dict | None, dry_run: bool) -> None:
    try:
        from navig import console_helper as ch

        label = "[dry-run] " if dry_run else ""
        ch.header(f"{label}NAVIG Installer — profile: {profile}")
        for a in actions:
            ch.dim(f"  • {a.description}")
        ch.newline()
        _print_effective_profile(profile, manifest, dry_run)
    except Exception:  # noqa: BLE001
        pass


def _print_effective_profile(profile: str, manifest: dict | None, dry_run: bool) -> None:
    """The effective-profile report: what applying this profile would do."""
    from rich.markup import escape  # noqa: PLC0415 — bracketed specs are markup to Rich

    from navig import console_helper as ch

    ch.subheader("Effective profile")
    pending = " (on apply)" if dry_run else ""

    modules = manifest.get("modules") if manifest else None
    if modules:
        ch.kv("modules", ", ".join(modules))
    ch.kv("config", escape(f"{PROFILE_CONFIG_KEY} = {profile}{pending}"))

    if manifest is None:
        ch.dim("legacy profile — modules and the config key only")
        ch.newline()
        return

    extras_spec = profile_extras_spec(manifest)
    if extras_spec:
        missing = missing_profile_extras(manifest)
        if missing:
            ch.kv(
                "extras",
                escape(f"{extras_spec}  (not installed) → "
                       + extras_install_command(f'navig[{",".join(missing)}]')),
            )
        else:
            ch.kv("extras", escape(extras_spec))
    else:
        ch.kv("extras", "none")

    daemon = manifest.get("daemon", None)
    if daemon is None:
        ch.kv("daemon", "none — this profile never registers a service")
    else:
        flags = daemon_cli_args(daemon)
        ch.kv(
            "daemon",
            "navig service install" + (f" {' '.join(flags)}" if flags else " (platform defaults)"),
        )

    mode = manifest.get("mode", None)
    if mode:
        ch.kv("mode", f"{mode} — written once, only while no mode is set")
    else:
        ch.kv("mode", "none — active_mode untouched")

    if manifest.get("summary"):
        ch.dim(str(manifest["summary"]))
    ch.newline()


def _print_results(results: list[Result], dry_run: bool) -> None:
    try:
        from navig import console_helper as ch

        for r in results:
            if r.state == ModuleState.APPLIED:
                ch.success(f"  ✓ {r.message}")
            elif r.state == ModuleState.SKIPPED:
                ch.dim(f"  · {r.message}")
            elif r.state == ModuleState.FAILED:
                ch.error(r.message, r.error or "")
        ch.newline()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "run_install",
    "DEFAULT_PROFILE",
    "VALID_PROFILES",
]
