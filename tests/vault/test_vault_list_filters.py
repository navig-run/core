"""`vault list` finds items by what `vault classify` wrote — against a REAL vault.

Found 2026-10-16: classify named all 98 items ("Discord bot — Digital Ghost (SCHEMA)")
but nothing could search those names — the store searched only the storage path and
provider, and the CLI had no search at all. And a `--tag` that matched nothing printed
"Vault is empty. Use 'navig vault add'" over a vault holding 98 items.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from navig.commands.vault import vault_app
from navig.vault.core import Vault

runner = CliRunner()


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path)
    v.add("discord", "api_key", {"api_key": "MTE0-fake-bot-token-value"}, label="discord")
    v.add("groq", "api_key", {"api_key": "gsk_fake_value_000000"}, label="groq")
    meta = {
        "discord": {"label": "Discord bot — Digital Ghost (SCHEMA)", "category": "messaging",
                    "issuer": "Discord bot", "tags": ["schema"]},
        "groq": {"label": "Groq — api key", "category": "ai", "issuer": "Groq",
                 "tags": ["duplicate-of:groq/api_key"], "notes": "free tier"},
    }
    for item in v.list():
        assert v.update(item.id, metadata=meta[item.provider])
    try:
        with patch("navig.commands.vault._vault_mod.get_vault", return_value=v):
            yield v
    finally:
        v._store.close()


def _ids(out: str, v: Vault) -> set[str]:
    return {i.provider for i in v.list() if i.id[:8] in out}


@pytest.mark.parametrize("query, expected", [
    ("digital ghost", {"discord"}),   # display name, case-insensitive
    ("duplicate-of", {"groq"}),       # a tag
    ("FREE TIER", {"groq"}),          # notes
    ("discord bot", {"discord"}),     # issuer
])
def test_search_matches_what_classify_wrote(vault, query, expected):
    res = runner.invoke(vault_app, ["list", "--search", query], env={"COLUMNS": "200"})
    assert res.exit_code == 0, res.output
    assert _ids(res.output, vault) == expected, res.output


def test_a_filter_that_matches_nothing_does_not_call_the_vault_empty(vault):
    for args in (["--tag", "no-such-tag"], ["--category", "payments"], ["--search", "zzz"]):
        res = runner.invoke(vault_app, ["list", *args], env={"COLUMNS": "200"})
        assert "matching filters" in res.output, (args, res.output)
        assert "empty" not in res.output.lower(), (args, res.output)


def test_search_never_prints_a_secret_value(vault):
    res = runner.invoke(vault_app, ["list", "--search", "groq"], env={"COLUMNS": "200"})
    assert "gsk_fake_value" not in res.output


def test_direct_call_with_typer_sentinels_does_not_crash(vault):
    """A CLI function called from Python keeps OptionInfo defaults for options it was
    not given: truthy sentinels filtered every row out and `search.casefold()` raised
    AttributeError (5 tests in tests/config/test_config_vault_sync.py went red)."""
    import navig.commands.vault as m

    # every option except the three this call passes, exactly like that suite does
    res = runner.invoke(vault_app, ["list"], env={"COLUMNS": "200"})
    assert res.exit_code == 0, res.output

    m.vault_list(provider=None, profile=None, json_output=False)
