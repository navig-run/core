"""
navig.core.execution — Execution settings management.

This module provides the ExecutionSettings class for managing execution mode
and confirmation level preferences. Part of the config.py decomposition (PR5/6).

Settings Resolution:
1. Project-local config (.navig/config.yaml) takes precedence
2. Falls back to global config (~/.navig/config.yaml)
3. Defaults: mode='interactive', confirmation_level='standard'

Usage:
    from navig.core.execution import ExecutionSettings
    
    # Via ConfigManager (facade)
    cfg = ConfigManager()
    mode = cfg.get_execution_mode()
    cfg.set_execution_mode('auto')
    
    # Direct use (with provider)
    settings = ExecutionSettings(provider)
    mode = settings.get_mode()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Valid execution modes
VALID_MODES = ["interactive", "auto"]

# Valid confirmation levels
VALID_CONFIRMATION_LEVELS = ["critical", "standard", "verbose"]


class ExecutionConfigProvider(Protocol):
    """Protocol for execution settings config provider (duck-typed dependency injection)."""
    
    @property
    def global_config(self) -> dict[str, Any]:
        """Global NAVIG configuration dictionary."""
        ...
    
    def _save_global_config(self, config: dict[str, Any]) -> None:
        """Save global configuration to disk."""
        ...


class ExecutionSettings:
    """
    Execution mode and confirmation level management.
    
    This class manages execution behavior preferences:
    - Execution mode: 'interactive' (prompts) or 'auto' (non-interactive)
    - Confirmation level: 'critical', 'standard', or 'verbose'
    
    Settings are resolved hierarchically:
    1. Project-local config (.navig/config.yaml)
    2. Global config (~/.navig/config.yaml)
    3. Built-in defaults
    
    Args:
        provider: Config provider implementing ExecutionConfigProvider protocol
    """
    
    def __init__(self, provider: ExecutionConfigProvider):
        self._provider = provider
    
    def get_mode(self) -> str:
        """
        Get the current execution mode.
        
        Checks project-local config first, then falls back to global config.
        
        Returns:
            'interactive' (default) or 'auto'
        """
        # Check project-local config first
        local_config_file = Path.cwd() / ".navig" / "config.yaml"
        if local_config_file.exists():
            try:
                with open(local_config_file, encoding="utf-8") as f:
                    local_config = yaml.safe_load(f) or {}
                    execution = local_config.get("execution", {})
                    if "mode" in execution:
                        return execution["mode"]
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        
        # Fall back to global config
        execution = self._provider.global_config.get("execution", {})
        return execution.get("mode", "interactive")
    
    def set_mode(self, mode: str) -> None:
        """
        Set the execution mode.
        
        Args:
            mode: 'interactive' or 'auto'
        
        Raises:
            ValueError: If mode is not valid
        """
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode '{mode}'. Must be one of: {', '.join(VALID_MODES)}")
        
        config = self._provider.global_config
        if "execution" not in config:
            config["execution"] = {}
        config["execution"]["mode"] = mode
        self._provider._save_global_config(config)
    
    def get_confirmation_level(self) -> str:
        """
        Get the current confirmation level.
        
        Checks project-local config first, then falls back to global config.
        
        Returns:
            'critical', 'standard' (default), or 'verbose'
        """
        # Check project-local config first
        local_config_file = Path.cwd() / ".navig" / "config.yaml"
        if local_config_file.exists():
            try:
                with open(local_config_file, encoding="utf-8") as f:
                    local_config = yaml.safe_load(f) or {}
                    execution = local_config.get("execution", {})
                    if "confirmation_level" in execution:
                        return execution["confirmation_level"]
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        
        # Fall back to global config
        execution = self._provider.global_config.get("execution", {})
        return execution.get("confirmation_level", "standard")
    
    def set_confirmation_level(self, level: str) -> None:
        """
        Set the confirmation level.
        
        Args:
            level: 'critical', 'standard', or 'verbose'
        
        Raises:
            ValueError: If level is not valid
        """
        if level not in VALID_CONFIRMATION_LEVELS:
            raise ValueError(
                f"Invalid level '{level}'. Must be one of: {', '.join(VALID_CONFIRMATION_LEVELS)}"
            )
        
        config = self._provider.global_config
        if "execution" not in config:
            config["execution"] = {}
        config["execution"]["confirmation_level"] = level
        self._provider._save_global_config(config)
    
    def get_settings(self) -> dict[str, str]:
        """
        Get all execution settings.
        
        Returns:
            Dict with 'mode' and 'confirmation_level' keys
        """
        return {
            "mode": self.get_mode(),
            "confirmation_level": self.get_confirmation_level(),
        }
