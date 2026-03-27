"""
NAVIG Core Module

Core DevOps functionality with no optional dependencies.
This module contains essential commands that form the stable foundation of NAVIG.

Command Groups:
- host: Host management
- app: Application management
- tunnel: SSH tunnel management
- db: Database operations
- remote: Remote command execution
- security: Security operations
- monitoring: Server monitoring
- web: Web server management
- docker: Container management
- backup: Backup operations
- files: File operations

Core Utilities (Agent-inspired):
- security: Sensitive data redaction, env var substitution, command safety
- hooks: Event-driven hook system for extensibility
"""

from navig.core.shared_config import Config


# Lazy imports for optional modules
def get_security():
    """Get security module (redaction, env vars, auditing)."""
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


def get_hooks():
    """Get hooks module (event system)."""
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


__all__ = ["Config", "get_security", "get_hooks"]
