"""
navig/cli/options.py
====================

Readers for the **global** CLI options dict.

``main()`` in ``cli/__init__.py`` puts every global flag into ``ctx.obj``, and every
command receives that same dict as its ``opts`` argument. The flags are therefore only
as real as the commands that remember to consult them — and several did not:

* ``navig --dry-run history clear --yes`` deleted the whole operation history and
  printed "✓ Cleared 2 operations" (#755);
* ``navig --dry-run context clear`` deleted ``.navig/config.yaml`` and reported success;
* ``navig --dry-run context set --host web1`` created ``.navig/`` and wrote to it.

Each of those had its own local reading of the flag, or none at all. One function here
means a command can consult it without re-deriving what it means, and an audit can ask
one question instead of grepping for a dozen spellings.
"""

from __future__ import annotations

from typing import Any

#: The key `main()` writes the global `--dry-run` flag under in `ctx.obj`.
DRY_RUN_KEY = "dry_run"


def is_dry_run(opts: dict[str, Any] | None, explicit: bool = False) -> bool:
    """True when this invocation must not change anything.

    ``explicit`` is for commands that ALSO define their own ``--dry-run`` option (e.g.
    ``navig history replay --dry-run``). Either spelling counts: honouring only the
    local one means the global flag silently executes, which is the worse failure
    because it is the flag people reach for when they are unsure.
    """
    return bool(explicit or (opts or {}).get(DRY_RUN_KEY))
