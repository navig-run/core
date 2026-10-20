"""The contradictions producer: the two allowed classes, the exclusions, idempotence,
and the read path (reading never writes)."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.recall import query, reconcile
from navig.recall.model import Record
from navig.recall.store import RecallStore


@pytest.fixture
def st(tmp_path: Path) -> RecallStore:
    return RecallStore(tmp_path / "reconcile.db")


def _pair(store: RecallStore, *, key: str = "widget decision", project: str = "alpha") -> None:
    store.upsert_record(Record(id="rk-yes", type="decision", title="Widget: ship it", project=project,
                               canonical_key=key, status="confirmed", status_basis="user"))
    store.upsert_record(Record(id="rk-no", type="decision", title="Widget: never", project=project,
                               canonical_key=key, status="rejected", status_basis="user"))


def test_class1_opposed_user_verdicts_become_a_link_and_a_review(st) -> None:
    _pair(st)
    res = reconcile.reconcile(st, project="alpha")
    assert res["found"] == 1 and res["links"] == 1 and res["reviews"] == 1
    rels = {(ln["b"], ln["rel"]) for ln in st.links_of("rk-yes")}
    assert ("rk-no", "contradicts") in rels
    reviews = reconcile.open_reviews(st, project="alpha")
    assert len(reviews) == 1 and reviews[0]["class"] == "opposed_verdicts"
    assert {reviews[0]["a"]["id"], reviews[0]["b"]["id"]} == {"rk-yes", "rk-no"}


def test_reconcile_is_idempotent(st) -> None:
    _pair(st)
    reconcile.reconcile(st, project="alpha")
    again = reconcile.reconcile(st, project="alpha")
    assert again["found"] == 1
    assert again["links"] == 0 and again["reviews"] == 0
    assert len(reconcile.open_reviews(st)) == 1


def test_class2_claimed_built_but_verify_says_absent(st) -> None:
    st.upsert_record(Record(id="rk-lie", type="feature", title="Shipped thing", project="alpha",
                            status="implemented", status_basis="assistant", impl="not_found"))
    res = reconcile.reconcile(st, project="alpha")
    assert res["found"] == 1 and res["reviews"] == 1
    assert res["links"] == 0  # self-contradiction: a review, never a self-link
    rows = reconcile.open_reviews(st)
    assert rows[0]["class"] == "claimed_built_not_found" and rows[0]["b"] is None


def test_assistant_verdicts_never_produce_class1(st) -> None:
    st.upsert_record(Record(id="rk-a1", type="decision", title="X yes", project="alpha",
                            canonical_key="x", status="confirmed", status_basis="assistant"))
    st.upsert_record(Record(id="rk-a2", type="decision", title="X no", project="alpha",
                            canonical_key="x", status="rejected", status_basis="assistant"))
    assert reconcile.reconcile(st)["found"] == 0


def test_both_sides_approved_is_not_a_contradiction(st) -> None:
    st.upsert_record(Record(id="rk-p1", type="decision", title="X", project="alpha",
                            canonical_key="x", status="confirmed", status_basis="user"))
    st.upsert_record(Record(id="rk-p2", type="decision", title="X again", project="alpha",
                            canonical_key="x", status="approved", status_basis="user"))
    assert reconcile.reconcile(st)["found"] == 0


def test_a_later_reversal_stays_supersedes_never_contradicts(st) -> None:
    _pair(st)
    # the reversal already recorded: rk-no supersedes rk-yes (review-merge shape)
    st.add_links([("rk-no", "rk-yes", "supersedes")])
    res = reconcile.reconcile(st, project="alpha")
    assert res["found"] == 0
    rels = {ln["rel"] for ln in st.links_of("rk-yes")} | {ln["rel"] for ln in st.links_of("rk-no")}
    assert "contradicts" not in rels


def test_project_filter_and_status_exclusions(st) -> None:
    _pair(st, project="beta")  # opposed pair in beta only
    assert reconcile.reconcile(st, project="alpha")["found"] == 0
    assert reconcile.reconcile(st, project="beta")["found"] == 1


def test_shared_alias_matches_like_a_canonical_key(st) -> None:
    st.upsert_record(Record(id="rk-al1", type="decision", title="Widget", project="alpha",
                            canonical_key="widget", aliases=["gadget"], status="confirmed",
                            status_basis="user"))
    st.upsert_record(Record(id="rk-al2", type="decision", title="Gadget", project="alpha",
                            canonical_key="gadget", aliases=["widget"], status="rejected",
                            status_basis="user"))
    assert reconcile.reconcile(st)["found"] == 1


def test_reading_never_writes(st) -> None:
    _pair(st)
    out = query.conflicts("alpha", reconcile_run=False, store=st)
    assert out["reconcile"]["found"] == 0 and out["reviews"] == []
    assert st.list_review() == []
