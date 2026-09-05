"""One process, one Matrix bot — and only its owner may stop it.

`navig.comms.matrix` keeps a module-level `_bot` singleton: `start()` registers,
`stop()` deregisters, and `get_matrix_bot()` is what the HitL router, the E2EE manager
and the gateway channel adapter all resolve through.

Two ownership rules were missing:

* `stop()` cleared `_bot` **unconditionally**, so one bot's shutdown deregistered
  whichever bot happened to be registered. A still-running instance became invisible to
  `get_matrix_bot()`, and every consumer resolving through it would then build a second
  bot on the same account — two sync loops, two device sessions.
* `MatrixChannelAdapter` may **adopt** the running singleton rather than construct one,
  and then stopped it on its own shutdown — closing the client and cancelling the sync
  loop of a bot the gateway's `_init_comms` still holds a reference to.

Latent rather than live today: the gateway and `telegram_worker` are separate
processes, so each currently ends up with exactly one bot. The adapter's adopt-then-stop
path is one shared process away from being reachable, and it is cheaper to make the
ownership explicit than to rediscover it from a Matrix account that keeps dropping.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_singleton():
    """The registration is module state — never leak it between tests."""
    import navig.comms.matrix as matrix_mod

    before = matrix_mod._bot
    matrix_mod._bot = None
    yield matrix_mod
    matrix_mod._bot = before


def _bare_bot(matrix_mod, *, running: bool = False):
    """A NavigMatrixBot with just the attributes stop() touches — no nio, no login."""
    bot = matrix_mod.NavigMatrixBot.__new__(matrix_mod.NavigMatrixBot)
    bot._running = running
    bot._sync_task = None
    bot._client = None
    bot._store = None
    return bot


# ── the registration ───────────────────────────────────────────────────────────


async def test_stopping_one_bot_does_not_deregister_another(_clean_singleton):
    matrix_mod = _clean_singleton
    live = _bare_bot(matrix_mod, running=True)
    other = _bare_bot(matrix_mod)
    matrix_mod._bot = live  # `live` is the registered, running instance

    await other.stop()

    assert matrix_mod.get_matrix_bot() is live, (
        "a different bot's shutdown deregistered the running one — every consumer "
        "resolving through get_matrix_bot() would now build a second bot"
    )


async def test_a_bot_deregisters_itself_on_stop(_clean_singleton):
    matrix_mod = _clean_singleton
    bot = _bare_bot(matrix_mod, running=True)
    matrix_mod._bot = bot

    await bot.stop()

    assert matrix_mod.get_matrix_bot() is None


async def test_stop_is_safe_when_nothing_is_registered(_clean_singleton):
    matrix_mod = _clean_singleton
    await _bare_bot(matrix_mod).stop()  # must not raise
    assert matrix_mod.get_matrix_bot() is None


# ── adapter ownership ──────────────────────────────────────────────────────────


def _adapter(monkeypatch, existing):
    """A MatrixChannelAdapter seeing `existing` as the process singleton."""
    import navig.comms.matrix as matrix_mod
    from navig.gateway.channels.matrix import MatrixChannelAdapter

    monkeypatch.setattr(matrix_mod, "get_matrix_bot", lambda: existing)
    return MatrixChannelAdapter({"homeserver_url": "http://localhost:6167"})


def _running_mock_bot():
    bot = MagicMock()
    bot.is_running = True
    bot.start = AsyncMock()
    bot.stop = AsyncMock()
    return bot


async def test_adapter_does_not_stop_a_bot_it_borrowed(monkeypatch, _clean_singleton):
    """The singleton belongs to whoever started it. Stopping it here closes their
    client, cancels their sync loop, and clears the registration they resolve through."""
    borrowed = _running_mock_bot()
    adapter = _adapter(monkeypatch, borrowed)

    await adapter.start()
    await adapter.stop()

    assert adapter._bot is borrowed  # it DID adopt it
    borrowed.stop.assert_not_awaited()
    borrowed.start.assert_not_awaited()  # already running, and not ours to start


async def test_adapter_stops_the_bot_it_created(monkeypatch, _clean_singleton):
    """The other direction — ownership must not become an excuse to leak."""
    import navig.comms.matrix as matrix_mod
    from navig.gateway.channels.matrix import MatrixChannelAdapter

    made = _running_mock_bot()
    made.is_running = False  # freshly constructed
    monkeypatch.setattr(matrix_mod, "get_matrix_bot", lambda: None)
    monkeypatch.setattr(matrix_mod, "NavigMatrixBot", lambda _cfg: made)

    adapter = MatrixChannelAdapter({"homeserver_url": "http://localhost:6167"})
    await adapter.start()
    made.start.assert_awaited_once()

    made.is_running = True
    await adapter.stop()
    made.stop.assert_awaited_once()


async def test_adapter_borrows_only_a_RUNNING_singleton(monkeypatch, _clean_singleton):
    """A registered-but-stopped bot is not usable, so the adapter builds its own and
    owns it."""
    import navig.comms.matrix as matrix_mod
    from navig.gateway.channels.matrix import MatrixChannelAdapter

    stale = _running_mock_bot()
    stale.is_running = False
    made = _running_mock_bot()
    made.is_running = False
    monkeypatch.setattr(matrix_mod, "get_matrix_bot", lambda: stale)
    monkeypatch.setattr(matrix_mod, "NavigMatrixBot", lambda _cfg: made)

    adapter = MatrixChannelAdapter({"homeserver_url": "http://localhost:6167"})
    await adapter.start()

    assert adapter._bot is made
    assert adapter._owns_bot is True
    stale.start.assert_not_awaited()
