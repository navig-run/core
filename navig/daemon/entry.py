"""
NAVIG Daemon Entry Point

Launched by the service manager (NSSM / Task Scheduler / manual).
Reads configuration from ~/.navig/daemon/config.json and starts
the supervisor with the appropriate subsystems.

Usage:
    python -m navig.daemon.entry
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from navig._daemon_defaults import _GATEWAY_PORT
from navig.core.yaml_io import atomic_write_text, read_text_retrying
from navig.platform import paths

# Test seam — when ``None`` (the normal state), ``_daemon_config_path()``
# resolves at CALL time so NAVIG_CONFIG_DIR isolation set after import still
# applies (see navig/vault/migrate.py:_legacy_db_path).
DAEMON_CONFIG: Path | None = None


def _daemon_config_path() -> Path:
    """Resolve the daemon config path at CALL time (honours the test seam)."""
    return DAEMON_CONFIG if DAEMON_CONFIG is not None else paths.config_dir() / "daemon" / "config.json"

DEFAULT_DAEMON_CONFIG = {
    "telegram_bot": True,
    "gateway": False,
    "gateway_port": _GATEWAY_PORT,
    "scheduler": False,
    "health_port": 0,
    "engagement": True,
}

# Use a lightweight standard logger at module level so that importing
# navig.daemon.entry does NOT open a RotatingFileHandler on debug.log.
# The DebugLogger file handle is opened only when the daemon actually
# starts running (inside main() -> NavigDaemon.run()).
logger: logging.Logger = logging.getLogger("navig.daemon.entry")


def _as_bool(value: object, default: bool) -> bool:
    """Coerce common JSON/env-style truthy/falsey values to bool.

    Thin wrapper over the canonical :func:`navig.core.coerce.coerce_bool` so every
    subsystem shares ONE truth table (true/false/1/0/yes/no/on/off/t/y/f/n,
    unknown→default).
    """
    from navig.core.coerce import coerce_bool

    return coerce_bool(value, default)


def _as_int(value: object, default: int) -> int:
    """Coerce common JSON/env-style numeric values to int."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return default
        try:
            return int(normalized, 10)
        except ValueError:
            return default
    return default


def _write_config_atomic(config: dict) -> None:
    """Persist daemon config using atomic replace to avoid partial writes."""
    _daemon_config_path().parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _daemon_config_path().with_suffix(_daemon_config_path().suffix + ".tmp")
    atomic_write_text(tmp_path, json.dumps(config, indent=2))
    os.replace(tmp_path, _daemon_config_path())


