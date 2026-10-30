import io
import tarfile
from pathlib import Path

import pytest
import typer

from navig.commands import config_backup

pytestmark = pytest.mark.integration


def test_import_config_requires_file_option(monkeypatch):
    """Saying "--file is required" and exiting 0 is the silent-failure shape: the
    caller sees ✗ and `$?` says success. Exit 2 = usage error."""
    messages = []
    monkeypatch.setattr(config_backup.ch, "error", lambda msg: messages.append(msg))

    with pytest.raises(typer.Exit) as exc:
        config_backup.import_config({})

    assert exc.value.exit_code == 2
    assert any("Input file is required" in message for message in messages)


def test_delete_export_requires_file_option(monkeypatch):
    messages = []
    monkeypatch.setattr(config_backup.ch, "error", lambda msg: messages.append(msg))

    with pytest.raises(typer.Exit) as exc:
        config_backup.delete_export({})

    assert exc.value.exit_code == 2
    assert any("Input file is required" in message for message in messages)


def test_inspect_export_of_a_corrupt_archive_exits_1(tmp_path, monkeypatch):
    monkeypatch.setattr(config_backup.ch, "error", lambda *a, **k: None)
    bad = tmp_path / "broken.tar.gz"
    bad.write_bytes(b"not a gzip archive")

    with pytest.raises(typer.Exit) as exc:
        config_backup.inspect_export({"file": str(bad)})

    assert exc.value.exit_code == 1


@pytest.mark.parametrize("json_out", [False, True])
def test_delete_export_failure_exits_1(tmp_path, monkeypatch, json_out):
    monkeypatch.setattr(config_backup.ch, "error", lambda *a, **k: None)
    monkeypatch.setattr(config_backup.ch, "raw_print", lambda *a, **k: None)
    target = tmp_path / "export.tar.gz"
    target.write_bytes(b"x")

    def _locked(path):
        raise PermissionError("in use")

    monkeypatch.setattr(config_backup.os, "remove", _locked)

    with pytest.raises(typer.Exit) as exc:
        config_backup.delete_export({"file": str(target), "yes": True, "json": json_out})

    assert exc.value.exit_code == 1
    assert target.exists()


def test_safe_extract_tar_blocks_path_traversal(tmp_path):
    archive_path = tmp_path / "bad.tar.gz"

    with tarfile.open(archive_path, "w:gz") as tar:
        payload = b"malicious"
        info = tarfile.TarInfo(name="../outside.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    with tarfile.open(archive_path, "r:gz") as tar:
        with pytest.raises(ValueError, match="Unsafe archive entry"):
            config_backup._safe_extract_tar(tar, Path(tmp_path / "extract"))
