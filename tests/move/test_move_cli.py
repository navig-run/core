"""``navig move out`` then ``navig move in``, through the CLI, between two fake machines."""

from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

from navig.commands import move as move_cmd
from navig.move import marker, state
from navig.vault.core import Vault

from .test_state import _machine, _new


@pytest.fixture()
def machines(tmp_path, monkeypatch):
    old_roots, old_vault = _machine(tmp_path / "old")
    old_vault.put("openai", b"sk-old")
    new_roots = _new(tmp_path)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(old_roots.config))  # the marker lands on the OLD machine
    monkeypatch.setattr(move_cmd, "_daemon_running", lambda: False)
    monkeypatch.chdir(os.getcwd())
    return old_roots, old_vault, new_roots


def test_out_then_in_moves_the_navig_and_stands_the_old_one_down(machines, tmp_path, monkeypatch) -> None:
    old_roots, old_vault, new_roots = machines
    bundle = tmp_path / "moving.navigmove"
    monkeypatch.setattr(state, "default_roots", lambda: old_roots)
    monkeypatch.setattr(move_cmd, "_vault", lambda: old_vault)
    out = CliRunner().invoke(move_cmd.move_app, ["out", "-o", str(bundle), "--yes", "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["ok"] and data["vault_items"] == 1 and data["os_connections_to_sign_in"] == ["Claude"]
    assert marker.read_moved()["bundle"] == str(bundle)

    # A second `out` from the stood-down machine is refused.
    again = CliRunner().invoke(move_cmd.move_app, ["out", "--yes", "--json"])
    assert again.exit_code == 1

    monkeypatch.setattr(state, "default_roots", lambda: new_roots)
    monkeypatch.setattr(move_cmd, "_fresh_vault", lambda: Vault(vault_dir=new_roots.config / "vault"))
    landed = CliRunner().invoke(
        move_cmd.move_app, ["in", str(bundle), "--yes", "--json"], env={"NAVIG_MOVE_PASSPHRASE": data["passphrase"]}
    )
    assert landed.exit_code == 0, landed.output
    result = json.loads(landed.output)
    assert result["vault_items"] == 1 and result["os_connections_to_sign_in"] == ["Claude"]
    assert Vault(vault_dir=new_roots.config / "vault").get_bytes("openai") == b"sk-old"
    assert "deck-key-old" in (new_roots.config / "config.yaml").read_text(encoding="utf-8")


def test_a_failed_pack_marks_nothing_moved(machines, tmp_path, monkeypatch) -> None:
    old_roots, old_vault, _ = machines
    monkeypatch.setattr(state, "default_roots", lambda: old_roots)

    class Locked:
        def list(self):
            return old_vault.list()

        def get_bytes(self, label):
            raise RuntimeError("locked")

    monkeypatch.setattr(move_cmd, "_vault", lambda: Locked())
    out = CliRunner().invoke(move_cmd.move_app, ["out", "-o", str(tmp_path / "x.navigmove"), "--yes"], env={"COLUMNS": "200"})
    assert out.exit_code == 1
    assert "vault is protected by a passphrase" in out.output
    assert marker.read_moved() is None
    assert not (tmp_path / "x.navigmove").exists()


def test_in_refuses_while_navig_runs_here(machines, tmp_path, monkeypatch) -> None:
    *_, new_roots = machines
    from navig.move.bundle import encrypt_file

    plain = tmp_path / "plain"
    plain.write_bytes(b"x")
    bundle = tmp_path / "x.navigmove"
    encrypt_file(plain, bundle, "ABCDE", {"hostname": "old"})  # a genuine header: only the running check can refuse
    monkeypatch.setattr(move_cmd, "_daemon_running", lambda: True)
    monkeypatch.setattr(state, "default_roots", lambda: new_roots)
    result = CliRunner().invoke(move_cmd.move_app, ["in", str(bundle), "--yes"], env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert "navig service stop" in result.output
    assert not new_roots.config.exists()


def test_out_without_a_terminal_needs_yes(machines, tmp_path, monkeypatch) -> None:
    old_roots, old_vault, _ = machines
    monkeypatch.setattr(state, "default_roots", lambda: old_roots)
    monkeypatch.setattr(move_cmd, "_vault", lambda: old_vault)
    result = CliRunner().invoke(move_cmd.move_app, ["out", "-o", str(tmp_path / "y.navigmove")], env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert marker.read_moved() is None
