"""A CLI that reads a ``json_ok`` gateway route must unwrap the envelope.

Gateway routes under ``navig/gateway/routes/`` answer with an **envelope** —
``json_ok(payload)`` -> ``{"ok": …, "data": <payload>, "error": …}`` (``routes/common``).
A CLI that reads a field straight off ``response.json()`` therefore always misses, because
the payload is one level down. The failure is silent and pernicious: a miss looks exactly
like *"the daemon has nothing to report"*, so a fully working subsystem reports empty.

That one mistake killed four separate command surfaces before it was caught:

* ``navig flux``  (#713) — "No peers discovered yet"; ``flux target`` could never set a target
* ``navig cron``  (#714) — no jobs, "Created job: None", and ``cron run`` reported **failure on success**
* ``navig gateway`` / ``navig browser`` (#719) — sessions, heartbeat, history, **pending approvals invisible**
* ``navig status`` (this change) — a running daemon with no uptime, 0 sessions, empty heartbeat/cron

This guard is the tripwire. It flags any ``navig/commands/*.py`` that makes HTTP calls and
references a ``json_ok`` route path but never routes a response through
:func:`navig.gateway_client.unwrap_envelope`.

**Two conventions live in this tree — that split is what breeds the bug:**

* ``navig/gateway/routes/*``      -> ``json_ok(payload)``  = ENVELOPED, payload under ``["data"]``
* ``navig/gateway/deck/routes/*`` -> ``web.json_response(payload)`` = RAW, read fields directly
  (verified: 0 of 52 deck route modules use ``json_ok``)

So a CLI hitting ``/api/deck/...`` is correct to read fields directly, and this guard only
scans the enveloped namespace. The path matcher requires a real path boundary precisely so
that ``/api/deck/cloud/status`` is never mistaken for a call to ``/status``.

**The exception that must never be swept:** error text lives at the **top level**.
``envelope_error`` produces ``{"ok": False, "data": None, "error": <message>, …}``, so an
error-extraction helper (e.g. ``browser._safe_get_error``) must read the RAW body —
unwrapping there would turn every message into "Unknown error".

Like the SSRF-wiring guard, this proves the *habit* (a module that talks to enveloped routes
knows about the envelope), not that every call site is individually correct. It is a tripwire
against a class of omission, not a correctness proof.
"""

from __future__ import annotations

import re
from pathlib import Path

# core/tests/quality/<this> -> parents[2] == core
_CORE = Path(__file__).resolve().parents[2] / "navig"
_ROUTES = _CORE / "gateway" / "routes"
_COMMANDS = _CORE / "commands"

# Command modules that reference an enveloped route path but legitimately never unwrap.
# Each entry needs a reason; test_allowlist_has_no_stale_entries keeps the list honest.
_ALLOWLIST: dict[str, str] = {}

_HTTP_CALL = re.compile(r"httpx\.(get|post)|requests\.(get|post|delete)|urllib\.request")


def _enveloped_route_paths() -> set[str]:
    """Route paths whose handler module uses ``json_ok``."""
    paths: set[str] = set()
    for path_file in _ROUTES.rglob("*.py"):
        src = path_file.read_text(encoding="utf-8", errors="replace")
        if "json_ok" not in src:
            continue
        for m in re.finditer(r'add_(?:get|post|put|delete)\(\s*"([^"]+)"', src):
            route = m.group(1)
            if len(route) > 3 and route != "/":
                paths.add(route)
    return paths


def _references_route(src: str, route: str) -> bool:
    """True if *src* calls *route* — requiring a real path boundary.

    The route must begin right after a base-URL interpolation (``f"{base}/status"``) or at
    the start of a literal (``"/status"``), and end at a quote or query. Without this,
    ``/api/deck/cloud/status`` would count as a call to ``/status`` — and the deck namespace
    is NOT enveloped, so that would be a false positive.
    """
    for m in re.finditer(re.escape(route), src):
        prev = src[m.start() - 1] if m.start() else '"'
        nxt = src[m.end()] if m.end() < len(src) else '"'
        if prev in '}"\'' and nxt in '"\'?{':
            return True
    return False


