from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_installer_harness_files_exist() -> None:
    root = _repo_root()
    installer_dir = root / "tests" / "installer"

    assert (installer_dir / "install.Tests.ps1").is_file()
    assert (installer_dir / "install.bats").is_file()


def test_install_ps1_has_no_run_guard() -> None:
    source = (_repo_root() / "install.ps1").read_text(encoding="utf-8")
    assert "NAVIG_INSTALL_PS1_NO_RUN" in source
    assert "if ($env:NAVIG_INSTALL_PS1_NO_RUN -ne \"1\") { Main }" in source


def test_windows_installer_supports_telegram_token_bootstrap() -> None:
    source = (_repo_root() / "scripts" / "install_navig_windows.ps1").read_text(encoding="utf-8")
    assert "NAVIG_TELEGRAM_BOT_TOKEN" in source
    assert "TELEGRAM_BOT_TOKEN" in source
    assert "SetEnvironmentVariable(\"TELEGRAM_BOT_TOKEN\"" in source


def test_linux_installer_supports_telegram_token_bootstrap() -> None:
    source = (_repo_root() / "scripts" / "install_navig_linux.sh").read_text(encoding="utf-8")
    assert "NAVIG_TELEGRAM_BOT_TOKEN" in source
    assert "TELEGRAM_BOT_TOKEN" in source
    assert "printf 'TELEGRAM_BOT_TOKEN=%s" in source
