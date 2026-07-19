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
from navig.core.yaml_io import atomic_write_text
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
    """Load daemon config or return defaults."""
    if _daemon_config_path().exists():
        try:
            payload = json.loads(_daemon_config_path().read_text(encoding="utf-8"))
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
            payload = json.loads(_daemon_config_path().read_text(encoding="utf-8"))
            should_repair = not isinstance(payload, dict)
        except (json.JSONDecodeError, OSError):
            should_repair = True

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
