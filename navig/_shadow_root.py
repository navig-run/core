"""Repair a package ROOT that a ``navig/`` folder in the working directory shadowed.

Measured 2026-10-19: ``C:\\studio`` holds an asset folder named ``navig``. From that
cwd, ``python -m navig …`` resolves the package ROOT to a **namespace package** built
from that folder (``__file__ is None``, no attributes) while every submodule still comes
from the editable install — so the first ``from navig import <attribute>`` anywhere in
the code died:

    ImportError: cannot import name '__version__' from 'navig' (unknown location)

There are 23 of those ``__version__`` imports and 260 ``from navig import …`` sites in
core, so repairing them one by one is hopeless: fix the root instead. NAVIG's own spawns
(``suggest``/``triggers``/``skills``/``history``) inherit the caller's cwd, which is how
a plain shell session in such a folder breaks the agent too. The console command
``navig …`` is unaffected — its ``sys.path[0]`` is the Scripts directory.

Called from ``navig.main`` before it touches the root; a no-op in every healthy run.
"""

from __future__ import annotations

import sys
from pathlib import Path


def repair_root() -> bool:
    """Swap a namespace-only ``sys.modules["navig"]`` for the real package.

    Returns True when it repaired. In a normal run the root already has a ``__file__``
    and this costs one attribute lookup.
    """
    root = sys.modules.get("navig")
    if root is None or getattr(root, "__file__", None):
        return False  # not imported yet, or already the real package

    try:
        import importlib.util

        # This module lives INSIDE the real package, so its directory is the package
        # directory the submodules are already loading from — no guesswork about which
        # install is active.
        pkg_dir = Path(__file__).resolve().parent
        init = pkg_dir / "__init__.py"
        if not init.is_file():
            return False
        spec = importlib.util.spec_from_file_location(
            "navig", init, submodule_search_locations=[str(pkg_dir)]
        )
        if spec is None or spec.loader is None:
            return False
        real = importlib.util.module_from_spec(spec)
        sys.modules["navig"] = real
        spec.loader.exec_module(real)
        return True
    except Exception:  # noqa: BLE001 — never make startup worse than the crash we fix
        # Leave whatever root is in place: the run then fails exactly as it did before.
        return False