def _load_config() -> dict:
    """Load daemon config or return defaults.

    Reads through ``read_text_retrying`` so a transient OS lock (an antivirus/backup
    agent, a read landing mid-``os.replace``) doesn't collapse to defaults at daemon
    boot — which would silently run this session with the DEFAULT feature toggles
    (gateway/scheduler/ports), ignoring the operator's config. A lock that survives the
    retries still degrades to defaults; genuine corruption is not retried.
    """
    if _daemon_config_path().exists():
        try:
            payload = json.loads(read_text_retrying(_daemon_config_path()))
            if isinstance(payload, dict):
                return payload
            logger.warning(
                "Daemon config %s has invalid root type %s; using defaults",
                _daemon_config_path(),
                type(payload).__name__,
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read daemon config %s: %s", _daemon_config_path(), exc)
    return DEFAULT_DAEMON_CONFIG.copy()


def save_default_config() -> Path:
    """Ensure daemon config exists and is valid JSON object; repair if malformed."""
    _daemon_config_path().parent.mkdir(parents=True, exist_ok=True)
    should_repair = not _daemon_config_path().exists()
    if not should_repair:
        try:
            payload = json.loads(read_text_retrying(_daemon_config_path()))
            should_repair = not isinstance(payload, dict)
        except json.JSONDecodeError:
            # Genuinely corrupt. Writes are atomic (temp + os.replace), so this is never
            # a half-written file we caught mid-flight — repair to defaults is safe.
            should_repair = True
        except OSError:
            # A transient read failure (an antivirus/backup agent holding the file, a read
            # landing mid-replace) — NOT corruption. Overwriting with defaults here would
            # silently reset the daemon's feature toggles (gateway/scheduler/ports). We
            # can't verify it right now, so leave the existing config untouched.
            should_repair = False

    if should_repair:
        _write_config_atomic(DEFAULT_DAEMON_CONFIG.copy())
    return _daemon_config_path()


def main() -> None:
    # Respect stop-intent flag written by `navig service stop`.
    # Any external watcher (tray app, startup script, RestartOnFailure) that
    # tries to spawn the daemon after a deliberate stop will hit this guard
    # and exit immediately — keeping the daemon truly stopped until a
    # deliberate `navig service start` clears the flag.
    try:
        from navig.daemon.service_manager import stop_flag_is_set, watchdog_deadline_active
        if stop_flag_is_set():
            logger.info(
                "Stop-intent flag is set (%s) — daemon start suppressed. "
                "Run `navig service start` to clear the flag and restart.",
                "~/.navig/daemon/stop_requested",
            )
            return
        if watchdog_deadline_active():
            logger.info(
                "Stop-watchdog deadline is active (%s) — daemon start suppressed. "
                "Wait for the watchdog window to expire or run `navig service start`.",
                "~/.navig/daemon/stop_watchdog_deadline",
            )
            return
    except Exception:  # noqa: BLE001
        pass  # If anything goes wrong checking the flag, proceed normally.

    # Already running? Then this launch is a duplicate — exit cleanly.
    #
    # THIS ENTRY POINT is what the Windows scheduled task runs
    # (`runpy.run_module('navig.daemon.entry')`, see service_manager
    # ._task_bootstrap_args) and what any tray/startup script reaches for. Until
    # now only the CLI path (`navig service start` -> NavigDaemon.start) asked
    # this question, so every OTHER launcher started a daemon unconditionally.
    #
    # Measured: with supervisor 8732 healthy and serving, two further launches
    # produced supervisors 8968 and 75516 and daemon/state.json showed the
    # NEWCOMER had taken over the pid file -- three supervisors, two gateways.
    # This guard is the PRECONDITION for the autostart task's repeating watchdog
    # trigger (#1180): a watchdog that re-runs this entry every few minutes would
    # multiply daemons without it. The trigger was re-added on 2026-09-04 once
    # this held and was measured holding; if you ever remove this guard, remove
    # the repeating trigger in service_manager._schtasks_xml in the SAME change.
    # tests/daemon/test_autostart_watchdog.py pins the pair together.
    #
    # Returning (not raising) matters: the launcher must see a SUCCESSFUL exit,
    # or Task Scheduler records a failure for what is the correct outcome.
    try:
        from navig.daemon.supervisor import NavigDaemon as _Daemon

        if _Daemon.is_running():
            logger.info(
                "Daemon already running (pid=%s) — this launch is a duplicate, exiting. "
                "Use `navig service stop` first if you meant to replace it.",
                _Daemon.read_pid(),
            )
            return
    except Exception:  # noqa: BLE001
        # Never let the guard itself stop a legitimate start: an unreadable pid
        # file must fall through to starting, not to refusing.
        pass

    # Load .env if available (for TELEGRAM_BOT_TOKEN etc.)
    try:
        from dotenv import load_dotenv

        # Try multiple locations: cwd, project root (relative to this file), ~/.navig
        project_root = Path(__file__).resolve().parent.parent.parent
        candidates = [
            Path.cwd() / ".env",
            project_root / ".env",
            paths.config_dir() / ".env",
        ]
        for candidate in candidates:
            if candidate.exists():
                load_dotenv(candidate)
                break
    except ImportError:
        pass  # optional dependency not installed; feature disabled

    cfg = _load_config()

    from navig.daemon.supervisor import NavigDaemon

    daemon = NavigDaemon(health_port=_as_int(cfg.get("health_port", 0), 0))

    if _as_bool(cfg.get("telegram_bot", True), True):
        # Allow bot_script override
        bot_path = cfg.get("bot_script")
        daemon.add_telegram_bot(
            bot_script=Path(bot_path) if bot_path else None,
        )

    if _as_bool(cfg.get("gateway", False), False):
        daemon.add_gateway(port=_as_int(cfg.get("gateway_port", _GATEWAY_PORT), _GATEWAY_PORT))

    if _as_bool(cfg.get("scheduler", False), False):
        daemon.add_scheduler()

    daemon.run()


if __name__ == "__main__":
    main()
