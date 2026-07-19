"""
navig doctor — Self-diagnostics (P1-15)

Reports on NAVIG installation health without mutating any state.
Checks: config, cache, vault, formations, skills, gateway, API keys.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any

import typer

from navig._daemon_defaults import _GATEWAY_PORT
from navig.console_helper import get_console
from navig.platform.paths import config_dir

logger = logging.getLogger(__name__)

doctor_app = typer.Typer(
    name="doctor",
    help="Run NAVIG self-diagnostics and report installation health.",
    invoke_without_command=True,
)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_OK = "✓"
_WARN = "⚠"
_ERR = "✗"
_INFO = "·"


class CheckResult(tuple):
    """One doctor row.

    Behaves exactly like the historical ``(icon, ok, line)`` 3-tuple — every
    existing consumer unpacks or indexes it — while ALSO carrying the raw
    structured fields (``label``, ``detail``) so ``--json`` can emit data
    instead of scraping glyphs and labels back out of pre-rendered strings.
    The rendered warn state is not stored: it is fully encoded by the icon
    (``row[0] == _WARN``), which is exactly what the human report prints.
    """

    label: str
    detail: str

    def __new__(cls, icon: str, ok: bool, line: str, *, label: str = "", detail: str = ""):
        self = super().__new__(cls, (icon, ok, line))
        self.label = label
        self.detail = detail
        return self


def _check(
    label: str,
    ok: bool,
    detail: str = "",
    warn: bool = False,
) -> CheckResult:
    """Return a formatted result row (a ``(icon, ok, line)`` tuple + structured fields)."""
    if ok:
        icon = _OK
    elif warn:
        icon = _WARN
    else:
        icon = _ERR
    line = f"  {icon} {label}" + (f": {detail}" if detail else "")
    return CheckResult(icon, ok, line, label=label, detail=detail)


# JSON output must carry plain data: strip ANSI escapes and the report glyphs
# from every emitted string (details are already plain text today — this is
# the guard that keeps a future exception message or wrapped value from
# smuggling terminal formatting into a machine-readable payload).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain_text(text: str) -> str:
    text = _ANSI_RE.sub("", str(text))
    for glyph in (_OK, _WARN, _ERR):
        text = text.replace(glyph, "")
    return text.strip()


def _gateway_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """Try a TCP connect to the gateway port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def _count_yaml_files(directory: Path) -> tuple[int, int]:
    """Return (total, error_count) for YAML files in directory."""
    total = 0
    errors = 0
    if not directory.exists():
        return 0, 0
    try:
        import yaml

        for f in directory.rglob("*.yaml"):
            total += 1
            try:
                yaml.safe_load(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                errors += 1
        for f in directory.rglob("*.yml"):
            total += 1
            try:
                yaml.safe_load(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                errors += 1
    except ImportError:
        pass  # optional dependency not installed; feature disabled
    return total, errors


def _runtime_dir() -> Path:
    """Resolve the isolated runtime root (`~/.navig/runtime`).

    Derives it from ``sys.executable`` when we're actually running inside the
    managed venv (``<runtime>/venv/(Scripts|bin)/python``); otherwise falls back
    to the installer's fixed location.
    """
    try:
        exe = Path(sys.executable).resolve()
        # Match the exact managed layout: <runtime>/venv/(Scripts|bin)/python(.exe)
        if (
            exe.parent.name in ("Scripts", "bin")
            and exe.parents[1].name == "venv"
            and exe.parents[2].name == "runtime"
        ):
            return exe.parents[2]
    except Exception:  # noqa: BLE001
        pass  # unexpected interpreter layout; use the default below
    return Path.home() / ".navig" / "runtime"


def _daemon_autostart() -> tuple[bool, str]:
    """Best-effort: is the daemon registered for OS auto-start? (registered, kind)."""
    import subprocess

    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["schtasks", "/query", "/tn", "NAVIG Daemon"],
                capture_output=True, text=True, timeout=5,
            )
            return r.returncode == 0, "Task Scheduler"
        r = subprocess.run(
            ["systemctl", "--user", "is-enabled", "navig-agent"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0, "systemd"
    except Exception:  # noqa: BLE001
        return False, ""  # tool missing/timeout — treat as not registered


# ──────────────────────────────────────────────────────────────────────────────
# Individual checks
# ──────────────────────────────────────────────────────────────────────────────


def check_runtime() -> list[tuple[str, bool, str]]:
    """Check the isolated uv-managed runtime: uv, venv, shim/PATH, daemon, skills.

    For dev/editable installs (not running from the managed runtime) the
    runtime-specific checks are skipped so they don't report false failures.
    """
    results: list[tuple[str, bool, str]] = []
    rt = _runtime_dir()
    is_win = os.name == "nt"
    venv_py = rt / "venv" / ("Scripts" if is_win else "bin") / ("python.exe" if is_win else "python")

    try:
        in_runtime = str(Path(sys.executable).resolve()).startswith(str(rt.resolve()))
    except Exception:  # noqa: BLE001
        in_runtime = False

    managed = in_runtime or venv_py.exists()
    if not managed:
        results.append(_check("install type", True, f"dev/editable ({sys.executable}) — runtime checks skipped"))
    else:
        uv_exe = rt / ("uv.exe" if is_win else "uv")
        results.append(_check("uv engine", uv_exe.exists(), str(uv_exe) if uv_exe.exists() else "missing from runtime", warn=True))
        results.append(_check("isolated venv", venv_py.exists(), str(rt / "venv") if venv_py.exists() else "missing", warn=True))
        results.append(_check("running from runtime", in_runtime, sys.executable, warn=True))
        shim = Path.home() / ".local" / "bin" / ("navig.cmd" if is_win else "navig")
        results.append(_check("launcher shim", shim.exists(), str(shim) if shim.exists() else "missing", warn=True))
        on_path = str(shim.parent) in os.environ.get("PATH", "").split(os.pathsep)
        results.append(_check("shim dir on PATH", on_path, "" if on_path else "open a new shell / re-run install", warn=True))
        registered, kind = _daemon_autostart()
        results.append(_check("daemon auto-start", registered, kind if registered else "not registered — run 'navig service install'", warn=True))

    try:
        from navig.platform.paths import store_dir
        skills_dir = store_dir() / "skills"
    except Exception:  # noqa: BLE001
        skills_dir = config_dir() / "data" / "store" / "skills"
    n = sum(1 for _ in skills_dir.glob("*/SKILL.md")) if skills_dir.exists() else 0
    results.append(_check("installed skills", True, f"{n} in {skills_dir}"))
    return results


def check_config() -> list[tuple[str, bool, str]]:
    """Check global config.yaml."""
    results = []
    config_path = config_dir() / "config.yaml"

    if not config_path.exists():
        results.append(_check("Config file", False, f"{config_path} not found"))
        return results

    try:
        import yaml

        content = config_path.read_text(encoding="utf-8", errors="replace")
        cfg = yaml.safe_load(content) or {}
        version = cfg.get("version", "missing")
        results.append(_check("Config file", True, f"{config_path} (v{version}, valid YAML)"))
    except Exception as e:
        results.append(_check("Config file", False, f"YAML error in {config_path}: {e}"))

    return results


def check_cache_dir() -> list[tuple[str, bool, str]]:
    """Check cache directory is writable."""
    results = []
    cache_dir = config_dir() / "cache"

    if not cache_dir.exists():
        results.append(_check("Cache dir", False, f"{cache_dir} does not exist", warn=True))
        return results

    test_file = cache_dir / ".write_test"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        results.append(_check("Cache dir", True, f"{cache_dir} (writable)"))
    except Exception as e:
        results.append(_check("Cache dir", False, f"Not writable: {e}"))

    return results


def check_storage() -> list[tuple[str, bool, str]]:
    """Check if the system has enough free disk space for NAVIG databases and operations."""
    import shutil

    results = []

    navig_dir = config_dir()
    navig_dir.mkdir(exist_ok=True, parents=True)

    try:
        usage = shutil.disk_usage(navig_dir)
        free_gb = usage.free / (1024**3)

        # Invariant: Must have at least 1GB free to safely run SQLite WAL, migrations, and model caches.
        if free_gb < 1.0:
            results.append(
                _check(
                    "Disk Space",
                    False,
                    f"FATAL: Only {free_gb:.2f}GB free. NAVIG requires >1GB to prevent DB corruption.",
                )
            )
        elif free_gb < 5.0:
            results.append(
                _check(
                    "Disk Space",
                    True,
                    f"Low Space Warning: {free_gb:.2f}GB free. Consider cleanup.",
                    warn=True,
                )
            )
        else:
            results.append(_check("Disk Space", True, f"{free_gb:.1f}GB free (OK)"))
    except Exception as e:
        results.append(_check("Disk Space", False, f"Failed to stat volume: {e}"))

    return results


def check_vault() -> list[tuple[str, bool, str]]:
    """Local vault health — no daemon required, no secrets, no mutation.

    Counts items and probes that ONE item's key wrapper actually decrypts,
    through the vault's public API. Only COUNTS ever reach the output: labels,
    providers and payloads must never appear in a diagnostic (doctor output
    gets copy-pasted into issues and logs).

    Every path resolves at CALL time (``NAVIG_CONFIG_DIR`` honoured when the
    check runs) — the lesson from the legacy-migration leak, where an
    import-time path constant silently pointed vault code at the REAL user
    home (see ``navig/vault/migrate.py:_legacy_db_path``). Deliberately avoids
    ``get_vault()``: the singleton triggers auto-migration (a mutation) and
    freezes its first-resolved path for the rest of the process.
    """
    results: list[tuple[str, bool, str]] = []
    try:
        from navig.platform.paths import vault_dir as _vault_dir_fn
        from navig.vault.migrate import check_legacy_exists, legacy_migration_done

        vdir = _vault_dir_fn()
        db_path = vdir / "vault.db"

        if not db_path.exists():
            results.append(
                _check("Vault", True, "no vault yet — created on first `navig vault set`")
            )
        else:
            try:
                from navig.vault.core import Vault
                from navig.vault.crypto import CryptoEngine, CryptoError

                vault = Vault(vdir)
                n = vault.count()
                detail = f"{n} item(s)"
                if n:
                    if not (vdir / CryptoEngine.SALT_FILE).exists():
                        # derive_key() would CREATE a fresh salt — a mutation, and
                        # one that can never decrypt the existing items.
                        raise CryptoError("salt file missing")
                    probe = next((i for i in vault.store().list() if i.encrypted_dek), None)
                    if probe is not None:
                        # Open one item's DEK *wrapper* only — never the payload.
                        CryptoEngine.open(vault.engine().derive_key(None), probe.encrypted_dek)
                        detail += " · encryption OK"
                results.append(_check("Vault", True, detail))
            except Exception as exc:  # noqa: BLE001
                # Exception CLASS only: messages can embed paths or item labels.
                results.append(_check("Vault", False, f"cannot open vault ({type(exc).__name__})"))

        if check_legacy_exists():
            if legacy_migration_done(vdir):
                results.append(_check("Legacy credentials", True, "legacy DB retained (migrated)"))
            else:
                results.append(
                    _check(
                        "Legacy credentials",
                        False,
                        "legacy credentials DB present — will auto-migrate on next vault use",
                        warn=True,
                    )
                )
    except Exception as exc:  # noqa: BLE001 — a doctor check must never crash doctor
        results.append(_check("Vault", False, f"COULD NOT VERIFY ({type(exc).__name__})", warn=True))

    return results


def check_sockets(target_port: int | None = None) -> list[tuple[str, bool, str]]:
    """Check if critical ports are available or correctly bound."""
    results = []

    # Bind-side semantics: "could a NEW daemon bind here?" — so resolve the
    # CONFIGURED port (config is canonical for binding, unlike check_gateway
    # which follows the live self-healed port).
    if target_port is None:
        try:
            from navig.gateway_client import gateway_cli_defaults

            target_port = gateway_cli_defaults()[0]
        except Exception:  # noqa: BLE001
            target_port = _GATEWAY_PORT

    # Try binding to see if the port is strictly available for a new daemon.
    # If it's not available, it should be the running gateway.
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            ans = s.connect_ex(("127.0.0.1", target_port))
            if ans == 0:
                results.append(
                    _check(
                        "Port Occupation",
                        True,
                        f"Port {target_port} is bound (Gateway running)",
                        warn=True,
                    )
                )
            else:
                # connect_ex can't detect OS port reservations (WinNAT/Hyper-V
                # excluded ranges refuse connects AND binds) — don't promise
                # bindability, only that nothing is listening.
                results.append(
                    _check(
                        "Port Occupation",
                        True,
                        f"Port {target_port} is not in use (OS-reserved ranges "
                        "may still block binding — the gateway self-heals to "
                        "a free port if so)",
                    )
                )
    except Exception as e:
        results.append(_check("Port Occupation", False, f"Socket error on port {target_port}: {e}"))

    return results


def check_formations() -> list[tuple[str, bool, str]]:
    """Check formations: count what the loader actually discovers.

    The old check counted ``*.yaml`` under ``config_dir()/formations`` — but
    formations are ``formation.json`` DIRS, and installs land under
    ``store_dir()/formations`` (#373). So it read the wrong dir with the wrong
    file type and effectively always showed 0. ``discover_formations()`` is the
    loader's real discovery (builtin + user store + config + plugins), so the
    count matches what actually loads.
    """
    results = []
    try:
        from navig.formations.loader import discover_formations

        n = len(discover_formations())
    except Exception as e:  # noqa: BLE001
        results.append(_check("Formations", False, f"discovery failed: {e}", warn=True))
        return results

    if n == 0:
        # Builtins ship with NAVIG, so 0 discoverable means the builtin store is
        # likely missing — a ⚠ (ok=False, warn=True), NOT a green tick: `_check`
        # renders ✓ whenever ok=True regardless of warn, and a green light over a
        # broken store is exactly what the doctor honesty rule forbids.
        results.append(
            _check("Formations", False, "0 discovered — builtin store may be missing", warn=True)
        )
    else:
        results.append(_check("Formations", True, f"{n} discovered"))

    return results


def check_skills() -> list[tuple[str, bool, str]]:
    """Check skills: count + parse errors."""
    results = []

    # Skills live inside the installed package
    try:
        import navig

        pkg_dir = Path(navig.__file__).parent.parent
        skills_dir = pkg_dir / "skills"
        if not skills_dir.exists():
            # Try navig-core shipped skills
            skills_dir = pkg_dir.parent / "skills"
    except Exception:
        skills_dir = None

    if skills_dir and skills_dir.exists():
        total, errors = _count_yaml_files(skills_dir)
        if errors:
            results.append(_check("Skills", False, f"{total} found, {errors} invalid"))
        else:
            results.append(_check("Skills", True, f"{total} found, 0 invalid"))
    else:
        results.append(_check("Skills", True, "Skills dir not found (non-fatal)", warn=True))

    return results


def check_gateway(port: int | None = None) -> list[tuple[str, bool, str]]:
    """Check if the gateway is running — on its LIVE port, not a guessed one."""
    results = []

    # Live-first: follow the self-healed port from ~/.navig/gateway.json when
    # the gateway is up (the configured port can be swallowed by reserved OS
    # ranges); fall back to config / the canonical default otherwise.
    if port is None:
        try:
            from navig.gateway_client import gateway_live_defaults

            port = gateway_live_defaults()[0]
        except Exception:  # noqa: BLE001
            port = _GATEWAY_PORT

    reachable = _gateway_reachable("127.0.0.1", port)
    if reachable:
        results.append(_check("Gateway", True, f"Responding at 127.0.0.1:{port}"))
    else:
        results.append(
            _check(
                "Gateway",
                False,
                f"No response at 127.0.0.1:{port} (start with: navig service start)",
                warn=True,
            )
        )

    # The mesh token is minted by the gateway on start — it belongs here, not under
    # AI provider keys (where it used to sit purely because both were "secrets").
    #
    # It lives in CONFIG (`gateway.mesh_token` — see GatewayServer._ensure_mesh_token),
    # NOT in a file. This check used to stat `<config_dir>/cache/mesh_token`, a path
    # nothing has ever written, so it warned "not found (generated on gateway start)"
    # on every single run — including on a healthy install whose token was present all
    # along. A warning that can never go green is worse than no warning: it teaches the
    # operator to skim past the one row that might have mattered.
    token = ""
    try:
        from navig.config import ConfigManager

        gw = (ConfigManager().global_config or {}).get("gateway", {}) or {}
        token = str(gw.get("mesh_token", "") or "").strip()
    except Exception:  # noqa: BLE001 — a health check must never crash the doctor
        token = ""

    if not token:  # legacy installs may still carry the old sidecar file
        legacy = config_dir() / "cache" / "mesh_token"
        try:
            if legacy.exists() and legacy.stat().st_size > 0:
                token = legacy.read_text(encoding="utf-8").strip()
        except Exception:  # noqa: BLE001
            pass

    if token:
        results.append(_check("MESH_TOKEN", True, "present (gateway.mesh_token)"))
    else:
        results.append(
            _check("MESH_TOKEN", False, "not set (minted on the next gateway start)", warn=True)
        )

    return results


# Pending events before the Event processor row warns about a growing backlog.
_EVENTS_BACKLOG_WARN = 50

# How long to wait on /api/deck/status. 3s was too tight: a gateway that is up but still
# warming (plugins + 99 skills load on boot) blew the deadline, and the row then reported
# a cheerful ✓ "not checked" — a green light over a check that never ran.
_STATUS_TIMEOUT = 8.0


def check_event_processor(port: int | None = None) -> list[tuple[str, bool, str]]:
    """Is the gateway's system-event processor actually draining events?

    Regression guard for the silent failure fixed in the gateway boot path:
    ``SystemEventQueue.start()`` was never called, so every emitted event
    (board_update, council_update, …) piled up undrained — 2,737 accumulated —
    while ``/api/events`` served heartbeats only and every light stayed green.
    Reads the additive ``events`` block on ``GET /api/deck/status``; a local
    request rides the deck's loopback desktop bypass, so no credential needed.
    """
    if port is None:
        try:
            from navig.gateway_client import gateway_live_defaults

            port = gateway_live_defaults()[0]
        except Exception:  # noqa: BLE001
            port = _GATEWAY_PORT

    if not _gateway_reachable("127.0.0.1", port):
        # The Gateway row already fails loudly when the daemon is down — this
        # daemon-dependent row degrades to "not checked" (like Reachability).
        return [_check("Event processor", False, "not checked — gateway not running", warn=True)]

    try:
        import requests

        from navig.gateway_client import gateway_request_headers

        resp = requests.get(
            f"http://127.0.0.1:{port}/api/deck/status",
            headers=gateway_request_headers(),
            timeout=_STATUS_TIMEOUT,
        )
        if resp.status_code != 200:
            return [
                _check(
                    "Event processor",
                    False,
                    f"COULD NOT VERIFY (status endpoint returned HTTP {resp.status_code})",
                    warn=True,
                )
            ]
        events = (resp.json() or {}).get("events")
    except Exception as exc:  # noqa: BLE001 — a doctor check must never crash doctor
        return [_check("Event processor", False, f"COULD NOT VERIFY ({exc})", warn=True)]

    if not isinstance(events, dict):
        return [
            _check(
                "Event processor",
                False,
                "COULD NOT VERIFY — daemon does not expose event stats (older core); "
                "restart the gateway after upgrading",
                warn=True,
            )
        ]

    try:
        running = bool(events.get("running"))
        pending = int(events.get("pending") or 0)
        history = int(events.get("history") or 0)
    except (TypeError, ValueError):
        return [_check("Event processor", False, "COULD NOT VERIFY (malformed event stats)", warn=True)]

    if not running:
        return [
            _check(
                "Event processor",
                False,
                f"NOT RUNNING — emitted events are piling up undrained ({pending} pending). "
                "Restart the gateway; if it persists, the processor failed to start "
                "(check logs for 'System event processor failed to start')",
            )
        ]
    if pending >= _EVENTS_BACKLOG_WARN:
        return [
            _check(
                "Event processor",
                False,
                f"running but {pending} pending — emit backlog growing "
                f"(warn threshold {_EVENTS_BACKLOG_WARN})",
                warn=True,
            )
        ]
    return [_check("Event processor", True, f"running · {pending} pending · {history} in history")]


def check_ai_providers() -> list[tuple[str, bool, str]]:
    """What AI the brain can ACTUALLY use.

    This check used to look only at three environment variables (plus a flat
    ``config.yaml`` key that nothing writes). That is not where NAVIG keeps provider
    auth: `navig connect add` and `navig vault` put keys in the **vault / auth
    profiles**, and a Claude Pro/Max subscription is an **OAuth token, not a key at
    all**. So doctor confidently reported "OPENAI_API_KEY: some models unavailable"
    at a user whose OpenAI key was sitting in their vault, and "Claude models
    unavailable" at a user with three working Claude connections.

    Resolve through the SAME path every other consumer uses (env → vault → auth
    profiles), and report the Connections the brain would actually route through.
    """
    results: list[tuple[str, bool, str]] = []

    # ── 1. Provider keys, resolved the way `navig ai` resolves them ──────────
    configured: list[str] = []
    try:
        from navig.providers import AuthProfileManager
        from navig.providers.connect import CONNECTION_TEMPLATES

        auth = AuthProfileManager()
        provider_ids = sorted(
            {t.provider_id for t in CONNECTION_TEMPLATES.values() if t.provider_id}
        )
        for pid in provider_ids:
            try:
                key, source = auth.resolve_auth(pid)
            except Exception:  # noqa: BLE001 — one bad provider must not kill the check
                continue
            if key:
                # `source` is like "vault:openai" / "profile:x" / "env:OPENAI_API_KEY".
                configured.append(pid)
                results.append(_check(pid, True, f"key configured ({source})"))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Provider keys", False, f"could not resolve: {exc}", warn=True))

    # ── 2. Connections — including whether the DEFAULT can actually route ────
    #
    # A default connection stuck in needs_reauth means the brain cannot do AI at
    # all, and nothing surfaced that anywhere. That is the single most important
    # thing this section can tell you.
    try:
        from navig.providers.connect import diagnostics_report

        report = diagnostics_report()
        conns = report.get("connections") or []
        default = next((c for c in conns if c.get("is_default")), None)

        if conns:
            results.append(_check("Connections", True, f"{len(conns)} configured"))
        if default:
            name = default.get("name") or default.get("connection_id")
            if default.get("is_routable"):
                results.append(_check("Default", True, f"{name} (ready)"))
            else:
                results.append(
                    _check(
                        "Default",
                        False,
                        f"{name} is {default.get('ui_state', 'not routable')} — the brain "
                        f"cannot run AI until this is fixed (navig connect test)",
                    )
                )
        elif conns:
            results.append(
                _check("Default", False, "no default connection set (navig connect default <id>)",
                       warn=True)
            )

        broken = [
            c for c in conns
            if not c.get("is_routable") and not c.get("is_default")
        ]
        if broken:
            names = ", ".join(str(c.get("name") or c.get("connection_id")) for c in broken[:3])
            results.append(
                _check("Needs re-auth", False, f"{len(broken)}: {names}", warn=True)
            )

        # In-flight logins. Only worth a line when there ARE any — an abandoned one
        # is the visible trace of a login that never completed.
        pending = report.get("pending_logins")
        if pending:
            results.append(
                _check("Logins in progress", True, f"{pending} (expire after 10 min)")
            )

        if not conns and not configured:
            results.append(
                _check(
                    "AI provider",
                    False,
                    "none configured — run `navig connect add <template>` or `navig init --provider`",
                    warn=True,
                )
            )
    except Exception as exc:  # noqa: BLE001 — never let doctor itself crash
        results.append(_check("Connections", False, f"could not read: {exc}", warn=True))

    return results


def check_browsers() -> list[tuple[str, bool, str]]:
    """Debug browsers left running that no NAVIG session owns.

    A leaked debug browser is invisible by design: the harness renders its content in
    a tab it then closes, so the window is blank — and a headless one shows nothing at
    all. Nothing ever looked for them, which is exactly how ~24 of them (and 3.6 GB of
    profiles) once accumulated before anyone noticed. Port scans miss them too: a
    browser on ``--remote-debugging-port=0`` holds an ephemeral port nobody can guess.

    Returns [] when there is nothing to say, so the section only appears when it has
    something worth telling you.
    """
    try:
        from navig.browser.targets import list_debug_browsers

        running = list_debug_browsers()
    except Exception as exc:  # noqa: BLE001 — doctor must never be the thing that breaks
        logger.debug("browser leak check failed: %s", exc)
        return []

    orphans = [b for b in running if b["kind"] == "orphan"]
    foreign = [b for b in running if b["kind"] == "foreign"]
    if not orphans and not foreign:
        return []

    results: list[tuple[str, bool, str]] = []
    if orphans:
        results.append(
            _check(
                "Leaked browsers",
                False,
                f"{len(orphans)} NAVIG-launched browser(s) still running "
                f"(reclaim: navig cdp stop --all)",
                warn=True,
            )
        )
    if foreign:
        # Not ours to kill — another harness may still be driving them. Say what they
        # are and let the operator decide.
        pids = ", ".join(str(b["pid"]) for b in foreign[:3])
        results.append(
            _check(
                "Foreign debug browsers",
                True,
                f"{len(foreign)} running that NAVIG did not launch (pid {pids}) — "
                f"left untouched",
            )
        )
    return results


def check_python_deps() -> list[tuple[str, bool, str]]:
    """Quick check for key optional dependencies."""
    results = []

    optional_deps = [
        ("aiohttp", "gateway server"),
        ("yaml", "config parsing"),
        ("typer", "CLI framework"),
        ("rich", "terminal output"),
        ("pydantic", "data validation"),
        ("cryptography", "vault / encryption"),
    ]

    for mod, purpose in optional_deps:
        try:
            importlib.import_module(mod)
            results.append(_check(f"Python/{mod}", True, purpose))
        except ImportError:
            results.append(_check(f"Python/{mod}", False, f"missing — affects {purpose}"))

    return results


def check_reachability() -> list[tuple[str, bool, str]]:
    """Can the bot actually HEAR? (lighthouse mode only — empty section otherwise.)

    "Uplink: online" is not proof of reachability. The lighthouse webhook URL embeds
    ``sha256(deck.api_key)`` — the Durable Object the edge routes Telegram's POSTs to
    — and the gateway *rotates* ``deck.api_key`` on its own (mints one when missing,
    upgrades one under 16 chars). A rotation leaves ``telegram.webhook_url`` addressing
    the OLD tenant, whose DO has no uplink socket: it queues every update and acks 202,
    while the brain, attached to the NEW tenant, reports a perfectly healthy uplink.
    The bot goes 100% deaf with every light green — so check the tenants match.
    """
    results: list[tuple[str, bool, str]] = []
    try:
        from navig.core import Config
        from navig.telegram.updates import corrected_webhook_url

        cfg = Config()
        if str(cfg.get("cloud.mode", "") or "").lower() != "lighthouse":
            return []  # not lighthouse — the section does not apply

        hook = cfg.get("telegram.webhook_url")
        if not hook:
            return [
                _check(
                    "Telegram webhook",
                    True,
                    "not configured (bot uses long-polling)",
                    warn=True,
                )
            ]

        edge = str(cfg.get("cloud.lighthouse_url", "") or "").strip().rstrip("/")
        if corrected_webhook_url(hook, cfg):
            results.append(
                _check(
                    "Telegram webhook",
                    False,
                    "STALE tenant — deck.api_key was rotated, so Telegram is delivering "
                    "to a dead edge queue and the bot cannot receive ANY message. "
                    "Fix: restart the gateway (it self-heals) or `navig lighthouse redeploy`",
                )
            )
        elif edge and not hook.startswith(f"{edge}/tg/"):
            # Not our edge — we can't derive or vouch for this tenant, so don't pretend to.
            results.append(
                _check("Telegram webhook", True, "custom host (not the lighthouse edge)", warn=True)
            )
        else:
            results.append(_check("Telegram webhook", True, "tenant matches the live brain"))
    except Exception as exc:  # noqa: BLE001 — doctor must never crash on a check
        results.append(_check("Telegram webhook", False, f"COULD NOT VERIFY ({exc})", warn=True))

    return results


def check_config_health() -> list[tuple[str, bool, str]]:
    """Did the config/identity layer have to SAVE ITSELF recently — and is it still armed?

    Every fix in this area is self-healing: a refused config wipe, a load that fell back
    to the last known-good cache, a deck.api_key restored from the vault. That is the
    right behaviour — and it is also how an install ends up quietly broken, because a
    daemon that heals itself at 3am and tells nobody looks exactly like a daemon that is
    fine. The whole chain was written after a bot went 100% deaf with every light green.

    So: surface the incidents, and confirm the recovery mirror actually exists. An armed
    net you cannot see is indistinguishable from no net at all.
    """
    results: list[tuple[str, bool, str]] = []

    # 1. A preserved corrupt file — the config was unreadable at some point.
    try:
        from navig.platform.paths import config_dir

        corrupt = config_dir() / "config.yaml.corrupt"
        if corrupt.exists():
            results.append(
                _check(
                    "config.yaml",
                    False,
                    f"a previous load FAILED — the unreadable copy is kept at {corrupt}. "
                    "Compare it with your live config, then delete it to clear this.",
                )
            )
    except Exception:  # noqa: BLE001 — a health check must never crash the doctor
        pass

    # 2. Recent self-healing events. Silence here is the healthy state.
    try:
        from navig.core import incidents

        events = incidents.recent(limit=3)
        if events:
            results.append(
                _check(
                    "Incidents",
                    False,
                    f"{len(events)} recent self-healing event(s) — the config layer had to "
                    "rescue itself. Latest: " + incidents.describe(events[0]),
                )
            )
            for entry in events[1:]:
                results.append(_check("", True, f"  {incidents.describe(entry)}", warn=True))
        else:
            results.append(_check("Incidents", True, "none — no config rescue was needed"))
    except Exception:  # noqa: BLE001
        pass

    # 3. Is the identity recoverable at all? (deck.api_key IS the Lighthouse tenant.)
    try:
        from navig.cloud import deck_key
        from navig.config import ConfigManager

        # A fresh ConfigManager, NOT navig.core.Config: that one is a process-wide
        # singleton that caches its instance and never re-reads the config dir, so a
        # health check built on it would report on whichever config happened to be
        # loaded first. A check that can silently describe the wrong install is worse
        # than no check.
        deck_cfg = (ConfigManager().global_config or {}).get("deck", {}) or {}
        if str(deck_cfg.get("api_key", "") or "").strip():
            if deck_key.is_mirrored():
                results.append(
                    _check("Key recovery", True, "deck.api_key is mirrored — a config wipe is survivable")
                )
            else:
                results.append(
                    _check(
                        "Key recovery",
                        False,
                        "deck.api_key is NOT mirrored to the vault — if config lost it, the "
                        "gateway would mint a NEW identity and silently move the bot's mailbox. "
                        "Start the daemon once to arm the mirror.",
                        warn=True,
                    )
                )
    except Exception:  # noqa: BLE001
        pass

    return results


def check_ledger() -> list[tuple[str, bool, str]]:
    """In-flight operations that outlived their process — interrupted, never completed.

    The operation ledger records a line only at completion (an atexit handler),
    so a hard-killed process leaves an in-flight MARKER but no ledger line
    (navig.operation_inflight). A marker whose owning process is gone is an
    operation that was interrupted; ``navig ledger reap`` records it honestly as
    ``interrupted``. Read-only here — doctor never mutates the ledger. Returns
    [] (section hidden) when there is nothing to say.
    """
    try:
        from navig import operation_inflight as _inflight
        from navig.operation_recorder import get_operation_recorder

        markers = get_operation_recorder().iter_inflight()
    except Exception:  # noqa: BLE001 — a health check must never crash the doctor
        return []

    if not markers:
        return []

    running = interrupted = 0
    for marker in markers:
        try:
            alive = _inflight.pid_is_alive(marker.pid, marker.create_time)
        except Exception:  # noqa: BLE001
            alive = True  # indeterminate → assume alive, never over-report interrupted
        if alive:
            running += 1
        else:
            interrupted += 1

    results: list[tuple[str, bool, str]] = []
    if interrupted:
        results.append(
            _check(
                "Interrupted ops",
                False,
                f"{interrupted} operation(s) never completed (owning process gone) — "
                "record them honestly: navig ledger reap",
            )
        )
    if running:
        results.append(
            _check("In-flight ops", True, f"{running} operation(s) currently running")
        )
    return results


def check_repo_guard() -> list[tuple[str, bool, str]]:
    """Multi-agent repo guard state for the CURRENT repo.

    Empty (section skipped) outside a git repo — the guard is a per-repo,
    opt-in protection (see ``navig repo guard install``), so its absence is
    reported as informational, never as a failure. Partial wiring IS flagged:
    a half-wired guard gives false safety.
    """
    results: list[tuple[str, bool, str]] = []
    try:
        import json as _json

        from navig.commands.repo import (
            _GUARD_MARKERS,
            _guard_event_wired,
            lock_state,
            read_lock,
            repo_root,
        )

        root = repo_root()
        if root is None:
            return []  # not inside a git repo — nothing to report

        settings_path = root / ".claude" / "settings.json"
        try:
            settings = _json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                settings = {}
        except (OSError, ValueError):
            settings = {}
        wired = [ev for ev in _GUARD_MARKERS if _guard_event_wired(settings, ev)]

        if not wired:
            results.append(
                _check(
                    "Repo Guard",
                    True,
                    "not installed (optional) — enable with: navig repo guard install",
                )
            )
        elif len(wired) < len(_GUARD_MARKERS):
            missing = sorted(set(_GUARD_MARKERS) - set(wired))
            results.append(
                _check(
                    "Repo Guard",
                    False,
                    f"partially wired (missing: {', '.join(missing)}) — "
                    "re-run: navig repo guard install",
                    warn=True,
                )
            )
        else:
            lk = lock_state(read_lock(root))
            if lk["state"] == "free":
                detail = "active — lock free"
            else:
                detail = (
                    f"active — lock {lk['state']} by session {lk.get('session', '?')} "
                    f"(age {lk.get('age_minutes', '?')}m, branch {lk.get('branch') or '?'})"
                )
            results.append(_check("Repo Guard", True, detail))
    except Exception:  # noqa: BLE001 — a doctor check must never crash doctor
        return []
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Report collection — the shared seam
# ──────────────────────────────────────────────────────────────────────────────


def _collect_sections(
    port: int | None = None, skip_deps: bool = False
) -> list[tuple[str, list[tuple[str, bool, str]]]]:
    """Run every doctor check and return the ordered ``(section, rows)`` pairs.

    Checks are looked up as module globals at call time so tests (and plugins)
    can monkeypatch individual ``check_*`` functions on this module.
    """
    secs: list[tuple[str, list[tuple[str, bool, str]]]] = [
        ("Config", check_config()),
        ("Runtime", check_runtime()),
        ("Storage", check_storage() + check_vault()),
        ("Filesystem", check_cache_dir()),
        ("Network Sockets", check_sockets(port)),
        ("Formations", check_formations()),
        ("Skills", check_skills()),
        ("Gateway", check_gateway(port=port) + check_event_processor(port=port)),
        ("AI Providers", check_ai_providers()),
        ("Wiring", check_wiring()),
    ]

    config_health_results = check_config_health()
    if config_health_results:
        secs.append(("Config Health", config_health_results))

    reachability_results = check_reachability()
    if reachability_results:  # only in lighthouse mode
        secs.append(("Reachability", reachability_results))

    ledger_results = check_ledger()
    if ledger_results:  # only when there are in-flight / interrupted operations
        secs.append(("Operations Ledger", ledger_results))

    repo_guard_results = check_repo_guard()
    if repo_guard_results:  # only inside a git repo
        secs.append(("Repo Guard", repo_guard_results))

    browser_results = check_browsers()
    if browser_results:  # only when a debug browser is actually running unowned
        secs.append(("Browsers", browser_results))

    if not skip_deps:
        secs.append(("Python Deps", check_python_deps()))
    return secs


def _shape_report(sections: list[tuple[str, list[tuple[str, bool, str]]]]) -> dict[str, Any]:
    """Shape collected sections into the machine-readable payload (the --json contract)."""
    from datetime import datetime, timezone

    from navig import __version__ as _navig_version

    passed = warnings = failed = 0
    out_sections: list[dict[str, Any]] = []
    for name, results in sections:
        checks: list[dict[str, Any]] = []
        for row in results:
            ok = bool(row[1])
            warn = (not ok) and row[0] == _WARN  # the rendered ⚠ state
            if ok:
                passed += 1
            elif warn:
                warnings += 1
            else:
                failed += 1
            checks.append(
                {
                    "label": _plain_text(getattr(row, "label", "")),
                    "ok": ok,
                    "warn": warn,
                    "detail": _plain_text(getattr(row, "detail", row[2])),
                }
            )
        out_sections.append({"name": name, "ok": all(c["ok"] for c in checks), "checks": checks})

    return {
        "ok": failed == 0 and warnings == 0,
        "sections": out_sections,
        "summary": {"passed": passed, "warnings": warnings, "failed": failed},
        "version": _navig_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def collect_report(
    port: int | None = None, skip_deps: bool = False, *, quiet: bool = False
) -> dict[str, Any]:
    """Structured health report — EXACTLY the dict ``navig doctor --json`` prints.

    The shared seam between the CLI's ``--json`` branch and programmatic
    consumers (the self-heal loop — ``navig.selfheal.doctor_remediation`` —
    and any surface rendering doctor verdicts): same process, same check
    functions, no subprocess.

    ``quiet=True`` captures anything a check narrates to stdout mid-run and
    forwards it to stderr, so a caller that owns stdout (machine mode) still
    emits clean data.
    """
    if quiet:
        import contextlib
        import io

        _buf = io.StringIO()
        with contextlib.redirect_stdout(_buf):
            sections = _collect_sections(port=port, skip_deps=skip_deps)
        if _buf.getvalue():
            sys.stderr.write(_buf.getvalue())
    else:
        sections = _collect_sections(port=port, skip_deps=skip_deps)
    return _shape_report(sections)


# ──────────────────────────────────────────────────────────────────────────────
# Main command
# ──────────────────────────────────────────────────────────────────────────────


def check_wiring() -> list[tuple[str, bool, str]]:
    """Plugin/module/command-map wiring health — the Store's doctor view.

    Covers: package-plugin lifecycle states, pip entry-point seams, module
    registry gating (stale overrides), command-map drift (entry-point commands
    missing from command_providers.json / names shadowed by the built-in map),
    and stale disabled_commands.json entries.
    """
    results: list[tuple[str, bool, str]] = []

    # Package-format plugins: lifecycle report.
    try:
        from navig.plugins.host import get_plugin_host

        tracker = get_plugin_host().tracker()
        report = tracker.report()
        failed = report.get("failed", 0)
        results.append(_check(
            "Plugin packages",
            failed == 0,
            tracker.summary_line(),
            warn=report.get("degraded", 0) > 0,
        ))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Plugin packages", False, str(exc)[:120], warn=True))

    # Pip entry-point seams: every navig.plugins/navig.commands ep must load.
    ep_commands: set[str] = set()
    try:
        from importlib.metadata import entry_points

        for group in ("navig.plugins", "navig.commands", "navig.connectors"):
            for ep in entry_points(group=group):
                if group == "navig.commands":
                    ep_commands.add(ep.name)
                try:
                    ep.load()
                except Exception as exc:  # noqa: BLE001
                    results.append(_check(f"entry-point {group}:{ep.name}", False, str(exc)[:120]))
        results.append(_check("Entry-point seams", True, f"{len(ep_commands)} plugin command(s)"))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Entry-point seams", False, str(exc)[:120], warn=True))

    # Module registry: stale user overrides (id no longer exists).
    try:
        from navig.core import Config
        from navig.modules.registry import get_registry

        modules = get_registry().discover().list_modules(include_dev=True)
        known_ids = {m["id"] for m in modules}
        overrides = Config().get("modules.overrides", default={}) or {}
        stale = [k for k in overrides if k not in known_ids] if isinstance(overrides, dict) else []
        locked = sum(1 for m in modules if m.get("locked"))
        results.append(_check(
            "Module registry",
            not stale,
            f"{len(modules)} modules · {locked} locked"
            + (f" · stale overrides: {', '.join(stale)}" if stale else ""),
            warn=bool(stale),
        ))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Module registry", False, str(exc)[:120], warn=True))

    # Command map drift + shadowing. Shadowing (a plugin command hidden behind a
    # built-in) is a real misconfiguration → fails. Drift (an entry-point command
    # absent from the release-generated map) is EXPECTED for third-party plugins
    # — the shipped map only covers first-party — so it is informational only
    # and must never flip doctor's exit code on a healthy install.
    try:
        from navig.cli.providers import _shipped_map
        from navig.cli.registration import _EXTERNAL_CMD_MAP

        shipped = set(_shipped_map().keys())
        drift = sorted(ep_commands - shipped)
        shadowed = sorted(ep_commands & set(_EXTERNAL_CMD_MAP.keys()))
        detail = f"{len(shipped)} mapped"
        if drift:
            # Informational: expected for third-party plugins (the shipped map is
            # first-party only). First-party staleness is enforced by
            # tests/cli/test_command_providers_fresh.py, not by failing here.
            detail += (f" · not in shipped map ({', '.join(drift)}) — 3rd-party is fine; "
                       "if first-party, run scripts/gen_command_providers.py")
        if shadowed:
            detail += f" · shadowed by built-ins: {', '.join(shadowed)}"
        results.append(_check("Command map", not shadowed, detail, warn=True))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Command map", False, str(exc)[:120], warn=True))

    # Stale disabled_commands.json (provider uninstalled).
    try:
        import json as _json

        f = config_dir() / "disabled_commands.json"
        if f.exists():
            mapping = _json.loads(f.read_text(encoding="utf-8"))
            from navig.plugins.host import get_plugin_host

            installed = {p.id for p in get_plugin_host().list_installed()}
            stale_cmds = sorted(c for c, pid in mapping.items() if pid not in installed)
            results.append(_check(
                "Disabled commands",
                not stale_cmds,
                f"{len(mapping)} entries"
                + (f" · stale (plugin uninstalled): {', '.join(stale_cmds)}" if stale_cmds else ""),
                warn=bool(stale_cmds),
            ))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Disabled commands", False, str(exc)[:120], warn=True))

    # Store one-liner.
    try:
        from navig.hub import store_status

        summary = store_status()
        by_state = summary["by_state"]
        results.append(_check(
            "Store",
            not summary["broken"],
            f"{by_state.get('wired', 0)} wired · {by_state.get('unwired', 0)} unwired · "
            f"{len(summary['broken'])} broken",
            warn=bool(summary["degraded"]),
        ))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("Store", False, str(exc)[:120], warn=True))

    return results


@doctor_app.callback(invoke_without_command=True)
def doctor(
    ctx: typer.Context,
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show all checks, including passing ones (--json always includes every check)",
    ),
    skip_deps: bool = typer.Option(False, "--skip-deps", help="Skip Python dependency checks"),
    port: int | None = typer.Option(
        None, "--port", help="Gateway port to probe (default: live-resolved, else config)"
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Machine-readable JSON report: ALWAYS includes every check (--verbose semantics), "
        "plain text only (no colors or glyphs), never prompts; exit code matches human mode",
    ),
    heal: bool = typer.Option(
        False,
        "--heal",
        help="Close the observe→repair loop: map failing checks to existing remediations, "
        "run the SAFE ones (report-only for anything disruptive), then re-check",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="With --heal: list what would be remediated without executing anything",
    ),
):
    """Run self-diagnostics on the NAVIG installation."""
    if ctx.invoked_subcommand is not None:
        return

    if dry_run and not heal:
        raise typer.BadParameter("--dry-run only applies with --heal")

    if heal:
        from navig.selfheal.doctor_remediation import run_heal

        raise typer.Exit(
            run_heal(port=port, skip_deps=skip_deps, dry_run=dry_run, json_output=json_output)
        )

    try:
        from rich import print as rprint  # noqa: F401
        from rich.table import Table  # noqa: F401

        console = get_console()
        _has_rich = True
    except ImportError:
        _has_rich = False
        console = None  # type: ignore[assignment]  # noqa: F841

    if json_output:
        # Machine mode: emit ONLY the JSON document — no header, no footer, no
        # prompts, no Rich. Every check from every section is included
        # (--verbose semantics); the exit code is identical to the human mode
        # (0 only when every row is ok — a ⚠ row flips it, exactly like ✗).
        # quiet=True: stdout must carry EXACTLY one JSON document — anything a
        # check narrates mid-run (e.g. ConfigManager announcing a migration)
        # is forwarded to stderr, where diagnostics belong in machine mode.
        import json as _json

        payload = collect_report(port=port, skip_deps=skip_deps, quiet=True)
        print(_json.dumps(payload, indent=2, ensure_ascii=False))
        raise typer.Exit(0 if payload["ok"] else 1)

    sections = _collect_sections(port=port, skip_deps=skip_deps)

    all_ok = True
    printed_lines: list[str] = []

    for section_name, results in sections:
        section_has_issues = any(not r[1] for r in results)
        if not verbose and not section_has_issues:
            # Summarise passing sections as one line
            printed_lines.append(f"  {_OK} {section_name}: all OK")
            continue

        printed_lines.append(f"\n  [{section_name}]")
        for _icon, ok, line in results:
            if not ok:
                all_ok = False
            printed_lines.append(line)

    # Print results
    header = "\n🩺 NAVIG Doctor\n" + ("─" * 55)
    print(header)
    for line in printed_lines:
        print(line)

    footer_icon = "✅" if all_ok else "⚠️ "
    footer = "\n" + ("─" * 55)
    if all_ok:
        footer += f"\n{footer_icon} All checks passed."
    else:
        footer += f"\n{footer_icon} Some issues found. Review items marked with ✗ or ⚠ above."
    print(footer)

    if not all_ok:
        raise typer.Exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# migrate-packs — fold the legacy ~/.navig/packs systems into plugins
# ──────────────────────────────────────────────────────────────────────────────


@doctor_app.command("migrate-packs")
def migrate_packs(
    apply: bool = typer.Option(False, "--apply", help="Actually migrate (default: dry-run)"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Migrate legacy ~/.navig/packs content into the plugin format (idempotent).

    - a dir with SKILL.md            → plugin wrapping it under skills/<id>/
    - a dir with navig.package.json  → plugin with the `navig.handler` manifest key
    Dry-run by default; --apply performs the moves. Sources are MOVED (the
    plugins dir becomes the single install root).
    """
    import json as _json
    import shutil as _shutil

    packs_dir = config_dir() / "packs"
    plugins_dir = config_dir() / "plugins"
    plan: list[dict[str, str]] = []

    if packs_dir.exists():
        for child in sorted(packs_dir.iterdir()):
            if not child.is_dir() or child.name in {"installed", "local"}:
                continue
            if (plugins_dir / child.name).exists():
                plan.append({"src": str(child), "action": "skip", "reason": "already migrated"})
                continue
            if (child / "SKILL.md").exists():
                plan.append({"src": str(child), "action": "skill-bundle", "dest": str(plugins_dir / child.name)})
            elif (child / "navig.package.json").exists():
                plan.append({"src": str(child), "action": "handler-pack", "dest": str(plugins_dir / child.name)})
            else:
                plan.append({"src": str(child), "action": "skip", "reason": "unrecognized layout"})

    if json_output:
        print(_json.dumps({"dry_run": not apply, "plan": plan}, indent=2))
    else:
        if not plan:
            print("  ✓ Nothing to migrate — ~/.navig/packs is clean.")
            return
        for step in plan:
            print(f"  {step['action']:>13}  {step['src']}" + (f" → {step.get('dest', '')}" if step.get("dest") else f"  ({step.get('reason', '')})"))
        if not apply:
            print("\n  Dry run — re-run with --apply to migrate.")

    if not apply:
        return

    migrated = 0
    for step in plan:
        if step["action"] == "skip":
            continue
        src = Path(step["src"])
        dest = Path(step["dest"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        manifest_dir = dest / ".claude-plugin"
        if step["action"] == "skill-bundle":
            # plugin wrapping the skill: content moves under skills/<id>/
            (dest / "skills").mkdir(parents=True, exist_ok=True)
            _shutil.move(str(src), str(dest / "skills" / src.name))
            manifest = {
                "name": src.name,
                "version": "1.0.0",
                "description": f"Migrated skill pack '{src.name}' (was ~/.navig/packs).",
            }
        else:  # handler-pack
            _shutil.move(str(src), str(dest))
            try:
                legacy = _json.loads((dest / "navig.package.json").read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                legacy = {}
            manifest = {
                "name": legacy.get("id", src.name),
                "version": legacy.get("version", "1.0.0"),
                "description": legacy.get("description", f"Migrated handler pack '{src.name}'."),
                "navig": {"handler": legacy.get("entry", "handler.py")},
            }
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "plugin.json").write_text(
            _json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        migrated += 1
        print(f"  ✓ migrated {src.name}")

    if migrated:
        print(f"\n  {migrated} pack(s) migrated to ~/.navig/plugins/. Legacy `navig package` reads plugins/ first.")
