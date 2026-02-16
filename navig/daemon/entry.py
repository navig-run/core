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
import sys
from pathlib import Path

NAVIG_HOME = Path.home() / ".navig"
DAEMON_CONFIG = NAVIG_HOME / "daemon" / "config.json"


def _load_config() -> dict:
    """Load daemon config or return defaults."""
    if DAEMON_CONFIG.exists():
        try:
            return json.loads(DAEMON_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "telegram_bot": True,
        "gateway": False,
        "gateway_port": 8765,
        "scheduler": False,
        "health_port": 0,
        "engagement": True,
    }


def save_default_config() -> Path:
    """Write the default daemon config if it doesn't exist."""
    DAEMON_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if not DAEMON_CONFIG.exists():
        DAEMON_CONFIG.write_text(
            json.dumps(_load_config(), indent=2), encoding="utf-8"
        )
    return DAEMON_CONFIG


def main() -> None:
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
        pass

    cfg = _load_config()

    from navig.daemon.supervisor import NavigDaemon

    daemon = NavigDaemon(health_port=cfg.get("health_port", 0))

    if cfg.get("telegram_bot", True):
        # Allow bot_script override
        bot_path = cfg.get("bot_script")
        daemon.add_telegram_bot(
            bot_script=Path(bot_path) if bot_path else None,
        )

    if cfg.get("gateway", False):
        daemon.add_gateway(port=cfg.get("gateway_port", 8765))

    if cfg.get("scheduler", False):
        daemon.add_scheduler()

    daemon.run()


if __name__ == "__main__":
    main()
