"""Hermetic unit tests for navig.identity.models (SocialLink, UserProfile)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

# ---------------------------------------------------------------------------
# SocialLink
# ---------------------------------------------------------------------------


class TestSocialLink:
    def test_basic_construction(self):
        from navig.identity.models import SocialLink

        sl = SocialLink(platform="twitter", handle="@user")
        assert sl.platform == "twitter"
        assert sl.handle == "@user"

    def test_verified_defaults_to_false(self):
        from navig.identity.models import SocialLink

        sl = SocialLink(platform="github", handle="gh_user")
        assert sl.verified is False

    def test_verified_can_be_set_true(self):
        from navig.identity.models import SocialLink

        sl = SocialLink(platform="github", handle="gh_user", verified=True)
        assert sl.verified is True

    def test_linked_at_has_default(self):
        from navig.identity.models import SocialLink

        sl = SocialLink(platform="discord", handle="disc#1234")
        assert sl.linked_at is not None


# ---------------------------------------------------------------------------
# UserProfile defaults
# ---------------------------------------------------------------------------


class TestUserProfileDefaults:
    def test_required_field_telegram_id(self):
        from navig.identity.models import UserProfile

        up = UserProfile(telegram_id=123456789)
        assert up.telegram_id == 123456789

    def test_language_defaults_to_en(self):
        from navig.identity.models import UserProfile

        up = UserProfile(telegram_id=1)
        assert up.language == "en"

    def test_preferred_channel_defaults_to_telegram(self):
        from navig.identity.models import UserProfile

        up = UserProfile(telegram_id=1)
        assert up.preferred_channel == "telegram"

    def test_ton_verified_defaults_to_false(self):
        from navig.identity.models import UserProfile

        up = UserProfile(telegram_id=1)
        assert up.ton_verified is False

    def test_socials_defaults_to_empty_list(self):
        from navig.identity.models import UserProfile

        up = UserProfile(telegram_id=1)
        assert up.socials == []

    def test_optional_fields_default_to_none(self):
        from navig.identity.models import UserProfile

        up = UserProfile(telegram_id=1)
        assert up.username is None
        assert up.display_name is None
        assert up.ton_wallet_address is None
        assert up.matrix_user_id is None
        assert up.timezone is None

    def test_metadata_defaults_to_empty_dict(self):
        from navig.identity.models import UserProfile

        up = UserProfile(telegram_id=1)
        assert up.metadata == {}


# ---------------------------------------------------------------------------
# UserProfile.to_dict
# ---------------------------------------------------------------------------


class TestUserProfileToDict:
    def test_contains_telegram_id(self):
        from navig.identity.models import UserProfile

        up = UserProfile(telegram_id=999)
        d = up.to_dict()
        assert d["telegram_id"] == 999

    def test_socials_is_list_of_dicts(self):
        from navig.identity.models import SocialLink, UserProfile

        up = UserProfile(
            telegram_id=1,
            socials=[SocialLink(platform="twitter", handle="tw_user", verified=True)],
        )
        d = up.to_dict()
        assert isinstance(d["socials"], list)
        assert len(d["socials"]) == 1
        sl_dict = d["socials"][0]
        assert sl_dict["platform"] == "twitter"
        assert sl_dict["handle"] == "tw_user"
        assert sl_dict["verified"] is True

    def test_timestamps_are_iso_strings(self):
        from navig.identity.models import UserProfile

        up = UserProfile(telegram_id=1)
        d = up.to_dict()
        # Should be parseable ISO strings
        datetime.fromisoformat(d["created_at"])
        datetime.fromisoformat(d["updated_at"])

    def test_all_optional_fields_included(self):
        from navig.identity.models import UserProfile

        up = UserProfile(
            telegram_id=42,
            username="jdoe",
            display_name="John Doe",
            language="fr",
            timezone="Europe/Paris",
        )
        d = up.to_dict()
        assert d["username"] == "jdoe"
        assert d["display_name"] == "John Doe"
        assert d["language"] == "fr"
        assert d["timezone"] == "Europe/Paris"


# ---------------------------------------------------------------------------
# UserProfile.from_dict (round-trip)
# ---------------------------------------------------------------------------


class TestUserProfileFromDict:
    def test_round_trip_minimal(self):
        from navig.identity.models import UserProfile

        up = UserProfile(telegram_id=777)
        restored = UserProfile.from_dict(up.to_dict())
        assert restored.telegram_id == up.telegram_id
        assert restored.language == up.language
        assert restored.preferred_channel == up.preferred_channel

    def test_round_trip_with_socials(self):
        from navig.identity.models import SocialLink, UserProfile

        up = UserProfile(
            telegram_id=10,
            socials=[SocialLink(platform="github", handle="gh_user", verified=False)],
        )
        restored = UserProfile.from_dict(up.to_dict())
        assert len(restored.socials) == 1
        sl = restored.socials[0]
        assert sl.platform == "github"
        assert sl.handle == "gh_user"
        assert sl.verified is False

    def test_round_trip_full(self):
        from navig.identity.models import SocialLink, UserProfile

        up = UserProfile(
            telegram_id=55,
            username="alice",
            display_name="Alice",
            ton_wallet_address="EQ123",
            ton_verified=True,
            socials=[SocialLink(platform="discord", handle="alice#0001", verified=True)],
            preferred_channel="matrix",
            matrix_user_id="@alice:matrix.org",
            language="de",
            timezone="Europe/Berlin",
        )
        d = up.to_dict()
        restored = UserProfile.from_dict(d)
        assert restored.username == "alice"
        assert restored.ton_verified is True
        assert restored.matrix_user_id == "@alice:matrix.org"
        assert restored.language == "de"
        assert restored.timezone == "Europe/Berlin"

    def test_from_dict_missing_optional_keys_use_defaults(self):
        from navig.identity.models import UserProfile

        d = {"telegram_id": 321}
        up = UserProfile.from_dict(d)
        assert up.telegram_id == 321
        assert up.language == "en"
        assert up.socials == []
        assert up.metadata == {}

    def test_from_dict_socials_reconstructed_as_social_link_objects(self):
        from navig.identity.models import SocialLink, UserProfile

        d = {
            "telegram_id": 1,
            "socials": [{"platform": "twitter", "handle": "t_user", "verified": True}],
        }
        up = UserProfile.from_dict(d)
        assert len(up.socials) == 1
        assert isinstance(up.socials[0], SocialLink)
        assert up.socials[0].verified is True

    def test_timestamps_parsed_from_iso_strings(self):
        from navig.identity.models import UserProfile

        d = {
            "telegram_id": 1,
            "created_at": "2024-01-15T12:00:00",
            "updated_at": "2024-06-01T08:30:00",
        }
        up = UserProfile.from_dict(d)
        assert isinstance(up.created_at, datetime)
        assert up.created_at.year == 2024
