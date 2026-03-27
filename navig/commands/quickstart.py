"""Quickstart wizard for NAVIG.

Goal: get a user to a working 'navig host list' and 'navig run' flow quickly.
This is intentionally minimal and leans on existing init + local discovery.
"""

from __future__ import annotations

from typing import Any

from navig import console_helper as ch
from navig.config import get_config_manager


def quickstart(options: dict[str, Any]) -> None:
    """Run a minimal onboarding flow.

    Steps:
    - Ensure the project is initialized (creates .navig/) unless already present.
    - Ensure at least one host exists globally; if not, offer local discovery.
    """

    from pathlib import Path

    auto_yes = bool(options.get("yes"))
    quiet = bool(options.get("quiet"))

    project_navig = Path.cwd() / ".navig"
    if not project_navig.exists():
        from navig.commands.init import init_app

        init_opts = dict(options)
        init_opts["yes"] = True  # quickstart should be hands-off by default
        init_opts["quiet"] = quiet
        init_app(init_opts)

    config_manager = get_config_manager()
    hosts = config_manager.list_hosts()

    if hosts:
        if not quiet:
            ch.success("Quickstart: configuration looks ready")
        return

    # No hosts exist yet: suggest local discovery.
    if not quiet:
        ch.header("Quickstart: Add Your First Host")
        ch.info("No hosts configured yet.")
        ch.info("Creating a local 'localhost' host is the fastest path.")

    if auto_yes or ch.confirm_action("Run local host discovery now?", default=True):
        from navig.commands.local_discovery import discover_local_host

        discover_local_host(
            name="localhost",
            auto_confirm=True,
            set_active=True,
            progress=not quiet,
        )
        return

    ch.warning("Quickstart incomplete")
    ch.info("Next steps:")
    ch.dim("  navig host add <name>")
    ch.dim("  navig host discover-local")
