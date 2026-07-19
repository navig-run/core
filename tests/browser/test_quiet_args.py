"""Quiet Chromium launch flags — no first-run/welcome tab, no EU search-engine choice screen."""

from __future__ import annotations

from pathlib import Path

from navig.browser.targets import BROWSER_APPS, CHROMIUM_QUIET_ARGS, _build_launch_args


def test_quiet_args_contains_the_key_flags():
    assert "--disable-search-engine-choice-screen" in CHROMIUM_QUIET_ARGS  # the "choose a search engine" screen
    assert "--no-first-run" in CHROMIUM_QUIET_ARGS                          # the welcome tab
    assert "--no-default-browser-check" in CHROMIUM_QUIET_ARGS


def test_quiet_args_has_no_disable_features():
    # Chrome honours only the LAST --disable-features switch; folding one in here would
    # silently clobber the extension loader's own --disable-features (cdp_actions).
    assert not any(a.startswith("--disable-features") for a in CHROMIUM_QUIET_ARGS)


def test_build_launch_args_browser_gets_quiet_flags():
    args = _build_launch_args("chrome.exe", "chrome", 9222, "/tmp/prof", None, None)
    assert "--remote-debugging-port=9222" in args
    assert "--user-data-dir=/tmp/prof" in args
    for flag in CHROMIUM_QUIET_ARGS:
        assert flag in args


def test_build_launch_args_electron_gets_no_quiet_flags():
    # Electron apps (Discord/Notion/…) have no first-run/search-engine screens.
    electron = next((a for a in ("discord", "notion", "slack") if a not in BROWSER_APPS), None)
    assert electron is not None, "expected at least one non-browser app id"
    args = _build_launch_args("app.exe", electron, 9223, None, None, None)
    assert "--disable-search-engine-choice-screen" not in args
    assert "--no-first-run" not in args


def test_build_launch_args_extra_args_come_after_quiet():
    args = _build_launch_args("chrome.exe", "chrome", 9222, None, None, ["--headless=new"])
    assert "--headless=new" in args
    assert args.index("--headless=new") > args.index("--disable-search-engine-choice-screen")


def test_hardened_build_args_are_quiet():
    from navig.browser.hardened import HardenedController

    c = HardenedController(webrtc_protection=False)
    args = c._build_args(Path("chrome.exe"))
    assert "--disable-search-engine-choice-screen" in args
    assert "--no-first-run" in args
    assert "--no-default-browser-check" in args
