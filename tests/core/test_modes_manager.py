"""Tests for modes/manager.py — ModeProfile, registry, PIN helpers."""
from __future__ import annotations

import base64
import hashlib

import pytest

from navig.modes.manager import (
    ModeProfile,
    _DEFAULT_MODE,
    _hash_pin,
    _verify_pin_hash,
    all_modes,
    get_mode,
    has_pin,
    set_pin,
    verify_pin,
)


# ──────────────────────────────────────────────────────────────────────────────
# ModeProfile
# ──────────────────────────────────────────────────────────────────────────────


class TestModeProfile:
    def test_name_stored(self):
        mp = ModeProfile("node", {})
        assert mp.name == "node"

    def test_label_defaults_to_uppercase_name(self):
        mp = ModeProfile("node", {})
        assert mp.label == "NODE"

    def test_label_from_data(self):
        mp = ModeProfile("node", {"label": "Custom"})
        assert mp.label == "Custom"

    def test_model_preference_default(self):
        mp = ModeProfile("x", {})
        assert mp.model_preference == "fast"

    def test_tool_tier_default(self):
        mp = ModeProfile("x", {})
        assert mp.tool_tier == "safe"

    def test_require_auth_default_false(self):
        mp = ModeProfile("x", {})
        assert mp.require_auth is False

    def test_gated_commands_default_empty(self):
        mp = ModeProfile("x", {})
        assert mp.gated_commands == []

    def test_repr_contains_name(self):
        mp = ModeProfile("builder", {"tool_tier": "elevated"})
        assert "builder" in repr(mp)

    def test_full_data(self):
        mp = ModeProfile(
            "architect",
            {
                "label": "ARCHITECT",
                "tool_tier": "privileged",
                "require_auth": True,
                "gated_commands": ["db drop"],
            },
        )
        assert mp.tool_tier == "privileged"
        assert mp.require_auth is True
        assert "db drop" in mp.gated_commands


# ──────────────────────────────────────────────────────────────────────────────
# Mode registry
# ──────────────────────────────────────────────────────────────────────────────


class TestAllModes:
    def test_returns_dict(self):
        result = all_modes()
        assert isinstance(result, dict)

    def test_non_empty(self):
        assert len(all_modes()) > 0

    def test_values_are_mode_profiles(self):
        for name, mp in all_modes().items():
            assert isinstance(mp, ModeProfile), f"{name} is wrong type"

    def test_default_mode_present(self):
        assert _DEFAULT_MODE in all_modes()


class TestGetMode:
    def test_existing_mode(self):
        mp = get_mode(_DEFAULT_MODE)
        assert mp is not None
        assert isinstance(mp, ModeProfile)

    def test_unknown_mode_returns_none(self):
        assert get_mode("totally_nonexistent_mode_xyz") is None

    def test_returns_correct_name(self):
        mp = get_mode(_DEFAULT_MODE)
        assert mp.name == _DEFAULT_MODE


# ──────────────────────────────────────────────────────────────────────────────
# PIN hashing — pure functions
# ──────────────────────────────────────────────────────────────────────────────


class TestHashPin:
    def test_returns_v2_prefix(self):
        result = _hash_pin("1234")
        assert result.startswith("v2:")

    def test_has_three_parts(self):
        result = _hash_pin("1234")
        parts = result.split(":")
        assert len(parts) == 3  # "v2", salt_b64, hash_b64

    def test_salt_is_base64(self):
        _, salt_b64, _ = _hash_pin("1234").split(":")
        decoded = base64.b64decode(salt_b64)
        assert len(decoded) == 16  # _SALT_LEN

    def test_different_calls_produce_different_salts(self):
        h1 = _hash_pin("1234")
        h2 = _hash_pin("1234")
        # same pin → different stored hashes (random salt)
        assert h1 != h2


class TestVerifyPinHash:
    def test_v2_correct_pin(self):
        stored = _hash_pin("5678")
        assert _verify_pin_hash("5678", stored) is True

    def test_v2_wrong_pin(self):
        stored = _hash_pin("5678")
        assert _verify_pin_hash("0000", stored) is False

    def test_v1_legacy_correct_pin(self):
        # v1: unsalted SHA-256 hex
        pin = "9999"
        stored = hashlib.sha256(pin.encode()).hexdigest()
        assert _verify_pin_hash(pin, stored) is True

    def test_v1_legacy_wrong_pin(self):
        pin = "9999"
        stored = hashlib.sha256(pin.encode()).hexdigest()
        assert _verify_pin_hash("1111", stored) is False

    def test_corrupted_v2_returns_false(self):
        assert _verify_pin_hash("1234", "v2:notbase64!:notbase64!") is False

    def test_strips_whitespace_from_pin(self):
        stored = _hash_pin("1234")
        assert _verify_pin_hash("  1234  ", stored) is True


# ──────────────────────────────────────────────────────────────────────────────
# set_pin / has_pin / verify_pin — filesystem-touched, use tmp_path patch
# ──────────────────────────────────────────────────────────────────────────────


class TestSetPinValidation:
    def test_non_digits_raise(self):
        with pytest.raises(ValueError, match="4 digits"):
            set_pin("abcd")

    def test_wrong_length_raise(self):
        with pytest.raises(ValueError):
            set_pin("12")

    def test_five_digits_raise(self):
        with pytest.raises(ValueError):
            set_pin("12345")


class TestPinFilesystem:
    def test_has_pin_false_before_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr("navig.modes.manager._pin_path", lambda: tmp_path / ".mode_pin")
        assert has_pin() is False

    def test_has_pin_true_after_set(self, tmp_path, monkeypatch):
        pin_path = tmp_path / ".mode_pin"
        monkeypatch.setattr("navig.modes.manager._pin_path", lambda: pin_path)
        monkeypatch.setattr("navig.modes.manager._navig_home", lambda: tmp_path)
        set_pin("4321")
        assert has_pin() is True

    def test_verify_pin_correct(self, tmp_path, monkeypatch):
        pin_path = tmp_path / ".mode_pin"
        monkeypatch.setattr("navig.modes.manager._pin_path", lambda: pin_path)
        monkeypatch.setattr("navig.modes.manager._navig_home", lambda: tmp_path)
        set_pin("7777")
        assert verify_pin("7777") is True

    def test_verify_pin_incorrect(self, tmp_path, monkeypatch):
        pin_path = tmp_path / ".mode_pin"
        monkeypatch.setattr("navig.modes.manager._pin_path", lambda: pin_path)
        monkeypatch.setattr("navig.modes.manager._navig_home", lambda: tmp_path)
        set_pin("7777")
        assert verify_pin("1234") is False

    def test_verify_pin_no_file_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr("navig.modes.manager._pin_path", lambda: tmp_path / "absent")
        assert verify_pin("0000") is False
