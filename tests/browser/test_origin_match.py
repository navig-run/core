"""Stage 2a — origin-binding / anti-phishing keystone.

This is the security floor for fully-autonomous autofill, so the matrix is
adversarial: homographs, look-alikes, the ``github.com.evil.com`` suffix trick,
public-suffix sites (``github.io``), and scheme enforcement.
"""

from __future__ import annotations

import pytest

from navig.browser.origin_match import (
    credential_matches,
    registrable_domain,
    same_registrable_domain,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://github.com/login", "github.com"),
        ("https://Login.GitHub.com/x?y=1", "github.com"),
        ("github.com", "github.com"),
        ("https://example.co.uk/a", "example.co.uk"),
        ("https://sub.example.co.uk", "example.co.uk"),
        ("alice.github.io", "alice.github.io"),  # github.io is a public suffix
        ("localhost", "localhost"),
        ("127.0.0.1", "127.0.0.1"),
    ],
)
def test_registrable_domain(value, expected):
    assert registrable_domain(value) == expected


# ---------------------------------------------------------------------------
# same_registrable_domain — subdomain vs different-site
# ---------------------------------------------------------------------------


def test_subdomain_matches_parent():
    assert same_registrable_domain("https://login.github.com", "github.com")


def test_public_suffix_siblings_do_not_match():
    # alice.github.io and bob.github.io are DIFFERENT sites.
    assert not same_registrable_domain("https://alice.github.io", "bob.github.io")


def test_different_sites_do_not_match():
    assert not same_registrable_domain("https://g00gle.com", "google.com")


# ---------------------------------------------------------------------------
# credential_matches — the gate every fill/restore passes
# ---------------------------------------------------------------------------


def test_exact_https_match():
    assert credential_matches("https://github.com/login", "github.com")


def test_subdomain_https_match():
    assert credential_matches("https://login.github.com/", "github.com")


def test_suffix_trick_is_rejected():
    # The classic phishing host: registrable is evil.com, not github.com.
    assert not credential_matches("https://github.com.evil.com/login", "github.com")


def test_lookalike_is_rejected():
    assert not credential_matches("https://g00gle.com/login", "google.com")


def test_http_is_rejected_by_default():
    assert not credential_matches("http://github.com/login", "github.com")


def test_http_allowed_when_insecure_opt_in():
    # dev/localhost path
    assert credential_matches("http://localhost:3000/login", "localhost", allow_insecure=True)


def test_homograph_is_rejected():
    # Cyrillic 'а' (U+0430) in "exаmple.com" — visually identical, different site.
    homograph = "https://exаmple.com/login"
    assert not credential_matches(homograph, "example.com")


def test_empty_inputs_are_rejected():
    assert not credential_matches("", "github.com")
    assert not credential_matches("https://github.com", "")


def test_co_uk_sibling_rejected():
    assert not credential_matches("https://evil.co.uk/login", "example.co.uk")
    assert credential_matches("https://example.co.uk/login", "example.co.uk")
