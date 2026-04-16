"""
NAVIG Core Module

Core DevOps functionality with no optional dependencies.
Provides the stable foundation of NAVIG: host management, application
management, context, execution settings, YAML I/O, security, and the
event-driven hook system.

Command Groups exposed via CLI:
    host, app, tunnel, db, remote, security, monitoring,
    web, docker, backup, files

Core Utilities:
    security — sensitive-data redaction, env-var substitution, command safety
    hooks    — event-driven hook system for extensibility
"""

from __future__ import annotations

from navig.core.apps import AppManager
from navig.core.context import ContextManager
from navig.core.execution import ExecutionSettings
from navig.core.hosts import HostManager
from navig.core.shared_config import Config
from navig.core.yaml_io import atomic_write_yaml, log_shadow_anomaly

# ---------------------------------------------------------------------------
# Lazy accessors — keep optional heavy imports out of the hot import path
# ---------------------------------------------------------------------------


def get_security() -> dict:
    """Return the security-module public API as a dict.

    Lazy import avoids the regex-compilation cost at startup when security
    functions are not needed.
    """
    from navig.core.security import (
        redact_sensitive_text,
        run_security_audit,
        substitute_env_vars,
        validate_command_safety,
    )

    return {
        "redact_sensitive_text": redact_sensitive_text,
        "substitute_env_vars": substitute_env_vars,
        "validate_command_safety": validate_command_safety,
        "run_security_audit": run_security_audit,
    }


def get_hooks() -> dict:
    """Return the hook-system public API as a dict."""
    from navig.core.hooks import (
        HookEvent,
        register_hook,
        trigger_hook,
        trigger_hook_sync,
    )

    return {
        "register_hook": register_hook,
        "trigger_hook": trigger_hook,
        "trigger_hook_sync": trigger_hook_sync,
        "HookEvent": HookEvent,
    }


__all__ = [
    # Core configuration
    "Config",
    # Manager classes
    "HostManager",
    "AppManager",
    "ContextManager",
    "ExecutionSettings",
    # YAML utilities
    "atomic_write_yaml",
    "log_shadow_anomaly",
    # Lazy accessor functions
    "get_security",
    "get_hooks",
]
