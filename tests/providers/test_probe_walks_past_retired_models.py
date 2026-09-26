"""A credential probe asks "does this key work?", not "is our first model id alive?".

Measured 2026-09-19 with the operator's own keys: ``navig ai providers --test``
and ``navig connect``'s validation both sent ``BUILTIN_PROVIDERS[p].models[0]``,
and that id was RETIRED for every provider a key existed for —
xai 404 "deprecated on 2025-09-15", openai 404 "does not exist", nvidia 410
"end of life 2026-08-26", openrouter 404 "No endpoints found". Four working
keys, four red verdicts, and a new connection recorded the dead id as its
``default_model``.

The probe now walks the known ids, skips the ones the provider says are gone,
and lists the id that answered first.
"""

from __future__ import annotations

import pytest

from navig.providers.clients import ProviderError
from navig.providers.drivers.native import NativeDriver
from navig.providers.probe_models import (
    MAX_PROBE_CANDIDATES,
    model_is_gone,
    probe_candidates,
    probe_first_answering,
)
from navig.providers.types import BUILTIN_PROVIDERS, ModelDefinition, ProviderConfig


def _err(status: int, message: str) -> ProviderError:
    return ProviderError(message=message, status_code=status, provider="p")


# ── classification: the four real shapes, plus the ones that must NOT skip ──


@pytest.mark.parametrize(
    "status,message",
    [
        (404, '{"code":"not-found","error":"The model grok-2-1212 was deprecated on 2025-09-15 '
              'and is no longer accessible via the API. Please use grok-3 instead."}'),
        (404, "The model `gpt-4-turbo-preview` does not exist or you do not have access to it."),
        (410, "The model 'meta/llama-3.3-70b-instruct' has reached its end of life on "
              "2026-08-26T09:00:00Z and is no longer available."),
        (404, "No endpoints found for anthropic/claude-3.5-sonnet."),
    ],
)
def test_the_four_measured_retirements_are_gone(status, message):
    assert model_is_gone(_err(status, message))


@pytest.mark.parametrize(
    "status,message",
    [
        (401, "Incorrect API key provided"),
        (403, "API key lacks permission for this model"),
        (402, "billing hard limit reached for this model"),
        (429, "Rate limit reached for model gpt-4.1"),
        (500, "internal server error"),
        (404, "Not Found"),  # a bare 404 is the ENDPOINT, not the model
    ],
)
def test_credential_and_service_errors_are_not_gone(status, message):
    assert not model_is_gone(_err(status, message))


def test_a_non_provider_exception_is_not_gone():
    assert not model_is_gone(RuntimeError("model socket closed"))


# ── candidates: manifest first, then the table, de-duplicated, capped ──


def test_candidates_put_the_audited_manifest_before_the_table():
    from navig.providers.registry import get_provider

    c = probe_candidates("xai")
    assert c[0] == get_provider("xai").models[0]
    assert "grok-2-1212" not in c[:1]
    assert len(c) <= MAX_PROBE_CANDIDATES
    assert len(c) == len(set(c))


def test_candidates_honour_an_explicit_first_and_unknown_provider():
    assert probe_candidates("xai", first="grok-3")[0] == "grok-3"
    assert probe_candidates("no-such-provider") == []
    assert probe_candidates(None, first=" custom-model ") == ["custom-model"]


@pytest.mark.parametrize("provider_id", sorted(BUILTIN_PROVIDERS))
def test_every_builtin_provider_with_models_has_a_probe_candidate(provider_id):
    cfg = BUILTIN_PROVIDERS[provider_id]
    if cfg.models:
        assert probe_candidates(provider_id), provider_id


# ── the walk ──


async def test_walk_skips_gone_ids_and_reports_them():
    calls: list[str] = []

    async def attempt(model: str) -> None:
        calls.append(model)
        if model in ("dead-1", "dead-2"):
            raise _err(404, f"The model {model} does not exist")

    answered, error, gone = await probe_first_answering(["dead-1", "dead-2", "live"], attempt)
    assert (answered, error, gone) == ("live", None, ["dead-1", "dead-2"])
    assert calls == ["dead-1", "dead-2", "live"]


async def test_walk_stops_at_the_first_credential_error():
    calls: list[str] = []

    async def attempt(model: str) -> None:
        calls.append(model)
        raise _err(401, "Incorrect API key provided")

    answered, error, gone = await probe_first_answering(["a", "b", "c"], attempt)
    assert answered is None and error is not None and error.status_code == 401
    assert gone == []
    assert calls == ["a"], "a bad key must not be re-sent for every model id"


async def test_walk_with_everything_gone_says_so():
    async def attempt(model: str) -> None:
        raise _err(410, f"model {model} end of life")

    answered, error, gone = await probe_first_answering(["a", "b"], attempt)
    assert answered is None and error.status_code == 410 and gone == ["a", "b"]


# ── the driver: the answering model is recorded FIRST ──


