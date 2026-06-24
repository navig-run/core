"""Tests for commands/packs.py — PackType, PackStatus, PackManifest, PackStep — batch 116."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# PackType enum
# ---------------------------------------------------------------------------

class TestPackTypeEnum:
    def test_workflow_value(self):
        from navig.commands.packs import PackType
        assert PackType.WORKFLOW.value == "workflow"

    def test_runbook_value(self):
        from navig.commands.packs import PackType
        assert PackType.RUNBOOK.value == "runbook"

    def test_checklist_value(self):
        from navig.commands.packs import PackType
        assert PackType.CHECKLIST.value == "checklist"

    def test_template_value(self):
        from navig.commands.packs import PackType
        assert PackType.TEMPLATE.value == "template"

    def test_bundle_value(self):
        from navig.commands.packs import PackType
        assert PackType.BUNDLE.value == "bundle"

    def test_all_are_strings(self):
        from navig.commands.packs import PackType
        for pt in PackType:
            assert isinstance(pt, str)


# ---------------------------------------------------------------------------
# PackStatus enum
# ---------------------------------------------------------------------------

class TestPackStatusEnum:
    def test_available_value(self):
        from navig.commands.packs import PackStatus
        assert PackStatus.AVAILABLE.value == "available"

    def test_installed_value(self):
        from navig.commands.packs import PackStatus
        assert PackStatus.INSTALLED.value == "installed"

    def test_outdated_value(self):
        from navig.commands.packs import PackStatus
        assert PackStatus.OUTDATED.value == "outdated"

    def test_local_value(self):
        from navig.commands.packs import PackStatus
        assert PackStatus.LOCAL.value == "local"

    def test_all_are_strings(self):
        from navig.commands.packs import PackStatus
        for ps in PackStatus:
            assert isinstance(ps, str)


# ---------------------------------------------------------------------------
# PackManifest dataclass
# ---------------------------------------------------------------------------

class TestPackManifestInit:
    def _make(self, **kwargs):
        from navig.commands.packs import PackManifest
        defaults = dict(name="my-pack")
        defaults.update(kwargs)
        return PackManifest(**defaults)

    def test_name_stored(self):
        m = self._make(name="deploy-runbook")
        assert m.name == "deploy-runbook"

    def test_description_default_empty(self):
        m = self._make()
        assert m.description == ""

    def test_version_default(self):
        m = self._make()
        assert m.version == "1.0.0"

    def test_type_default_runbook(self):
        from navig.commands.packs import PackManifest, PackType
        m = self._make()
        assert m.type == PackType.RUNBOOK

    def test_steps_default_empty(self):
        m = self._make()
        assert m.steps == []

    def test_variables_default_empty(self):
        m = self._make()
        assert m.variables == {}

    def test_tags_default_empty(self):
        m = self._make()
        assert m.tags == []

    def test_license_default_mit(self):
        m = self._make()
        assert m.license == "MIT"

    def test_custom_type(self):
        from navig.commands.packs import PackManifest, PackType
        m = self._make(type=PackType.WORKFLOW)
        assert m.type == PackType.WORKFLOW

    def test_source_path_default_none(self):
        m = self._make()
        assert m.source_path is None


class TestPackManifestToDict:
    def _make(self, **kwargs):
        from navig.commands.packs import PackManifest
        return PackManifest(name="test", **kwargs)

    def test_to_dict_has_name(self):
        d = self._make().to_dict()
        assert d["name"] == "test"

    def test_to_dict_type_is_string(self):
        d = self._make().to_dict()
        assert isinstance(d["type"], str)

    def test_to_dict_excludes_source_path(self):
        d = self._make().to_dict()
        assert "source_path" not in d

    def test_to_dict_excludes_installed_at(self):
        d = self._make().to_dict()
        assert "installed_at" not in d

    def test_to_dict_steps_is_list(self):
        d = self._make().to_dict()
        assert isinstance(d["steps"], list)


class TestPackManifestFromDict:
    def test_name_set(self):
        from navig.commands.packs import PackManifest
        m = PackManifest.from_dict({"name": "foo"})
        assert m.name == "foo"

    def test_type_string_parsed(self):
        from navig.commands.packs import PackManifest, PackType
        m = PackManifest.from_dict({"name": "x", "type": "workflow"})
        assert m.type == PackType.WORKFLOW

    def test_unknown_type_defaults_runbook(self):
        from navig.commands.packs import PackManifest, PackType
        m = PackManifest.from_dict({"name": "x", "type": "unknown_type"})
        assert m.type == PackType.RUNBOOK

    def test_source_path_set(self, tmp_path):
        from navig.commands.packs import PackManifest
        m = PackManifest.from_dict({"name": "x"}, source_path=tmp_path)
        assert m.source_path == tmp_path

    def test_version_passed_through(self):
        from navig.commands.packs import PackManifest
        m = PackManifest.from_dict({"name": "x", "version": "2.1.0"})
        assert m.version == "2.1.0"

    def test_ignores_unknown_fields(self):
        from navig.commands.packs import PackManifest
        m = PackManifest.from_dict({"name": "x", "unknown_field": "ignored"})
        assert m.name == "x"

    def test_author_set(self):
        from navig.commands.packs import PackManifest
        m = PackManifest.from_dict({"name": "x", "author": "NAVIG team"})
        assert m.author == "NAVIG team"


# ---------------------------------------------------------------------------
# PackStep dataclass
# ---------------------------------------------------------------------------

class TestPackStepInit:
    def _make(self, **kwargs):
        from navig.commands.packs import PackStep
        defaults = dict(description="Deploy app")
        defaults.update(kwargs)
        return PackStep(**defaults)

    def test_description_stored(self):
        s = self._make(description="Run migrations")
        assert s.description == "Run migrations"

    def test_command_default_none(self):
        s = self._make()
        assert s.command is None

    def test_notes_default_none(self):
        s = self._make()
        assert s.notes is None

    def test_continue_on_error_default_false(self):
        s = self._make()
        assert s.continue_on_error is False

    def test_skip_if_default_none(self):
        s = self._make()
        assert s.skip_if is None

    def test_custom_command(self):
        s = self._make(command="navig run 'deploy.sh'")
        assert s.command == "navig run 'deploy.sh'"


class TestPackStepFromDict:
    def test_description_extracted(self):
        from navig.commands.packs import PackStep
        s = PackStep.from_dict({"description": "Check disk space"})
        assert s.description == "Check disk space"

    def test_command_extracted(self):
        from navig.commands.packs import PackStep
        s = PackStep.from_dict({"description": "X", "command": "df -h"})
        assert s.command == "df -h"

    def test_continue_on_error_set(self):
        from navig.commands.packs import PackStep
        s = PackStep.from_dict({"description": "X", "continue_on_error": True})
        assert s.continue_on_error is True

    def test_notes_extracted(self):
        from navig.commands.packs import PackStep
        s = PackStep.from_dict({"description": "X", "notes": "Be careful"})
        assert s.notes == "Be careful"

    def test_empty_dict_defaults(self):
        from navig.commands.packs import PackStep
        s = PackStep.from_dict({})
        assert s.description == ""
        assert s.command is None
        assert s.continue_on_error is False
