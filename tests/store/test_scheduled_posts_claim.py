"""ScheduledPostStore.claim_publish — the atomic open → publishing claim."""

from __future__ import annotations

from navig.store.scheduled_posts import ScheduledPostStore


def _post(tmp_path):
    store = ScheduledPostStore(db_path=tmp_path / "sp.db")
    pid = store.create(body="hi", content={"body": "hi"}, targets=[], status="scheduled")
    return store, pid


def test_second_claim_is_refused_while_publishing(tmp_path):
    store, pid = _post(tmp_path)
    assert store.claim_publish(pid, "k") is True
    assert store.get(pid)["status"] == "publishing"
    assert store.claim_publish(pid, "k") is False
    assert store.claim_publish(pid, "other") is False


def test_claim_refuses_a_replay_of_a_published_key(tmp_path):
    store, pid = _post(tmp_path)
    store.update(pid, status="published", idempotency_key="k")
    assert store.claim_publish(pid, "k") is False
    assert store.claim_publish(pid, "fresh") is True  # an explicit new publish is allowed


def test_claim_after_failure_is_allowed(tmp_path):
    store, pid = _post(tmp_path)
    store.update(pid, status="failed", idempotency_key="k")
    assert store.claim_publish(pid, "k") is True
    assert store.claim_publish(404, "k") is False
