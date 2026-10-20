"""URL patterns for Lenses — Chrome match patterns, Greasemonkey globs, ``/regex/`` includes.

Every pattern a Lens declares is turned into a JavaScript ``RegExp`` (``source`` + ``flags``)
HERE, once, at save time. The page runtime (``runtime.js``) only ever does
``new RegExp(source, flags).test(url)`` — so the matching rules live in exactly one place
and every host (NAVIG OS pane, ``navig cdp`` browsers, the Chrome extension) agrees on them.

The emitted sources use only syntax that means the same thing in Python ``re`` and JS
``RegExp`` (escapes, ``(?:…)``, ``.*``, ``[^/]``), which is what lets the test-suite check
match tables in Python and trust the browser to agree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Schemes a match pattern may name. `*` means http OR https (Chrome semantics).
_SCHEMES = ("http", "https", "file", "ftp", "ws", "wss")
_MATCH_RE = re.compile(r"^(\*|[a-z][a-z0-9+.-]*)://([^/]*)(/.*)?$")
# Greasemonkey's `.tld` magic: `*://*.google.tld/*` matches google.com, google.co.uk, …
_TLD_RE = r"(?:\.[a-z]{2,63}(?:\.[a-z]{2})?)"


class PatternError(ValueError):
    """A pattern a Lens declared cannot be compiled — the message names the pattern."""


@dataclass(frozen=True)
class JsRegex:
    """A pattern compiled for the page runtime: ``new RegExp(source, flags)``."""

    source: str
    flags: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "flags": self.flags}

    def test(self, url: str) -> bool:
        """Python-side evaluation (tests and `navig lens show --url`). Only valid for
        sources this module EMITTED — a user `/regex/` may use JS-only syntax."""
        flags = re.IGNORECASE if "i" in self.flags else 0
        return re.search(self.source, url, flags) is not None


def _esc(text: str) -> str:
    """Escape literal text for a regex source valid in both Python and JS."""
    return re.sub(r"[.*+?^${}()|\[\]\\/]", lambda m: "\\" + m.group(0), text)


def match_pattern_to_regex(pattern: str) -> JsRegex:
    """Compile a Chrome match pattern (``*://*.example.com/path*``, ``<all_urls>``).

    Chrome semantics: ``*`` scheme = http|https; ``*.host`` matches the host and every
    subdomain; any port is accepted; the path pattern is matched against path + query
    (the fragment never takes part — the runtime strips it before testing).
    """
    p = pattern.strip()
    if p == "<all_urls>":
        return JsRegex(r"^(?:https?|file|ftp|wss?)://")
    m = _MATCH_RE.match(p)
    if not m:
        raise PatternError(f"invalid @match {pattern!r}: expected <scheme>://<host>/<path>")
    scheme, host, path = m.group(1), m.group(2), m.group(3)
    if path is None:
        raise PatternError(f"invalid @match {pattern!r}: missing the path (add '/*')")
    if scheme != "*" and scheme not in _SCHEMES:
        raise PatternError(f"invalid @match {pattern!r}: unsupported scheme {scheme!r}")
    if scheme == "file":
        if host not in ("", "*"):
            raise PatternError(f"invalid @match {pattern!r}: file:// patterns take no host")
        host_re = ""
    elif host == "*":
        host_re = r"[^/?#]+"
    elif host.startswith("*."):
        rest = host[2:]
        if not rest or "*" in rest:
            raise PatternError(f"invalid @match {pattern!r}: '*' may only lead the host")
        host_re = r"(?:[^/?#]*\.)?" + _esc(rest.lower()) + r"(?::\d+)?"
    else:
        if "*" in host:
            raise PatternError(f"invalid @match {pattern!r}: '*' may only lead the host")
        if not host:
            raise PatternError(f"invalid @match {pattern!r}: missing host")
        host_re = _esc(host.lower()) + r"(?::\d+)?"
    scheme_re = "https?" if scheme == "*" else _esc(scheme)
    path_re = ".*".join(_esc(part) for part in path.split("*"))
    return JsRegex(f"^{scheme_re}://{host_re}{path_re}$")


def include_to_regex(pattern: str) -> JsRegex:
    """Compile a Greasemonkey ``@include`` / ``@exclude``: a ``/regex/flags`` or a glob.

    Glob: ``*`` matches anything, ``.tld`` matches any public suffix, everything else is
    literal and the whole URL must match. A ``/regex/`` is passed through to the browser
    unchanged (it is JavaScript regex syntax) after a cheap structural check.
    """
    p = pattern.strip()
    if not p:
        raise PatternError("empty @include/@exclude")
    if len(p) >= 2 and p.startswith("/") and p.rfind("/") > 0:
        end = p.rfind("/")
        source, flags = p[1:end], p[end + 1:]
        if not source:
            raise PatternError(f"invalid regex include {pattern!r}: empty pattern")
        if not re.fullmatch(r"[gimsuy]*", flags):
            raise PatternError(f"invalid regex include {pattern!r}: bad flags {flags!r}")
        # `g`/`y` make RegExp.test() stateful (lastIndex) — a correct match would fail on
        # the next test. Drop them; they never change WHETHER a URL matches.
        return JsRegex(source, "".join(f for f in flags if f not in "gy"))
    if p == "*":
        return JsRegex(".*")
    parts = re.split(r"(\*|\.tld)", p)
    out: list[str] = []
    for part in parts:
        if part == "*":
            out.append(".*")
        elif part == ".tld":
            out.append(_TLD_RE)
        elif part:
            out.append(_esc(part))
    return JsRegex("^" + "".join(out) + "$", "i")
