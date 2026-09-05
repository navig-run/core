"""`navig gateway session show|clear` pointed at a route that never existed.

Both built ``{base}/sessions/{key}`` — but the gateway only ever registered the COLLECTION
``GET /sessions``. There is no ``/sessions/{key}`` at all, for any method. So:

* ``gateway session show <key>``  -> 404 -> **"Session not found"** for every real session
* ``gateway session clear <key>`` -> 404 -> **"Failed to clear session: 404"**, always

The routes that actually serve a single session live under the memory namespace and had the
right semantics all along:

* ``GET    /memory/history/{session_key}``  -> ``json_ok({...messages...})``
* ``DELETE /memory/sessions/{session_key}`` -> ``json_ok({"deleted": true})`` or 404

Same family as the DOA gateway MCP routes (#706) and the envelope sweep (#713/#714/#719/#721):
a surface nothing exercised, so a permanently-404ing command looked like an empty result.

The load-bearing test here is ``test_every_session_url_the_cli_builds_has_a_route``: it walks
the registered routes and asserts each URL the command constructs resolves to one — which is
what nothing was checking.
"""

from __future__ import annotations

import inspect
import re
import unittest
from pathlib import Path

_CORE = Path(__file__).resolve().parents[2] / "navig"
_ROUTES_DIR = _CORE / "gateway" / "routes"


def _registered_routes() -> set[tuple[str, str]]:
    """(METHOD, path) for every route the gateway registers."""
    found: set[tuple[str, str]] = set()
    for path_file in _ROUTES_DIR.rglob("*.py"):
        src = path_file.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'add_(get|post|put|delete)\(\s*"([^"]+)"', src):
            found.add((m.group(1).upper(), m.group(2)))
    return found


def _matches(route: str, url_path: str) -> bool:
    """Does a concrete url path match a route template with {placeholders}?"""
    pattern = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", re.escape(route).replace(r"\{", "{").replace(r"\}", "}")) + "$"
    pattern = pattern.replace(r"\{", "{").replace(r"\}", "}")
    pattern = re.sub(r"\{[^}]+\}", r"[^/]+", pattern)
    return re.match(pattern, url_path) is not None


def _session_urls() -> list[tuple[str, str]]:
    """(method, concrete path) for each request `gateway_session` builds.

    The command used to hand-roll ``requests.get(f"{_base}/…")`` per action; it now
    goes through ``_gw_api("METHOD", "/path", …)`` so every failure exits non-zero.
    Only the extraction changed — the invariant these tests exist for (every URL the
    CLI builds resolves to a registered route) is unchanged, and
    ``test_the_extractor_sees_all_three_actions`` is what stops this from silently
    matching nothing and passing vacuously.
    """
    from navig.commands.gateway import gateway_session

    src = inspect.getsource(gateway_session)
    urls: list[tuple[str, str]] = []
    for m in re.finditer(
        r"_gw_api\(\s*\"(GET|POST|PUT|DELETE)\"\s*,\s*f?\"([^\"]+)\"", src
    ):
        # substitute the f-string placeholder with a concrete key
        path = re.sub(r"\{[a-z_]+\}", "SOMEKEY", m.group(2))
        urls.append((m.group(1).upper(), path))
    return urls


class TestSessionUrlsResolve(unittest.TestCase):
    def test_the_extractor_sees_all_three_actions(self):
        """A silently-empty extraction would make the guard below pass vacuously."""
        urls = _session_urls()
        self.assertEqual(len(urls), 3, f"expected list/show/clear, got {urls}")
        self.assertIn(("GET", "/sessions"), urls)

    def test_every_session_url_the_cli_builds_has_a_route(self):
        """THE REGRESSION: `/sessions/{key}` was never registered, so show and clear could
        only ever 404."""
        routes = _registered_routes()
        unmatched = [
            (method, path)
            for method, path in _session_urls()
            if not any(rm == method and _matches(rp, path) for rm, rp in routes)
        ]
        self.assertEqual(
            unmatched, [], f"CLI builds URLs with no registered route: {unmatched}"
        )

    def test_the_dead_path_is_really_absent(self):
        """Pins WHY this broke — so nobody 'restores' the old URL."""
        routes = _registered_routes()
        self.assertNotIn(("GET", "/sessions/{session_key}"), routes)
        self.assertNotIn(("DELETE", "/sessions/{session_key}"), routes)
        self.assertIn(("GET", "/sessions"), routes)

    def test_the_replacement_routes_exist_with_the_right_methods(self):
        routes = _registered_routes()
        self.assertIn(("GET", "/memory/history/{session_key}"), routes)
        self.assertIn(("DELETE", "/memory/sessions/{session_key}"), routes)

    def test_show_and_clear_target_the_memory_namespace(self):
        urls = _session_urls()
        gets = [p for m, p in urls if m == "GET"]
        deletes = [p for m, p in urls if m == "DELETE"]
        # Two GETs: the /sessions collection (list) and /memory/history/… (show).
        # dict() used to collapse them, hiding whichever came second.
        self.assertTrue(gets, f"no GET extracted from {urls}")
        self.assertTrue(all(p.startswith(("/sessions", "/memory/history")) for p in gets), gets)
        self.assertTrue(deletes and all(p.startswith("/memory/sessions") for p in deletes), deletes)

    def test_show_unwraps_the_envelope(self):
        """Both memory routes answer json_ok(...), so the payload is under ["data"].

        The unwrap moved INTO ``_gw_api`` (one place instead of per-command), so what
        this pins now is that the command does not read a raw body behind the helper's
        back — reading ``response.json()`` directly is exactly how the #713/#714
        envelope misses happened, and three of them were still live in this module
        (``queue add`` printed "Task added: None").
        """
        from navig.commands.gateway import _gw_api, gateway_session

        src = inspect.getsource(gateway_session)
        self.assertNotIn("response.json()", src)
        self.assertIn("_gw_api(", src)
        self.assertIn("_unwrap(response.json())", inspect.getsource(_gw_api))


class TestRouteMatcher(unittest.TestCase):
    def test_placeholder_matching(self):
        self.assertTrue(_matches("/memory/history/{session_key}", "/memory/history/abc"))
        self.assertTrue(_matches("/sessions", "/sessions"))

    def test_does_not_match_a_deeper_path(self):
        self.assertFalse(_matches("/sessions", "/sessions/abc"))
        self.assertFalse(_matches("/memory/history/{k}", "/memory/history/a/b"))
