"""
Tests for navig/core/scaffolder.py — Scaffolder class.
Batch 89.
"""
import tarfile
from pathlib import Path

import pytest
import yaml

from navig.core.scaffolder import Scaffolder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_template(tmp_path: Path, data: dict) -> Path:
    """Write a YAML template file and return its path."""
    p = tmp_path / "template.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


SIMPLE_TEMPLATE = {
    "meta": {"variables": {"project_name": "default_project"}},
    "structure": [
        {"type": "directory", "path": "src"},
        {"type": "file", "path": "README.md", "content": "# {{ project_name }}"},
    ],
}


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

class TestScaffolderInit:
    def test_jinja_env_set(self):
        s = Scaffolder()
        assert s.jinja_env is not None

    def test_jinja_autoescape_off(self):
        s = Scaffolder()
        assert s.jinja_env.autoescape is False

    def test_jinja_keep_trailing_newline(self):
        s = Scaffolder()
        assert s.jinja_env.keep_trailing_newline is True


# ---------------------------------------------------------------------------
# validate_template
# ---------------------------------------------------------------------------

class TestValidateTemplate:
    def test_valid_returns_dict(self, tmp_path):
        s = Scaffolder()
        p = write_template(tmp_path, SIMPLE_TEMPLATE)
        result = s.validate_template(p)
        assert isinstance(result, dict)

    def test_valid_returns_structure_key(self, tmp_path):
        s = Scaffolder()
        p = write_template(tmp_path, SIMPLE_TEMPLATE)
        result = s.validate_template(p)
        assert "structure" in result

    def test_missing_structure_raises(self, tmp_path):
        s = Scaffolder()
        p = write_template(tmp_path, {"meta": {}})
        with pytest.raises(ValueError, match="structure"):
            s.validate_template(p)

    def test_not_dict_raises(self, tmp_path):
        s = Scaffolder()
        p = tmp_path / "template.yaml"
        p.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="dictionary"):
            s.validate_template(p)

    def test_invalid_yaml_raises(self, tmp_path):
        s = Scaffolder()
        p = tmp_path / "template.yaml"
        p.write_text("key: [\n  broken yaml", encoding="utf-8")
        with pytest.raises(ValueError, match="YAML"):
            s.validate_template(p)

    def test_meta_variables_preserved(self, tmp_path):
        s = Scaffolder()
        p = write_template(tmp_path, SIMPLE_TEMPLATE)
        result = s.validate_template(p)
        assert result["meta"]["variables"]["project_name"] == "default_project"


