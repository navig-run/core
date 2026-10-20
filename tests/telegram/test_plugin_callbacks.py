"""Plugin-owned Telegram buttons: registration rules and the module switch."""

from __future__ import annotations

import asyncio

import pytest

from navig.telegram import plugin_callbacks as pc


class FakeChannel:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def _api_call(self, method, payload):
        self.calls.append((method, payload))
        return {"ok": True}


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(pc._REGISTRY)
    pc._REGISTRY.clear()
    yield
    pc._REGISTRY.clear()
    pc._REGISTRY.update(saved)


def _cq(data="zz:1"):
    return {"id": "cb1", "data": data, "message": {"chat": {"id": 42}, "message_id": 7}, "from": {"id": 42}}


def test_a_registered_prefix_gets_its_presses(monkeypatch):
    got = []

    async def handler(channel, cq, data):
        got.append(data)

    monkeypatch.setattr(pc, "_module_enabled", lambda m: True)
    pc.register_callback_prefix("zz:", handler, module="zmod")
    assert asyncio.run(pc.dispatch(FakeChannel(), _cq("zz:abc"), "zz:abc")) is True
    assert got == ["zz:abc"]
    assert asyncio.run(pc.dispatch(FakeChannel(), _cq("other:1"), "other:1")) is False


def test_a_switched_off_module_never_sees_the_press(monkeypatch):
    got = []

    async def handler(channel, cq, data):
        got.append(data)

    monkeypatch.setattr(pc, "_module_enabled", lambda m: False)
    pc.register_callback_prefix("zz:", handler, module="zmod")
    ch = FakeChannel()
    assert asyncio.run(pc.dispatch(ch, _cq(), "zz:1")) is True
    assert got == [] and ch.calls[0][0] == "answerCallbackQuery" and "switched off" in ch.calls[0][1]["text"]


def test_an_unreadable_module_state_counts_as_off(monkeypatch):
    import navig.modules.registry as reg

    def boom():
        raise RuntimeError("registry broken")

    monkeypatch.setattr(reg, "get_registry", boom)
    assert pc._module_enabled("zmod") is False


def test_registration_rules():
    async def h(*_a):
        return None

    with pytest.raises(ValueError):
        pc.register_callback_prefix("nocolon", h, module="m")
    with pytest.raises(ValueError):
        pc.register_callback_prefix("zz:", h, module="")
    pc.register_callback_prefix("zz:", h, module="m1")
    pc.register_callback_prefix("zz:", h, module="m1")  # idempotent re-register
    with pytest.raises(ValueError, match="already belongs"):
        pc.register_callback_prefix("zz:", h, module="m2")


def test_a_plugin_cannot_claim_a_core_prefix():
    from navig.gateway.channels import telegram_extensions as tx

    core_prefix = next(p for e in tx.EXTENSIONS for p in e.callback_prefixes if p.endswith(":") and len(p) <= 8)

    async def h(*_a):
        return None

    with pytest.raises(ValueError, match="shadows"):
        pc.register_callback_prefix(core_prefix, h, module="m")


def test_a_crashing_handler_answers_instead_of_hanging(monkeypatch):
    async def h(*_a):
        raise RuntimeError("plugin bug")

    monkeypatch.setattr(pc, "_module_enabled", lambda m: True)
    pc.register_callback_prefix("zz:", h, module="m")
    ch = FakeChannel()
    assert asyncio.run(pc.dispatch(ch, _cq(), "zz:1")) is True
    assert ch.calls and ch.calls[0][1]["show_alert"] is True
