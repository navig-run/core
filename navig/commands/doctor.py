"""
navig doctor — Self-diagnostics (P1-15)

Reports on NAVIG installation health without mutating any state.
Checks: config, cache, formations, skills, gateway, API keys.
"""

from __future__ import annotations

import importlib
import logging
import os
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


def _check(
    label: str,
    ok: bool,
    detail: str = "",
    warn: bool = False,
) -> tuple[str, bool, str]:
    """Return a formatted result tuple."""
    if ok:
        icon = _OK
    elif warn:
        icon = _WARN
    else:
        icon = _ERR
    return icon, ok, f"  {icon} {label}" + (f": {detail}" if detail else "")


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


def check_sockets(target_port: int = _GATEWAY_PORT) -> list[tuple[str, bool, str]]:
    """Check if critical ports are available or correctly bound."""
    results = []

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
                results.append(
                    _check(
                        "Port Occupation",
                        True,
                        f"Port {target_port} is available for binding",
                    )
                )
    except Exception as e:
        results.append(_check("Port Occupation", False, f"Socket error on port {target_port}: {e}"))

    return results


def check_formations() -> list[tuple[str, bool, str]]:
    """Check formations dir: count + parse errors."""
    results = []
    formations_dir = config_dir() / "formations"
    total, errors = _count_yaml_files(formations_dir)

    if total == 0:
        results.append(
            _check(
                "Formations",
                True,
                "0 installed (built-in defaults will be used)",
                warn=True,
            )
        )
    elif errors:
        results.append(_check("Formations", False, f"{total} found, {errors} invalid"))
    else:
        results.append(_check("Formations", True, f"{total} found, 0 invalid"))

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


def check_gateway(port: int = _GATEWAY_PORT) -> list[tuple[str, bool, str]]:
    """Check if gateway is running on the configured port."""
    results = []

    # Try to read port from config
    try:
        import yaml

        cfg_path = config_dir() / "config.yaml"
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8", errors="replace")) or {}
            port = cfg.get("gateway", {}).get("port", port)
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

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

    return results


def check_env_keys() -> list[tuple[str, bool, str]]:
    """Check important environment variables / config values."""
    results = []

    # Try to read from actual config for more accurate reporting
    cfg: dict[str, Any] = {}
    try:
        import yaml

        cfg_path = config_dir() / "config.yaml"
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8", errors="replace")) or {}
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    key_checks = [
        ("OPENROUTER_API_KEY", "openrouter_api_key", "ai commands degraded without it"),
        ("OPENAI_API_KEY", "openai_api_key", "some models unavailable"),
        ("ANTHROPIC_API_KEY", "anthropic_api_key", "Claude models unavailable"),
    ]

    for env_var, cfg_key, impact in key_checks:
        val = os.environ.get(env_var, "") or cfg.get(cfg_key, "")
        if val and val.strip() and val.strip() not in ("<your-key>", ""):
            results.append(_check(env_var, True, "set"))
        else:
            results.append(_check(env_var, False, impact, warn=True))

    # Mesh token
    mesh_token_path = config_dir() / "cache" / "mesh_token"
    has_token = mesh_token_path.exists() and mesh_token_path.stat().st_size > 0
    if has_token:
        results.append(_check("MESH_TOKEN", True, f"present ({mesh_token_path})"))
    else:
        results.append(
            _check("MESH_TOKEN", False, "not found (generated on gateway start)", warn=True)
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


# ──────────────────────────────────────────────────────────────────────────────
# Main command
# ──────────────────────────────────────────────────────────────────────────────


@doctor_app.callback(invoke_without_command=True)
def doctor(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show all checks, including passing ones"
    ),
    skip_deps: bool = typer.Option(False, "--skip-deps", help="Skip Python dependency checks"),
    port: int = typer.Option(_GATEWAY_PORT, "--port", help="Gateway port to probe"),
):
    """Run self-diagnostics on the NAVIG installation."""

    try:
        from rich import print as rprint  # noqa: F401
        from rich.table import Table  # noqa: F401

        console = get_console()
        _has_rich = True
    except ImportError:
        _has_rich = False
        console = None  # type: ignore[assignment]  # noqa: F841

    sections: list[tuple[str, list[tuple[str, bool, str]]]] = [
        ("Config", check_config()),
        ("Runtime", check_runtime()),
        ("Storage", check_storage()),
        ("Filesystem", check_cache_dir()),
        ("Network Sockets", check_sockets(port)),
        ("Formations", check_formations()),
        ("Skills", check_skills()),
        ("Gateway", check_gateway(port=port)),
        ("API Keys", check_env_keys()),
    ]

    if not skip_deps:
        sections.append(("Python Deps", check_python_deps()))

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
