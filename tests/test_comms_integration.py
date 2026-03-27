"""Integration tests for the unified comms + identity stack."""

import pytest

from navig.comms import dispatch
from navig.comms.types import DeliveryResult, FanoutResult, NotificationTarget
from navig.identity.models import SocialLink, UserProfile
from navig.identity.store import IdentityStore

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_db(tmp_path):
    return tmp_path / "test_identity.db"


@pytest.fixture()
def store(tmp_db):
    return IdentityStore(tmp_db)


# ── Identity Store Tests ──────────────────────────────────────────────


class TestIdentityStore:
    def test_get_nonexistent(self, store):
        assert store.get(999999) is None

    def test_get_or_create(self, store):
        p = store.get_or_create(12345, username="testuser")
        assert p.telegram_id == 12345
        assert p.username == "testuser"
        assert p.preferred_channel == "telegram"

    def test_save_and_retrieve(self, store):
        profile = UserProfile(
            telegram_id=42,
            username="deep_thought",
            display_name="Deep Thought",
            ton_wallet_address="UQabc123",
            ton_verified=True,
            preferred_channel="both",
            matrix_user_id="@deep:navig.local",
            socials=[SocialLink(platform="github", handle="deep42")],
        )
        store.save(profile)
        got = store.get(42)
        assert got is not None
        assert got.ton_wallet_address == "UQabc123"
        assert got.ton_verified is True
        assert got.preferred_channel == "both"
        assert got.matrix_user_id == "@deep:navig.local"
        assert len(got.socials) == 1
        assert got.socials[0].platform == "github"

    def test_delete(self, store):
        store.get_or_create(99)
        assert store.delete(99) is True
        assert store.get(99) is None
        assert store.delete(99) is False

    def test_list_all(self, store):
        for i in range(5):
            store.get_or_create(i + 1, username=f"user{i}")
        all_profiles = store.list_all()
        assert len(all_profiles) == 5

    def test_search_by_wallet(self, store):
        p = UserProfile(telegram_id=77, ton_wallet_address="UQwallet77")
        store.save(p)
        found = store.search_by_wallet("UQwallet77")
        assert found is not None
        assert found.telegram_id == 77

    def test_count(self, store):
        assert store.count() == 0
        store.get_or_create(1)
        store.get_or_create(2)
        assert store.count() == 2


# ── Comms Dispatch Tests (stubbed backends) ───────────────────────────


class FakeTelegramNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, notification):
        self.sent.append(notification)


class TestCommsDispatch:
    def setup_method(self):
        self.fake_tg = FakeTelegramNotifier()
        dispatch.configure(
            telegram_notifier=self.fake_tg,
            matrix_notifier=None,
            default_channel="telegram",
        )

    @pytest.mark.asyncio
    async def test_send_telegram(self):
        target = NotificationTarget.telegram(chat_id=123)
        result = await dispatch.send_user_notification("telegram", target, "Hello test")
        assert isinstance(result, DeliveryResult)
        assert result.ok
        assert result.channel == "telegram"

    @pytest.mark.asyncio
    async def test_send_none(self):
        target = NotificationTarget(telegram_chat_id=123)
        result = await dispatch.send_user_notification("none", target, "noop")
        assert result.ok
        assert result.channel == "none"

    @pytest.mark.asyncio
    async def test_send_matrix_not_configured(self):
        target = NotificationTarget.matrix(room_id="!room:test")
        result = await dispatch.send_user_notification("matrix", target, "hello matrix")
        assert not result.ok
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_fanout_partial(self):
        target = NotificationTarget(telegram_chat_id=123, matrix_room_id="!r:t")
        result = await dispatch.send_user_notification("both", target, "broadcast")
        assert isinstance(result, FanoutResult)
        # Only telegram succeeds (matrix not configured)
        assert result.any_ok

    @pytest.mark.asyncio
    async def test_auto_resolves_to_default(self):
        target = NotificationTarget.telegram(chat_id=456)
        result = await dispatch.send_user_notification("auto", target, "Auto msg")
        assert result.ok
        assert result.channel == "telegram"


# ── UserProfile Serialization Tests ────────────────────────────────


class TestUserProfileSerialization:
    def test_to_dict_and_back(self):
        p = UserProfile(
            telegram_id=1,
            username="test",
            socials=[SocialLink(platform="x", handle="@test")],
        )
        d = p.to_dict()
        p2 = UserProfile.from_dict(d)
        assert p2.telegram_id == 1
        assert p2.username == "test"
        assert len(p2.socials) == 1
        assert p2.socials[0].handle == "@test"
