"""Tests for selfheal/__init__.py (ContributeConfig) and selfheal/scanner.py."""
from __future__ import annotations

from pathlib import Path

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# selfheal/__init__.py — ContributeConfig
# ──────────────────────────────────────────────────────────────────────────────
from navig.selfheal import ContributeConfig


class TestContributeConfigDefaults:
    def test_defaults(self):
        c = ContributeConfig()
        assert c.enabled is False
        assert c.alias == ""
        assert c.min_confidence == pytest.approx(0.80)
        assert c.github_token_env == "NAVIG_GITHUB_TOKEN"
        assert c.upstream_repo == "navig-run/core"
        assert c.clone_path == ""

    def test_from_dict_empty(self):
        c = ContributeConfig.from_dict({})
        assert c.enabled is False
        assert c.min_confidence == pytest.approx(0.80)

    def test_from_dict_enabled(self):
        c = ContributeConfig.from_dict({"enabled": True, "alias": "Alice"})
        assert c.enabled is True
        assert c.alias == "Alice"

    def test_from_dict_custom_confidence(self):
        c = ContributeConfig.from_dict({"min_confidence": 0.95})
        assert c.min_confidence == pytest.approx(0.95)

    def test_from_dict_contributor_alias_fallback(self):
        # legacy key
        c = ContributeConfig.from_dict({"contributor_alias": "Bob"})
        assert c.alias == "Bob"

    def test_from_dict_alias_takes_priority(self):
        c = ContributeConfig.from_dict({"alias": "Eve", "contributor_alias": "Bob"})
        assert c.alias == "Eve"

    def test_from_dict_unknown_keys_ignored(self):
        c = ContributeConfig.from_dict({"unknown_key": "xyz"})
        assert c.enabled is False

    def test_to_dict_keys(self):
        d = ContributeConfig().to_dict()
        for key in ("enabled", "alias", "min_confidence", "github_token_env", "upstream_repo", "clone_path"):
            assert key in d

    def test_to_dict_roundtrip(self):
        original = ContributeConfig(enabled=True, alias="Dev", min_confidence=0.9)
        restored = ContributeConfig.from_dict(original.to_dict())
        assert restored.enabled is True
        assert restored.alias == "Dev"
        assert restored.min_confidence == pytest.approx(0.9)

    def test_to_dict_values_serializable(self):
        import json

        d = ContributeConfig(enabled=True, alias="x").to_dict()
        json.dumps(d)  # should not raise

    def test_custom_upstream_repo(self):
        c = ContributeConfig.from_dict({"upstream_repo": "myorg/myrepo"})
        assert c.upstream_repo == "myorg/myrepo"


# ──────────────────────────────────────────────────────────────────────────────
# selfheal/scanner.py — ScanFinding, _is_sensitive_path, _collect_py_files
# ──────────────────────────────────────────────────────────────────────────────
from navig.selfheal.scanner import ScanFinding, _collect_py_files, _is_sensitive_path


class TestScanFinding:
    def _make(self, **kwargs):
        defaults = dict(
            file="navig/core.py",
            line=10,
            severity="medium",
            category="readability",
            description="Missing docstring",
            suggested_fix='Add """..."""',
            confidence=0.9,
        )
        defaults.update(kwargs)
        return ScanFinding(**defaults)

    def test_creation(self):
        f = self._make()
        assert f.file == "navig/core.py"
        assert f.severity == "medium"

    def test_confidence_clamped_above_1(self):
        f = self._make(confidence=1.5)
        assert f.confidence == pytest.approx(1.0)

    def test_confidence_clamped_below_0(self):
        f = self._make(confidence=-0.5)
        assert f.confidence == pytest.approx(0.0)

    def test_confidence_string_coerced(self):
        f = self._make(confidence="0.75")
        assert f.confidence == pytest.approx(0.75)

    def test_confidence_invalid_becomes_zero(self):
        f = self._make(confidence="not-a-float")
        assert f.confidence == pytest.approx(0.0)

    def test_line_coerced_from_string(self):
        f = self._make(line="42")
        assert f.line == 42

    def test_line_coerced_minimum_1(self):
        f = self._make(line=-5)
        assert f.line >= 1

    def test_line_invalid_defaults_to_1(self):
        f = self._make(line="invalid")
        assert f.line == 1


class TestIsSensitivePath:
    def test_vault_directory_is_sensitive(self):
        assert _is_sensitive_path(Path("navig/vault/store.py"))

    def test_secret_in_name_is_sensitive(self):
        assert _is_sensitive_path(Path("utils/secret_manager.py"))

    def test_token_in_name_is_sensitive(self):
        assert _is_sensitive_path(Path("auth/token.py"))

    def test_key_in_name_is_sensitive(self):
        assert _is_sensitive_path(Path("crypto/api.key"))

    def test_env_file_is_sensitive(self):
        assert _is_sensitive_path(Path(".env"))

    def test_password_file_is_sensitive(self):
        assert _is_sensitive_path(Path("utils/password_check.py"))

    def test_normal_file_is_not_sensitive(self):
        assert not _is_sensitive_path(Path("navig/commands/run.py"))

    def test_normal_utils_not_sensitive(self):
        assert not _is_sensitive_path(Path("navig/core/helpers.py"))

    def test_vault_as_dir_is_sensitive(self):
        assert _is_sensitive_path(Path("navig") / "vault" / "core.py")

    def test_case_insensitive_secret(self):
        assert _is_sensitive_path(Path("utils/SECRET_key.py"))


class TestCollectPyFiles:
    def test_empty_dir(self, tmp_path):
        result = _collect_py_files(tmp_path)
        assert result == []

    def test_finds_py_files(self, tmp_path):
        (tmp_path / "a.py").write_text("# code", encoding="utf-8")
        (tmp_path / "b.py").write_text("# code", encoding="utf-8")
        result = _collect_py_files(tmp_path)
        assert len(result) == 2

    def test_ignores_non_py_files(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello", encoding="utf-8")
        (tmp_path / "data.yaml").write_text("key: val", encoding="utf-8")
        result = _collect_py_files(tmp_path)
        assert result == []

    def test_excludes_sensitive_paths(self, tmp_path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "store.py").write_text("# vault code", encoding="utf-8")
        result = _collect_py_files(tmp_path)
        assert result == []

    def test_respects_max_files(self, tmp_path):
        for i in range(10):
            (tmp_path / f"module_{i}.py").write_text("# x", encoding="utf-8")
        result = _collect_py_files(tmp_path, max_files=3)
        assert len(result) <= 3

    def test_returns_sorted_list(self, tmp_path):
        names = ["z.py", "a.py", "m.py"]
        for n in names:
            (tmp_path / n).write_text("# x", encoding="utf-8")
        result = _collect_py_files(tmp_path)
        paths = [r.name for r in result]
        assert paths == sorted(paths)
