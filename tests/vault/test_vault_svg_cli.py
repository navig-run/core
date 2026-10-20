"""`navig vault export --svg | open | import | opener` through the real Typer app.

Each test runs against real ``Vault(tmp_path / …)`` instances patched in as the vault the
command sees — never the developer's own. The engine itself is covered by
plugins/navig-vault/tests/test_svg.py; this pins the wiring and the CLI contract.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from navig.commands.vault import vault_app

pytestmark = pytest.mark.integration

runner = CliRunner()
PASS = "a-chosen-passphrase-long-enough"


@pytest.fixture
def vaults(tmp_path, monkeypatch):
    from navig_vault import svg
    from navig_vault.core import Vault

    monkeypatch.setattr(svg, "ITERATIONS", 1000)
    monkeypatch.delenv("NAVIG_VAULT_SVG_PASSPHRASE", raising=False)
    a, b = Vault(tmp_path / "a"), Vault(tmp_path / "b")
    a.add("github", "token", {"token": "ghp_core_secret"})
    a.put("note", b"plain note")
    return a, b


def _run(vault, args, **env):
    with patch("navig.commands.vault_svg._vault_mod.get_vault", return_value=vault):
        return runner.invoke(vault_app, args, env=env or None)


def _json(res):
    return json.loads(res.output[res.output.index("{"):])


def test_the_svg_verbs_are_registered_on_navig_vault():
    names = {c.name for c in vault_app.registered_commands}
    assert {"export", "open", "import", "opener"} <= names


def test_export_without_svg_is_a_usage_error_with_a_hint(vaults):
    res = _run(vaults[0], ["export", "github"])
    assert res.exit_code == 2 and "--svg" in res.output


def test_export_all_refuses_without_yes_when_there_is_no_terminal(vaults, tmp_path):
    res = _run(vaults[0], ["export", "--svg", "--all", "-o", str(tmp_path / "x.svg")])
    assert res.exit_code == 2 and not (tmp_path / "x.svg").exists()


def test_export_open_import_round_trip(vaults, tmp_path):
    a, b = vaults
    out = tmp_path / "all.svg"
    res = _run(a, ["export", "--svg", "--all", "--yes", "-o", str(out), "--json"])
    assert res.exit_code == 0, res.output
    passphrase = _json(res)["files"][0]["passphrase"]

    res = _run(a, ["open", str(out), "--json"], NAVIG_VAULT_SVG_PASSPHRASE=passphrase)
    assert res.exit_code == 0, res.output
    assert {i["label"] for i in _json(res)["items"]} == {"github", "note"}
    assert "ghp_core_secret" not in res.output  # hidden without --reveal

    res = _run(b, ["import", str(out)], NAVIG_VAULT_SVG_PASSPHRASE=passphrase)
    assert res.exit_code == 0, res.output
    assert b.get_api_key("github") == "ghp_core_secret" and b.get_bytes("note") == b"plain note"

    res = _run(b, ["import", str(out), "--json"], NAVIG_VAULT_SVG_PASSPHRASE=passphrase)
    assert res.exit_code == 0 and sorted(_json(res)["skipped"]) == ["github", "note"]


def test_the_generated_passphrase_is_shown_whole_at_a_narrow_width(vaults, tmp_path):
    """Shown exactly once — a truncated passphrase (`esqzm-62n…`) would lock the file forever."""
    res = _run(vaults[0], ["export", "--svg", "github", "-o", str(tmp_path / "g.svg")], COLUMNS="60")
    assert res.exit_code == 0, res.output
    import re

    assert re.search(r"\b[2-9a-km-np-z]{5}(-[2-9a-km-np-z]{5}){4}\b", res.output), res.output


def test_a_wrong_passphrase_exits_1(vaults, tmp_path):
    a, _ = vaults
    out = tmp_path / "g.svg"
    assert _run(a, ["export", "--svg", "github", "--choose-passphrase", "-o", str(out)],
                NAVIG_VAULT_SVG_PASSPHRASE=PASS).exit_code == 0
    res = _run(a, ["open", str(out)], NAVIG_VAULT_SVG_PASSPHRASE=PASS + "x")
    assert res.exit_code == 1 and "wrong passphrase" in res.output


def test_an_unknown_item_exits_3_and_writes_nothing(vaults, tmp_path):
    res = _run(vaults[0], ["export", "--svg", "nope", "-o", str(tmp_path / "n.svg")])
    assert res.exit_code == 3 and not (tmp_path / "n.svg").exists()


def test_each_writes_one_file_per_item(vaults, tmp_path):
    res = _run(vaults[0], ["export", "--svg", "--all", "--yes", "--each", "-o", str(tmp_path / "each"), "--json"])
    assert res.exit_code == 0, res.output
    files = _json(res)["files"]
    assert len(files) == 2 and len({f["passphrase"] for f in files}) == 2


def test_opener_writes_the_page_and_never_overwrites(tmp_path):
    out = tmp_path / "open.html"
    assert runner.invoke(vault_app, ["opener", "-o", str(out)]).exit_code == 0
    assert "crypto.subtle" in out.read_text(encoding="utf-8")
    assert runner.invoke(vault_app, ["opener", "-o", str(out)]).exit_code == 1


def test_an_old_navig_vault_without_the_svg_engine_gets_an_update_hint(monkeypatch, tmp_path):
    """Core only requires navig-vault>=0.6.0; a release without svg.py must not traceback."""
    import builtins

    real_import = builtins.__import__

    def no_svg(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "navig_vault" and fromlist and "svg" in fromlist:
            raise ImportError("cannot import name 'svg'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", no_svg)
    res = runner.invoke(vault_app, ["opener", "-o", str(tmp_path / "o.html")])
    assert res.exit_code == 1 and "pip install -U navig-vault" in res.output


def test_a_revealed_secret_is_shown_whole_at_a_narrow_width(vaults, tmp_path):
    """The sibling of the passphrase test: `open --reveal` once printed `ghp_co…` at 44
    columns, a secret nobody can copy. It folds onto the next line instead."""
    a, _ = vaults
    out = tmp_path / "g.svg"
    res = _run(a, ["export", "--svg", "github", "-o", str(out), "--json"])
    passphrase = _json(res)["files"][0]["passphrase"]
    res = _run(a, ["open", str(out), "--reveal"], NAVIG_VAULT_SVG_PASSPHRASE=passphrase, COLUMNS="44")
    assert res.exit_code == 0, res.output
    assert "…" not in res.output, res.output
    assert "ghp_core_secret" in "".join(res.output.split()), res.output
