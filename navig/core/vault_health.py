"""Tell "the vault engine is missing" apart from "you never configured that".

Why this exists
---------------
Every credential reader in navig swallows vault read errors on purpose, so a caller can ask
"is this set?" without handling a locked vault. That is the right default, and it has one
bad consequence: an **uninstalled** ``navig-vault`` looks exactly like "you never configured
this", and the repair each caller then advertises is wrong.

It has already cost a real debugging detour once: ``navig telegram contacts tag-list`` said
*"Telegram api_id/api_hash are not set. Run `navig telegram setup`"* — advertising a
re-authentication over credentials that were present and intact the whole time. The cause was
an editable install that predated navig-vault becoming a hard dependency; pip does not
retroactively install new dependencies into an existing environment.

``navig.telegram.config.vault_unavailable_reason`` fixed that one surface. The same shape
exists wherever a secret is read, so the distinction lives here for every OTHER surface
instead of being re-derived per module.

Telegram deliberately keeps its own copy. It probes ``navig.telegram.config._vault()``,
which is the seam its tests monkeypatch; routing it through here would swap the seam out
from under a passing suite to remove six lines of duplication. New credential surfaces
should use this module — the point is that the next one gets the distinction by calling a
function, not by remembering the distinction exists.

Why it is not in ``navig.vault``
--------------------------------
It cannot be. ``navig/vault/<module>.py`` are shims that alias to the ``navig_vault``
package, so ``import navig.vault`` **is itself the failure** this module reports — a helper
living there could never load in the one situation it exists for. It sits in ``navig.core``,
which has no such dependency.

What it deliberately does NOT report
------------------------------------
A **locked** vault. That is a different problem with a different repair, and the per-label
error handling each caller already has covers it. Only the engine being unimportable is
reported here, because only that one is systematically misattributed to the user.
"""

from __future__ import annotations

__all__ = ["vault_unavailable_reason", "explain_missing_credential"]


def vault_unavailable_reason() -> str | None:
    """Why the vault cannot be read *at all*, or ``None`` when it can.

    Returns the ImportError text when the ``navig-vault`` engine is not installed, and
    ``None`` for every other outcome — including a vault that is present but locked, empty,
    or erroring on a particular label.
    """
    try:
        from navig.vault import get_vault  # noqa: PLC0415 - import IS the probe

        get_vault()
    except ImportError as exc:
        return str(exc)
    except Exception:  # noqa: BLE001 - locked/other: the caller's per-label handling covers it
        return None
    return None


def explain_missing_credential(what: str, repair: str) -> str:
    """The message to show when a credential read came back empty.

    Picks between "the engine is missing" and the caller's own configuration advice, so a
    caller does not have to branch. *what* names the thing that was not found (e.g.
    ``"Cloudflare credential"``); *repair* is what to suggest when the vault is genuinely
    readable and the secret simply is not there.

    Keeping the choice here means a new credential surface gets the distinction by using
    this function, rather than by remembering that the distinction exists.
    """
    reason = vault_unavailable_reason()
    if reason is None:
        return f"no {what} — {repair}"
    # The ImportError text already carries the install command, so it is not repeated here.
    return (
        f"no {what} — but the vault engine could not be loaded, so this is probably NOT a "
        f"configuration problem: your credentials are likely present and intact.\n"
        f"  {reason}"
    )
