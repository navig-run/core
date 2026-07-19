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
