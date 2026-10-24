"""Acceptance B — NAVIG Reflex plan/commit (plan §3, §3.6, §3.7).

Black-box through the in-process ``navig ask …`` CLI and the documented fake HomeBridge
seam. The token store is asserted through its documented persistence contract
(``commit_plans`` in the existing RuntimeStore, plan §3.4) — never a private helper.
"""

from __future__ import annotations

import json
import re
import sqlite3

import _support as S
import pytest

pytestmark = pytest.mark.integration

PLAN_UTTERANCE = "turn off the desk lamp"
BATCH_UTTERANCE = "turn off everything"


def _ask(capsys, *args: str) -> tuple[int, str, str]:
    return S.invoke_cli(["ask", *args], capsys)


def _plan(fake_home, capsys, *extra: str) -> tuple[dict, dict]:
    exit_code, stdout, stderr = _ask(capsys, PLAN_UTTERANCE, "--plan", "--json", *extra)
    text = S.combined(stdout, stderr)
    assert exit_code == 0, f"navig ask … --plan --json failed (exit {exit_code}): {text[-1500:]}"
    envelope = S.json_payload(text)
    S.answer_envelope_ok(envelope)
    return envelope, S.plan_of(envelope)


def _commit(capsys, token: str, *extra: str) -> tuple[int, dict | None, str]:
    # Plan §9 DoD spells the command as `navig ask "<utterance>" --commit <token>`;
    # the token (not the text) is what must drive the commit/replay.
    exit_code, stdout, stderr = _ask(capsys, PLAN_UTTERANCE, "--commit", token, "--json", *extra)
    text = S.combined(stdout, stderr)
    payload = S.json_payload(text) if exit_code == 0 else None
    return exit_code, payload, text


def _runtime_db():
    return S.runtime_db_path()


# --------------------------------------------------------------------------- #
# Plan is pure, contract-shaped, and emits no LLM
# --------------------------------------------------------------------------- #
def test_plan_json_contract_and_nothing_mutates(fake_home, capsys):
    envelope, plan = _plan(fake_home, capsys)

    assert plan.get("tool") == "home.off", f"the utterance must route to home.off, got {plan!r}"
    assert "desk-lamp" in S.plan_text(plan), f"the resolved target must name the desk lamp: {plan!r}"
    assert isinstance(plan.get("blast_radius"), dict) and plan["blast_radius"], (
        f"blast_radius missing from the plan: {plan!r}"
    )
    assert plan.get("profile") in {"fast", "safe", "campaign"}, f"profile missing: {plan!r}"
    assert plan.get("profile") == "fast", "fast is the documented default profile (plan §3.5)"
    S.token_of(plan)

    assert fake_home.mutations == [], f"--plan performed an effect: {fake_home.mutations!r}"
    assert fake_home.mutating_calls() == [], (
        f"--plan reached a mutating bridge call: {fake_home.mutating_calls()!r}"
    )
    assert envelope.get("ok") is True
    data = envelope.get("data") or {}
    if "alternatives" in data:
        assert isinstance(data["alternatives"], list), (
            "alternatives must be reported as a list (never auto-executed)"
        )
    assert data.get("executed") in (False, None), (
        "--plan must report executed=false — a plan is not an effect"
    )


def test_plan_human_output_names_tool_target_and_commit_token(fake_home, capsys):
    exit_code, stdout, stderr = _ask(capsys, PLAN_UTTERANCE, "--plan")
    text = S.combined(stdout, stderr)
    assert exit_code == 0, f"navig ask … --plan failed (exit {exit_code}): {text[-1500:]}"
    lowered = text.lower()
    assert "home.off" in lowered or "turn off" in lowered, f"tool missing from plan output: {text[:800]}"
    assert "desk lamp" in lowered or "desk-lamp" in lowered, f"target missing from plan output: {text[:800]}"
    assert re.search(r"commit[_ ]?token|token", lowered), f"commit token missing from plan output: {text[:800]}"
    assert fake_home.mutations == [], "--plan must not execute"


