"""
Matrix Feature Toggle System

Controls which Matrix capabilities are enabled at runtime.
Features are configured in ~/.navig/config.yaml under comms.matrix.features.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

from navig.core.coerce import coerce_bool

logger = logging.getLogger(__name__)

# Feature defaults — safe-by-default philosophy
MATRIX_FEATURE_DEFAULTS: dict[str, bool] = {
    "messaging": True,  # send / read / tail commands
    "room_management": True,  # create / join / leave / invite rooms
    "admin_ops": False,  # user management, server admin (dangerous)
    "notifications": True,  # bridge into NAVIG notification pipeline
    "registration_control": False,  # toggle open/closed registration (admin)
    "file_sharing": False,  # upload / download files (Phase 2)
    "e2ee": False,  # end-to-end encryption (Phase 3)
}

FEATURE_DESCRIPTIONS: dict[str, str] = {
    "messaging": "Send/read/tail messages",
    "room_management": "Create/join/leave/invite rooms",
    "admin_ops": "User management, server admin",
    "notifications": "NAVIG notification pipeline bridge",
    "registration_control": "Toggle open/closed registration",
    "file_sharing": "Upload/download files",
    "e2ee": "End-to-end encryption",
}


def _get_matrix_features_config() -> dict[str, Any]:
    """Load the features block from config, falling back to defaults."""
    try:
        from navig.config import get_config_manager

        cfg = get_config_manager().get_global_config()
        return cfg.get("comms", {}).get("matrix", {}).get("features", {})
    except Exception:
        return {}


def is_matrix_enabled() -> bool:
    """Check if Matrix is enabled at all."""
    try:
        from navig.config import get_config_manager

        cfg = get_config_manager().get_global_config()
        return coerce_bool(
            cfg.get("comms", {}).get("matrix", {}).get("enabled"), default=False
        )
    except Exception:
        return False


def is_feature_enabled(feature: str) -> bool:
    """
    Check if a specific Matrix feature is enabled.

    Uses config value if set, otherwise falls back to MATRIX_FEATURE_DEFAULTS.

    coerce_bool, not a raw read. These values come straight from ``config.yaml``,
    where ``navig config set`` stores its argument as a STRING — and ``bool("false")``
    is True. The gates that default to *False* are the dangerous ones
    (``admin_ops``, ``registration_control``, ``file_sharing``), so an operator who
    ran the command this module's own error message prints —

        navig config set comms.matrix.features.admin_ops false

    — turned the gate ON. Coercion only ever moves that the safe way: a
    ``"false"``-ish string now closes a gate it used to open, and every truthy
    spelling still opens it exactly as before.
    """
    features = _get_matrix_features_config()
    default = MATRIX_FEATURE_DEFAULTS.get(feature, False)
    return coerce_bool(features.get(feature, default), default=default)


def get_all_features() -> dict[str, bool]:
    """Return a dict of all features with their resolved on/off state.

    Same coercion as :func:`is_feature_enabled`, and for the same reason twice over:
    this backs ``navig matrix features``, so an uncoerced string made the listing
    disagree with the gate it is supposed to describe.
    """
    features = _get_matrix_features_config()
    return {
        key: coerce_bool(features.get(key, default), default=default)
        for key, default in MATRIX_FEATURE_DEFAULTS.items()
    }


def require_feature(feature: str):
    """
    Decorator: abort CLI command if a Matrix feature is disabled.

    Usage::

        @matrix_app.command("send")
        @require_feature("messaging")
        def send_message(room: str, message: str):
            ...
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not is_feature_enabled(feature):
                try:
                    from rich.console import Console

                    console = Console(stderr=True)
                    console.print(f"[red]✗[/] Matrix feature '{feature}' is disabled.")
                    console.print(
                        f"  Enable: [cyan]navig config set comms.matrix.features.{feature} true[/]"
                    )
                except ImportError:
                    print(f"✗ Matrix feature '{feature}' is disabled.")
                    print(f"  Enable: navig config set comms.matrix.features.{feature} true")
                import typer

                raise typer.Exit(1)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def require_matrix():
    """
    Decorator: abort CLI command if Matrix is not enabled at all.

    Usage::

        @matrix_app.command("status")
        @require_matrix()
        def status():
            ...
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not is_matrix_enabled():
                try:
                    from rich.console import Console

                    console = Console(stderr=True)
                    console.print("[red]✗[/] Matrix is not enabled.")
                    console.print("  Enable: [cyan]navig config set comms.matrix.enabled true[/]")
                except ImportError:
                    print("✗ Matrix is not enabled.")
                    print("  Enable: navig config set comms.matrix.enabled true")
                import typer

                raise typer.Exit(1)
            return func(*args, **kwargs)

        return wrapper

    return decorator
