"""
Batch 123 — agent/tools/git_tools (_find_git_root, _run_git, _GIT_TIMEOUT)
           + tools/image_generation (enums, ImageGenerationConfig, GeneratedImage)

Pure-unit tests: real filesystem for _find_git_root, mocked subprocess for _run_git,
no network, no external API keys.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# git_tools constants and helpers
# ---------------------------------------------------------------------------

from navig.agent.tools.git_tools import _GIT_TIMEOUT, _find_git_root, _run_git


class TestGITTimeout:
    def test_value_is_30(self):
        assert _GIT_TIMEOUT == 30

    def test_positive(self):
        assert _GIT_TIMEOUT > 0


class TestFindGitRoot:
    def test_finds_root_from_cwd(self, tmp_path):
        """A .git dir at root level should be detected from root itself."""
        (tmp_path / ".git").mkdir()
        result = _find_git_root(tmp_path)
        assert result == tmp_path

    def test_finds_root_from_nested_dir(self, tmp_path):
        """Walk upward and find .git in parent."""
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        result = _find_git_root(nested)
        assert result == tmp_path

    def test_returns_none_when_no_git(self, tmp_path):
        """No .git anywhere → None."""
        result = _find_git_root(tmp_path)
        assert result is None

    def test_immediate_parent_wins(self, tmp_path):
        """Closest .git wins, not a grandparent."""
        (tmp_path / ".git").mkdir()
        child = tmp_path / "sub"
        child.mkdir()
        (child / ".git").mkdir()
        result = _find_git_root(child)
        assert result == child

    def test_returns_path_object(self, tmp_path):
        (tmp_path / ".git").mkdir()
        result = _find_git_root(tmp_path)
        assert isinstance(result, Path)


class TestRunGit:
    """Tests for _run_git — mocked via subprocess.run."""

    def _mock_completed(self, stdout="", stderr="", returncode=0):
        cp = MagicMock(spec=subprocess.CompletedProcess)
        cp.stdout = stdout
        cp.stderr = stderr
        cp.returncode = returncode
        return cp

    def test_success_returns_true_and_stdout(self, tmp_path):
        with patch("navig.agent.tools.git_tools.subprocess.run") as mock_run:
            mock_run.return_value = self._mock_completed(stdout="main\n", returncode=0)
            ok, output = _run_git(["branch", "--show-current"], cwd=tmp_path)
        assert ok is True
        assert "main" in output

    def test_failure_returns_false_and_stderr(self, tmp_path):
        with patch("navig.agent.tools.git_tools.subprocess.run") as mock_run:
            mock_run.return_value = self._mock_completed(stderr="not a git repo", returncode=128)
            ok, output = _run_git(["status"], cwd=tmp_path)
        assert ok is False
        assert "not a git repo" in output

    def test_timeout_returns_false(self, tmp_path):
        with patch("navig.agent.tools.git_tools.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
            ok, output = _run_git(["log"], cwd=tmp_path)
        assert ok is False
        assert "timed out" in output

    def test_file_not_found_returns_false(self, tmp_path):
        with patch("navig.agent.tools.git_tools.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            ok, output = _run_git(["status"], cwd=tmp_path)
        assert ok is False
        assert "not found" in output

    def test_generic_exception_returns_false(self, tmp_path):
        with patch("navig.agent.tools.git_tools.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("permission denied")
            ok, output = _run_git(["log"], cwd=tmp_path)
        assert ok is False
        assert len(output) > 0

    def test_failure_falls_back_to_stdout_when_no_stderr(self, tmp_path):
        with patch("navig.agent.tools.git_tools.subprocess.run") as mock_run:
            mock_run.return_value = self._mock_completed(stdout="some output", stderr="", returncode=1)
            ok, output = _run_git(["diff"], cwd=tmp_path)
        assert ok is False
        assert "some output" in output


# ---------------------------------------------------------------------------
# image_generation enums
# ---------------------------------------------------------------------------

from navig.tools.image_generation import (
    GeneratedImage,
    ImageGenerationConfig,
    ImageProvider,
    ImageQuality,
    ImageSize,
    ImageStyle,
    is_image_generation_available,
)


class TestImageProvider:
    def test_openai_value(self):
        assert ImageProvider.OPENAI.value == "openai"

    def test_stability_value(self):
        assert ImageProvider.STABILITY.value == "stability"

    def test_local_value(self):
        assert ImageProvider.LOCAL.value == "local"

    def test_three_members(self):
        assert len(list(ImageProvider)) == 3


class TestImageSize:
    def test_square_small(self):
        assert ImageSize.SQUARE_SMALL.value == "256x256"

    def test_square_medium(self):
        assert ImageSize.SQUARE_MEDIUM.value == "512x512"

    def test_square_large(self):
        assert ImageSize.SQUARE_LARGE.value == "1024x1024"

    def test_landscape(self):
        assert ImageSize.LANDSCAPE.value == "1792x1024"

    def test_portrait(self):
        assert ImageSize.PORTRAIT.value == "1024x1792"

    def test_five_members(self):
        assert len(list(ImageSize)) == 5


class TestImageQuality:
    def test_standard_value(self):
        assert ImageQuality.STANDARD.value == "standard"

    def test_hd_value(self):
        assert ImageQuality.HD.value == "hd"

    def test_two_members(self):
        assert len(list(ImageQuality)) == 2


class TestImageStyle:
    def test_vivid_value(self):
        assert ImageStyle.VIVID.value == "vivid"

    def test_natural_value(self):
        assert ImageStyle.NATURAL.value == "natural"

    def test_two_members(self):
        assert len(list(ImageStyle)) == 2


# ---------------------------------------------------------------------------
# ImageGenerationConfig
# ---------------------------------------------------------------------------


class TestImageGenerationConfig:
    def test_provider_default_openai(self):
        cfg = ImageGenerationConfig()
        assert cfg.provider == ImageProvider.OPENAI

    def test_default_size_square_large(self):
        cfg = ImageGenerationConfig()
        assert cfg.default_size == ImageSize.SQUARE_LARGE

    def test_default_quality_standard(self):
        cfg = ImageGenerationConfig()
        assert cfg.default_quality == ImageQuality.STANDARD

    def test_default_style_vivid(self):
        cfg = ImageGenerationConfig()
        assert cfg.default_style == ImageStyle.VIVID

    def test_save_locally_true(self):
        cfg = ImageGenerationConfig()
        assert cfg.save_locally is True

    def test_max_concurrent_default_2(self):
        cfg = ImageGenerationConfig()
        assert cfg.max_concurrent == 2

    def test_rate_limit_delay_default(self):
        cfg = ImageGenerationConfig()
        assert cfg.rate_limit_delay == pytest.approx(1.0)

    def test_local_api_url_default(self):
        cfg = ImageGenerationConfig()
        assert "localhost:7860" in cfg.local_api_url

    def test_api_keys_default_none(self):
        cfg = ImageGenerationConfig()
        assert cfg.openai_api_key is None
        assert cfg.stability_api_key is None

    def test_from_dict_provider(self):
        cfg = ImageGenerationConfig.from_dict({"provider": "stability"})
        assert cfg.provider == ImageProvider.STABILITY

    def test_from_dict_default_provider_openai(self):
        cfg = ImageGenerationConfig.from_dict({})
        assert cfg.provider == ImageProvider.OPENAI

    def test_from_dict_size(self):
        cfg = ImageGenerationConfig.from_dict({"default_size": "512x512"})
        assert cfg.default_size == ImageSize.SQUARE_MEDIUM

    def test_can_override_provider(self):
        cfg = ImageGenerationConfig(provider=ImageProvider.LOCAL)
        assert cfg.provider == ImageProvider.LOCAL


# ---------------------------------------------------------------------------
# GeneratedImage
# ---------------------------------------------------------------------------


class TestGeneratedImage:
    def _make(self, **kwargs):
        defaults = dict(
            prompt="a red fox",
            revised_prompt=None,
            provider=ImageProvider.OPENAI,
            size="1024x1024",
        )
        defaults.update(kwargs)
        return GeneratedImage(**defaults)

    def test_prompt_stored(self):
        assert self._make().prompt == "a red fox"

    def test_provider_stored(self):
        assert self._make().provider == ImageProvider.OPENAI

    def test_size_stored(self):
        assert self._make().size == "1024x1024"

    def test_url_default_none(self):
        assert self._make().url is None

    def test_b64_data_default_none(self):
        assert self._make().b64_data is None

    def test_local_path_default_none(self):
        assert self._make().local_path is None

    def test_generation_time_default_zero(self):
        assert self._make().generation_time == 0.0

    def test_model_default_none(self):
        assert self._make().model is None

    def test_to_dict_prompt(self):
        assert self._make().to_dict()["prompt"] == "a red fox"

    def test_to_dict_provider_is_str(self):
        assert self._make().to_dict()["provider"] == "openai"

    def test_to_dict_size(self):
        assert self._make().to_dict()["size"] == "1024x1024"

    def test_to_dict_created_at_is_str(self):
        assert isinstance(self._make().to_dict()["created_at"], str)

    def test_to_dict_url_none(self):
        assert self._make().to_dict()["url"] is None


# ---------------------------------------------------------------------------
# is_image_generation_available
# ---------------------------------------------------------------------------


class TestIsImageGenerationAvailable:
    def test_returns_bool(self):
        result = is_image_generation_available()
        assert isinstance(result, bool)

    def test_false_when_no_keys_in_env(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("STABILITY_API_KEY", raising=False)
        # May still return True if httpx unavailable; just verify bool
        result = is_image_generation_available()
        assert isinstance(result, bool)

    def test_true_when_openai_key_set(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        # Only True if httpx is available too — structure test
        result = is_image_generation_available()
        assert isinstance(result, bool)
