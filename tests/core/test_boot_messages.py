"""Tests for navig.boot_messages — NAVIG_BOOT_MESSAGES list and get_boot_message."""

from __future__ import annotations


class TestNaviqBootMessages:
    def test_is_non_empty_list(self):
        from navig.boot_messages import NAVIG_BOOT_MESSAGES

        assert isinstance(NAVIG_BOOT_MESSAGES, list)
        assert len(NAVIG_BOOT_MESSAGES) > 0

    def test_all_items_are_non_empty_strings(self):
        from navig.boot_messages import NAVIG_BOOT_MESSAGES

        for msg in NAVIG_BOOT_MESSAGES:
            assert isinstance(msg, str) and msg.strip()


class TestGetBootMessage:
    def _get(self, **kwargs):
        from navig.boot_messages import get_boot_message

        return get_boot_message(**kwargs)

    def test_returns_string(self):
        assert isinstance(self._get(), str)

    def test_base_message_is_from_list(self):
        from navig.boot_messages import NAVIG_BOOT_MESSAGES

        base_messages = set(NAVIG_BOOT_MESSAGES)
        for _ in range(20):
            msg = self._get()
            # The returned message either IS a base message or starts with one
            assert any(msg.startswith(b) for b in base_messages), f"Unexpected: {msg!r}"

    def test_no_extras_adds_no_suffix(self):
        from navig.boot_messages import NAVIG_BOOT_MESSAGES

        msg = self._get()
        # Without extras, the message equals one of the base messages exactly
        assert msg in NAVIG_BOOT_MESSAGES

    def test_location_appended(self):
        msg = self._get(location="48.8566° N")
        assert "Position: 48.8566° N." in msg

    def test_uptime_appended(self):
        msg = self._get(uptime=3600)
        assert "Last session: 3600s." in msg

    def test_signal_strength_appended(self):
        msg = self._get(signal_strength=99)
        assert "Signal: 99%." in msg

    def test_all_extras_combined(self):
        msg = self._get(location="loc", uptime=0, signal_strength=50)
        assert "Position: loc." in msg
        assert "Last session: 0s." in msg
        assert "Signal: 50%." in msg

    def test_uptime_zero_is_included(self):
        # uptime=0 is falsy but should still be appended (not None)
        msg = self._get(uptime=0)
        assert "Last session: 0s." in msg

    def test_randomness_over_time(self):
        # Over many calls, we should see at least 2 different messages
        # (with 10 messages, chance of getting same in 30 calls is ~(1/10)^29)
        msgs = {self._get() for _ in range(30)}
        assert len(msgs) > 1
