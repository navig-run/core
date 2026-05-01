"""
Batch 46 — hermetic unit tests for:
  navig/providers/_local_defaults.py           — URL constants
  navig/connectors/google_calendar/oauth_config.py — CALENDAR_SCOPES, build_calendar_oauth_config
  navig/ui/status.py                           — render_status_header
  navig/ui/timeline.py                         — render_event_timeline
  navig/agent/skill_drafter.py                 — SkillDraft, SkillDrafter
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# navig/providers/_local_defaults.py
# ---------------------------------------------------------------------------

from navig.providers._local_defaults import (
    _LLAMACPP_BASE_URL,
    _LLAMACPP_USER_BASE_URL,
    _OLLAMA_BASE_URL,
    _OLLAMA_USER_BASE_URL,
)


class TestLocalDefaults:
    def test_ollama_base_url_loopback(self):
        assert _OLLAMA_BASE_URL == "http://127.0.0.1:11434"

    def test_ollama_user_base_url_localhost(self):
        assert _OLLAMA_USER_BASE_URL == "http://localhost:11434"

    def test_llamacpp_base_url_loopback(self):
        assert _LLAMACPP_BASE_URL == "http://127.0.0.1:8080"

    def test_llamacpp_user_base_url_localhost(self):
        assert _LLAMACPP_USER_BASE_URL == "http://localhost:8080"

    def test_base_urls_are_explicit_loopback(self):
        """Internal probe URLs must use 127.0.0.1 not localhost."""
        assert "127.0.0.1" in _OLLAMA_BASE_URL
        assert "127.0.0.1" in _LLAMACPP_BASE_URL

    def test_user_urls_use_localhost(self):
        """User-facing URLs must use localhost."""
        assert "localhost" in _OLLAMA_USER_BASE_URL
        assert "localhost" in _LLAMACPP_USER_BASE_URL

    def test_ollama_port_is_11434(self):
        assert ":11434" in _OLLAMA_BASE_URL
        assert ":11434" in _OLLAMA_USER_BASE_URL

    def test_llamacpp_port_is_8080(self):
        assert ":8080" in _LLAMACPP_BASE_URL
        assert ":8080" in _LLAMACPP_USER_BASE_URL

    def test_all_urls_are_http(self):
        for url in (_OLLAMA_BASE_URL, _OLLAMA_USER_BASE_URL, _LLAMACPP_BASE_URL, _LLAMACPP_USER_BASE_URL):
            assert url.startswith("http://")

    def test_urls_are_strings(self):
        for url in (_OLLAMA_BASE_URL, _OLLAMA_USER_BASE_URL, _LLAMACPP_BASE_URL, _LLAMACPP_USER_BASE_URL):
            assert isinstance(url, str)


# ---------------------------------------------------------------------------
# navig/connectors/google_calendar/oauth_config.py
# ---------------------------------------------------------------------------

from navig.connectors.google_calendar.oauth_config import (
    CALENDAR_SCOPES,
    build_calendar_oauth_config,
)


class TestCalendarScopes:
    def test_is_list(self):
        assert isinstance(CALENDAR_SCOPES, list)

    def test_not_empty(self):
        assert len(CALENDAR_SCOPES) > 0

    def test_contains_calendar_scope(self):
        assert "https://www.googleapis.com/auth/calendar" in CALENDAR_SCOPES

    def test_contains_events_scope(self):
        assert "https://www.googleapis.com/auth/calendar.events" in CALENDAR_SCOPES

    def test_contains_openid(self):
        assert "openid" in CALENDAR_SCOPES

    def test_contains_email_and_profile(self):
        assert "email" in CALENDAR_SCOPES
        assert "profile" in CALENDAR_SCOPES


class TestBuildCalendarOAuthConfig:
    def test_basic_build(self):
        cfg = build_calendar_oauth_config(client_id="my-client-id")
        assert cfg.client_id == "my-client-id"

    def test_name_is_google_calendar(self):
        cfg = build_calendar_oauth_config(client_id="x")
        assert cfg.name == "Google Calendar"

    def test_client_secret_none_by_default(self):
        cfg = build_calendar_oauth_config(client_id="x")
        assert cfg.client_secret is None

    def test_client_secret_when_provided(self):
        cfg = build_calendar_oauth_config(client_id="x", client_secret="s3cr3t")
        assert cfg.client_secret == "s3cr3t"

    def test_scopes_match_calendar_scopes(self):
        cfg = build_calendar_oauth_config(client_id="x")
        assert cfg.scopes == CALENDAR_SCOPES

    def test_authorize_url_is_google(self):
        cfg = build_calendar_oauth_config(client_id="x")
        assert "google" in cfg.authorize_url.lower() or "accounts.google.com" in cfg.authorize_url

    def test_token_url_is_google(self):
        cfg = build_calendar_oauth_config(client_id="x")
        assert "google" in cfg.token_url.lower() or "oauth2.googleapis.com" in cfg.token_url

    def test_userinfo_url_set(self):
        cfg = build_calendar_oauth_config(client_id="x")
        assert cfg.userinfo_url is not None
        assert "http" in cfg.userinfo_url


# ---------------------------------------------------------------------------
# navig/ui/status.py
# ---------------------------------------------------------------------------

from navig.ui.models import Event, StatusChip
from navig.ui.status import render_status_header


class TestRenderStatusHeader:
    def _chip(self, label="daemon", color="green", icon="◉", icon_safe="*", value=None):
        return StatusChip(label=label, color=color, icon=icon, icon_safe=icon_safe, value=value)

    def test_empty_chips_no_output(self):
        mock_console = MagicMock()
        with patch("navig.ui.status.console", mock_console):
            render_status_header([])
        mock_console.print.assert_not_called()

    def test_single_chip_no_value(self):
        mock_console = MagicMock()
        with (
            patch("navig.ui.status.console", mock_console),
            patch("navig.ui.status.SAFE_MODE", False),
            patch("navig.ui.status.COLOR_STYLE", {}),
        ):
            render_status_header([self._chip(label="daemon", value=None)])
        mock_console.print.assert_called_once()
        args = mock_console.print.call_args[0][0]
        assert "daemon" in args

    def test_single_chip_with_value(self):
        mock_console = MagicMock()
        with (
            patch("navig.ui.status.console", mock_console),
            patch("navig.ui.status.SAFE_MODE", False),
            patch("navig.ui.status.COLOR_STYLE", {}),
        ):
            render_status_header([self._chip(label="peers", value="3")])
        args = mock_console.print.call_args[0][0]
        assert "peers" in args
        assert "3" in args

    def test_multiple_chips_joined_with_sep(self):
        mock_console = MagicMock()
        chips = [self._chip("a"), self._chip("b"), self._chip("c")]
        sep = "  |  "
        with (
            patch("navig.ui.status.console", mock_console),
            patch("navig.ui.status.SAFE_MODE", False),
            patch("navig.ui.status.COLOR_STYLE", {}),
        ):
            render_status_header(chips, sep=sep)
        args = mock_console.print.call_args[0][0]
        assert sep in args

    def test_safe_mode_uses_icon_safe(self):
        mock_console = MagicMock()
        chip = self._chip(icon="◉", icon_safe="*")
        with (
            patch("navig.ui.status.console", mock_console),
            patch("navig.ui.status.SAFE_MODE", True),
            patch("navig.ui.status.COLOR_STYLE", {}),
        ):
            render_status_header([chip])
        args = mock_console.print.call_args[0][0]
        assert "*" in args

    def test_non_safe_mode_uses_icon(self):
        mock_console = MagicMock()
        chip = self._chip(icon="◉", icon_safe="*")
        with (
            patch("navig.ui.status.console", mock_console),
            patch("navig.ui.status.SAFE_MODE", False),
            patch("navig.ui.status.COLOR_STYLE", {}),
        ):
            render_status_header([chip])
        args = mock_console.print.call_args[0][0]
        assert "◉" in args

    def test_color_style_lookup_applied(self):
        mock_console = MagicMock()
        chip = StatusChip(label="x", color="success", icon="✔", icon_safe="ok")
        color_style = {"success": "bold green"}
        with (
            patch("navig.ui.status.console", mock_console),
            patch("navig.ui.status.SAFE_MODE", False),
            patch("navig.ui.status.COLOR_STYLE", color_style),
        ):
            render_status_header([chip])
        args = mock_console.print.call_args[0][0]
        assert "bold green" in args

    def test_never_raises_on_exception(self):
        """render_status_header is best-effort and must not propagate exceptions."""
        with patch("navig.ui.status.console", side_effect=Exception("boom")):
            # Should not raise
            render_status_header([self._chip()])

    def test_fallback_prints_to_stdout_on_console_failure(self, capsys):
        chip = self._chip(label="host", value="prod", icon_safe="+")
        broken_console = MagicMock()
        broken_console.print.side_effect = RuntimeError("console failure")
        with (
            patch("navig.ui.status.console", broken_console),
            patch("navig.ui.status.SAFE_MODE", True),
            patch("navig.ui.status.COLOR_STYLE", {}),
        ):
            render_status_header([chip])
        out = capsys.readouterr().out
        assert "host" in out


# ---------------------------------------------------------------------------
# navig/ui/timeline.py
# ---------------------------------------------------------------------------

from navig.ui.timeline import render_event_timeline


class TestRenderEventTimeline:
    def _event(self, timestamp="12:00", icon="●", label="deploy", detail="ok", color="green"):
        return Event(timestamp=timestamp, icon=icon, label=label, detail=detail, color=color)

    def test_empty_events_no_output(self):
        mock_console = MagicMock()
        with patch("navig.ui.timeline.console", mock_console):
            render_event_timeline([])
        mock_console.print.assert_not_called()

    def test_shows_title_when_show_title_true(self):
        mock_console = MagicMock()
        with (
            patch("navig.ui.timeline.console", mock_console),
            patch("navig.ui.timeline.SAFE_MODE", False),
            patch("navig.ui.timeline.COLOR_STYLE", {}),
        ):
            render_event_timeline([self._event()], title="Deploy Log", show_title=True)
        # First print call should be the title
        first_call_arg = mock_console.print.call_args_list[0][0][0]
        assert "Deploy Log" in first_call_arg

    def test_no_title_when_show_title_false(self):
        mock_console = MagicMock()
        with (
            patch("navig.ui.timeline.console", mock_console),
            patch("navig.ui.timeline.SAFE_MODE", False),
            patch("navig.ui.timeline.COLOR_STYLE", {}),
        ):
            render_event_timeline([self._event()], show_title=False)
        # Only 1 print call for the event row
        assert mock_console.print.call_count == 1

    def test_event_label_in_output(self):
        mock_console = MagicMock()
        with (
            patch("navig.ui.timeline.console", mock_console),
            patch("navig.ui.timeline.SAFE_MODE", False),
            patch("navig.ui.timeline.COLOR_STYLE", {}),
        ):
            render_event_timeline([self._event(label="migration")])
        all_args = " ".join(str(a) for call_obj in mock_console.print.call_args_list for a in call_obj[0])
        assert "migration" in all_args

    def test_event_timestamp_in_output(self):
        mock_console = MagicMock()
        with (
            patch("navig.ui.timeline.console", mock_console),
            patch("navig.ui.timeline.SAFE_MODE", False),
            patch("navig.ui.timeline.COLOR_STYLE", {}),
        ):
            render_event_timeline([self._event(timestamp="09:30")])
        all_args = " ".join(str(a) for call_obj in mock_console.print.call_args_list for a in call_obj[0])
        assert "09:30" in all_args

    def test_multiple_events_all_rendered(self):
        mock_console = MagicMock()
        events = [self._event(label=f"ev{i}") for i in range(4)]
        with (
            patch("navig.ui.timeline.console", mock_console),
            patch("navig.ui.timeline.SAFE_MODE", False),
            patch("navig.ui.timeline.COLOR_STYLE", {}),
        ):
            render_event_timeline(events, show_title=False)
        # 4 event rows
        assert mock_console.print.call_count == 4

    def test_safe_mode_uses_dash_separator(self):
        mock_console = MagicMock()
        with (
            patch("navig.ui.timeline.console", mock_console),
            patch("navig.ui.timeline.SAFE_MODE", True),
            patch("navig.ui.timeline.COLOR_STYLE", {}),
        ):
            render_event_timeline([self._event()], show_title=False)
        args = mock_console.print.call_args[0][0]
        assert "-" in args

    def test_non_safe_mode_uses_em_dash(self):
        mock_console = MagicMock()
        with (
            patch("navig.ui.timeline.console", mock_console),
            patch("navig.ui.timeline.SAFE_MODE", False),
            patch("navig.ui.timeline.COLOR_STYLE", {}),
        ):
            render_event_timeline([self._event()], show_title=False)
        args = mock_console.print.call_args[0][0]
        assert "—" in args

    def test_never_raises_on_exception(self):
        with patch("navig.ui.timeline.console", side_effect=Exception("boom")):
            render_event_timeline([self._event()])  # should not raise

    def test_default_title_is_timeline(self):
        mock_console = MagicMock()
        with (
            patch("navig.ui.timeline.console", mock_console),
            patch("navig.ui.timeline.SAFE_MODE", False),
            patch("navig.ui.timeline.COLOR_STYLE", {}),
        ):
            render_event_timeline([self._event()], show_title=True)
        first_arg = mock_console.print.call_args_list[0][0][0]
        assert "Timeline" in first_arg


# ---------------------------------------------------------------------------
# navig/agent/skill_drafter.py
# ---------------------------------------------------------------------------

from navig.agent.skill_drafter import SkillDraft, SkillDrafter


class TestSkillDraft:
    def test_fields(self):
        d = SkillDraft(name="my-skill", safe=True, yaml_text="name: my-skill\n")
        assert d.name == "my-skill"
        assert d.safe is True
        assert "my-skill" in d.yaml_text

    def test_unsafe_flag(self):
        d = SkillDraft(name="rm-all", safe=False, yaml_text="name: rm-all\n")
        assert d.safe is False


class _FakePattern:
    def __init__(self, sequence=()):
        self.sequence = sequence


class TestSkillDrafter:
    def test_default_output_dir_contains_skills(self):
        drafter = SkillDrafter()
        assert "skills" in str(drafter.output_dir)

    def test_custom_output_dir(self, tmp_path):
        drafter = SkillDrafter(output_dir=tmp_path)
        assert drafter.output_dir == tmp_path

    def test_draft_with_sequence(self):
        drafter = SkillDrafter()
        pattern = _FakePattern(sequence=["ls -la"])
        draft = drafter.draft(pattern)
        assert draft.name == "ls-la"
        assert "ls -la" in draft.yaml_text

    def test_draft_safe_flag_true_for_safe_cmd(self):
        drafter = SkillDrafter()
        draft = drafter.draft(_FakePattern(sequence=["git status"]))
        assert draft.safe is True

    def test_draft_safe_flag_false_for_rm_force(self):
        drafter = SkillDrafter()
        draft = drafter.draft(_FakePattern(sequence=["rm --force /tmp/x"]))
        assert draft.safe is False

    def test_draft_safe_flag_false_for_drop(self):
        drafter = SkillDrafter()
        draft = drafter.draft(_FakePattern(sequence=["drop table users"]))
        assert draft.safe is False

    def test_draft_safe_flag_false_for_truncate(self):
        drafter = SkillDrafter()
        draft = drafter.draft(_FakePattern(sequence=["truncate logs"]))
        assert draft.safe is False

    def test_draft_empty_sequence_uses_fallback_command(self):
        drafter = SkillDrafter()
        draft = drafter.draft(_FakePattern(sequence=[]))
        assert draft.name == "command"

    def test_draft_none_sequence_uses_fallback(self):
        drafter = SkillDrafter()
        pattern = _FakePattern()
        pattern.sequence = None
        draft = drafter.draft(pattern)
        assert draft.name == "command"

    def test_draft_yaml_contains_header(self):
        drafter = SkillDrafter()
        draft = drafter.draft(_FakePattern(sequence=["echo hi"]))
        assert "name:" in draft.yaml_text
        assert "steps:" in draft.yaml_text

    def test_draft_returns_skill_draft_instance(self):
        drafter = SkillDrafter()
        draft = drafter.draft(_FakePattern(sequence=["ls"]))
        assert isinstance(draft, SkillDraft)

    def test_apply_writes_file(self, tmp_path):
        drafter = SkillDrafter(output_dir=tmp_path)
        draft = SkillDraft(name="test-skill", safe=True, yaml_text="name: test-skill\n")
        with patch("navig.agent.skill_drafter.atomic_write_text") as mock_write:
            result = drafter.apply(draft)
        mock_write.assert_called_once()
        assert "test-skill.yaml" in str(result)

    def test_apply_creates_output_dir(self, tmp_path):
        nested = tmp_path / "a" / "b" / "skills"
        drafter = SkillDrafter(output_dir=nested)
        draft = SkillDraft(name="s", safe=True, yaml_text="name: s\n")
        with patch("navig.agent.skill_drafter.atomic_write_text"):
            drafter.apply(draft)
        assert nested.exists()

    def test_apply_returns_path(self, tmp_path):
        drafter = SkillDrafter(output_dir=tmp_path)
        draft = SkillDraft(name="skill-x", safe=True, yaml_text="x")
        with patch("navig.agent.skill_drafter.atomic_write_text"):
            path = drafter.apply(draft)
        assert path == tmp_path / "skill-x.yaml"

    def test_slugify_basic(self):
        assert SkillDrafter._slugify("ls -la") == "ls-la"

    def test_slugify_special_chars(self):
        result = SkillDrafter._slugify("hello.world foo")
        assert result == "hello-world-foo"

    def test_slugify_truncates_at_64(self):
        result = SkillDrafter._slugify("a" * 100)
        assert len(result) == 64

    def test_slugify_uppercase_lowercased(self):
        result = SkillDrafter._slugify("GIT STATUS")
        assert result == result.lower()

    def test_slugify_strips_leading_trailing_dashes(self):
        result = SkillDrafter._slugify("  --cmd--  ")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_slugify_empty_string(self):
        result = SkillDrafter._slugify("")
        assert result == ""
