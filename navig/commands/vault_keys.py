"""Compat shim -- `navig vault keys` now lives in ``navig_vault_cli``.

The vault CLI (vault + keys + svg + federation) lives in the navig-vault package; core
keeps the dotted path so the registration map, the ``cred`` / ``cred-profile`` / ``secret``
aliases and every in-tree import keep reaching the same module object.
"""
from __future__ import annotations

import sys as _sys

try:
    import navig_vault.commands.vault_keys as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a broken install
    raise ImportError(
        "`navig vault` requires the navig-vault-cli package. "
        "Install it with: pip install navig-vault-cli"
    ) from exc

_sys.modules[__name__] = _impl
