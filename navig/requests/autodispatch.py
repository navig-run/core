"""
navig.requests.autodispatch — policy for "handle critical/important things on
my own".

When the user enables auto-dispatch (config flag ``ai.auto_dispatch``), navig
may resolve a request *without* blocking on a human — but only when the action
is both high-priority AND safe to run unattended. Dangerous / never-level
actions ALWAYS ask, regardless of priority. This is intentionally conservative:
the whole point of the asks system is that a human stays in the loop for
anything risky.

The decision is a pure function so it can be unit-tested as a truth table.
"""

from __future__ import annotations

from navig.approval.policies import DEFAULT_AUTO_EVOLVE_WHITELIST

# Priorities that qualify as "critical and important" per the product spec.
_AUTO_PRIORITIES = {"important", "critical"}

# Levels that may NEVER be auto-dispatched, even when critical.
_BLOCKED_LEVELS = {"dangerous", "never"}


def should_auto_dispatch(
    request: dict,
    *,
    enabled: bool,
    whitelist: list[str] | None = None,
) -> bool:
    """Return True if *request* may be executed automatically.

    Rules (all must hold):
      1. ``enabled`` is True (the user opted in via ai.auto_dispatch).
      2. ``request["priority"]`` is "important" or "critical".
      3. The action is safe: ``level == "safe"`` OR the command/source matches
         an entry in the auto-evolve whitelist. DANGEROUS / NEVER are excluded.

    Parameters
    ----------
    request:
        A unified request dict (see ``UserRequest.to_dict``). Reads
        ``priority``, ``level`` and (optionally) ``command``/``source``.
    enabled:
        The runtime ai.auto_dispatch toggle.
    whitelist:
        Override for the safe-action whitelist (defaults to the shared
        auto-evolve whitelist so the two stay in sync).
    """
    if not enabled:
        return False

    priority = str(request.get("priority", "normal")).lower()
    if priority not in _AUTO_PRIORITIES:
        return False

    level = str(request.get("level") or "").lower()
    if level in _BLOCKED_LEVELS:
        return False

    if level == "safe":
        return True

    # Not explicitly safe — only auto-dispatch when the action is whitelisted.
    wl = whitelist if whitelist is not None else DEFAULT_AUTO_EVOLVE_WHITELIST
    candidate = str(request.get("command") or request.get("source") or "").lower().strip()
    if not candidate:
        return False
    import fnmatch

    return any(fnmatch.fnmatch(candidate, pat.lower()) for pat in wl)
