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
import os
from pathlib import Path

from navig.debug_logger import get_debug_logger
from navig.platform import paths

NAVIG_HOME = paths.config_dir()
DAEMON_CONFIG = NAVIG_HOME / "daemon" / "config.json"

DEFAULT_DAEMON_CONFIG = {
    "telegram_bot": True,
    "gateway": False,
    "gateway_port": 8789,
    "scheduler": False,
    "health_port": 0,
    "engagement": True,
}

logger = get_debug_logger()


def _as_bool(value: object, default: bool) -> bool:
    """Coerce common JSON/env-style truthy/falsey values to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


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
    DAEMON_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = DAEMON_CONFIG.with_suffix(DAEMON_CONFIG.suffix + ".tmp")
    atomic_write_text(tmp_path, json.dumps(config, indent=2))
    os.replace(tmp_path, DAEMON_CONFIG)


def _load_config() -> dict:
    """Load daemon config or return defaults."""
    if DAEMON_CONFIG.exists():
        try:
            payload = json.loads(DAEMON_CONFIG.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
            logger.warning(
                "Daemon config %s has invalid root type %s; using defaults",
                DAEMON_CONFIG,
                type(payload).__name__,
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read daemon config %s: %s", DAEMON_CONFIG, exc)
    return DEFAULT_DAEMON_CONFIG.copy()


def save_default_config() -> Path:
    """Ensure daemon config exists and is valid JSON object; repair if malformed."""
    DAEMON_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    should_repair = not DAEMON_CONFIG.exists()
    if not should_repair:
        try:
            payload = json.loads(DAEMON_CONFIG.read_text(encoding="utf-8"))
            should_repair = not isinstance(payload, dict)
        except (json.JSONDecodeError, OSError):
            should_repair = True

    if should_repair:
        _write_config_atomic(DEFAULT_DAEMON_CONFIG.copy())
    return DAEMON_CONFIG


def main() -> None:
    # Respect stop-intent flag written by `navig service stop`.
    # Any external watcher (tray app, startup script, RestartOnFailure) that
    # tries to spawn the daemon after a deliberate stop will hit this guard
    # and exit immediately — keeping the daemon truly stopped until a
    # deliberate `navig service start` clears the flag.
    try:
        from navig.daemon.service_manager import stop_flag_is_set
        if stop_flag_is_set():
            logger.info(
                "Stop-intent flag is set (%s) — daemon start suppressed. "
                "Run `navig service start` to clear the flag and restart.",
                "~/.navig/daemon/stop_requested",
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
            NAVIG_HOME / ".env",
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
        daemon.add_gateway(port=_as_int(cfg.get("gateway_port", 8789), 8789))

    if _as_bool(cfg.get("scheduler", False), False):
        daemon.add_scheduler()

    daemon.run()


if __name__ == "__main__":
    main()
