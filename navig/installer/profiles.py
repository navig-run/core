"""
Profile-to-module mapping for the NAVIG installer.

Each profile is an ordered list of module names (in navig/installer/modules/).
Modules are applied in order and must be individually idempotent.

Legacy installer profiles
-------------------------
node            – bare minimum: dirs + CLI verification + legacy migration
operator        – node + shell integration + vault + telegram  (default)
architect       – operator + MCP configuration
system_standard – operator + telegram + MCP + service daemon
system_deep     – system_standard + tray (Windows) + persona assets

Public installation profiles (``install.sh -p`` / ``navig init --profile``)
--------------------------------------------------------------------------
Four named profiles a user picks at install time — assembled ONLY from the
existing levers above (modules, pip extras, `navig service install` flags,
the mode registry). They are NOT security boundaries and add no new daemon,
CLI namespace or enforcement surface.

cli       – config dirs + CLI verification only; never registers a service
minimal   – cli + shell PATH integration; never registers a service
server    – headless: full config stack + MCP, no desktop; daemon with the gateway,
            and no public tunnel (cloud.enabled false) — a watcher stays private
full      – everything the default (operator) install runs today

Each public profile carries a manifest (INSTALL_PROFILES) with:

``modules``  ordered module names — must be real files in installer/modules/
``extras``   pip extras the profile is built for (validated ⊆ pyproject)
``daemon``   ``None`` = never register a service; ``{}`` = `navig service
             install` with its own defaults; otherwise the component flags
             ``{"bot"/"gateway"/"scheduler": bool}`` passed to that command
``mode``     one-time ``active_mode`` suggestion, written ONLY when no mode
             is set yet — a mode the operator already chose is never touched
``config``   one-time config defaults ``{dotted.key: scalar}``, written ONLY
             when the key is unset — same never-re-imposed rule as ``mode``.
             The server profile sets ``cloud.enabled: false``: the gateway's
             default-on quick tunnel published every fresh VPS watcher at a
             public trycloudflare URL (seen on a clean Ubuntu 24.04 VM)
``summary``  one line for the effective-profile report

``monitors`` is deliberately absent: there is no existing per-profile monitor
wire worth the name, and inventing one would ship a field nothing reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: Config key written when a profile is applied (read by `navig doctor` and
#: `navig config settings`).
PROFILE_CONFIG_KEY = "install.profile"

#: Component flags `navig service install` actually declares
#: (--bot/--no-bot, --gateway/--no-gateway, --scheduler/--no-scheduler).
#: Validated against that command's signature at check time — see
#: :func:`validate_profile_manifest`.
DAEMON_FLAGS = frozenset({"bot", "gateway", "scheduler"})

_LEGACY_PROFILE_MODULES: dict[str, list[str]] = {
    "node": [
        "config_paths",
        "core_cli",
        "migrate_legacy",
    ],
    "operator": [
        "config_paths",
        "core_cli",
        "migrate_legacy",
        "shell_integration",
        "vault_bootstrap",
        "telegram",
    ],
    "architect": [
        "config_paths",
        "core_cli",
        "migrate_legacy",
        "shell_integration",
        "vault_bootstrap",
        "telegram",
        "mcp",
    ],
    "system_standard": [
        "config_paths",
        "core_cli",
        "migrate_legacy",
        "shell_integration",
        "vault_bootstrap",
        "telegram",
        "mcp",
        "service",
    ],
    "system_deep": [
        "config_paths",
        "core_cli",
        "migrate_legacy",
        "shell_integration",
        "vault_bootstrap",
        "telegram",
        "mcp",
        "service",
        "tray",
        "persona_assets",
    ],
}

#: The four public installation profiles. Key order = the order they are
#: documented and listed in error messages.
INSTALL_PROFILES: dict[str, dict[str, Any]] = {
    "cli": {
        "summary": "CLI only — config dirs + CLI verification, no shell edits, no service",
        "modules": [
            "config_paths",
            "core_cli",
        ],
        "extras": ["interactive"],
        "daemon": None,
        "mode": "node",
        "config": {"vault.role": "client"},
    },
    "minimal": {
        "summary": "CLI + shell PATH integration, no service",
        "modules": [
            "config_paths",
            "core_cli",
            "shell_integration",
        ],
        "extras": ["interactive"],
        "daemon": None,
        "mode": "node",
        "config": {"vault.role": "client"},
    },
    "server": {
        "summary": "Headless server — full config stack + MCP, no desktop, daemon with the gateway, no public tunnel",
        "modules": [
            "config_paths",
            "core_cli",
            "migrate_legacy",
            "shell_integration",
            "vault_bootstrap",
            "telegram",
            "mcp",
        ],
        "extras": ["interactive", "telegram"],
        # No "scheduler": cron runs inside the gateway; the flag is deprecated and
        # printed a warning on every server install.
        "daemon": {"bot": True, "gateway": True},
        "mode": "operator",
        # vault.role client: a server is meant to `navig vault join` an authority and read
        # only its grants. Never "authority" by default — serving secrets is an explicit
        # opt-in (vault.authority.enabled).
        "config": {"cloud.enabled": False, "vault.role": "client"},
    },
    "full": {
        "summary": "Everything the default (operator) install runs today",
        "modules": list(_LEGACY_PROFILE_MODULES["operator"]),
        "extras": ["interactive", "desktop", "telegram"],
        "daemon": {},
        "mode": None,
        # The desktop install keeps its own vault; it can become the authority with
        # vault.authority.enabled, or a client with `navig vault join`.
        "config": {"vault.role": "local"},
    },
}

#: Profile → ordered module list. The planner reads this; the four public
#: profiles share their module list with their manifest (defined above) so
#: there is exactly one place a profile's modules can change.
PROFILE_MODULES: dict[str, list[str]] = {
    **_LEGACY_PROFILE_MODULES,
    **{name: list(manifest["modules"]) for name, manifest in INSTALL_PROFILES.items()},
}

DEFAULT_PROFILE: str = "operator"
VALID_PROFILES: list[str] = list(PROFILE_MODULES)

#: Modules that exist as files in navig/installer/modules/. A manifest may only
#: name these (legacy profiles are not re-validated: system_deep still lists the
#: not-yet-implemented ``persona_assets`` placeholder).
KNOWN_MODULES: frozenset[str] = frozenset(
    p.stem
    for p in (Path(__file__).parent / "modules").glob("*.py")
    if p.stem != "__init__"
)


# ─────────────────────────────────────────────────────────────────────────────
# Manifest access + validation
# ─────────────────────────────────────────────────────────────────────────────


def resolve_install_profile(name: str) -> dict[str, Any] | None:
    """Return a copy of the manifest for a PUBLIC profile, or None for a legacy one.

    Legacy profiles (node/operator/architect/system_*) predate the manifest layer
    and keep their historical behaviour: modules only.

    Raises
    ------
    ValueError
        *name* is not a profile ``navig init --profile`` accepts at all. The
        message lists every valid name.
    """
    if name in INSTALL_PROFILES:
        return {k: v for k, v in INSTALL_PROFILES[name].items()}
    if name in VALID_PROFILES:
        return None
    raise ValueError(
        f"Unknown install profile {name!r}. "
        f"Valid profiles: {', '.join(sorted(VALID_PROFILES))}"
    )


def validate_profile_manifest(name: str, manifest: dict[str, Any]) -> list[str]:
    """Return one error string per manifest defect; an empty list means valid.

    Checks the three things that can silently rot:

    * ``extras``  ⊆ ``[project.optional-dependencies]`` in core/pyproject.toml
      (skipped with a note when the pyproject is not on disk — a wheel install
      ships no pyproject, and an unverifiable check must not invent a verdict);
    * ``modules`` ⊆ :data:`KNOWN_MODULES` (files that actually exist);
    * ``daemon``  keys ⊆ :data:`DAEMON_FLAGS`, cross-checked against the real
      ``navig service install`` signature, values bool/None.

    ``mode`` is checked against the modes registry only if it imports; a profile
    must never fail to apply because an optional subsystem is missing.
    """
    errors: list[str] = []

    # ── extras ⊆ pyproject ────────────────────────────────────────────────
    extras = manifest.get("extras", [])
    if not isinstance(extras, (list, tuple)):
        errors.append(f"extras must be a list, got {type(extras).__name__}")
    else:
        # None = pyproject.toml not on disk (installed wheel) — an unverifiable
        # check is skipped, never reported as a pass it did not make.
        known = _pyproject_extras()
        for extra in extras if known is not None else ():
            if extra not in known:
                errors.append(
                    f"unknown extra {extra!r} — not in "
                    f"[project.optional-dependencies] of core/pyproject.toml"
                )

    # ── modules ⊆ known modules ───────────────────────────────────────────
    modules = manifest.get("modules", [])
    if not isinstance(modules, (list, tuple)) or not modules:
        errors.append("modules must be a non-empty list of module names")
    else:
        for mod in modules:
            if mod not in KNOWN_MODULES:
                errors.append(
                    f"unknown module {mod!r} — no navig/installer/modules/{mod}.py "
                    f"(known: {', '.join(sorted(KNOWN_MODULES))})"
                )

    # ── daemon ⊆ real `navig service install` component flags ─────────────
    daemon = manifest.get("daemon", None)
    if daemon is not None:
        if not isinstance(daemon, dict):
            errors.append(
                "daemon must be None (no service), {} (service defaults) "
                f"or a flag dict, got {type(daemon).__name__}"
            )
        else:
            # Empty = the live signature could not be read (import failure) — that
            # is "cannot verify", not "the flag is gone", so only the constant is
            # checked then. Drift is still caught whenever inspection works: a
            # non-empty result missing the key means the flag really was removed.
            real = _service_install_flags()
            for key, value in daemon.items():
                if key not in DAEMON_FLAGS:
                    errors.append(
                        f"unknown daemon flag {key!r} — real flags: "
                        f"{', '.join(sorted(DAEMON_FLAGS))}"
                    )
                elif real and key not in real:
                    errors.append(
                        f"daemon flag {key!r} is not declared by "
                        "`navig service install` any more — update the manifest"
                    )
                elif not isinstance(value, (bool, type(None))):
                    errors.append(
                        f"daemon flag {key!r} must be bool or None, "
                        f"got {type(value).__name__}"
                    )

    # ── config defaults: dotted str keys → scalars ────────────────────────
    config = manifest.get("config", None)
    if config is not None:
        if not isinstance(config, dict):
            errors.append(f"config must be a dict of dotted keys, got {type(config).__name__}")
        else:
            for key, value in config.items():
                if not isinstance(key, str) or not key or key.startswith(".") or key.endswith("."):
                    errors.append(f"config key {key!r} must be a dotted name like 'cloud.enabled'")
                elif not isinstance(value, (bool, int, float, str)):
                    errors.append(
                        f"config {key!r} must be a scalar default, got {type(value).__name__}"
                    )

    # ── mode (best-effort: optional subsystem) ────────────────────────────
    mode = manifest.get("mode", None)
    if mode is not None:
        try:
            from navig.modes.manager import all_modes  # noqa: PLC0415

            if mode not in all_modes():
                errors.append(
                    f"unknown mode {mode!r} — modes: {', '.join(sorted(all_modes()))}"
                )
        except Exception:  # noqa: BLE001 — a missing optional subsystem is not a manifest defect
            pass

    return errors


def validate_install_profile(name: str) -> dict[str, Any] | None:
    """Resolve *name* and raise ValueError if its manifest is defective.

    Returns the manifest (None for a legacy profile) when valid. This is the
    gate ``run_install`` runs BEFORE any module mutates anything.
    """
    manifest = resolve_install_profile(name)
    if manifest is None:
        return None
    errors = validate_profile_manifest(name, manifest)
    if errors:
        raise ValueError(
            f"Invalid install profile {name!r}: " + "; ".join(errors)
        )
    return manifest


def daemon_cli_args(daemon: dict[str, Any] | None) -> list[str]:
    """The `navig service install` flags a daemon manifest maps to (``None`` → no call).

    ``{}`` yields ``[]`` — register with the command's own defaults, which is
    exactly what the installer did before profiles existed.
    """
    if daemon is None:
        return []
    return [f"--{key}" for key in sorted(daemon) if daemon[key] is True]


def profile_extras_spec(manifest: dict[str, Any] | None) -> str | None:
    """``navig[a,b]`` for a manifest's extras, or None when there are none."""
    extras = list((manifest or {}).get("extras") or [])
    if not extras:
        return None
    return f"navig[{','.join(extras)}]"


