"""Recall S3 — model extraction with verified quotes, authority rules, budget, cache,
reconciliation, review, evaluation and implementation verification. No network: the model
is a fake callable; transcripts are synthetic."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from navig.agent.recall_extractor import parse_items
from navig.recall.archive import Archive
from navig.recall.evaluate import score
from navig.recall.extract import (
    ExtractOptions,
    apply_rules,
    build_windows,
    run_extraction,
    verify_quotes,
)
from navig.recall.ingest import Ingestor
from navig.recall.model import Evidence, Record
from navig.recall.projects import ProjectResolver
from navig.recall.store import RecallStore
from navig.recall.vault import Vault
from navig.recall.verify import verify_record

SID = "22222222-3333-4444-5555-666666666666"


def _line(n: int, rtype: str, content: Any) -> str:
    return json.dumps({"type": rtype, "uuid": f"u{n}", "sessionId": SID, "cwd": r"E:\proj\alpha",
                       "timestamp": f"2026-01-0{1 + n % 8}T10:00:00Z",
                       "message": {"role": rtype, "content": content}})


def _transcript(home: Path, lines: list[str]) -> None:
    d = home / "projects" / "E--proj-alpha"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{SID}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def bank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Ingestor:
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "data" / "recall"
    ing = Ingestor(store=RecallStore(root / "index.db"), archive=Archive(root / "archive"),
                   vault=Vault(root / "vault"),
                   resolver=ProjectResolver(entries=[("alpha", r"E:\proj\alpha")], home=r"C:\Users\x"))
    home = tmp_path / "claude"
    _transcript(home, [
        _line(1, "user", "We could build a Telegram controller for OBS. Mail me at ops@example.com"),
        _line(2, "assistant", [{"type": "text", "text": "Use OBS WebSocket v5 on port 4455."}]),
        _line(3, "user", "Yes, let's go with WebSocket. Call the module obs-bridge."),
    ])
    ing.run(("claude_code",), claude_homes=[home], codex_home=home / "none")
    return ing


@dataclass
class FakeResult:
    items: list[dict[str, Any]]
    prompt_tokens: int = 1000
    completion_tokens: int = 200
    parse_error: str = ""


@dataclass
class FakeLLM:
    items: list[dict[str, Any]]
    prompts: list[str] = field(default_factory=list)

    def __call__(self, prompt: str) -> FakeResult:
        self.prompts.append(prompt)
        return FakeResult(self.items)


def _items(labels: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {"type": "idea", "title": "Telegram controller for OBS", "summary": "Control OBS from Telegram.",
         "status": "proposed", "decided_by": "none",
         "quotes": [{"msg": labels["user1"], "text": "We could build a Telegram controller for OBS."}]},
        {"type": "decision", "title": "Use OBS WebSocket", "summary": "The operator chose WebSocket.",
         "status": "approved", "decided_by": "user",
         "quotes": [{"msg": labels["user2"], "text": "let's go with WebSocket"}]},
        {"type": "term", "title": "obs-bridge", "summary": "Module name.", "status": "confirmed",
         "decided_by": "user", "quotes": [{"msg": labels["asst"], "text": "Use OBS WebSocket v5"}]},
        {"type": "risk", "title": "Invented", "summary": "x", "status": "proposed", "decided_by": "none",
         "quotes": [{"msg": labels["asst"], "text": "a sentence that was never said"}]},
    ]


def _labels(ing: Ingestor) -> dict[str, str]:
    turns = ing.store.source_turns(f"cc:{SID}")
    by_text = {t["text"][:10]: f"m{t['seq']}" for t in turns}
    return {"user1": by_text["We could b"], "asst": by_text["Use OBS We"], "user2": by_text["Yes, let's"]}


def _opts(**kw: Any) -> ExtractOptions:
    return ExtractOptions(**{"budget_usd": 5.0, "day_cap_usd": 20.0, "recovery": False, **kw})


def test_windows_redact_pii_and_label_turns(bank: Ingestor) -> None:
    wins = build_windows(bank.store.source_turns(f"cc:{SID}"))
    assert len(wins) == 1
    assert "ops@example.com" not in wins[0].text and "[email]" in wins[0].text
    assert all(lab.startswith("m") for lab in wins[0].labels)
    assert "] USER: We could build" in wins[0].text


def test_windows_split_and_overlap() -> None:
    turns = [{"seq": i, "msg_id": f"u{i}", "speaker": "assistant", "text": "x" * 900, "ts": ""} for i in range(10)]
    wins = build_windows(turns, window_chars=3000)
    assert len(wins) > 2
    assert set(wins[0].labels) & set(wins[1].labels)  # consecutive windows overlap


def test_quotes_must_be_verbatim_in_the_cited_line() -> None:
    labels = {"m1": {"msg_id": "u1", "speaker": "user_typed", "ts": "", "sent": "Let’s ship   it on Friday."}}
    good, bad = verify_quotes({"quotes": [{"msg": "m1", "text": "Let's ship it"},
                                          {"msg": "m1", "text": "ship it on Monday"},
                                          {"msg": "m9", "text": "Let's ship it"}]}, labels)
    assert [g["quote"] for g in good] == ["Let's ship it"] and bad == 2


def test_authority_rules_never_promote_assistant_text() -> None:
    user_q = [{"speaker": "user_typed"}]
    asst_q = [{"speaker": "assistant"}]
    assert apply_rules({"status": "approved", "decided_by": "user"}, user_q) == ("approved", "user", "user_endorsed")
    assert apply_rules({"status": "rejected", "decided_by": "user"}, user_q) == ("rejected", "user", "user_rejected")
    assert apply_rules({"status": "approved", "decided_by": "user"}, asst_q) == ("exploring", "assistant", "none")
    assert apply_rules({"status": "implemented", "decided_by": "assistant"}, asst_q)[0] == "proposed"


def test_extraction_creates_cited_records_and_respects_authority(bank: Ingestor) -> None:
    llm = FakeLLM(_items(_labels(bank)))
    rep = run_extraction(bank, _opts(), llm=llm)
    assert rep.calls == 1 and rep.rejected_quotes == 1 and rep.unquoted_items == 1
    assert "ops@example.com" not in llm.prompts[0]
    recs = {r.title: bank.store.get_record(r.id) for r in bank.store.list_records(limit=50) if "extracted" in r.tags}
    decision = recs["Use OBS WebSocket"]
    assert (decision.status, decision.status_basis, decision.endorsement) == ("approved", "user", "user_endorsed")
    assert decision.evidence[0].speaker == "user_typed"
    term = recs["obs-bridge"]  # "confirmed by user" but cited only an assistant line → capped
    assert (term.status, term.status_basis) == ("exploring", "assistant")
    assert "Invented" not in recs
    assert bank.store.list_review()[0]["kind"] == "unquoted_candidate"


def test_rerun_is_free_and_budget_stops_before_spending(bank: Ingestor) -> None:
    llm = FakeLLM(_items(_labels(bank)))
    run_extraction(bank, _opts(), llm=llm)
    again = run_extraction(bank, _opts(), llm=llm)
    assert again.calls == 0 and again.cached == 1 and len(llm.prompts) == 1
    fresh = FakeLLM([])
    capped = run_extraction(bank, _opts(budget_usd=0.0, source_ids=("cc:none",)), llm=fresh)
    assert capped.calls == 0
    bank.store.reset()  # an index rebuild must not forget what was paid for
    bank.reindex()
    assert run_extraction(bank, _opts(), llm=FakeLLM([])).cached == 1


def test_budget_stop(bank: Ingestor) -> None:
    rep = run_extraction(bank, _opts(budget_usd=0.0000001), llm=FakeLLM([]))
    assert rep.calls == 0 and rep.stopped.startswith("run budget")


def test_parse_error_is_recorded_and_retried(bank: Ingestor) -> None:
    class Broken:
        calls = 0

        def __call__(self, prompt: str) -> FakeResult:
            Broken.calls += 1
            return FakeResult([], parse_error="no JSON object")

    rep = run_extraction(bank, _opts(), llm=Broken())
    assert rep.parse_errors == 1 and rep.cost_usd > 0  # the spend is still counted
    run_extraction(bank, _opts(), llm=FakeLLM([]))
    assert bank.store.extract_summary()["ok"]["runs"] == 1


def test_refines_merges_into_existing_record_and_near_duplicates_go_to_review(bank: Ingestor) -> None:
    labels = _labels(bank)
    run_extraction(bank, _opts(), llm=FakeLLM(_items(labels)))
    idea = next(r for r in bank.store.list_records(rtype="idea", limit=10) if "extracted" in r.tags)
    bank.store.reset()
    bank.reindex()
    later = [{"type": "idea", "title": "Remote OBS bot over Telegram", "summary": "Same idea, refined.",
              "status": "exploring", "decided_by": "none", "refines": idea.id,
              "quotes": [{"msg": labels["user1"], "text": "Telegram controller for OBS"}]}]
    run_extraction(bank, _opts(window_chars=100_000, focus=("ideas",)), llm=FakeLLM(later))
    merged = bank.store.get_record(idea.id)
    assert merged.status == "exploring" and any(e["kind"] == "refined" for e in bank.store.events_for(idea.id))
    near = [{"type": "idea", "title": "Telegram controller for OBS app", "summary": "dup?", "status": "proposed",
             "decided_by": "none", "quotes": [{"msg": labels["user1"], "text": "build a Telegram controller"}]}]
    run_extraction(bank, _opts(focus=("findings",)), llm=FakeLLM(near))
    assert any(r["kind"] == "possible_duplicate" for r in bank.store.list_review())


def test_dry_run_sends_nothing_and_estimates_cost(bank: Ingestor) -> None:
    rep = run_extraction(bank, _opts(dry_run=True), llm=None)
    assert rep.calls == 0 and rep.est_prompt_tokens > 0 and rep.est_cost_usd > 0


def test_parse_items_salvages_what_real_answers_break() -> None:
    # measured on MiMo Flash: trailing text after a valid object, and one malformed item
    assert parse_items('{"items": [{"type": "idea"}]} {"items": []}') == [{"type": "idea"}]
    broken = '{"items": [{"type": "idea", "title": "a \\"quoted\\" one"}, {"type": "term" "title": "x"}, {"type": "plan"}]}'
    assert [i["type"] for i in parse_items(broken)] == ["idea", "plan"]


def test_parse_items_tolerates_fences_and_prose() -> None:
    assert parse_items('```json\n{"items": [{"type": "idea"}]}\n```') == [{"type": "idea"}]
    assert parse_items('Sure! {"items": []} hope that helps') == []
    with pytest.raises(ValueError):
        parse_items("no json here")


def test_complete_raw_estimates_usage_when_the_provider_reports_none(monkeypatch: pytest.MonkeyPatch) -> None:
    import navig.llm.generate as gen

    monkeypatch.setattr(gen, "_call_provider_rich", lambda **kw: ("hello " * 20, 0, 0, {}))
    res = gen.complete_raw([{"role": "user", "content": "x" * 350}], provider="mimo", model="m")
    assert res.prompt_tokens > 0 and res.finish_reason == "stop:estimated_usage"


def test_explicit_zero_temperature_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    import navig.llm.generate as gen

    seen: dict[str, Any] = {}
    monkeypatch.setattr(gen, "_call_provider", lambda **kw: seen.update(kw) or "ok")
    gen.llm_generate([{"role": "user", "content": "x"}], model_override="mimo:m", temperature=0.0)
    assert seen["temperature"] == 0.0


def test_eval_scores_against_audit_provenance(bank: Ingestor) -> None:
    gold = Record(id="rk-gold", type="feature", title="Telegram controller for OBS", project="alpha",
                  tags=["audit"], attrs={"sessions": [SID]})
    bank.commit([gold])
    run_extraction(bank, _opts(), llm=FakeLLM(_items(_labels(bank))))
    scores, attribution_errors = score(bank.store, [f"cc:{SID}"])
    assert (scores[0].gold, scores[0].found) == (1, 1) and attribution_errors == 0


def test_verify_finds_code_and_tests_but_never_claims_verified(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "obs_bridge.py").write_text("def obs_bridge_connect():\n    pass\n", encoding="utf-8")
    (repo / "tests" / "test_obs.py").write_text("from src.obs_bridge import obs_bridge_connect\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    rec = Record(id="rk-v", type="idea", title="Add `obs_bridge_connect` helper", project="alpha")
    res = verify_record(rec, repo)
    assert res.impl == "likely" and res.code_hits and res.test_hits
    assert verify_record(Record(id="rk-n", type="idea", title="Use `quantum_teleporter` and `warp_drive_core`",
                                project="alpha"), repo).impl == "not_found"


def test_evidence_round_trip_unchanged() -> None:
    e = Evidence("cc:a", "u1", "user_typed", "quote", "2026-01-01")
    assert Evidence.from_dict(e.to_dict()) == e
