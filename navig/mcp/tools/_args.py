"""Argument coercion shared by the MCP tool handlers.

Dependency-free (no navig imports) so any tool module can use it — the package
``__init__`` eagerly imports the tool modules, so this cannot live there.
"""

from __future__ import annotations


def coerce_bool(value: object, default: bool = False) -> bool:
    """Coerce an MCP tool's boolean argument.

    MCP arguments arrive as decoded JSON, so a "boolean" parameter can turn up as a
    real ``bool``, a **number**, or a string, depending on the client.

    Numbers used to be dropped: the three copies of this helper tested ``bool`` then
    ``str`` and returned *default* for everything else, so an ``int`` never reached a
    branch. That inverted the caller's intent in both directions — ``hold_ctrl=0``
    (default ``True``) came back **True**, and ``force=1`` (default ``False``) came
    back **False**. An explicitly supplied value must never resolve to its opposite.

    Strings keep the original narrow whitelist on purpose. This is deliberately
    STRICTER than :func:`navig.core.coerce.coerce_bool`, which also accepts
    ``on``/``y``/``t``: these arguments include ``force``, ``overwrite`` and
    ``recursive``, and widening what counts as an affirmative on a destructive flag is
    a safety decision, not a cleanup. Falsy strings already resolve correctly —
    ``false``/``no``/``off``/``0`` are all outside the whitelist.

    ``None`` (and anything else) yields *default*, unchanged.
    """
    if isinstance(value, bool):  # must precede int — bool IS an int subclass
        return value
    if isinstance(value, int):  # a JSON number: 0 is false, anything else true
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return default
