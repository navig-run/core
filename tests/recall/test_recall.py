"""Navig Recall: source parsing (speaker attribution, selective policy, cursors, projects,
audits) and the bank end to end (ingest, idempotence, rewrites, rebuild, round trip).

Every transcript here is synthetic, built by the helpers below; no real conversation text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from navig.recall.archive import Archive
from navig.recall.ingest import Ingestor
from navig.recall.mdtables import find_tables
from navig.recall.model import Evidence, Record
from navig.recall.pass0 import merge
from navig.recall.projects import ProjectResolver
from navig.recall.redact import redact_secrets
from navig.recall.sources import claude_code, codex
from navig.recall.sources import session_audit as sa
from navig.recall.store import RecallStore
from navig.recall.vault import Vault, parse, render

# ── synthetic transcript builders ────────────────────────────

TS = "2026-10-01T10:00:{:02d}.000Z"


class CC:
    """Builds a Claude Code transcript line by line, in the record shapes measured on 2.1.x."""

    def __init__(self, session: str = "11111111-2222-3333-4444-555555555555", cwd: str = r"E:\proj\alpha",
                 prefix: str = "u"):
        self.session, self.cwd, self.n, self.prefix = session, cwd, 0, prefix
        self.lines: list[str] = []

    def _base(self, rtype: str, **extra: Any) -> dict[str, Any]:
        self.n += 1
        return {"type": rtype, "uuid": f"{self.prefix}{self.n:04d}", "parentUuid": f"{self.prefix}{self.n - 1:04d}",
                "sessionId": self.session, "timestamp": TS.format(self.n % 60), "cwd": self.cwd,
                "gitBranch": "main", "version": "2.1.300", "isSidechain": False, **extra}

    def user(self, content: Any, **extra: Any) -> CC:
        self.lines.append(json.dumps(self._base("user", message={"role": "user", "content": content}, **extra)))
        return self

    def assistant(self, blocks: list[dict[str, Any]]) -> CC:
        self.lines.append(json.dumps(self._base("assistant", message={"role": "assistant", "content": blocks})))
        return self

    def say(self, text: str) -> CC:
        return self.assistant([{"type": "text", "text": text}])

    def tool_use(self, tid: str, name: str, inp: dict[str, Any]) -> CC:
        return self.assistant([{"type": "tool_use", "id": tid, "name": name, "input": inp}])

    def tool_result(self, tid: str, content: Any, tur: Any = None, is_error: bool = False) -> CC:
        block = {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": is_error}
        return self.user([block], toolUseResult=tur if tur is not None else {})

    def raw(self, line: str) -> CC:
        self.lines.append(line)
        return self

    def title(self, text: str) -> CC:
        self.lines.append(json.dumps({"type": "ai-title", "aiTitle": text, "sessionId": self.session}))
        return self

    def write(self, home: Path, project_dir: str = "E--proj-alpha", *, partial_tail: str = "") -> Path:
        d = home / "projects" / project_dir
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{self.session}.jsonl"
        p.write_text("\n".join(self.lines) + "\n" + partial_tail, encoding="utf-8")
        return p


def rich_session(cwd: str = r"E:\proj\alpha") -> CC:
    """A session exercising every kept and skipped record kind."""
    cc = CC(cwd=cwd)
    cc.title("Telegram controller for OBS")
    cc.user("We could build a Telegram controller for OBS. <task-notification>agent found the websocket port is 4455"
            "</task-notification> <pasted_content id=\"9\">pasted log line</pasted_content>"
            "<system-reminder>harness noise</system-reminder>")
    cc.assistant([{"type": "thinking", "thinking": "private reasoning"},
                  {"type": "text", "text": "## Options\n\n| option | cost |\n|---|---|\n| obs-websocket | free |\n"
                                           "| plugin | high |\n"}])
    cc.tool_use("t-bash", "Bash", {"command": "ls"}).tool_result("t-bash", "file-a\nfile-b")
    cc.tool_use("t-ask", "AskUserQuestion", {"questions": []})
    cc.tool_result("t-ask", "answered", tur={
        "questions": [{"question": "Which transport?", "header": "Transport",
                       "options": [{"label": "WebSocket"}, {"label": "HTTP"}]}],
        "answers": {"Which transport?": "WebSocket"}})
    cc.tool_use("t-plan", "ExitPlanMode", {"plan": "# OBS remote plan\n\n## Steps\n1. connect\n2. map buttons"})
    cc.tool_result("t-plan", "User has approved your plan.", tur={"plan": "# OBS remote plan"})
    cc.tool_use("t-agent", "Agent", {"description": "Research OBS websocket", "prompt": "…"})
    cc.tool_result("t-agent", [{"type": "text", "text": "OBS websocket v5 listens on 4455 and needs auth."}])
    cc.user("ok", isCompactSummary=True)
    cc.raw("{not json")
    return cc


def write_audit(root: Path) -> Path:
    """A miniature session-audit root in the navig layout."""
    (root / "registry").mkdir(parents=True)
    (root / "projects" / "E--proj-alpha").mkdir(parents=True)
    (root / "reports").mkdir()
    (root / "README.md").write_text("# Session Audit\n\nscope alpha\n", encoding="utf-8")
    features = {"schemaVersion": 1, "updatedAt": "2026-10-15T00:00:00Z", "features": [
        {"featureId": "obs-remote", "canonicalName": "OBS remote control over Telegram", "aliases": ["obs bot"],
         "status": "PARTIAL", "confidence": "probable", "evidence": ["digest: edits to obs.py"],
         "sessions": [{"id": "11111111-2222-3333-4444-555555555555", "role": "originator", "auditRef": "x"}],
         "plans": [], "touchedFiles": ["obs.py"], "completionTask": {"text": "Finish mobile controls"},
         "mergeHistory": []},
        {"featureId": "dark-mode", "canonicalName": "Dark mode", "aliases": [], "status": "IMPLEMENTED \u2014 UNVERIFIED",
         "confidence": "probable", "evidence": [], "sessions": [], "plans": [], "touchedFiles": [],
         "completionTask": None, "mergeHistory": []},
        {"featureId": "voice-notes", "canonicalName": "Voice notes", "status": "IDEA ONLY", "sessions": []},
    ]}
    (root / "registry" / "features.json").write_text(json.dumps(features), encoding="utf-8")
    (root / "decisions.md").write_text(
        "# Durable decisions\n\n| # | decision | date | session | status today |\n|---|---|---|---|---|\n"
        "| 1 | Use Tauri, not Electron | 2026-06-25 | `2715f658` | **in force** — `obs-remote` |\n"
        "| 2 | Ship navig-media | 2026-07-07 | `e6cc102d` | **reversed** — re-created later |\n",
        encoding="utf-8")
    (root / "recovered-ideas.md").write_text(
        "# Recovered ideas\n\n## Top 10\n\n| rank | idea | value | recommendation |\n|---|---|---|---|\n"
        "| 1 | **Guard stale gate trees** | H | **adopt** |\n| 2 | Old crate name | L | reject |\n",
        encoding="utf-8")
    (root / "conflicts.md").write_text(
        "# Conflicts\n\n## C-001 · Borderline merge pairs\n\nKeep separate.\n\n## C-002 · Lens format\n\nOpen.\n",
        encoding="utf-8")
    (root / "relationships.json").write_text(json.dumps({"edges": [
        {"from": "aaaa", "to": "bbbb", "type": "continuation-of"}]}), encoding="utf-8")
    (root / "reports" / "unified-roadmap.md").write_text("# Roadmap\n\n## Phase 0\n\nLand branches.\n",
                                                          encoding="utf-8")
    return root


# ── sources ──────────────────────────────────────────────────


def _by_speaker(turns):
    out: dict[str, list] = {}
    for t in turns:
        out.setdefault(t.speaker, []).append(t)
    return out


def test_injected_blocks_are_not_attributed_to_the_user(tmp_path: Path) -> None:
    p = rich_session().write(tmp_path)
    res = claude_code.parse(p)
    sp = _by_speaker(res.turns)
    typed = [t.text for t in sp["user_typed"]]
    assert typed == ["We could build a Telegram controller for OBS."], typed
    assert sp["agent_report"][0].text == "agent found the websocket port is 4455"
    assert sp["user_pasted"][0].text == "pasted log line"
    assert not any("harness noise" in t.text for t in res.turns)


def test_policy_keeps_decision_evidence_and_skips_noise(tmp_path: Path) -> None:
    res = claude_code.parse(rich_session().write(tmp_path))
    texts = " ".join(t.text for t in res.turns)
    assert "private reasoning" not in texts  # thinking skipped
    assert "file-a" not in texts  # Bash output skipped
    sp = _by_speaker(res.turns)
    answer = sp["user_answer"][0]
    assert answer.meta["pairs"][0] == {"question": "Which transport?", "answer": "WebSocket",
                                       "header": "Transport", "options": ["WebSocket", "HTTP"]}
    plan = [t for t in res.turns if t.kind == "plan"]
    assert len(plan) == 1 and plan[0].text.startswith("# OBS remote plan")
    verdict = sp["user_plan_verdict"][0]
    assert verdict.meta["approved"] is True
    descriptions = {t.meta.get("description") for t in sp["agent_report"] if t.kind == "report"}
    assert "Research OBS websocket" in descriptions
    assert res.title == "Telegram controller for OBS"
    assert res.bad_lines == 1
    assert res.cwd == r"E:\proj\alpha"


def test_compact_summary_is_skipped(tmp_path: Path) -> None:
    res = claude_code.parse(rich_session().write(tmp_path))
    assert not any(t.text == "ok" for t in res.turns)


def test_rejected_plan_is_not_approved(tmp_path: Path) -> None:
    cc = CC().tool_use("t1", "ExitPlanMode", {"plan": "# Risky plan"})
    cc.tool_result("t1", "User chose to stay in plan mode", tur="Error", is_error=True)
    res = claude_code.parse(cc.write(tmp_path))
    verdict = [t for t in res.turns if t.speaker == "user_plan_verdict"][0]
    assert verdict.meta["approved"] is False and verdict.text.startswith("Plan not approved")


def test_partial_last_line_is_left_for_the_next_read(tmp_path: Path) -> None:
    cc = CC().user("first")
    p = cc.write(tmp_path, partial_tail='{"type": "user", "uuid": "half')
    res = claude_code.parse(p)
    assert [t.text for t in res.turns] == ["first"]
    assert res.end_offset < p.stat().st_size


def test_tool_result_after_cursor_resolves_through_pending(tmp_path: Path) -> None:
    cc = CC().tool_use("t-ask", "AskUserQuestion", {"questions": []})
    p = cc.write(tmp_path)
    first = claude_code.parse(p)
    assert "t-ask" in first.meta["pending_tools"]
    cc.tool_result("t-ask", "x", tur={"questions": [], "answers": {"Ship it?": "Yes"}})
    cc.write(tmp_path)
    second = claude_code.parse(p, first.end_offset, first.meta["pending_tools"])
    assert [t.speaker for t in second.turns] == ["user_answer"]
    assert "t-ask" not in second.meta["pending_tools"]


def test_discover_dedupes_the_same_session_seen_twice(tmp_path: Path) -> None:
    cc = CC().user("hello")
    cc.write(tmp_path, "E--proj-alpha")
    cc.write(tmp_path, "e--proj-alpha-copy")  # junction-style duplicate folder
    refs = list(claude_code.discover([tmp_path]))
    assert len(refs) == 1


def test_codex_skips_injected_context(tmp_path: Path) -> None:
    f = tmp_path / "sessions" / "2026" / "10" / "01" / "rollout-2026-10-01T10-00-00-abcd1234-0000-0000-0000-000000000000.jsonl"
    f.parent.mkdir(parents=True)
    rows = [
        {"type": "session_meta", "payload": {"id": "x", "cwd": r"E:\proj\beta"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "<environment_context>x"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": "rules"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "Rename the CLI verb"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                              "content": [{"type": "output_text", "text": "Done."}]}},
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    refs = list(codex.discover(tmp_path))
    assert refs[0].source_id == "codex:abcd1234-0000-0000-0000-000000000000"
    res = codex.parse(Path(refs[0].path))
    assert [(t.speaker, t.text) for t in res.turns] == [("user_typed", "Rename the CLI verb"), ("assistant", "Done.")]
    assert res.cwd == r"E:\proj\beta"


def test_project_resolver_folds_worktrees_and_is_case_insensitive(tmp_path: Path) -> None:
    r = ProjectResolver(entries=[("navig", r"E:\projects\apps\navig"), ("apps", r"E:\projects\apps")],
                        home=r"C:\Users\someone")
    assert r.resolve(r"e:\projects\apps\navig\core").id == "navig"
    assert r.resolve(r"E:\projects\apps\navig\.dev\worktrees\recall-s1\core").id == "navig"
    assert r.resolve(r"E:\projects\apps\other").id == "apps"
    assert r.resolve(r"C:\Users\someone\.codex\worktrees\a1b2\navig").id == "navig"
    assert r.resolve("").id == "shared"
    assert r.by_claude_slug("e--projects-apps-navig") == "navig"


def test_tables_in_code_fences_are_ignored() -> None:
    text = "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```\n\nReal:\n\n| k | `a|b` |\n|---|---|\n| x | y |\n"
    tables = find_tables(text)
    assert len(tables) == 1
    assert tables[0].columns == ["k", "`a|b`"] and tables[0].caption == "Real:"


def test_session_audit_records_carry_audit_basis(tmp_path: Path) -> None:
    root = write_audit(tmp_path / "session-audit")
    recs, links, events = sa.build_records(root, "alpha")
    by_ext = {r.ext_id: r for r in recs if r.ext_id}
    partial = by_ext["obs-remote"]
    assert (partial.type, partial.status, partial.impl, partial.status_basis) == ("feature", "exploring", "partial", "audit")
    assert partial.endorsement == "none"  # derived analysis never endorses
    assert by_ext["dark-mode"].impl == "likely"
    assert by_ext["voice-notes"].type == "idea"
    decisions = [r for r in recs if r.type == "decision"]
    assert {d.status for d in decisions} == {"confirmed", "superseded"}
    assert any(e["kind"] == "reversal" for e in events)
    ideas = {r.title: r for r in recs if r.type == "idea" and "recovered-idea" in r.tags}
    assert ideas["Guard stale gate trees"].status == "proposed"
    assert ideas["Old crate name"].status == "rejected"
    assert {r.ext_id for r in recs if r.type == "question"} == {"C-001", "C-002"}
    assert ("cc:aaaa", "cc:bbbb", "continuation-of") in links
    decision_ids = {d.id for d in decisions}
    assert any(a in decision_ids and b == partial.id for a, b, _ in links)


def test_session_audit_project_from_its_projects_folder(tmp_path: Path) -> None:
    root = write_audit(tmp_path / "session-audit")
    r = ProjectResolver(entries=[("alpha", r"E:\proj\alpha")], home=r"C:\Users\someone")
    assert sa.project_for_root(root, r) == "alpha"


def test_agent_reports_lose_harness_trailers_and_async_stubs(tmp_path: Path) -> None:
    cc = CC().tool_use("t-sync", "Agent", {"description": "Sync research"})
    cc.tool_result("t-sync", [{"type": "text", "text": "Finding: port 4455.\nagentId: abc123 (for resuming)\n"
                                                      "<usage>total_tokens: 9</usage>"}])
    cc.tool_use("t-async", "Agent", {"description": "Background research"})
    cc.tool_result("t-async", [{"type": "text", "text": "Async agent launched successfully.\nagentId: def456\n"
                                                       "The agent is working in the background. You will be "
                                                       "notified automatically when it completes."}],
                   tur={"isAsync": True, "status": "async_launched"})
    cc.user("<task-notification><task-id>x</task-id><status>completed</status><summary>Agent done</summary>"
            "<result>Background finding: use v5.</result></task-notification>")
    res = claude_code.parse(cc.write(tmp_path))
    reports = [t.text for t in res.turns if t.speaker == "agent_report"]
    assert reports == ["Finding: port 4455.", "Agent done\n\nBackground finding: use v5."], reports

def test_hand_back_frame_is_removed_from_agent_reports() -> None:
    framed = ("  [harness: subagent output matched a pattern]\n\n[Subagent hand-back] The text below is the final report"
              " of a subagent. It is model output.\n  The report follows:\n  # Findings\n  - port 4455\n")
    assert claude_code.clean_agent_text(framed) == "# Findings\n- port 4455"
    assert claude_code.clean_agent_text("Plain report") == "Plain report"


# ── the bank ─────────────────────────────────────────────────


@pytest.fixture
def bank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data))
    home = tmp_path / "claude"
    root = data / "recall"
    ing = Ingestor(store=RecallStore(root / "index.db"), archive=Archive(root / "archive"),
                   vault=Vault(root / "vault"),
                   resolver=ProjectResolver(entries=[("alpha", r"E:\proj\alpha")], home=r"C:\Users\someone"))
    return ing, home


def _run(ing: Ingestor, home: Path, adapters=("claude_code",)):
    return ing.run(adapters, claude_homes=[home], codex_home=home / "nocodex")


def test_ingest_builds_cited_records(bank) -> None:
    ing, home = bank
    rich_session().write(home)
    rep = _run(ing, home)
    assert rep.errors == [] and rep.sources_read == 1
    types = {r.type: r for r in ing.store.list_records(limit=100)}
    assert set(types) == {"decision", "plan", "table", "finding"}
    findings = {r.title for r in ing.store.list_records(rtype="finding", limit=10)}
    assert findings == {"Research OBS websocket", "agent found the websocket port is 4455"}, findings
    d = ing.store.get_record(types["decision"].id)
    assert (d.status, d.status_basis, d.endorsement) == ("confirmed", "user", "user_endorsed")
    assert d.project == "alpha"
    assert d.evidence and d.evidence[0].speaker == "user_answer"
    plan = types["plan"]
    assert (plan.status, plan.endorsement) == ("approved", "user_endorsed")
    table = ing.store.get_record(types["table"].id)
    assert "| obs-websocket | free |" in table.body and table.title == "Options"
    # every cited turn exists and can be opened
    for rec in ing.store.list_records(limit=100):
        for ev in ing.store.get_record(rec.id).evidence:
            assert ing.store.turn_window(ev.source_id, ev.msg_id), (rec.type, ev.msg_id)


def test_reingest_is_a_noop_and_append_reads_only_the_delta(bank) -> None:
    ing, home = bank
    cc = rich_session()
    cc.write(home)
    _run(ing, home)
    before = ing.store.stats()
    again = _run(ing, home)
    assert again.unchanged == 1 and again.turns_added == 0 and ing.store.stats() == before
    cc.user("Also add a mobile layout")
    cc.write(home)
    delta = _run(ing, home)
    assert delta.turns_added == 1 and delta.rewritten == 0
    assert ing.store.search_turns("mobile layout")[0]["speaker"] == "user_typed"


def test_rewritten_file_is_reread_without_losing_knowledge(bank) -> None:
    ing, home = bank
    p = rich_session().write(home)
    _run(ing, home)
    n_records = ing.store.stats()["records"]
    p.write_text(CC(prefix="r").user("completely different start").lines[0] + "\n", encoding="utf-8")
    rep = _run(ing, home)
    assert rep.rewritten == 1
    assert ing.store.stats()["records"] == n_records  # nothing derived is deleted
    assert ing.store.search_turns("completely different")


def test_missing_source_keeps_its_archive(bank) -> None:
    ing, home = bank
    p = rich_session().write(home)
    _run(ing, home)
    p.unlink()
    rep = _run(ing, home)
    assert rep.sources_read == 0
    assert list(ing.archive.read_turns("claude_code", "cc:11111111-2222-3333-4444-555555555555"))


def test_reindex_rebuilds_an_identical_index(bank, tmp_path: Path) -> None:
    ing, home = bank
    rich_session().write(home)
    write_audit(home / "session-audit")
    ing.run(("claude_code", "session_audit"), claude_homes=[home], codex_home=home / "x")
    before = ing.store.stats()
    hits = [r["id"] for r in ing.store.search_records("OBS")]
    counts = ing.reindex()
    after = ing.store.stats()
    assert after["sources"] == before["sources"]
    assert after["turns"] == before["turns"] and after["records"] == before["records"]
    assert after["events"] == before["events"] and after["links"] == before["links"]
    assert [r["id"] for r in ing.store.search_records("OBS")] == hits
    assert counts["records"] == before["records"]


def test_secrets_are_redacted_before_archive(bank) -> None:
    ing, home = bank
    CC().user("my key is sk-ant-abcdefghijklmnop1234 and run with -p 8080").write(home)
    _run(ing, home)
    stored = " ".join(t.text for t in ing.archive.read_turns("claude_code", "cc:11111111-2222-3333-4444-555555555555"))
    assert "sk-ant-abcdefghijklmnop1234" not in stored
    assert "-p 8080" in stored  # the MySQL -p rule is off for prose


def test_multilingual_search(bank) -> None:
    ing, home = bank
    CC().user("Принято решение: использовать Tauri").user("Le niveau est très élevé").write(home)
    _run(ing, home)
    assert ing.store.search_turns("решение")
    assert ing.store.search_turns("eleve")  # diacritics folded


def test_session_audit_import_is_idempotent(bank) -> None:
    ing, home = bank
    write_audit(home / "session-audit")
    first = ing.run(("session_audit",), claude_homes=[home])
    assert first.errors == [] and first.records_created >= 8
    second = ing.run(("session_audit",), claude_homes=[home])
    assert second.records_created == 0 and second.records_updated == 0
    assert ing.store.search_turns("Land branches")  # report sections are searchable


def test_vault_round_trip_is_stable() -> None:
    rec = Record(id="rk-abc", type="plan", title="Plan: ü & ## tricky", summary="line one\nline two",
                 body="# Heading\n\n## Steps\n\n- a\n<!-- not a marker -->\n", project="alpha",
                 aliases=["x"], tags=["plan"], attrs={"k": [1, 2]},
                 evidence=[Evidence("cc:s", "u1", "assistant", "quote ## inside", "2026-10-01")])
    text = render(rec)
    again = parse(text)
    assert again == rec
    assert render(again) == text


def test_merge_never_lets_weaker_evidence_override_the_user() -> None:
    user = Record(id="rk-1", type="plan", title="P", status="approved", status_basis="user", updated="2026-10-02",
                  evidence=[Evidence("cc:a", "1", "user_plan_verdict", "Plan approved")])
    later_assistant = Record(id="rk-1", type="plan", title="P", status="proposed", status_basis="assistant",
                             updated="2026-10-03", evidence=[Evidence("cc:b", "9", "assistant", "same plan")])
    merged, events = merge(user, later_assistant)
    assert merged.status == "approved" and len(merged.evidence) == 2
    assert [k for k, _ in events] == ["evidence"]


def test_redact_keeps_ordinary_flags() -> None:
    assert redact_secrets("npm -p 3000") == "npm -p 3000"
    assert "ghp_" in redact_secrets("token ghp_" + "a" * 30) and "a" * 30 not in redact_secrets("ghp_" + "a" * 30)


# ── session links: which session touched, planned or committed what ──


def _linked_session(cwd: str = r"E:\proj\alpha", session: str = "11111111-2222-3333-4444-555555555555") -> CC:
    cc = CC(session=session, cwd=cwd).title("Wire the store").user("fix the store please")
    cc.tool_use("e1", "Edit", {"file_path": cwd + r"\core\store.py", "old_string": "a", "new_string": "b"})
    cc.tool_result("e1", "The file has been updated.")
    cc.tool_use("e2", "Write", {"file_path": cwd + r"\core\broken.py", "content": "x"})
    cc.tool_result("e2", "permission denied", is_error=True)
    cc.tool_use("b1", "Bash", {"command": "git add core/store.py && git commit -m 'fix' -- core/store.py"})
    cc.tool_result("b1", "[feat/store a1b2c3d4e] fix\n 1 file changed, 1 insertion(+)")
    cc.tool_use("b2", "Bash", {"command": "git log --oneline -1"})
    cc.tool_result("b2", "[not a commit 1234567] output")
    cc.tool_use("p1", "ExitPlanMode", {"plan": "# Plan\n1. do it"})
    cc.tool_result("p1", "User has approved your plan.",
                   tur={"plan": "# Plan\n1. do it", "filePath": r"C:\Users\someone\.claude\plans\brave-plan.md"})
    return cc


def test_parser_records_only_successful_edits_commits_and_plan_files(tmp_path: Path) -> None:
    res = claude_code.parse(_linked_session().write(tmp_path))
    assert res.meta["touched"] == [r"E:\proj\alpha\core\store.py"]  # the failed Write is not a touch
    assert res.meta["commits"] == ["a1b2c3d4e"]  # `git log` output is never read as a commit
    assert res.meta["planned"] == [r"C:\Users\someone\.claude\plans\brave-plan.md"]
    assert res.meta["links_v"] >= 1


def test_target_keys_fold_worktrees_and_plans() -> None:
    from navig.recall.session_links import query_key, target_key

    r = ProjectResolver(entries=[("alpha", r"E:\proj\alpha")], home=r"C:\Users\someone")
    assert target_key(r"E:\proj\alpha\core\store.py", r) == "file:alpha/core/store.py"
    assert target_key(r"e:\PROJ\alpha\.dev\worktrees\fix-x\core\store.py", r) == "file:alpha/core/store.py"
    assert target_key(r"C:\Users\someone\.claude\plans\brave-plan.md", r) == "plan:claude/brave-plan.md"
    assert target_key(r"C:\elsewhere\notes.txt", r) is None  # in no project → no link
    assert target_key("core/store.py", r) is None  # relative paths are resolved by the caller
    assert query_key("A1B2C3D4E5F6A7", r) == "commit:a1b2c3d4e5f6"


def test_ingest_links_sessions_and_they_survive_reindex(bank) -> None:
    from navig.recall.session_links import find_sessions

    ing, home = bank
    _linked_session().write(home)
    _linked_session(session="99999999-2222-3333-4444-555555555555").write(home, "E--proj-alpha-2")
    assert _run(ing, home).errors == []
    rows = find_sessions(ing.store, r"E:\proj\alpha\core\store.py", resolver=ing.resolver)
    assert {r.session_id for r in rows} == {"11111111-2222-3333-4444-555555555555",
                                            "99999999-2222-3333-4444-555555555555"}
    assert rows[0].rels == ("touched",) and rows[0].project == "alpha" and rows[0].branch == "main"
    assert rows[0].to_dict()["open_uri"].startswith("vscode://anthropic.claude-code/open?session=")
    # a full commit id finds the session that printed the short one
    assert len(find_sessions(ing.store, "commit:a1b2c3d4e5f60718", resolver=ing.resolver)) == 2
    assert len(find_sessions(ing.store, r"C:\Users\someone\.claude\plans\brave-plan.md", resolver=ing.resolver)) == 2
    assert find_sessions(ing.store, r"E:\proj\alpha\core\broken.py", resolver=ing.resolver) == []
    before = ing.store.stats()["links"]
    assert _run(ing, home).unchanged == 2  # re-ingest adds nothing
    assert ing.store.stats()["links"] == before
    ing.reindex()
    assert ing.store.stats()["links"] == before
    with pytest.raises(ValueError):
        find_sessions(ing.store, r"C:\elsewhere\notes.txt", resolver=ing.resolver)


def test_manual_link_and_unlink_are_replayed_by_reindex(bank) -> None:
    from navig.recall import session_links as sl

    ing, home = bank
    sid = "11111111-2222-3333-4444-555555555555"
    _linked_session().write(home)
    _run(ing, home)
    doc = r"E:\proj\alpha\docs\design.md"
    assert sl.link(ing.store, ing.vault, sid, doc, ing.resolver) is True
    assert sl.link(ing.store, ing.vault, sid, doc, ing.resolver) is False  # idempotent
    assert [r.rels for r in sl.find_sessions(ing.store, doc, resolver=ing.resolver)] == [("linked",)]
    # unlink a derived link too: the operator decides what is related
    assert sl.unlink(ing.store, ing.vault, sid, r"E:\proj\alpha\core\store.py", ing.resolver) == 1
    ing.reindex()
    assert [r.rels for r in sl.find_sessions(ing.store, doc, resolver=ing.resolver)] == [("linked",)]
    assert sl.find_sessions(ing.store, r"E:\proj\alpha\core\store.py", resolver=ing.resolver) == []
    with pytest.raises(ValueError):
        sl.link(ing.store, ing.vault, "not-a-uuid", doc, ing.resolver)
    with pytest.raises(LookupError):
        sl.link(ing.store, ing.vault, "00000000-0000-0000-0000-000000000000", doc, ing.resolver)


def test_sources_read_before_session_links_are_relinked_once(bank) -> None:
    ing, home = bank
    _linked_session().write(home)
    _run(ing, home)
    src = ing.store.get_source("cc:11111111-2222-3333-4444-555555555555")
    meta = dict(src["meta"])
    meta.pop("links_v")
    ing.store.upsert_source({**src, "meta": meta})  # as an S1 bank left it
    sid = "cc:11111111-2222-3333-4444-555555555555"
    # Drop the rows WITHOUT a tombstone: this simulates a bank that predates session links
    # (an `unlink` is a person's decision and must not be faked here).
    ing.store._write("DELETE FROM links WHERE a = ?", (sid,))
    assert ing.store.links_of(sid) == []
    turns = ing.store.stats()["turns"]
    rep = _run(ing, home)
    assert rep.sources_read == 1 and rep.turns_added == 0
    assert ing.store.stats()["turns"] == turns
    assert {lk["rel"] for lk in ing.store.links_of("cc:11111111-2222-3333-4444-555555555555")} == {
        "touched", "committed", "planned"}
    assert _run(ing, home).unchanged == 1  # and only once


def test_activity_states() -> None:
    from datetime import datetime, timezone

    from navig.recall.session_links import activity

    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    assert activity("2026-10-16T11:45:00Z", now) == "active"
    assert activity("2026-10-12T11:45:00Z", now) == "recent"
    assert activity("2026-09-01T11:45:00Z", now) == "historical"
    assert activity("", now) == "historical"


def test_worktree_and_checkout_edits_share_one_key_with_nested_spaces() -> None:
    from navig.recall.session_links import target_key

    # `core` is its own registered space inside the repo (as on the owner's machine).
    r = ProjectResolver(entries=[("alpha", r"E:\proj\alpha"), ("alpha-core", r"E:\proj\alpha\core")],
                        home=r"C:\Users\someone")
    want = "file:alpha-core/navig/store.py"
    assert target_key(r"E:\proj\alpha\core\navig\store.py", r) == want
    assert target_key(r"E:\proj\alpha\.dev\worktrees\fix-x\core\navig\store.py", r) == want
    assert target_key(r"C:\Users\someone\.codex\worktrees\ab12\alpha\core\navig\store.py", r) == want
    assert target_key(r"E:\proj\alpha\.dev\worktrees\fix-x\README.md", r) == "file:alpha/README.md"
    # a Codex worktree of an unregistered project still gets a stable key
    assert target_key(r"C:\Users\someone\.codex\worktrees\ab12\Other\a.py", r) == "file:other/a.py"


def test_relink_retires_links_an_older_extractor_derived(bank) -> None:
    ing, home = bank
    sid = "cc:11111111-2222-3333-4444-555555555555"
    _linked_session().write(home)
    _run(ing, home)
    stale = (sid, "file:alpha/wrong/key.py", "touched")
    ing.store.add_links([stale, (sid, "file:alpha/docs/kept.md", "linked")])
    ing.vault.append_links([stale, (sid, "file:alpha/docs/kept.md", "linked")])
    src = ing.store.get_source(sid)
    ing.store.upsert_source({**src, "meta": {**src["meta"], "links_v": 1}})
    rep = _run(ing, home)
    assert rep.rewritten == 0 and rep.sources_read == 1  # a relink is not a rewrite
    for _ in range(2):  # live index, then rebuilt from the vault
        bs = {(lk["b"], lk["rel"]) for lk in ing.store.links_of(sid)}
        assert ("file:alpha/wrong/key.py", "touched") not in bs
        assert ("file:alpha/docs/kept.md", "linked") in bs  # hand links are never retired
        assert ("file:alpha/core/store.py", "touched") in bs
        ing.reindex()
    assert ing.store.get_source(sid)["status"] == "ok"


def test_dot_segments_are_collapsed_before_keying() -> None:
    from navig.recall.session_links import target_key

    r = ProjectResolver(entries=[("alpha", r"E:\proj\alpha"), ("alpha-core", r"E:\proj\alpha\core")],
                        home=r"C:\Users\someone")
    assert target_key(r"E:\proj\alpha\core\..\apps\os\x.tsx", r) == "file:alpha/apps/os/x.tsx"
    assert target_key(r"E:\proj\alpha\core\.\navig\a.py", r) == "file:alpha-core/navig/a.py"


# ── tombstones: an operator's unlink must outlive re-reads, not only reindex ──


def test_a_manual_unlink_survives_the_next_full_re_read(bank) -> None:
    from navig.recall import session_links as sl

    ing, home = bank
    sid = "cc:11111111-2222-3333-4444-555555555555"
    target = r"E:\proj\alpha\core\store.py"
    _linked_session().write(home)
    _run(ing, home)
    assert ing.store.link_tombstoned(sid, "file:alpha/core/store.py", "touched") is False

    # The person removes a link Recall derived from the transcript.
    assert sl.unlink(ing.store, ing.vault, "11111111-2222-3333-4444-555555555555", target,
                     ing.resolver) == 1
    assert ("file:alpha/core/store.py", "touched") not in [
        (lk["b"], lk["rel"]) for lk in ing.store.links_of(sid)]
    assert ing.store.link_tombstoned(sid, "file:alpha/core/store.py", "touched") is True

    # …and the transcript is fully re-read (links_v downgraded → the whole file re-parsed,
    # which is where the link used to be re-derived and come back).
    src = ing.store.get_source(sid)
    ing.store.upsert_source({**src, "meta": {**src["meta"], "links_v": 1}})
    assert _run(ing, home).sources_read == 1
    assert ing.store.link_tombstoned(sid, "file:alpha/core/store.py", "touched") is True
    assert sl.find_sessions(ing.store, target, resolver=ing.resolver) == []
    assert ("file:alpha/core/store.py", "touched") not in [
        (lk["b"], lk["rel"]) for lk in ing.store.links_of(sid)]
    # …and the tombstone is recorded, so a second unlink reports "nothing was linked"
    assert sl.unlink(ing.store, ing.vault, "11111111-2222-3333-4444-555555555555", target,
                     ing.resolver) == 0

    # …and a full rebuild from the vault replays the tombstone over the older derived line.
    ing.reindex()
    assert ing.store.link_tombstoned(sid, "file:alpha/core/store.py", "touched") is True
    assert sl.find_sessions(ing.store, target, resolver=ing.resolver) == []


def test_a_manual_relink_after_an_unlink_is_allowed_and_survives_reindex(bank) -> None:
    from navig.recall import session_links as sl

    ing, home = bank
    sid, sid_full = "11111111-2222-3333-4444-555555555555", "cc:11111111-2222-3333-4444-555555555555"
    doc = r"E:\proj\alpha\docs\design.md"
    _linked_session().write(home)
    _run(ing, home)

    assert sl.link(ing.store, ing.vault, sid, doc, ing.resolver) is True   # link
    assert sl.unlink(ing.store, ing.vault, sid, doc, ing.resolver) == 1    # unlink
    assert sl.find_sessions(ing.store, doc, resolver=ing.resolver) == []
    # A person may change their mind: their own link (never the derivation) re-opens the pair.
    assert sl.link(ing.store, ing.vault, sid, doc, ing.resolver) is True   # link again
    assert [r.rels for r in sl.find_sessions(ing.store, doc, resolver=ing.resolver)] == [("linked",)]
    ing.reindex()  # vault order link → unlink → link decides the final state
    assert [r.rels for r in sl.find_sessions(ing.store, doc, resolver=ing.resolver)] == [("linked",)]
    assert ing.store.link_tombstoned(sid_full, "file:alpha/docs/design.md", "linked") is False


def test_tombstones_are_not_links_anywhere_they_are_counted(bank) -> None:
    ing, home = bank
    sid = "cc:11111111-2222-3333-4444-555555555555"
    _linked_session().write(home)
    _run(ing, home)
    from navig.recall import session_links as sl

    before = ing.store.stats()["links"]
    # The SERVICE, not the bare store method: the vault line it appends is what keeps the
    # index and a later reindex in agreement (the bare call would tombstone without one).
    assert sl.unlink(ing.store, ing.vault, "11111111-2222-3333-4444-555555555555",
                     r"E:\proj\alpha\core\store.py", ing.resolver) == 1
    after = ing.store.stats()["links"]
    assert after == before - 1  # only `touched` was pointing at that pair
    assert ing.store.links_of(sid) == [] or all(
        lk["b"] != "file:alpha/core/store.py" for lk in ing.store.links_of(sid))
    # rebuilding the index must not count tombstones as links either
    counts = ing.reindex()
    assert counts["links"] >= 0
    assert ing.store.stats()["links"] == after
