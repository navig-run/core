"""`GET /api/deck/inbox` carries what triage needs: `content_hash` and `url`.

Triage (Deck + OS) groups duplicate captures client-side; without these two fields the
list gives it nothing to group by, and the hash only exists on the history rows (half
the contract) — so both the live scan and the store history are asserted here.
"""

from __future__ import annotations

import hashlib
import json

import pytest

pytestmark = pytest.mark.integration


class _ListReq:
    def __init__(self, query: dict | None = None):
        self.rel_url = type("U", (), {"query": query or {}})()


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """A project wiki inbox with captures; spaces and the global inbox off this tmp tree."""
    import navig.gateway.deck.routes.inbox as inbox

    root = tmp_path / "proj"
    (root / ".navig" / "wiki" / "inbox").mkdir(parents=True)
    monkeypatch.setattr(inbox, "_find_project_root", lambda: root)
    monkeypatch.setattr("navig.spaces.resolver.discover_space_paths", lambda: {})
    monkeypatch.setattr("navig.platform.paths.data_dir", lambda: tmp_path / "data")
    return inbox, root


async def _rows(inbox) -> list[dict]:
    resp = await inbox.handle_deck_inbox_list(_ListReq())
    assert resp.status == 200
    # Server-side Response: the payload is bytes (`.json()` is a CLIENT method).
    return json.loads(resp.body)["data"]["events"]


async def test_scan_payload(sandbox):
    inbox, root = sandbox
    inbox_dir = root / ".navig" / "wiki" / "inbox"
    v1 = "---\nurl: https://github.com/navig-run/labs/pull/7\nkind: page\ntitle: PR 7\n---\n\n# PR 7\n\nBody.\n"
    (inbox_dir / "capture-v1.md").write_text(v1, encoding="utf-8")
    legacy = "# Legacy link\n\nSource: https://www.youtube.com/watch?v=dQw4w9WgXcQ\n\nWatch this.\n"
    (inbox_dir / "legacy-link.md").write_text(legacy, encoding="utf-8")
    (inbox_dir / "plain.md").write_text("# Just a thought\n\nText.\n", encoding="utf-8")

    rows = await _rows(inbox)
    by_name = {r["filename"]: r for r in rows if r["filename"].endswith(".md")}
    assert {"capture-v1.md", "legacy-link.md", "plain.md"} <= set(by_name)

    v1_row = by_name["capture-v1.md"]
    assert v1_row["url"] == "https://github.com/navig-run/labs/pull/7", "v1 frontmatter url"
    assert len(v1_row["content_hash"]) == 16 and int(v1_row["content_hash"], 16) >= 0
    # The hash must be content-derived: an edited capture changes it.
    expect = hashlib.sha256(v1.encode()).hexdigest()[:16]
    assert v1_row["content_hash"] == expect

    assert by_name["legacy-link.md"]["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert by_name["plain.md"]["url"] is None, "a thought with no source has no url"

    # Two identical files hash identically; an edited one does not.
    (inbox_dir / "copy.md").write_text(v1, encoding="utf-8")
    rows2 = await _rows(inbox)
    names = {r["filename"]: r["content_hash"] for r in rows2 if r["filename"].endswith(".md")}
    assert names["copy.md"] == names["capture-v1.md"]
    (inbox_dir / "copy.md").write_text(v1 + "\nedited\n", encoding="utf-8")
    rows3 = await _rows(inbox)
    assert {r["filename"]: r["content_hash"] for r in rows3}["copy.md"] != names["capture-v1.md"]

    # Rows keep their newest-first order (triage assumes it).
    stamps = [r["created_at"] for r in rows if r.get("created_at")]
    assert stamps == sorted(stamps, reverse=True)


async def test_history_rows_carry_hash_and_null_url(sandbox, monkeypatch):
    import navig.gateway.deck.routes.inbox as inbox
    from navig.inbox.store import InboxEvent, InboxStore

    store = InboxStore()
    store.insert_event(InboxEvent(source_path="x.md", source_type="file", filename="routed.md",
                                  content_hash="feedfacefeedface", status="routed"))
    rows = await _rows(inbox)  # the sandbox root scans empty; history still returns
    mine = [r for r in rows if r.get("filename") == "routed.md"]
    assert mine and mine[0]["content_hash"] == "feedfacefeedface"
    assert mine[0]["url"] is None, "history keeps the hash, not the captured text"
