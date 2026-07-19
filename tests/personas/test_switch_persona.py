"""Regression: switch_persona persists the persona and rolls back on failure.

The old best-effort in-process agent hot-swap (`_get_agent_instance` ->
`navig.gateway.server.get_agent`) imported a name that never existed, so it was
100% dead and has been removed. Persona application is channel_router's job on
the per-session agent's next construction; the persisted store is the source of
truth. These tests pin that persist/rollback contract.
"""

from __future__ import annotations

import pytest

import navig.personas.loader as loader_mod
import navig.personas.resolver as resolver_mod
import navig.personas.store as store_mod
from navig.personas import manager as manager_mod
from navig.personas.manager import PersonaSwitchError, switch_persona


class _Config:
    pass


def _wire(monkeypatch, *, load=None, resolve_ok=True):
    monkeypatch.setattr(
        resolver_mod, "resolve_persona",
        lambda name, cwd=None: (object() if resolve_ok else None),
    )
    monkeypatch.setattr(
        loader_mod, "load_persona",
        load or (lambda name, cwd=None: (_Config(), "soul")),
    )
    monkeypatch.setattr(store_mod, "get_active_persona", lambda user_id, chat_id: "old-persona")


async def test_switch_persists_new_persona(monkeypatch):
    saved = []
    _wire(monkeypatch)
    monkeypatch.setattr(
        store_mod, "set_active_persona",
        lambda user_id, chat_id, name: saved.append((user_id, chat_id, name)),
    )

    cfg = await switch_persona("hacker", 7, 99, deliver_assets=False)

    assert isinstance(cfg, _Config)
    assert saved == [(7, 99, "hacker")]  # persisted the NEW persona


async def test_unknown_persona_raises_before_persist(monkeypatch):
    saved = []
    _wire(monkeypatch, resolve_ok=False)
    monkeypatch.setattr(store_mod, "set_active_persona", lambda *a: saved.append(a))

    with pytest.raises(PersonaSwitchError):
        await switch_persona("ghost", 7, 99, deliver_assets=False)
    assert saved == []  # never persisted


async def test_load_failure_rolls_back_to_previous(monkeypatch):
    saved = []

    def boom_load(name, cwd=None):
        raise RuntimeError("bad persona file")

    _wire(monkeypatch, load=boom_load)
    monkeypatch.setattr(
        store_mod, "set_active_persona",
        lambda user_id, chat_id, name: saved.append(name),
    )

    with pytest.raises(PersonaSwitchError):
        await switch_persona("hacker", 7, 99, deliver_assets=False)
    # the failed switch rolled the store back to the previous persona
    assert saved == ["old-persona"]


def test_dead_agent_hotswap_is_gone():
    # the phantom in-process hot-swap helper is removed, not just neutered
    assert not hasattr(manager_mod, "_get_agent_instance")
