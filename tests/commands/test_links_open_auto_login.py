"""`navig links open` with a credential must actually log in.

It never did. The command POSTed a task spec to /api/v1/browser/task -- a route nothing in the
repository serves; the module it used describes itself as a bridge to a "Go browser executor"
this Python-only core never had. Every credentialed open hit the dead route, caught the
httpx error, and fell to a plain webbrowser.open. The `--headless` flag being unread was only
the visible edge of that.

Ported onto the CDP stack behind `navig cdp new` / `navig cdp login`. These tests stub the
three primitives and assert the sequence, the teardown, and the fallbacks. No browser is
launched here; the primitives themselves have their own tests.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.integration


def _link(cred="cred-1", url="https://example.com/login"):
    return SimpleNamespace(id="L1", url=url, vault_cred_id=cred, title="t")


def _run(args, *, link, new=None, login=None):
    """Drive `links open` with the CDP primitives stubbed; return (result, calls)."""
    from navig.commands.links import links_app

    calls: list[tuple] = []
    db = SimpleNamespace(get=lambda _id: link, record_visit=lambda _id: calls.append(("visit", _id)))

    def _new(**kw):
        calls.append(("new", kw))
        return new if new is not None else {"ok": True, "port": 9333, "profile": kw.get("profile")}

    async def _login(port, **kw):
        calls.append(("login", port, kw))
        return login if login is not None else {"status": "ok"}

    def _stop(port=None, all_ports=False):
        calls.append(("stop", port))
        return {"ok": True}

    with patch("navig.commands.links._links_db_mod") as mod, \
         patch("navig.browser.cdp_actions.new", _new), \
         patch("navig.browser.cdp_actions.login", _login), \
         patch("navig.browser.cdp_actions.stop", _stop), \
         patch("webbrowser.open", lambda url: calls.append(("webbrowser", url))):
        mod.get_links_db.return_value = db
        result = CliRunner().invoke(links_app, ["open", "L1", *args], obj={})
    return result, calls


def test_a_credentialed_link_launches_and_logs_in_through_cdp():
    result, calls = _run([], link=_link())
    assert result.exit_code == 0, result.output
    kinds = [c[0] for c in calls]
    assert kinds == ["visit", "new", "login"], f"expected launch then login, got {kinds}"
    _, port, kw = calls[2]
    assert port == 9333
    assert kw["open_url"] == "https://example.com/login", "login() must navigate to the link"
    assert ("webbrowser", "https://example.com/login") not in calls, "must not ALSO plain-open"


def test_a_headful_open_leaves_the_browser_for_the_user():
    """The window is the deliverable; closing it would defeat the command."""
    _, calls = _run([], link=_link())
    assert ("stop", 9333) not in calls


def test_a_headless_open_logs_in_then_closes_the_browser():
    """Invisible and left running is a leak. --headless exists to warm a profile."""
    result, calls = _run(["--headless"], link=_link())
    assert result.exit_code == 0, result.output
    kinds = [c[0] for c in calls]
    assert kinds == ["visit", "new", "login", "stop"], kinds
    assert calls[1][1]["headless"] is True, "the flag must reach the launcher (it was never read)"
    assert calls[3] == ("stop", 9333)


def test_the_profile_flag_reaches_the_launcher():
    _, calls = _run(["--profile", "work"], link=_link())
    assert calls[1][1]["profile"] == "work"


def test_a_link_without_a_credential_still_opens_plainly():
    """The un-credentialed path is unchanged: default browser, no CDP."""
    result, calls = _run([], link=_link(cred=None))
    assert result.exit_code == 0, result.output
    assert ("webbrowser", "https://example.com/login") in calls
    assert not any(c[0] in ("new", "login") for c in calls)


def test_headless_without_a_credential_is_refused_not_ignored():
    result, calls = _run(["--headless"], link=_link(cred=None))
    assert result.exit_code == 2
    assert not any(c[0] in ("new", "login", "webbrowser") for c in calls), (
        "nothing to log into and nothing to show: it must do nothing and say why"
    )


def test_a_failed_launch_falls_back_to_a_plain_open():
    result, calls = _run([], link=_link(), new={"ok": False, "error": "no chrome"})
    assert result.exit_code == 0, result.output
    assert ("webbrowser", "https://example.com/login") in calls
    assert not any(c[0] == "login" for c in calls)


def test_no_credential_in_the_vault_is_reported_and_the_page_stays_open():
    result, calls = _run([], link=_link(), login={"status": "no_credential"})
    assert result.exit_code == 0
    assert "No vault login found" in result.output
    assert not any(c[0] == "stop" for c in calls), "headful: the page is still useful"


def test_a_raising_login_does_not_crash_the_command():
    from navig.commands.links import links_app
    calls = []
    db = SimpleNamespace(get=lambda _id: _link(), record_visit=lambda _id: None)

    async def _boom(port, **kw):
        raise RuntimeError("CDP socket closed")

    with patch("navig.commands.links._links_db_mod") as mod, \
         patch("navig.browser.cdp_actions.new", lambda **kw: {"ok": True, "port": 1}), \
         patch("navig.browser.cdp_actions.login", _boom), \
         patch("navig.browser.cdp_actions.stop", lambda **kw: calls.append("stop")):
        mod.get_links_db.return_value = db
        result = CliRunner().invoke(links_app, ["open", "L1"], obj={})
    assert result.exit_code == 0, result.output
    assert "did not complete" in result.output