# ---------------------------------------------------------------------------
# generate — basic structure creation
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_creates_directory(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "structure": [{"type": "directory", "path": "mydir"}],
        }
        s.generate(data, target)
        assert (target / "mydir").is_dir()

    def test_creates_file(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "structure": [{"type": "file", "path": "hello.txt", "content": "hi"}],
        }
        s.generate(data, target)
        assert (target / "hello.txt").is_file()

    def test_file_content(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "structure": [{"type": "file", "path": "hello.txt", "content": "world"}],
        }
        s.generate(data, target)
        assert (target / "hello.txt").read_text() == "world"

    def test_jinja_variable_substitution_in_content(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "structure": [
                {"type": "file", "path": "readme.md", "content": "# {{ name }}"}
            ],
        }
        s.generate(data, target, {"name": "MyApp"})
        assert (target / "readme.md").read_text() == "# MyApp"

    def test_jinja_variable_substitution_in_path(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "structure": [{"type": "directory", "path": "{{ app_name }}"}],
        }
        s.generate(data, target, {"app_name": "coolapp"})
        assert (target / "coolapp").is_dir()

    def test_meta_variables_used_as_defaults(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "meta": {"variables": {"greeting": "Hello"}},
            "structure": [
                {"type": "file", "path": "g.txt", "content": "{{ greeting }}"}
            ],
        }
        s.generate(data, target)
        assert (target / "g.txt").read_text() == "Hello"

    def test_user_variables_override_meta_defaults(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "meta": {"variables": {"greeting": "Hello"}},
            "structure": [
                {"type": "file", "path": "g.txt", "content": "{{ greeting }}"}
            ],
        }
        s.generate(data, target, {"greeting": "Howdy"})
        assert (target / "g.txt").read_text() == "Howdy"

    def test_empty_structure_no_crash(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        s.generate({"structure": []}, target)

    def test_nested_directory_and_file(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "structure": [
                {
                    "type": "directory",
                    "path": "src",
                    "children": [
                        {"type": "file", "path": "main.py", "content": "# main"}
                    ],
                }
            ],
        }
        s.generate(data, target)
        assert (target / "src" / "main.py").is_file()
        assert (target / "src" / "main.py").read_text() == "# main"

    def test_file_with_empty_content(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "structure": [{"type": "file", "path": "empty.txt", "content": ""}],
        }
        s.generate(data, target)
        assert (target / "empty.txt").read_text() == ""

    def test_item_with_blank_path_skipped(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "structure": [
                {"type": "file", "path": "", "content": "oops"},
                {"type": "file", "path": "valid.txt", "content": "ok"},
            ],
        }
        s.generate(data, target)
        assert (target / "valid.txt").is_file()


# ---------------------------------------------------------------------------
# _check_condition
# ---------------------------------------------------------------------------

class TestCheckCondition:
    def setup_method(self):
        self.s = Scaffolder()

    def test_no_condition_returns_true(self):
        assert self.s._check_condition({}, {}) is True

    def test_condition_true_literal(self):
        assert self.s._check_condition({"condition": "true"}, {}) is True

    def test_condition_false_literal(self):
        assert self.s._check_condition({"condition": "false"}, {}) is False

    def test_condition_variable_true(self):
        assert self.s._check_condition({"condition": "{{ flag }}"}, {"flag": "true"}) is True

    def test_condition_variable_false(self):
        assert self.s._check_condition({"condition": "{{ flag }}"}, {"flag": "false"}) is False

    def test_condition_yes_as_variable(self):
        # "yes" without {{ }} becomes {{ yes }}, which is undefined → empty → False
        assert self.s._check_condition({"condition": "yes"}, {}) is False

    def test_condition_yes_as_jinja_var(self):
        # When yes is set via a variable named 'yes' it should render as 'yes'
        assert self.s._check_condition({"condition": "{{ yes }}"}, {"yes": "yes"}) is True

    def test_condition_1(self):
        assert self.s._check_condition({"condition": "1"}, {}) is True

    def test_condition_on_as_variable(self):
        # "on" without {{ }} becomes {{ on }}, which is undefined → empty → False
        assert self.s._check_condition({"condition": "on"}, {}) is False

    def test_condition_no(self):
        assert self.s._check_condition({"condition": "no"}, {}) is False

    def test_condition_none_is_true(self):
        assert self.s._check_condition({"condition": None}, {}) is True

    def test_invalid_condition_returns_false(self):
        # A condition with undeclared variable logs warning and returns False
        assert self.s._check_condition({"condition": "{{ undefined_var }}"}, {}) is False


# ---------------------------------------------------------------------------
# Conditional items in generate
# ---------------------------------------------------------------------------

class TestGenerateWithCondition:
    def test_condition_false_skips_file(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "structure": [
                {
                    "type": "file",
                    "path": "secret.txt",
                    "content": "secret",
                    "condition": "false",
                }
            ],
        }
        s.generate(data, target)
        assert not (target / "secret.txt").exists()

    def test_condition_true_includes_file(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "structure": [
                {
                    "type": "file",
                    "path": "visible.txt",
                    "content": "here",
                    "condition": "true",
                }
            ],
        }
        s.generate(data, target)
        assert (target / "visible.txt").is_file()

    def test_condition_uses_variable(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "structure": [
                {
                    "type": "file",
                    "path": "optional.txt",
                    "content": "opt",
                    "condition": "{{ include_optional }}",
                }
            ],
        }
        s.generate(data, target, {"include_optional": "true"})
        assert (target / "optional.txt").is_file()

    def test_condition_false_variable_skips(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "structure": [
                {
                    "type": "file",
                    "path": "optional.txt",
                    "content": "opt",
                    "condition": "{{ include_optional }}",
                }
            ],
        }
        s.generate(data, target, {"include_optional": "false"})
        assert not (target / "optional.txt").exists()


# ---------------------------------------------------------------------------
# source raises NotImplementedError
# ---------------------------------------------------------------------------

class TestSourceNotImplemented:
    def test_source_raises(self, tmp_path):
        s = Scaffolder()
        target = tmp_path / "out"
        target.mkdir()
        data = {
            "structure": [
                {"type": "file", "path": "f.txt", "source": "some_file.txt"}
            ],
        }
        with pytest.raises(NotImplementedError):
            s.generate(data, target)


# ---------------------------------------------------------------------------
# generate_to_temp_archive
# ---------------------------------------------------------------------------

class TestGenerateToTempArchive:
    def test_returns_path(self, tmp_path):
        s = Scaffolder()
        data = {
            "structure": [{"type": "file", "path": "hello.txt", "content": "hi"}],
        }
        result = s.generate_to_temp_archive(data)
        assert isinstance(result, Path)

    def test_archive_is_tar_gz(self, tmp_path):
        s = Scaffolder()
        data = {
            "structure": [{"type": "file", "path": "hello.txt", "content": "hi"}],
        }
        result = s.generate_to_temp_archive(data)
        assert result.suffix == ".gz"
        assert tarfile.is_tarfile(result)

    def test_archive_contains_file(self, tmp_path):
        s = Scaffolder()
        data = {
            "structure": [{"type": "file", "path": "hello.txt", "content": "world"}],
        }
        result = s.generate_to_temp_archive(data)
        with tarfile.open(result, "r:gz") as tar:
            names = tar.getnames()
        assert "hello.txt" in names

    def test_archive_file_content(self, tmp_path):
        s = Scaffolder()
        data = {
            "structure": [{"type": "file", "path": "msg.txt", "content": "payload"}],
        }
        result = s.generate_to_temp_archive(data)
        with tarfile.open(result, "r:gz") as tar:
            f = tar.extractfile("msg.txt")
            content = f.read().decode()
        assert content == "payload"

    def test_archive_with_variables(self, tmp_path):
        s = Scaffolder()
        data = {
            "structure": [
                {"type": "file", "path": "name.txt", "content": "{{ who }}"}
            ],
        }
        result = s.generate_to_temp_archive(data, {"who": "navig"})
        with tarfile.open(result, "r:gz") as tar:
            f = tar.extractfile("name.txt")
            content = f.read().decode()
        assert content == "navig"

    def test_archive_does_not_include_temp_root(self, tmp_path):
        s = Scaffolder()
        data = {
            "structure": [{"type": "file", "path": "f.txt", "content": "x"}],
        }
        result = s.generate_to_temp_archive(data)
        with tarfile.open(result, "r:gz") as tar:
            names = tar.getnames()
        # Root should not be a temp directory name with many segments
        for name in names:
            assert "/" not in name or name.startswith("f.txt")

    def test_archive_directory_in_tar(self, tmp_path):
        s = Scaffolder()
        data = {
            "structure": [
                {
                    "type": "directory",
                    "path": "subdir",
                    "children": [
                        {"type": "file", "path": "file.txt", "content": "in subdir"}
                    ],
                }
            ],
        }
        result = s.generate_to_temp_archive(data)
        with tarfile.open(result, "r:gz") as tar:
            names = tar.getnames()
        assert any("subdir" in n for n in names)
