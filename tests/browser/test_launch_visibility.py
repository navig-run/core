"""Window visibility is decided by the CALLER'S CONTEXT, not by the controller.

Regression cover for the blank-window leak: `cdp_new` (the MCP tool an autonomous agent
calls) advertised ``"headless": {"default": False}``, so an agent — which has no screen and
never opts in — opened a real Chrome window on the operator's desktop on every call. Those
windows render blank (content goes to a tab that is then closed) and survive the CLI
(browsers are spawned DETACHED_PROCESS), so they stacked up during unattended sessions.

The tests that matter here assert the actual Chrome **argv**, not just the resolver's
return value: a resolver that returns True while `--headless=new` never reaches the command
line would still put a window on screen.
"""

from __future__ import annotations

import pytest

from navig.browser import cdp_actions
from navig.browser.visibility import context_default, resolve_headless


class _FakeTarget:
    def to_dict(self):
        return {"port": 9999, "url": "about:blank"}


@pytest.fixture
def captured_argv(monkeypatch, tmp_path):
    """Capture the `extra_args` a launch would pass to Chrome, without launching."""
    seen: dict = {}

    def _fake_launch(app, port=None, user_data_dir=None, profile_directory=None, extra_args=None):
        seen["app"] = app
        seen["extra_args"] = list(extra_args or [])
        return _FakeTarget()

    monkeypatch.setattr("navig.browser.targets.launch_with_cdp", _fake_launch)
    monkeypatch.setattr("navig.browser.targets.new_session_profile_dir",
                        lambda profile=None: str(tmp_path / "profile"))
    # Neutral config: these tests are about context defaults, and must not depend on
    # whatever `browser.headless` happens to be set to on the machine running them.
    monkeypatch.setattr("navig.browser.visibility._configured_headless", lambda: None)
    return seen


# ── the resolver ──────────────────────────────────────────────────────────────


def test_context_defaults():
    assert context_default("agent") is True  # no eyes attached
    assert context_default("script") is True  # unattended by definition
    assert context_default("human") is False  # they asked to watch


def test_unknown_context_raises_rather_than_guessing():
    """A caller nobody classified is exactly the one that should not open a window."""
    with pytest.raises(ValueError, match="unknown launch context"):
        context_default("cron-ish")


def test_explicit_argument_always_wins(monkeypatch):
    monkeypatch.setattr("navig.browser.visibility._configured_headless", lambda: None)
    assert resolve_headless(False, context="agent") is False  # --headed
    assert resolve_headless(True, context="human") is True  # --headless


def test_config_overrides_context_default(monkeypatch):
    monkeypatch.setattr("navig.browser.visibility._configured_headless", lambda: False)
    assert resolve_headless(context="agent") is False
    monkeypatch.setattr("navig.browser.visibility._configured_headless", lambda: True)
    assert resolve_headless(context="human") is True


def test_config_string_false_is_honoured(monkeypatch):
    """`navig config set browser.headless false` stores the STRING "false".

    ``bool("false")`` is ``True``, so a raw read would turn the operator's "off" into
    "on" — the exact footgun `coerce_bool` exists for.
    """
    monkeypatch.setattr("navig.browser.visibility._configured_headless", lambda: "false")
    assert bool("false") is True  # the trap, stated
    assert resolve_headless(context="agent") is False  # …and not fallen into


def test_unrecognised_config_token_falls_back_to_context(monkeypatch):
    monkeypatch.setattr("navig.browser.visibility._configured_headless", lambda: "maybe")
    assert resolve_headless(context="agent") is True
    assert resolve_headless(context="human") is False


def test_unreadable_config_never_breaks_a_launch(monkeypatch):
    """A locked config file must not turn a cosmetic preference into an outage."""
    def _boom():
        raise OSError("config.yaml is locked by another process")

    monkeypatch.setattr("navig.config.get_config_manager", _boom)
    assert resolve_headless(context="agent") is True  # context default, no exception


# ── argv teeth: does --headless=new actually reach Chrome? ─────────────────────


