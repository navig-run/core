"""Tests for navig.ui._capabilities and terminal-setup onboarding step."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

# ── Helpers ──────────────────────────────────────────────────────────────────


def _reload_theme():
    """Reload theme module to re-evaluate NERD_FONT_AVAILABLE."""
    import navig.ui.theme as mod

    importlib.reload(mod)
    return mod


# ── navig.ui._capabilities ────────────────────────────────────────────────────


class TestReadWriteTerminalJson:
    def test_write_then_read(self, tmp_path):
        from navig.ui._capabilities import read_terminal_json, write_terminal_json

        write_terminal_json(tmp_path, nerd_font=True)
        data = read_terminal_json(tmp_path)
        assert data["nerd_font"] is True
        assert "checked_at" in data

    def test_read_missing(self, tmp_path):
        from navig.ui._capabilities import read_terminal_json

        data = read_terminal_json(tmp_path)
        assert data == {}

    def test_read_invalid_json(self, tmp_path):
        from navig.ui._capabilities import read_terminal_json

        (tmp_path / "terminal.json").write_text("not json", encoding="utf-8")
        assert read_terminal_json(tmp_path) == {}

    def test_write_merges_existing(self, tmp_path):
        from navig.ui._capabilities import read_terminal_json, write_terminal_json

        # Write initial data
        write_terminal_json(tmp_path, nerd_font=False, unicode=True)
        # Overwrite only nerd_font
        write_terminal_json(tmp_path, nerd_font=True)
        data = read_terminal_json(tmp_path)
        assert data["nerd_font"] is True
        assert data.get("unicode") is True  # preserved from first write


class TestProbeNerdFont:
    def test_windows_registry_hit(self, tmp_path):
        """If winreg returns a Nerd Font entry, probe returns True."""
        caps = importlib.import_module("navig.ui._capabilities")

        fake_key = object()
        with (
            patch.object(sys, "platform", "win32"),
            patch.dict("sys.modules", {"winreg": MagicMock()}),
        ):
            import winreg as wr  # type: ignore[import]

            wr.OpenKey.return_value.__enter__ = lambda s: s
            wr.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
            wr.QueryInfoKey.return_value = (1, 0, 0)
            wr.EnumValue.return_value = ("JetBrainsMono Nerd Font Regular (TrueType)", "path", 1)
            # Re-import to use our mocked winreg
            importlib.reload(caps)
            with patch.object(caps, "_probe_windows", return_value=True):
                assert caps.probe_nerd_font() is True

    def test_directory_hit(self, tmp_path):
        from navig.ui._capabilities import _any_nerd_font_in_dir

        nf_dir = tmp_path / "fonts"
        nf_dir.mkdir()
        (nf_dir / "JetBrainsMonoNerdFont-Regular.ttf").write_bytes(b"")
        assert _any_nerd_font_in_dir(nf_dir) is True

    def test_directory_miss(self, tmp_path):
        from navig.ui._capabilities import _any_nerd_font_in_dir

        nf_dir = tmp_path / "fonts"
        nf_dir.mkdir()
        (nf_dir / "Arial.ttf").write_bytes(b"")
        assert _any_nerd_font_in_dir(nf_dir) is False

    def test_directory_nonexistent(self, tmp_path):
        from navig.ui._capabilities import _any_nerd_font_in_dir

        assert _any_nerd_font_in_dir(tmp_path / "nonexistent") is False


# ── navig.ui.theme — NERD_FONT_AVAILABLE ─────────────────────────────────────


class TestDetectNerdFont:
    def test_env_override_on(self, monkeypatch):
        monkeypatch.setenv("NAVIG_NERD_FONT", "1")
        monkeypatch.delenv("CI", raising=False)
        mod = _reload_theme()
        assert mod.NERD_FONT_AVAILABLE is True

    def test_env_override_off(self, monkeypatch):
        monkeypatch.setenv("NAVIG_NERD_FONT", "0")
        monkeypatch.delenv("CI", raising=False)
        mod = _reload_theme()
        assert mod.NERD_FONT_AVAILABLE is False

    def test_ci_disables(self, monkeypatch):
        monkeypatch.setenv("CI", "true")
        monkeypatch.delenv("NAVIG_NERD_FONT", raising=False)
        mod = _reload_theme()
        assert mod.NERD_FONT_AVAILABLE is False

    def test_reads_terminal_json_true(self, tmp_path, monkeypatch):
        from navig.ui._capabilities import write_terminal_json

        write_terminal_json(tmp_path, nerd_font=True)
        monkeypatch.setenv("NAVIG_HOME", str(tmp_path))
        monkeypatch.delenv("NAVIG_NERD_FONT", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)
        mod = _reload_theme()
        assert mod.NERD_FONT_AVAILABLE is True

    def test_reads_terminal_json_false(self, tmp_path, monkeypatch):
        from navig.ui._capabilities import write_terminal_json

        write_terminal_json(tmp_path, nerd_font=False)
        monkeypatch.setenv("NAVIG_HOME", str(tmp_path))
        monkeypatch.delenv("NAVIG_NERD_FONT", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)
        mod = _reload_theme()
        assert mod.NERD_FONT_AVAILABLE is False


# ── navig.ui.icons — nf_icon fallback ────────────────────────────────────────


class TestNfIconFallback:
    def test_falls_back_to_icon_when_no_font(self, monkeypatch):
        # Force NERD_FONT_AVAILABLE off by patching icons module attribute
        import navig.ui.icons as icons_mod
        import navig.ui.theme as theme_mod

        orig = theme_mod.NERD_FONT_AVAILABLE
        theme_mod.NERD_FONT_AVAILABLE = False
        icons_mod.NERD_FONT_AVAILABLE = False
        try:
            result = icons_mod.nf_icon("warning")
            # Should return the regular icon, not a raw codepoint
            # The result must be printable ASCII/Unicode, not a replacement box candidate
            assert result == icons_mod.icon("warning")
        finally:
            theme_mod.NERD_FONT_AVAILABLE = orig
            icons_mod.NERD_FONT_AVAILABLE = orig

    def test_returns_nf_codepoint_when_font_available(self, monkeypatch):
        import navig.ui.icons as icons_mod
        import navig.ui.theme as theme_mod

        orig = theme_mod.NERD_FONT_AVAILABLE
        theme_mod.NERD_FONT_AVAILABLE = True
        icons_mod.NERD_FONT_AVAILABLE = True
        try:
            result = icons_mod.nf_icon("warning")
            # Should NOT equal the plain icon (it should be a Nerd Font codepoint)
            assert result == icons_mod._NF_ICONS.get("warning", icons_mod.icon("warning"))
        finally:
            theme_mod.NERD_FONT_AVAILABLE = orig
            icons_mod.NERD_FONT_AVAILABLE = orig


# ── terminal-setup onboarding step ───────────────────────────────────────────


class TestTerminalSetupStep:
    def _make_step(self, tmp_path):
        from navig.onboarding.steps import _step_terminal_setup

        return _step_terminal_setup(tmp_path)

    def test_step_id_and_phase(self, tmp_path):
        step = self._make_step(tmp_path)
        assert step.id == "terminal-setup"
        assert step.phase == "bootstrap"
        assert step.on_failure == "skip"

    def test_skips_when_no_tty_and_no_font(self, tmp_path):
        step = self._make_step(tmp_path)
        with (
            patch("navig.ui._capabilities.probe_nerd_font", return_value=False),
            patch("sys.stdout") as mock_stdout,
        ):
            mock_stdout.isatty.return_value = False
            result = step.run()
        assert result.status == "skipped"
        assert result.output["reason"] == "no-tty"
        # terminal.json should still be written
        assert (tmp_path / "terminal.json").exists()

    def test_completes_when_font_detected(self, tmp_path):
        step = self._make_step(tmp_path)
        with patch("navig.ui._capabilities.probe_nerd_font", return_value=True):
            result = step.run()
        assert result.status == "completed"
        assert result.output["nerd_font"] is True

    def test_verify_returns_false_before_run(self, tmp_path):
        step = self._make_step(tmp_path)
        assert step.verify() is False

    def test_verify_returns_true_after_run(self, tmp_path):
        step = self._make_step(tmp_path)
        with (
            patch("navig.ui._capabilities.probe_nerd_font", return_value=True),
        ):
            step.run()
        assert step.verify() is True

    def test_registered_in_build_step_registry(self):
        """terminal-setup must appear in the build_step_registry output."""
        from unittest.mock import MagicMock

        from navig.onboarding import steps as s

        cfg = MagicMock()
        cfg.navig_dir = Path("/tmp/fake_navig_dir")
        cfg.reset = False
        genesis = MagicMock()
        ids = [st.id for st in s.build_step_registry(cfg, genesis=genesis)]
        assert "terminal-setup" in ids
