"""Regression: the interactive menu's "Backup all databases" action must call a real function.

`interactive.py` imported `backup_all_databases_cmd` from `navig.commands.backup`
— a name that has never existed — so selecting "Backup all databases" in
`navig` (the interactive shell) always raised ImportError. The fix calls the
real `backup_all_databases(name, compress, options)` with the same defaults the
`navig backup --db-all` CLI uses.

This pins the seam (the menu action lives inside the TUI dispatch loop and isn't
unit-invokable): the real function exists with the exact signature the fix
passes, and the phantom name stays gone.
"""

from __future__ import annotations

import inspect

import navig.commands.backup as backup_mod


def test_real_backup_all_databases_exists_with_expected_signature():
    fn = getattr(backup_mod, "backup_all_databases", None)
    assert callable(fn), "backup_all_databases must exist — the menu action calls it"

    params = list(inspect.signature(fn).parameters)
    # The fix calls backup_all_databases(None, "gzip", {}) positionally.
    assert params[:3] == ["name", "compress", "options"], (
        f"signature drifted to {params!r}; update the interactive menu call to match"
    )


def test_phantom_cmd_wrapper_is_gone():
    assert not hasattr(backup_mod, "backup_all_databases_cmd")