def test_agent_launch_is_headless_in_the_argv(captured_argv):
    """THE regression: an agent calling `new` with no flag gets no window."""
    res = cdp_actions.new(app="chrome", port=9999, context="agent")
    assert res["ok"] is True
    assert res["headless"] is True
    assert "--headless=new" in captured_argv["extra_args"]


def test_script_launch_is_headless_in_the_argv(captured_argv):
    cdp_actions.new(app="chrome", port=9999, context="script")
    assert "--headless=new" in captured_argv["extra_args"]


def test_human_launch_keeps_its_window(captured_argv):
    """`cdp login` / `profile open` must still show a window — a person logs in there."""
    res = cdp_actions.new(app="chrome", port=9999, context="human")
    assert res["headless"] is False
    assert not any(a.startswith("--headless") for a in captured_argv["extra_args"])


def test_explicit_headed_beats_agent_context_in_the_argv(captured_argv):
    cdp_actions.new(app="chrome", port=9999, headless=False, context="agent")
    assert not any(a.startswith("--headless") for a in captured_argv["extra_args"])


def test_launch_gains_headless_for_browsers(captured_argv, monkeypatch):
    """`cdp_launch` had NO visibility control at all — it could only open a window."""
    monkeypatch.setattr("navig.browser.targets.probe_port", lambda *a, **k: None)
    cdp_actions.launch("chrome", port=9999, context="agent")
    assert "--headless=new" in captured_argv["extra_args"]


def test_launch_does_not_send_headless_to_electron_apps(captured_argv, monkeypatch):
    """Discord/Slack/VS Code are not browsers; `--headless=new` is meaningless there."""
    monkeypatch.setattr("navig.browser.targets.probe_port", lambda *a, **k: None)
    cdp_actions.launch("discord", port=9999, context="agent")
    assert not any(a.startswith("--headless") for a in captured_argv["extra_args"])


# ── the surfaces that carry the default to callers ────────────────────────────


def test_mcp_cdp_new_defaults_to_headless(captured_argv):
    """An absent `headless` argument must reach the agent context, not be coerced False.

    ``bool(args.get("headless", False))`` collapsed "unspecified" into "windowed", which
    is how the agent path always got a window.
    """
    from navig.mcp.tools.cdp import _tool_cdp_new

    res = _tool_cdp_new(None, {"port": 9999})
    assert res["headless"] is True
    assert "--headless=new" in captured_argv["extra_args"]


def test_mcp_cdp_new_still_honours_an_explicit_false(captured_argv):
    from navig.mcp.tools.cdp import _tool_cdp_new

    res = _tool_cdp_new(None, {"port": 9999, "headless": False})
    assert res["headless"] is False


def test_mcp_schemas_advertise_the_headless_default():
    """The schema is what the model reads; it must not still say 'opt-in, default False'."""
    from navig.mcp.tools.cdp import register as _register

    class _Server:
        # The three attributes `register` writes to. Deliberately NOT wrapped in a
        # try/except that skips: a skip here would report "pass" for the one assertion
        # that checks what the model actually reads.
        def __init__(self):
            self.tools: dict = {}
            self._tool_handlers: dict = {}
            self._tool_safety: dict = {}

    server = _Server()
    _register(server)

    for name in ("cdp_new", "cdp_launch"):
        prop = server.tools[name]["inputSchema"]["properties"]["headless"]
        assert prop["default"] is True, f"{name} still advertises a windowed default"
        assert "opt-in" not in prop["description"].lower(), (
            f"{name} description still tells the model headless is opt-in"
        )


def test_cli_headless_and_headed_are_mutually_exclusive():
    """Guessing which one the operator meant is how a 'silent' run ends up on screen."""
    import typer

    from navig.commands.cdp import _visibility_flag

    assert _visibility_flag(headless=False, headed=False) is None  # unspecified
    assert _visibility_flag(headless=True, headed=False) is True
    assert _visibility_flag(headless=False, headed=True) is False
    with pytest.raises(typer.BadParameter):
        _visibility_flag(headless=True, headed=True)
