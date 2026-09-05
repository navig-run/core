"""A transient/unreadable read of error_log.json must NOT wipe the recorded error history.

AIContextManager loads the log in __init__; on a transient lock the old code set
self.error_logs = [] (via `except Exception`), and the next log_error() appended to that
empty list and saved — persisting a single entry over the whole history. Because
get_ai_context_manager() caches a singleton, a poisoned load kept wiping on every error.

Now _load_error_logs reads via json_io.load_json_for_update (RAISES JsonReadError on an
unreadable-but-populated file), sets _load_failed, and _save_error_logs REFUSES while that
flag is set. log_error re-attempts the load first, so it self-heals once the lock lifts.
"""

from __future__ import annotations

import pytest

import navig.core.json_io as jio
from navig.ai_context import AIContextManager

pytestmark = pytest.mark.integration


def _seed_two(config_dir):
    m = AIContextManager(config_dir=config_dir)
    m.log_error("tunnel", "navig tunnel up", "boom-1")
    m.log_error("db", "navig db dump", "boom-2")
    return m


def test_normal_round_trip_persists_history(tmp_path):
    _seed_two(tmp_path)
    fresh = AIContextManager(config_dir=tmp_path)
    assert [e.error for e in fresh.error_logs] == ["boom-1", "boom-2"]


def test_log_error_during_transient_lock_does_not_wipe_history(monkeypatch, tmp_path):
    _seed_two(tmp_path)
    log_file = tmp_path / "error_log.json"
    before = log_file.read_text(encoding="utf-8")
    assert "boom-1" in before and "boom-2" in before

    def _locked(*_a, **_k):
        raise OSError("file is locked")

    monkeypatch.setattr(jio, "read_text_retrying", _locked)

    mgr = AIContextManager(config_dir=tmp_path)  # construction load hits the lock
    assert mgr._load_failed is True
    mgr.log_error("net", "navig net ping", "boom-3")  # append + save must be REFUSED

    # History intact on disk (read directly — Path.read_text, not the patched json_io).
    assert log_file.read_text(encoding="utf-8") == before


def test_manager_self_heals_once_the_lock_lifts(monkeypatch, tmp_path):
    _seed_two(tmp_path)
    log_file = tmp_path / "error_log.json"

    calls = {"n": 0}
    real = jio.read_text_retrying

    def _locked_then_ok(path, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("locked")  # construction load fails
        return real(path, **kw)      # log_error's self-heal reload succeeds

    monkeypatch.setattr(jio, "read_text_retrying", _locked_then_ok)

    mgr = AIContextManager(config_dir=tmp_path)
    assert mgr._load_failed is True and mgr.error_logs == []
    mgr.log_error("net", "navig net ping", "boom-3")  # reloads real history, appends, saves
    assert mgr._load_failed is False

    monkeypatch.setattr(jio, "read_text_retrying", real)
    fresh = AIContextManager(config_dir=tmp_path)
    assert [e.error for e in fresh.error_logs] == ["boom-1", "boom-2", "boom-3"]
