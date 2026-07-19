"""Config-incident reporter — push a notification when the config/identity layer had to
RESCUE itself.

``navig.core.incidents.record()`` writes each rescue (a refused wipe, a config recovered
from cache, ``deck.api_key`` restored or RE-IDENTIFIED) to a JSONL log that ``navig
doctor`` → Config Health reads back. But a doctor row is a **pull**: the operator learns
their config was wiped, or their bot's identity moved, only when they next happen to run
doctor. That is exactly the failure mode this whole area exists to kill — a daemon that
heals itself at 3am and tells nobody, so every light stays green while the bot goes deaf.

So when an incident is recorded, also **push** it out the notify fan-out. Three safeguards,
mirroring ``self_errors``:

  * **throttle + dedupe** — an incident is usually a one-shot at boot, but a crash-loop
    could re-record it; the same event won't re-fire within the cooldown.
  * **loop-safe** — ``incidents.record()`` is sync and may be called off the event loop
    (or with no loop at all, on the CLI); the push is hopped onto the gateway's loop and
    is a silent no-op when there is no loop to hop to.
  * **best-effort** — the hook runs inside ``record()``, which must never raise.

Default ON (``monitors.config_incidents.enabled``, in ``NavigGateway.MONITORS_DEFAULT_ON``):
a MISSED config rescue is exactly the silent failure this exists to catch, and it is
noiseless on a healthy install (nothing records) and only pushes if a channel is
configured. Disable it from the deck Monitors card if you truly don't want it.
"""

from __future__ import annotations

import asyncio
import logging

from navig.notify.producers.self_errors import _Throttle

logger = logging.getLogger("navig.notify")

# A config incident is, by definition, the config layer rescuing itself — every recorded
# type is worth a push. Reading the description registry keeps NEW incident types covered
# automatically (a type with no description still pushes, using its id).
_PRIORITY = "high"

# The notification type this producer dispatches. It MUST be registered in
# navig.notify.types.NOTIFICATION_TYPES, or the router resolves zero channels for it and
# the push is silently dropped (test_config_incident_reporter guards this).
NOTIFY_TYPE = "config_incident"


class ConfigIncidentReporter:
    """Turns a recorded ``incidents`` event into a proactive notification."""

    def __init__(self, loop: asyncio.AbstractEventLoop, *, sink=None) -> None:
        self._loop = loop
        self._throttle = _Throttle()
        self._sink = sink or self._dispatch  # injectable for tests

    def on_incident(self, event: str, data: dict) -> None:
        """Sync entry point, called from ``incidents.record()``. Never raises."""
        try:
            now = self._loop.time()
            if not self._throttle.allow(event, now):
                return
            title, body = self._render(event)
            # record() may run on any thread (write-batcher, boot) — hop to the loop.
            self._loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._sink(title, body)))
        except Exception:  # noqa: BLE001 — a push must never break record()
            pass

    @staticmethod
    def _render(event: str) -> tuple[str, str]:
        from navig.core import incidents

        summary = incidents.DESCRIPTIONS.get(event, event)
        # No leading glyph: the telegram channel prepends emoji_for_type("config_incident"),
        # and the deck feed renders the type's own icon — a title emoji would double it.
        return "NAVIG config health", summary

    async def _dispatch(self, title: str, body: str) -> None:
        try:
            from navig.notify import dispatch

            await dispatch(NOTIFY_TYPE, title, body, priority=_PRIORITY,
                           data={"source": "navig"})
        except Exception:  # noqa: BLE001
            logger.debug("config-incident notify failed", exc_info=True)


_reporter: ConfigIncidentReporter | None = None


def install_config_incident_reporter() -> ConfigIncidentReporter:
    """Register the reporter as the ``incidents`` push sink. Idempotent. Call from the loop."""
    global _reporter
    if _reporter is not None:
        return _reporter
    from navig.core import incidents

    loop = asyncio.get_running_loop()
    _reporter = ConfigIncidentReporter(loop)
    incidents.set_notify_hook(_reporter.on_incident)
    return _reporter


def uninstall_config_incident_reporter() -> None:
    global _reporter
    if _reporter is not None:
        from navig.core import incidents

        incidents.set_notify_hook(None)
        _reporter = None
