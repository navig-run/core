"""Regression: the web-fetch / web-search kill-switches must actually disable the tool.

`navig config set web.fetch.enabled false` stores the STRING "false" (truthy), so the raw
`if not fetch_config.get("enabled", True)` never fired — the "disable" switch left the tool
ON. Both gates now coerce, so a string "false" disables as intended.

On pristine main these tests would fall through to the real web_fetch/web_search (network);
with the fix they short-circuit to the disabled-error, so they run offline.
"""

import pytest

from navig.mcp.tools import system as sysmod


class _Server:
    _config = None


@pytest.fixture
def force_web_config(monkeypatch):
    """Force navig.tools.web.get_web_config to return a fixed dict (as if set via CLI)."""

    def _install(cfg):
        import navig.tools.web as web

        monkeypatch.setattr(web, "get_web_config", lambda *_a, **_k: cfg)

    return _install


def test_fetch_kill_switch_honors_string_false(force_web_config):
    force_web_config({"fetch": {"enabled": "false"}, "search": {"enabled": "false"}})
    out = sysmod._tool_web_fetch(_Server(), {"url": "http://example.com"})
    assert out == {"error": "Web fetch is disabled in configuration"}


def test_search_kill_switch_honors_string_false(force_web_config):
    force_web_config({"fetch": {"enabled": "false"}, "search": {"enabled": "false"}})
    out = sysmod._tool_web_search(_Server(), {"query": "hello"})
    assert out == {"error": "Web search is disabled in configuration"}


def test_fetch_enabled_true_string_does_not_block(force_web_config, monkeypatch):
    """A string "true" must NOT be read as disabled — it should pass the gate."""
    force_web_config({"fetch": {"enabled": "true"}, "search": {"enabled": "true"}})

    # Stub the actual fetch so we assert the gate was passed without doing network I/O.
    import navig.tools.web as web

    class _Result:
        error = None
        title = "ok"
        url = "http://example.com"
        content = "hi"

    monkeypatch.setattr(web, "web_fetch", lambda *_a, **_k: _Result())
    out = sysmod._tool_web_fetch(_Server(), {"url": "http://example.com"})
    assert out.get("error") != "Web fetch is disabled in configuration"


# ── end-to-end: config → get_web_config → the gate ──────────────────────────────
#
# Everything above stubs out `get_web_config` itself, so it can only ever prove that the
# COERCION is right. It cannot see whether the configured value reaches the gate at all —
# and it did not: `get_web_config` called `config_manager.get_global_config_value("web")`,
# a method that has never existed, and swallowed the AttributeError into "return
# default_config". The switch was therefore permanently ON while these tests stayed green.
#
# These two drive the real `get_web_config`, so the whole chain has to work.


class _ConfigManager:
    """Only what get_web_config uses: ConfigManager's dotted `get`."""

    def __init__(self, config):
        self._config = config

    def get(self, dotted_key, default=None):
        node = self._config
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def test_fetch_kill_switch_works_end_to_end_from_config():
    server = _Server()
    server._config = _ConfigManager({"web": {"fetch": {"enabled": "false"}}})

    out = sysmod._tool_web_fetch(server, {"url": "http://example.com"})

    assert out == {"error": "Web fetch is disabled in configuration"}


def test_search_kill_switch_works_end_to_end_from_config():
    server = _Server()
    server._config = _ConfigManager({"web": {"search": {"enabled": False}}})

    out = sysmod._tool_web_search(server, {"query": "hello"})

    assert out == {"error": "Web search is disabled in configuration"}
