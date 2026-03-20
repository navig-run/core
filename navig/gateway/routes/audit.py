"""Audit log routes: GET /audit"""
from __future__ import annotations

from navig.gateway.routes.common import json_ok, require_bearer_auth


def register(app, gateway) -> None:
    app.router.add_get("/audit", _tail(gateway))


def _tail(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth

        limit   = min(int(r.query.get("limit", 50)), 500)
        action  = r.query.get("action")
        actor   = r.query.get("actor")
        status  = r.query.get("status")

        # Fetch more than requested so filters don't starve results
        raw = gw.audit_log.tail(n=max(limit * 4, 200))

        if action:
            raw = [e for e in raw if e.get("action", "").startswith(action)]
        if actor:
            raw = [e for e in raw if e.get("actor") == actor]
        if status:
            raw = [e for e in raw if e.get("status") == status]

        # Return most recent `limit` after filtering, newest-first
        events = list(reversed(raw[-limit:])) if len(raw) > limit else list(reversed(raw))
        return json_ok({"events": events, "count": len(events)})
    return h
