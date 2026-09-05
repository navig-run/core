"""Every CORS surface must allow what the Deck actually sends.

The same allow-list is hand-maintained in FOUR separately-built places:

  * Python - ``navig/gateway/middleware.py``      (the daemon, direct/tunnel mode)
  * Python - ``navig/gateway/deck/auth.py``       (the deck auth preflight)
  * TS     - ``services/lighthouse/src/index.ts`` (the self-hosted edge)
  * TS     - ``services/api/src/index.ts``        (the broker)

One of them drifted, and the failure was invisible from every one of them.
Measured on the operator's live edge, 2026-09-04::

    Access-Control-Request-Headers: content-type,x-telegram-init-data,x-telegram-user
    -> Access-Control-Allow-Headers: Authorization,Content-Type,X-Telegram-Init-Data

``X-Telegram-User`` is missing, so the browser refuses to send the request at
all and ``fetch`` rejects with ``TypeError: Failed to fetch`` -- which the Deck
can only render as "cannot reach your edge", indistinguishable from the edge
being down. The edge was perfectly healthy.

It hid because ``authHeaders()`` sets that header ONLY when Telegram initData is
present: a plain browser worked, and the Telegram Mini App -- the entire point
of lighthouse mode -- could not load a single request. The same list also
omitted DELETE, which the Deck issues from ten call sites.

So this guard does NOT pin the four lists to each other: four copies can be
wrong together, and three of them were right only by luck. It derives the
requirement from the CLIENT -- what ``apps/deck/lib/api.ts`` actually emits --
and asserts each surface permits it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_DECK_API = _REPO / "apps" / "deck" / "lib" / "api.ts"
_SURFACES = {
    "daemon middleware": _REPO / "core" / "navig" / "gateway" / "middleware.py",
    "deck auth": _REPO / "core" / "navig" / "gateway" / "deck" / "auth.py",
    "lighthouse edge": _REPO / "services" / "lighthouse" / "src" / "index.ts",
    "broker api": _REPO / "services" / "api" / "src" / "index.ts",
}
# The edge is a transparent proxy to the daemon's deck API, so it must accept
# every method the Deck sends there. The broker is a different API surface with
# its own (GET/POST) contract, so methods are checked only where they apply.
_METHOD_SURFACES = ("daemon middleware", "lighthouse edge")


def _text(p: Path) -> str:
    if not p.exists():
        pytest.skip(f"{p} not present in this checkout")
    return p.read_text(encoding="utf-8")


def _headers_the_deck_sends() -> set[str]:
    """The custom headers `authHeaders()` puts on every Deck request."""
    src = _text(_DECK_API)
    body = re.search(r"export function authHeaders\(\)[^{]*\{(.*?)\n\}", src, re.S)
    assert body, "could not find authHeaders() in apps/deck/lib/api.ts"
    found = set(re.findall(r"headers\[['\"]([A-Za-z-]+)['\"]\]", body.group(1)))
    found |= set(re.findall(r"['\"]([A-Za-z-]+)['\"]\s*:", body.group(1)))
    # Only the ones that actually force a preflight / need declaring.
    return {h for h in found if h.lower() not in {"accept"}}


def _methods_the_deck_sends() -> set[str]:
    src = _text(_DECK_API)
    return {m.upper() for m in re.findall(r"method:\s*['\"]([A-Za-z]+)['\"]", src)}


def _lists(name: str, kind: str) -> list[set[str]]:
    """Every allow-list of `kind` ("headers"/"methods") declared by that surface.

    Parses the LIST ITSELF, never the file text. The first version of this guard
    lowercased the whole file and asked "is 'x-telegram-user' in it" -- which the
    explanatory COMMENT above the list satisfies, so it passed against a
    deliberately broken allow-list. A guard that cannot fail is worse than none;
    caught by the teeth test, not by review.

    Returns one set per occurrence, because the daemon declares its list twice
    and both have to be right.
    """
    src = _text(_SURFACES[name])
    ts_key = {"headers": "allowHeaders", "methods": "allowMethods"}[kind]
    py_key = {"headers": "Access-Control-Allow-Headers",
              "methods": "Access-Control-Allow-Methods"}[kind]
    out: list[set[str]] = []
    for body in re.findall(rf"{ts_key}:\s*\[(.*?)\]", src, re.S):
        out.append({v.strip().lower() for v in re.findall(r"['\"]([^'\"]+)['\"]", body)})
    for body in re.findall(rf'"{py_key}":\s*"([^"]*)"', src):
        out.append({v.strip().lower() for v in body.split(",") if v.strip()})
    return out


def test_the_deck_sends_the_headers_we_think_it_does() -> None:
    """Floor: if this regex ever reads nothing, every assertion below is vacuous."""
    sent = _headers_the_deck_sends()
    assert "X-Telegram-Init-Data" in sent, sent
    assert "X-Telegram-User" in sent, (
        "authHeaders() no longer sends X-Telegram-User -- if that is deliberate, "
        "update this guard; it is the header whose absence from the edge's "
        "allow-list broke the Mini App entirely"
    )
    assert len(sent) >= 3, sent


@pytest.mark.parametrize("surface", sorted(_SURFACES))
def test_every_surface_allows_every_header_the_deck_sends(surface: str) -> None:
    lists = _lists(surface, "headers")
    assert lists, f"parsed NO allow-headers list out of {_SURFACES[surface]}"
    for allowed in lists:
        assert "content-type" in allowed, (
            f"parsed a list with no Content-Type out of {_SURFACES[surface]} -- "
            f"the parser is broken, so every assertion here would be vacuous: {allowed}"
        )
    for header in sorted(_headers_the_deck_sends()):
      for allowed in lists:
        assert header.lower() in allowed, (
            f"{surface} ({_SURFACES[surface].relative_to(_REPO)}) does not allow "
            f"'{header}', which apps/deck sends on every request. A preflight "
            f"answered without it means the browser never sends the request, and "
            f"the Deck reports 'TypeError: Failed to fetch' -- which looks exactly "
            f"like the server being down."
        )


@pytest.mark.parametrize("surface", _METHOD_SURFACES)
def test_the_proxied_surfaces_allow_every_method_the_deck_uses(surface: str) -> None:
    lists = _lists(surface, "methods")
    assert lists, f"parsed NO allow-methods list out of {_SURFACES[surface]}"
    for allowed in lists:
        assert "get" in allowed, (
            f"parsed a list with no GET out of {_SURFACES[surface]} -- parser broken"
        )
    for method in sorted(_methods_the_deck_sends()):
      for allowed in lists:
        assert method.lower() in allowed, (
            f"{surface} ({_SURFACES[surface].relative_to(_REPO)}) does not allow "
            f"{method}, which apps/deck issues against the daemon's deck API."
        )


def test_the_method_scan_is_not_vacuous() -> None:
    """DELETE is the one that was missing; if the scan stops seeing it the
    parametrised test above would pass while checking nothing."""
    methods = _methods_the_deck_sends()
    assert "DELETE" in methods, methods
    assert "POST" in methods, methods
