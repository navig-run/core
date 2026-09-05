"""`agent.fallback_chain` and `agent.prompt_cache` had readers that could only fail.

All three readers called ``navig.core.config_loader.load_config()`` with no arguments. That
function loads *a file* and takes a required ``path``, so every call raised ``TypeError``
straight into a bare ``except`` that returned the "nothing configured" default:

    _load_fallback_chain()      -> []            always
    _prompt_cache_enabled()     -> True          always
    get_prompt_cache_config()   -> (True, None)  always

`agent.fallback_chain` is **documented** in ``docs/ai-models-and-modes.md`` with a worked
YAML example and the instruction "Configure a chain you can actually reach … so a capped
Claude recovers to another model you have a key for". An operator who followed it got no
recovery at all: when their primary model hit a rate cap the call simply failed, while the
docs said they had configured a fallback.

This is the same defect as #1024, in three more places — an exception handler around a
config read makes a wrong call signature indistinguishable from "the user set nothing".
These tests read a **real** ``config.yaml`` through a **real** ``ConfigManager`` for that
reason: a stub accepting ``*args, **kwargs`` answers happily and would go green over the
broken bare call.
"""

from __future__ import annotations

import pytest

CONFIG = (
    "agent:\n"
    "  fallback_chain:\n"
    "    - nvidia:meta/llama-3.1-70b-instruct\n"
    "    - openrouter:google/gemini-2.5-flash\n"
    "  prompt_cache: false\n"
    "  prompt_cache_ttl: 1h\n"
)


@pytest.fixture
def operator_config(monkeypatch, tmp_path):
    """A real ConfigManager over a real config.yaml holding the documented example.

    ⚠ `ConfigManager(config_dir=…)` is the wrong lever: it sets `base_dir`, while `cm.get()`
    reads the *global* config out of `global_config_dir`, which comes from
    `NAVIG_CONFIG_DIR`.
    """
    import navig.config as config_mod

    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    cm = config_mod.ConfigManager()
    monkeypatch.setattr(config_mod, "get_config_manager", lambda *a, **k: cm)
    return cm


def test_the_documented_fallback_chain_reaches_the_caller(operator_config):
    """The exact YAML from docs/ai-models-and-modes.md must come back, in order."""
    from navig.llm.generate import _load_fallback_chain

    assert _load_fallback_chain() == [
        "nvidia:meta/llama-3.1-70b-instruct",
        "openrouter:google/gemini-2.5-flash",
    ], (
        "the operator's configured recovery chain did not reach run_llm — a capped primary "
        "model fails outright instead of falling back, while the docs say it is configured."
    )


def test_prompt_cache_can_actually_be_turned_off(operator_config):
    """`prompt_cache: false` must disable it. It previously could not be disabled at all."""
    from navig.llm.generate import _prompt_cache_enabled

    assert _prompt_cache_enabled() is False


def test_the_prompt_cache_ttl_is_read(operator_config):
    from navig.agent.prompt_caching import get_prompt_cache_config

    assert get_prompt_cache_config() == (False, "1h")


def test_the_string_form_of_the_toggle_is_coerced(monkeypatch, tmp_path):
    """`navig config set agent.prompt_cache false` stores the STRING "false", which is truthy.

    The canonical `coerce_bool` handles it; a bare `bool(...)` would leave caching on for an
    operator who used the CLI rather than editing YAML.
    """
    import navig.config as config_mod

    (tmp_path / "config.yaml").write_text(
        'agent:\n  prompt_cache: "false"\n', encoding="utf-8"
    )
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    cm = config_mod.ConfigManager()
    monkeypatch.setattr(config_mod, "get_config_manager", lambda *a, **k: cm)

    from navig.llm.generate import _prompt_cache_enabled

    assert _prompt_cache_enabled() is False


def test_absent_settings_keep_their_documented_defaults(monkeypatch, tmp_path):
    """No config must mean caching on and no fallback chain — not an exception."""
    import navig.config as config_mod

    (tmp_path / "config.yaml").write_text("agent: {}\n", encoding="utf-8")
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    cm = config_mod.ConfigManager()
    monkeypatch.setattr(config_mod, "get_config_manager", lambda *a, **k: cm)

    from navig.agent.prompt_caching import get_prompt_cache_config
    from navig.llm.generate import _load_fallback_chain, _prompt_cache_enabled

    assert _load_fallback_chain() == []
    assert _prompt_cache_enabled() is True
    assert get_prompt_cache_config() == (True, None)


def test_an_unreadable_config_does_not_raise(monkeypatch):
    """These sit on the LLM call path; a config failure must not break generation."""
    import navig.config as config_mod

    def _boom(*a, **k):
        raise OSError("config is being rewritten")

    monkeypatch.setattr(config_mod, "get_config_manager", _boom)

    from navig.agent.prompt_caching import get_prompt_cache_config
    from navig.llm.generate import _load_fallback_chain, _prompt_cache_enabled

    assert _load_fallback_chain() == []
    assert _prompt_cache_enabled() is True
    assert get_prompt_cache_config() == (True, None)
