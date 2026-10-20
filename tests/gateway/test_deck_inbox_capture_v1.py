"""POST /api/deck/inbox/capture — Navig Capture v1 (frontmatter, validation, idempotent
retries, immediate Recall indexing) while legacy payloads keep their exact old note."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from navig.gateway.deck.routes import inbox as inbox_routes  # noqa: E402
from navig.inbox import capture_v1  # noqa: E402


class _Cfg:
    def __init__(self, on: bool):
        self.on = on

    def get(self, key, default=None):
        return self.on if key == "recall.read_local" else default


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "data"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(d))
    monkeypatch.setattr("navig.config.get_config_manager", lambda: _Cfg(False))
    return d


async def _post(body, raw: str | None = None, max_size: int = 100 * 1024 * 1024):
    app = web.Application(client_max_size=max_size)  # the gateway's own cap (server.py)
    app.router.add_post("/api/deck/inbox/capture", inbox_routes.handle_deck_inbox_capture)
    async with TestClient(TestServer(app)) as client:
        if raw is not None:
            resp = await client.post("/api/deck/inbox/capture", data=raw, headers={"Content-Type": "application/json"})
        else:
            resp = await client.post("/api/deck/inbox/capture", json=body)
        return resp.status, await resp.json()


async def test_a_legacy_payload_writes_the_exact_old_note(data: Path):
    status, body = await _post({"type": "selection", "url": "https://e.com/a", "title": "Hello",
                                "selection": "some text"})
    assert status == 200 and body["data"]["captured"] and "recall" not in body["data"]
    text = Path(body["data"]["path"]).read_text(encoding="utf-8")
    assert text == "# Hello\n\nSource: https://e.com/a\n\nsome text\n"
    assert Path(body["data"]["path"]).parent == data / "inbox"


async def test_a_bare_legacy_page_capture_still_works(data: Path):
    status, body = await _post({}, raw="not json")
    assert status == 200
    assert Path(body["data"]["path"]).read_text(encoding="utf-8") == "# Captured item\n\n"


async def test_a_v1_capture_carries_frontmatter_that_reads_back(data: Path):
    status, body = await _post({
        "v": 1, "kind": "selection", "url": "https://chatgpt.com/c/abc", "title": "Pricing", "project": "navig",
        "content_md": "| A | B |\n|---|---|\n| 1 | 2 |", "hint": "table", "note": "keep this",
        "meta": {"site": "chatgpt.com", "conversation_id": "abc", "message_ids": ["m1"], "role": "assistant"},
        "client": {"id": "dock", "version": "1.11.0"}, "captured_at": "2026-10-16T10:00:00Z", "id": "cap-test-0001",
    })
    assert status == 200
    d = body["data"]
    assert d["v"] == 1 and d["id"] == "cap-test-0001" and d["project"] == "navig"
    assert d["recall"] == {"indexed": False, "reason": "recall is off (navig recall enable)"}
    fm, rest = capture_v1.read_note(Path(d["path"]).read_text(encoding="utf-8"))
    assert fm["navig_capture"] == 1 and fm["kind"] == "selection" and fm["project"] == "navig"
    assert fm["conversation_id"] == "abc" and fm["message_ids"] == ["m1"] and fm["role"] == "assistant"
    assert fm["client"] == {"id": "dock", "version": "1.11.0"} and fm["note"] == "keep this"
    assert capture_v1.note_content(fm, rest) == "| A | B |\n|---|---|\n| 1 | 2 |"


async def test_a_retried_v1_id_returns_the_note_already_written(data: Path):
    payload = {"v": 1, "kind": "selection", "url": "https://e.com", "title": "T", "content": "x", "id": "cap-retry-0001"}
    s1, b1 = await _post(payload)
    s2, b2 = await _post(payload)
    assert s1 == s2 == 200 and b2["data"]["duplicate"] and b1["data"]["path"] == b2["data"]["path"]
    assert len(list((data / "inbox").glob("*.md"))) == 1


@pytest.mark.parametrize("payload,status,needle", [
    ({"v": 2, "kind": "page"}, 400, "unsupported capture version"),
    ({"v": 1, "kind": "video"}, 400, "kind must be one of"),
    ({"v": 1, "kind": "selection"}, 400, "needs content"),
    ({"v": 1, "kind": "page", "project": "../etc"}, 400, "project must be"),
    ({"v": 1, "kind": "page", "meta": {"message_ids": ["x"] * 501}}, 400, "more than 500"),
    ({"v": 1, "kind": "page", "meta": {"role": "admin"}}, 400, "meta.role"),
    ({"v": 1, "kind": "page", "hint": "secret"}, 400, "hint must be"),
    ({"v": 1, "kind": "page", "captured_at": "yesterday"}, 400, "ISO 8601"),
    ({"v": 1, "kind": "page", "id": "a/b"}, 400, "id must be"),
    ({"v": 1, "kind": "selection", "content": "x" * (capture_v1.MAX_CONTENT_BYTES + 1)}, 413, "larger than 2 MB"),
    ({"type": "page", "content": "x" * (capture_v1.MAX_CONTENT_BYTES + 1)}, 413, "larger than 2 MB"),
])
async def test_invalid_captures_are_refused_and_write_nothing(data: Path, payload, status, needle):
    got, body = await _post(payload)
    assert got == status and needle in body["error"]
    assert not (data / "inbox").exists() or not list((data / "inbox").iterdir())


async def test_a_body_over_the_server_cap_is_413_never_an_empty_note(data: Path):
    status, body = await _post({"v": 1, "kind": "selection", "content": "x" * 4096}, max_size=1024)
    assert status == 413
    assert not (data / "inbox").exists() or not list((data / "inbox").iterdir())


async def test_a_long_page_title_is_shortened_not_refused(data: Path):
    status, body = await _post({"v": 1, "kind": "page", "url": "https://e.com", "title": "t" * 1000})
    assert status == 200
    fm, _ = capture_v1.read_note(Path(body["data"]["path"]).read_text(encoding="utf-8"))
    assert len(fm["title"]) == capture_v1.MAX_TITLE


async def test_with_recall_enabled_the_capture_is_searchable_at_once(data: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("navig.config.get_config_manager", lambda: _Cfg(True))
    status, body = await _post({"v": 1, "kind": "selection", "url": "https://e.com/x", "title": "Zebra",
                                "content": "the zebra protocol is our sync layer"})
    assert status == 200
    rec = body["data"]["recall"]
    assert rec["indexed"] and rec["turns"] == 1 and rec["project"] == "shared"
    from navig.recall.store import get_recall_store

    st = get_recall_store()
    assert Path(st.db_path).resolve().is_relative_to(data.resolve())
    hits = st.search_turns("zebra protocol")
    assert hits and hits[0]["speaker"] == "web_capture" and hits[0]["source_id"] == rec["source_id"]
