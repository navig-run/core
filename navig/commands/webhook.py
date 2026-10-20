"""navig webhook / webhooks — SUPERSEDED by `navig trigger`.

Every verb here called a webhook-management API (``/api/v1/webhooks`` on
``daemon.browser_port``) that nothing in the repository has ever served, so every
invocation failed at the connection on every install. Webhooks are owned by triggers:

    outbound:  navig trigger add <name> --action webhook:<url>
    inbound:   navig trigger add <name> --type webhook --action <command>
    manage:    navig trigger list | disable <id> | remove <id> | test <id>

The group stays registered under both names (no CLI break): each verb prints the
`navig trigger` command that replaced it and exits 1 (see ``navig.cli.superseded``).
"""

from __future__ import annotations

import typer

from navig.cli.superseded import moved

webhook_app = typer.Typer(
    name="webhook",
    help="Webhooks are trigger actions and trigger types (moved → navig trigger)",
    invoke_without_command=True,
    no_args_is_help=False,
)

# Accept whatever the old verbs accepted: the callback answers before any parsing.
_ANY_ARGS = {"ignore_unknown_options": True, "allow_extra_args": True}


@webhook_app.callback()
def _webhook_callback(ctx: typer.Context) -> None:
    group = ctx.info_name if ctx.info_name in ("webhook", "webhooks") else "webhook"
    moved(f"navig {group} {ctx.invoked_subcommand}" if ctx.invoked_subcommand else f"navig {group}")


def _stub(verb: str, replacement: str) -> None:
    def _cmd() -> None:
        moved(f"navig webhook {verb}")

    _cmd.__name__ = f"webhook_{verb.replace('-', '_')}"
    _cmd.__doc__ = f"Moved to `{replacement}`."
    webhook_app.command(verb, context_settings=_ANY_ARGS)(_cmd)


for _verb, _replacement in (
    ("list", "navig trigger list"),
    ("add-inbound", "navig trigger add <name> --type webhook --action <command>"),
    ("add-outbound", "navig trigger add <name> --action webhook:<url>"),
    ("disable", "navig trigger disable <id>"),
    ("delete", "navig trigger remove <id>"),
    ("test", "navig trigger test <id>"),
):
    _stub(_verb, _replacement)
