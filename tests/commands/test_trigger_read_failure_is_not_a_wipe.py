"""A failed READ of triggers.yaml must never become a destructive WRITE.

The sibling of `test_trigger_save_failure_is_reported`: that one pinned the WRITE
side (a trigger that could not be persisted must not be reported as created). This
is the read side, which was left open — the same asymmetry as the cdp-PID fix, where
one door into a destructive action was hardened and the one beside it was not.

`_load_triggers` cleared `self._triggers`, printed a WARNING, and returned. The
manager then held an EMPTY set, and every mutating verb is
`_ensure_loaded()` -> mutate -> `_save_triggers()`, so the next `navig trigger add`
wrote that empty set back over a populated file — atomically, at exit 0, with a ⚠
line above a ✓. One transient lock is enough: an antivirus or backup agent holding
the file, or a read landing mid-`os.replace`. Measured against the old code: three
triggers in, one out.

The warning sink is why no guard saw it. `test_command_exit_honesty` bans
`ch.error`-then-return tree-wide, but its ERROR_SINKS are {error, failure} — a
`ch.warning` is invisible to it. And `test_no_config_wipe_pattern` is data-flow
aware but scoped to ONE function: here the taint travels through `self._triggers`
across two methods, and the write is a hand-rolled mkstemp+os.replace rather than
`atomic_write_yaml`, so both of that guard's anchors miss.

The fix is the config layer's rule, applied to this store: remember that the load
failed and REFUSE to save while it stands, so an unreadable file is never replaced
by a partial one. Reads still degrade visibly (`trigger list` on a corrupt file
still runs) — only writes are blocked, which is the half that destroys data.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration


def _mgr(tmp_path):
    from navig.commands.triggers import TriggerManager

    return TriggerManager(config_manager=SimpleNamespace(global_config_dir=str(tmp_path)))


def _trigger(tid):
    from navig.commands.triggers import ActionType, Trigger, TriggerAction, TriggerType

    return Trigger(
        id=tid,
        name=f"probe {tid}",
        type=TriggerType.MANUAL,
        actions=[TriggerAction(type=ActionType.NOTIFY, target="console")],
    )


def _seed(tmp_path, count=3):
    """Persist `count` real triggers and return (manager, file, original bytes)."""
    mgr = _mgr(tmp_path)
    for i in range(1, count + 1):
        assert mgr.add_trigger(_trigger(f"t{i}")) is True
    return mgr, mgr.triggers_file, mgr.triggers_file.read_text(encoding="utf-8")


def test_an_unreadable_triggers_file_is_not_overwritten(tmp_path):
    """Real malformed content, not a patched stdlib: the file HAS data and cannot
    be parsed, which is exactly the case that must never be overwritten."""
    _, path, _original = _seed(tmp_path)
    corrupt = "version: 1\ntriggers: [ this is not: valid: yaml\n"
    path.write_text(corrupt, encoding="utf-8")

    fresh = _mgr(tmp_path)
    assert fresh.add_trigger(_trigger("t4")) is False, (
        "a trigger was reported as added on top of a file that could not be read"
    )
    assert path.read_text(encoding="utf-8") == corrupt, (
        "the unreadable file was REPLACED — this is the data-loss bug"
    )


def test_a_transient_read_lock_does_not_destroy_the_file(tmp_path, monkeypatch):
    """The antivirus / mid-`os.replace` case, forced at the reader itself.

    Patched at `read_text_retrying` — the one seam every yaml read funnels through —
    rather than at `builtins.open`, which `Path.read_text` does NOT go through (it
    calls `io.open`). Patching the wrong seam made an earlier probe report a pass
    while never exercising the failure at all.
    """
    from navig.core import yaml_io

    _, path, original = _seed(tmp_path)

    def _locked(*_a, **_kw):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(yaml_io, "read_text_retrying", _locked)

    fresh = _mgr(tmp_path)
    assert fresh.add_trigger(_trigger("t4")) is False
    assert path.read_text(encoding="utf-8") == original, (
        "one transient lock destroyed every existing trigger"
    )


def test_a_malformed_entry_does_not_drop_the_others(tmp_path):
    """A partial load is worse than an empty one — it looks plausible on disk."""
    _, path, _original = _seed(tmp_path)
    broken = "version: 1\ntriggers:\n- id: good\n  name: good\n  type: manual\n- 12345\n"
    path.write_text(broken, encoding="utf-8")

    fresh = _mgr(tmp_path)
    assert fresh.add_trigger(_trigger("t4")) is False
    assert path.read_text(encoding="utf-8") == broken, (
        "the surviving entries were dropped because one row failed to parse"
    )


def test_list_does_not_report_an_unreadable_store_as_an_empty_one(tmp_path, monkeypatch, capsys):
    """"I could not read it" is not "you have none" — and the advice differs.

    `navig trigger list` answered "No triggers configured. Add one with: navig
    trigger add" at exit 0 when the file could not be read: it told the operator to
    recreate triggers they still have. The manager warns (a read must not crash a
    read-only verb); the COMMAND decides the exit code.
    """
    import typer

    from navig.commands import triggers as mod

    _seed(tmp_path)
    corrupt = "version: 1\ntriggers: [ broken: : :\n"
    (tmp_path / "triggers" / "triggers.yaml").write_text(corrupt, encoding="utf-8")

    # Capture the real class FIRST: `_mgr` constructs `TriggerManager` through this
    # same module attribute, so patching it with a lambda that calls `_mgr` recurses.
    real = mod.TriggerManager
    monkeypatch.setattr(
        mod,
        "TriggerManager",
        lambda *a, **k: real(
            config_manager=SimpleNamespace(global_config_dir=str(tmp_path))
        ),
    )

    with pytest.raises(typer.Exit) as exc:
        mod.list_triggers()
    assert exc.value.exit_code == 1

    out = capsys.readouterr().out
    assert "No triggers configured" not in out, (
        "an unreadable store was reported as an empty one"
    )


# ── anti-vacuity: refusing ALWAYS would satisfy every test above ──────────


def test_a_normal_add_still_persists_and_keeps_the_existing_ones(tmp_path):
    mgr, path, _ = _seed(tmp_path)

    fresh = _mgr(tmp_path)
    assert fresh.add_trigger(_trigger("t4")) is True

    reread = _mgr(tmp_path)
    ids = {t.id for t in reread.list_triggers()}
    assert ids == {"t1", "t2", "t3", "t4"}, f"expected all four, got {sorted(ids)}"


def test_list_still_reports_a_genuinely_empty_store_at_exit_zero(tmp_path, monkeypatch, capsys):
    """The other direction — otherwise `raise Exit(1)` always would pass the test
    above, and a fresh install would look broken."""
    from navig.commands import triggers as mod

    # Capture the real class FIRST: `_mgr` constructs `TriggerManager` through this
    # same module attribute, so patching it with a lambda that calls `_mgr` recurses.
    real = mod.TriggerManager
    monkeypatch.setattr(
        mod,
        "TriggerManager",
        lambda *a, **k: real(
            config_manager=SimpleNamespace(global_config_dir=str(tmp_path))
        ),
    )
    mod.list_triggers()  # must not raise

    assert "No triggers configured" in capsys.readouterr().out


def test_a_fresh_install_with_no_file_is_not_a_read_failure(tmp_path):
    """Absent is not unreadable — the first `trigger add` on a new machine must work."""
    mgr = _mgr(tmp_path)
    assert not mgr.triggers_file.exists()
    assert mgr.add_trigger(_trigger("first")) is True
    assert mgr.triggers_file.exists()


def test_an_empty_file_is_safe_to_overwrite(tmp_path):
    """Empty / comments-only loses nothing when replaced — refusing here would make
    a blank file permanently unwritable, which is a different way to lose the feature.
    """
    mgr = _mgr(tmp_path)
    mgr.triggers_file.parent.mkdir(parents=True, exist_ok=True)
    mgr.triggers_file.write_text("# nothing here yet\n", encoding="utf-8")

    fresh = _mgr(tmp_path)
    assert fresh.add_trigger(_trigger("t1")) is True