# --------------------------------------------------------------------------- #
# Commit once + replay is a no-op with the same Answer
# --------------------------------------------------------------------------- #
def test_commit_executes_exactly_once_and_replay_reuses_the_answer(fake_home, capsys):
    _envelope, plan = _plan(fake_home, capsys)
    token = S.token_of(plan)

    exit_code, committed, text = _commit(capsys, token)
    assert exit_code == 0, f"--commit failed (exit {exit_code}): {text[-1500:]}"
    assert committed is not None
    S.answer_envelope_ok(committed)
    assert committed.get("ok") is True
    assert fake_home.mutations == [("off", "desk-lamp")], (
        f"commit must perform the effect exactly once, got {fake_home.mutations!r}"
    )

    replay_code, replay, replay_text = _commit(capsys, token)
    assert replay_code == 0, f"replaying the token must exit 0 (no-op), got {replay_code}: {replay_text[-1500:]}"
    assert replay is not None
    S.answer_envelope_ok(replay)
    assert replay == committed, (
        "replay must return the stored Answer verbatim (lead invariant #2: commit is "
        "acknowledged+replayable, not a re-run); replay differs:\n"
        f"committed={json.dumps(committed, sort_keys=True, default=str)[:1000]}\n"
        f"replay={json.dumps(replay, sort_keys=True, default=str)[:1000]}"
    )
    assert fake_home.mutations == [("off", "desk-lamp")], (
        f"replay must not call the provider again, got {fake_home.mutations!r}"
    )


def _commit_row(token: str):
    """The documented persistence row for a token (plan §3.4: state + stored result)."""
    db_path = _runtime_db()
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(commit_plans)")}
        if not columns:
            return None
        result_column = next(
            (name for name in ("result_json", "result") if name in columns), None
        )
        if result_column is None or "state" not in columns:
            return None
        selected = f"state, {result_column}"
        return conn.execute(
            f"SELECT {selected} FROM commit_plans WHERE token = ?", (token,)
        ).fetchone()


def test_commit_is_acknowledged_one_tool_and_never_falls_back_to_another(fake_home, capsys):
    """Lead invariants #2/#6: one tool per token, no commit-time fallback, row records the result."""
    _envelope, plan = _plan(fake_home, capsys)
    token = S.token_of(plan)

    exit_code, committed, text = _commit(capsys, token)
    assert exit_code == 0, f"--commit failed (exit {exit_code}): {text[-1500:]}"
    assert committed is not None

    row = _commit_row(token)
    assert row is not None, "the commit must be recorded in the RuntimeStore commit_plans table"
    state, result = row
    assert str(state).lower() in {"committed", "done", "done:", "acked", "executed"}, (
        f"the commit row must be acknowledged, got state={state!r}"
    )
    assert result, "the commit row must store the Answer result (replay source)"
    assert "speech" in str(result).lower(), f"the stored result must be the Answer: {str(result)[:400]}"

    assert fake_home.attempted == [("off", "desk-lamp")], (
        f"one commit token must execute exactly one tool, got attempts {fake_home.attempted!r}"
    )
    assert fake_home.mutations == [("off", "desk-lamp")]

    from navig.platform.paths import config_dir  # noqa: F401 — keep the isolation documented

    token_db = S.runtime_db_path()
    assert token_db.name == "runtime.db", f"tokens must live in runtime.db, got {token_db}"
    databases = {path.name for path in token_db.parent.glob("*.db")}
    assert databases == {"runtime.db"}, (
        f"the commit must not create a new sqlite file; found {sorted(databases)} in {token_db.parent}"
    )

    # A failing tool must surface as a failed commit — never a silent second-tool fallback.
    _reset_lamp(fake_home)
    _envelope2, plan2 = _plan(fake_home, capsys)
    token2 = S.token_of(plan2)
    fake_home.fail_next_mutation = True
    fail_code, _fail_payload, fail_text = _commit(capsys, token2)
    assert fail_code != 0, f"a failing provider must fail the commit: {fail_text[-1500:]}"
    assert fake_home.attempted == [("off", "desk-lamp")], (
        "on a failing commit no second tool may be attempted (fallback is classification-time only): "
        f"{fake_home.attempted!r}"
    )
    assert fake_home.mutations == [], "a failed provider call must not be recorded as applied"
    failed_row = _commit_row(token2)
    assert failed_row is not None, "even a failed commit must be journaled"
    assert str(failed_row[0]).lower() == "failed", (
        f"the failed commit must be recorded as failed, got state={failed_row[0]!r}"
    )


def test_unknown_token_exits_2_with_actionable_hint(fake_home, capsys):
    exit_code, _payload, text = _commit(capsys, "plan-0000000000")
    assert exit_code == 2, f"unknown token must exit 2, got {exit_code}: {text[-1200:]}"
    lowered = text.lower()
    assert "--plan" in lowered or "plan again" in lowered, (
        f"the unknown-token error must tell the operator to mint a plan: {text[-1200:]}"
    )
    assert fake_home.mutations == []


