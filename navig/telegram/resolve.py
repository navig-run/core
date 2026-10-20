"""
Ask Telegram, handle by handle, whether a username still reaches a person.

The distinction this module exists to hold
------------------------------------------
There are exactly three outcomes, and two of them are silence:

* **a verdict** — the handle was looked at and an answer was obtained;
* **unchecked** — nothing was asked, or the answer could not be trusted;
* **aborted** — the whole run stopped early.

The tempting fourth outcome is to record "gone" for whatever did not answer.
That is wrong in the only way that matters: a flood-wait, a timeout or a
transient RPC error all look like "no such user" from the outside, and a live
person filed as dead is a permanent mistake -- :func:`navig.telegram.contacts`
already refuses it for that reason, and :func:`navig.contacts.flag`'s
``--from-file`` will happily believe whatever this hands it.

So an error that is not *specifically* "this username does not exist" produces
no verdict at all. Silence is recorded as silence.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Iterable

#: A username that resolves to something that is not a person cannot be messaged
#: as if it were one -- a channel, a group and a bot all answer a `t.me/` link,
#: and all three are dead ends for reaching a human.
STATE_OK = "ok"
STATE_GONE = "gone"
STATE_INVALID = "invalid"
STATE_NOT_A_USER = "not_a_user"

#: Errors that genuinely mean "there is no such username".
#: Anything else is *not* evidence and must fall through to `unchecked`.
_USERNAME_ABSENT = ("UsernameNotOccupiedError", "UsernameInvalidError")

DEFAULT_LIMIT = 85
DEFAULT_DELAY = 2.5


def _verdict_for(entity) -> str:
    """Map a resolved entity onto a liveness verdict.

    :func:`navig.telegram.contacts.taggable_reason` already answers "can this
    be a contact, and if not, why" -- the same three reasons are the answer to
    "does this handle reach a person", so the two classifications cannot drift
    apart by having separate copies of them.
    """
    from .contacts import taggable_reason

    reason = taggable_reason(entity)
    if reason is None:
        return STATE_OK
    if reason == "account is deleted":
        return STATE_GONE
    return STATE_NOT_A_USER


def _classify(exc: BaseException) -> str | None:
    """``None`` means "this proves nothing" -- do not record a verdict.

    Deliberately a name check rather than ``except (ValueError, RPCError)``:
    the existing code records *every* RPCError as an unresolved handle, which is
    fine when the caller only wants a retry list and wrong the moment the caller
    writes the result down as truth.
    """
    name = type(exc).__name__
    if name in _USERNAME_ABSENT:
        return STATE_INVALID if "Invalid" in name else STATE_GONE
    if isinstance(exc, ValueError):
        # telethon raises this for "cannot find any entity corresponding to"
        # when the username is not registered.
        return STATE_GONE
    return None


async def resolve_handles(
    handles: Iterable[str],
    *,
    limit: int = DEFAULT_LIMIT,
    delay: float = DEFAULT_DELAY,
    max_flood_wait: int = 300,
    progress: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """
    Resolve up to ``limit`` usernames and return verdicts, never guesses.

    Returns::

        {
          "verdicts": [{"handle": ..., "state": ...}, ...],
          "unchecked": [handle, ...],   # asked nothing of, or answer untrusted
          "resolved": int,
          "checked": int,
          "aborted": str | None,       # why the run stopped early
        }

    Pacing is not optional. ``contacts.resolveUsername`` is limited far more
    tightly than sending a message, and the ceiling is enforced by Telegram
    rather than by us -- so the caller gets a *cap* (``limit``) and a *delay*,
    and a flood over ``max_flood_wait`` seconds stops the run outright.
    """
    from .contacts import MAX_FLOOD_WAIT, UserClient

    handles = [str(h).strip().lstrip("@") for h in handles]
    handles = [h for h in handles if h]
    # De-duplicate but keep order: the book can carry the same handle on two
    # contacts, and asking Telegram twice spends budget once per call.
    seen: set[str] = set()
    ordered = [h for h in handles if not (h.lower() in seen or seen.add(h.lower()))]
    if not ordered:
        # Nothing to ask about, so no session to open: a login prompt is a poor
        # answer to "check zero handles".
        return {"verdicts": [], "unchecked": [], "resolved": 0, "checked": 0,
                "aborted": None}

    verdicts: list[dict] = []
    unchecked: list[str] = []
    aborted: str | None = None
    checked = 0

    ceiling = min(limit, len(ordered)) if limit else len(ordered)

    try:
        from telethon.errors import FloodWaitError
    except ImportError:  # pragma: no cover - telethon is optional
        return {"verdicts": [], "unchecked": ordered, "resolved": 0,
                "checked": 0, "aborted": "telethon is not installed"}

    async with UserClient() as client:
        for handle in ordered:
            if checked >= ceiling:
                unchecked.extend(ordered[checked:])
                break
            if checked and delay:
                await asyncio.sleep(delay)
            checked += 1
            try:
                entity = await client.get_entity(handle)
            except FloodWaitError as exc:
                wait = int(getattr(exc, "seconds", 0) or 0)
                if wait > (max_flood_wait or MAX_FLOOD_WAIT):
                    # Stopping is the correct answer. The handles that follow
                    # were never asked about, and saying so beats either
                    # guessing or silently omitting them.
                    unchecked.extend(ordered[checked - 1:])
                    checked -= 1
                    aborted = (f"FLOOD_WAIT {wait}s while resolving @{handle} -- over the "
                               f"{max_flood_wait}s ceiling. Nothing after it was checked, "
                               f"and nothing before it is in doubt.")
                    break
                await asyncio.sleep(wait + 1)
                try:
                    entity = await client.get_entity(handle)
                except Exception as retry_exc:  # noqa: BLE001 - classified below
                    state = _classify(retry_exc)
                    if state is None:
                        unchecked.append(handle)
                    else:
                        verdicts.append({"handle": handle, "state": state})
                    if progress:
                        progress({"handle": handle, "state": state or "unchecked"})
                    continue
            except Exception as exc:  # noqa: BLE001 - classified, never re-raised
                state = _classify(exc)
                if state is None:
                    unchecked.append(handle)
                else:
                    verdicts.append({"handle": handle, "state": state})
                if progress:
                    progress({"handle": handle, "state": state or "unchecked"})
                continue

            state = _verdict_for(entity)
            verdicts.append({"handle": handle, "state": state})
            if progress:
                progress({"handle": handle, "state": state})

    return {
        "verdicts": verdicts,
        "unchecked": unchecked,
        "resolved": len(verdicts),
        "checked": checked,
        "aborted": aborted,
    }
