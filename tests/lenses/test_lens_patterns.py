"""Lens URL patterns: Chrome match patterns, Greasemonkey globs, /regex/ includes.

The emitted RegExp sources are evaluated by the page runtime in the browser; these
tables check them in Python (the sources only use syntax both engines share), and
tests/lenses/test_lens_runtime_browser.py confirms a real page agrees.
"""

from __future__ import annotations

import pytest

from navig.lenses.patterns import PatternError, include_to_regex, match_pattern_to_regex

MATCH_TABLE = [
    # pattern, url, expected
    ("*://example.com/*", "https://example.com/", True),
    ("*://example.com/*", "http://example.com/a/b?q=1", True),
    ("*://example.com/*", "https://example.com:8443/x", True),
    ("*://example.com/*", "https://www.example.com/", False),
    ("*://example.com/*", "https://example.com.evil.net/", False),
    ("*://example.com/*", "ftp://example.com/", False),
    ("*://*.example.com/*", "https://example.com/", True),
    ("*://*.example.com/*", "https://a.b.example.com/x", True),
    ("*://*.example.com/*", "https://notexample.com/", False),
    ("https://example.com/watch*", "https://example.com/watch?v=1", True),
    ("https://example.com/watch*", "https://example.com/shorts/1", False),
    ("https://example.com/", "https://example.com/", True),
    ("https://example.com/", "https://example.com/?q", False),
    ("http://*/*", "http://anything.test/x", True),
    ("http://*/*", "https://anything.test/x", False),
    ("file:///*", "file:///C:/x.html", True),
    ("<all_urls>", "https://x.y/", True),
    ("<all_urls>", "chrome://settings", False),
]


@pytest.mark.parametrize(("pattern", "url", "expected"), MATCH_TABLE)
def test_match_pattern_table(pattern: str, url: str, expected: bool) -> None:
    assert match_pattern_to_regex(pattern).test(url) is expected


@pytest.mark.parametrize(
    "bad",
    ["example.com/*", "https://example.com", "gopher://x/*", "https://a*.com/*", "https://*"],
)
def test_invalid_match_patterns_are_rejected_with_the_pattern_named(bad: str) -> None:
    with pytest.raises(PatternError, match="@match"):
        match_pattern_to_regex(bad)


INCLUDE_TABLE = [
    ("*", "https://anything/", True),
    ("https://example.com/*", "https://example.com/a", True),
    ("https://example.com/*", "https://example.com.evil/", False),
    ("http*://*.google.tld/*", "https://www.google.co.uk/search", True),
    ("http*://*.google.tld/*", "https://www.google.com/", True),
    ("http*://*.google.tld/*", "https://www.google.evil/x", True),  # .tld is loose by design
    ("/^https:\\/\\/news\\.ycombinator\\.com\\//", "https://news.ycombinator.com/item", True),
    ("/^https:\\/\\/news\\.ycombinator\\.com\\//", "http://news.ycombinator.com/", False),
    ("/EXAMPLE/i", "https://example.com/", True),
]


@pytest.mark.parametrize(("pattern", "url", "expected"), INCLUDE_TABLE)
def test_include_table(pattern: str, url: str, expected: bool) -> None:
    assert include_to_regex(pattern).test(url) is expected


def test_stateful_regex_flags_are_dropped() -> None:
    # `g`/`y` make RegExp.test() advance lastIndex, so the SAME url would alternate
    # true/false across calls in the browser. They never change whether a URL matches.
    assert include_to_regex("/example/gi").flags == "i"


def test_bad_regex_flags_are_rejected() -> None:
    with pytest.raises(PatternError):
        include_to_regex("/x/q")