def missing_profile_extras(manifest: dict[str, Any] | None) -> list[str]:
    """Profile extras whose requirements are NOT importable-installed (``[]`` = unknown).

    Pure inspection: never installs anything. An extra that declares no
    requirements (``deck``, ``media`` — empty alias extras) counts as satisfied.
    """
    extras = list((manifest or {}).get("extras") or [])
    if not extras:
        return []
    requirements = _navig_requirement_extras()
    if requirements is None:
        return []  # package metadata unreadable — say nothing rather than guess

    import importlib.metadata  # noqa: PLC0415

    missing: list[str] = []
    for extra in extras:
        reqs = requirements.get(extra)
        if reqs is None:
            missing.append(extra)  # declared but unknown to the installed metadata
            continue
        for req in reqs:
            pkg = _requirement_name(req)
            if not pkg:
                continue
            try:
                importlib.metadata.version(pkg)
            except importlib.metadata.PackageNotFoundError:
                missing.append(extra)
                break
    return missing


# ─────────────────────────────────────────────────────────────────────────────
# helpers (all lazy/reading — nothing here mutates state)
# ─────────────────────────────────────────────────────────────────────────────


def _pyproject_path() -> Path | None:
    """core/pyproject.toml — the source of truth for extras. None for a wheel install."""
    path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return path if path.is_file() else None


