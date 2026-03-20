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
from typing import Any, Dict

from navig import console_helper as ch
from navig.assistant_utils import ensure_navig_directory
from navig.config import ConfigManager


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
        self.ai_context_dir = self.navig_dir / 'ai_context'

        # Load assistant configuration
        self.assistant_config = self._load_assistant_config()

        # Initialize modules (lazy loading)
        self._auto_detection = None
        self._proactive_display = None
        self._error_resolution = None
        self._context_generator = None

    def _load_assistant_config(self) -> Dict[str, Any]:
        """Load assistant configuration from config.yaml."""
        global_config = self.config.global_config

        # Default configuration
        default_config = {
            'enabled': True,
            'suggestion_level': 'normal',  # minimal | normal | verbose
            'auto_analysis': True,
            'confirmation_required': True,
            'monitoring_interval_seconds': 300,
            'max_history_entries': 1000,
            'thresholds': {
                'cpu_warning': 80,
                'cpu_critical': 95,
                'memory_warning': 80,
                'memory_critical': 95,
                'disk_warning': 80,
                'disk_critical': 90
            },
            'log_paths': {
                'nginx': '/var/log/nginx/error.log',
                'mysql': '/var/log/mysql/error.log'
            }
        }

        # Merge with user configuration if exists
        if 'proactive_assistant' in global_config:
            user_config = global_config['proactive_assistant']
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
            from navig.modules.auto_detection import AutoDetection
            self._auto_detection = AutoDetection(self)
        return self._auto_detection

    @property
    def proactive_display(self):
        """Lazy load proactive display module."""
        if self._proactive_display is None:
            from navig.modules.proactive_display import ProactiveDisplay
            self._proactive_display = ProactiveDisplay(self)
        return self._proactive_display

    @property
    def error_resolution(self):
        """Lazy load error resolution module."""
        if self._error_resolution is None:
            from navig.modules.error_resolution import ErrorResolution
            self._error_resolution = ErrorResolution(self)
        return self._error_resolution

    @property
    def context_generator(self):
        """Lazy load context generator module."""
        if self._context_generator is None:
            from navig.modules.context_generator import ContextGenerator
            self._context_generator = ContextGenerator(self)
        return self._context_generator

    def is_enabled(self) -> bool:
        """Check if assistant is enabled."""
        return self.assistant_config.get('enabled', True)

    def get_suggestion_level(self) -> str:
        """Get current suggestion level (minimal/normal/verbose)."""
        return self.assistant_config.get('suggestion_level', 'normal')

    def should_auto_analyze(self) -> bool:
        """Check if automatic analysis is enabled."""
        return self.assistant_config.get('auto_analysis', True)

    def requires_confirmation(self) -> bool:
        """Check if high-risk operations require confirmation."""
        return self.assistant_config.get('confirmation_required', True)

    def log_audit(self, action: str, details: Dict[str, Any]):
        """
        Log assistant action to audit log.
        
        Args:
            action: Action type (e.g., 'suggestion_shown', 'error_analyzed')
            details: Additional details about the action
        """
        audit_file = self.ai_context_dir / 'assistant_audit.log'

        try:
            timestamp = datetime.now().isoformat()
            log_entry = f"[{timestamp}] {action}: {json.dumps(details)}\n"

            with open(audit_file, 'a') as f:
                f.write(log_entry)

        except Exception as e:
            ch.dim(f"Could not write to audit log: {e}")

