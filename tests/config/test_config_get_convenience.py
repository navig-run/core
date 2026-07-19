"""`navig.config.get(key, default)` — the documented convenience reader.

The language rules (CLAUDE.md) say *"Config via ``navig.config.get(key, default)``"*, and
~seven callers did ``from navig.config import get`` — but the function was never defined, so
every one of them fell into its ``except`` and silently used its default (config overrides
for provider keys, mesh_token, thresholds, … never applied). These tests pin the restored
API: it exists, resolves dotted keys through the canonical resolver, and never raises.
"""

from __future__ import annotations

import navig.config as cfg


def test_get_is_defined_and_callable() -> None:
    # The exact thing the ~seven `from navig.config import get` callers assume exists.
    assert callable(cfg.get)


def test_get_returns_default_for_absent_key() -> None:
    assert cfg.get("definitely.not.a.real.key", "SENTINEL") == "SENTINEL"
    assert cfg.get("definitely.not.a.real.key") is None


def test_get_reads_a_dotted_key_from_config(monkeypatch, tmp_path) -> None:
    """A value present in config is returned — not the default (the whole point)."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    # Reset the cached singletons so they pick up the isolated config dir.
    from navig.config import reset_config_manager

    reset_config_manager()

    from navig.core import Config

    Config().set("agent.context_compress_threshold", 0.42)
    assert cfg.get("agent.context_compress_threshold") == 0.42
    assert cfg.get("agent.context_compress_threshold", 0.99) == 0.42  # default not used


def test_get_never_raises_even_if_resolution_blows_up(monkeypatch) -> None:
    """A read must never take a caller down — it degrades to the default."""
    import navig.core

    def _boom(*_a, **_k):
        raise RuntimeError("config exploded")

    monkeypatch.setattr(navig.core, "Config", _boom)
    assert cfg.get("anything", "fallback") == "fallback"
