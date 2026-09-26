"""Quiet Chromium launch flags — no first-run/welcome tab, no EU search-engine choice screen."""

from __future__ import annotations

from pathlib import Path

from navig.browser.targets import (
    BROWSER_APPS,
    CHROMIUM_DISABLED_FEATURES,
    CHROMIUM_QUIET_ARGS,
    _build_launch_args,
    merge_disable_features,
)


def test_quiet_args_contains_the_key_flags():
    assert "--disable-search-engine-choice-screen" in CHROMIUM_QUIET_ARGS  # the "choose a search engine" screen
    assert "--no-first-run" in CHROMIUM_QUIET_ARGS                          # the welcome tab
    assert "--no-default-browser-check" in CHROMIUM_QUIET_ARGS
    # Every NAVIG close leaves exit_type = "Crashed" (measured, headless and headed), which
    # raises the "Restore pages?" infobar on the next headed launch. Chrome's own switch.
    assert "--hide-crash-restore-bubble" in CHROMIUM_QUIET_ARGS


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


# ── --disable-features: one switch, or Chrome keeps only the last ─────────────────────


def _disable_switches(args: list[str]) -> list[str]:
    return [a for a in args if a.startswith("--disable-features=")]


def _features(args: list[str]) -> list[str]:
    sw = _disable_switches(args)
    assert len(sw) == 1, f"expected exactly one --disable-features, got {len(sw)}: {sw}"
    return sw[0][len("--disable-features="):].split(",")


def test_browser_launch_disables_the_on_device_model_download():
    """The 4 GB `OptGuideOnDeviceModel` was downloaded into EVERY automation profile.

    Measured 2026-09-15: two named profiles held 4,072 MB each of the identical model,
    against 6 MB and 1 MB of actual login state. Playwright disables exactly these; the
    raw launch never did.
    """
    args = _build_launch_args("chrome.exe", "chrome", 9280, "/tmp/prof", None, None)
    feats = _features(args)
    for name in CHROMIUM_DISABLED_FEATURES:
        assert name in feats, f"{name} missing — the profile will download the on-device model"
    assert "OptimizationGuideModelDownloading" in CHROMIUM_DISABLED_FEATURES  # the one that gates it


def test_electron_apps_do_not_get_the_browser_feature_set():
    """Discord/Notion are Chromium but not Chrome-the-browser; they never fetch the model,
    and a caller's own switches (if any) must still be merged rather than dropped."""
    electron = next((a for a in ("discord", "notion", "slack") if a not in BROWSER_APPS), None)
    assert electron is not None
    assert _disable_switches(_build_launch_args("app.exe", electron, 9333, None, None, None)) == []
    args = _build_launch_args("app.exe", electron, 9333, None, None,
                              ["--disable-features=A", "--disable-features=B"])
    assert _features(args) == ["A", "B"]


def test_the_extension_loader_switch_survives_the_base_set():
    """THE regression this guards. The extension loader adds
    `--disable-features=DisableLoadExtensionCommandLineSwitch`; without it `--load-extension`
    is silently dropped on Stable 137+. A base list adding its own switch used to clobber
    it — so the base list carried none, and every profile paid 4 GB for that caution."""
    args = _build_launch_args(
        "chrome.exe", "chrome", 9280, "/tmp/prof", None,
        ["--load-extension=/x", "--disable-features=DisableLoadExtensionCommandLineSwitch"],
    )
    feats = _features(args)  # asserts exactly ONE switch
    assert "DisableLoadExtensionCommandLineSwitch" in feats
    assert "OptimizationGuideModelDownloading" in feats
    assert "--load-extension=/x" in args


def test_merge_disable_features_is_an_ordered_deduplicated_union():
    out = merge_disable_features(
        ["--a", "--disable-features=X,Y", "--b", "--disable-features=Y,Z", "--c"],
        base=("Z", "W"),
    )
    assert out[:3] == ["--a", "--b", "--c"], "other switches keep their order"
    assert out[-1] == "--disable-features=Z,W,X,Y", "base first, then callers, no repeats"
    assert merge_disable_features(["--a"]) == ["--a"], "nothing to merge ⇒ no switch added"
    assert merge_disable_features(["--disable-features="]) == [], "an empty switch vanishes"
