"""
Tests for navig.migrations.migrate_addons_to_templates
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# Module import guard
# ---------------------------------------------------------------------------
from navig.migrations.migrate_addons_to_templates import (
    AddonToTemplateMigration,
    migrate_addons_to_templates,
    migrate_addons_to_templates_cmd,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_migration(tmp_path: Path, *, dry_run: bool = False, force: bool = False) -> AddonToTemplateMigration:
    """Return a migration instance whose paths are redirected to tmp_path."""
    mock_cfg = MagicMock()
    mock_cfg.apps_dir = tmp_path / "apps"

    with (
        patch("navig.migrations.migrate_addons_to_templates.get_config_manager", return_value=mock_cfg),
        patch("navig.migrations.migrate_addons_to_templates.ch"),
    ):
        m = AddonToTemplateMigration(dry_run=dry_run, force=force)

    m.config_manager = mock_cfg
    # redirect repo paths to tmp
    m.repo_root = tmp_path
    m.addons_dir = tmp_path / "addons"
    m.templates_dir = tmp_path / "store" / "templates"
    return m


# ---------------------------------------------------------------------------
# _load_file
# ---------------------------------------------------------------------------


class TestLoadFile:
    def test_loads_json(self, tmp_path):
        p = tmp_path / "x.json"
        p.write_text('{"a": 1}', encoding="utf-8")
        m = _make_migration(tmp_path)
        assert m._load_file(p) == {"a": 1}

    def test_loads_yaml(self, tmp_path):
        p = tmp_path / "x.yaml"
        p.write_text("a: 1\n", encoding="utf-8")
        m = _make_migration(tmp_path)
        assert m._load_file(p) == {"a": 1}

    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        p = tmp_path / "x.yaml"
        p.write_text("", encoding="utf-8")
        m = _make_migration(tmp_path)
        assert m._load_file(p) == {}

    def test_invalid_json_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json}", encoding="utf-8")
        m = _make_migration(tmp_path)
        with pytest.raises(Exception):
            m._load_file(p)


# ---------------------------------------------------------------------------
# _save_yaml
# ---------------------------------------------------------------------------


class TestSaveYaml:
    def test_writes_valid_yaml(self, tmp_path):
        m = _make_migration(tmp_path)
        out = tmp_path / "out.yaml"
        m._save_yaml(out, {"key": "value", "num": 42})
        loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert loaded["key"] == "value"
        assert loaded["num"] == 42

    def test_adds_migration_header_comment(self, tmp_path):
        m = _make_migration(tmp_path)
        out = tmp_path / "out.yaml"
        m._save_yaml(out, {"x": 1})
        content = out.read_text(encoding="utf-8")
        assert "# Migrated from addons system" in content


# ---------------------------------------------------------------------------
# _migrate_repo_addons — no-op when addons/ missing
# ---------------------------------------------------------------------------


class TestMigrateRepoAddons:
    def _run(self, m: AddonToTemplateMigration):
        with patch.object(m, "_save_yaml") as mock_save:
            with patch("navig.migrations.migrate_addons_to_templates.ch"):
                m._migrate_repo_addons()
        return mock_save

    def test_no_addons_dir_skips_silently(self, tmp_path):
        m = _make_migration(tmp_path)
        # addons_dir does not exist — should be a no-op
        self._run(m)
        assert m.migrated_repo == []
        assert m.errors == []

    def test_migrates_json_addon(self, tmp_path):
        m = _make_migration(tmp_path)
        addon_dir = m.addons_dir / "myaddon"
        addon_dir.mkdir(parents=True)
        (addon_dir / "addon.json").write_text('{"name": "myaddon"}', encoding="utf-8")

        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            m._migrate_repo_addons()

        assert "myaddon" in m.migrated_repo
        target = m.templates_dir / "myaddon" / "template.yaml"
        assert target.exists()

    def test_migrates_yaml_addon(self, tmp_path):
        m = _make_migration(tmp_path)
        addon_dir = m.addons_dir / "yamlpkg"
        addon_dir.mkdir(parents=True)
        (addon_dir / "addon.yaml").write_text("name: yamlpkg\n", encoding="utf-8")

        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            m._migrate_repo_addons()

        assert "yamlpkg" in m.migrated_repo

    def test_skips_addon_without_source(self, tmp_path):
        m = _make_migration(tmp_path)
        addon_dir = m.addons_dir / "orphan"
        addon_dir.mkdir(parents=True)
        # No addon.json or addon.yaml

        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            m._migrate_repo_addons()

        assert "orphan" not in m.migrated_repo
        assert any("orphan" in s for s, _ in m.skipped)

    def test_skips_already_existing_target(self, tmp_path):
        m = _make_migration(tmp_path)
        addon_dir = m.addons_dir / "existing"
        addon_dir.mkdir(parents=True)
        (addon_dir / "addon.json").write_text("{}", encoding="utf-8")

        # Pre-create target
        target_dir = m.templates_dir / "existing"
        target_dir.mkdir(parents=True)
        (target_dir / "template.yaml").write_text("# old\n", encoding="utf-8")

        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            m._migrate_repo_addons()

        assert "existing" not in m.migrated_repo
        assert m.skipped  # skipped because already exists

    def test_force_overwrites_existing_target(self, tmp_path):
        m = _make_migration(tmp_path, force=True)
        addon_dir = m.addons_dir / "existing"
        addon_dir.mkdir(parents=True)
        (addon_dir / "addon.json").write_text('{"v": 2}', encoding="utf-8")

        target_dir = m.templates_dir / "existing"
        target_dir.mkdir(parents=True)
        (target_dir / "template.yaml").write_text("# old\n", encoding="utf-8")

        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            m._migrate_repo_addons()

        assert "existing" in m.migrated_repo

    def test_dry_run_does_not_write_files(self, tmp_path):
        m = _make_migration(tmp_path, dry_run=True)
        addon_dir = m.addons_dir / "drytest"
        addon_dir.mkdir(parents=True)
        (addon_dir / "addon.json").write_text("{}", encoding="utf-8")

        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            m._migrate_repo_addons()

        assert "drytest" in m.migrated_repo
        assert not (m.templates_dir / "drytest" / "template.yaml").exists()

    def test_copies_readme_alongside_template(self, tmp_path):
        m = _make_migration(tmp_path)
        addon_dir = m.addons_dir / "withdoc"
        addon_dir.mkdir(parents=True)
        (addon_dir / "addon.json").write_text("{}", encoding="utf-8")
        (addon_dir / "README.md").write_text("# doc\n", encoding="utf-8")

        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            m._migrate_repo_addons()

        assert (m.templates_dir / "withdoc" / "README.md").exists()

    def test_records_error_on_corrupt_json(self, tmp_path):
        m = _make_migration(tmp_path)
        addon_dir = m.addons_dir / "corrupt"
        addon_dir.mkdir(parents=True)
        (addon_dir / "addon.json").write_text("{bad json}", encoding="utf-8")

        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            m._migrate_repo_addons()

        assert any("corrupt" in p for p, _ in m.errors)


# ---------------------------------------------------------------------------
# _migrate_user_addons
# ---------------------------------------------------------------------------


class TestMigrateUserAddons:
    def test_no_apps_dir_is_noop(self, tmp_path):
        m = _make_migration(tmp_path)
        # apps_dir does not exist
        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            m._migrate_user_addons()
        assert m.migrated_user == []

    def test_migrates_json_user_addon(self, tmp_path):
        m = _make_migration(tmp_path)
        apps_dir = tmp_path / "apps"
        m.config_manager.apps_dir = apps_dir
        server_dir = apps_dir / "prod"
        addons_dir = server_dir / "addons"
        addons_dir.mkdir(parents=True)
        (addons_dir / "myaddon.json").write_text('{"key": "val"}', encoding="utf-8")

        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            m._migrate_user_addons()

        assert "prod/myaddon" in m.migrated_user
        target = server_dir / "templates" / "myaddon.yaml"
        assert target.exists()

    def test_migrates_yaml_user_addon(self, tmp_path):
        m = _make_migration(tmp_path)
        apps_dir = tmp_path / "apps"
        m.config_manager.apps_dir = apps_dir
        server_dir = apps_dir / "prod"
        addons_dir = server_dir / "addons"
        addons_dir.mkdir(parents=True)
        (addons_dir / "myaddon.yaml").write_text("key: val\n", encoding="utf-8")

        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            m._migrate_user_addons()

        assert "prod/myaddon" in m.migrated_user

    def test_skips_existing_user_override(self, tmp_path):
        m = _make_migration(tmp_path)
        apps_dir = tmp_path / "apps"
        m.config_manager.apps_dir = apps_dir
        server_dir = apps_dir / "prod"
        addons_dir = server_dir / "addons"
        addons_dir.mkdir(parents=True)
        (addons_dir / "old.json").write_text("{}", encoding="utf-8")

        templates_dir = server_dir / "templates"
        templates_dir.mkdir(parents=True)
        (templates_dir / "old.yaml").write_text("# existing\n", encoding="utf-8")

        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            m._migrate_user_addons()

        assert "prod/old" not in m.migrated_user
        assert m.skipped


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


class TestRun:
    def test_returns_true_on_no_errors(self, tmp_path):
        m = _make_migration(tmp_path)

        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            result = m.run()

        assert result is True

    def test_returns_false_on_errors(self, tmp_path):
        m = _make_migration(tmp_path)
        m.errors = [("/some/path", "some error")]

        with patch("navig.migrations.migrate_addons_to_templates.ch"):
            result = m.run()

        assert result is False


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def test_migrate_addons_to_templates_wrapper(tmp_path):
    with (
        patch("navig.migrations.migrate_addons_to_templates.get_config_manager"),
        patch("navig.migrations.migrate_addons_to_templates.ch"),
    ):
        # bare call with no addons dir → returns True (no errors)
        result = migrate_addons_to_templates(dry_run=True, force=False)

    assert isinstance(result, bool)


def test_migrate_addons_to_templates_cmd_success():
    with (
        patch(
            "navig.migrations.migrate_addons_to_templates.migrate_addons_to_templates",
            return_value=True,
        ),
    ):
        # Should not raise
        migrate_addons_to_templates_cmd({})


def test_migrate_addons_to_templates_cmd_failure():
    with (
        patch(
            "navig.migrations.migrate_addons_to_templates.migrate_addons_to_templates",
            return_value=False,
        ),
        pytest.raises(SystemExit),
    ):
        migrate_addons_to_templates_cmd({})
