"""Regression: `navig script run` and two telegram status commands exited 0 on failure.

1. THE SCRIPT RUNNER RAN ON. `navig script run <name>` printed
   "Script failed with exit code N" and then exited **0** itself, so
   `navig script run deploy && ./next.sh` ran next.sh on top of a failed deploy —
   the runner threw away the one number it existed to report.

2. AN UNENFORCED OWNER GATE WAS INVISIBLE TO AUTOMATION.
   `navig telegram business status` printed
   "owner gate       : NOT SAFE — <reason>" and exited 0, so nothing scripted could
   detect that the business layer's owner gate was not being enforced. It now exits 1,
   raised at the END of the command so the operator still sees the full rights table.

3. A BROKEN LOGIN LOOKED FINE. `navig telegram status` printed
   "session present but NOT authorized" and exited 0. That is distinct from the
   `ch.info("not logged in")` case above it, which is a normal state and stays exit 0.

(`telegram sessions` and `template` were swept concurrently by another session —
covered in tests/commands/test_telegram_template_exit_honesty.py.)
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.integration


# ── navig script run ─────────────────────────────────────────────────────────


def test_a_failed_script_does_not_report_success(tmp_path, monkeypatch):
    """`navig script run deploy && ./next.sh` ran next.sh after a failed deploy."""
    import navig.commands.script as mod

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "boom.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    monkeypatch.setattr(mod, "_get_scripts_dir", lambda: scripts)

    result = CliRunner().invoke(mod.script_app, ["run", "boom"], obj={})

    assert result.exit_code == 1, "a script that exited 3 was reported as success"
    assert "exit code 3" in " ".join(result.output.split()), result.output


def test_a_successful_script_still_exits_zero(tmp_path, monkeypatch):
    """Anti-vacuity: if every run now raised, the assertion above would pass while
    the command was unusable."""
    import navig.commands.script as mod

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "ok.py").write_text("print('fine')\n", encoding="utf-8")
    monkeypatch.setattr(mod, "_get_scripts_dir", lambda: scripts)

    assert CliRunner().invoke(mod.script_app, ["run", "ok"], obj={}).exit_code == 0


def test_running_a_missing_script_is_a_usage_error(tmp_path, monkeypatch):
    import navig.commands.script as mod

    monkeypatch.setattr(mod, "_get_scripts_dir", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(mod.script_app, ["run", "nothing-here"], obj={})

    assert result.exit_code == 2
    assert "not found" in " ".join(result.output.split())


def test_creating_a_script_that_already_exists_is_a_usage_error(tmp_path, monkeypatch):
    import navig.commands.script as mod

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "dup.py").write_text("# already here\n", encoding="utf-8")
    monkeypatch.setattr(mod, "_get_scripts_dir", lambda: scripts)

    result = CliRunner().invoke(mod.script_app, ["new", "dup"], obj={})

    assert result.exit_code == 2, "overwriting silently, or reporting success"
    # and it must not have clobbered the existing file
    assert (scripts / "dup.py").read_text(encoding="utf-8") == "# already here\n"


# ── telegram status surfaces ─────────────────────────────────────────────────


def test_an_unenforced_owner_gate_exits_nonzero(monkeypatch):
    """The security signal this command exists to give. It printed NOT SAFE and
    exited 0, so no script could gate on it."""
    import typer

    import navig.commands._telegram_mtproto as mod

    app = typer.Typer()
    mod.register(app)

    class _Perm:
        @staticmethod
        def business_enabled():
            return True

        @staticmethod
        def arming_blocked_reason():
            return "require_auth is off"

        @staticmethod
        def all_policies():
            return {"shell": "owner", "web": "off"}

    class _Biz:
        @staticmethod
        def deletion_alert_enabled():
            return False

    # Patch the ATTRIBUTE on the package, not sys.modules: the command does
    # `from navig.telegram import permissions as perm`, which reads
    # navig.telegram.permissions once the submodule has been imported by anything
    # else. Patching sys.modules only worked when this test ran first — it passed
    # alone and failed in the full suite.
    import navig.telegram as tg_pkg

    monkeypatch.setattr(tg_pkg, "permissions", _Perm, raising=False)
    monkeypatch.setattr(tg_pkg, "business", _Biz, raising=False)

    result = CliRunner().invoke(app, ["business", "status"], obj={})

    assert result.exit_code == 1, (
        "an unenforced owner gate reported success:\n" + result.output
    )
    flat = " ".join(result.output.split())
    assert "NOT SAFE" in flat
    # raised at the END: the full rights table must still have been printed
    assert "shell" in flat and "web" in flat, (
        "the raise was placed too early — the operator lost the rights table"
    )


def test_an_enforced_owner_gate_exits_zero(monkeypatch):
    """Anti-vacuity: the safe configuration must stay exit 0."""
    import typer

    import navig.commands._telegram_mtproto as mod

    app = typer.Typer()
    mod.register(app)

    class _Perm:
        @staticmethod
        def business_enabled():
            return True

        @staticmethod
        def arming_blocked_reason():
            return None

        @staticmethod
        def all_policies():
            return {"shell": "owner"}

    class _Biz:
        @staticmethod
        def deletion_alert_enabled():
            return True

    # Patch the ATTRIBUTE on the package, not sys.modules: the command does
    # `from navig.telegram import permissions as perm`, which reads
    # navig.telegram.permissions once the submodule has been imported by anything
    # else. Patching sys.modules only worked when this test ran first — it passed
    # alone and failed in the full suite.
    import navig.telegram as tg_pkg

    monkeypatch.setattr(tg_pkg, "permissions", _Perm, raising=False)
    monkeypatch.setattr(tg_pkg, "business", _Biz, raising=False)

    result = CliRunner().invoke(app, ["business", "status"], obj={})

    assert result.exit_code == 0, result.output
    assert "enforced" in " ".join(result.output.split())
