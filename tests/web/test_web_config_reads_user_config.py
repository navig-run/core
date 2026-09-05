"""``get_web_config`` must actually read the user's ``web:`` config.

It called ``config_manager.get_global_config_value("web")`` — a method ConfigManager has
never had. The AttributeError landed in a bare ``except Exception: return default_config``,
so the entire ``web:`` section was discarded and every caller silently received the
hardcoded defaults.

The consequence that matters is the kill-switch. ``mcp/tools/system.py`` gates both web
tools on ``config["fetch"]["enabled"]`` / ``config["search"]["enabled"]`` — and carries a
comment explaining that the value is coerced because ``navig config set web.fetch.enabled
false`` stores the *string* ``"false"``. That hardening was real, but it was applied to a
value that could never arrive: the key was always the default ``True``. So the documented
way to disable web fetch and web search did nothing at all.

These tests drive the real function with a real ConfigManager-shaped object.
"""

from __future__ import annotations

from typing import Any

from navig.tools.web import get_web_config


class _FakeConfigManager:
    """Mirrors the ConfigManager contract these callers rely on: a dotted ``get``."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self.calls: list[str] = []

    def get(self, dotted_key: str, default: Any = None) -> Any:
        self.calls.append(dotted_key)
        node: Any = self._config
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def test_disabling_web_fetch_in_config_is_visible_to_the_caller() -> None:
    """The kill-switch: `navig config set web.fetch.enabled false` must reach the tool."""
    cm = _FakeConfigManager({"web": {"fetch": {"enabled": False}}})

    config = get_web_config(cm)

    assert config["fetch"]["enabled"] is False


def test_disabling_web_search_in_config_is_visible_to_the_caller() -> None:
    cm = _FakeConfigManager({"web": {"search": {"enabled": False}}})

    config = get_web_config(cm)

    assert config["search"]["enabled"] is False


def test_the_config_string_false_survives_to_the_caller_for_coercion() -> None:
    """`navig config set` stores raw strings; the value must arrive so coerce_bool can act.

    The MCP tools coerce this themselves — the bug was never that the string was truthy,
    it was that the string never got there at all.
    """
    cm = _FakeConfigManager({"web": {"fetch": {"enabled": "false"}}})

    assert get_web_config(cm)["fetch"]["enabled"] == "false"


def test_configured_values_override_defaults() -> None:
    cm = _FakeConfigManager(
        {"web": {"fetch": {"timeout_seconds": 5, "max_chars": 123}, "search": {"provider": "brave"}}}
    )

    config = get_web_config(cm)

    assert config["fetch"]["timeout_seconds"] == 5
    assert config["fetch"]["max_chars"] == 123
    assert config["search"]["provider"] == "brave"


def test_api_key_from_config_is_not_discarded() -> None:
    """Keys stored in config were invisible, so only the env var ever worked."""
    cm = _FakeConfigManager({"web": {"search": {"api_key": "from-config"}}})

    assert get_web_config(cm)["search"]["api_key"] == "from-config"


def test_unset_keys_keep_their_defaults() -> None:
    cm = _FakeConfigManager({"web": {"search": {"provider": "mojeek"}}})

    config = get_web_config(cm)

    assert config["search"]["provider"] == "mojeek"
    assert config["fetch"]["enabled"] is True  # untouched section keeps its default


def test_missing_web_section_yields_defaults() -> None:
    config = get_web_config(_FakeConfigManager({}))

    assert config["fetch"]["enabled"] is True
    assert config["search"]["enabled"] is True


def test_a_non_dict_web_section_does_not_raise() -> None:
    """Hand-edited YAML can put anything here; defaults are the safe answer."""
    config = get_web_config(_FakeConfigManager({"web": "nonsense"}))

    assert config["fetch"]["enabled"] is True


def test_a_non_dict_subsection_does_not_raise() -> None:
    config = get_web_config(_FakeConfigManager({"web": {"fetch": "nonsense"}}))

    assert config["fetch"]["enabled"] is True


def test_a_failing_config_read_degrades_to_defaults() -> None:
    """An unreadable config must not crash the tool — but must not be silent either."""

    class _Exploding:
        def get(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError("config unavailable")

    config = get_web_config(_Exploding())

    assert config["fetch"]["enabled"] is True
    assert config["search"]["enabled"] is True


def test_the_real_config_manager_satisfies_the_contract_this_relies_on() -> None:
    """Pin the premise: ConfigManager really does have `get` and really lacks the old name.

    Without this, the fix could silently rot back into the same shape.
    """
    from navig.config import ConfigManager

    assert hasattr(ConfigManager, "get")
    assert not hasattr(ConfigManager, "get_global_config_value")
