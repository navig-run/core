"""
Tests for navig/template_manager.py

Covers TemplateSchema.validate(), Template (with temp dir), and
TemplateManager.discover_templates() / list_templates().
All tests are hermetic — no real filesystem outside of tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from navig.template_manager import Template, TemplateManager, TemplateSchema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_META = {
    "name": "test-template",
    "version": "1.0.0",
    "description": "A test template",
    "author": "tester",
}


def _make_template_dir(tmp_path: Path, name: str, meta: dict, fmt: str = "yaml") -> Path:
    """Create a minimal template directory under tmp_path."""
    tdir = tmp_path / name
    tdir.mkdir()
    meta_file = tdir / (f"template.{fmt}")
    if fmt == "yaml":
        meta_file.write_text(yaml.dump(meta), encoding="utf-8")
    else:
        meta_file.write_text(json.dumps(meta), encoding="utf-8")
    return tdir


# ---------------------------------------------------------------------------
# TemplateSchema.validate()
# ---------------------------------------------------------------------------


class TestTemplateSchemaValidate:
    def test_valid_minimal_metadata_passes(self):
        ok, err = TemplateSchema.validate(_VALID_META)
        assert ok is True
        assert err is None

    def test_missing_name_fails(self):
        m = {k: v for k, v in _VALID_META.items() if k != "name"}
        ok, err = TemplateSchema.validate(m)
        assert ok is False
        assert "name" in err

    def test_missing_version_fails(self):
        m = {k: v for k, v in _VALID_META.items() if k != "version"}
        ok, err = TemplateSchema.validate(m)
        assert ok is False
        assert "version" in err

    def test_missing_description_fails(self):
        m = {k: v for k, v in _VALID_META.items() if k != "description"}
        ok, err = TemplateSchema.validate(m)
        assert ok is False
        assert "description" in err

    def test_missing_author_fails(self):
        m = {k: v for k, v in _VALID_META.items() if k != "author"}
        ok, err = TemplateSchema.validate(m)
        assert ok is False
        assert "author" in err

    def test_empty_version_string_fails(self):
        m = {**_VALID_META, "version": ""}
        ok, err = TemplateSchema.validate(m)
        assert ok is False

    def test_non_string_version_fails(self):
        m = {**_VALID_META, "version": 123}
        ok, err = TemplateSchema.validate(m)
        assert ok is False

    def test_dependencies_as_list_passes(self):
        m = {**_VALID_META, "dependencies": ["dep-a"]}
        ok, err = TemplateSchema.validate(m)
        assert ok is True

    def test_dependencies_as_non_list_fails(self):
        m = {**_VALID_META, "dependencies": "dep-a"}
        ok, err = TemplateSchema.validate(m)
        assert ok is False
        assert "list" in err.lower()

    def test_enabled_true_passes(self):
        m = {**_VALID_META, "enabled": True}
        ok, err = TemplateSchema.validate(m)
        assert ok is True

    def test_enabled_false_passes(self):
        m = {**_VALID_META, "enabled": False}
        ok, err = TemplateSchema.validate(m)
        assert ok is True

    def test_enabled_string_fails(self):
        m = {**_VALID_META, "enabled": "yes"}
        ok, err = TemplateSchema.validate(m)
        assert ok is False

    def test_optional_fields_ignored(self):
        m = {**_VALID_META, "paths": {"/app": "/var/www"}, "services": {}}
        ok, err = TemplateSchema.validate(m)
        assert ok is True


# ---------------------------------------------------------------------------
# Template class
# ---------------------------------------------------------------------------


class TestTemplate:
    def test_loads_yaml_metadata(self, tmp_path):
        tdir = _make_template_dir(tmp_path, "alpha", _VALID_META)
        t = Template(tdir)
        assert t.name == "alpha"
        assert t.metadata["name"] == "test-template"
        assert t.metadata_format == "yaml"

    def test_loads_json_metadata(self, tmp_path):
        tdir = _make_template_dir(tmp_path, "beta", _VALID_META, fmt="json")
        t = Template(tdir)
        assert t.metadata_format == "json"
        assert t.metadata["version"] == "1.0.0"

    def test_prefers_yaml_over_json(self, tmp_path):
        tdir = tmp_path / "both"
        tdir.mkdir()
        yaml_meta = {**_VALID_META, "name": "yaml-version"}
        json_meta = {**_VALID_META, "name": "json-version"}
        (tdir / "template.yaml").write_text(yaml.dump(yaml_meta), encoding="utf-8")
        (tdir / "template.json").write_text(json.dumps(json_meta), encoding="utf-8")
        t = Template(tdir)
        assert t.metadata_format == "yaml"
        assert t.metadata["name"] == "yaml-version"

    def test_missing_metadata_file_raises(self, tmp_path):
        tdir = tmp_path / "empty"
        tdir.mkdir()
        with pytest.raises(FileNotFoundError):
            Template(tdir)

    def test_invalid_metadata_raises_value_error(self, tmp_path):
        bad = {"name": "x", "version": "1.0"}  # missing description and author
        tdir = _make_template_dir(tmp_path, "bad", bad)
        with pytest.raises(ValueError):
            Template(tdir)

    def test_is_enabled_false_by_default(self, tmp_path):
        tdir = _make_template_dir(tmp_path, "gamma", _VALID_META)
        t = Template(tdir)
        assert t.is_enabled() is False

    def test_is_enabled_true_when_set(self, tmp_path):
        m = {**_VALID_META, "enabled": True}
        tdir = _make_template_dir(tmp_path, "delta", m)
        t = Template(tdir)
        assert t.is_enabled() is True

    def test_get_paths_empty_default(self, tmp_path):
        tdir = _make_template_dir(tmp_path, "epsilon", _VALID_META)
        t = Template(tdir)
        assert t.get_paths() == {}

    def test_get_paths_returns_dict(self, tmp_path):
        m = {**_VALID_META, "paths": {"/app": "/var/www/html"}}
        tdir = _make_template_dir(tmp_path, "zeta", m)
        t = Template(tdir)
        assert t.get_paths() == {"/app": "/var/www/html"}

    def test_get_services_empty_default(self, tmp_path):
        tdir = _make_template_dir(tmp_path, "eta", _VALID_META)
        t = Template(tdir)
        assert t.get_services() == {}

    def test_get_commands_empty_default(self, tmp_path):
        tdir = _make_template_dir(tmp_path, "theta", _VALID_META)
        t = Template(tdir)
        assert t.get_commands() == []

    def test_get_env_vars_empty_default(self, tmp_path):
        tdir = _make_template_dir(tmp_path, "iota", _VALID_META)
        t = Template(tdir)
        assert t.get_env_vars() == {}

    def test_check_dependencies_no_deps(self, tmp_path):
        tdir = _make_template_dir(tmp_path, "kappa", _VALID_META)
        t = Template(tdir)
        met, missing = t.check_dependencies(["other"])
        assert met is True
        assert missing == []

    def test_check_dependencies_met(self, tmp_path):
        m = {**_VALID_META, "dependencies": ["dep-a"]}
        tdir = _make_template_dir(tmp_path, "lambda", m)
        t = Template(tdir)
        met, missing = t.check_dependencies(["dep-a", "dep-b"])
        assert met is True
        assert missing == []

    def test_check_dependencies_missing(self, tmp_path):
        m = {**_VALID_META, "dependencies": ["dep-a", "dep-b"]}
        tdir = _make_template_dir(tmp_path, "mu", m)
        t = Template(tdir)
        met, missing = t.check_dependencies(["dep-a"])
        assert met is False
        assert "dep-b" in missing

    def test_register_and_call_hook(self, tmp_path):
        tdir = _make_template_dir(tmp_path, "nu", _VALID_META)
        t = Template(tdir)
        called = []
        t.register_hook("onLoad", lambda tmpl: called.append(tmpl.name))
        t.load()
        assert "nu" in called

    def test_load_idempotent(self, tmp_path):
        tdir = _make_template_dir(tmp_path, "xi", _VALID_META)
        t = Template(tdir)
        called = []
        t.register_hook("onLoad", lambda _: called.append(1))
        t.load()
        t.load()  # second call is no-op
        assert len(called) == 1

    def test_enable_sets_enabled(self, tmp_path):
        tdir = _make_template_dir(tmp_path, "omicron", _VALID_META)
        t = Template(tdir)
        assert not t.is_enabled()
        t.enable()
        assert t.is_enabled()

    def test_disable_clears_enabled(self, tmp_path):
        m = {**_VALID_META, "enabled": True}
        tdir = _make_template_dir(tmp_path, "pi", m)
        t = Template(tdir)
        assert t.is_enabled()
        t.disable()
        assert not t.is_enabled()


# ---------------------------------------------------------------------------
# TemplateManager
# ---------------------------------------------------------------------------


class TestTemplateManager:
    def test_empty_directory_discovers_nothing(self, tmp_path):
        tm = TemplateManager(templates_dir=tmp_path)
        result = tm.discover_templates()
        assert result == []

    def test_discovers_yaml_template(self, tmp_path):
        _make_template_dir(tmp_path, "t1", _VALID_META)
        tm = TemplateManager(templates_dir=tmp_path)
        result = tm.discover_templates()
        assert "t1" in result

    def test_discovers_json_template(self, tmp_path):
        _make_template_dir(tmp_path, "t2", _VALID_META, fmt="json")
        tm = TemplateManager(templates_dir=tmp_path)
        result = tm.discover_templates()
        assert "t2" in result

    def test_skips_dir_without_metadata(self, tmp_path):
        no_meta = tmp_path / "no_meta"
        no_meta.mkdir()
        tm = TemplateManager(templates_dir=tmp_path)
        result = tm.discover_templates()
        assert "no_meta" not in result

    def test_get_template_returns_none_for_unknown(self, tmp_path):
        tm = TemplateManager(templates_dir=tmp_path)
        tm.discover_templates()
        assert tm.get_template("__missing__") is None

    def test_get_template_returns_template(self, tmp_path):
        _make_template_dir(tmp_path, "fa", _VALID_META)
        tm = TemplateManager(templates_dir=tmp_path)
        tm.discover_templates()
        t = tm.get_template("fa")
        assert t is not None
        assert t.name == "fa"

    def test_list_templates_all(self, tmp_path):
        _make_template_dir(tmp_path, "p1", _VALID_META)
        _make_template_dir(tmp_path, "p2", {**_VALID_META, "enabled": True})
        tm = TemplateManager(templates_dir=tmp_path)
        tm.discover_templates()
        result = tm.list_templates()
        assert len(result) == 2

    def test_list_templates_enabled_only(self, tmp_path):
        _make_template_dir(tmp_path, "q1", _VALID_META)
        _make_template_dir(tmp_path, "q2", {**_VALID_META, "enabled": True})
        tm = TemplateManager(templates_dir=tmp_path)
        tm.discover_templates()
        result = tm.list_templates(enabled_only=True)
        names = [t.name for t in result]
        assert "q2" in names
        assert "q1" not in names

    def test_list_templates_sorted_by_name(self, tmp_path):
        _make_template_dir(tmp_path, "zzz", {**_VALID_META, "name": "zzz"})
        _make_template_dir(tmp_path, "aaa", {**_VALID_META, "name": "aaa"})
        tm = TemplateManager(templates_dir=tmp_path)
        tm.discover_templates()
        names = [t.metadata["name"] for t in tm.list_templates()]
        assert names == sorted(names)

    def test_enable_template_not_found(self, tmp_path):
        tm = TemplateManager(templates_dir=tmp_path)
        result = tm.enable_template("ghost")
        assert result is False

    def test_enable_template_already_enabled(self, tmp_path):
        _make_template_dir(tmp_path, "already_on", {**_VALID_META, "enabled": True})
        tm = TemplateManager(templates_dir=tmp_path)
        tm.discover_templates()
        result = tm.enable_template("already_on")
        assert result is True  # returns True idempotently

    def test_enable_template_missing_deps(self, tmp_path):
        m = {**_VALID_META, "dependencies": ["missing-dep"]}
        _make_template_dir(tmp_path, "needs_dep", m)
        tm = TemplateManager(templates_dir=tmp_path)
        tm.discover_templates()
        result = tm.enable_template("needs_dep")
        assert result is False

    def test_apply_template_config_adds_paths(self, tmp_path):
        m = {**_VALID_META, "enabled": True, "paths": {"/app": "/var/www"}}
        _make_template_dir(tmp_path, "with_paths", m)
        tm = TemplateManager(templates_dir=tmp_path)
        tm.discover_templates()
        server_cfg: dict = {}
        result = tm.apply_template_config(server_cfg)
        assert result.get("paths", {}).get("/app") == "/var/www"

    def test_apply_template_config_no_enabled_templates(self, tmp_path):
        _make_template_dir(tmp_path, "disabled", _VALID_META)
        tm = TemplateManager(templates_dir=tmp_path)
        tm.discover_templates()
        result = tm.apply_template_config({})
        assert result == {}

    def test_validate_all_templates_returns_dict(self, tmp_path):
        _make_template_dir(tmp_path, "v1", _VALID_META)
        tm = TemplateManager(templates_dir=tmp_path)
        tm.discover_templates()
        results = tm.validate_all_templates()
        assert isinstance(results, dict)
        assert results.get("v1") is True

    def test_get_template_commands_empty_when_none_enabled(self, tmp_path):
        _make_template_dir(tmp_path, "no_cmds", _VALID_META)
        tm = TemplateManager(templates_dir=tmp_path)
        tm.discover_templates()
        assert tm.get_template_commands() == []
