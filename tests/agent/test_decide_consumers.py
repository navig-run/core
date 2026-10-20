"""The three Wave-1 consumers of the decision model (Clef).

Each must (a) use Clef's answer when it has one, and (b) run its previous logic
UNCHANGED when ``decide`` returns ``None`` — that second half is what makes the feature
safe to switch on, so each consumer is checked from both sides.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from navig.agent import decide

# ── Telegram triage: agent/router/llm_classifier.classify_by_llm ──


@pytest.fixture
def triage(monkeypatch):
    from navig.agent.router import llm_classifier

    llm_classifier._cache.clear()
    calls = {"llm": 0, "decide": []}

    async def fake_llm(prompt):
        calls["llm"] += 1
        return "MEDIUM"

    monkeypatch.setattr(llm_classifier, "_call_llm", fake_llm)
    yield llm_classifier, calls
    llm_classifier._cache.clear()


def _answer(qid: str, answer: str, conf: float = 0.9) -> dict[str, decide.Decision]:
    return {qid: decide.Decision(answer=answer, confidence=conf, probs={answer: conf})}


def test_triage_uses_the_decision_model_when_it_answers(triage, monkeypatch):
    llm_classifier, calls = triage

    async def fake_adecide(state, questions, *, use, images=None):
        calls["decide"].append((use, [q.id for q in questions], sorted(questions[0].criteria)))
        return _answer("tier", "AGENTIC")

    monkeypatch.setattr(decide, "adecide", fake_adecide)
    assert asyncio.run(llm_classifier.classify_by_llm("deploy the app and watch logs")) == "AGENTIC"
    assert calls["llm"] == 0
    use, qids, options = calls["decide"][0]
    assert use == "telegram_triage" and qids == ["tier"]
    assert options == ["AGENTIC", "COMPLEX", "MEDIUM", "REASONING", "SIMPLE"]


def test_triage_falls_back_to_the_llm_when_the_model_declines(triage, monkeypatch):
    llm_classifier, calls = triage

    async def declines(*a, **k):
        return None

    monkeypatch.setattr(decide, "adecide", declines)
    assert asyncio.run(llm_classifier.classify_by_llm("hello")) == "MEDIUM"
    assert calls["llm"] == 1


def test_triage_result_is_cached_whichever_method_answered(triage, monkeypatch):
    llm_classifier, calls = triage
    n = {"decide": 0}

    async def fake_adecide(*a, **k):
        n["decide"] += 1
        return _answer("tier", "SIMPLE")

    monkeypatch.setattr(decide, "adecide", fake_adecide)
    for _ in range(3):
        assert asyncio.run(llm_classifier.classify_by_llm("what time is it")) == "SIMPLE"
    assert n["decide"] == 1


def test_triage_with_the_feature_off_is_byte_for_byte_the_old_path(triage, monkeypatch):
    """No monkeypatching of decide: the real settings say "off" (isolated config)."""
    llm_classifier, calls = triage
    monkeypatch.setattr(decide, "settings", lambda: decide.Settings(
        enabled=False, model="clef-flash", uses=dict.fromkeys(decide.USES, "on"), min_confidence=0.5,
        timeout_s=1.5, daily_cap=2000, shadow=False, redact=True, account_id="",
    ))
    assert asyncio.run(llm_classifier.classify_by_llm("anything")) == "MEDIUM"
    assert calls["llm"] == 1


def test_the_llm_prompt_still_lists_every_tier():
    from navig.agent.router import llm_classifier

    for tier in ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING", "AGENTIC"):
        assert f"  {tier}" in llm_classifier._SYSTEM_PROMPT
    assert llm_classifier._SYSTEM_PROMPT.endswith("No punctuation.")


# ── Inbox sorting: inbox/classifier.Classifier ───────────────


def test_inbox_category_options_are_exactly_the_routable_categories():
    from navig.inbox import classifier

    assert set(classifier._CATEGORY_DESCRIPTIONS) == set(classifier._CATEGORY_KEYWORDS) | {"ignore"}


def test_inbox_uses_the_decision_model_and_reports_alternatives(monkeypatch):
    from navig.inbox.classifier import Classifier

    seen = {}

    def fake_decide(state, questions, *, use, images=None):
        seen["use"] = use
        seen["state"] = state
        return {
            "category": decide.Decision(
                answer="hub/roadmap",
                confidence=0.81,
                probs={"hub/roadmap": 0.81, "hub/tasks": 0.12, "archive": 0.05, "ignore": 0.02},
            )
        }

    def llm_must_not_run(*a, **k):
        raise AssertionError("the LLM path ran although the decision model answered")

    monkeypatch.setattr(decide, "decide", fake_decide)
    monkeypatch.setattr(Classifier, "_classify_llm", llm_must_not_run)
    r = Classifier(use_llm=True).classify("Q3 milestones and release plan", filename="plan.md")
    assert (r.category, r.method, r.confidence) == ("hub/roadmap", "clef", 0.81)
    assert r.alternatives[0] == ("hub/tasks", 0.12)
    assert seen["use"] == "inbox_sort" and "plan.md" in seen["state"]


def test_inbox_falls_back_to_llm_then_bm25_when_the_model_declines(monkeypatch):
    from navig.inbox.classifier import Classifier

    monkeypatch.setattr(decide, "decide", lambda *a, **k: None)
    calls = {"llm": 0}

    def llm_fails(self, *a, **k):
        calls["llm"] += 1
        raise RuntimeError("offline")

    monkeypatch.setattr(Classifier, "_classify_llm", llm_fails)
    r = Classifier(use_llm=True).classify("sprint backlog ticket todo", filename="t.md")
    assert calls["llm"] == 1
    assert r.method == "bm25" and r.category == "hub/tasks"


def test_inbox_no_llm_flag_never_consults_the_model(monkeypatch):
    from navig.inbox.classifier import Classifier

    def must_not_run(*a, **k):
        raise AssertionError("--no-llm must not reach the decision model")

    monkeypatch.setattr(decide, "decide", must_not_run)
    assert Classifier(use_llm=False).classify("roadmap milestone", filename="x.md").method == "bm25"


# ── Inbox space routing: inbox/space_scorer.find_best_destination ──


def _space(root: Path, name: str, keywords: list[str], description: str) -> Path:
    sp = root / name
    (sp / ".navig" / "inbox").mkdir(parents=True)
    (sp / ".navig" / "inbox" / "routes.yaml").write_text(
        "channels:\n"
        f"  - id: main\n    name: {name}\n    description: {description}\n"
        f"    keywords: [{', '.join(keywords)}]\n",
        encoding="utf-8",
    )
    return sp


@pytest.fixture
def spaces(tmp_path):
    src = _space(tmp_path, "source", ["misc"], "where it came from")
    fin = _space(tmp_path, "finance", ["invoice", "receipt", "budget"], "money matters")
    dev = _space(tmp_path, "devops", ["docker", "deploy", "server"], "infrastructure work")
    return tmp_path, src, fin, dev


def test_space_routing_uses_the_model_pick(spaces, monkeypatch):
    from navig.inbox import space_scorer

    root, src, fin, dev = spaces
    seen = {}
    monkeypatch.setattr(decide, "is_active", lambda use: use == "inbox_space")

    def fake_decide(state, questions, *, use, images=None):
        seen["options"] = sorted(questions[0].criteria)
        return {"space": decide.Decision(answer="finance", confidence=0.9, probs={"finance": 0.9})}

    monkeypatch.setattr(decide, "decide", fake_decide)
    # The keywords point at devops — the model's answer must win.
    dest = space_scorer.find_best_destination(["docker", "deploy"], src, root, text="Invoice 4411 attached")
    assert dest == fin / ".navig" / "inbox"
    assert seen["options"] == ["devops", "finance", "none"], "the source space must never be offered"


def test_space_routing_none_means_stay(spaces, monkeypatch):
    from navig.inbox import space_scorer

    root, src, fin, dev = spaces
    monkeypatch.setattr(decide, "is_active", lambda use: True)
    monkeypatch.setattr(
        decide, "decide",
        lambda *a, **k: {"space": decide.Decision(answer="none", confidence=0.95, probs={"none": 0.95})},
    )
    assert space_scorer.find_best_destination(["docker", "deploy"], src, root, text="random note") is None


def test_space_routing_falls_back_to_keywords_when_the_model_declines(spaces, monkeypatch):
    from navig.inbox import space_scorer

    root, src, fin, dev = spaces
    monkeypatch.setattr(decide, "is_active", lambda use: True)
    monkeypatch.setattr(decide, "decide", lambda *a, **k: None)
    dest = space_scorer.find_best_destination(["docker", "deploy"], src, root, text="docker deploy")
    assert dest == dev / ".navig" / "inbox"


def test_space_routing_without_text_is_the_old_keyword_path(spaces, monkeypatch):
    from navig.inbox import space_scorer

    root, src, fin, dev = spaces

    def must_not_run(*a, **k):
        raise AssertionError("no text → the model must not be asked")

    monkeypatch.setattr(decide, "decide", must_not_run)
    assert space_scorer.find_best_destination(["invoice", "budget"], src, root) == fin / ".navig" / "inbox"
