"""Security: the deck inbox routes only ever operate on files that live in an inbox dir.

A `path:<abs>` event id is client-supplied and the deck API is reachable remotely (Lighthouse).
Without the guard, `route` would read (and with mode=move relocate) an arbitrary file off disk,
`promote` would read one into a plan/wiki, and `skip` would inject a store event a later numeric
`route` acts on. Each handler now rejects a source outside the scanned inbox dirs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


class _Req:
    def __init__(self, event_id: str, body: dict | None = None):
        self.match_info = {"event_id": event_id}
        self._body = body or {}

    async def json(self):
        return self._body


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """A project root with a real wiki inbox; no spaces; a controlled global inbox."""
    import navig.gateway.deck.routes.inbox as inbox

    root = tmp_path / "proj"
    (root / ".navig" / "wiki" / "inbox").mkdir(parents=True)
    monkeypatch.setattr(inbox, "_find_project_root", lambda: root)
    monkeypatch.setattr("navig.spaces.resolver.discover_space_paths", lambda: {})
    monkeypatch.setattr("navig.platform.paths.data_dir", lambda: tmp_path / "data")
    return inbox, root


def test_source_in_inbox_accepts_inbox_and_rejects_outside(sandbox, tmp_path):
    inbox, root = sandbox
    inside = root / ".navig" / "wiki" / "inbox" / "note.md"
    inside.write_text("x", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    traversal = root / ".navig" / "wiki" / "inbox" / ".." / ".." / ".." / "secret.txt"

    assert inbox._source_in_inbox(inside, root) is True
    assert inbox._source_in_inbox(outside, root) is False
    assert inbox._source_in_inbox(traversal, root) is False   # .. escape collapses → outside
    assert inbox._source_in_inbox(None, root) is False


async def test_route_rejects_out_of_inbox_path(sandbox, tmp_path, monkeypatch):
    inbox, _root = sandbox
    called: list = []
    monkeypatch.setattr(inbox, "_route_file", lambda *a, **k: called.append(a) or {"status": "x"})

    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    resp = await inbox.handle_deck_inbox_route(_Req(f"path:{secret}"))

    assert resp.status == 403
    assert called == []  # the arbitrary file was never read/routed


async def test_route_allows_in_inbox_path(sandbox, monkeypatch):
    inbox, root = sandbox
    called: list = []
    # Stub the real routing; a non-routed status skips the success-path emit/reindex.
    monkeypatch.setattr(inbox, "_route_file", lambda *a, **k: called.append(a) or {"status": "skipped"})

    f = root / ".navig" / "wiki" / "inbox" / "note.md"
    f.write_text("hello", encoding="utf-8")
    resp = await inbox.handle_deck_inbox_route(_Req(f"path:{f}"))

    assert resp.status == 200
    assert len(called) == 1  # validation passed → routing invoked


async def test_skip_rejects_out_of_inbox_path(sandbox, tmp_path, monkeypatch):
    inbox, _root = sandbox
    inserted: list = []

    class _FakeStore:
        def insert_event(self, ev):
            inserted.append(ev)

    monkeypatch.setattr("navig.inbox.store.InboxStore", _FakeStore)
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")

    resp = await inbox.handle_deck_inbox_skip(_Req(f"path:{secret}"))
    assert resp.status == 403
    assert inserted == []  # no bogus event recorded for an out-of-inbox file


async def test_promote_rejects_out_of_inbox_path(sandbox, tmp_path):
    inbox, _root = sandbox
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")

    resp = await inbox.handle_deck_inbox_promote(_Req(f"path:{secret}", {"to_tier": "roadmap"}))
    assert resp.status == 403
