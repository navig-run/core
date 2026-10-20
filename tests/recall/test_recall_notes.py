"""Recall ⇄ web notes (navig-notes): synced notes become cited turns, checkboxes become the
person's own tasks, page quotes are kept apart from their words, and a deleted note is forgotten."""

from __future__ import annotations

from pathlib import Path

import pytest

navig_notes = pytest.importorskip("navig_notes")

from navig.recall.archive import Archive  # noqa: E402
from navig.recall.ingest import Ingestor  # noqa: E402
from navig.recall.projects import ProjectResolver  # noqa: E402
from navig.recall.sources import notes as notes_src  # noqa: E402
from navig.recall.store import RecallStore  # noqa: E402
from navig.recall.vault import Vault  # noqa: E402


def _note(nid: str, body: str, *, rev: int = 1, title: str = "") -> dict:
    return {
        "id": nid, "schema": 2, "title": title, "body": body, "scope": "page",
        "pageKey": "github.com/navig-run/labs/issues/12", "siteKey": "github.com",
        "sourceUrl": "https://github.com/navig-run/labs/issues/12", "sourceTitle": "Issue 12",
        "tags": ["work"], "state": "open", "createdAt": 1_790_000_000_000, "updatedAt": 1_790_000_000_000 + rev, "rev": rev,
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data))
    monkeypatch.setenv("NAVIG_NOTES_DB", str(tmp_path / "notes.db"))
    from navig_notes import deck_routes

    deck_routes._STORE.clear()
    lib = deck_routes.get_store()
    assert Path(lib.db_path).resolve().is_relative_to(tmp_path.resolve())
    root = data / "recall"
    ing = Ingestor(store=RecallStore(root / "index.db"), archive=Archive(root / "archive"), vault=Vault(root / "vault"),
                   resolver=ProjectResolver(entries=[], home=str(tmp_path)))
    yield ing, lib
    for s in deck_routes._STORE.values():
        s.close()
    deck_routes._STORE.clear()


def test_a_synced_note_becomes_turns_and_tasks(env):
    ing, lib = env
    lib.sync([_note("n1", "Retry path is flaky on Windows\n- [ ] add a regression test\n- [x] reproduce it\n"
                          "> quoted from the issue: fails on CI", title="Retry path")], [], share="local")
    rep = ing.run(["notes"])
    assert rep.sources_read == 1 and not rep.errors
    src = ing.store.get_source("notes:n1")
    assert src["project"] == "notes" and src["meta"]["brain_level"] == "local"
    assert not notes_src.may_leave_machine(src["meta"])
    own = ing.store.search_turns("flaky", adapter="notes")
    assert own and own[0]["speaker"] == "user_typed" and own[0]["msg_id"] == "n1:note"
    quote = ing.store.search_turns("quoted", adapter="notes")
    assert quote and quote[0]["speaker"] == "web_capture", "a page quote is not the person's own words"
    tasks = {(r.title, r.status, r.status_basis) for r in (ing.store.get_record(x.id) for x in ing.store.list_records(rtype="task"))}
    assert ("add a regression test", "unresolved", "user") in tasks
    assert ("reproduce it", "implemented", "user") in tasks
    rec = next(ing.store.get_record(x.id) for x in ing.store.list_records(rtype="task"))
    assert rec.attrs["link"] == "https://github.com/navig-run/labs/issues/12"


def test_unchanged_note_is_not_reread_and_an_edit_rewrites(env):
    ing, lib = env
    lib.sync([_note("n1", "first")], [])
    ing.run(["notes"])
    assert ing.run(["notes"]).unchanged == 1
    lib.sync([_note("n1", "second version", rev=2)], [])
    rep = ing.run(["notes"])
    assert rep.rewritten == 1
    assert ing.store.search_turns("second", adapter="notes") and not ing.store.search_turns("first", adapter="notes")


def test_a_deleted_note_is_forgotten(env):
    ing, lib = env
    lib.sync([_note("n1", "secret plan")], [])
    ing.run(["notes"])
    assert ing.store.search_turns("secret", adapter="notes")
    lib.sync([], [{"id": "n1", "rev": 1}])
    rep = ing.run(["notes"])
    assert rep.forgotten == 1
    assert ing.store.list_sources(adapter="notes") == [] and not ing.store.search_turns("secret", adapter="notes")


def test_agent_level_may_leave_the_machine(env):
    ing, lib = env
    lib.sync([_note("n1", "ok to use")], [], share="agent")
    ing.run(["notes"])
    assert notes_src.may_leave_machine(ing.store.get_source("notes:n1")["meta"])
