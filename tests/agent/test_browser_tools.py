"""Tests for the agent ``browser_tool`` (headless Playwright, per-chat session).

No real Chromium: we monkeypatch ``browser_session._open_controller`` to hand back
a fake controller. The important invariants:
  * the ``browser`` toolset resolves + registers to exactly ``browser_tool``,
  * command parsing (batching / array / @eN refs) is correct,
  * a screenshot rides back as ``output["_screenshot"]`` (→ the Telegram photo seam),
  * a session PERSISTS across separate ``asyncio.run`` tool calls keyed by
    ``_session_id`` (the regression guard for the throwaway-loop dispatch), and
  * distinct keys get distinct browsers; ``close`` tears one down.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from navig.agent.tools import browser_session as bs
from navig.agent.tools.browser_tools import BrowserTool, parse_commands


class FakeController:
    """Minimal stand-in for BrowserController (only what browser_tool calls)."""

    def __init__(self) -> None:
        self.navigated: list[str] = []
        self.clicks: list[tuple[int, dict]] = []
        self.fills: list[tuple[str, str, str]] = []
        self._stopped = False

    @property
    def is_running(self) -> bool:
        return not self._stopped

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> dict:
        self.navigated.append(url)
        return {"url": url, "title": "Fake Page", "status": 200}

    async def get_a11y_snapshot_with_refs(self) -> tuple:
        text = '[1] button "Go"\n[2] link "Home"'
        ref_map = {
            1: {"role": "button", "name": "Go", "raw_line": text.splitlines()[0]},
            2: {"role": "link", "name": "Home", "raw_line": text.splitlines()[1]},
        }
        return text, ref_map

    async def click_by_ref(self, ref_id: int, ref_map: dict, timeout: int = 5000) -> dict:
        self.clicks.append((ref_id, dict(ref_map)))
        return {"ok": True}

    async def screenshot_base64(self, quality: int = 60) -> str:
        return base64.b64encode(b"JPEG_SCREENSHOT_BYTES").decode()

    async def go_back(self) -> bool:
        return True

    async def get_text(self, selector: str | None = None) -> str:
        return "readable page text"

    async def get_content(self) -> str:
        return "<html>page</html>"

    async def evaluate(self, script: str):
        return 42

    async def stop(self) -> None:
        self._stopped = True


@pytest.fixture(autouse=True)
def _fake_browser(monkeypatch):
    """Patch the controller factory + reset session state around each test."""
    created: list[FakeController] = []

    async def _fake_open(stealth: bool = False, cdp_url: str | None = None) -> FakeController:
        c = FakeController()
        c.cdp_url = cdp_url  # record how the session was opened (attach vs headless)
        created.append(c)
        return c

    monkeypatch.setattr(bs, "_open_controller", _fake_open)
    bs.close_all()
    bs._SESSIONS.clear()
    yield created
    bs.close_all()
    bs._SESSIONS.clear()


def _run(tool: BrowserTool, command, key: str) -> dict:
    """Invoke the tool the way the dispatcher does — a fresh asyncio.run per call."""
    res = asyncio.run(tool.run({"command": command, "_session_id": key}))
    assert res.success, res.error
    return res.output


# ── Toolset membership ──────────────────────────────────────────────────────


def test_toolset_membership():
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY
    from navig.agent.tools import register_browser_tools
    from navig.agent.toolsets import resolve_toolset_names

    register_browser_tools()
    assert resolve_toolset_names("browser") == ["browser_tool"]
    assert "browser_tool" in _AGENT_REGISTRY


# ── Parsing (pure) ──────────────────────────────────────────────────────────


def test_parse_string_batch():
    cmds = parse_commands("fill @e1 hello world; click @e2")
    assert [c.verb for c in cmds] == ["fill", "click"]
    assert cmds[0].argv[0] == "@e1"
    assert cmds[0].rest == "@e1 hello world"
    assert cmds[1].argv == ["@e2"]


def test_parse_array_is_single_command():
    cmds = parse_commands(["evaluate", "var x = 1; x + 2"])
    assert len(cmds) == 1
    assert cmds[0].verb == "evaluate"
    assert cmds[0].rest == "var x = 1; x + 2"  # ';' preserved in array mode


# ── Execution + persistence ─────────────────────────────────────────────────


def test_navigate_returns_screenshot():
    out = _run(BrowserTool(), "navigate example.com", key="k1")
    assert out["url"] == "https://example.com"
    assert out["_screenshot"] == b"JPEG_SCREENSHOT_BYTES"


def test_snapshot_then_click_persists_and_uses_refmap(_fake_browser):
    tool = BrowserTool()
    # 3 separate asyncio.run calls on one chat key — navigation stops a batch, so
    # snapshot/click are their own calls; all must reuse the SAME persistent browser.
    _run(tool, "navigate example.com", key="chat")
    out2 = _run(tool, "snapshot", key="chat")  # stores ref_map on the session
    assert "@e1" in out2["result"] and "@e2" in out2["result"]
    _run(tool, "click @e1", key="chat")        # reuses the stored ref_map
    assert len(_fake_browser) == 1, "session must persist across tool calls (one controller)"
    ctrl = _fake_browser[0]
    assert ctrl.clicks and ctrl.clicks[0][0] == 1
    assert ctrl.clicks[0][1] == {  # the stored ref_map was passed through
        1: {"role": "button", "name": "Go", "raw_line": '[1] button "Go"'},
        2: {"role": "link", "name": "Home", "raw_line": '[2] link "Home"'},
    }


def test_click_without_snapshot_is_actionable():
    out = _run(BrowserTool(), "click @e1", key="fresh")
    assert "snapshot" in out["result"].lower()


def test_distinct_sessions_get_distinct_browsers(_fake_browser):
    tool = BrowserTool()
    _run(tool, "navigate a.com", key="a")
    _run(tool, "navigate b.com", key="b")
    assert len(_fake_browser) == 2
    assert bs.active_session_count() == 2


def test_close_tears_down_session():
    tool = BrowserTool()
    _run(tool, "navigate x.com", key="z")
    assert bs.active_session_count() == 1
    _run(tool, "close", key="z")
    assert bs.active_session_count() == 0


def test_unknown_command_rejected():
    res = asyncio.run(BrowserTool().run({"command": "frobnicate", "_session_id": "k"}))
    assert not res.success
    assert "unknown" in (res.error or "").lower()


# ── Desktop-pane attach (shared browser) ────────────────────────────────────


def test_port_from_endpoint():
    assert bs._port_from_endpoint("http://127.0.0.1:9333") == 9333
    assert bs._port_from_endpoint("http://127.0.0.1:9333/") == 9333
    assert bs._port_from_endpoint("http://localhost:9222/json") == 9222
    assert bs._port_from_endpoint("nonsense") is None


def test_desktop_endpoint_makes_session_attach(_fake_browser):
    # navig-os advertises its visible pane's CDP endpoint for this chat session.
    bs.register_desktop_endpoint("deskkey", "http://127.0.0.1:9333")
    try:
        _run(BrowserTool(), "navigate example.com", key="deskkey")
        # get_or_open must have opened the session ATTACHED to that endpoint,
        # not a headless browser.
        assert _fake_browser[-1].cdp_url == "http://127.0.0.1:9333"
    finally:
        bs.clear_desktop_endpoint("deskkey")


def test_headless_when_no_desktop_endpoint(_fake_browser):
    _run(BrowserTool(), "navigate example.com", key="plainkey")
    assert _fake_browser[-1].cdp_url is None  # headless (no pane registered)