def _pyproject_extras() -> set[str] | None:
    """Set of declared extra names, or None when the navig pyproject is unreadable.

    Requires ``name = "navig"``: an installed package resolves this path to
    ``site-packages/pyproject.toml``, and comparing against SOME OTHER project's
    extras would invent "unknown extra" errors that block a valid profile.
    """
    path = _pyproject_path()
    if path is None:
        return None
    try:
        import tomllib  # noqa: PLC0415 — stdlib since 3.11

        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — unreadable/unparsable is "cannot verify", not "valid"
        return None
    project = data.get("project", {})
    if project.get("name") != "navig":
        return None
    extras = project.get("optional-dependencies", {})
    return set(extras) if isinstance(extras, dict) else None


def _service_install_flags() -> frozenset[str]:
    """The component flags `navig service install` really declares, today.

    Read from the live command signature rather than trusted from a constant, so
    a flag renamed in commands/service.py fails validation here instead of
    silently passing a manifest that no longer maps to anything.
    """
    try:
        import inspect  # noqa: PLC0415

        from navig.commands.service import service_install  # noqa: PLC0415

        return frozenset(inspect.signature(service_install).parameters) & DAEMON_FLAGS
    except Exception:  # noqa: BLE001 — cannot inspect → check nothing rather than fake a pass
        return frozenset()


def _navig_requirement_extras() -> dict[str, list[str]] | None:
    """``{extra: [requirement, ...]}`` from the installed navig package metadata."""
    try:
        import importlib.metadata  # noqa: PLC0415

        reqs = importlib.metadata.requires("navig") or []
    except Exception:  # noqa: BLE001
        return None
    out: dict[str, list[str]] = {}
    for req in reqs:
        extra = _requirement_extra(req)
        if extra is None:
            continue  # a base requirement, not an extra
        out.setdefault(extra, []).append(req)
    return out


def _requirement_extra(req: str) -> str | None:
    """The ``extra == "x"`` marker from a metadata requirement line, else None."""
    import re  # noqa: PLC0415

    m = re.search(r'extra\s*==\s*"([^"]+)"', req)
    if m:
        return m.group(1)
    m = re.search(r"extra\s*==\s*'([^']+)'", req)
    return m.group(1) if m else None


def _requirement_name(req: str) -> str | None:
    """Distribution name at the head of a requirement line (``pkg[extra]>=1`` → pkg)."""
    import re  # noqa: PLC0415

    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
    return m.group(1) if m else None