def test_expired_token_exits_2_and_never_executes(fake_home, capsys):
    _envelope, plan = _plan(fake_home, capsys)
    token = S.token_of(plan)

    db_path = _runtime_db()
    assert db_path.exists(), f"the token store must be the existing RuntimeStore DB at {db_path} (plan §3.4)"
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "commit_plans" in tables, (
            f"commit tokens must live in the RuntimeStore 'commit_plans' table, got tables={sorted(tables)}"
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(commit_plans)")}
        assert {"token", "expires_at", "state"} <= columns, (
            f"commit_plans is missing documented columns (plan §3.4): {sorted(columns)}"
        )
        conn.execute("UPDATE commit_plans SET expires_at = 0 WHERE token = ?", (token,))
        conn.commit()

    exit_code, _payload, text = _commit(capsys, token)
    assert exit_code == 2, f"expired token must exit 2, got {exit_code}: {text[-1200:]}"
    lowered = text.lower()
    assert "expired" in lowered or "run --plan again" in lowered or "--plan" in lowered, (
        f"the expired-token error must be actionable: {text[-1200:]}"
    )
    assert fake_home.mutations == [], "an expired token must never execute"


# --------------------------------------------------------------------------- #
# Profiles (plan §3.5): safe/campaign plan-only; fast auto-commits single actions
# --------------------------------------------------------------------------- #
def _reset_lamp(fake_home) -> None:
    fake_home.mutations.clear()
    fake_home.calls.clear()
    fake_home.attempted.clear()
    fake_home.fail_next_mutation = False
    fake_home.states["desk-lamp"]["power"] = True


def test_safe_and_campaign_profiles_force_plan_only(fake_home, capsys):
    for profile in ("safe", "campaign"):
        _reset_lamp(fake_home)
        exit_code, stdout, stderr = _ask(capsys, PLAN_UTTERANCE, "--profile", profile, "--json")
        text = S.combined(stdout, stderr)
        assert exit_code == 0, f"profile {profile} failed (exit {exit_code}): {text[-1500:]}"
        envelope = S.json_payload(text)
        S.answer_envelope_ok(envelope)
        plan = S.plan_of(envelope)
        assert plan.get("profile") == profile, f"plan must carry the requested profile: {plan!r}"
        S.token_of(plan)
        assert fake_home.mutations == [], (
            f"profile {profile} must force plan-only for a mutating action, got {fake_home.mutations!r}"
        )


def test_fast_profile_auto_commits_a_reversible_single_action(fake_home, capsys):
    exit_code, stdout, stderr = _ask(capsys, PLAN_UTTERANCE, "--profile", "fast", "--json")
    text = S.combined(stdout, stderr)
    assert exit_code == 0, f"fast profile failed (exit {exit_code}): {text[-1500:]}"
    envelope = S.json_payload(text)
    S.answer_envelope_ok(envelope)
    assert fake_home.mutations == [("off", "desk-lamp")], (
        "fast (default) must auto plan+commit a reversible single-target action, got "
        f"{fake_home.mutations!r}"
    )


def test_destructive_batch_never_auto_executes_in_any_profile(fake_home, capsys):
    for profile in ("fast", "safe", "campaign"):
        _reset_lamp(fake_home)
        exit_code, stdout, stderr = _ask(capsys, BATCH_UTTERANCE, "--profile", profile, "--json")
        text = S.combined(stdout, stderr)
        assert exit_code == 0, f"batch plan failed for {profile} (exit {exit_code}): {text[-1500:]}"
        envelope = S.json_payload(text)
        S.answer_envelope_ok(envelope)
        plan = S.plan_of(envelope)
        blast = plan.get("blast_radius") or {}
        assert blast.get("kind") == "batch", f"the batch must be recognised as a batch: {plan!r}"
        assert int(blast.get("count", 0)) >= 2, f"blast_radius.count must name the targets: {plan!r}"
        S.token_of(plan)
        assert fake_home.mutations == [], (
            f"a destructive batch must never execute without --commit (profile {profile}), got "
            f"{fake_home.mutations!r}"
        )


# --------------------------------------------------------------------------- #
# Unknown utterance must fall through to the existing agent path (no dead end)
# --------------------------------------------------------------------------- #
def test_unknown_utterance_falls_through_to_the_agent(monkeypatch, capsys, cli_env):
    calls: list[str] = []

    import navig.commands.ai as ai_mod

    def fake_ask_ai(question, model, options):  # noqa: ANN001 - mirrors the real signature
        calls.append(str(question))

    monkeypatch.setattr(ai_mod, "ask_ai", fake_ask_ai)

    exit_code, _stdout, _stderr = S.invoke_cli(
        ["ask", "write me a limerick about a cheese that compiles"], capsys
    )
    assert exit_code == 0, "an unregistered utterance must never dead-end"
    assert calls == ["write me a limerick about a cheese that compiles"], (
        "the unknown utterance must fall through to the existing ask_ai path exactly once, got "
        f"{calls!r}"
    )
