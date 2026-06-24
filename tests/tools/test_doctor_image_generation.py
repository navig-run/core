"""Batch 119: tests for navig/commands/doctor.py and navig/tools/image_generation.py."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ===========================================================================
# doctor.py — _check helper
# ===========================================================================

class TestDoctorCheck:
    def test_ok_result(self):
        from navig.commands.doctor import _check

        icon, ok, line = _check("Config", True, "all good")
        assert ok is True
        assert "Config" in line
        assert "all good" in line

    def test_error_result(self):
        from navig.commands.doctor import _check

        icon, ok, line = _check("Config", False, "not found")
        assert ok is False
        assert "Config" in line

    def test_warn_result(self):
        from navig.commands.doctor import _check

        icon, ok, line = _check("Cache", False, "missing", warn=True)
        assert ok is False
        # warn icon should differ from error icon

    def test_no_detail(self):
        from navig.commands.doctor import _check

        icon, ok, line = _check("Label", True)
        assert ok is True
        assert "Label" in line

    def test_returns_three_tuple(self):
        from navig.commands.doctor import _check

        result = _check("X", True)
        assert len(result) == 3


# ===========================================================================
# doctor.py — _gateway_reachable
# ===========================================================================

class TestGatewayReachable:
    def test_reachable_returns_true(self):
        from navig.commands.doctor import _gateway_reachable

        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=None)
        mock_cm.__exit__ = MagicMock(return_value=False)

        with patch("socket.create_connection", return_value=mock_cm):
            result = _gateway_reachable("127.0.0.1", 8789)

        assert result is True

    def test_os_error_returns_false(self):
        from navig.commands.doctor import _gateway_reachable

        with patch("socket.create_connection", side_effect=OSError("refused")):
            result = _gateway_reachable("127.0.0.1", 9999)

        assert result is False

    def test_timeout_returns_false(self):
        from navig.commands.doctor import _gateway_reachable

        with patch("socket.create_connection", side_effect=TimeoutError("timeout")):
            result = _gateway_reachable("127.0.0.1", 9999, timeout=0.1)

        assert result is False


# ===========================================================================
# doctor.py — _count_yaml_files
# ===========================================================================

class TestCountYamlFiles:
    def test_empty_dir(self, tmp_path):
        from navig.commands.doctor import _count_yaml_files

        total, errors = _count_yaml_files(tmp_path)
        assert total == 0
        assert errors == 0

    def test_nonexistent_dir(self, tmp_path):
        from navig.commands.doctor import _count_yaml_files

        total, errors = _count_yaml_files(tmp_path / "nope")
        assert total == 0
        assert errors == 0

    def test_valid_yaml(self, tmp_path):
        from navig.commands.doctor import _count_yaml_files

        (tmp_path / "a.yaml").write_text("key: value\n", encoding="utf-8")
        (tmp_path / "b.yml").write_text("x: 1\n", encoding="utf-8")

        total, errors = _count_yaml_files(tmp_path)
        assert total == 2
        assert errors == 0

    def test_invalid_yaml_counted_as_error(self, tmp_path):
        from navig.commands.doctor import _count_yaml_files

        (tmp_path / "bad.yaml").write_text(":\n  - {broken", encoding="utf-8")
        (tmp_path / "ok.yaml").write_text("good: true\n", encoding="utf-8")

        total, errors = _count_yaml_files(tmp_path)
        assert total == 2
        assert errors == 1

    def test_nested_yaml(self, tmp_path):
        from navig.commands.doctor import _count_yaml_files

        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.yaml").write_text("nested: yes\n", encoding="utf-8")

        total, errors = _count_yaml_files(tmp_path)
        assert total == 1
        assert errors == 0


# ===========================================================================
# doctor.py — _find_browser_agent
# ===========================================================================

class TestFindBrowserAgent:
    def test_returns_none_when_not_found(self):
        from navig.commands.doctor import _find_browser_agent

        with patch("shutil.which", return_value=None):
            # Also ensure none of the candidate paths exist
            with patch("pathlib.Path.exists", return_value=False):
                result = _find_browser_agent()

        assert result is None

    def test_returns_path_when_found_in_path(self):
        from navig.commands.doctor import _find_browser_agent

        with patch("shutil.which", return_value="/usr/bin/navig-browser-agent"):
            with patch("pathlib.Path.exists", return_value=False):
                result = _find_browser_agent()

        assert result is not None
        assert "navig-browser-agent" in str(result)

    def test_returns_path_from_candidate(self, tmp_path):
        from navig.commands.doctor import _find_browser_agent
        import sys

        agent_path = tmp_path / "bin" / "navig-browser-agent"
        agent_path.parent.mkdir(parents=True, exist_ok=True)
        agent_path.write_text("#!/bin/sh\n", encoding="utf-8")

        with patch("sys.prefix", str(tmp_path)):
            with patch("shutil.which", return_value=None):
                result = _find_browser_agent()

        assert result == agent_path


# ===========================================================================
# doctor.py — check_config
# ===========================================================================

class TestCheckConfig:
    def test_config_not_found(self, tmp_path):
        from navig.commands.doctor import check_config

        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            results = check_config()

        assert len(results) == 1
        _, ok, line = results[0]
        assert ok is False
        assert "not found" in line.lower() or "config" in line.lower()

    def test_valid_config(self, tmp_path):
        from navig.commands.doctor import check_config

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("version: 2\nhost: local\n", encoding="utf-8")

        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            results = check_config()

        assert len(results) == 1
        _, ok, _ = results[0]
        assert ok is True

    def test_invalid_yaml_config(self, tmp_path):
        from navig.commands.doctor import check_config

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(": broken: {[", encoding="utf-8")

        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            results = check_config()

        _, ok, _ = results[0]
        assert ok is False


# ===========================================================================
# doctor.py — check_cache_dir
# ===========================================================================

class TestCheckCacheDir:
    def test_cache_dir_not_exists(self, tmp_path):
        from navig.commands.doctor import check_cache_dir

        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            results = check_cache_dir()

        assert len(results) == 1
        _, ok, _ = results[0]
        assert ok is False  # warn: does not exist

    def test_writable_cache(self, tmp_path):
        from navig.commands.doctor import check_cache_dir

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            results = check_cache_dir()

        _, ok, _ = results[0]
        assert ok is True

    def test_unwritable_cache(self, tmp_path):
        from navig.commands.doctor import check_cache_dir
        import sys

        if sys.platform == "win32":
            pytest.skip("chmod not reliable on Windows")

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache_dir.chmod(0o444)  # read-only

        try:
            with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
                results = check_cache_dir()
            _, ok, _ = results[0]
            assert ok is False
        finally:
            cache_dir.chmod(0o755)


# ===========================================================================
# image_generation.py — Enums
# ===========================================================================

class TestImageEnums:
    def test_image_provider_values(self):
        from navig.tools.image_generation import ImageProvider

        assert ImageProvider.OPENAI.value == "openai"
        assert ImageProvider.STABILITY.value == "stability"
        assert ImageProvider.LOCAL.value == "local"

    def test_image_size_values(self):
        from navig.tools.image_generation import ImageSize

        assert ImageSize.SQUARE_LARGE.value == "1024x1024"
        assert ImageSize.LANDSCAPE.value == "1792x1024"
        assert ImageSize.PORTRAIT.value == "1024x1792"

    def test_image_quality_values(self):
        from navig.tools.image_generation import ImageQuality

        assert ImageQuality.STANDARD.value == "standard"
        assert ImageQuality.HD.value == "hd"

    def test_image_style_values(self):
        from navig.tools.image_generation import ImageStyle

        assert ImageStyle.VIVID.value == "vivid"
        assert ImageStyle.NATURAL.value == "natural"


# ===========================================================================
# image_generation.py — ImageGenerationConfig
# ===========================================================================

class TestImageGenerationConfig:
    def test_defaults(self):
        from navig.tools.image_generation import ImageGenerationConfig, ImageProvider, ImageSize, ImageQuality, ImageStyle

        cfg = ImageGenerationConfig()
        assert cfg.provider == ImageProvider.OPENAI
        assert cfg.default_size == ImageSize.SQUARE_LARGE
        assert cfg.default_quality == ImageQuality.STANDARD
        assert cfg.default_style == ImageStyle.VIVID
        assert cfg.save_locally is True
        assert cfg.max_concurrent == 2

    def test_from_env_defaults(self, monkeypatch):
        from navig.tools.image_generation import ImageGenerationConfig, ImageProvider

        monkeypatch.delenv("IMAGE_PROVIDER", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("STABILITY_API_KEY", raising=False)
        monkeypatch.delenv("LOCAL_IMAGE_API_URL", raising=False)

        cfg = ImageGenerationConfig.from_env()
        assert cfg.provider == ImageProvider.OPENAI
        assert cfg.openai_api_key is None

    def test_from_env_with_values(self, monkeypatch):
        from navig.tools.image_generation import ImageGenerationConfig, ImageProvider

        monkeypatch.setenv("IMAGE_PROVIDER", "stability")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        monkeypatch.setenv("STABILITY_API_KEY", "stab-key")

        cfg = ImageGenerationConfig.from_env()
        assert cfg.provider == ImageProvider.STABILITY
        assert cfg.openai_api_key == "sk-test-123"
        assert cfg.stability_api_key == "stab-key"

    def test_from_dict_minimal(self):
        from navig.tools.image_generation import ImageGenerationConfig, ImageProvider

        cfg = ImageGenerationConfig.from_dict({})
        assert cfg.provider == ImageProvider.OPENAI

    def test_from_dict_full(self):
        from navig.tools.image_generation import ImageGenerationConfig, ImageProvider, ImageSize, ImageQuality

        data = {
            "provider": "stability",
            "openai_api_key": "key1",
            "stability_api_key": "key2",
            "local_api_url": "http://localhost:9000",
            "default_size": "512x512",
            "default_quality": "hd",
            "default_style": "natural",
            "output_dir": "/tmp/imgs",
            "save_locally": False,
        }
        cfg = ImageGenerationConfig.from_dict(data)
        assert cfg.provider == ImageProvider.STABILITY
        assert cfg.openai_api_key == "key1"
        assert cfg.default_size == ImageSize.SQUARE_MEDIUM
        assert cfg.default_quality == ImageQuality.HD
        assert cfg.save_locally is False

    def test_output_dir_uses_navig_data_dir(self, monkeypatch, tmp_path):
        from navig.tools.image_generation import ImageGenerationConfig

        monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
        cfg = ImageGenerationConfig()
        assert str(tmp_path) in cfg.output_dir
        assert "images" in cfg.output_dir


# ===========================================================================
# image_generation.py — GeneratedImage
# ===========================================================================

class TestGeneratedImage:
    def _make(self, **kwargs):
        from navig.tools.image_generation import GeneratedImage, ImageProvider
        defaults = {
            "prompt": "a cat",
            "revised_prompt": "a fluffy cat",
            "provider": ImageProvider.OPENAI,
            "size": "1024x1024",
        }
        defaults.update(kwargs)
        return GeneratedImage(**defaults)

    def test_to_dict_basic(self):
        img = self._make()
        d = img.to_dict()
        assert d["prompt"] == "a cat"
        assert d["revised_prompt"] == "a fluffy cat"
        assert d["provider"] == "openai"
        assert d["size"] == "1024x1024"
        assert "created_at" in d

    def test_to_dict_with_url(self):
        img = self._make(url="https://example.com/image.png")
        d = img.to_dict()
        assert d["url"] == "https://example.com/image.png"

    def test_to_dict_with_local_path(self):
        img = self._make(local_path="/tmp/image.png")
        d = img.to_dict()
        assert d["local_path"] == "/tmp/image.png"

    def test_to_dict_generation_time(self):
        img = self._make(generation_time=1.23)
        d = img.to_dict()
        assert d["generation_time"] == 1.23

    def test_to_dict_seed(self):
        img = self._make(seed=42)
        d = img.to_dict()
        assert d["seed"] == 42

    def test_default_values(self):
        img = self._make()
        assert img.url is None
        assert img.b64_data is None
        assert img.local_path is None
        assert img.generation_time == 0.0
        assert img.model is None
        assert img.seed is None
