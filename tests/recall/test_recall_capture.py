"""Recall ⇄ browser captures (Navig Capture v1): notes the Dock wrote into the inbox become
attributed, project-scoped, searchable turns — and captured tables become table records.

Every note here is synthetic, written through the same renderer the capture route uses."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.inbox import capture_v1
from navig.recall.archive import Archive
from navig.recall.ingest import Ingestor
from navig.recall.projects import ProjectResolver
from navig.recall.sources import ADAPTERS
from navig.recall.sources import capture as cap_src
from navig.recall.store import RecallStore
from navig.recall.vault import Vault

TABLE = "| Plan | Price | Seats |\n|---|---|---|\n| Free | $0 | 1 |\n| Team | $12 | 10 |"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data))
    inbox = data / "inbox"
    inbox.mkdir(parents=True)
    root = data / "recall"
    ing = Ingestor(store=RecallStore(root / "index.db"), archive=Archive(root / "archive"), vault=Vault(root / "vault"),
                   resolver=ProjectResolver(entries=[("alpha", str(tmp_path / "alpha"))], home=str(tmp_path / "home")))

    def write(**body) -> Path:
        cap = capture_v1.parse_body({"v": 1, "client": {"id": "dock", "version": "test"}, **body})
        p = inbox / f"{capture_v1.slug_for(cap.title)}--{cap.id}.md"
        p.write_text(capture_v1.render_note(cap), encoding="utf-8")
        return p

    return ing, inbox, write


def test_capture_is_a_registered_adapter():
    assert "capture" in ADAPTERS


def test_a_selection_becomes_a_web_capture_turn_under_its_project(env):
    ing, _inbox, write = env
    write(kind="selection", url="https://example.com/a", title="Pricing page", project="alpha",
          content="We settled on usage-based pricing for the agent tier.", hint="decision", note="from the call")
    rep = ing.run(["capture"])
    assert rep.sources_read == 1 and rep.turns_added == 1 and not rep.errors
    hits = ing.store.search_turns("usage-based pricing", project="alpha")
    assert hits and hits[0]["speaker"] == "web_capture" and hits[0]["kind"] == "selection"
    src = ing.store.get_source(hits[0]["source_id"])
    assert src["adapter"] == "capture" and src["project"] == "alpha"
    assert src["meta"]["url"] == "https://example.com/a" and src["meta"]["hint"] == "decision"
    # The operator's note travels with the capture and is searchable too.
    assert ing.store.search_turns("from the call", project="alpha")
    # Re-running is a no-op.
    again = ing.run(["capture"])
    assert again.unchanged == 1 and again.turns_added == 0


def test_an_unknown_project_falls_back_to_shared_never_invented(env):
    ing, _inbox, write = env
    write(kind="selection", url="https://example.com/b", title="B", project="no-such-project", content="orphan text")
    ing.run(["capture"])
    hit = ing.store.search_turns("orphan")[0]
    src = ing.store.get_source(hit["source_id"])
    assert src["project"] == "shared" and src["meta"]["requested_project"] == "no-such-project"


def test_a_captured_table_becomes_a_table_record(env):
    ing, _inbox, write = env
    write(kind="table", url="https://example.com/p", title="Plans", project="alpha", content=TABLE)
    rep = ing.run(["capture"])
    assert rep.records_created == 1
    recs = ing.store.list_records(rtype="table")
    rec = ing.store.get_record(recs[0].id)
    assert rec.project == "alpha" and rec.evidence[0].speaker == "web_capture"
    assert rec.attrs["columns"] == ["Plan", "Price", "Seats"]


def test_a_whole_chat_capture_is_left_to_the_chats_library(env):
    # Owner decision 2026-10-16: conversations belong to navig-chats (read via the `chats`
    # source with its share levels); a whole-chat capture must not become a second copy.
    ing, _inbox, write = env
    write(kind="chat", url="https://chatgpt.com/c/abc", title="Chat", project="alpha",
          content="## You\n\nHow do I shard SQLite?\n\n## ChatGPT\n\nUse one database file per tenant.",
          meta={"site": "chatgpt.com", "conversation_id": "abc", "message_ids": ["m-user", "m-bot"]})
    ing.run(["capture"])
    assert ing.store.search_turns("tenant") == []
    assert not [s for s in ing.store.list_sources() if s["adapter"] == "capture"]
def test_ingest_file_indexes_one_capture_now_and_a_moved_note_stays_searchable(env):
    ing, inbox, write = env
    p = write(kind="selection", url="https://example.com/c", title="C", project="alpha", content="quokka migration notes")
    out = cap_src.ingest_file(p, ing)
    assert out["indexed"] and out["project"] == "alpha" and out["turns"] == 1
    assert ing.store.search_turns("quokka", project="alpha")
    # The inbox router may move the note away later: what Recall archived is kept.
    p.rename(inbox.parent / "elsewhere.md")
    rep = ing.run(["capture"])
    assert rep.errors == [] and ing.store.search_turns("quokka", project="alpha")
    assert ing.reindex()["turns"] >= 1 and ing.store.search_turns("quokka", project="alpha")


def test_notes_that_are_not_v1_captures_are_ignored(env):
    ing, inbox, _write = env
    (inbox / "legacy--note.md").write_text("# Old\n\nSource: https://x\n\nplain legacy capture\n", encoding="utf-8")
    (inbox / "other--fm.md").write_text("---\ntitle: x\n---\n\nnot a capture\n", encoding="utf-8")
    rep = ing.run(["capture"])
    assert rep.sources_seen == 0 and not ing.store.search_turns("legacy")
    assert cap_src.ingest_file(inbox / "legacy--note.md", ing) == {"indexed": False, "reason": "not a v1 capture"}


def test_secrets_in_a_capture_are_redacted_before_storage(env):
    ing, _inbox, write = env
    write(kind="selection", url="https://example.com/k", title="K", content="token sk-ant-api03-" + "a" * 40 + " here")
    ing.run(["capture"])
    turns = list(ing.archive.read_turns("capture", ing.store.list_sources(adapter="capture")[0]["source_id"]))
    assert "sk-ant-api03-aaaa" not in turns[0].text
