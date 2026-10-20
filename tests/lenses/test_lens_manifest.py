"""Lens header parsing — a Tampermonkey header in, a manifest out, nothing dropped."""

from __future__ import annotations

import pytest

from navig.lenses.manifest import LensFormatError, derive_id, parse_manifest

GREASY = """// ==UserScript==
// @name         Hide Shorts
// @namespace    https://example.org/scripts
// @version      2.1
// @description  Removes Shorts shelves
// @match        *://*.youtube.com/*
// @exclude-match *://music.youtube.com/*
// @include      /^https:\\/\\/m\\.youtube\\.com\\//
// @run-at       document-start
// @noframes
// @grant        GM_addStyle
// @grant        GM_xmlhttpRequest
// @license      MIT
// @supportURL   https://example.org/issues
// ==/UserScript==
GM_addStyle("ytd-reel-shelf-renderer{display:none}");
"""


def test_parses_a_real_world_header() -> None:
    m = parse_manifest(GREASY)
    assert m.name == "Hide Shorts"
    assert m.version == "2.1"
    assert m.matches == ["*://*.youtube.com/*"]
    assert m.exclude_matches == ["*://music.youtube.com/*"]
    assert m.run_at == "document-start"
    assert m.noframes is True
    assert m.grants == ["GM_addStyle", "GM_xmlhttpRequest"]
    assert len(m.include_res) == 2 and len(m.exclude_res) == 1


def test_unknown_keys_are_kept_not_dropped() -> None:
    m = parse_manifest(GREASY)
    assert m.extra == {"license": ["MIT"], "supportURL": ["https://example.org/issues"]}
    assert m.to_dict()["extra"]["license"] == ["MIT"]


def test_unsupported_grant_is_a_warning_and_never_reaches_the_runtime() -> None:
    # The fixture grants GM_xmlhttpRequest, which the page runtime now implements — so
    # the example is one it still does not implement. Same property either way: a grant
    # outside SUPPORTED_GRANTS warns, and never reaches the script.
    src = GREASY.replace("// @grant        GM_xmlhttpRequest", "// @grant        GM_getResourceText")
    m = parse_manifest(src)
    assert m.unsupported_grants == ["GM_getResourceText"]
    assert any("GM_getResourceText" in w for w in m.warnings)
    assert "GM_getResourceText" not in m.runtime_dict()["grants"]


def test_xhr_and_value_change_listener_grants_reach_the_runtime() -> None:
    """The two APIs this runtime gained must stop warning and start being passed.

    A regression here is silent in the worst way: the script feature-detects, sees
    `undefined`, and quietly takes its degraded path (VK Toolkit loses its cross-tab
    merge and its requests).
    """
    for grant in (
        "GM_xmlhttpRequest",
        "GM_addValueChangeListener",
        "GM_removeValueChangeListener",
        "GM.xmlHttpRequest",
        "GM.addValueChangeListener",
    ):
        src = GREASY.replace("// @grant        GM_xmlhttpRequest", f"// @grant        {grant}")
        m = parse_manifest(src)
        assert m.unsupported_grants == [], f"{grant} must be supported"
        assert not any(grant in w for w in m.warnings), f"{grant} must not warn"
        assert grant in m.runtime_dict()["grants"], f"{grant} must reach the script"


def test_id_is_stable_across_reinstalls_and_namespaced() -> None:
    a = parse_manifest(GREASY).id
    assert a == parse_manifest(GREASY.replace("2.1", "3.0")).id  # update, not a duplicate
    assert a == derive_id("Hide Shorts", "https://example.org/scripts")
    assert a != derive_id("Hide Shorts", "someone-else")


def test_navig_id_overrides_and_is_validated() -> None:
    src = GREASY.replace("// @noframes", "// @navig-id hide-shorts")
    assert parse_manifest(src).id == "hide-shorts"
    with pytest.raises(LensFormatError, match="@navig-id"):
        parse_manifest(GREASY.replace("// @noframes", "// @navig-id ../etc"))


def test_no_match_runs_nowhere_with_a_warning() -> None:
    src = "// ==UserScript==\n// @name x\n// ==/UserScript==\nalert(1)\n"
    m = parse_manifest(src)
    assert m.runs_anywhere is False
    assert any("runs nowhere" in w for w in m.warnings)


@pytest.mark.parametrize(
    ("src", "msg"),
    [
        ("", "empty"),
        ("alert(1)", "no ==UserScript== header"),
        ("// ==UserScript==\n// @match *://x/*\n// ==/UserScript==\n", "@name"),
        ("// ==UserScript==\n// @name x\n// @match nope\n// ==/UserScript==\n", "@match"),
        (
            "// ==UserScript==\n// @name x\n// @require http://cdn.test/a.js\n// ==/UserScript==\n",
            "https",
        ),
    ],
)
def test_invalid_sources_say_what_to_fix(src: str, msg: str) -> None:
    with pytest.raises(LensFormatError, match=msg):
        parse_manifest(src)


def test_unknown_run_at_falls_back_with_a_warning() -> None:
    m = parse_manifest(GREASY.replace("document-start", "context-menu"))
    assert m.run_at == "document-idle"
    assert any("context-menu" in w for w in m.warnings)


def test_the_api_dict_carries_the_exact_runtime_regexes() -> None:
    """Host UIs ("Lenses on this page") match with the RegExps the page runtime runs."""
    m = parse_manifest(
        "// ==UserScript==\n// @name x\n// @match *://*.example.com/*\n"
        "// @exclude-match *://admin.example.com/*\n// ==/UserScript==\n"
    )
    d = m.to_dict()
    assert d["include_res"] == [r.to_dict() for r in m.include_res] and d["include_res"]
    assert d["exclude_res"] == [r.to_dict() for r in m.exclude_res] and d["exclude_res"]
    assert all(set(r) == {"source", "flags"} for r in d["include_res"] + d["exclude_res"])
