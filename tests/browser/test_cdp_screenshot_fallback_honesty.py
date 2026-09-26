"""`navig cdp screenshot` must say when it captured the DESKTOP instead of the page.

The OS fallback is documented ("no CDP target → full-screen capture"), but until #1519
the result was `{"ok": True, "via": "os-automation", "path": …}` and nothing else — the
same shape as a page capture to any caller not reading `via`. Measured cost: the pixel
harness diffed two 7282x4320 desktop shots against 1422x804 page baselines and reported
*size mismatch* on a clean tree (#1515), and a blanket recapture had earlier BAKED one in
as a baseline (#1231). A desktop shot also carries every other window on the screen.

The contract now: a fallback result carries ``fallback: True`` and a ``note`` that names
what was captured; a real page capture carries neither.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from navig.browser import cdp_actions


class _FakeImage:
    def __init__(self) -> None:
        self.saved: list[object] = []

    def save(self, target, format=None):  # noqa: A002 - mirrors PIL's signature
        self.saved.append(target)
        if hasattr(target, "write"):
            target.write(b"\x89PNG-not-really")


@pytest.fixture
def no_bridge(monkeypatch):
    async def _none(port, tab_index=0):  # noqa: ARG001
        return None

    monkeypatch.setattr(cdp_actions, "_try_bridge", _none)


@pytest.fixture
def fake_desktop(monkeypatch, tmp_path):
    import navig.adapters.automation.screenshot as shot_mod

    img = _FakeImage()
    monkeypatch.setattr(shot_mod, "capture_full_screen", lambda: (img, "fake-backend"))
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    return img


def test_fallback_result_is_flagged_and_named(no_bridge, fake_desktop, tmp_path):
    result = asyncio.run(cdp_actions.screenshot(port=45678, out="probe.png"))
    assert result["ok"] is True
    assert result["via"] == "os-automation"
    assert result["fallback"] is True, "a desktop capture must not look like a page capture"
    assert "FULL SCREEN" in result["note"] and "45678" in result["note"]
    assert result["path"].endswith("probe.png")


def test_fallback_base64_is_flagged_too(no_bridge, fake_desktop):
    result = asyncio.run(cdp_actions.screenshot(port=45678, as_base64=True))
    assert result["fallback"] is True
    assert result["via"] == "os-automation"
    assert result["base64"], "the capture itself still comes back — flagged, not withheld"


def test_page_capture_carries_no_fallback_flag(monkeypatch, tmp_path):
    bridge = MagicMock()

    async def _shot(name=None, full_page=False):  # noqa: ARG001
        return str(tmp_path / "page.png")

    bridge.screenshot = _shot

    async def _bridge(port, tab_index=0):  # noqa: ARG001
        return bridge

    async def _select(b, tab, url):  # noqa: ARG001
        return None

    monkeypatch.setattr(cdp_actions, "_try_bridge", _bridge)
    monkeypatch.setattr(cdp_actions, "_select", _select)
    result = asyncio.run(cdp_actions.screenshot(port=45678, out="page.png"))
    assert result["via"] == "cdp"
    assert "fallback" not in result and "note" not in result


def test_cli_warns_a_human_about_the_fallback(monkeypatch):
    """The human path prints the note as a WARNING, never as the green success line."""
    from navig.commands import cdp as cdp_cmd

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(cdp_cmd.ch, "warning", lambda msg: calls.append(("warning", msg)))
    monkeypatch.setattr(cdp_cmd.ch, "success", lambda msg: calls.append(("success", msg)))
    monkeypatch.setattr(cdp_cmd, "_resolve_port", lambda _explicit, port: port)

    async def _fallback(port, **_kw):
        return {"ok": True, "via": "os-automation", "backend": "x", "fallback": True,
                "note": f"no CDP target on port {port} — captured the FULL SCREEN", "path": "p.png"}

    # The command imports the module lazily inside its body, so patching the module attribute
    # is what it will see.
    monkeypatch.setattr(cdp_actions, "screenshot", _fallback)
    cdp_cmd.cdp_screenshot(port=45678, out="p.png", full_page=False, tab=None, url=None, json_out=False)
    kinds = [k for k, _ in calls]
    assert kinds and kinds[0] == "warning", calls
    assert "FULL SCREEN" in calls[0][1]
    assert all("FULL SCREEN" not in m for k, m in calls if k == "success"), (
        "the fallback note must not be printed as a success line"
    )
