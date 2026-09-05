"""`allowed_domains` is a guardrail — it must not be satisfied by a substring.

Both navigation gates (`StealthController._check_domain` and
`BrowserController._check_domain_allowed`) matched the allow list with `pattern in domain`. So an
operator who restricted automation to `example.com` was also permitting:

* `example.com.evil.net` — an attacker-controlled host that merely STARTS with the allowed one,
* `notexample.com`       — a different site that merely CONTAINS it.

`navig.browser.origin_match` already exists for precisely this and states the rule outright — "no
fuzzy/substring matching, ever" — naming `github.com.evil.com` as the attack it prevents. It is
public-suffix aware (`a.github.io` is not `b.github.io`) and normalises IDN homographs. Both gates
now use it. The BLOCK list keeps substring semantics on purpose: over-blocking is the safe
direction, and tightening it would silently un-block existing config entries.
"""

from __future__ import annotations

import pytest

from navig.browser.controller import BrowserConfig, BrowserController
from navig.browser.stealth import StealthConfig, StealthController


def _stealth(allowed: list[str], blocked: list[str] | None = None) -> StealthController:
    cfg = StealthConfig()
    cfg.allowed_domains = allowed
    cfg.blocked_domains = blocked or []
    return StealthController(cfg)


def _controller(allowed: list[str], blocked: list[str] | None = None) -> BrowserController:
    return BrowserController(
        BrowserConfig(allowed_domains=allowed, blocked_domains=blocked or [])
    )


_CASES = [
    pytest.param("https://example.com/ok", True, id="exact-host"),
    pytest.param("https://sub.example.com/ok", True, id="subdomain-allowed"),
    pytest.param("https://deep.sub.example.com/ok", True, id="deep-subdomain-allowed"),
    pytest.param("https://example.com.evil.net/pwn", False, id="suffix-attack"),
    pytest.param("https://notexample.com/", False, id="contains-attack"),
    pytest.param("https://evil.net/?u=example.com", False, id="allowed-name-in-query"),
    pytest.param("https://evil.net/", False, id="unrelated"),
]


@pytest.mark.parametrize("url, allowed", _CASES)
def test_stealth_allowlist_is_not_substring_matched(url: str, allowed: bool) -> None:
    assert _stealth(["example.com"])._check_domain(url) is allowed


@pytest.mark.parametrize("url, allowed", _CASES)
def test_controller_allowlist_is_not_substring_matched(url: str, allowed: bool) -> None:
    """The same gate exists twice — both had the same hole."""
    assert _controller(["example.com"])._check_domain_allowed(url) is allowed


def test_wildcard_prefix_still_understood() -> None:
    """Config commonly writes `*.example.com`; the star is stripped, not matched literally."""
    assert _stealth(["*.example.com"])._check_domain("https://sub.example.com/") is True
    assert _stealth(["*.example.com"])._check_domain("https://example.com.evil.net/") is False


def test_empty_allowlist_allows_everything() -> None:
    """Unchanged behaviour: no allow list configured means no restriction."""
    assert _stealth([])._check_domain("https://anything.example/") is True
    assert _controller([])._check_domain_allowed("https://anything.example/") is True


def test_block_list_still_wins_and_stays_substring() -> None:
    """Blocking is deliberately broad — over-blocking is the safe direction."""
    sc = _stealth(["example.com"], ["evil"])
    assert sc._check_domain("https://evil.example.com/") is False   # blocked beats allowed
    assert _stealth([], ["tracker.net"])._check_domain("https://ads.tracker.net/") is False


def test_public_suffix_hosts_are_not_conflated() -> None:
    """`a.github.io` and `b.github.io` are separate sites — the matcher is suffix-list aware."""
    assert _stealth(["alice.github.io"])._check_domain("https://alice.github.io/x") is True
    assert _stealth(["alice.github.io"])._check_domain("https://bob.github.io/x") is False
