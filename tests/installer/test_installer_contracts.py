"""Tests for navig.installer.contracts — Action, Result, InstallerContext, ModuleState."""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.installer.contracts import (
    Action,
    InstallerContext,
    ModuleState,
    Result,
)


class TestModuleState:
    def test_enum_values(self):
        assert ModuleState.PENDING.value == "pending"
        assert ModuleState.APPLIED.value == "applied"
        assert ModuleState.FAILED.value == "failed"
        assert ModuleState.SKIPPED.value == "skipped"
        assert ModuleState.ROLLED_BACK.value == "rolled_back"

    def test_enum_members(self):
        members = {s.name for s in ModuleState}
        assert members == {"PENDING", "APPLIED", "FAILED", "SKIPPED", "ROLLED_BACK"}


class TestAction:
    def test_required_fields(self):
        a = Action(id="test.step", description="do it", module="my_mod")
        assert a.id == "test.step"
        assert a.description == "do it"
        assert a.module == "my_mod"

    def test_defaults(self):
        a = Action(id="x", description="y", module="z")
        assert a.data == {}
        assert a.reversible is True
        assert a.undo_data == {}

    def test_data_is_independent(self):
        a1 = Action(id="a", description="", module="m")
        a2 = Action(id="b", description="", module="m")
        a1.data["key"] = 1
        assert a2.data == {}

    def test_reversible_false(self):
        a = Action(id="x", description="y", module="z", reversible=False)
        assert a.reversible is False


class TestResult:
    def test_ok_when_applied(self):
        r = Result(action_id="a.b", state=ModuleState.APPLIED)
        assert r.ok is True

    def test_ok_when_skipped(self):
        r = Result(action_id="a.b", state=ModuleState.SKIPPED)
        assert r.ok is True

    def test_not_ok_when_failed(self):
        r = Result(action_id="a.b", state=ModuleState.FAILED, error="boom")
        assert r.ok is False

    def test_not_ok_when_pending(self):
        r = Result(action_id="a.b", state=ModuleState.PENDING)
        assert r.ok is False

    def test_error_default_none(self):
        r = Result(action_id="a.b", state=ModuleState.APPLIED)
        assert r.error is None

    def test_message_default_empty(self):
        r = Result(action_id="x", state=ModuleState.APPLIED)
        assert r.message == ""


class TestInstallerContext:
    def test_required_profile(self):
        ctx = InstallerContext(profile="node")
        assert ctx.profile == "node"

    def test_defaults(self):
        ctx = InstallerContext(profile="operator")
        assert ctx.dry_run is False
        assert ctx.quiet is False
        assert isinstance(ctx.config_dir, Path)
        assert ctx.extra == {}

    def test_dry_run(self):
        ctx = InstallerContext(profile="node", dry_run=True)
        assert ctx.dry_run is True

    def test_extra_dict(self):
        ctx = InstallerContext(profile="node", extra={"token": "abc"})
        assert ctx.extra["token"] == "abc"
