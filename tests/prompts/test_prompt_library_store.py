"""PromptLibraryStore: CRUD, search, facets, resolve, archive."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.store.prompt_library import PromptLibraryStore, PromptRecord, get_prompt_store, slugify


@pytest.fixture
def store(tmp_path: Path) -> PromptLibraryStore:
    s = PromptLibraryStore(tmp_path / "prompts.db")
    s.upsert(PromptRecord(id="dev/coding/debug-root-cause", title="Debug to the Root Cause",
                          body="Find the real cause.", use_when="A bug resists fixes", domain="dev",
                          category="coding", tags=["debug"]))
    s.upsert(PromptRecord(id="creative/images/marketing-poster", title="Marketing Poster",
                          body="Create a poster for [PRODUCT].", domain="creative", category="images",
                          variables=["[PRODUCT]"]))
    s.upsert(PromptRecord(id="dev/workflows/polish/app-quality/01-audit", title="Audit", body="Audit.",
                          domain="dev", category="workflows", collection="app-quality", step=1))
    return s


def test_create_derives_a_unique_slug_and_marks_user_owned(store):
    a = store.create(title="My Prompt!", body="Hi [NAME]", domain="Human", category="Health")
    b = store.create(title="My Prompt!", body="again", domain="human", category="health")
    assert a.id == "human/health/my-prompt" and b.id == "human/health/my-prompt-2"
    assert a.user_edited and a.source == "user" and a.variables == ["[NAME]"]


def test_create_rejects_empty_title(store):
    with pytest.raises(ValueError):
        store.create(title="  ", body="x")


def test_update_marks_edited_and_redetects_variables(store):
    rec = store.update("creative/images/marketing-poster", {"body": "Poster for [BRAND]", "tags": "a, #B"})
    assert rec.user_edited and rec.variables == ["[BRAND]"] and rec.tags == ["a", "b"]
    with pytest.raises(ValueError):
        store.update("creative/images/marketing-poster", {"nope": 1})
    with pytest.raises(KeyError):
        store.update("missing", {"title": "x"})


def test_favorite_and_use_are_not_content_edits(store):
    pid = "dev/coding/debug-root-cause"
    assert store.set_favorite(pid).favorite is True
    store.record_use(pid)
    rec = store.get(pid)
    assert rec.use_count == 1 and rec.last_used_at and not rec.user_edited
    assert [r.id for r in store.list(favorite=True)] == [pid]
    assert store.list(sort="used")[0].id == pid


def test_search_ranks_title_and_tolerates_odd_input(store):
    assert store.list(q="poster")[0].id == "creative/images/marketing-poster"
    assert store.list(q="debu")[0].id == "dev/coding/debug-root-cause"  # prefix match
    store.list(q='"unbalanced (quote')  # FTS syntax characters must not raise
    assert store.list(q="resists")[0].id == "dev/coding/debug-root-cause"  # use_when indexed


def test_search_follows_edits(store):
    store.update("dev/coding/debug-root-cause", {"body": "zebra stripes"})
    assert [r.id for r in store.list(q="zebra")] == ["dev/coding/debug-root-cause"]
    assert store.list(q="real cause") == []


def test_filters_and_facets(store):
    assert {r.id for r in store.list(domain="dev")} == {
        "dev/coding/debug-root-cause", "dev/workflows/polish/app-quality/01-audit"}
    assert [r.id for r in store.list(tag="debug")] == ["dev/coding/debug-root-cause"]
    f = store.facets()
    assert f["total"] == 3
    assert f["domains"]["dev"]["count"] == 2 and f["domains"]["dev"]["categories"]["coding"] == 1
    assert f["collections"] == [{"name": "app-quality", "domain": "dev", "count": 1}]
    assert f["tags"] == {"debug": 1}


def test_resolve_exact_then_tail_then_search(store):
    assert [r.id for r in store.resolve("dev/coding/debug-root-cause")] == ["dev/coding/debug-root-cause"]
    assert [r.id for r in store.resolve("Marketing Poster")] == ["creative/images/marketing-poster"]
    assert [r.id for r in store.resolve("debug root cause")] == ["dev/coding/debug-root-cause"]
    assert store.resolve("") == []


def test_archive_hides_and_purge_deletes(store):
    pid = "creative/images/marketing-poster"
    assert store.delete(pid)
    assert pid not in {r.id for r in store.list()} and store.list(q="poster") == []
    assert store.get(pid).archived and store.count() == 2
    store.update(pid, {"archived": False})
    assert store.list(q="poster")
    assert store.delete(pid, purge=True) and store.get(pid) is None
    assert store.list(q="poster") == []
    assert store.delete("missing") is False


def test_singleton_follows_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "a"))
    a = get_prompt_store()
    a.create(title="only in a", body="x")
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "b"))
    b = get_prompt_store()
    assert Path(b.db_path).parent == tmp_path / "b" and b.count() == 0


def test_slugify():
    assert slugify(" Dev\\Coding/My  Prompt?! ") == "dev/coding/my-prompt"
