"""Resolve a ``vault://`` reference on THIS machine: local vault first, then the authority.

The one entry point the CLI (``navig vault get/run``) and host credential
resolution use. A label stored in the local vault always wins; only a label
this machine does not hold is asked of the vault authority this machine is
enrolled with (``navig vault join``). An install that never joined behaves
exactly as before: a missing label is a ``KeyError``.
"""

from __future__ import annotations

__all__ = ["client", "resolve"]


def client():
    """A FederationClient over this machine's vault (enrolled or not)."""
    from navig_vault.federation.client import FederationClient  # noqa: PLC0415

    from navig.vault.core import get_vault  # noqa: PLC0415

    return FederationClient(get_vault())


def resolve(ref: str) -> str:
    """The plaintext *ref* names. ``KeyError`` when neither the vault nor the authority has it."""
    from navig_vault.refs import parse_ref, resolve_ref  # noqa: PLC0415

    from navig.vault.core import get_vault  # noqa: PLC0415

    parsed = parse_ref(ref)
    vault = get_vault()
    if vault._store.get(parsed.label) is not None:
        return resolve_ref(vault, parsed)
    fed = client()
    if fed.enrolled():
        return fed.resolve(parsed.uri())
    raise KeyError(parsed.label)
