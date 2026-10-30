"""The telegram worker serves the gateway HTTP itself (``_start_gateway_http``), never
through ``NavigGateway.start()`` — so the benign-loop-noise filter installed there never
reached this loop, and the Windows Proactor reset kept hitting the default handler."""

from __future__ import annotations

import asyncio

import pytest

from navig.daemon import telegram_worker


class _Stop(Exception):
    pass


@pytest.mark.parametrize("enable_gateway", [True, False])
def test_the_worker_loop_gets_the_noise_filter(monkeypatch, enable_gateway):
    seen: list = []

    def _fake_load_env() -> None:
        seen.append(asyncio.get_running_loop().get_exception_handler())
        raise _Stop  # everything after this needs a live config; the filter is set by now

    monkeypatch.setattr(telegram_worker, "_load_env", _fake_load_env)

    with pytest.raises(_Stop):
        asyncio.run(telegram_worker._run(enable_gateway=enable_gateway))

    assert len(seen) == 1
    assert getattr(seen[0], "_navig_noise_filter", False), "the worker loop is unfiltered"
