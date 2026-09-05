"""`navig evolution`, `agent status` and `context set` reported failure and exited 0.

The sharpest is evolution: all four commands printed "X evolution failed: …" and
exited 0, so

    navig evolution skill "do the thing" && <use the skill>

used a skill that was never created.

`agent status` is the other half of the same coin — a status query that
successfully reports "not installed" SUCCEEDED, so it stays exit 0 (only its ✗
glyph was wrong); a status that could not be READ is unanswerable and exits 1.
Both directions are pinned here, or a later sweep will "fix" the first into
uselessness.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer

pytestmark = pytest.mark.integration


# ── evolution ──────────────────────────────────────────────────────────


def _result(success: bool, *, attempts: int = 1, error: str = "generator gave up"):
    return SimpleNamespace(success=success, error=error, attempts=attempts, artifact="x")


COMMANDS = [
    ("evolve_skill", "navig.core.evolution.skill", "SkillEvolver"),
    ("evolve_workflow", "navig.core.evolution.workflow", "WorkflowEvolver"),
    ("evolve_pack", "navig.core.evolution.pack", "PackEvolver"),
    ("evolve_script", "navig.core.evolution.script", "ScriptEvolver"),
]


def _patch_evolver(monkeypatch, module, cls_name, result):
    import sys

    evolver = MagicMock()
    evolver.evolve.return_value = result
    mod = sys.modules.get(module)
    if mod is None:  # pragma: no cover - import shape differs
        pytest.skip(f"{module} not importable in this build")
    monkeypatch.setattr(mod, cls_name, lambda *a, **k: evolver)
    return evolver


@pytest.mark.parametrize(("fn_name", "module", "cls_name"), COMMANDS)
def test_a_failed_evolution_exits_non_zero(monkeypatch, fn_name, module, cls_name):
    import importlib

    importlib.import_module(module)
    import navig.commands.evolution as e_mod

    _patch_evolver(monkeypatch, module, cls_name, _result(False))

    with pytest.raises(typer.Exit) as exc:
        getattr(e_mod, fn_name)("do the thing")
    assert exc.value.exit_code == 1


@pytest.mark.parametrize(("fn_name", "module", "cls_name"), COMMANDS)
def test_a_successful_evolution_still_exits_zero(monkeypatch, fn_name, module, cls_name):
    """Anti-vacuity — raising unconditionally would satisfy every test above."""
    import importlib

    importlib.import_module(module)
    import navig.commands.evolution as e_mod

    _patch_evolver(monkeypatch, module, cls_name, _result(True))
    getattr(e_mod, fn_name)("do the thing")  # must not raise


def test_a_cached_artifact_is_reported_as_reused(monkeypatch, capsys):
    """`attempts == 0` means BaseEvolver returned a library hit, not a generation.

    Replaces a `# Could print path` comment that was never implemented; this is
    the part the result actually carries.
    """
    import importlib

    importlib.import_module("navig.core.evolution.skill")
    import navig.commands.evolution as e_mod

    _patch_evolver(
        monkeypatch, "navig.core.evolution.skill", "SkillEvolver", _result(True, attempts=0)
    )
    e_mod.evolve_skill("do the thing")

    assert "Reused an existing artifact" in capsys.readouterr().out


def test_retries_are_reported(monkeypatch, capsys):
    import importlib

    importlib.import_module("navig.core.evolution.skill")
    import navig.commands.evolution as e_mod

    _patch_evolver(
        monkeypatch, "navig.core.evolution.skill", "SkillEvolver", _result(True, attempts=3)
    )
    e_mod.evolve_skill("do the thing")

    assert "3 attempts" in capsys.readouterr().out


# ── agent status: both directions ──────────────────────────────────────


def test_an_unreadable_agent_status_exits_non_zero(monkeypatch, tmp_path):
    """A status we could not read is a failure, not an answer."""
    import navig.commands.agent as a_mod

    cfg_dir = tmp_path / "agentcfg"
    cfg_dir.mkdir()
    # Real malformed data rather than a patched stdlib: the file EXISTS (so this is
    # not the "not installed" path) but cannot be parsed, which is exactly the
    # "status we could not read" case.
    (cfg_dir / "config.yaml").write_text("{{{ not: [valid", encoding="utf-8")
    monkeypatch.setattr(a_mod, "_get_agent_config_dir", lambda: cfg_dir)

    with pytest.raises(typer.Exit) as exc:
        a_mod.agent_status_cmd(plain=False) if hasattr(a_mod, "agent_status_cmd") else a_mod.agent_status(plain=False)
    assert exc.value.exit_code == 1


def test_a_not_installed_agent_status_still_exits_zero(monkeypatch, tmp_path, capsys):
    """The query SUCCEEDED — "not installed" is a real answer.

    Same rule as `cron status` and `browser status`. Only the ✗ glyph was wrong.
    """
    import navig.commands.agent as a_mod

    empty = tmp_path / "nothing"
    empty.mkdir()
    monkeypatch.setattr(a_mod, "_get_agent_config_dir", lambda: empty)

    fn = getattr(a_mod, "agent_status_cmd", None) or a_mod.agent_status
    fn(plain=False)  # must not raise

    out = capsys.readouterr().out
    assert "not installed" in out.lower()
    assert "✗" not in out, "an error glyph over a zero exit is the same disagreement"


# ── the three the SOURCE GUARD structurally cannot see ─────────────────
#
# `test_command_exit_honesty` exempts any function containing a `raise` ANYWHERE
# from its tail check, because "print the error here, raise later" is a legitimate
# shape. All three below raise somewhere else in the same function, so they were
# invisible to it — the error was simply the last statement of its block and the
# function fell off the end at exit 0. A behaviour test is the only thing that can
# hold them, exactly as the guard's own docstring prescribes for such misses.


def test_a_failed_evolution_fix_exits_non_zero(monkeypatch, tmp_path):
    """`evolve_fix` is the FIFTH evolution command and had the same bug.

    It hid behind its own `raise typer.Exit(1)` for a missing file, several lines
    earlier — a raise that cannot rescue an error printed after it.
    """
    import importlib

    fix_mod = importlib.import_module("navig.core.evolution.fix")
    import navig.commands.evolution as e_mod

    target = tmp_path / "thing.py"
    target.write_text("x = 1\n", encoding="utf-8")

    evolver = MagicMock()
    evolver.evolve.return_value = _result(False, error="model refused")
    monkeypatch.setattr(fix_mod, "FixEvolver", lambda *a, **k: evolver)

    with pytest.raises(typer.Exit) as exc:
        e_mod.evolve_fix(file_path=target, instruction="fix the bug", check=None)
    assert exc.value.exit_code == 1


def test_a_successful_evolution_fix_still_exits_zero(monkeypatch, tmp_path):
    """Anti-vacuity for the test above."""
    import importlib

    fix_mod = importlib.import_module("navig.core.evolution.fix")
    import navig.commands.evolution as e_mod

    target = tmp_path / "thing.py"
    target.write_text("x = 1\n", encoding="utf-8")

    evolver = MagicMock()
    evolver.evolve.return_value = _result(True)
    monkeypatch.setattr(fix_mod, "FixEvolver", lambda *a, **k: evolver)

    e_mod.evolve_fix(file_path=target, instruction="fix the bug", check=None)


def test_an_unknown_personality_action_is_a_usage_error():
    """`navig agent personality bogus` printed "Unknown action" and exited 0."""
    import navig.commands.agent as a_mod

    with pytest.raises(typer.Exit) as exc:
        a_mod.agent_personality(action="bogus", name=None)
    assert exc.value.exit_code == 2, "an action that does not exist is a usage error"


def test_a_known_personality_action_is_not_a_usage_error():
    """Anti-vacuity: `list` must not raise Exit(2) just because the other did."""
    import navig.commands.agent as a_mod

    try:
        a_mod.agent_personality(action="list", name=None)
    except typer.Exit as exc:  # a real failure is allowed; a USAGE error is not
        assert exc.exit_code != 2, "a valid action reported as a usage error"


# ── context set ────────────────────────────────────────────────────────


def _ctx_config(hosts=("prod",), apps=()):
    return SimpleNamespace(
        host_exists=lambda h: h in hosts,
        list_hosts=lambda: list(hosts),
        get_active_host=lambda: None,
        app_exists=lambda h, a: a in apps,
        list_apps=lambda h: list(apps),
    )


def test_set_context_with_no_target_is_a_usage_error(monkeypatch):
    import navig.commands.context as c_mod

    monkeypatch.setattr(c_mod, "get_config_manager", lambda: _ctx_config())
    with pytest.raises(typer.Exit) as exc:
        c_mod.set_context(host=None, app=None)
    assert exc.value.exit_code == 2, "a malformed invocation is exit 2"


def test_set_context_with_an_unknown_host_exits_non_zero(monkeypatch):
    import navig.commands.context as c_mod

    monkeypatch.setattr(c_mod, "get_config_manager", lambda: _ctx_config())
    with pytest.raises(typer.Exit) as exc:
        c_mod.set_context(host="nope", app=None)
    assert exc.value.exit_code == 1, "a lookup that found nothing is exit 1"


def test_set_context_for_an_app_without_a_host_is_a_usage_error(monkeypatch):
    import navig.commands.context as c_mod

    monkeypatch.setattr(c_mod, "get_config_manager", lambda: _ctx_config())
    with pytest.raises(typer.Exit) as exc:
        c_mod.set_context(host=None, app="web")
    assert exc.value.exit_code == 2
