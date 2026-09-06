"""The channel-neutral half of the Extensions gate.

``telegram_extensions`` is two things wearing one name: a **Telegram catalog**
(18 bundles, their commands, their callback prefixes, the copy the bot prints)
and a **resolver** that is not about Telegram at all — it reads
``modules.overrides``, applies a documented precedence, and coerces whatever it
finds into a bool without ever raising.

Only the first half is Telegram's. The second half is the answer to "is this
feature switched on?", which Discord and WhatsApp will need in exactly the same
shape the day they grow a catalog — and if it is still living inside
``telegram_extensions`` on that day, it gets copied instead of reused, and the
copy drifts. Two implementations of a precedence rule is how "off" starts
meaning different things on different channels.

So this module owns the resolution and knows nothing about any channel. It has
no catalog, no commands, and no opinion about what an extension IS — a caller
hands it the four facts that decide the answer.

⚠ **Nothing here may mention a specific channel.** That is not style: the whole
value is that a second channel can call it unchanged. Pinned by
``core/tests/quality/test_extension_gate_is_channel_neutral.py``.

DELIBERATELY FAIL-OPEN, exactly as before: any error resolves to ENABLED and
logs at most once. A developer's omission must never silence the operator's bot.
Do not "fix" this into fail-closed.

Stdlib only at module scope — ``navig help`` must respond in under 50 ms, so
every navig import is function-scoped.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel for "the dotted key is absent" as distinct from "present and falsey".
# The legacy rule below turns entirely on that distinction: an operator who set
# the old key to `false` must keep their `false`, while one who never touched it
# must fall through to the extension's default.
MISSING: Any = object()

# (channel, extension id) pairs already reported, so a warning is once-per-process
# rather than once-per-callback. Keyed by channel too, because the same bare id
# can legitimately exist on two channels and each is its own omission.
_WARNED: set[tuple[str, str]] = set()


def config_value(dotted: str) -> Any:
    """Read a dotted config key, returning :data:`MISSING` when it is absent.

    Never raises: an unreadable config must not decide a feature is off.
    """
    try:
        from navig.core import Config  # noqa: PLC0415

        return Config().get(dotted, MISSING)
    except Exception:  # noqa: BLE001
        return MISSING


def warn_unknown(channel: str, ext_id: str) -> None:
    """Report an extension id that is in no catalog — once per process."""
    key = (channel, ext_id)
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning(
        "unknown %s extension %r - treating as enabled (fail open)", channel, ext_id
    )


def resolve_enabled(
    *,
    module_id: str,
    default_enabled: bool,
    locked: bool = False,
    legacy_key: str | None = None,
) -> bool:
    """Resolve one extension's on/off state.  Pure read.  Never raises.

    Order - first match wins::

        0. LOCKED    locked                          -> True
        1. OPERATOR  modules.overrides[<module_id>]  -> coerce_bool(v, default)
        2. LEGACY    legacy_key PRESENT in config    -> coerce_bool(v, default)
        3. DEFAULT   default_enabled

    "PRESENT" means the dotted key EXISTS, not that it is truthy.

    NOTHING IS EVER WRITTEN - no migration pass, no backfill.  A legacy key stays
    the operator's record until they touch the new toggle; from then on rule 1
    shadows it permanently.

    The caller resolves an unknown id itself (there is no catalog here to consult)
    and should call :func:`warn_unknown` before returning True for one.
    """
    if locked:
        return True
    try:
        from navig.core.coerce import coerce_bool  # noqa: PLC0415

        override = config_value(f"modules.overrides.{module_id}")
        if override is not MISSING:
            return coerce_bool(override, default=default_enabled)
        if legacy_key:
            legacy = config_value(legacy_key)
            if legacy is not MISSING:
                return coerce_bool(legacy, default=default_enabled)
        return default_enabled
    except Exception as exc:  # noqa: BLE001
        logger.debug("extension gate for %r raised %r; allowing through", module_id, exc)
        return True
