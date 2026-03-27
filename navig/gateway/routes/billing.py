"""Billing event log routes: GET /billing"""

from __future__ import annotations

from navig.gateway.routes.common import json_ok, require_bearer_auth


def register(app, gateway) -> None:
    app.router.add_get("/billing", _tail(gateway))


def _tail(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth

        limit = min(int(r.query.get("limit", 50)), 500)
        actor = r.query.get("actor")
        event_type = r.query.get("event_type")

        raw = gw.billing_emitter.tail(n=max(limit * 4, 200))

        if actor:
            raw = [e for e in raw if e.get("actor") == actor]
        if event_type:
            raw = [e for e in raw if e.get("event_type", "").startswith(event_type)]

        events = list(reversed(raw[-limit:])) if len(raw) > limit else list(reversed(raw))
        return json_ok({"events": events, "count": len(events)})

    return h
