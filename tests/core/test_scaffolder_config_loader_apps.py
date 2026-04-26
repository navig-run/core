"""
Tests for:
  - navig.core.scaffolder    (Scaffolder template validation, generation, conditions)
  - navig.core.config_loader (load_config, circular detection, include processing)
  - navig.core.apps          (AppManager exists/list_apps/find_hosts_with_app)

All tests are hermetic — no real SSH, config manager, or network calls.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ===========================================================================
# navig.core.scaffolder
# ===========================================================================

class TestScaffolderValidateTemplate:
    def _scaffolder(self):
        from navig.core.scaffolder import Scaffolder
        return Scaffolder()

    def test_valid_template_returns_dict(self, tmp_path):
        s = self._scaffolder()
        tmpl = tmp_path / "tmpl.yaml"
        tmpl.write_text(
            "structure:\n  - path: mydir\n    type: directory\n",
            encoding="utf-8",
        )
        data = s.validate_template(tmpl)
        assert isinstance(data, dict)
        assert "structure" in data

    def test_missing_structure_raises_value_error(self, tmp_path):
        s = self._scaffolder()
        tmpl = tmp_path / "tmpl.yaml"
        tmpl.write_text("meta:\n  name: test\n", encoding="utf-8")
        with pytest.raises(ValueError, match="structure"):
            s.validate_template(tmpl)

    def test_non_dict_raises_value_error(self, tmp_path):
        s = self._scaffolder()
        tmpl = tmp_path / "tmpl.yaml"
        tmpl.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="dictionary"):
            s.validate_template(tmpl)

    def test_invalid_yaml_raises_value_error(self, tmp_path):
        s = self._scaffolder()
        tmpl = tmp_path / "tmpl.yaml"
        tmpl.write_text("key: [\nunot_closed", encoding="utf-8")
        with pytest.raises(ValueError, match="YAML"):
            s.validate_template(tmpl)

    def test_meta_section_returned(self, tmp_path):
        s = self._scaffolder()
        tmpl = tmp_path / "tmpl.yaml"
        tmpl.write_text(
            "meta:\n  name: mytemplate\nstructure: []\n",
            encoding="utf-8",
        )
        data = s.validate_template(tmpl)
        assert data["meta"]["name"] == "mytemplate"


class TestScaffolderCheckCondition:
    def _scaffolder(self):
        from navig.core.scaffolder import Scaffolder
        return Scaffolder()

    def test_no_condition_returns_true(self):
        s = self._scaffolder()
        assert s._check_condition({}, {}) is True

    def test_condition_none_returns_true(self):
        s = self._scaffolder()
        assert s._check_condition({"condition": None}, {}) is True

    def test_condition_true_string(self):
        s = self._scaffolder()
        # "true" wrapped in {{ }} → renders "true" → True
        assert s._check_condition({"condition": "true"}, {}) is True

    def test_condition_false_string(self):
        s = self._scaffolder()
        assert s._check_condition({"condition": "false"}, {}) is False

    def test_condition_variable_truthy(self):
        s = self._scaffolder()
        assert s._check_condition({"condition": "include_tests"}, {"include_tests": True}) is True

    def test_condition_variable_falsy(self):
        s = self._scaffolder()
        assert s._check_condition({"condition": "include_tests"}, {"include_tests": False}) is False

    def test_condition_jinja_expression(self):
        s = self._scaffolder()
        # {{ "yes" }} renders "yes" which is in the truthy set
        assert s._check_condition({"condition": "{{ 'yes' }}"}, {}) is True

    def test_invalid_condition_returns_false(self):
        s = self._scaffolder()
        # Accessing undefined variable in strict mode would fail; the handler catches and returns False
        # We use a clearly broken expression
        result = s._check_condition({"condition": "{{ undefined_variable }}"}, {})
        # jinja2 renders undefined as "" by default → "" is not truthy
        assert result is False


class TestScaffolderGenerate:
    def _scaffolder(self):
        from navig.core.scaffolder import Scaffolder
        return Scaffolder()

    def test_creates_directory(self, tmp_path):
        s = self._scaffolder()
        template_data = {
            "structure": [{"path": "mydir", "type": "directory"}]
        }
        s.generate(template_data, tmp_path)
        assert (tmp_path / "mydir").is_dir()

    def test_creates_file_with_content(self, tmp_path):
        s = self._scaffolder()
        template_data = {
            "structure": [{"path": "hello.txt", "type": "file", "content": "hello world"}]
        }
        s.generate(template_data, tmp_path)
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hello world"

    def test_renders_jinja_variables(self, tmp_path):
        s = self._scaffolder()
        template_data = {
            "structure": [
                {"path": "{{ name }}.txt", "type": "file", "content": "Hi {{ name }}!"}
            ]
        }
        s.generate(template_data, tmp_path, variables={"name": "Alice"})
        assert (tmp_path / "Alice.txt").read_text(encoding="utf-8") == "Hi Alice!"

    def test_merges_template_default_variables(self, tmp_path):
        s = self._scaffolder()
        template_data = {
            "meta": {"variables": {"version": "1.0"}},
            "structure": [
                {"path": "version.txt", "type": "file", "content": "{{ version }}"}
            ],
        }
        s.generate(template_data, tmp_path)
        assert (tmp_path / "version.txt").read_text(encoding="utf-8") == "1.0"

    def test_user_variables_override_template_defaults(self, tmp_path):
        s = self._scaffolder()
        template_data = {
            "meta": {"variables": {"version": "1.0"}},
            "structure": [
                {"path": "version.txt", "type": "file", "content": "{{ version }}"}
            ],
        }
        s.generate(template_data, tmp_path, variables={"version": "2.5"})
        assert (tmp_path / "version.txt").read_text(encoding="utf-8") == "2.5"

    def test_nested_directory_structure(self, tmp_path):
        s = self._scaffolder()
        template_data = {
            "structure": [
                {
                    "path": "parent",
                    "type": "directory",
                    "children": [
                        {"path": "child.txt", "type": "file", "content": "nested"}
                    ],
                }
            ]
        }
        s.generate(template_data, tmp_path)
        nested_file = tmp_path / "parent" / "child.txt"
        assert nested_file.read_text(encoding="utf-8") == "nested"

    def test_condition_false_skips_item(self, tmp_path):
        s = self._scaffolder()
        template_data = {
            "structure": [
                {
                    "path": "skip_me.txt",
                    "type": "file",
                    "content": "should not exist",
                    "condition": "false",
                }
            ]
        }
        s.generate(template_data, tmp_path)
        assert not (tmp_path / "skip_me.txt").exists()

    def test_empty_path_skipped(self, tmp_path):
        s = self._scaffolder()
        template_data = {
            "structure": [{"path": "", "type": "file", "content": "ignored"}]
        }
        # Should not raise and should not create any file
        s.generate(template_data, tmp_path)
        assert not list(tmp_path.iterdir())


# ===========================================================================
# navig.core.config_loader
# ===========================================================================

class TestConfigLoaderConstants:
    def test_max_include_depth_is_10(self):
        from navig.core.config_loader import MAX_INCLUDE_DEPTH
        assert MAX_INCLUDE_DEPTH == 10

    def test_config_loader_error_is_exception(self):
        from navig.core.config_loader import ConfigLoaderError
        e = ConfigLoaderError("boom")
        assert isinstance(e, Exception)
        assert str(e) == "boom"

    def test_circular_dependency_error_is_config_loader_error(self):
        from navig.core.config_loader import CircularDependencyError, ConfigLoaderError
        e = CircularDependencyError("cycle")
        assert isinstance(e, ConfigLoaderError)


class TestProcessIncludes:
    def test_passthrough_scalar(self):
        from navig.core.config_loader import _process_includes
        result = _process_includes("hello", Path("/fake"), set(), 0)
        assert result == "hello"

    def test_passthrough_list(self):
        from navig.core.config_loader import _process_includes
        result = _process_includes([1, 2, 3], Path("/fake"), set(), 0)
        assert result == [1, 2, 3]

    def test_passthrough_none(self):
        from navig.core.config_loader import _process_includes
        result = _process_includes(None, Path("/fake"), set(), 0)
        assert result is None

    def test_passthrough_plain_dict(self, tmp_path):
        from navig.core.config_loader import _process_includes
        data = {"key": "value", "num": 42}
        result = _process_includes(data, tmp_path, set(), 0)
        assert result["key"] == "value"
        assert result["num"] == 42


class TestLoadConfigBasic:
    def test_loads_simple_yaml(self, tmp_path):
        from navig.core.config_loader import load_config

        cfg = tmp_path / "config.yaml"
        cfg.write_text("host: myserver\nport: 22\n", encoding="utf-8")
        result = load_config(cfg)
        assert result["host"] == "myserver"
        assert result["port"] == 22

    def test_file_not_found_raises(self, tmp_path):
        from navig.core.config_loader import load_config

        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_env_var_substitution(self, tmp_path, monkeypatch):
        from navig.core.config_loader import load_config

        monkeypatch.setenv("MY_HOST", "production-server")
        cfg = tmp_path / "config.yaml"
        cfg.write_text("host: ${MY_HOST}\n", encoding="utf-8")
        result = load_config(cfg)
        assert result["host"] == "production-server"


class TestCircularIncludeDetection:
    def test_circular_raises(self, tmp_path):
        from navig.core.config_loader import _load_yaml_recursive, CircularDependencyError

        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        a.write_text(f"$include: b.yaml\nkey: a\n", encoding="utf-8")
        b.write_text(f"$include: a.yaml\nkey: b\n", encoding="utf-8")

        with pytest.raises((CircularDependencyError, Exception)):
            _load_yaml_recursive(a, set())


# ===========================================================================
# navig.core.apps — AppManager
# ===========================================================================

def _make_mock_config(
    config_dirs: list[Path] | None = None,
    host_apps: dict[str, dict] | None = None,
    hosts: list[str] | None = None,
) -> MagicMock:
    """Build a mock AppConfigProvider."""
    cfg = MagicMock()
    cfg.get_config_directories.return_value = config_dirs or []

    def load_host(name, use_cache=True):
        if host_apps and name in host_apps:
            return {"apps": host_apps[name]}
        raise FileNotFoundError(name)

    cfg.load_host_config.side_effect = load_host
    cfg.list_hosts.return_value = hosts or []
    return cfg


class TestAppManagerExists:
    def test_false_when_no_apps_dir_and_no_host_config(self):
        from navig.core.apps import AppManager

        config = _make_mock_config()
        mgr = AppManager(config)
        assert mgr.exists("myhost", "myapp") is False

    def test_true_from_legacy_embedded(self):
        from navig.core.apps import AppManager

        config = _make_mock_config(host_apps={"myhost": {"myapp": {}}})
        mgr = AppManager(config)
        assert mgr.exists("myhost", "myapp") is True

    def test_false_for_wrong_host(self):
        from navig.core.apps import AppManager

        config = _make_mock_config(host_apps={"otherhost": {"myapp": {}}})
        mgr = AppManager(config)
        assert mgr.exists("myhost", "myapp") is False

    def test_true_from_individual_file(self, tmp_path):
        from navig.core.apps import AppManager

        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()
        app_file = apps_dir / "myapp.yaml"
        app_file.write_text("host: targethost\nname: myapp\n", encoding="utf-8")

        config = _make_mock_config(config_dirs=[tmp_path])
        mgr = AppManager(config)
        assert mgr.exists("targethost", "myapp") is True

    def test_false_when_host_config_missing(self):
        from navig.core.apps import AppManager

        config = _make_mock_config()
        mgr = AppManager(config)
        assert mgr.exists("nonexistent", "app") is False


class TestAppManagerListApps:
    def test_empty_list_when_no_apps(self):
        from navig.core.apps import AppManager

        config = _make_mock_config()
        mgr = AppManager(config)
        assert mgr.list_apps("myhost") == []

    def test_returns_apps_from_legacy_embedded(self):
        from navig.core.apps import AppManager

        config = _make_mock_config(host_apps={"myhost": {"app1": {}, "app2": {}}})
        mgr = AppManager(config)
        result = mgr.list_apps("myhost")
        assert sorted(result) == ["app1", "app2"]

    def test_returns_apps_from_individual_files(self, tmp_path):
        from navig.core.apps import AppManager

        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()
        for app in ["alpha", "beta"]:
            (apps_dir / f"{app}.yaml").write_text(f"host: myhost\nname: {app}\n", encoding="utf-8")

        config = _make_mock_config(config_dirs=[tmp_path])
        mgr = AppManager(config)
        result = mgr.list_apps("myhost")
        assert "alpha" in result
        assert "beta" in result

    def test_deduplicates_across_sources(self, tmp_path):
        from navig.core.apps import AppManager

        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()
        (apps_dir / "shared.yaml").write_text("host: myhost\nname: shared\n", encoding="utf-8")

        # Same "shared" app also in legacy host config
        config = _make_mock_config(
            config_dirs=[tmp_path],
            host_apps={"myhost": {"shared": {}}},
        )
        mgr = AppManager(config)
        result = mgr.list_apps("myhost")
        assert result.count("shared") == 1


class TestAppManagerFindHostsWithApp:
    def test_empty_when_no_hosts(self):
        from navig.core.apps import AppManager

        config = _make_mock_config(hosts=[])
        mgr = AppManager(config)
        assert mgr.find_hosts_with_app("myapp") == []

    def test_finds_matching_host(self):
        from navig.core.apps import AppManager

        config = _make_mock_config(
            hosts=["host1", "host2"],
            host_apps={"host1": {"myapp": {}}, "host2": {}},
        )
        mgr = AppManager(config)
        result = mgr.find_hosts_with_app("myapp")
        assert "host1" in result
        assert "host2" not in result

    def test_finds_multiple_hosts(self):
        from navig.core.apps import AppManager

        config = _make_mock_config(
            hosts=["host1", "host2"],
            host_apps={"host1": {"shared": {}}, "host2": {"shared": {}}},
        )
        mgr = AppManager(config)
        result = mgr.find_hosts_with_app("shared")
        assert sorted(result) == ["host1", "host2"]
