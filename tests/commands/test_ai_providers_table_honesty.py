"""``navig ai providers`` — the status table says one thing per row.

Measured on the operator's own install: ``anthropic  ✓ subscription  not_found``.
``resolve_auth`` returns the *string* ``"not_found"`` (truthy) when it finds
nothing, so ``source or "connection"`` kept it and the row contradicted itself.
The credential on that branch IS the routable connection, so the row names it.

Also pins the surface this file's sibling change made reachable: GitHub Models
appears in the table at all (it has a ``BUILTIN_PROVIDERS`` entry now).
"""

from __future__ import annotations

import re

from typer.testing import CliRunner

from navig.commands import ai as ai_cmd


def _rows(monkeypatch, *, routable: set[str], resolved: dict[str, tuple[str, str]]) -> str:
    class _Auth:
        def resolve_auth(self, name):
            return resolved.get(name, (None, "not_found"))

    import navig.providers as providers_pkg

    monkeypatch.setattr(providers_pkg, "AuthProfileManager", lambda: _Auth())

    import navig.providers.connect as connect_mod
    import navig.providers.inference as inference_mod

    monkeypatch.setattr(
        connect_mod, "list_connections",
        lambda: [{"provider": p, "is_routable": True} for p in routable],
    )
    monkeypatch.setattr(inference_mod, "_provider_of", lambda c: c["provider"])

    result = CliRunner().invoke(ai_cmd.ai_app, ["providers"], env={"COLUMNS": "160"})
    assert result.exit_code == 0, result.output
    return result.output


def _row(output: str, provider: str) -> str:
    for line in output.splitlines():
        if re.match(rf"\s*{re.escape(provider)}\s", line):
            return re.sub(r"\s+", " ", line).strip()
    raise AssertionError(f"no row for {provider!r} in:\n{output}")


def test_subscription_row_names_the_connection_not_not_found(monkeypatch):
    out = _rows(monkeypatch, routable={"anthropic"}, resolved={})
    row = _row(out, "anthropic")
    assert "✓ subscription" in row
    assert "connection" in row
    assert "not_found" not in row


def test_key_store_source_is_still_reported_verbatim(monkeypatch):
    out = _rows(monkeypatch, routable=set(),
                resolved={"github_models": ("ghp_x", "config:github_models.token")})
    row = _row(out, "github_models")
    assert "✓ configured" in row and "config:github_models.token" in row


def test_github_models_is_a_row(monkeypatch):
    out = _rows(monkeypatch, routable=set(), resolved={})
    row = _row(out, "github_models")
    assert "15 models" in row


# ── `--test <provider>` walks past a retired first model id ──


class _Client:
    def __init__(self, gone: set[str]):
        self.gone, self.sent = gone, []

    async def complete(self, request):
        from navig.providers.clients import ProviderError

        self.sent.append(request.model)
        if request.model in self.gone:
            raise ProviderError(message=f"The model {request.model} was deprecated",
                                status_code=404, provider="xai")
        return object()

    async def close(self):
        pass


def _test_run(monkeypatch, client, provider="xai"):
    class _Auth:
        def resolve_auth(self, name):
            return ("key", "vault:xai")

    import navig.providers as providers_pkg

    monkeypatch.setattr(providers_pkg, "AuthProfileManager", lambda: _Auth())
    monkeypatch.setattr(providers_pkg, "create_client", lambda *a, **k: client)
    import navig.providers.inference as inference_mod

    monkeypatch.setattr(inference_mod, "resolve_provider_credential", lambda pid, *a, **k: ("key", None))
    result = CliRunner().invoke(ai_cmd.ai_app, ["providers", "--test", provider], env={"COLUMNS": "160"})
    assert result.exit_code == 0, result.output
    return re.sub(r"\s+", " ", result.output)


def test_test_flag_reports_working_when_only_the_first_id_is_retired(monkeypatch):
    from navig.providers.probe_models import probe_candidates

    first, second = probe_candidates("xai")[:2]
    client = _Client(gone={first})
    out = _test_run(monkeypatch, client)
    assert "✓ xai is working!" in out and f"answered as {second}" in out
    assert first in out and "retired" in out, "the operator is told which id rotted"
    assert client.sent == [first, second]


def test_test_flag_with_every_id_retired_does_not_blame_the_key(monkeypatch):
    from navig.providers.probe_models import probe_candidates

    client = _Client(gone=set(probe_candidates("xai")))
    out = _test_run(monkeypatch, client)
    assert "every known model id is retired" in out
    assert "credential was never judged" in out
