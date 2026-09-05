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
