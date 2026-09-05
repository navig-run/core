"""Tests for the scheduled AI briefing composer.

This module had no tests at all, which is why two defects survived in `_compose_raw`:

1. Every source failure was swallowed by a bare `pass` with no log, so a crashed
   life dashboard produced "No new activity to brief on yet — add data in Apps to
   start tracking" — a message that sends the operator to add data they already have.
2. `build_spaces_briefing_lines` is a *display* helper that returns a human-facing
   `["_No spaces available for briefing._"]` placeholder when there are no spaces.
   Appending it made `raw` non-empty on a fresh install, so the real empty state
   never fired and the LLM was handed a markdown placeholder as its only input.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from navig.notify import briefings

pytestmark = pytest.mark.asyncio


@pytest.fixture
def sources(monkeypatch):
    """Neutralise every briefing source; each test opts into what it needs.

    Patched at the DEFINING module, which is where `_compose_raw`'s function-local
    imports resolve them.
    """
    import navig.commands.life_dashboard as life
    import navig.llm.generate as llm
    import navig.spaces.briefing as spaces_brief
    import navig.spaces.progress as spaces_prog

    state = {
        "dashboard": {"text": ""},
        "space_rows": [],
        "space_lines": ["_No spaces available for briefing._"],
    }

    monkeypatch.setattr(life, "build_dashboard", lambda *a, **k: state["dashboard"])
    monkeypatch.setattr(spaces_prog, "collect_spaces_progress", lambda *a, **k: state["space_rows"])
    monkeypatch.setattr(
        spaces_brief, "build_spaces_briefing_lines", lambda *a, **k: state["space_lines"]
    )
    # Deterministic "polish" so no test reaches a real provider.
    monkeypatch.setattr(llm, "llm_generate", lambda **kw: "POLISHED")

    # navig_harbor is a CLOSED plugin: present on some machines, absent on others.
    # Left unpatched it feeds real finance data in and these tests pass or fail
    # depending on whose machine runs them. Neutralise it when importable; when it
    # isn't, there is nothing to neutralise.
    try:
        from navig_harbor import bizops
    except ImportError:
        pass
    else:
        monkeypatch.setattr(bizops, "get_overview", lambda *a, **k: {})
    return state


def _hide_harbor(monkeypatch) -> None:
    """Make ``from navig_harbor import bizops`` raise ImportError.

    A ``None`` entry in sys.modules makes the import machinery raise, so this models
    an install without the closed plugin whether or not it is installed here.
    """
    import sys

    monkeypatch.setitem(sys.modules, "navig_harbor", None)


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Capture what the briefing hands to the notification router."""
    dispatch = AsyncMock(return_value={"channels": [{"channel": "deck", "ok": True}]})
    monkeypatch.setattr(
        briefings, "get_notification_router", lambda: MagicMock(dispatch=dispatch)
    )
    return dispatch


@pytest.fixture
def enabled(monkeypatch):
    from navig.notify import prefs

    monkeypatch.setattr(
        prefs,
        "get_settings",
        lambda: {"briefing_enabled": True, "briefing_channels": []},
    )


def _sent_text(dispatch) -> str:
    return dispatch.await_args.args[2]


# ── source failures must not read as "you have no data" ───────────────────────


async def test_all_sources_failing_says_so_instead_of_asking_for_data(
    sources, captured_dispatch, enabled, monkeypatch
):
    import navig.commands.life_dashboard as life
    import navig.spaces.progress as spaces_prog

    def _boom(*a, **k):
        raise RuntimeError("data store unreachable")

    monkeypatch.setattr(life, "build_dashboard", _boom)
    monkeypatch.setattr(spaces_prog, "collect_spaces_progress", _boom)

    await briefings.build_and_dispatch_briefing()

    text = _sent_text(captured_dispatch)
    assert "add data in Apps" not in text, (
        "every source crashed — telling the operator to add data they already have "
        f"is the wrong message. Got: {text!r}"
    )
    assert "Couldn't build" in text
    assert "life dashboard" in text and "spaces progress" in text


async def test_a_partial_failure_is_disclosed_alongside_the_content(
    sources, captured_dispatch, enabled, monkeypatch
):
    """A briefing silently missing a section is the same misleading shape."""
    import navig.spaces.progress as spaces_prog

    sources["dashboard"] = {"text": "3 habits due"}

    def _boom(*a, **k):
        raise RuntimeError("space scan failed")

    monkeypatch.setattr(spaces_prog, "collect_spaces_progress", _boom)

    await briefings.build_and_dispatch_briefing()

    text = _sent_text(captured_dispatch)
    assert "POLISHED" in text, "the surviving content must still be briefed"
    assert "unavailable: spaces progress" in text


async def test_a_missing_optional_plugin_is_not_reported_as_a_failure(
    sources, captured_dispatch, enabled, monkeypatch
):
    """navig_harbor is a closed plugin most installs don't have — absence is normal."""
    _hide_harbor(monkeypatch)
    sources["dashboard"] = {"text": "all clear"}

    await briefings.build_and_dispatch_briefing()

    text = _sent_text(captured_dispatch)
    assert "unavailable" not in text, f"an absent optional plugin is not a fault: {text!r}"


async def test_an_optional_plugin_that_is_PRESENT_but_broken_is_reported(
    sources, captured_dispatch, enabled, monkeypatch
):
    """Absent is fine; installed-and-raising is a real fault worth disclosing."""
    pytest.importorskip("navig_harbor", reason="closed plugin not installed here")
    from navig_harbor import bizops

    sources["dashboard"] = {"text": "all clear"}
    monkeypatch.setattr(
        bizops, "get_overview", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ledger down"))
    )

    await briefings.build_and_dispatch_briefing()

    assert "unavailable: finance overview" in _sent_text(captured_dispatch)


# ── the genuine empty state ───────────────────────────────────────────────────


async def test_a_fresh_install_gets_the_real_empty_state(sources, captured_dispatch, enabled):
    """No data and no failures — the placeholder must not masquerade as content."""
    await briefings.build_and_dispatch_briefing()

    text = _sent_text(captured_dispatch)
    assert "No new activity to brief on yet" in text
    assert "_No spaces available for briefing._" not in text, (
        "a display placeholder reached the briefing body — it must not be treated as data"
    )
    assert "POLISHED" not in text, "there was nothing to summarise; the LLM must not be called"


async def test_real_space_data_is_briefed(sources, captured_dispatch, enabled):
    sources["space_rows"] = [object()]        # the data api reports rows...
    sources["space_lines"] = ["*Spaces Progress:*", "- `alpha` (local) — 40.0% · ship it"]

    await briefings.build_and_dispatch_briefing()

    assert "POLISHED" in _sent_text(captured_dispatch)


# ── enable/force gating is unchanged ─────────────────────────────────────────


async def test_disabled_briefing_is_skipped(sources, captured_dispatch, monkeypatch):
    from navig.notify import prefs

    monkeypatch.setattr(
        prefs, "get_settings", lambda: {"briefing_enabled": False, "briefing_channels": []}
    )

    result = await briefings.build_and_dispatch_briefing()

    assert result == {"skipped": "disabled"}
    captured_dispatch.assert_not_awaited()


async def test_force_overrides_the_disabled_setting(sources, captured_dispatch, monkeypatch):
    from navig.notify import prefs

    monkeypatch.setattr(
        prefs, "get_settings", lambda: {"briefing_enabled": False, "briefing_channels": []}
    )
    sources["dashboard"] = {"text": "manual send"}

    await briefings.build_and_dispatch_briefing(force=True)

    captured_dispatch.assert_awaited_once()