def _unwraps(src: str) -> bool:
    return "unwrap_envelope" in src or bool(re.search(r"\b_unwrap\(", src))


def _offenders() -> list[tuple[str, list[str]]]:
    enveloped = _enveloped_route_paths()
    out: list[tuple[str, list[str]]] = []
    for mod in sorted(_COMMANDS.glob("*.py")):
        src = mod.read_text(encoding="utf-8", errors="replace")
        if not _HTTP_CALL.search(src) or _unwraps(src):
            continue
        hits = sorted(r for r in enveloped if _references_route(src, r))
        if hits:
            out.append((mod.name, hits))
    return out


def test_cli_modules_calling_enveloped_routes_unwrap_them():
    offenders = [(n, h) for n, h in _offenders() if n not in _ALLOWLIST]
    assert not offenders, (
        "These command modules call a json_ok gateway route but never unwrap the envelope, "
        "so every field read off response.json() silently misses (it looks like the daemon "
        "has nothing to report). Route the body through "
        "navig.gateway_client.unwrap_envelope(), or add an allowlist entry with the reason:\n"
        + "\n".join(f"  {n} -> {h}" for n, h in offenders)
    )


def test_allowlist_has_no_stale_entries():
    flagged = {n for n, _ in _offenders()}
    stale = sorted(n for n in _ALLOWLIST if n not in flagged)
    assert not stale, f"allowlist entries no longer flagged — remove them: {stale}"


def test_the_collector_actually_finds_routes():
    """A silently-empty route map would make this guard pass vacuously forever."""
    paths = _enveloped_route_paths()
    assert len(paths) > 30, f"only {len(paths)} enveloped routes resolved — collector broken"
    assert "/status" in paths


# ── detector behaviour ───────────────────────────────────────────────────────


def test_detector_matches_an_interpolated_base_url():
    assert _references_route('requests.get(f"{_base}/status", timeout=2)', "/status")


def test_detector_matches_a_bare_literal_and_a_query():
    assert _references_route('_get("/mesh/peers")', "/mesh/peers")
    assert _references_route('_gw_request("GET", f"/heartbeat/history?limit={n}")',
                             "/heartbeat/history")


def test_detector_does_not_confuse_the_raw_deck_namespace():
    """THE false positive this boundary rule exists for: deck routes are NOT enveloped, so a
    CLI reading them directly is correct and must not be flagged."""
    src = 'requests.get(f"http://127.0.0.1:{port}/api/deck/cloud/status")'
    assert not _references_route(src, "/status")
    assert not _references_route('f"{b}/api/deck/status"', "/status")


def test_unwrap_detection_accepts_both_spellings():
    assert _unwraps("from navig.gateway_client import unwrap_envelope")
    assert _unwraps("data = _unwrap(response.json())")
    assert not _unwraps("data = response.json()")


def test_deck_routes_really_are_unenveloped():
    """Pins the premise for scanning only gateway/routes: if deck ever adopts json_ok, this
    guard must be widened to cover it."""
    deck = _CORE / "gateway" / "deck" / "routes"
    # `return` here reported PASS. This test pins the PREMISE that scanning only
    # gateway/routes is sufficient; if the deck routes are renamed or moved, the premise
    # becomes unverified and the guard would go green saying so was checked.
    assert deck.is_dir(), (
        f"{deck} is missing -- the premise this test pins can no longer be checked. "
        "Repoint it at the deck routes' new home rather than letting it pass."
    )
    using = [p.name for p in deck.rglob("*.py")
             if "json_ok" in p.read_text(encoding="utf-8", errors="replace")]
    assert not using, (
        "deck routes started using json_ok — widen this guard to the deck namespace: "
        f"{using}"
    )
