"""Unit tests for voice/playback.py and onboarding/boot_anim.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.voice.playback import (
    ASSETS_DIR,
    NotificationSound,
    _resolve_asset,
)
from navig.onboarding import boot_anim as _ba


# ---------------------------------------------------------------------------
# boot_anim constants
# ---------------------------------------------------------------------------

class TestBootAnimConstants:
    def test_hex_chars(self):
        assert _ba._HEX == "0123456789ABCDEF"

    def test_rows_positive(self):
        assert _ba._ROWS > 0

    def test_fps_positive(self):
        assert _ba._FPS > 0

    def test_total_secs_positive(self):
        assert _ba._TOTAL_SECS > 0

    def test_total_frames_computed(self):
        expected = int(_ba._TOTAL_SECS * _ba._FPS)
        assert _ba._TOTAL_F == expected

    def test_act1_end_less_than_act2_end(self):
        assert _ba._ACT1_END < _ba._ACT2_END

    def test_act2_end_less_than_total(self):
        assert _ba._ACT2_END < _ba._TOTAL_F

    def test_min_cols_60(self):
        assert _ba._MIN_COLS == 60

    def test_act1_roughly_25_pct(self):
        ratio = _ba._ACT1_END / _ba._TOTAL_F
        assert 0.20 <= ratio <= 0.30

    def test_act2_roughly_82_pct(self):
        ratio = _ba._ACT2_END / _ba._TOTAL_F
        assert 0.75 <= ratio <= 0.90


# ---------------------------------------------------------------------------
# NotificationSound enum
# ---------------------------------------------------------------------------

class TestNotificationSound:
    def test_has_alarm(self):
        assert NotificationSound.ALARM.value == "alarm-default.mp3"

    def test_has_wake(self):
        assert NotificationSound.WAKE.value == "echo_en_wake.wav"

    def test_has_ok(self):
        assert NotificationSound.OK.value == "echo_en_ok.wav"

    def test_str_subclass(self):
        # NotificationSound extends str, so equality with string should work
        assert NotificationSound.ALARM == "alarm-default.mp3"

    def test_all_values_are_strings(self):
        for sound in NotificationSound:
            assert isinstance(sound.value, str)

    def test_all_values_have_extension(self):
        for sound in NotificationSound:
            assert "." in sound.value


# ---------------------------------------------------------------------------
# ASSETS_DIR
# ---------------------------------------------------------------------------

class TestAssetsDir:
    def test_is_path(self):
        assert isinstance(ASSETS_DIR, Path)

    def test_is_absolute(self):
        assert ASSETS_DIR.is_absolute()


# ---------------------------------------------------------------------------
# _resolve_asset
# ---------------------------------------------------------------------------

class TestResolveAsset:
    def test_unknown_name_returns_none(self):
        result = _resolve_asset("totally-nonexistent-file-xyz.mp3")
        assert result is None

    def test_absolute_path_found(self, tmp_path):
        f = tmp_path / "test.wav"
        f.write_bytes(b"RIFF")
        result = _resolve_asset(str(f))
        assert result == f

    def test_absolute_path_missing_returns_none(self, tmp_path):
        missing = tmp_path / "missing.wav"
        result = _resolve_asset(str(missing))
        assert result is None

    def test_enum_value_lookup_missing_asset(self):
        """Enum value found but file doesn't exist → None."""
        result = _resolve_asset("alarm-default.mp3")
        # File may or may not exist in test env; just ensure no exception
        assert result is None or isinstance(result, Path)

    def test_enum_name_case_insensitive_missing(self):
        """Enum name lookup (ALARM → file); no exception if missing."""
        result = _resolve_asset("alarm")
        assert result is None or isinstance(result, Path)

    def test_returns_path_or_none(self):
        result = _resolve_asset("bogus")
        assert result is None or isinstance(result, Path)
