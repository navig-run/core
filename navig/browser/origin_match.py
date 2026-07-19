"""Origin-binding — the anti-phishing keystone for browser auto-login.

A stored credential/session for ``example.com`` may be filled/restored **only**
on that registrable domain (eTLD+1), over ``https``, and only when the domain's
IDN/punycode form matches. This is the single most important defence for a
*fully autonomous* autofill: it is what stops the agent from being tricked into
typing your password on ``g00gle.com``, ``github.com.evil.com``, or a Cyrillic
homograph of a real site.

Design rules (all enforced here):
- **Registrable domain** (public-suffix aware): ``login.github.com`` ≡ ``github.com``,
  but ``alice.github.io`` ≢ ``bob.github.io`` (``github.io`` is a public suffix).
- **Punycode/IDN normalisation**: both sides are canonicalised to ASCII/punycode
  before comparison, so a look-alike unicode host never equals the real one.
- **https-only** by default (``allow_insecure`` opt-in for localhost/dev).
- **No fuzzy/substring matching, ever.** Exact registrable-domain equality.

Uses :mod:`tldextract` (its *bundled* public-suffix snapshot — no network) when
available, with a self-contained fallback covering common multi-part suffixes.
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

__all__ = [
    "registrable_domain",
    "same_registrable_domain",
    "credential_matches",
    "host_of",
]

# Minimal multi-part public-suffix fallback used only if tldextract is missing.
# tldextract (with the full PSL) is the primary path; this keeps the common
# cases correct so the security check never silently degrades to "last two
# labels" (which would wrongly treat a.github.io == b.github.io as matching).
_FALLBACK_MULTI_SUFFIXES: frozenset[str] = frozenset(
    {
        "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk",
        "com.au", "net.au", "org.au", "gov.au", "edu.au",
        "co.nz", "co.jp", "or.jp", "ne.jp", "go.jp",
        "co.in", "com.br", "com.cn", "com.mx", "com.tr",
        "co.za", "com.sg", "com.hk", "co.kr",
        # provider-owned suffixes where each label is a separate site
        "github.io", "gitlab.io", "pages.dev", "workers.dev",
        "vercel.app", "netlify.app", "herokuapp.com", "web.app",
        "firebaseapp.com", "s3.amazonaws.com", "blogspot.com",
    }
)


@lru_cache(maxsize=1)
def _tld_extract():
    """Return a tldextract callable using its bundled snapshot (no network), or None."""
    try:
        import tldextract  # noqa: PLC0415

        # suffix_list_urls=() forces the packaged snapshot — deterministic, offline.
        # include_psl_private_domains=True so provider-owned suffixes (github.io,
        # *.pages.dev, blogspot.com, …) are treated as public suffixes — i.e.
        # alice.github.io and bob.github.io are DIFFERENT sites. Security-correct.
        return tldextract.TLDExtract(
            suffix_list_urls=(),
            cache_dir=None,
            include_psl_private_domains=True,
        )
    except Exception:  # noqa: BLE001
        return None


def _idna_normalize(host: str) -> str:
    """Canonicalise a hostname to lowercase ASCII/punycode.

    Both the page host and the stored domain pass through this before
    comparison, so a homograph host produces a different punycode string than
    the legitimate domain and cannot match. Falls back to the lowercased host
    when strict IDNA rejects it (we never want to crash the security check).
    """
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return ""
    if host.isascii():
        return host
    try:
        import idna  # noqa: PLC0415

        # uts46 folds confusables per the IDNA table; per-label to tolerate
        # a mixed host and to match how tldextract/browsers segment labels.
        return ".".join(
            idna.encode(label, uts46=True).decode("ascii") if label else ""
            for label in host.split(".")
        )
    except Exception:  # noqa: BLE001
        try:
            return host.encode("idna").decode("ascii")
        except Exception:  # noqa: BLE001
            return host


def host_of(host_or_url: str) -> str:
    """Extract the bare hostname from a URL or a host string (no scheme/port/path)."""
    v = (host_or_url or "").strip()
    if not v:
        return ""
    parsed = urlparse(v if "://" in v else "//" + v, scheme="")
    host = parsed.hostname or ""
    return host.lower()


def _registrable_from_labels(host: str) -> str:
    """Fallback registrable-domain using the embedded multi-suffix set."""
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last2 = ".".join(labels[-2:])
    last3 = ".".join(labels[-3:])
    if last2 in _FALLBACK_MULTI_SUFFIXES:
        return ".".join(labels[-3:])  # registrable = label + 2-part suffix
    if last3 in _FALLBACK_MULTI_SUFFIXES:
        return ".".join(labels[-4:])
    return last2


def registrable_domain(host_or_url: str) -> str:
    """Return the registrable (eTLD+1) domain in canonical punycode/lowercase.

    ``https://Login.GitHub.com/x`` → ``github.com``; ``alice.github.io`` →
    ``alice.github.io``. Bare hosts, IPs, and ``localhost`` return themselves
    (normalised). Returns "" for empty input.
    """
    host = _idna_normalize(host_of(host_or_url))
    if not host or host == "localhost":
        return host
    # Bare IPv4/IPv6 → return as-is (no registrable-domain concept).
    if host.replace(".", "").isdigit() or ":" in host:
        return host

    extract = _tld_extract()
    if extract is not None:
        try:
            ext = extract(host)
            if ext.registered_domain:
                return ext.registered_domain.lower()
            # No known public suffix (e.g. intranet host) → whole host.
            return host
        except Exception:  # noqa: BLE001
            pass
    return _registrable_from_labels(host)


def same_registrable_domain(a: str, b: str) -> bool:
    """True when two hosts/URLs share the same non-empty registrable domain."""
    ra = registrable_domain(a)
    rb = registrable_domain(b)
    return bool(ra) and ra == rb


def credential_matches(
    page_url: str,
    login_domain: str,
    *,
    allow_insecure: bool = False,
) -> bool:
    """Decide whether a credential for *login_domain* may act on *page_url*.

    Enforces: https-only (unless *allow_insecure*), and exact registrable-domain
    equality after IDN normalisation. This is the gate every restore/fill passes.

    Note: *page_url* must be the **top-frame** URL. Cross-origin iframe binding
    is the caller's responsibility (autofill passes the top-frame URL here).
    """
    if not page_url or not login_domain:
        return False
    parsed = urlparse(page_url if "://" in page_url else "https://" + page_url)
    scheme = (parsed.scheme or "").lower()
    if not allow_insecure and scheme != "https":
        return False
    if allow_insecure and scheme not in ("https", "http", ""):
        return False
    page_host = parsed.hostname or ""
    if not page_host:
        return False
    return same_registrable_domain(page_host, login_domain)
