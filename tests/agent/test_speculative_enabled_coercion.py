"""`navig config set agent.speculative.enabled false` must disable speculative decoding.

`navig config set` stores its argument verbatim as a string, and ``bool("false")`` is
``True``. Both readers of ``agent.speculative.enabled`` took the value raw:

    get_speculative_executor():        if not spec_cfg.get("enabled", True): return None
    get_speculative_runtime_snapshot(): enabled = bool(spec_cfg.get("enabled", True))

so a config-disabled feature stayed ON (the executor was created) and the status helper
reported it as enabled. Both now go through ``navig.core.coerce.coerce_bool``.
"""

from __future__ import annotations

import pytest

from navig.agent.speculative import (
    get_speculative_executor,
    get_speculative_runtime_snapshot,
    reset_speculative_executor,
)


class _FakeMgr:
    """Config manager stub exposing ``global_config`` as both readers use it."""

    def __init__(self, speculative_cfg: dict) -> None:
        self.global_config = {"agent": {"speculative": speculative_cfg}}


def _patch_cfg(monkeypatch, speculative_cfg: dict) -> None:
    monkeypatch.setattr(
        "navig.config.get_config_manager", lambda: _FakeMgr(speculative_cfg)
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("false", False),  # the footgun: string "false" is truthy
        ("off", False),
        ("0", False),
        ("no", False),
        ("on", True),
        ("true", True),
        (True, True),
        (False, False),
    ],
)
def test_snapshot_enabled_coerces_config_strings(monkeypatch, raw, expected):
    _patch_cfg(monkeypatch, {"enabled": raw})
    assert get_speculative_runtime_snapshot()["enabled"] is expected


def test_snapshot_unset_and_unknown_default_to_enabled(monkeypatch):
    _patch_cfg(monkeypatch, {})  # unset → default True
    assert get_speculative_runtime_snapshot()["enabled"] is True
    _patch_cfg(monkeypatch, {"enabled": "maybe"})  # unknown token → default, not truthy
    assert get_speculative_runtime_snapshot()["enabled"] is True


def test_executor_gate_honors_string_false(monkeypatch):
    """The behaviour gate: `config set agent.speculative.enabled false` must actually
    stop the executor from being created (returns None)."""
    reset_speculative_executor()  # clear any singleton from another test
    _patch_cfg(monkeypatch, {"enabled": "false"})
    try:
        # dispatch_fn is never reached when disabled; pass a dummy to avoid the registry.
        assert get_speculative_executor(dispatch_fn=lambda *a, **k: None) is None
    finally:
        reset_speculative_executor()
