"""The navig.run/proof "download, then verify" commands must work as printed.

The proof page tells a visitor to download ``receipt.json`` / ``operations.jsonl``
and run, in that folder::

    navig block verify-receipt receipt.json
    navig ledger verify --path operations.jsonl

Both resolved the relative path against the ACTIVE SPACE (``main.py`` chdir's there
before any command runs), not the folder the visitor stood in. So the receipt check
failed with "receipt not found", and the ledger check was worse: it printed
"No ledger … nothing recorded yet" and EXITED 0 — a verify command reporting success
on a file it never read.

These tests run the published commands against the published artifacts, from a
directory that is not the process cwd, exactly as a visitor would.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.block import block_app
from navig.commands.ledger import ledger_app

_PROOF = Path(__file__).resolve().parents[3] / "web" / "www" / "public" / "proof"
_runner = CliRunner()


@pytest.fixture
def download_folder(tmp_path: Path, monkeypatch) -> Path:
    """The visitor's download folder, while the process cwd sits in the active space."""
    if not (_PROOF / "receipt.json").is_file():
        pytest.skip("web/www/public/proof is not in this checkout")
    downloads = tmp_path / "Downloads"
    space = tmp_path / "active-space"
    downloads.mkdir()
    space.mkdir()
    shutil.copy2(_PROOF / "receipt.json", downloads / "receipt.json")
    shutil.copy2(_PROOF / "operations.jsonl", downloads / "operations.jsonl")
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(downloads))
    monkeypatch.chdir(space)
    return downloads


def test_verify_receipt_finds_the_downloaded_file(download_folder: Path) -> None:
    result = _runner.invoke(block_app, ["verify-receipt", "receipt.json"])
    assert result.exit_code == 0, result.output
    assert "VALID" in result.output


def test_verify_receipt_rejects_a_tampered_download(download_folder: Path) -> None:
    receipt = download_folder / "receipt.json"
    receipt.write_text(
        receipt.read_text(encoding="utf-8").replace("receipts over claims", "receipts over claimz"),
        encoding="utf-8",
    )
    result = _runner.invoke(block_app, ["verify-receipt", "receipt.json"])
    assert result.exit_code == 1, result.output


def test_ledger_verify_reads_the_downloaded_file(download_folder: Path) -> None:
    result = _runner.invoke(ledger_app, ["verify", "--path", "operations.jsonl"])
    assert result.exit_code == 0, result.output
    assert "5 operations, chain intact" in result.output


def test_ledger_verify_named_missing_file_fails(download_folder: Path) -> None:
    result = _runner.invoke(ledger_app, ["verify", "--path", "no-such.jsonl"])
    assert result.exit_code == 1, result.output
    # Rich wraps at the console width, and the message embeds the absolute path, so a
    # deep checkout (a .dev/worktrees/ worktree) splits the phrase across lines.
    assert "nothing was verified" in " ".join(result.output.split())


def test_ledger_verify_named_missing_file_fails_as_json(download_folder: Path) -> None:
    result = _runner.invoke(ledger_app, ["verify", "--path", "no-such.jsonl", "--json"])
    assert result.exit_code == 1, result.output
