"""Batch 70 — blackbox/timeline, identity/models, comms/types."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.blackbox.timeline — format_event_summary, render_timeline
# ---------------------------------------------------------------------------

def _make_event(event_type, payload, source="navig"):
    from navig.blackbox.types import BlackboxEvent
    return BlackboxEvent(
        id="test001",
        event_type=event_type,
        timestamp=datetime.now(timezone.utc),
        payload=payload,
        source=source,
    )


class TestFormatEventSummary:
    def test_command_event(self):
        from navig.blackbox.timeline import format_event_summary
        from navig.blackbox.types import EventType
        event = _make_event(EventType.COMMAND, {"command": "navig", "args": "run ls"})
        result = format_event_summary(event)
        assert "navig" in result
        assert "run ls" in result

    def test_command_fallback_when_empty(self):
        from navig.blackbox.timeline import format_event_summary
        from navig.blackbox.types import EventType
        event = _make_event(EventType.COMMAND, {})
        assert format_event_summary(event) == "(unknown command)"

    def test_crash_event(self):
        from navig.blackbox.timeline import format_event_summary
        from navig.blackbox.types import EventType
        event = _make_event(EventType.CRASH, {"exception_type": "ValueError", "exception_msg": "oops"})
        result = format_event_summary(event)
        assert "ValueError" in result
        assert "oops" in result

    def test_crash_event_no_msg(self):
        from navig.blackbox.timeline import format_event_summary
        from navig.blackbox.types import EventType
        event = _make_event(EventType.CRASH, {"exception_type": "KeyError"})
        result = format_event_summary(event)
        assert "KeyError" in result

    def test_error_event(self):
        from navig.blackbox.timeline import format_event_summary
        from navig.blackbox.types import EventType
        event = _make_event(EventType.ERROR, {"message": "Something went wrong"})
        result = format_event_summary(event)
        assert "Something went wrong" in result

    def test_warning_event(self):
        from navig.blackbox.timeline import format_event_summary
        from navig.blackbox.types import EventType
        event = _make_event(EventType.WARNING, {"message": "Config deprecated"})
        result = format_event_summary(event)
        assert "Config deprecated" in result

    def test_session_event(self):
        from navig.blackbox.timeline import format_event_summary
        from navig.blackbox.types import EventType
        event = _make_event(EventType.SESSION, {"action": "start"})
        result = format_event_summary(event)
        assert "Session" in result
        assert "start" in result

    def test_output_event_first_line(self):
        from navig.blackbox.timeline import format_event_summary
        from navig.blackbox.types import EventType
        event = _make_event(EventType.OUTPUT, {"stdout": "first line\nsecond line"})
        result = format_event_summary(event)
        assert "first line" in result

    def test_system_event(self):
        from navig.blackbox.timeline import format_event_summary
        from navig.blackbox.types import EventType
        event = _make_event(EventType.SYSTEM, {"message": "System started"})
        result = format_event_summary(event)
        assert "System started" in result

    def test_long_output_truncated_to_120(self):
        from navig.blackbox.timeline import format_event_summary
        from navig.blackbox.types import EventType
        long_msg = "x" * 200
        event = _make_event(EventType.ERROR, {"message": long_msg})
        result = format_event_summary(event)
        assert len(result) <= 120


class TestRenderTimeline:
    def test_empty_events_prints_no_events(self):
        from navig.blackbox.timeline import render_timeline
        mock_console = MagicMock()
        render_timeline([], console=mock_console)
        mock_console.print.assert_called_once()
        call_str = str(mock_console.print.call_args)
        assert "No blackbox events" in call_str

    def test_renders_table_with_events(self):
        from navig.blackbox.timeline import render_timeline
        from navig.blackbox.types import EventType
        events = [_make_event(EventType.COMMAND, {"command": "test"})]
        mock_console = MagicMock()
        render_timeline(events, console=mock_console)
        mock_console.print.assert_called_once()

    def test_limit_respected(self):
        from navig.blackbox.timeline import render_timeline
        from navig.blackbox.types import EventType
        events = [_make_event(EventType.SYSTEM, {"message": f"m{i}"}) for i in range(10)]
        mock_console = MagicMock()
        render_timeline(events, limit=3, console=mock_console)
        # Table should render (not the "no events" msg)
        mock_console.print.assert_called_once()


# ---------------------------------------------------------------------------
# navig.identity.models — SocialLink, UserProfile
# ---------------------------------------------------------------------------

class TestSocialLink:
    def test_defaults(self):
        from navig.identity.models import SocialLink
        s = SocialLink(platform="github", handle="user123")
        assert s.verified is False

    def test_verified_flag(self):
        from navig.identity.models import SocialLink
        s = SocialLink(platform="twitter", handle="@me", verified=True)
        assert s.verified is True


class TestUserProfile:
    def _make_profile(self, telegram_id=42):
        from navig.identity.models import UserProfile
        return UserProfile(telegram_id=telegram_id, username="testuser")

    def test_to_dict_includes_telegram_id(self):
        p = self._make_profile(42)
        d = p.to_dict()
        assert d["telegram_id"] == 42

    def test_to_dict_includes_username(self):
        p = self._make_profile()
        d = p.to_dict()
        assert d["username"] == "testuser"

    def test_to_dict_includes_socials_list(self):
        from navig.identity.models import SocialLink, UserProfile
        p = UserProfile(telegram_id=1, socials=[SocialLink("github", "gh_user")])
        d = p.to_dict()
        assert isinstance(d["socials"], list)
        assert d["socials"][0]["platform"] == "github"

    def test_to_dict_has_timestamps(self):
        p = self._make_profile()
        d = p.to_dict()
        assert "created_at" in d
        assert "updated_at" in d

    def test_from_dict_roundtrip(self):
        from navig.identity.models import UserProfile
        p = UserProfile(telegram_id=99, username="roundtrip", language="fr")
        d = p.to_dict()
        restored = UserProfile.from_dict(d)
        assert restored.telegram_id == 99
        assert restored.username == "roundtrip"
        assert restored.language == "fr"

    def test_from_dict_missing_optional_fields(self):
        from navig.identity.models import UserProfile
        d = {"telegram_id": 1}
        p = UserProfile.from_dict(d)
        assert p.telegram_id == 1
        assert p.username is None
        assert p.language == "en"

    def test_defaults(self):
        from navig.identity.models import UserProfile
        p = UserProfile(telegram_id=1)
        assert p.preferred_channel == "telegram"
        assert p.language == "en"


# ---------------------------------------------------------------------------
# navig.comms.types — NotificationTarget, DeliveryPriority, etc.
# ---------------------------------------------------------------------------

class TestNotificationTarget:
    def test_telegram_factory(self):
        from navig.comms.types import NotificationTarget
        t = NotificationTarget.telegram(123)
        assert t.telegram_chat_id == 123

    def test_matrix_factory(self):
        from navig.comms.types import NotificationTarget
        t = NotificationTarget.matrix("!room:example.com")
        assert t.matrix_room_id == "!room:example.com"

    def test_auto_factory(self):
        from navig.comms.types import NotificationTarget
        t = NotificationTarget.auto("user_99")
        assert t.user_id == "user_99"


class TestDeliveryPriority:
    def test_values(self):
        from navig.comms.types import DeliveryPriority
        assert DeliveryPriority.LOW.value == "low"
        assert DeliveryPriority.CRITICAL.value == "critical"


class TestNotificationOptions:
    def test_defaults(self):
        from navig.comms.types import NotificationOptions, DeliveryPriority
        o = NotificationOptions()
        assert o.priority == DeliveryPriority.NORMAL
        assert o.silent is False
        assert o.retry_count == 2
        assert o.parse_mode == "HTML"


class TestDeliveryResult:
    def test_ok_true(self):
        from navig.comms.types import DeliveryResult
        r = DeliveryResult(ok=True, channel="telegram")
        assert r.ok is True
        assert r.error is None

    def test_ok_false_with_error(self):
        from navig.comms.types import DeliveryResult
        r = DeliveryResult(ok=False, channel="telegram", error="timeout")
        assert r.ok is False
        assert r.error == "timeout"

    def test_timestamp_set_automatically(self):
        from navig.comms.types import DeliveryResult
        r = DeliveryResult(ok=True, channel="matrix")
        assert r.timestamp is not None
