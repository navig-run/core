"""Order-independent mocking of ``navig.daemon.service_manager`` for the installer tests.

``navig.installer.modules.service.apply()`` resolves its backend lazily::

    from navig.daemon import service_manager

That reads the ``service_manager`` **attribute** on the already-imported ``navig.daemon``
package. So patching only ``sys.modules["navig.daemon.service_manager"]`` is silently
ignored the moment ANY earlier test in the same xdist worker imports the real submodule —
importing it binds it as that attribute, and the attribute wins over the sys.modules key.

That is exactly why ``test_applied_when_install_succeeds`` and friends passed in isolation
but failed in the full parallel run: a *legitimate* prior import (not a leaked stub)
poisoned them. Note the distinction from the playwright case — nothing here leaks; the
tests' own mocking technique was order-dependent.

These helpers replace the WHOLE ``navig.daemon`` package for the duration of the block, so
the mock is picked up regardless of import order, and ``patch.dict`` restores it after.
"""

from __future__ import annotations

import contextlib
import sys
from unittest.mock import MagicMock, patch


@contextlib.contextmanager
def fake_service_manager(sm: MagicMock):
    """Make ``from navig.daemon import service_manager`` yield *sm*, whatever the import order."""
    daemon = MagicMock()
    daemon.service_manager = sm
    with patch.dict(sys.modules, {"navig.daemon": daemon, "navig.daemon.service_manager": sm}):
        yield sm


@contextlib.contextmanager
def no_service_manager():
    """Make ``from navig.daemon import service_manager`` RAISE, whatever the import order.

    Setting the *package* to ``None`` halts the import before any attribute lookup — robust
    even when the real submodule was attribute-bound by an earlier import.
    """
    with patch.dict(sys.modules, {"navig.daemon": None, "navig.daemon.service_manager": None}):
        yield
