"""Regression: `navig run` must not report success when the command never ran.

`run_remote_command` resolves its payload through `_resolve_command` / `_encode_b64_command`,
helpers that print the specific reason and return None — a correct helper contract.
The caller then did:

    if final_command is None:
        return          # ← exit 0

so `navig run --file missing.sh` printed "File not found" and exited 0 with nothing
executed, and `... && next-step` ran anyway. Same silent-failure class #345/#358
addressed, displaced one frame up — which is why the AST guard in
tests/quality/test_command_exit_honesty.py cannot see it (it matches error-then-return
in a single frame; this needs cross-function reasoning). These tests are the cover.

The helpers themselves are deliberately left returning None: they are the ones that
know WHY it failed, and the caller is the right place to turn that into an exit code.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import typer

import navig.commands.remote as remote_mod


@pytest.fixture
def stub_host(monkeypatch):
    """Get past host resolution — these tests are about payload resolution only."""
    import navig.cli.recovery as rec
    import navig.config as cfg_mod

    monkeypatch.setattr(rec, "require_active_host", lambda *a, **k: "prod")
    monkeypatch.setattr(
        cfg_mod,
        "get_config_manager",
        lambda: SimpleNamespace(
            load_host_config=lambda _h: {"host": "prod"},
            is_local_host=lambda _h: False,
        ),
    )
    monkeypatch.setattr(remote_mod, "_check_powershell_quoting_issues", lambda *a, **k: None)
    # The destructive-op confirm reads its own config_manager; stub the gate itself
    # rather than reconstructing that dependency. A resolve failure never reaches it.
    monkeypatch.setattr(remote_mod.ch, "confirm_operation", lambda *a, **k: True)


def _never_executes(monkeypatch):
    """Fail loudly if anything reaches the remote — the point is that it must not."""
    import navig.remote as r

    def _boom(*_a, **_k):
        raise AssertionError("a command was sent to the host despite a resolve failure")

    monkeypatch.setattr(r, "RemoteOperations", lambda *_a, **_k: SimpleNamespace(
        execute_command=_boom, execute_command_interactive=_boom,
    ))


def test_missing_command_file_exits_nonzero(monkeypatch, stub_host, tmp_path):
    _never_executes(monkeypatch)
    with pytest.raises(typer.Exit) as exc:
        remote_mod.run_remote_command(None, {}, file=tmp_path / "nope.sh")
    assert exc.value.exit_code != 0


def test_empty_command_file_exits_nonzero(monkeypatch, stub_host, tmp_path):
    """An empty file is not an empty command — it is a mistake worth an exit code."""
    _never_executes(monkeypatch)
    empty = tmp_path / "empty.sh"
    empty.write_text("   \n", encoding="utf-8")
    with pytest.raises(typer.Exit) as exc:
        remote_mod.run_remote_command(None, {}, file=empty)
    assert exc.value.exit_code != 0


def test_a_directory_is_not_a_command_file(monkeypatch, stub_host, tmp_path):
    _never_executes(monkeypatch)
    with pytest.raises(typer.Exit) as exc:
        remote_mod.run_remote_command(None, {}, file=tmp_path)
    assert exc.value.exit_code != 0


def test_b64_encode_failure_exits_nonzero(monkeypatch, stub_host):
    """The second swallowed path: encoding failed, so the payload is wrong — sending
    it would be worse than stopping, and reporting success is worse than either."""
    _never_executes(monkeypatch)
    monkeypatch.setattr(remote_mod, "_try_decode_b64", lambda _c: None)
    monkeypatch.setattr(remote_mod, "_encode_b64_command", lambda _c: None)
    with pytest.raises(typer.Exit) as exc:
        remote_mod.run_remote_command("echo hi", {"b64": True})
    assert exc.value.exit_code != 0


def test_a_resolvable_command_still_reaches_the_host(monkeypatch, stub_host, tmp_path):
    """Anti-vacuity: the tests above must fail for the RIGHT reason. If resolution
    simply never worked, every one of them would pass while `navig run` was broken."""
    script = tmp_path / "ok.sh"
    script.write_text("echo hello\n", encoding="utf-8")

    sent: list[str] = []
    import navig.remote as r
    monkeypatch.setattr(r, "RemoteOperations", lambda *_a, **_k: SimpleNamespace(
        execute_command=lambda cmd, *a, **k: sent.append(cmd)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    ))

    remote_mod.run_remote_command(None, {}, file=script)  # must NOT raise
    assert sent and "echo hello" in sent[0]
