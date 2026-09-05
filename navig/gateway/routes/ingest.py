"""Inbound Signals ingest: POST /api/ingest/{source}

Your own website/backend fires HMAC-signed events here; each verified event is
mapped onto a notification type and handed to ``notify.dispatch``, so it lands in
the deck (bell + Inbox + toast) and every channel you enabled for that type in
Settings → Notifications.

Self-authenticating: no deck/Bearer auth sits on ``/api/*`` (only ``/api/deck/*``
is gated), so this route validates the per-source HMAC itself — exactly like the
SMS/Telegram webhook routes do for their providers.

Reachability without a tunnel is Lighthouse: the edge forwards the opaque public
path ``/ingest/<tenant>/<source>`` down the uplink, which rewrites it to this
loopback route. See ``navig/cloud/uplink.py``.
"""

from __future__ import annotations

import collections
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

    from navig.gateway.server import NavigGateway  # noqa: F401

try:
    from aiohttp import web
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("aiohttp is required for gateway routes") from exc

logger = logging.getLogger("navig.notify")

_MAX_BODY = 256 * 1024  # 256 KB — signal payloads are small JSON
# Replay-dedupe within the timestamp window: (source, signature) -> first-seen
# monotonic time. Eviction is by AGE (primary), not count: a signature older than
# the replay window can never gate a valid replay (verify_and_render rejects an
# out-of-tolerance timestamp), so we drop it. The count cap is only a memory
# backstop under a flood — the old count-only cap (2000) evicted in-window sigs
# early under sustained load, silently reopening the replay hole.
_SEEN: "collections.OrderedDict[str, float]" = collections.OrderedDict()
# 2x the verify tolerance (signals.DEFAULT_TOLERANCE_S = 300) — comfortably covers
# the whole window in either clock-skew direction.
_SEEN_TTL_S = 600.0
_SEEN_CAP = 20000  # memory backstop only (time eviction keeps it far smaller normally)


def register(app: "web.Application", gateway: "NavigGateway") -> None:
    """Register the ingest route (public — your websites POST here, signed)."""
    app.router.add_post("/api/ingest/{source}", _handler)


def _seen(key: str, *, now: float | None = None) -> bool:
    """Return True if *key* was seen within the replay window (and record it if not).

    Entries are kept in first-seen order and evicted by AGE once past
    ``_SEEN_TTL_S`` — so the window can't be reopened by evicting an in-window
    signature early under load. A replay-hit does NOT refresh the age (the window
    is anchored to the original event's timestamp, not the replay's arrival).
    """
    now = time.monotonic() if now is None else now
    cutoff = now - _SEEN_TTL_S
    # Drop expired entries from the front (oldest first-seen); stop at the first
    # still-fresh one since insertion order == first-seen order.
    while _SEEN:
        oldest = next(iter(_SEEN))
        if _SEEN[oldest] >= cutoff:
            break
        del _SEEN[oldest]
    if key in _SEEN:
        return True
    _SEEN[key] = now
    # Memory backstop: only trips under a flood of unique sigs within one window.
    while len(_SEEN) > _SEEN_CAP:
        _SEEN.popitem(last=False)
    return False


async def _handler(request: "web.Request") -> "web.Response":
    from navig.notify import signals

    source_name = request.match_info.get("source", "")
    src = signals.get_source(source_name)
    if src is None or not src.get("enabled", True):
        return web.json_response({"ok": False, "error": "unknown_source"}, status=404)

    body = await request.read()
    if len(body) > _MAX_BODY:
        return web.json_response({"ok": False, "error": "payload_too_large"}, status=413)

    result = signals.verify_and_render(src, dict(request.headers), body)
    if not result.ok:
        return web.json_response(
            {"ok": False, "error": result.error}, status=result.http_status
        )

    # Replay defence #2: drop a re-fired identical signature inside the window.
    # (Marked BEFORE dispatch so a concurrent duplicate is caught; rolled back
    # below on any failure so a retry of a FAILED delivery isn't lost as a dupe.)
    replay_key = f"{source_name}:{result.signature}"
    if _seen(replay_key):
        return web.json_response({"ok": True, "duplicate": True})

    try:
        from navig.notify import dispatch as notify_dispatch

        outcome = await notify_dispatch(
            result.notify_type,
            result.title,
            result.body,
            priority=result.priority,
            data=result.data,
        )
    except Exception:  # noqa: BLE001 — dispatch blew up before delivering anything
        _SEEN.pop(replay_key, None)  # not delivered → let a retry re-attempt
        logger.debug("signal dispatch failed for %s", source_name, exc_info=True)
        return web.json_response({"ok": False, "error": "dispatch_failed"}, status=502)

    channels = [c.get("channel") for c in (outcome.get("channels") or []) if c.get("ok")]
    # A delivery was ATTEMPTED to ≥1 channel and every one FAILED — a real failure,
    # not an intentional mute (master-off / muted / no enabled channels yield an
    # EMPTY channel list, which is an honest 200). Don't ack success and swallow the
    # replay dedupe: roll the mark back and return a retryable 502 so the signed
    # sender re-fires instead of silently losing the event.
    if (outcome.get("channels") or []) and not channels:
        _SEEN.pop(replay_key, None)
        logger.warning("signal '%s': every channel failed to deliver", source_name)
        return web.json_response(
            {"ok": False, "error": "delivery_failed"}, status=502
        )

    signals.record_hit(source_name)  # best-effort (guarded; never raises)
    return web.json_response(
        {"ok": True, "type": result.notify_type, "delivered": channels}
    )
