"""One greeting per boot, across processes.

The supervisor runs a gateway AND a telegram_worker, each with its own channel, so
a per-process "announce once" flag was honoured twice and the operator was greeted
twice per restart (measured in their own chat: 19:10 ×2, 19:30 ×3 including the
engagement greeting). The claim has to be made somewhere both processes can see.
"""

from __future__ import annotations

import pytest

from navig import boot_messages as bm


@pytest.fixture
def claim_dir(tmp_path, monkeypatch):
    """Point the marker at a temp dir — never the operator's real cache."""
    monkeypatch.setattr(bm, "_marker_path", lambda: tmp_path / "boot_greeting.claim")
    monkeypatch.setattr(bm, "_dedupe_sec", lambda: 300)
    return tmp_path


def test_only_the_first_caller_of_a_boot_greets(claim_dir):
    assert bm.claim_boot_greeting(now=1000.0) is True
    # The sibling process, booting in the same second — the measured case.
    assert bm.claim_boot_greeting(now=1000.0) is False
    assert bm.claim_boot_greeting(now=1000.4) is False


def test_a_later_boot_greets_again(claim_dir):
    assert bm.claim_boot_greeting(now=1000.0) is True
    assert bm.claim_boot_greeting(now=1000.0 + 299) is False   # inside the window
    assert bm.claim_boot_greeting(now=1000.0 + 301) is True    # a real boot later
    assert bm.claim_boot_greeting(now=1000.0 + 301) is False   # its sibling, quiet


def test_an_unreadable_marker_is_treated_as_stale_not_as_a_claim(claim_dir):
    """A corrupt marker must not silence greetings forever — the failure mode
    would be permanent and invisible."""
    bm._marker_path().write_text("not a timestamp", encoding="utf-8")
    assert bm.claim_boot_greeting(now=1000.0) is True


def test_it_fails_open_when_the_marker_cannot_be_written(claim_dir, monkeypatch):
    """Greeting once too often beats an install that never says hello."""
    monkeypatch.setattr(bm, "_marker_path",
                        lambda: claim_dir / "no-such-dir" / "x" / "claim")

    def _boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr("pathlib.Path.mkdir", _boom)
    assert bm.claim_boot_greeting(now=1000.0) is True


def test_no_leftover_temp_file_after_a_stale_replacement(claim_dir):
    bm.claim_boot_greeting(now=1000.0)
    bm.claim_boot_greeting(now=2000.0)          # replaces the stale marker
    leftovers = [p.name for p in claim_dir.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_the_toggle_defaults_on_and_honours_the_config_string(monkeypatch):
    class _Cfg:
        def __init__(self, v):
            self.v = v

        def get(self, key, default=None):
            return self.v if key == bm.CFG_GREETING_ENABLED else default

    import navig.core as core
    monkeypatch.setattr(core, "Config", lambda: _Cfg(None))
    assert bm.boot_greeting_enabled() is True
    # `navig config set …enabled false` stores the truthy STRING "false".
    monkeypatch.setattr(core, "Config", lambda: _Cfg("false"))
    assert bm.boot_greeting_enabled() is False
    monkeypatch.setattr(core, "Config", lambda: _Cfg(False))
    assert bm.boot_greeting_enabled() is False


def test_the_channel_asks_before_greeting(claim_dir):
    """Source contract: the greeting path must consult BOTH the toggle and the
    cross-process claim. Without this the next refactor can quietly restore the
    per-process flag and the duplicate comes back."""
    import inspect
    from pathlib import Path

    src = Path(inspect.getfile(
        __import__("navig.gateway.channels.telegram", fromlist=["x"])
    )).read_text(encoding="utf-8")
    start = src.index("async def _deferred_setup")
    body = src[start:start + 2000]
    assert "boot_greeting_enabled()" in body
    assert "claim_boot_greeting()" in body
