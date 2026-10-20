"""The suite's state-reset fixtures must never delete from the operator's REAL config dir.

`_reset_model_fallback_state` (autouse, before AND after every test) unlinks
`model_health.json`, the fallback state and its ledger from `config_dir()/perf`. It
assumed the session's isolated dir. Whenever a test left `config_dir()` resolving to the
real `~/.navig`, it deleted the LIVE daemon's files — seen 2026-10-10: the cache vanished
between heartbeats several times a day (≈20 gates/day run this suite on the box), taking
with it the 401 verdict that reroutes chats off a broken Claude login.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests import conftest as cf


def test_the_real_config_dir_is_recognised():
    assert cf._is_real_config_dir(Path(os.path.expanduser("~")) / ".navig")


def test_the_isolated_session_dir_is_not(tmp_path):
    assert not cf._is_real_config_dir(tmp_path / "cfg")
    from navig.platform.paths import config_dir

    assert not cf._is_real_config_dir(config_dir()), "the session isolation is not active"


def test_reset_refuses_the_real_dir_and_names_the_test(monkeypatch, tmp_path):
    # Point "the real dir" at a sandbox that HOLDS the files, then make config_dir()
    # resolve to it — exactly the state a non-isolated test leaves behind.
    fake_real = (tmp_path / "home" / ".navig").resolve()
    perf = fake_real / "perf"
    perf.mkdir(parents=True)
    victim = perf / "model_health.json"
    victim.write_text('{"anthropic:claude-opus-4-8": {"status": "auth"}}', encoding="utf-8")
    monkeypatch.setattr(cf, "_REAL_CONFIG_DIRS", frozenset({fake_real}))
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(fake_real))

    gen = cf._reset_model_fallback_state.__wrapped__(
        type("R", (), {"node": type("N", (), {"nodeid": "tests/x.py::t"})()})()
    )
    with pytest.warns(UserWarning, match=r"tests/x\.py::t: config_dir\(\) resolved to the real"):
        next(gen)
    assert victim.exists(), "the reset deleted the operator's live verdicts"
