"""A trigger that could not be persisted must not be reported as created.

`_save_triggers` printed the write error and returned None, and all three callers
did `self._save_triggers()` then `return True` unconditionally. So `navig trigger
add` said the trigger existed, the file was never written, and the next run simply
did not have it — a disk error turned into silent data loss.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration


def _mgr(tmp_path):
    from navig.commands.triggers import TriggerManager

    return TriggerManager(config_manager=SimpleNamespace(global_config_dir=str(tmp_path)))


def _trigger(tid="t1"):
    from navig.commands.triggers import ActionType, Trigger, TriggerAction, TriggerType

    return Trigger(
        id=tid,
        name="probe",
        type=TriggerType.MANUAL,
        actions=[TriggerAction(type=ActionType.NOTIFY, target="console")],
    )


def test_add_trigger_reports_false_when_the_write_fails(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)

    def _fail(*_a, **_kw):
        raise OSError("disk full")

    # os.replace is the last step of the atomic write — fail there, after the temp
    # file exists, which is the realistic disk-full/permission case.
    monkeypatch.setattr(os, "replace", _fail)

    assert mgr.add_trigger(_trigger()) is False, (
        "a trigger that was never written to disk was reported as created"
    )


def test_add_trigger_reports_true_when_the_write_succeeds(tmp_path):
    """The other half — otherwise `return False` always would also pass above."""
    mgr = _mgr(tmp_path)
    assert mgr.add_trigger(_trigger("t2")) is True
    assert mgr.triggers_file.exists()


def test_remove_trigger_reports_false_when_the_write_fails(tmp_path, monkeypatch):
    """Same contract on the delete path: a removal that did not persist is not done."""
    mgr = _mgr(tmp_path)
    assert mgr.add_trigger(_trigger("t3")) is True

    def _fail(*_a, **_kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(os, "replace", _fail)
    assert mgr.remove_trigger("t3") is False
