"""Commit-token store: additive RuntimeStore v4→v5, claim-once, replay, expiry.

The migration assertion uses ``RuntimeStore.SCHEMA_VERSION`` (never a literal), and
the store is always a throwaway ``runtime.db`` under ``tmp_path`` — there is no
second database for tokens.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from navig.reflex.errors import PlanExpired, PlanUnknown
from navig.reflex.plans import PLAN_TTL_SECONDS, CommitPlan, new_token
from navig.reflex.store import CommitPlanStore
from navig.store.runtime import RuntimeStore


def _plan(tool: str = "home.off") -> CommitPlan:
    return CommitPlan(token=new_token(), tool=tool, command=["navig", "home", "off", "x"])


def test_runtime_store_owns_the_table_and_the_version(runtime: RuntimeStore) -> None:
    tables = {
        row[0]
        for row in runtime._read_all("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "commit_plans" in tables
    assert runtime.get_schema_version() == RuntimeStore.SCHEMA_VERSION


def test_a_v4_database_gains_commit_plans_on_open(tmp_path: Path) -> None:
    """Simulate a deployed v4 runtime.db: the additive migration runs once, no data loss."""
    db = tmp_path / "runtime.db"
    store = RuntimeStore(db)
    store.set_ai_state(user_id=1, chat_id=1, mode="active", persona="tyler")
    store.close()

    # Rewind the schema marker and drop the v5 table — exactly a v4 database.
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE commit_plans")
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (4)")
    conn.commit()
    conn.close()

    reopened = RuntimeStore(db)
    try:
        assert reopened.get_schema_version() == RuntimeStore.SCHEMA_VERSION
        # The migration re-created the table and kept the operator's data.
        assert reopened.get_ai_state(1)["persona"] == "tyler"
        plan = CommitPlanStore(reopened).create(_plan())
        assert CommitPlanStore(reopened).get(plan.token) is not None
    finally:
        reopened.close()


def test_create_and_get_round_trip(plan_store: CommitPlanStore) -> None:
    plan = plan_store.create(_plan())
    assert plan.token.startswith("plan-")
    assert plan.expires_at > plan.created_at
    loaded = plan_store.get(plan.token)
    assert loaded is not None
    assert loaded.tool == "home.off"
    assert loaded.state == "pending"
    assert loaded.command == ["navig", "home", "off", "x"]


def test_get_unknown_token_is_none(plan_store: CommitPlanStore) -> None:
    assert plan_store.get("plan-deadbeef") is None
    assert plan_store.get("") is None


def test_claim_is_exactly_once(plan_store: CommitPlanStore) -> None:
    plan = plan_store.create(_plan())
    claimed, fresh = plan_store.claim(plan.token)
    assert fresh is True
    assert claimed.state == "committed"
    again, fresh_again = plan_store.claim(plan.token)
    assert fresh_again is False


def test_finish_stores_the_result_and_state(plan_store: CommitPlanStore) -> None:
    plan = plan_store.create(_plan())
    plan_store.claim(plan.token)
    finished = plan_store.finish(plan.token, {"ok": True, "speech": "done"})
    assert finished.state == "done"
    replay = plan_store.get(plan.token)
    assert replay is not None
    assert replay.result == {"ok": True, "speech": "done"}


def test_failed_commit_records_failure(plan_store: CommitPlanStore) -> None:
    plan = plan_store.create(_plan())
    plan_store.claim(plan.token)
    finished = plan_store.finish(plan.token, {"ok": False, "speech": "boom"}, ok=False)
    assert finished.state == "failed"
    claimed, fresh = plan_store.claim(plan.token)
    assert fresh is False
    assert claimed.result == {"ok": False, "speech": "boom"}


def test_unknown_token_raises_with_the_next_command(plan_store: CommitPlanStore) -> None:
    with pytest.raises(PlanUnknown) as info:
        plan_store.claim("plan-nope")
    assert "--plan" in info.value.hint


def test_expired_token_raises_and_asks_for_a_new_plan(
    plan_store: CommitPlanStore, clock
) -> None:
    plan = plan_store.create(_plan())
    clock.advance(PLAN_TTL_SECONDS + 1)
    with pytest.raises(PlanExpired) as info:
        plan_store.claim(plan.token)
    assert "run --plan again" in info.value.hint
    assert plan.token in info.value.message


def test_expiry_does_not_block_a_replay_of_a_finished_plan(
    plan_store: CommitPlanStore, clock
) -> None:
    plan = plan_store.create(_plan())
    plan_store.claim(plan.token)
    plan_store.finish(plan.token, {"ok": True, "speech": "done"})
    clock.advance(PLAN_TTL_SECONDS * 10)
    claimed, fresh = plan_store.claim(plan.token)
    assert fresh is False
    assert claimed.result == {"ok": True, "speech": "done"}


def test_pending_lists_only_live_plans(plan_store: CommitPlanStore, clock) -> None:
    first = plan_store.create(_plan("home.on"))
    second = plan_store.create(_plan("home.off"))
    assert {plan.token for plan in plan_store.pending()} == {first.token, second.token}
    plan_store.claim(first.token)
    assert [plan.token for plan in plan_store.pending()] == [second.token]


def test_default_store_is_the_existing_runtime_db(tmp_path, monkeypatch) -> None:
    """No new DB file: the default plan store opens ``<config dir>/runtime.db``."""
    from navig.platform import paths

    data_dir = tmp_path / "data"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data_dir))
    plan_store = CommitPlanStore()
    assert plan_store.store.db_path == paths.config_dir() / "runtime.db"
    assert not data_dir.exists() or not list(data_dir.glob("*.db"))
