"""PATH installation must reach the file the user's LOGIN shell reads.

The defect these pin: the installer appended to `.bashrc`, `.zshrc`, `.profile` only.
macOS Terminal starts a LOGIN shell — zsh reads `.zprofile`, bash reads `.bash_profile` —
so a fresh macOS install could leave `navig` on PATH only inside a shell that was started
from an already-configured terminal. fish never reads `.profile` at all, and it also
cannot parse `export PATH=…`.
"""

from __future__ import annotations

import pytest

from navig.installer import contracts
from navig.installer.modules import shell_integration as si


def _ctx() -> contracts.InstallerContext:
    return contracts.InstallerContext(profile="standard")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_zsh_gets_the_login_file_first(home, monkeypatch):
    (home / ".zprofile").write_text("", encoding="utf-8")
    (home / ".zshrc").write_text("", encoding="utf-8")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    candidates = [p.name for p in si._shell_rc_candidates()]
    assert candidates[0] == ".zprofile"
    assert ".zshrc" in candidates


def test_bash_gets_bash_profile_on_a_login_shell(home, monkeypatch):
    (home / ".bash_profile").write_text("", encoding="utf-8")
    (home / ".bashrc").write_text("", encoding="utf-8")
    monkeypatch.setenv("SHELL", "/bin/bash")
    assert [p.name for p in si._shell_rc_candidates()][0] == ".bash_profile"


def test_fish_gets_config_fish_and_never_profile(home, monkeypatch):
    fish_dir = home / ".config" / "fish"
    fish_dir.mkdir(parents=True)
    (fish_dir / "config.fish").write_text("", encoding="utf-8")
    (home / ".profile").write_text("", encoding="utf-8")
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    candidates = si._shell_rc_candidates()
    assert [p.name for p in candidates] == ["config.fish"]


def test_an_unknown_shell_still_reaches_profile(home, monkeypatch):
    (home / ".profile").write_text("", encoding="utf-8")
    monkeypatch.setenv("SHELL", "/usr/bin/tcsh")
    assert [p.name for p in si._shell_rc_candidates()] == [".profile"]


def test_a_fish_snippet_uses_fish_add_path(home):
    fish_dir = home / ".config" / "fish"
    fish_dir.mkdir(parents=True)
    rc = fish_dir / "config.fish"
    rc.write_text("# existing\n", encoding="utf-8")

    action = contracts.Action(
        id="shell_integration.config.fish",
        description="test",
        module="shell_integration",
        data={"rc": str(rc), "bin_dir": "/opt/navig/bin", "syntax": "fish"},
        reversible=True,
    )
    result = si.apply(action, _ctx())

    assert result.state == contracts.ModuleState.APPLIED
    content = rc.read_text(encoding="utf-8")
    assert 'fish_add_path "/opt/navig/bin"' in content
    assert "export PATH" not in content


def test_a_posix_snippet_keeps_the_export_form(home):
    rc = home / ".profile"
    rc.write_text("", encoding="utf-8")

    action = contracts.Action(
        id="shell_integration.profile",
        description="test",
        module="shell_integration",
        data={"rc": str(rc), "bin_dir": "/opt/navig/bin", "syntax": "posix"},
        reversible=True,
    )
    si.apply(action, _ctx())

    assert 'export PATH="/opt/navig/bin:$PATH"' in rc.read_text(encoding="utf-8")
