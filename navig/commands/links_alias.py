"""``navig links`` — DEPRECATED alias of ``navig bookmarks`` (the navig-bookmarks plugin).

The bookmark manager moved out of core into ``plugins/navig-bookmarks`` and was renamed.
This module is what core mounts as ``links`` now (``cli/registration.py``):

* **plugin installed** — a group carrying the plugin's own commands (the very same command
  objects, so ``navig links list`` IS ``navig bookmarks list``), whose callback prints a
  one-line deprecation notice to stderr first. Same store too: the plugin copies the old
  ``links.db`` into its ``bookmarks.db`` on first use, read-only.
* **plugin missing** — every invocation prints how to install it and exits 1. A deprecated
  name that silently did nothing would be worse than one that says why.

Either way it first opens the legacy ``links.db`` (when it exists), which repairs a store
written by the old FTS triggers — ``navig doctor`` names ``navig links list`` as that repair.

The previous implementation (``navig.commands.links``) and its store
(``navig.memory.links_db``) stay in the tree for one release: ``navig import
--persist-bookmarks``, the browser orchestrator, ``navig doctor`` and their tests still use
the store, and the old command module's tests pin behaviour the plugin re-implements.
Nothing mounts ``navig.commands.links`` any more.
"""

from __future__ import annotations

import sys

import typer
from typer.core import TyperGroup

REPLACEMENT = "navig bookmarks"
INSTALL_HINT = "pip install navig-bookmarks    (or: pip install \"navig[bookmarks]\")"
DEPRECATION = (
    "`navig links` is deprecated and will be removed in the next release — "
    f"use `{REPLACEMENT}` (same commands, same bookmarks)."
)


def _notice() -> None:
    print(DEPRECATION, file=sys.stderr)


def _repair_legacy_store() -> None:
    """Open the legacy ``links.db`` once, as ``navig links`` always did — never create it.

    Opening ``LinksDB`` is what repairs a store written by the old, unsafe FTS triggers, and
    ``navig doctor`` names ``navig links list`` as that repair. Forwarding alone would turn
    that advice into a no-op, so the alias keeps doing it for as long as the store exists.
    """
    try:
        from navig.memory import links_db  # noqa: PLC0415
        from navig.platform import paths  # noqa: PLC0415

        if (paths.data_dir() / "links.db").exists():
            links_db.get_links_db()
    except Exception as exc:  # noqa: BLE001 - a broken legacy store must not block the new one
        print(f"note: could not open the legacy links store ({exc})", file=sys.stderr)


class _MissingPluginGroup(TyperGroup):
    """Accepts any arguments and answers every one of them with the install hint."""

    def parse_args(self, ctx, args):  # noqa: ANN001, ANN201
        ctx.args = []
        return []

    def invoke(self, ctx):  # noqa: ANN001, ANN201
        _notice()
        _repair_legacy_store()
        print(
            f"The bookmark manager is now the navig-bookmarks plugin, which is not installed.\n"
            f"  {INSTALL_HINT}\n"
            f"then run `{REPLACEMENT}`.",
            file=sys.stderr,
        )
        ctx.exit(1)


def _missing_app() -> typer.Typer:
    app = typer.Typer(
        name="links",
        cls=_MissingPluginGroup,
        help="[deprecated] Renamed to `navig bookmarks` — install the navig-bookmarks plugin.",
        add_completion=False,
    )

    @app.callback()
    def _links() -> None:
        """[deprecated] Renamed to `navig bookmarks` — install the navig-bookmarks plugin."""

    return app


def _forwarding_app(bookmarks_app: typer.Typer) -> typer.Typer:
    alias = typer.Typer(
        name="links",
        cls=bookmarks_app.info.cls,
        help="[deprecated] Renamed to `navig bookmarks`; this name forwards there.",
        no_args_is_help=True,
    )
    # The plugin's own command and sub-group registrations: one implementation, two names.
    alias.registered_commands.extend(bookmarks_app.registered_commands)
    alias.registered_groups.extend(bookmarks_app.registered_groups)

    @alias.callback()
    def _deprecated() -> None:
        """[deprecated] Renamed to `navig bookmarks`."""
        _notice()
        _repair_legacy_store()

    return alias


def build_links_app() -> typer.Typer:
    try:
        from navig_bookmarks.commands.bookmarks import app as bookmarks_app  # noqa: PLC0415
    except ImportError:
        return _missing_app()
    return _forwarding_app(bookmarks_app)


links_app = build_links_app()
