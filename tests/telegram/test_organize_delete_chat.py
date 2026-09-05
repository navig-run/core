"""`organize.delete_chat` removes a chat for everyone — so its guards are the feature.

Deleting a supergroup cannot be undone on Telegram's side. These tests pin the three
things that keep that survivable: it is dry-run by default, it reports the resolved
*title* (not just the id you typed), and ``expect_title`` refuses to fire when the id
resolves to a chat you did not mean.

They also pin the string-id coercion in ``resolve_entity``: numeric chat ids arrive from
the CLI as strings, and Telethon reads a string as a username — so ``"-100…"`` raised
"Cannot find any entity" while the identical ``int`` worked.
"""

from __future__ import annotations

import pytest

from navig.telegram import media, organize


class _Ent:
    def __init__(self, id=1736302429, title="🎵 M:\\music\\edit", participants_count=2):
        self.id = id
        self.title = title
        self.participants_count = participants_count


class _FakeClient:
    def __init__(self, ent=None):
        self.ent = ent or _Ent()
        self.requests: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get_entity(self, ref):
        return self.ent

    async def get_dialogs(self):
        return []

    async def __call__(self, request):
        self.requests.append(request)
        return None


def _patch(monkeypatch, ent=None) -> _FakeClient:
    fake = _FakeClient(ent)
    monkeypatch.setattr(organize, "UserClient", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_dry_run_by_default_and_deletes_nothing(monkeypatch):
    fake = _patch(monkeypatch)
    res = await organize.delete_chat("-1001736302429")
    assert res["dry_run"] is True
    assert res.get("deleted") is not True
    assert fake.requests == []          # nothing was sent to Telegram


@pytest.mark.asyncio
async def test_dry_run_reports_title_and_members_so_you_confirm_a_name(monkeypatch):
    """An id is easy to mistype; a title is not. The preview must name the chat."""
    _patch(monkeypatch, _Ent(title="🎵 M:\\music\\edit", participants_count=2))
    res = await organize.delete_chat("-1001736302429")
    assert res["title"] == "🎵 M:\\music\\edit"
    assert res["members"] == 2
    assert res["id"] == 1736302429


@pytest.mark.asyncio
async def test_confirm_issues_the_delete_request(monkeypatch):
    fake = _patch(monkeypatch)
    res = await organize.delete_chat("-1001736302429", confirm=True)
    assert res["deleted"] is True
    assert len(fake.requests) == 1
    assert type(fake.requests[0]).__name__ == "DeleteChannelRequest"


@pytest.mark.asyncio
async def test_expect_title_mismatch_refuses_before_deleting(monkeypatch):
    """The fuse: a right-looking id that resolves to the wrong chat must not delete."""
    fake = _patch(monkeypatch, _Ent(title="Family photos"))
    with pytest.raises(ValueError, match="does not match expected"):
        await organize.delete_chat("-1001736302429", confirm=True,
                                   expect_title="🎵 M:\\music\\edit")
    assert fake.requests == []


@pytest.mark.asyncio
async def test_expect_title_match_allows_the_delete(monkeypatch):
    fake = _patch(monkeypatch, _Ent(title="🎵 M:\\music\\edit"))
    res = await organize.delete_chat("x", confirm=True, expect_title="🎵 M:\\music\\edit")
    assert res["deleted"] is True
    assert len(fake.requests) == 1


# ── resolve_entity: the string-vs-int chat id bug ───────────────────────────
class _StrictClient:
    """Telethon's real behaviour: a str is looked up as a username and fails."""

    def __init__(self):
        self.seen: list = []
        self.dialogs_warmed = 0

    async def get_entity(self, ref):
        self.seen.append(ref)
        if isinstance(ref, str):
            raise ValueError(f"Cannot find any entity corresponding to {ref!r}")
        return _Ent(id=ref if isinstance(ref, int) else 1)

    async def get_dialogs(self):
        self.dialogs_warmed += 1
        return []


@pytest.mark.asyncio
async def test_resolve_entity_coerces_a_numeric_string_chat_id():
    c = _StrictClient()
    ent = await media.resolve_entity(c, "-1001736302429")
    assert ent.id == -1001736302429
    assert c.seen == [-1001736302429]            # coerced on the FIRST attempt
    assert c.dialogs_warmed == 0                  # no needless dialog warm-up


@pytest.mark.asyncio
async def test_resolve_entity_still_accepts_a_username():
    c = _StrictClient()
    with pytest.raises(ValueError):
        await media.resolve_entity(c, "@somechannel")
    assert c.dialogs_warmed == 1                  # warmed the cache before giving up


@pytest.mark.asyncio
async def test_resolve_entity_accepts_a_plain_int():
    c = _StrictClient()
    ent = await media.resolve_entity(c, 1736302429)
    assert ent.id == 1736302429
