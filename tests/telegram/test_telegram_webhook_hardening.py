"""Tests for Telegram webhook hardenings (size limit, idempotency, error logging).

The handler is tested by calling ``_webhook_handler`` directly with mocked
aiohttp Request objects — no live server required.
"""

from __future__ import annotations

import asyncio
import collections
import json
from unittest.mock import AsyncMock, MagicMock

from navig.gateway.routes.telegram_webhook import (
    _MAX_PAYLOAD_BYTES,
    _SEEN_UPDATE_IDS_CAPACITY,
    _make_seen_update_ids,
    _webhook_handler,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_gateway(accepted: bool = True) -> MagicMock:
    """Return a minimal mock gateway with a configured telegram channel."""
    channel = MagicMock()
    channel.handle_webhook_update = AsyncMock(return_value=accepted)
    gw = MagicMock()
    gw.channels = {"telegram": channel}
    gw.channel_registry = None
    return gw


def _make_request(
    body: bytes,
    content_length: int | None = None,
    secret: str = "",
) -> MagicMock:
    """Return a mocked aiohttp Request with the given body."""
    req = MagicMock()
    req.content_length = content_length if content_length is not None else len(body)
    req.headers = MagicMock()
    req.headers.get = lambda k, d="": secret if k == "X-Telegram-Bot-Api-Secret-Token" else d
    req.read = AsyncMock(return_value=body)
    return req


def _run(coro):
    """Run a coroutine synchronously using asyncio.run() (Python 3.10+)."""
    return asyncio.run(coro)


# ── Payload size guard via Content-Length header ──────────────────────────────


class TestPayloadSizeGuardContentLength:
    def test_over_limit_via_header_returns_413(self):
        gw = _make_gateway()
        seen = _make_seen_update_ids()
        handler = _webhook_handler(gw, seen)

        body = json.dumps({"update_id": 1, "message": {}}).encode()
        req = _make_request(body, content_length=_MAX_PAYLOAD_BYTES + 1)

        resp = _run(handler(req))
        assert resp.status == 413

    def test_absent_content_length_proceeds_to_body_check(self):
        gw = _make_gateway()
        seen = _make_seen_update_ids()
        handler = _webhook_handler(gw, seen)

        payload = {"update_id": 100, "message": {"text": "hi"}}
        body = json.dumps(payload).encode()
        req = _make_request(body, content_length=None)

        resp = _run(handler(req))
        # Telegram channel accepted → 200
        assert resp.status == 200

    def test_exactly_at_limit_is_allowed(self):
        """Content-Length exactly equal to the limit should pass."""
        gw = _make_gateway()
        seen = _make_seen_update_ids()
        handler = _webhook_handler(gw, seen)

        payload = {"update_id": 200, "message": {}}
        body = json.dumps(payload).encode()
        req = _make_request(body, content_length=_MAX_PAYLOAD_BYTES)

        resp = _run(handler(req))
        assert resp.status == 200


# ── Payload size guard on actual body ─────────────────────────────────────────


class TestPayloadSizeGuardBody:
    def test_large_body_no_content_length_returns_413(self):
        gw = _make_gateway()
        seen = _make_seen_update_ids()
        handler = _webhook_handler(gw, seen)

        oversized_body = b"x" * (_MAX_PAYLOAD_BYTES + 1)
        req = MagicMock()
        req.content_length = None  # header absent
        req.headers = MagicMock()
        req.headers.get = lambda k, d="": d
        req.read = AsyncMock(return_value=oversized_body)

        resp = _run(handler(req))
        assert resp.status == 413


# ── Update-ID idempotency ─────────────────────────────────────────────────────


class TestUpdateIdIdempotency:
    def test_duplicate_update_id_acked_without_dispatch(self):
        gw = _make_gateway()
        seen = _make_seen_update_ids()
        handler = _webhook_handler(gw, seen)

        payload = {"update_id": 42, "message": {"text": "hello"}}
        body = json.dumps(payload).encode()

        # First call — should dispatch
        req1 = _make_request(body)
        resp1 = _run(handler(req1))
        assert resp1.status == 200
        assert gw.channels["telegram"].handle_webhook_update.call_count == 1

        # Second call with same update_id — must NOT dispatch
        req2 = _make_request(body)
        resp2 = _run(handler(req2))
        assert resp2.status == 200
        # Still only 1 dispatch call
        assert gw.channels["telegram"].handle_webhook_update.call_count == 1

    def test_different_update_ids_both_dispatched(self):
        gw = _make_gateway()
        seen = _make_seen_update_ids()
        handler = _webhook_handler(gw, seen)

        for uid in (10, 11):
            body = json.dumps({"update_id": uid, "message": {}}).encode()
            req = _make_request(body)
            _run(handler(req))

        assert gw.channels["telegram"].handle_webhook_update.call_count == 2

    def test_lru_eviction_after_capacity(self):
        """After adding more than capacity entries, the oldest is evicted."""
        gw = _make_gateway()
        seen = _make_seen_update_ids()
        handler = _webhook_handler(gw, seen)

        # Fill to capacity + 1
        for uid in range(_SEEN_UPDATE_IDS_CAPACITY + 1):
            body = json.dumps({"update_id": uid}).encode()
            req = _make_request(body)
            _run(handler(req))

        # The very first update_id (0) should have been evicted
        assert 0 not in seen
        # The cache should be at capacity
        assert len(seen) == _SEEN_UPDATE_IDS_CAPACITY

    def test_no_update_id_in_body_does_not_crash(self):
        """Payloads without update_id should still be processed."""
        gw = _make_gateway()
        seen = _make_seen_update_ids()
        handler = _webhook_handler(gw, seen)

        body = json.dumps({"message": {"text": "no id"}}).encode()
        req = _make_request(body)
        resp = _run(handler(req))
        assert resp.status == 200


# ── Invalid JSON body ─────────────────────────────────────────────────────────


class TestInvalidJson:
    def test_garbage_body_returns_400(self):
        gw = _make_gateway()
        seen = _make_seen_update_ids()
        handler = _webhook_handler(gw, seen)

        req = MagicMock()
        req.content_length = 5
        req.headers = MagicMock()
        req.headers.get = lambda k, d="": d
        req.read = AsyncMock(return_value=b"not{json")

        resp = _run(handler(req))
        assert resp.status == 400

    def test_empty_body_returns_400(self):
        gw = _make_gateway()
        seen = _make_seen_update_ids()
        handler = _webhook_handler(gw, seen)

        req = MagicMock()
        req.content_length = 0
        req.headers = MagicMock()
        req.headers.get = lambda k, d="": d
        req.read = AsyncMock(return_value=b"")

        resp = _run(handler(req))
        assert resp.status == 400


# ── Missing telegram channel ──────────────────────────────────────────────────


class TestMissingChannel:
    def test_returns_503_when_no_channel(self):
        gw = MagicMock()
        gw.channels = {}  # no "telegram" key
        gw.channel_registry = None
        seen = _make_seen_update_ids()
        handler = _webhook_handler(gw, seen)

        body = json.dumps({"update_id": 1, "message": {}}).encode()
        req = _make_request(body)
        resp = _run(handler(req))
        assert resp.status == 503


# ── make_seen_update_ids helper ───────────────────────────────────────────────


class TestMakeSeenUpdateIds:
    def test_returns_ordered_dict(self):
        seen = _make_seen_update_ids()
        assert isinstance(seen, collections.OrderedDict)

    def test_initially_empty(self):
        seen = _make_seen_update_ids()
        assert len(seen) == 0


# ── Compatibility: handler still returns 200 on channel error ────────────────


class TestCompatibilityMode:
    def test_channel_exception_still_returns_200(self):
        """Telegram requires 200 even on internal errors to prevent retries."""
        gw = _make_gateway()
        gw.channels["telegram"].handle_webhook_update = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )
        seen = _make_seen_update_ids()
        handler = _webhook_handler(gw, seen)

        body = json.dumps({"update_id": 99, "message": {}}).encode()
        req = _make_request(body)
        resp = _run(handler(req))
        # Compatibility mode: return 200 so Telegram doesn't retry
        assert resp.status == 200


