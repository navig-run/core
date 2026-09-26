"""A green revalidate must not leave a connection routing to a model that is gone.

`inference.py` routes every request that names no model to
``connection.default_model``. A connection created when the probe id was live
keeps that id forever — `revalidate` only ever *filled* an empty default. So
once the provider retired it (measured 2026-09-19: the first id of xai, openai,
nvidia and openrouter), the connection revalidated HEALTHY on a neighbouring
model and then failed every real request on the dead one.

The driver now lists the id that answered FIRST and omits ids the provider
reported retired, so a default that is no longer listed was probed and is gone.
"""

from __future__ import annotations

import pytest

from navig.providers import connect as connect_mod
from navig.providers.connect import connect_provider, revalidate
from navig.providers.connection_types import AuthState, HealthState
from navig.providers.connections import ConnectionStore
from navig.providers.drivers.base import ModelInfo, ValidationResult
from navig.providers.drivers.fake import FakeDriver


class _Vault:
    def __init__(self):
        self.items = {}

    def put(self, label, payload, *, provider=None, **kw):
        self.items[label] = payload
        return "id-" + label

    def get_secret(self, label):
        if label not in self.items:
            raise KeyError(label)
        return self.items[label].decode()

    def delete(self, label):
        return self.items.pop(label, None) is not None


class _Driver(FakeDriver):
    def __init__(self, result):
        super().__init__()
        self.result, self.seen_models = result, []

    async def validate(self, *, secret_ref, endpoint=None, model=None):
        self.seen_models.append(model)
        return self.result


def _ok(*ids: str) -> ValidationResult:
    return ValidationResult(ok=True, health=HealthState.HEALTHY.value,
                            models=[ModelInfo(id=i) for i in ids])


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(tmp_path / "connections.db", vault=_Vault())


@pytest.fixture
async def connection(store):
    # A STORED row (openai-compat requires an endpoint, so it is never a shared
    # BYOK virtual connection), created when "old-id" was the live first model.
    drv = _Driver(_ok("old-id", "new-id"))
    conn = await connect_provider("openai-compat", api_key="sk-test", store=store, driver=drv,
                                  endpoint="https://llm.example/v1")
    assert conn.default_model == "old-id"
    assert not conn.connection_id.startswith("configured:")
    return conn


async def test_a_retired_default_is_replaced_by_the_id_that_answered(store, connection, monkeypatch):
    drv = _Driver(_ok("new-id"))  # the walk skipped "old-id" and "new-id" answered
    monkeypatch.setattr(connect_mod, "get_driver", lambda *_a, **_kw: drv)

    conn = await revalidate(connection.connection_id, store=store)

    assert drv.seen_models == ["old-id"], "the stored default must be the FIRST candidate probed"
    assert conn.auth_state == AuthState.CONNECTED
    assert conn.default_model == "new-id"
    assert store.get(connection.connection_id).default_model == "new-id", "must be persisted"


async def test_a_live_default_is_kept_even_when_not_listed_first(store, connection, monkeypatch):
    drv = _Driver(_ok("new-id", "old-id"))  # both live; caller-chosen default still valid
    monkeypatch.setattr(connect_mod, "get_driver", lambda *_a, **_kw: drv)

    conn = await revalidate(connection.connection_id, store=store)

    assert conn.default_model == "old-id", "an operator's live choice must not be overridden"


async def test_a_failed_revalidate_never_rewrites_the_default(store, connection, monkeypatch):
    bad = ValidationResult(ok=False, health=HealthState.INVALID.value,
                           error_message="Invalid API key.", models=[ModelInfo(id="new-id")])
    drv = _Driver(bad)
    monkeypatch.setattr(connect_mod, "get_driver", lambda *_a, **_kw: drv)

    conn = await revalidate(connection.connection_id, store=store)

    assert conn.auth_state == AuthState.NEEDS_REAUTH
    assert conn.default_model == "old-id", "a failing verdict is not evidence about the model"


async def test_a_shared_byok_connection_also_follows_the_answering_model(store, monkeypatch):
    """The virtual (env/vault key) path has no row: its default is re-derived
    from the registry manifest on every read, and revalidate re-synthesises the
    record from the validation result — so the answering id becomes the default
    without anything to update."""
    from navig.providers.registry import get_provider

    first = _Driver(_ok("old-id", "new-id"))
    conn = await connect_provider("openai-api", api_key="sk-test", store=store, driver=first)
    assert conn.connection_id.startswith("configured:")
    manifest_first = get_provider("openai").models[0]

    drv = _Driver(_ok("new-id"))
    monkeypatch.setattr(connect_mod, "get_driver", lambda *_a, **_kw: drv)
    conn = await revalidate(conn.connection_id, store=store)
    assert drv.seen_models == [manifest_first], "the virtual default is the manifest's first id"
    assert conn.default_model == "new-id"
