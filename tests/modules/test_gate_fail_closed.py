"""The capability gate must fail CLOSED, and must not re-enter the handler.

Two defects, both in the same twelve lines of ``navig/modules/gate.py``:

1. ``requires_module`` caught *every* exception from its own registry lookup and
   then ran the handler anyway, logging "allowing through". A gate that opens on
   error is not a gate: any exception in the module registry silently un-gated
   every capability route in the daemon. That is the failure mode the decorator
   exists to prevent, and it is the kind that ships green — a broken registry is
   rare, and when it happens nothing looks wrong from the outside.

2. ``return await handler(request)`` sat *inside* the same ``try``. A handler that
   raised was therefore caught, logged as "allowing through", and **invoked a
   second time** — every side effect of that handler ran twice. For a route that
   writes, posts or schedules, that is a duplicate, not a retry.

Failing closed does not cut off recovery: the decorator is applied only to
capability routes. Liveness, readiness and auth-bootstrap routes are never
decorated, so a registry fault degrades exactly the surface being guarded.
"""

from __future__ import annotations

import pytest

from navig.modules import gate


class _BrokenRegistry:
    def is_enabled(self, module_id: str) -> bool:
        raise RuntimeError("registry exploded")


class _Registry:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def is_enabled(self, module_id: str) -> bool:
        return self._enabled


class _Request:
    """Just enough for a handler the gate never reads from."""


def _handler(calls: list[str], *, raise_: bool = False):
    async def handle(request):
        calls.append("handler")
        if raise_:
            raise ValueError("handler blew up")
        return "ok"

    return handle


@pytest.mark.asyncio
async def test_an_enabled_module_reaches_the_handler(monkeypatch):
    monkeypatch.setattr("navig.modules.registry.get_registry", lambda: _Registry(True))
    calls: list[str] = []
    wrapped = gate.requires_module("media")(_handler(calls))
    assert await wrapped(_Request()) == "ok"
    assert calls == ["handler"]


@pytest.mark.asyncio
async def test_a_disabled_module_is_403_and_the_handler_never_runs(monkeypatch):
    monkeypatch.setattr("navig.modules.registry.get_registry", lambda: _Registry(False))
    calls: list[str] = []
    wrapped = gate.requires_module("media")(_handler(calls))
    resp = await wrapped(_Request())
    assert resp.status == 403
    assert resp.body  # a structured payload, not an empty refusal
    assert calls == []


@pytest.mark.asyncio
async def test_a_broken_registry_fails_closed_and_the_handler_never_runs(monkeypatch):
    """P0: an exception in the registry must not open the gate."""
    monkeypatch.setattr("navig.modules.registry.get_registry", lambda: _BrokenRegistry())
    calls: list[str] = []
    wrapped = gate.requires_module("media")(_handler(calls))

    resp = await wrapped(_Request())

    assert resp.status == 503, "a gate that cannot decide must refuse, not allow"
    # The decisive assertion: the handler did NOT run on the failure path.
    assert calls == []
    assert b"module_gate_unavailable" in resp.body


@pytest.mark.asyncio
async def test_a_failing_handler_is_not_invoked_twice(monkeypatch):
    """The handler must run exactly once, and its error must surface."""
    monkeypatch.setattr("navig.modules.registry.get_registry", lambda: _Registry(True))
    calls: list[str] = []
    wrapped = gate.requires_module("media")(_handler(calls, raise_=True))

    with pytest.raises(ValueError, match="handler blew up"):
        await wrapped(_Request())

    assert calls == ["handler"], "the handler must not re-enter after raising"


@pytest.mark.asyncio
async def test_disabled_and_unavailable_are_distinguishable(monkeypatch):
    """A surface must be able to tell a user choice from a registry fault.

    Collapsing them would make a fault look like a policy denial — and a surface
    that auto-enables on 403 would flip a module on behind the operator's back.
    """
    monkeypatch.setattr("navig.modules.registry.get_registry", lambda: _BrokenRegistry())
    broken = await gate.requires_module("media")(_handler([]))(_Request())

    monkeypatch.setattr("navig.modules.registry.get_registry", lambda: _Registry(False))
    denied = await gate.requires_module("media")(_handler([]))(_Request())

    assert (broken.status, denied.status) == (503, 403)
    assert b"module_gate_unavailable" in broken.body
    assert b"module_disabled" in denied.body
