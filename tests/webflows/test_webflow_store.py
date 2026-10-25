"""The Webflow store: ids, immutable revisions, list/get/delete, and the rule that an
unreadable or hand-broken file is reported, never replayed or overwritten."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.webflows import SCHEMA, WebflowError, WebflowStore, webflows_dir

ORIGIN = "http://127.0.0.1:8123"


def flow(name: str = "Place an order", **over) -> dict:
    doc = {
        "schema": SCHEMA,
        "name": name,
        "origins": [ORIGIN],
        "start_url": f"{ORIGIN}/order.html",
        "params": [{"name": "customer"}],
        "steps": [{"action": "fill", "css": "#name", "value": "{{customer}}"},
                  {"action": "assert", "has_text": "Thanks"}],
    }
    doc.update(over)
    return doc


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebflowStore:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    assert webflows_dir() == tmp_path / "cfg" / "webflows"
    return WebflowStore()


def test_save_assigns_id_and_revision(store: WebflowStore) -> None:
    doc = store.save(flow())
    assert doc["id"] == "wf_place-an-order" and doc["revision"] == 1
    assert doc["created_at"] == doc["updated_at"]
    assert (store.root / "wf_place-an-order" / "r1.json").is_file()
    again = store.save(flow())
    assert again["id"] == "wf_place-an-order-2"


def test_update_creates_a_new_revision_and_keeps_the_old(store: WebflowStore) -> None:
    first = store.save(flow())
    edited = dict(first, name="Place an order (v2)")
    second = store.save(edited, wf_id=first["id"])
    assert second["revision"] == 2 and second["created_at"] == first["created_at"]
    assert store.revisions(first["id"]) == [1, 2]
    assert store.get(first["id"])["name"] == "Place an order (v2)"
    assert store.get(first["id"], revision=1)["name"] == "Place an order"
    assert store.get(first["id"], revision=7) is None


def test_list_and_delete(store: WebflowStore) -> None:
    assert store.list() == []
    a = store.save(flow("A"))
    store.save(flow("B"))
    rows = {r.id: r for r in store.list()}
    assert set(rows) == {"wf_a", "wf_b"}
    assert rows["wf_a"].params == ("customer",) and rows["wf_a"].steps == 2
    assert store.delete(a["id"]) is True
    assert store.delete(a["id"]) is False
    assert [r.id for r in store.list()] == ["wf_b"]


def test_invalid_documents_are_never_written(store: WebflowStore) -> None:
    with pytest.raises(WebflowError):
        store.save(flow(steps=[{"action": "navigate", "url": "https://evil.example/"}]))
    assert not store.root.exists() or not any(store.root.iterdir())


def test_bad_ids_are_refused(store: WebflowStore) -> None:
    for bad in ("../etc", "wf_../x", "WF_A", ""):
        with pytest.raises(WebflowError):
            store.get(bad)


def test_a_broken_revision_is_reported_not_replayed(store: WebflowStore) -> None:
    doc = store.save(flow())
    path = store.root / doc["id"] / "r1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["steps"][0]["action"] = "eval_js"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert store.get(doc["id"]) is None
    assert store.list() == []
    # The next save still stacks on top instead of reusing (and destroying) r1.
    fixed = store.save(flow(), wf_id=doc["id"])
    assert fixed["revision"] == 2 and path.is_file()


def test_export_is_import_ready(store: WebflowStore) -> None:
    from navig.webflows import loads

    doc = store.save(flow())
    text = store.export(doc["id"])
    assert loads(text) == doc
