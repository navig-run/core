"""Every space-control handler is actually WIRED to a URL.

A handler can be perfectly correct, fully unit-tested, and still be dead code:
`register_deck_routes` is a separate file from the handlers, so writing the
handler and forgetting the one-line `app.router.add_post(...)` produces a 404
with no error anywhere. That exact failure has shipped here before — the generic
webhook receiver was written, tested and never reachable.

So this resolves the real aiohttp router: the route must exist, accept POST, and
dispatch to the handler we think it does. Unit tests calling the handler
directly cannot catch a missing registration; only routing can.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp")

from aiohttp import web

from navig.gateway.deck import register_deck_routes
from navig.gateway.deck.routes import catalog as cat


def _routed_app() -> web.Application:
    app = web.Application()
    register_deck_routes(app, require_auth=False)
    return app


async def _resolve(app: web.Application, method: str, path: str):
    """Ask the real router what (if anything) serves `method path`."""
    for resource in app.router.resources():
        info = resource.get_info()
        # Dynamic resources expose a compiled pattern; static ones a plain path.
        pattern = info.get("pattern")
        formatter = info.get("formatter") or info.get("path")
        if pattern is not None:
            match = pattern.fullmatch(path)
            if not match:
                continue
        elif formatter != path:
            continue
        for route in resource:
            if route.method in (method, "*"):
                return route
    return None


@pytest.mark.parametrize(
    ("path", "handler"),
    [
        ("/api/deck/spaces/demo/apps", cat.handle_deck_space_apps),
        ("/api/deck/spaces/demo/app-sections", cat.handle_deck_space_app_sections),
        ("/api/deck/spaces/demo/books", cat.handle_deck_space_books),
        ("/api/deck/spaces/demo/enable", cat.handle_deck_space_enable),
        ("/api/deck/spaces/demo/activate", cat.handle_deck_space_activate),
    ],
)
async def test_space_control_routes_are_registered(path, handler):
    route = await _resolve(_routed_app(), "POST", path)
    assert route is not None, f"POST {path} resolves to NOTHING — handler is unreachable"
    assert route.handler is handler, f"POST {path} dispatches to {route.handler!r}, not {handler!r}"


async def test_app_sections_is_not_swallowed_by_the_dynamic_id_segment():
    """`{id}` is greedy enough to eat a sibling path if one were mis-declared —
    pin that `app-sections` is its own route, not a space literally named that."""
    app = _routed_app()
    sections = await _resolve(app, "POST", "/api/deck/spaces/demo/app-sections")
    apps = await _resolve(app, "POST", "/api/deck/spaces/demo/apps")
    assert sections is not None and apps is not None
    assert sections.handler is not apps.handler


async def test_resolver_returns_none_for_an_unregistered_path():
    """Negative control. Without this, a `_resolve` that matched everything
    would make every assertion above pass while proving nothing."""
    app = _routed_app()
    assert await _resolve(app, "POST", "/api/deck/spaces/demo/not-a-real-route") is None
    # Right path, wrong verb — the route exists but not for GET.
    assert await _resolve(app, "GET", "/api/deck/spaces/demo/app-sections") is None


async def test_spaces_scan_serves_the_card_payload():
    """The read side of the same feature — the sidebar hydrates from this."""
    route = await _resolve(_routed_app(), "GET", "/api/deck/spaces/scan")
    assert route is not None, "GET /api/deck/spaces/scan resolves to NOTHING"
    assert route.handler is cat.handle_deck_spaces_scan
