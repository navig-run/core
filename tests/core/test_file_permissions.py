from __future__ import annotations

from pathlib import Path

from navig.core import file_permissions


def test_set_owner_only_file_permissions_unix_chmod_failure_is_non_fatal(monkeypatch, tmp_path: Path):
    target = tmp_path / "sample.txt"
    target.write_text("x", encoding="utf-8")

    monkeypatch.setattr(file_permissions.os, "name", "posix", raising=False)

    def _raise_oserror(*_args, **_kwargs):
        raise OSError("chmod failed")

    monkeypatch.setattr(file_permissions.os, "chmod", _raise_oserror)

    file_permissions.set_owner_only_file_permissions(target)


def test_set_owner_only_file_permissions_windows_subprocess_failure_is_non_fatal(monkeypatch, tmp_path: Path):
    target = tmp_path / "sample.txt"
    target.write_text("x", encoding="utf-8")

    monkeypatch.setattr(file_permissions.os, "name", "nt", raising=False)

    import getpass
    import subprocess

    monkeypatch.setattr(getpass, "getuser", lambda: "tester")

    def _raise_oserror(*_args, **_kwargs):
        raise OSError("icacls unavailable")

    monkeypatch.setattr(subprocess, "run", _raise_oserror)

    file_permissions.set_owner_only_file_permissions(target)
