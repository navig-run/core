"""NAVIG OS sign-ins travel with a move: decrypted by NAVIG OS on the old machine,
re-sealed by NAVIG OS on the new one, and never written to disk in between.

Regression: every move used to leave them behind ("sign in again: Claude") because
credentials.enc is sealed to the machine. The unit tests drive a fake runner; the last
test runs the real transfer CLI (bun) end to end when bun and apps/os are present.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.move import state
from navig.move.os_sign_ins import OsCredentialsError
from navig.vault.core import Vault

from .test_state import PASS, _machine, _new

DOC = {
    "version": 1,
    "credentials": [
        {
            "id": {"type": "llm_api_key", "connectionSlug": "claude"},
            "credential": {"value": "sk-test-not-real"},
        },
    ],
}


class FakeRunner:
    def __init__(self, *, export: bytes | None = None, fail: str = "") -> None:
        self.exported = export if export is not None else json.dumps(DOC).encode()
        self.fail = fail
        self.imported: list[tuple[Path, bytes]] = []

    def export(self, os_dir: Path) -> bytes:
        if self.fail == "export":
            raise OsCredentialsError("NAVIG OS sign-in export failed: locked")
        return self.exported

    def import_(self, os_dir: Path, payload: bytes) -> int:
        if self.fail == "import":
            raise OsCredentialsError("NAVIG OS sign-in import failed: disk full")
        self.imported.append((os_dir, payload))
        return len(json.loads(payload)["credentials"])


def _pack(tmp_path, runner):
    roots, vault = _machine(tmp_path / "old")
    out = tmp_path / "move.navigmove"
    manifest = state.pack(out, PASS, roots, vault, os_runner=runner)
    return out, manifest


def test_carried_sign_ins_are_resealed_on_the_new_machine(tmp_path) -> None:
    out, manifest = _pack(tmp_path, FakeRunner())
    assert manifest["os_sign_ins"] == {"carried": 1, "note": ""}
    assert manifest["os_connections_to_sign_in"] == [], "Claude travelled, so nothing to redo"

    new = _new(tmp_path)
    landing = FakeRunner()
    result = state.unpack(
        out, PASS, new, lambda: Vault(vault_dir=new.config / "vault"), os_runner=landing
    )
    assert result.os_sign_ins_restored == 1
    assert result.os_to_sign_in == [] and result.os_sign_ins_note == ""
    # Imported into THIS machine's NAVIG OS dir, the exact bytes that were exported.
    assert landing.imported == [(new.os, json.dumps(DOC).encode())]
    # The decrypted document never landed on disk.
    assert not any(p.name == "navig-os-sign-ins.json" for p in new.home.rglob("*"))


def test_no_navig_os_on_the_old_machine_says_so_and_lists_what_to_redo(tmp_path) -> None:
    _, manifest = _pack(tmp_path, None)
    assert manifest["os_sign_ins"]["carried"] == 0
    assert "not installed" in manifest["os_sign_ins"]["note"]
    assert manifest["os_connections_to_sign_in"] == ["Claude"]


def test_an_unreadable_store_degrades_to_sign_in_again(tmp_path) -> None:
    _, manifest = _pack(tmp_path, FakeRunner(fail="export"))
    assert "could not be read" in manifest["os_sign_ins"]["note"]
    assert manifest["os_connections_to_sign_in"] == ["Claude"]


def test_no_navig_os_on_the_new_machine_names_what_was_lost(tmp_path) -> None:
    out, _ = _pack(tmp_path, FakeRunner())
    new = _new(tmp_path)
    result = state.unpack(
        out, PASS, new, lambda: Vault(vault_dir=new.config / "vault"), os_runner=None
    )
    assert result.os_sign_ins_restored == 0
    assert "could not be restored" in result.os_sign_ins_note
    assert result.os_to_sign_in == ["Claude"]


def test_a_failed_import_names_what_was_lost(tmp_path) -> None:
    out, _ = _pack(tmp_path, FakeRunner())
    new = _new(tmp_path)
    result = state.unpack(
        out,
        PASS,
        new,
        lambda: Vault(vault_dir=new.config / "vault"),
        os_runner=FakeRunner(fail="import"),
    )
    assert "disk full" in result.os_sign_ins_note
    assert result.os_to_sign_in == ["Claude"]


def test_real_round_trip_through_navig_os(tmp_path) -> None:
    """The real thing: NAVIG OS seals a store, move carries it, NAVIG OS re-seals it.

    The runner is found the way `navig move` finds it (a prebuilt tree, or the apps/os
    checkout next to this package with bun on PATH); skipped where neither exists.
    """
    from navig.move import os_sign_ins

    runner = os_sign_ins.find_runner()
    if runner is None:
        pytest.skip("needs NAVIG OS (an apps/os checkout with bun, or a prebuilt server tree)")
    roots, vault = _machine(tmp_path / "old")
    (roots.os / "credentials.enc").unlink()  # replace the placeholder with a real store
    assert runner.import_(roots.os, json.dumps(DOC).encode()) == 1

    out = tmp_path / "move.navigmove"
    manifest = state.pack(out, PASS, roots, vault, os_runner=runner)
    assert manifest["os_sign_ins"]["carried"] == 1

    new = _new(tmp_path)
    result = state.unpack(
        out, PASS, new, lambda: Vault(vault_dir=new.config / "vault"), os_runner=runner
    )
    assert result.os_sign_ins_restored == 1
    sealed_new = (new.os / "credentials.enc").read_bytes()
    assert sealed_new[:8] == b"CRAFT01\0"
    assert b"sk-test-not-real" not in sealed_new, "the store on disk is encrypted"
    assert json.loads(runner.export(new.os)) == DOC
