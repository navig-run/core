"""A non-secret key field's placeholder is a hint, never an accepted default.

`navig connector connect supabase` prompted the project URL with
`default=field.placeholder`, so pressing Enter stored `https://xyz.supabase.co` in the
vault and connect() — which only checks the URL is non-empty — reported success.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import typer

from navig.commands import connector_cmd
from navig.connectors import secrets
from navig.connectors.secrets import KeyField

_URL = KeyField(
    name="url",
    label="Project URL",
    vault_label="supabase-url",
    secret=False,
    placeholder="https://xyz.supabase.co",
)


def _connector(*fields: KeyField):
    return SimpleNamespace(manifest=SimpleNamespace(key_fields=list(fields)))


@pytest.fixture
def stored(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []
    monkeypatch.setattr(secrets, "resolve", lambda field: "")
    monkeypatch.setattr(secrets, "store", lambda field, value: calls.append((field.name, value)))
    monkeypatch.setattr(
        connector_cmd, "ch", SimpleNamespace(error=lambda *a, **k: None, info=lambda *a, **k: None)
    )
    return calls


def _press_enter(prompts: list):
    def fake_prompt(text, default=None, **_kw):
        prompts.append(text)
        return default  # what click returns for an empty answer

    return fake_prompt


def test_enter_on_a_required_field_stores_nothing_and_exits_1(
    monkeypatch: pytest.MonkeyPatch, stored: list
) -> None:
    prompts: list = []
    monkeypatch.setattr(typer, "prompt", _press_enter(prompts))
    with pytest.raises(typer.Exit) as exc:
        connector_cmd._prompt_api_key_fields(_connector(_URL))
    assert exc.value.exit_code == 1
    assert stored == [], "the illustrative placeholder was stored as a real value"
    assert "https://xyz.supabase.co" in prompts[0], "the placeholder should still be shown"


def test_a_typed_value_is_stored(monkeypatch: pytest.MonkeyPatch, stored: list) -> None:
    monkeypatch.setattr(typer, "prompt", lambda *a, **k: "https://real.supabase.co")
    connector_cmd._prompt_api_key_fields(_connector(_URL))
    assert stored == [("url", "https://real.supabase.co")]
