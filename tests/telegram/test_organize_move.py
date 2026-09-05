"""`organize.move` must delete only the originals that were actually forwarded.

telethon's ``forward_messages`` returns a list aligned to the input ids, with ``None``
where a message couldn't be forwarded (deleted / uncopyable). ``move`` used to delete the
FULL requested id list unconditionally — so any message that failed to forward was still
deleted-for-everyone → permanently lost. These tests pin the fix with a fake client.
"""

from __future__ import annotations

from navig.telegram import organize


class _Msg:
    """Stand-in for a telethon Message (a successfully forwarded result)."""


class _Ent:
    def __init__(self, ref):
        self.id = ref if isinstance(ref, int) else 999


class _FakeClient:
    """Shared fake for every ``async with UserClient()`` in forward()/move()."""

    def __init__(self, forward_return):
        self._forward_return = forward_return
        self.deleted: list[tuple[list, bool]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get_entity(self, ref):
        return _Ent(ref)

    async def forward_messages(self, to, ids, frm, drop_author=False):
        return self._forward_return

    async def delete_messages(self, ent, ids, revoke=False):
        self.deleted.append((list(ids), revoke))


def _patch(monkeypatch, forward_return) -> _FakeClient:
    fake = _FakeClient(forward_return)
    monkeypatch.setattr(organize, "UserClient", lambda: fake)
    return fake


# ── move ──────────────────────────────────────────────────────────────────────


async def test_move_deletes_only_forwarded_on_partial(monkeypatch):
    # 3 requested; the middle id couldn't be forwarded (telethon → None placeholder).
    fake = _patch(monkeypatch, [_Msg(), None, _Msg()])
    res = await organize.move("src", [10, 20, 30], "dst", confirm=True)

    assert fake.deleted == [([10, 30], True)]  # 20 KEPT, not deleted
    assert res["moved"] == 2
    assert res["deleted"] == 2
    assert res["requested"] == 3
    assert res["skipped"] == 1
    assert "left in place" in res["note"]


async def test_move_all_forwarded_deletes_all(monkeypatch):
    fake = _patch(monkeypatch, [_Msg(), _Msg(), _Msg()])
    res = await organize.move("src", [10, 20, 30], "dst", confirm=True)

    assert fake.deleted == [([10, 20, 30], True)]
    assert res["moved"] == 3
    assert res["deleted"] == 3
    assert "skipped" not in res


async def test_move_none_forwarded_deletes_nothing(monkeypatch):
    # Degenerate all-None (telethon normally raises here, but move must be safe regardless).
    fake = _patch(monkeypatch, [None, None])
    res = await organize.move("src", [10, 20], "dst", confirm=True)

    assert fake.deleted == []  # nothing deleted
    assert res["moved"] == 0
    assert res["deleted"] == 0
    assert res["skipped"] == 2


async def test_move_dry_run_forwards_and_deletes_nothing(monkeypatch):
    fake = _patch(monkeypatch, [_Msg()])
    res = await organize.move("src", [10], "dst", confirm=False)

    assert res["dry_run"] is True
    assert fake.deleted == []


# ── forward count ──────────────────────────────────────────────────────────────


async def test_forward_count_excludes_none_and_reports_ids(monkeypatch):
    _patch(monkeypatch, [_Msg(), None, _Msg()])
    res = await organize.forward("src", [10, 20, 30], "dst")

    assert res["forwarded"] == 2  # was 3 (len incl. None) before the fix
    assert res["forwarded_ids"] == [10, 30]
    assert res["requested"] == 3


async def test_forward_single_message_normalized(monkeypatch):
    # telethon returns a single Message (not a list) for a single input.
    _patch(monkeypatch, _Msg())
    res = await organize.forward("src", [10], "dst")

    assert res["forwarded"] == 1
    assert res["forwarded_ids"] == [10]
