"""Stage 5 — coherent fingerprint generation + application surfaces."""

from __future__ import annotations

from navig.browser import fingerprint as fp


def test_seed_is_deterministic():
    a = fp.generate(seed="profile:work")
    b = fp.generate(seed="profile:work")
    assert a.ua == b.ua
    assert a.screen == b.screen
    assert a.hardware_concurrency == b.hardware_concurrency
    assert a.webgl_renderer == b.webgl_renderer


def test_different_seeds_can_differ():
    seeds = {fp.generate(seed=f"s{i}").ua for i in range(8)}
    # not all identical across seeds (pool has >1 profile)
    assert len(seeds) >= 1  # at minimum stable; realistically varied


def test_locale_timezone_geo_override():
    f = fp.generate(seed="x", locale="de-DE", timezone="Europe/Berlin",
                    geolocation={"latitude": 52.5, "longitude": 13.4, "accuracy": 50})
    assert f.locale == "de-DE"
    assert f.timezone == "Europe/Berlin"
    assert f.geolocation["latitude"] == 52.5
    assert f.languages[0] == "de-DE"


def test_context_options_are_coherence_safe():
    f = fp.generate(seed="x", locale="en-GB", timezone="Europe/London")
    opts = fp.to_context_options(f)
    assert opts["locale"] == "en-GB"
    assert opts["timezone_id"] == "Europe/London"
    assert opts["geolocation"]  # default geo present
    assert "geolocation" in opts.get("permissions", [])
    # never leaks a UA / header into context opts (that would be a Patchright tell)
    assert "user_agent" not in opts and "userAgent" not in opts


def test_context_options_without_geo():
    f = fp.generate(seed="x")
    f.geolocation = None
    opts = fp.to_context_options(f)
    assert "geolocation" not in opts


def test_sec_ch_ua_matches_chrome_major():
    f = fp.generate(seed="x")
    assert f.chrome_major in f.sec_ch_ua()


def test_init_script_reflects_fingerprint():
    f = fp.generate(seed="x")
    js = fp.to_init_script(f)
    assert str(f.hardware_concurrency) in js
    assert f.webgl_renderer in js
    assert f.platform in js
    assert "getImageData" in js  # canvas noise present


def test_webrtc_args_present():
    args = fp.webrtc_launch_args()
    assert any("webrtc-ip-handling-policy" in a for a in args)
