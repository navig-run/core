"""`navig --dry-run context clear` deleted .navig/config.yaml and reported success.

Same class as #755, found by auditing every command that receives `opts` and mutates
state. `--dry-run` is a GLOBAL flag delivered in `ctx.obj`, and both context verbs
ignored it:

* ``clear`` unlinked ``.navig/config.yaml`` (or rewrote it) and printed
  "✓ Project context cleared";
* ``set`` created ``.navig/`` and wrote the file.

These are project-local settings rather than an audit trail, so the blast radius is
smaller than the history one — but ``--dry-run`` is exactly the flag someone uses to find
out what a command would do to a repo they do not want to touch.
"""

from __future__ import annotations

import pytest

from navig.commands.context import clear_context, set_context


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project dir with a .navig/config.yaml, made the cwd."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "cfg"))
    proj = tmp_path / "proj"
    (proj / ".navig").mkdir(parents=True)
    (proj / ".navig" / "config.yaml").write_text(
        "active_host: web1\nactive_app: site\n", encoding="utf-8"
    )
    monkeypatch.chdir(proj)
    return proj


# ── clear ───────────────────────────────────────────────────────────────────────


def test_dry_run_clear_leaves_the_file_alone(project, capsys) -> None:
    """The bug: this used to unlink config.yaml."""
    clear_context({"dry_run": True})

    cfg = project / ".navig" / "config.yaml"
    assert cfg.exists()
    assert "active_host: web1" in cfg.read_text(encoding="utf-8")
    assert "DRY RUN" in capsys.readouterr().out


def test_dry_run_clear_does_not_claim_success(project, capsys) -> None:
    clear_context({"dry_run": True})

    assert "cleared" not in capsys.readouterr().out.lower().replace("would", "")


def test_clear_still_clears_for_real(project, capsys) -> None:
    clear_context({})

    assert not (project / ".navig" / "config.yaml").exists()
    assert "cleared" in capsys.readouterr().out.lower()


def test_clear_with_nothing_set_is_a_no_op(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    proj = tmp_path / "empty"
    proj.mkdir()
    monkeypatch.chdir(proj)

    clear_context({"dry_run": True})

    assert "No project context to clear" in capsys.readouterr().out


# ── set ─────────────────────────────────────────────────────────────────────────


def test_dry_run_set_writes_nothing(tmp_path, monkeypatch, capsys) -> None:
    """It used to create .navig/ and write config.yaml under --dry-run."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    proj = tmp_path / "fresh"
    proj.mkdir()
    monkeypatch.chdir(proj)

    from unittest.mock import MagicMock, patch

    cm = MagicMock()
    cm.host_exists.return_value = True
    with patch("navig.commands.context.get_config_manager", return_value=cm):
        set_context(host="web1", opts={"dry_run": True})

    assert not (proj / ".navig").exists(), "dry run must not create the directory"
    cm.set_local_config.assert_not_called()
    assert "DRY RUN" in capsys.readouterr().out


def test_set_still_writes_for_real(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    proj = tmp_path / "fresh2"
    proj.mkdir()
    monkeypatch.chdir(proj)

    from unittest.mock import MagicMock, patch

    cm = MagicMock()
    cm.host_exists.return_value = True
    cm.get_local_config.return_value = {}
    with patch("navig.commands.context.get_config_manager", return_value=cm):
        set_context(host="web1", opts={})

    cm.set_local_config.assert_called_once()
    assert (proj / ".navig").is_dir()


def test_set_validates_before_previewing(tmp_path, monkeypatch, capsys) -> None:
    """An unknown host is still an error under --dry-run, not a cheerful preview.

    The exit code is part of that claim: this used to print "not found" and exit 0,
    so `--dry-run` reported the failure to a human and success to the shell. It now
    raises, and the two original assertions below are unchanged — validation still
    precedes the preview, and no DRY RUN banner is printed for a host that does not
    exist.
    """
    import pytest
    import typer

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    proj = tmp_path / "fresh3"
    proj.mkdir()
    monkeypatch.chdir(proj)

    from unittest.mock import MagicMock, patch

    cm = MagicMock()
    cm.host_exists.return_value = False
    cm.list_hosts.return_value = ["other"]
    with patch("navig.commands.context.get_config_manager", return_value=cm):
        with pytest.raises(typer.Exit) as exc:
            set_context(host="nope", opts={"dry_run": True})
    assert exc.value.exit_code == 1, "a lookup that found nothing is exit 1"

    out = capsys.readouterr().out
    assert "not found" in out
    assert "DRY RUN" not in out


def test_the_shared_helper_is_the_one_used() -> None:
    """Both modules read the flag through one function, not two spellings."""
    from navig.cli.options import is_dry_run
    from navig.commands.history import _is_dry_run

    assert _is_dry_run is is_dry_run
    assert is_dry_run({"dry_run": True}) is True
    assert is_dry_run({}, True) is True
    assert is_dry_run(None) is False
