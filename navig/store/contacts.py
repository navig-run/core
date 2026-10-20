"""Compat shim -- the contact store now lives in ``navig_contacts.store`` (navig-contacts).

The address book moved into its plugin so ``navig-contacts`` runs without navig. Aliases itself
to the implementation rather than re-exporting it, so the module object is IDENTICAL: the
``get_contact_store()`` singleton is shared, and tests that patch ``navig.store.contacts.X``
by dotted path patch the real thing. Same shape as ``navig.vault.core``.
"""
from __future__ import annotations

import sys as _sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - static analysis only
    from navig_contacts.store import *  # noqa: F401,F403

try:
    from navig_contacts import store as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a broken install
    raise ImportError(
        "NAVIG's contact store requires the navig-contacts package. "
        "Install it with: pip install navig-contacts"
    ) from exc

_sys.modules[__name__] = _impl
