"""The provenance views end-to-end: history, trace, changes, unfinished, conflicts —
project isolation, worktree/Windows path folding, privacy filtering, and honest
attribution (an agent's claim never renders as an observed lifecycle).

Fixtures build links directly (the derivation from transcripts is proven in
test_recall.py / test_recall_surfaces.py); what is under test here is the VIEW layer:
resolution, joins, filters and the for_model contract on every surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.recall import query
from navig.recall.model import Evidence, Record, Turn
from navig.recall.projects import ProjectResolver
from navig.recall.store import RecallStore

ALPHA = r"E:\proj\alpha"
BETA = r"E:\proj\beta"
SESSION = "11111111-1111-1111-1111-111111111111"
SID = f"cc:{SESSION}"


@pytest.fixture
def bank(tmp_path: Path) -> RecallStore:
    """A store where SESSION touched a file, planned a plan, made a commit — and one
    record (alpha-good) rests on that session, another (alpha-private) also rests on a
    below-agent chat, plus a beta-project record for isolation."""
    st = RecallStore(tmp_path / "views.db")
    st.upsert_source({"source_id": SID, "adapter": "claude_code", "project": "alpha",
                      "title": "built the widget", "cwd": ALPHA, "meta": {"git_branch": "feat/x"}})
    st.upsert_source({"source_id": "chats:chatgpt:priv", "adapter": "chats", "project": "alpha",
                      "title": "private chat", "meta": {"brain_level": "local"}})
    st.upsert_source({"source_id": "cc:22222222-2222-2222-2222-222222222222",
                      "adapter": "claude_code", "project": "beta", "title": "beta work",
                      "cwd": BETA, "meta": {}})
    st.add_links([
        (SID, "file:alpha/core/widget.py", "touched"),
        (SID, "plan:claude/widget-plan.md", "planned"),
        (SID, "commit:abc1234", "committed"),
        (SID, "file:alpha/core/widget.py", "linked"),
    ])
    st.add_turns(SID, [Turn(msg_id="m1", speaker="user_typed", text="ship the widget")])
    st.upsert_record(Record(id="rk-good", type="feature", title="The widget ships", project="alpha",
                            status="confirmed", status_basis="user", lifecycle="committed",
                            attrs={"lifecycle_basis": "observed"},
                            evidence=[Evidence(source_id=SID, msg_id="m1", speaker="user_typed",
                                               excerpt="ship the widget")]))
    st.upsert_record(Record(id="rk-priv", type="decision", title="widget secret plan", project="alpha",
                            status="confirmed", status_basis="user",
                            evidence=[Evidence(source_id=SID, msg_id="m1", speaker="user_typed",
                                               excerpt="ship the widget"),
                                      Evidence(source_id="chats:chatgpt:priv", msg_id="c1",
                                               speaker="user_typed", excerpt="secret")]))
    st.upsert_record(Record(id="rk-beta", type="feature", title="beta widget", project="beta",
                            status="confirmed", status_basis="user",
                            evidence=[Evidence(source_id="cc:22222222-2222-2222-2222-222222222222",
                                               msg_id="b1", speaker="user_typed", excerpt="beta")]))
    return st


def _resolver() -> ProjectResolver:
    return ProjectResolver(entries=[("alpha", ALPHA), ("beta", BETA)], home=r"C:\Users\someone")


# ── history ─────────────────────────────────────────────────


def test_file_history_includes_git_commits_no_session_printed(tmp_path: Path) -> None:
    """A change landed WITHOUT a transcript printing its sha (merge/land flows) still
    shows in file history — git's own file log ∪ session-linked commits. Real-benchmark
    miss q2 (2026-10-20): history previously listed only commits a session happened to
    print, so the newest commit touching a file was invisible."""
    import os
    import subprocess

    repo = tmp_path / "r"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repo), check=True,
                   capture_output=True, env=env)
    (repo / "x.py").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "x.py"], cwd=str(repo), check=True,
                   capture_output=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "landed without a printed sha"],
                   cwd=str(repo), check=True, capture_output=True, env=env)
    sha = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=str(repo),
                         capture_output=True, text=True, check=True, env=env).stdout.strip()

    st = RecallStore(tmp_path / "h.db")
    sid = "cc:11111111-1111-1111-1111-111111111111"
    st.upsert_source({"source_id": sid, "adapter": "claude_code", "project": "proj",
                      "title": "t", "cwd": str(repo), "meta": {}})
    st.add_turns(sid, [Turn(msg_id="m1", speaker="user_typed", text="edited x.py")])
    # the session touched the file but NEVER committed it (no committed link exists)
    st.add_links([(sid, "file:proj/x.py", "touched")])

    out = query.history("file:proj/x.py", project="proj", root=str(repo),
                        resolver=ProjectResolver(entries=[("proj", str(repo))],
                                                 home=r"C:\Users\x"),
                        store=st)
    shas = [c["sha"] for c in out["commits"]]
    assert any(s.startswith(sha[:7]) for s in shas), f"git commit missing: {shas}"
    assert out["commits"][0]["known"] is True


def test_history_merges_sessions_commits_and_records(bank) -> None:
    out = query.history("file:alpha/core/widget.py", store=bank)
    assert out["key"] == "file:alpha/core/widget.py"
    assert [s["session_id"] for s in out["sessions"]] == [SESSION]
    assert [c["sha"] for c in out["commits"]] == ["abc1234"]
    assert {r["id"] for r in out["records"]} == {"rk-good", "rk-priv"}
    kinds = {e["kind"] for e in out["timeline"]}
    assert kinds == {"session", "commit", "record"}


def test_history_resolves_worktree_and_relative_shapes(bank) -> None:
    """A worktree-typed path and the main-checkout path are the same history."""
    res = _resolver()
    wt = query.history(r"E:\proj\alpha\.dev\worktrees\feat-x\core\widget.py",
                       store=bank, resolver=res)
    plain = query.history("file:alpha/core/widget.py", store=bank, resolver=res)
    assert wt["key"] == plain["key"] == "file:alpha/core/widget.py"
    assert wt["count"] == plain["count"]


def test_history_outside_known_projects_raises(bank) -> None:
    with pytest.raises(ValueError, match="not inside a known project"):
        query.history("E:\\elsewhere\\x.py", store=bank)


def test_history_keyword_filters_records(bank) -> None:
    out = query.history("file:alpha/core/widget.py", keyword="secret", store=bank)
    assert {r["id"] for r in out["records"]} == {"rk-priv"}


def test_history_for_model_withholds_the_private_chat_record(bank) -> None:
    """rk-priv rests partly on a below-agent chat → withheld from model reads (even though
    it is also linked to the session); local reads still see it."""
    vis = query.history("file:alpha/core/widget.py", for_model=True, store=bank)
    assert {r["id"] for r in vis["records"]} == {"rk-good"}
    assert "rk-priv" not in {e["ref"] for e in vis["timeline"] if e["kind"] == "record"}
    local = query.history("file:alpha/core/widget.py", store=bank)
    assert "rk-priv" in {r["id"] for r in local["records"]}


# ── trace ───────────────────────────────────────────────────


def test_trace_session_shows_its_edges(bank) -> None:
    out = query.trace(SESSION, store=bank)
    assert out["kind"] == "session"
    assert "file:alpha/core/widget.py" in out["files"]
    assert "plan:claude/widget-plan.md" in out["plans"]
    assert [c["sha"] for c in out["commits"]] == ["abc1234"]
    assert {r["id"] for r in out["records"]} == {"rk-good", "rk-priv"}


def test_trace_commit_shows_sessions_even_when_git_cannot_confirm(bank) -> None:
    out = query.trace("abc1234", store=bank, root=None)
    assert out["kind"] == "commit"
    assert out["commit"]["known"] is False  # no git root given — honest, not fabricated
    assert [s["session_id"] for s in out["sessions"]] == [SESSION]
    assert {r["id"] for r in out["records"]} == {"rk-good", "rk-priv"}


def test_trace_rejects_garbage(bank) -> None:
    with pytest.raises(ValueError):
        query.trace("not-a-sha-not-a-uuid", store=bank)
    with pytest.raises(LookupError):
        query.trace("99999999-9999-9999-9999-999999999999", store=bank)


# ── changes / unfinished / isolation ────────────────────────


def test_changes_reads_the_cache_and_scopes_by_project(bank) -> None:
    bank.upsert_commits([{"sha": "a" * 40, "subject": "alpha work", "project": "alpha",
                          "committer_ts": "2026-10-18T00:00:00"},
                         {"sha": "b" * 40, "subject": "beta work", "project": "beta",
                          "committer_ts": "2026-10-18T00:00:00"}])
    out = query.changes("alpha", store=bank)
    assert [c["subject"] for c in out["commits"]] == ["alpha work"]
    # alpha's cached commit knows the session that made it? (not linked by sha here) — no sessions
    assert all("sessions" in c for c in out["commits"])
    with pytest.raises(ValueError, match="name a project"):
        query.changes(None, store=bank)


def test_unfinished_is_type_scoped_oldest_first_and_never_verified(bank) -> None:
    for rid, life, rtype, impl, upd in (
        ("r-plan-old", "proposed", "plan", "unknown", "2026-01-01"),
        ("r-task-new", "implemented_uncommitted", "task", "partial", "2026-09-01"),
        ("r-done", "verified", "plan", "verified", "2026-01-01"),
        ("r-dec", "proposed", "decision", "unknown", "2026-01-01"),
        ("r-built", "committed", "task", "verified", "2026-01-01"),
    ):
        bank.upsert_record(Record(id=rid, type=rtype, title=rid, project="alpha",
                                  lifecycle=life, impl=impl, updated=upd, created=upd))
    out = query.unfinished("alpha", store=bank)
    ids = [r["id"] for r in out["records"]]
    # oldest first: plan(old) then task(new); verified-lifecycle, non-plan/task types and
    # impl=verified records are all out.
    assert ids == ["r-plan-old", "r-task-new"]
    assert out["records"][0]["lifecycle"] == "proposed"


def test_unfinished_is_project_isolated(bank) -> None:
    bank.upsert_record(Record(id="r-beta-task", type="task", title="beta task", project="beta",
                              lifecycle="proposed", updated="2026-01-01"))
    ids = [r["id"] for r in query.unfinished("alpha", store=bank)["records"]]
    assert "r-beta-task" not in ids


def test_project_isolation_across_views(bank) -> None:
    """alpha's history never leaks beta's session/records and vice versa."""
    beta_sess = "22222222-2222-2222-2222-222222222222"
    out = query.history("file:alpha/core/widget.py", store=bank)
    assert all(s["session_id"] != beta_sess for s in out["sessions"])
    assert "rk-beta" not in {r["id"] for r in out["records"]}


def test_conflicts_reads_without_writing_and_filters_for_model(bank) -> None:
    bank.upsert_record(Record(id="rk-c1", type="decision", title="Widget: yes", project="alpha",
                              canonical_key="widget decision", status="confirmed",
                              status_basis="user",
                              evidence=[Evidence(source_id="chats:chatgpt:priv", msg_id="c9",
                                                 speaker="user_typed", excerpt="no")]))
    bank.upsert_record(Record(id="rk-c2", type="decision", title="Widget: no", project="alpha",
                              canonical_key="widget decision", status="rejected",
                              status_basis="user"))
    read_only = query.conflicts("alpha", reconcile_run=False, store=bank)
    assert read_only["reviews"] == []  # nothing recorded yet — reading never writes
    ran = query.conflicts("alpha", store=bank)
    assert ran["reconcile"]["found"] == 1
    again = query.conflicts("alpha", reconcile_run=False, store=bank)
    assert len(again["reviews"]) == 1
    # for_model: the private-chat-backed side hides the whole finding
    hidden = query.conflicts("alpha", reconcile_run=False, for_model=True, store=bank)
    assert hidden["reviews"] == []
    shown = query.conflicts("alpha", reconcile_run=False, store=bank)
    assert len(shown["reviews"]) == 1


# ── session_brief (the SessionStart section) ────────────────


def test_session_brief_lines_and_byte_budget(bank) -> None:
    bank.upsert_record(Record(id="r-dec", type="decision", title="Ship the widget", project="alpha",
                              status="confirmed", status_basis="user", endorsement="user_endorsed",
                              updated="2026-10-12", created="2026-10-12"))
    bank.upsert_record(Record(id="r-old-dec", type="decision", title="Use the old way", project="beta",
                              status="confirmed", status_basis="user"))
    bank.upsert_record(Record(id="r-plan", type="plan", title="The widget plan", project="alpha",
                              lifecycle="proposed", updated="2026-10-01"))
    bank.upsert_record(Record(id="r-sup", type="feature", title="Old widget approach", project="alpha",
                              status="superseded", updated="2026-10-11"))
    out = query.session_brief("alpha", store=bank)
    assert "decision · confirmed: Ship the widget" in out
    assert "Use the old way" not in out  # beta project stays out
    assert "unfinished: 1 plan/task(s) awaiting verification" in out
    assert "latest supersession: Old widget approach" in out
    assert all(ln.startswith("  ") for ln in out.splitlines())
    # byte budget: hard-truncated, never over (budget + the ellipsis's utf-8 bytes)
    budget = 40
    tiny = query.session_brief("alpha", budget=budget, store=bank)
    assert len(tiny.encode("utf-8")) <= budget + len("…".encode())


def test_session_brief_fails_open_and_needs_a_project(bank) -> None:
    assert query.session_brief(None, store=bank) == ""
    # the bank's only alpha decision rests on a below-agent chat → filtered out,
    # so a model-facing brief of this bank is empty (privacy, not emptiness, is the point)
    assert query.session_brief("alpha", store=bank) == ""

    class _Broken:
        def list_records(self, *a, **k):
            raise RuntimeError("bank on fire")

    assert query.session_brief("alpha", store=_Broken()) == ""