# ── Channel-level update-id dedup (the lighthouse path bypasses the route LRU) ──


class TestChannelLevelUpdateDedupe:
    """``handle_webhook_update`` must dedup on its own.

    The lighthouse uplink calls the channel DIRECTLY, so the route-level
    ``seen_update_ids`` LRU never sees those deliveries. Without channel dedup
    the edge's 30s timeout → Telegram retry → full reprocessing produced the
    triple vision/OCR loop (2026-10-13).
    """

    def _make_channel(self, store_path):
        from navig.gateway.channels.telegram import TelegramChannel
        from navig.gateway.dedupe import UpdateDedupe

        ch = TelegramChannel("test-token", require_auth=False)
        ch._update_dedupe = UpdateDedupe(store_path=store_path)
        ch._running = True
        ch._process_update = AsyncMock()
        return ch

    def test_duplicate_update_id_processes_once(self, tmp_path):
        ch = self._make_channel(tmp_path / "dedupe.json")
        update = {"update_id": 7, "message": {"text": "hi"}}

        assert _run(ch.handle_webhook_update(dict(update))) is True
        assert _run(ch.handle_webhook_update(dict(update))) is True

        assert ch._process_update.await_count == 1

    def test_different_update_ids_both_process(self, tmp_path):
        ch = self._make_channel(tmp_path / "dedupe.json")

        _run(ch.handle_webhook_update({"update_id": 1}))
        _run(ch.handle_webhook_update({"update_id": 2}))

        assert ch._process_update.await_count == 2

    def test_duplicate_survives_restart_via_the_disk_layer(self, tmp_path):
        store = tmp_path / "dedupe.json"
        ch1 = self._make_channel(store)
        _run(ch1.handle_webhook_update({"update_id": 9}))
        assert ch1._process_update.await_count == 1

        # Fresh channel over the same store file = a restarted process with an
        # empty in-memory cache but the disk ledger intact.
        ch2 = self._make_channel(store)
        _run(ch2.handle_webhook_update({"update_id": 9}))
        assert ch2._process_update.await_count == 0

    def test_missing_update_id_never_dedups(self, tmp_path):
        ch = self._make_channel(tmp_path / "dedupe.json")

        _run(ch.handle_webhook_update({"message": {"text": "a"}}))
        _run(ch.handle_webhook_update({"message": {"text": "b"}}))

        assert ch._process_update.await_count == 2

    def test_dedup_sits_after_the_secret_check(self, tmp_path):
        """An unauthenticated POST must not poison the durable ledger: a wrong
        secret is rejected before recording, so the correct delivery of the
        SAME id still processes exactly once."""
        ch = self._make_channel(tmp_path / "dedupe.json")
        ch.webhook_secret = "s3cret"
        update = {"update_id": 5}

        assert _run(ch.handle_webhook_update(dict(update), "wrong")) is False
        assert ch._process_update.await_count == 0

        assert _run(ch.handle_webhook_update(dict(update), "s3cret")) is True
        assert ch._process_update.await_count == 1

        assert _run(ch.handle_webhook_update(dict(update), "s3cret")) is True
        assert ch._process_update.await_count == 1
