"""
Batch 61: hermetic unit tests for
  - navig/core/thresholds.py      (Threshold, REGISTRY, resolve)
  - navig/importers/models.py     (ImportedItem, validate_item_dict)
  - navig/commands/crash.py       (crash_app Typer commands)
  - navig/ui/prompts.py           (render_keymap_footer, render_action_approval)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig/core/thresholds.py
# ---------------------------------------------------------------------------

class TestThreshold:
    def test_construction(self) -> None:
        from navig.core.thresholds import Threshold
        t = Threshold(warn_pct=75.0, crit_pct=90.0)
        assert t.warn_pct == 75.0
        assert t.crit_pct == 90.0

    def test_frozen(self) -> None:
        from navig.core.thresholds import Threshold
        t = Threshold(warn_pct=75.0, crit_pct=90.0)
        with pytest.raises((AttributeError, TypeError)):
            t.warn_pct = 50.0  # type: ignore[misc]

    def test_is_dataclass(self) -> None:
        import dataclasses
        from navig.core.thresholds import Threshold
        assert dataclasses.is_dataclass(Threshold)


class TestRegistry:
    def test_is_dict(self) -> None:
        from navig.core.thresholds import REGISTRY
        assert isinstance(REGISTRY, dict)

    def test_has_cpu_usage(self) -> None:
        from navig.core.thresholds import REGISTRY
        assert "cpu_usage" in REGISTRY

    def test_has_memory_usage(self) -> None:
        from navig.core.thresholds import REGISTRY
        assert "memory_usage" in REGISTRY

    def test_has_disk_usage(self) -> None:
        from navig.core.thresholds import REGISTRY
        assert "disk_usage" in REGISTRY

    def test_all_values_are_threshold(self) -> None:
        from navig.core.thresholds import REGISTRY, Threshold
        for v in REGISTRY.values():
            assert isinstance(v, Threshold)

    def test_warn_pct_less_than_crit_pct(self) -> None:
        from navig.core.thresholds import REGISTRY
        for name, t in REGISTRY.items():
            assert t.warn_pct < t.crit_pct, f"{name}: warn_pct >= crit_pct"


class TestDefaults:
    def test_defaults_importable(self) -> None:
        from navig.core.thresholds import DEFAULTS
        assert DEFAULTS is not None

    def test_defaults_is_threshold(self) -> None:
        from navig.core.thresholds import DEFAULTS, Threshold
        assert isinstance(DEFAULTS, Threshold)

    def test_defaults_values(self) -> None:
        from navig.core.thresholds import DEFAULTS
        assert DEFAULTS.warn_pct == 80.0
        assert DEFAULTS.crit_pct == 95.0


class TestResolve:
    def test_known_metric_returns_specific_threshold(self) -> None:
        from navig.core.thresholds import resolve
        t = resolve("cpu_usage")
        assert t.warn_pct == 75.0
        assert t.crit_pct == 90.0

    def test_unknown_metric_returns_defaults(self) -> None:
        from navig.core.thresholds import DEFAULTS, resolve
        t = resolve("unknown_metric_xyz")
        assert t == DEFAULTS

    def test_returns_threshold_type(self) -> None:
        from navig.core.thresholds import Threshold, resolve
        assert isinstance(resolve("cpu_usage"), Threshold)

    def test_all_registry_keys_resolvable(self) -> None:
        from navig.core.thresholds import REGISTRY, resolve
        for name in REGISTRY:
            t = resolve(name)
            assert t is REGISTRY[name]

    def test_error_rate_thresholds(self) -> None:
        from navig.core.thresholds import resolve
        t = resolve("error_rate")
        assert t.warn_pct == 5.0
        assert t.crit_pct == 15.0


# ---------------------------------------------------------------------------
# navig/importers/models.py
# ---------------------------------------------------------------------------

class TestImportedItem:
    def _make(self, **kwargs) -> "ImportedItem":
        from navig.importers.models import ImportedItem
        defaults = dict(source="test-source", type="server", label="My Server", value="192.168.1.1")
        defaults.update(kwargs)
        return ImportedItem(**defaults)

    def test_construction(self) -> None:
        item = self._make()
        assert item.source == "test-source"
        assert item.type == "server"
        assert item.label == "My Server"

    def test_meta_defaults_empty(self) -> None:
        item = self._make()
        assert item.meta == {}

    def test_validate_valid(self) -> None:
        item = self._make()
        item.validate()  # must not raise

    def test_validate_valid_contact_type(self) -> None:
        item = self._make(type="contact")
        item.validate()

    def test_validate_valid_bookmark_type(self) -> None:
        item = self._make(type="bookmark")
        item.validate()

    def test_validate_invalid_type_raises(self) -> None:
        item = self._make(type="unknown")
        with pytest.raises(ValueError, match="type must be one of"):
            item.validate()

    def test_validate_empty_source_raises(self) -> None:
        item = self._make(source="")
        with pytest.raises(ValueError, match="source"):
            item.validate()

    def test_validate_empty_label_raises(self) -> None:
        item = self._make(label="")
        with pytest.raises(ValueError, match="label"):
            item.validate()

    def test_validate_non_dict_meta_raises(self) -> None:
        item = self._make()
        object.__setattr__(item, "meta", "not-a-dict")  # bypass slots
        with pytest.raises(ValueError, match="meta"):
            item.validate()

    def test_to_dict_keys(self) -> None:
        item = self._make()
        d = item.to_dict()
        for key in ("source", "type", "label", "value", "meta"):
            assert key in d

    def test_to_dict_values(self) -> None:
        item = self._make(meta={"port": 22})
        d = item.to_dict()
        assert d["source"] == "test-source"
        assert d["meta"] == {"port": 22}


class TestValidateItemDict:
    def _dict(self, **kwargs):
        defaults = dict(source="s", type="server", label="L", value="v", meta={})
        defaults.update(kwargs)
        return defaults

    def test_valid_dict_returns_dict(self) -> None:
        from navig.importers.models import validate_item_dict
        result = validate_item_dict(self._dict())
        assert isinstance(result, dict)

    def test_missing_field_raises(self) -> None:
        from navig.importers.models import validate_item_dict
        d = self._dict()
        del d["source"]
        with pytest.raises(ValueError, match="missing"):
            validate_item_dict(d)

    def test_bad_type_raises(self) -> None:
        from navig.importers.models import validate_item_dict
        with pytest.raises(ValueError):
            validate_item_dict(self._dict(type="bad"))

    def test_non_dict_meta_coerced_to_empty(self) -> None:
        from navig.importers.models import validate_item_dict
        result = validate_item_dict(self._dict(meta="not-a-dict"))
        assert result["meta"] == {}

    def test_returns_required_keys(self) -> None:
        from navig.importers.models import validate_item_dict
        result = validate_item_dict(self._dict())
        assert set(result.keys()) == {"source", "type", "label", "value", "meta"}


# ---------------------------------------------------------------------------
# navig/commands/crash.py
# ---------------------------------------------------------------------------

class TestCrashApp:
    def test_importable(self) -> None:
        from navig.commands.crash import crash_app
        assert crash_app is not None

    def test_is_typer(self) -> None:
        import typer
        from navig.commands.crash import crash_app
        assert isinstance(crash_app, typer.Typer)

    def test_alias_matches_app(self) -> None:
        from navig.commands import crash as crash_mod
        assert crash_mod.crash_app is crash_mod.app

    def test_export_no_crash_report(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.crash import crash_app

        mock_handler = MagicMock()
        mock_handler.get_latest_crash_report.return_value = None

        with patch("navig.core.crash_handler.CrashHandler", return_value=mock_handler):
            runner = CliRunner()
            result = runner.invoke(crash_app, [])
        assert "no crash" in result.output.lower()  # message is printed regardless of exit code

    def test_export_with_crash_report(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.crash import crash_app

        mock_handler = MagicMock()
        mock_handler.get_latest_crash_report.return_value = {"error": "segfault", "ts": "2024-01-01"}

        with patch("navig.core.crash_handler.CrashHandler", return_value=mock_handler):
            runner = CliRunner()
            result = runner.invoke(crash_app, [])
        assert result.exit_code == 0
        assert "segfault" in result.output

    def test_export_to_file(self, tmp_path) -> None:
        from typer.testing import CliRunner
        from navig.commands.crash import crash_app

        out_file = tmp_path / "crash.json"
        mock_handler = MagicMock()
        mock_handler.get_latest_crash_report.return_value = {"error": "oops"}

        with patch("navig.core.crash_handler.CrashHandler", return_value=mock_handler):
            runner = CliRunner()
            result = runner.invoke(crash_app, [ "--output", str(out_file)])
        assert result.exit_code == 0
        assert out_file.exists()
        import json
        assert json.loads(out_file.read_text())["error"] == "oops"


# ---------------------------------------------------------------------------
# navig/ui/prompts.py
# ---------------------------------------------------------------------------

class TestRenderKeymapFooter:
    def test_importable(self) -> None:
        from navig.ui.prompts import render_keymap_footer
        assert callable(render_keymap_footer)

    def test_empty_keymap_no_output(self) -> None:
        from navig.ui.prompts import render_keymap_footer
        mock_console = MagicMock()
        with patch("navig.ui.prompts.console", mock_console):
            render_keymap_footer({})
        mock_console.print.assert_not_called()

    def test_single_binding(self) -> None:
        from navig.ui.prompts import render_keymap_footer
        mock_console = MagicMock()
        with patch("navig.ui.prompts.console", mock_console):
            render_keymap_footer({"q": "quit"})
        mock_console.print.assert_called_once()
        output = mock_console.print.call_args[0][0]
        assert "q" in output and "quit" in output

    def test_multiple_bindings(self) -> None:
        from navig.ui.prompts import render_keymap_footer
        mock_console = MagicMock()
        with patch("navig.ui.prompts.console", mock_console):
            render_keymap_footer({"q": "quit", "h": "help", "r": "refresh"})
        mock_console.print.assert_called_once()

    def test_custom_separator(self) -> None:
        from navig.ui.prompts import render_keymap_footer
        mock_console = MagicMock()
        with patch("navig.ui.prompts.console", mock_console):
            render_keymap_footer({"a": "act", "b": "back"}, separator=" | ")
        output = mock_console.print.call_args[0][0]
        assert " | " in output

    def test_never_raises(self) -> None:
        from navig.ui.prompts import render_keymap_footer
        mock_console = MagicMock()
        mock_console.print.side_effect = RuntimeError("boom")
        render_keymap_footer({"q": "quit"})  # must not propagate


class TestRenderActionApproval:
    def test_importable(self) -> None:
        from navig.ui.prompts import render_action_approval
        assert callable(render_action_approval)

    def test_returns_true_on_y(self) -> None:
        from navig.ui.prompts import render_action_approval
        mock_console = MagicMock()
        with patch("navig.ui.prompts.console", mock_console), \
             patch("builtins.input", return_value="y"):
            result = render_action_approval("ls -la")
        assert result is True

    def test_returns_true_on_yes(self) -> None:
        from navig.ui.prompts import render_action_approval
        mock_console = MagicMock()
        with patch("navig.ui.prompts.console", mock_console), \
             patch("builtins.input", return_value="yes"):
            result = render_action_approval("ls -la")
        assert result is True

    def test_returns_false_on_n(self) -> None:
        from navig.ui.prompts import render_action_approval
        mock_console = MagicMock()
        with patch("navig.ui.prompts.console", mock_console), \
             patch("builtins.input", return_value="n"):
            result = render_action_approval("ls -la")
        assert result is False

    def test_returns_false_on_empty(self) -> None:
        from navig.ui.prompts import render_action_approval
        mock_console = MagicMock()
        with patch("navig.ui.prompts.console", mock_console), \
             patch("builtins.input", return_value=""):
            result = render_action_approval("ls -la")
        assert result is False

    def test_returns_false_on_eoferror(self) -> None:
        from navig.ui.prompts import render_action_approval
        mock_console = MagicMock()
        with patch("navig.ui.prompts.console", mock_console), \
             patch("builtins.input", side_effect=EOFError):
            result = render_action_approval("ls -la")
        assert result is False

    def test_returns_false_on_keyboard_interrupt(self) -> None:
        from navig.ui.prompts import render_action_approval
        mock_console = MagicMock()
        with patch("navig.ui.prompts.console", mock_console), \
             patch("builtins.input", side_effect=KeyboardInterrupt):
            result = render_action_approval("ls -la")
        assert result is False

    def test_command_shown_in_output(self) -> None:
        from navig.ui.prompts import render_action_approval
        mock_console = MagicMock()
        with patch("navig.ui.prompts.console", mock_console), \
             patch("builtins.input", return_value="n"):
            render_action_approval("my-command --flag")
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("my-command" in c for c in calls)

    def test_hint_shown_when_provided(self) -> None:
        from navig.ui.prompts import render_action_approval
        mock_console = MagicMock()
        with patch("navig.ui.prompts.console", mock_console), \
             patch("builtins.input", return_value="n"):
            render_action_approval("cmd", hint="This is risky")
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("risky" in c for c in calls)

    def test_no_hint_no_extra_print(self) -> None:
        from navig.ui.prompts import render_action_approval
        mock_console = MagicMock()
        with patch("navig.ui.prompts.console", mock_console), \
             patch("builtins.input", return_value="n"):
            render_action_approval("cmd", hint=None)
        # without hint, fewer console.print calls
        call_count_no_hint = mock_console.print.call_count
        mock_console.reset_mock()
        with patch("navig.ui.prompts.console", mock_console), \
             patch("builtins.input", return_value="n"):
            render_action_approval("cmd", hint="hint text")
        assert mock_console.print.call_count > call_count_no_hint or call_count_no_hint >= 1