class _Client:
    def __init__(self, gone: set[str], auth_ok: bool = True):
        self.gone, self.auth_ok, self.sent = gone, auth_ok, []

    async def complete(self, request):
        self.sent.append(request.model)
        if not self.auth_ok:
            raise _err(401, "Incorrect API key provided")
        if request.model in self.gone:
            raise _err(404, f"The model `{request.model}` does not exist")
        return object()

    async def close(self):
        pass


def _driver(monkeypatch, client, provider_id="xai"):
    import navig.providers.drivers.native as native_mod

    monkeypatch.setattr(native_mod, "get_builtin_provider", lambda pid: BUILTIN_PROVIDERS.get(pid), raising=False)
    import navig.providers.clients as clients_mod

    monkeypatch.setattr(clients_mod, "create_client", lambda *a, **k: client)
    return NativeDriver(secret_resolver=lambda ref: "k", provider_id=provider_id)


async def test_connect_validation_survives_a_retired_first_id(monkeypatch):
    first = probe_candidates("xai")[0]
    client = _Client(gone={first})
    drv = _driver(monkeypatch, client)
    result = await drv.validate(secret_ref="vault:xai")
    assert result.ok is True
    assert result.models[0].id == client.sent[-1] == probe_candidates("xai")[1]
    assert first not in [m.id for m in result.models], "a retired id must not be re-listed"
    assert client.sent == probe_candidates("xai")[:2]


async def test_connect_validation_lists_the_table_ids_after_the_manifest(monkeypatch):
    client = _Client(gone=set())
    drv = _driver(monkeypatch, client)
    result = await drv.validate(secret_ref="vault:xai")
    ids = [m.id for m in result.models]
    assert ids[0] == probe_candidates("xai")[0]
    for m in BUILTIN_PROVIDERS["xai"].models:
        assert m.id in ids


async def test_connect_validation_with_a_bad_key_is_invalid_after_one_call(monkeypatch):
    client = _Client(gone=set(), auth_ok=False)
    drv = _driver(monkeypatch, client)
    result = await drv.validate(secret_ref="vault:xai")
    assert result.ok is False and result.health == "invalid"
    assert len(client.sent) == 1


async def test_connect_validation_with_every_id_retired_does_not_blame_the_key(monkeypatch):
    client = _Client(gone=set(probe_candidates("xai")))
    drv = _driver(monkeypatch, client)
    result = await drv.validate(secret_ref="vault:xai")
    assert result.ok is False
    assert result.health == "degraded"
    assert "retired" in result.error_message and "registry" in result.error_message


async def test_custom_endpoint_probes_the_caller_model_only(monkeypatch):
    client = _Client(gone=set())
    import navig.providers.clients as clients_mod

    monkeypatch.setattr(clients_mod, "create_client", lambda *a, **k: client)
    drv = NativeDriver(secret_resolver=lambda ref: "k", provider_id=None)
    result = await drv.validate(secret_ref="vault:x", endpoint="https://llm.example/v1", model="my-model")
    assert result.ok is True and client.sent == ["my-model"]


def test_probe_walk_is_the_only_completion_site_in_the_driver():
    """Teeth against a reintroduced single-model probe: every 1-token completion
    in the driver goes through the walk."""
    import ast
    import inspect

    import navig.providers.drivers.native as native_mod

    source = inspect.getsource(native_mod)
    tree = ast.parse(source)
    methods = [
        fn
        for cls in tree.body if isinstance(cls, ast.ClassDef)
        for fn in cls.body if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    assert len(methods) >= 5, "scan floor: the driver class was not found"
    sites = [
        fn.name for fn in methods
        if "CompletionRequest(" in (ast.get_source_segment(source, fn) or "") and fn.name != "_probe_walk"
    ]
    assert sites == [], f"completions outside _probe_walk: {sites}"


def test_table_provider_config_still_reaches_the_walk_when_registry_is_empty():
    """A provider with a ProviderConfig but no manifest still has candidates."""
    cfg = ProviderConfig(name="zzz", base_url="https://z/v1", models=[ModelDefinition(id="m1", name="M1")])
    BUILTIN_PROVIDERS["zzz"] = cfg
    try:
        assert probe_candidates("zzz") == ["m1"]
    finally:
        del BUILTIN_PROVIDERS["zzz"]


def test_candidates_skip_ids_already_known_retired_but_keep_an_explicit_first():
    """`liveness.RETIRED_MODELS` is what we have already paid a call to learn;
    re-discovering it per probe is the cost this helper exists to avoid. An
    explicit *first* is the caller's stored default and stays, so the walk can
    report it gone and the connection can move off it."""
    from navig.llm.liveness import is_retired

    for pid in ("xai", "nvidia", "openrouter", "openai"):
        cands = probe_candidates(pid)
        assert cands, f"{pid}: no candidates at all — the filter must not empty a keyed provider"
        for m in cands:
            assert not is_retired(m, pid), (pid, m)
    assert probe_candidates("xai", first="grok-2-1212")[0] == "grok-2-1212"
