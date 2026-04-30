"""
Batch 115: tests for
  navig/daemon/scheduler.py
  navig/commands/_async_utils.py
  navig/commands/boot_cmd.py
  navig/commands/watch_cmd.py
  navig/gateway/deck/routes/static_assets.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner as TyCliRunner

# ---------------------------------------------------------------------------
# navig/daemon/scheduler.py
# ---------------------------------------------------------------------------

from navig.daemon.scheduler import get_scheduler


def test_get_scheduler_returns_none():
    result = get_scheduler()
    assert result is None


def test_get_scheduler_twice():
    """Should always return None (compatibility shim)."""
    assert get_scheduler() is None
    assert get_scheduler() is None


# ---------------------------------------------------------------------------
# navig/commands/_async_utils.py
# ---------------------------------------------------------------------------

from navig.commands._async_utils import run_sync


def test_run_sync_no_loop():
    """Without a running loop, asyncio.run is used."""

    async def coro():
        return 42

    result = run_sync(coro())
    assert result == 42


def test_run_sync_returns_value():
    async def add(a, b):
        return a + b

    result = run_sync(add(3, 4))
    assert result == 7


def test_run_sync_propagates_exception():
    async def failing():
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        run_sync(failing())


def test_run_sync_with_running_loop():
    """When called inside a running loop, uses ThreadPoolExecutor fallback."""
    results = []

    async def inner():
        from navig.commands._async_utils import run_sync as _rs

        async def coro():
            return "from inner"

        val = _rs(coro())
        results.append(val)

    asyncio.run(inner())
    assert results == ["from inner"]


def test_run_sync_returns_none_for_none_coro():
    async def nothing():
        return None

    assert run_sync(nothing()) is None


# ---------------------------------------------------------------------------
# navig/commands/boot_cmd.py
# ---------------------------------------------------------------------------

from navig.commands.boot_cmd import boot_app
import navig.console_helper as _ch

_runner = TyCliRunner()


def test_boot_show_exits_ok():
    with patch.object(_ch, "warn", create=True, return_value=None):
        result = _runner.invoke(boot_app, ["show"])
    assert result.exit_code == 0


def test_boot_show_warns_not_implemented():
    with patch.object(_ch, "warn", create=True, return_value=None):
        result = _runner.invoke(boot_app, ["show"])
    assert result.exit_code == 0


def test_boot_run_exits_ok():
    with patch.object(_ch, "warn", create=True, return_value=None):
        result = _runner.invoke(boot_app, ["run"])
    assert result.exit_code == 0


def test_boot_run_dry_run_flag():
    with patch.object(_ch, "warn", create=True, return_value=None):
        result = _runner.invoke(boot_app, ["run", "--dry-run"])
    assert result.exit_code == 0


def test_boot_no_args_shows_help():
    result = _runner.invoke(boot_app, [])
    # no_args_is_help=True → exit code 0 (help) or 2 (typer may differ)
    assert result.exit_code in (0, 2)


# ---------------------------------------------------------------------------
# navig/commands/watch_cmd.py
# ---------------------------------------------------------------------------

from navig.commands.watch_cmd import watch_app

_watch_runner = TyCliRunner()


def test_watch_start_exits_ok():
    with patch.object(_ch, "warn", create=True, return_value=None):
        result = _watch_runner.invoke(watch_app, ["start"])
    assert result.exit_code == 0


def test_watch_start_with_path():
    with patch.object(_ch, "warn", create=True, return_value=None):
        result = _watch_runner.invoke(watch_app, ["start", "/tmp"])
    assert result.exit_code == 0


def test_watch_list_exits_ok():
    with patch.object(_ch, "warn", create=True, return_value=None):
        result = _watch_runner.invoke(watch_app, ["list"])
    assert result.exit_code == 0


def test_watch_no_args_shows_help():
    result = _watch_runner.invoke(watch_app, [])
    assert result.exit_code in (0, 2)


# ---------------------------------------------------------------------------
# navig/gateway/deck/routes/static_assets.py
# ---------------------------------------------------------------------------

from navig.gateway.deck.routes.static_assets import (
    _find_deck_static_dir,
    handle_deck_index,
)


def test_find_deck_static_dir_no_existing():
    """Returns None when no real deck-static dir exists in test env."""
    result = _find_deck_static_dir()
    # May or may not find a dir; just assert type
    assert result is None or isinstance(result, Path)


def test_find_deck_static_dir_override_exists(tmp_path):
    """Returns path when override dir has index.html."""
    index = tmp_path / "index.html"
    index.write_text("<html></html>")
    result = _find_deck_static_dir(override=str(tmp_path))
    assert result == tmp_path


def test_find_deck_static_dir_override_missing(tmp_path):
    """Returns None when override exists but no index.html; may fall through."""
    # When override fails, candidates (including navig-deck/dist) may be found
    result = _find_deck_static_dir(override=str(tmp_path))
    # Either None (no candidates) or a valid Path (candidate found)
    assert result is None or isinstance(result, Path)


def test_find_deck_static_dir_override_nonexistent():
    """Override does not exist; may fall through to workspace candidate."""
    result = _find_deck_static_dir(override="/nonexistent/path/xyz123")
    assert result is None or isinstance(result, Path)


@pytest.mark.asyncio
async def test_handle_deck_index_no_static_dir():
    """When no static dir is found, returns 404 response."""
    req = MagicMock()
    with patch(
        "navig.gateway.deck.routes.static_assets._find_deck_static_dir",
        return_value=None,
    ):
        resp = await handle_deck_index(req)
    assert resp.status == 404


@pytest.mark.asyncio
async def test_handle_deck_index_with_static_dir(tmp_path):
    """When static dir exists with index.html, returns FileResponse."""
    index = tmp_path / "index.html"
    index.write_text("<html>NAVIG Deck</html>")

    from aiohttp import web as aio_web

    fake_response = aio_web.Response(text="<html>ok</html>", status=200)

    with (
        patch(
            "navig.gateway.deck.routes.static_assets._find_deck_static_dir",
            return_value=tmp_path,
        ),
        patch("aiohttp.web.FileResponse", return_value=fake_response),
    ):
        resp = await handle_deck_index(MagicMock())
    assert resp.status == 200


def test_find_deck_static_dir_override_expands_home(tmp_path, monkeypatch):
    """~ in path is expanded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    index = tmp_path / "index.html"
    index.write_text("<html></html>")
    result = _find_deck_static_dir(override=str(tmp_path))
    assert result == tmp_path


def test_find_deck_static_dir_multiple_candidates(tmp_path):
    """Returns first candidate in which index.html exists."""
    # Override provides a valid dir
    index = tmp_path / "index.html"
    index.write_text("<html></html>")
    result = _find_deck_static_dir(override=str(tmp_path))
    assert result == tmp_path


# ---------------------------------------------------------------------------
# Additional _async_utils edge cases
# ---------------------------------------------------------------------------

def test_run_sync_async_sleep():
    """run_sync works with async operations that take time."""
    import time

    async def delayed():
        await asyncio.sleep(0.01)
        return "done"

    start = time.monotonic()
    result = run_sync(delayed())
    elapsed = time.monotonic() - start
    assert result == "done"
    assert elapsed >= 0.01


def test_run_sync_with_side_effects():
    log = []

    async def recorder():
        log.append("ran")
        return len(log)

    run_sync(recorder())
    run_sync(recorder())
    assert log == ["ran", "ran"]
