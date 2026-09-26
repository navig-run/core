"""An agent's stealth browser must not put a window on the operator's screen.

The visibility work made every agent-facing launch windowless — except this one, which
legitimately cannot be. ``StealthConfig.headless`` defaults to ``False`` on purpose
("headless=False is harder to detect for most CAPTCHAs"), so forcing headless here would
defeat the only reason to reach for this engine.

A bare ``StealthController()`` therefore opened a real, visible, audible window on every
stealth call from ``browser_tool`` — the same blank-window complaint, arriving through the
one path that cannot go headless.

The engine already had the answer: a large negative window position is "real enough to
defeat headless bot-detection, invisible to the user". navig-download's TikTok path has run
that way for the same reason, so this is the house pattern rather than a new idea.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from navig.agent.tools import browser_session


def _stealth_branch_source() -> str:
    return inspect.getsource(browser_session._open_controller)


def _calls_named(src: str, name: str) -> list[ast.Call]:
    """Every CALL to *name* in *src*, resolved on the AST.

    ⚠ Deliberately not a substring search. The fix this file guards carries a comment
    explaining why a bare `StealthController()` was wrong — and a textual scan matched that
    PROSE and failed on the fixed code. Same trap the repo's other guards document: assert
    on the tree, never on the text, or the explanation of a bug reads as the bug.
    """
    tree = ast.parse(textwrap.dedent(src))
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and ((isinstance(n.func, ast.Name) and n.func.id == name)
             or (isinstance(n.func, ast.Attribute) and n.func.attr == name))
    ]


def test_the_stealth_branch_does_not_launch_a_bare_controller():
    """A `StealthController()` with no config is a visible, unmuted window."""
    bare = [c for c in _calls_named(_stealth_branch_source(), "StealthController")
            if not c.args and not c.keywords]
    assert not bare, (
        "StealthController is constructed with no config, which defaults to a VISIBLE, "
        "unmuted window — an agent has no screen. Pass a StealthConfig with an offscreen "
        "window_position."
    )


def test_the_controller_is_given_a_config():
    """Positive form of the rule above: it must actually receive one."""
    calls = _calls_named(_stealth_branch_source(), "StealthController")
    assert calls, "the stealth branch no longer constructs a StealthController"
    assert any(c.args or c.keywords for c in calls), "no configuration is passed"


def test_the_stealth_branch_is_offscreen_and_muted():
    src = _stealth_branch_source()
    assert "window_position" in src, "the stealth branch must place its window offscreen"
    assert "mute_audio" in src, "an offscreen window can still play audio at the operator"


def test_it_stays_headful_because_headless_defeats_the_engine():
    """The fix must NOT be `headless=True` — that removes the reason to use stealth."""
    from navig.browser.stealth import StealthConfig

    cfg = StealthConfig(window_position=(-2400, -2400), mute_audio=True)
    assert cfg.headless is False, (
        "stealth must stay headful; hiding is done by position, not by headless mode"
    )


def test_the_offscreen_position_and_mute_reach_chrome_argv():
    """A config that never reaches the command line hides nothing.

    Pins the two lines in StealthController that translate config into launch args — the
    'written but never wired' failure mode this repo keeps finding.
    """
    from navig.browser.stealth import StealthController

    src = inspect.getsource(StealthController)
    assert "--window-position=" in src, "window_position never becomes a launch arg"
    assert "--mute-audio" in src, "mute_audio never becomes a launch arg"


def test_the_non_stealth_and_fallback_branches_are_still_headless():
    """The other two paths have no anti-detection requirement, so they stay headless."""
    src = _stealth_branch_source()
    assert src.count("headless=True") >= 2, (
        "the plain and stealth-import-failure branches must both stay headless"
    )
