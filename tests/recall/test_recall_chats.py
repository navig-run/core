"""Recall ⇄ the chat library: only shared chats are read, citations open the exact message,
decisions settle across chats, and withdrawing a share forgets everything Recall derived."""

from __future__ import annotations

from pathlib import Path

import pytest

navig_chats = pytest.importorskip("navig_chats")

from navig.recall.archive import Archive  # noqa: E402
from navig.recall.ingest import Ingestor  # noqa: E402
from navig.recall.projects import ProjectResolver  # noqa: E402
from navig.recall.sources import chats as chats_src  # noqa: E402
from navig.recall.store import RecallStore  # noqa: E402
from navig.recall.vault import Vault  # noqa: E402


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data))
    monkeypatch.setenv("NAVIG_CHATS_DB", str(tmp_path / "chats.db"))
    from navig_chats import deck_routes
    from navig_chats.models import ConversationIn, MessageIn

    deck_routes._STORE.clear()
    lib = deck_routes.get_store()
    assert Path(lib.db_path).resolve().is_relative_to(tmp_path.resolve())

    def chat(native: str, msgs: list[tuple[str, str, str, str]], title: str = "Chat") -> str:
        lib.upsert(ConversationIn(provider="chatgpt", native_id=native, title=title, url=f"https://chatgpt.com/c/{native}",
                                  messages=[MessageIn(id=i, role=r, text=t, created_at=ts) for i, r, t, ts in msgs],
                                  completeness="rendered", provenance="live"))
        return f"chatgpt:{native}"

    root = data / "recall"
    ing = Ingestor(store=RecallStore(root / "index.db"), archive=Archive(root / "archive"), vault=Vault(root / "vault"),
                   resolver=ProjectResolver(entries=[], home=str(tmp_path)))
    yield ing, lib, chat, root
    for s in deck_routes._STORE.values():
        s.close()
    deck_routes._STORE.clear()


def _ingest(ing):
    return ing.run(["chats"])


def test_nothing_is_read_until_a_chat_is_shared(env):
    ing, lib, chat, root = env
    chat("a1", [("u1", "user", "Decision: use SQLite", "2026-01-01T10:00:00")])
    rep = _ingest(ing)
    assert rep.sources_read == 0 and ing.store.list_sources(adapter="chats") == []
    assert not (root / "archive" / "chats").exists()


def test_shared_chat_becomes_cited_turns_and_records(env):
    ing, lib, chat, _root = env
    cid = chat("a1", [
        ("u1", "user", "Decision (storage): Postgres", "2026-01-01T10:00:00"),
        ("a1", "assistant", "OK.\n- [ ] write the migration\nDecision: add Redis too", "2026-01-01T10:01:00"),
    ], title="Storage")
    lib.update(cid, brain="local")
    rep = _ingest(ing)
    assert rep.sources_read == 1 and rep.turns_added == 2 and not rep.errors
    src = ing.store.get_source("chats:chatgpt:a1")
    assert src["project"] == "chats" and src["meta"]["brain_level"] == "local"
    assert not chats_src.may_leave_machine(src["meta"])
    turns = ing.store.search_turns("Postgres", adapter="chats")
    assert turns and turns[0]["msg_id"] == "u1" and turns[0]["speaker"] == "user_typed"
    recs = {(r.type, r.status, r.status_basis) for r in (ing.store.get_record(x.id) for x in ing.store.list_records())}
    assert ("decision", "confirmed", "user") in recs
    assert ("decision", "proposed", "assistant") in recs  # the assistant never decides
    assert ("task", "proposed", "assistant") in recs
    dec = next(ing.store.get_record(x.id) for x in ing.store.list_records(rtype="decision")
               if ing.store.get_record(x.id).status == "confirmed")
    assert dec.attrs["link"] == "https://chatgpt.com/c/a1#navig-msg=u1"
    assert dec.evidence[0].source_id == "chats:chatgpt:a1" and dec.evidence[0].msg_id == "u1"
    assert _ingest(ing).unchanged == 1  # idempotent


def test_a_later_chat_supersedes_an_earlier_decision(env):
    ing, lib, chat, _root = env
    folder = lib.create_folder("Infra")
    lib.update_folder(folder["id"], brain="local", brain_project="navig")
    a = chat("a", [("u1", "user", "Decision (storage): Postgres", "2026-01-01T10:00:00")])
    b = chat("b", [("u1", "user", "Decision (storage): SQLite with FTS5", "2026-03-01T10:00:00")])
    lib.bulk([a, b], "move", folder["id"])
    _ingest(ing)
    decisions = [ing.store.get_record(x.id) for x in ing.store.list_records(rtype="decision", project="navig")]
    by = {d.title: d for d in decisions}
    assert by["storage: Postgres"].status == "superseded"
    assert by["storage: SQLite with FTS5"].status == "confirmed"
    rel = ing.store.related(by["storage: Postgres"].id)
    assert any(r["rel"] == "superseded_by" and r["other"] == by["storage: SQLite with FTS5"].id for r in rel)
    assert ing.store.list_sources(adapter="chats", project="navig")  # mapped project, not another space's


def test_withdrawing_a_share_forgets_everything_recall_derived(env):
    ing, lib, chat, root = env
    cid = chat("a1", [("u1", "user", "Decision: keep the secret plan", "2026-01-01T10:00:00")])
    lib.update(cid, brain="local")
    _ingest(ing)
    assert ing.store.search_turns("secret", adapter="chats")
    vault_files = list((root / "vault").rglob("rk-*.md"))
    assert vault_files
    lib.update(cid, brain="off")
    rep = _ingest(ing)
    assert rep.forgotten == 1
    assert ing.store.search_turns("secret") == [] and ing.store.list_records() == []
    assert ing.store.get_source("chats:chatgpt:a1") is None
    assert not any((root / "archive" / "chats").glob("*")) if (root / "archive" / "chats").exists() else True
    assert not any(p.exists() for p in vault_files)
    # A rebuild from the archive + vault does not bring it back.
    ing.reindex()
    assert ing.store.search_turns("secret") == []


def test_an_edited_chat_is_re_read_not_appended(env):
    ing, lib, chat, _root = env
    cid = chat("e", [("u1", "user", "Decision: blue", "2026-01-01T10:00:00")])
    lib.update(cid, brain="agent")
    _ingest(ing)
    src = ing.store.get_source("chats:chatgpt:e")
    assert chats_src.may_leave_machine(src["meta"])
    chat("e", [("u1", "user", "Decision: green", "2026-01-01T10:00:00")])  # edited in place
    rep = _ingest(ing)
    assert rep.rewritten == 1
    assert ing.store.search_turns("blue") == [] and ing.store.search_turns("green")
    titles = [ing.store.get_record(x.id).title for x in ing.store.list_records(rtype="decision")]
    assert titles == ["green"]


def test_without_the_plugin_there_are_no_chat_sources(monkeypatch):
    monkeypatch.setattr(chats_src, "_plugin", lambda: None)
    assert list(chats_src.discover()) == [] and not chats_src.available()
