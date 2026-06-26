"""Regression: file_history.enabled must be READ from config, not silently defaulted.

Before the fix, _is_enabled() called ConfigManager.get (no such method) inside a
bare `except Exception: return False`, so it was permanently False regardless of
config. The fix uses Config().get(...), which actually reads the value.
"""
from navig.core import Config
from navig.file_history import get_file_history_store


def test_file_history_enabled_reflects_config(monkeypatch) -> None:
    monkeypatch.setattr(
        Config, "get",
        lambda self, key, default=None, scope="merged":
            True if key == "file_history.enabled" else default,
    )
    assert get_file_history_store()._is_enabled() is True
