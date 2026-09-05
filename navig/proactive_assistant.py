"""
Proactive Assistant Core

Coordinates all four modules of the intelligent AI assistant system:
1. Auto-Detection & Analysis
2. Proactive Information Display
3. Intelligent Error Resolution
4. AI Copilot Integration
"""

import json
from datetime import datetime
from typing import Any

from navig import console_helper as ch
from navig.assistant_utils import ensure_navig_directory
from navig.config import ConfigManager
from navig.core.coerce import coerce_bool


class ProactiveAssistant:
    """
    Main coordinator for the proactive assistant system.

    Integrates all four modules and provides a unified interface.
    """

    def __init__(self, config_manager: ConfigManager):
        """
        Initialize the proactive assistant.

        Args:
            config_manager: NAVIG configuration manager instance
        """
        self.config = config_manager
        self.navig_dir = ensure_navig_directory()
        self.ai_context_dir = self.navig_dir / "ai_context"

        # Load assistant configuration
        self.assistant_config = self._load_assistant_config()

        # Initialize modules (lazy loading)
        self._auto_detection = None
        self._proactive_display = None
        self._error_resolution = None
        self._context_generator = None

    def _load_assistant_config(self) -> dict[str, Any]:
        """Load assistant configuration from config.yaml."""
        global_config = self.config.global_config

        # Default configuration
        default_config = {
            "enabled": True,
            "suggestion_level": "normal",  # minimal | normal | verbose
            "auto_analysis": True,
            "confirmation_required": True,
            "monitoring_interval_seconds": 300,
            "max_history_entries": 1000,
            "thresholds": {
                "cpu_warning": 80,
                "cpu_critical": 95,
                "memory_warning": 80,
                "memory_critical": 95,
                "disk_warning": 80,
                "disk_critical": 90,
            },
            "log_paths": {
                "nginx": "/var/log/nginx/error.log",
                "mysql": "/var/log/mysql/error.log",
            },
        }

        # Merge with user configuration if exists
        if "proactive_assistant" in global_config:
            user_config = global_config["proactive_assistant"]
            # Deep merge
            for key, value in user_config.items():
                if isinstance(value, dict) and key in default_config:
                    default_config[key].update(value)
                else:
                    default_config[key] = value

        return default_config

    @property
    def auto_detection(self):
        """Lazy load auto-detection module."""
        if self._auto_detection is None:
            from navig.proactive.auto_detection import AutoDetection

            self._auto_detection = AutoDetection(self)
        return self._auto_detection

    @property
    def proactive_display(self):
        """Lazy load proactive display module."""
        if self._proactive_display is None:
            from navig.proactive.proactive_display import ProactiveDisplay

            self._proactive_display = ProactiveDisplay(self)
        return self._proactive_display

    @property
    def error_resolution(self):
        """Lazy load error resolution module."""
        if self._error_resolution is None:
            from navig.proactive.error_resolution import ErrorResolution

            self._error_resolution = ErrorResolution(self)
        return self._error_resolution

    @property
    def context_generator(self):
        """Lazy load context generator module."""
        if self._context_generator is None:
            from navig.proactive.context_generator import ContextGenerator

            self._context_generator = ContextGenerator(self)
        return self._context_generator

    def is_enabled(self) -> bool:
        """Check if assistant is enabled."""
        return coerce_bool(self.assistant_config.get("enabled", True), default=True)

    def get_suggestion_level(self) -> str:
        """Get current suggestion level (minimal/normal/verbose)."""
        return self.assistant_config.get("suggestion_level", "normal")

    def should_auto_analyze(self) -> bool:
        """Whether a failed command is analysed automatically.

        Set it with ``navig config set proactive_assistant.auto_analysis false``.

        coerce_bool, not a raw read: `navig config set` stores its argument as a
        STRING, and ``bool("false")`` is True — so this returned True for every value
        an operator could type, and the setting could not be turned off. Gates the
        auto-analysis in ``assistant_hooks`` and ``proactive/auto_detection``.
        """
        return coerce_bool(self.assistant_config.get("auto_analysis", True), default=True)

    def requires_confirmation(self) -> bool:
        """Whether a destructive operation must be confirmed.

        Set it with ``navig config set proactive_assistant.confirmation_required false``.

        Same string-vs-bool trap as above. The polarity here is fail-SAFE — an
        uncoerced ``"false"`` kept the confirmation ON — so nothing was ever
        unguarded; the setting was simply inert, and an operator who turned it off
        kept being prompted with no way to tell why. Gates the destructive-operation
        prompt in ``proactive/proactive_display.check_pre_execution_warnings``.
        """
        return coerce_bool(
            self.assistant_config.get("confirmation_required", True), default=True
        )

    def log_audit(self, action: str, details: dict[str, Any]):
        """
        Log assistant action to audit log.

        Args:
            action: Action type (e.g., 'suggestion_shown', 'error_analyzed')
            details: Additional details about the action
        """
        audit_file = self.ai_context_dir / "assistant_audit.log"

        try:
            timestamp = datetime.now().isoformat()
            log_entry = f"[{timestamp}] {action}: {json.dumps(details)}\n"

            with open(audit_file, "a", encoding="utf-8") as f:
                f.write(log_entry)

        except Exception as e:
            # ch.dim before — the quietest sink there is, for the loss of an AUDIT
            # line. An audit trail whose gaps are invisible is worse than no trail:
            # it reads as a complete record of what the assistant did.
            ch.warning(
                f"Assistant audit log NOT written ({action}): {e}\n"
                f"  This action is missing from {audit_file} — the trail has a gap, "
                "not a record."
            )
